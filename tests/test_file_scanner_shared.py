"""
Regression tests for the split file-selection paths.

The vector indexer and the symbol graph each used to walk the tree with their
own filter. Only the indexer applied `.indexignore`, so on a Next.js project
the symbol graph walked into `app/.next`, spent its whole budget on minified
bundles and produced a graph with no application code in it — while
`find_symbol` simply answered "no symbols found".
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import config
from codebase_indexer import CodebaseIndexer
from file_scanner import IgnoreMatcher, load_ignore_patterns, looks_minified, scan_files
from symbol_graph import build_symbol_graph


@pytest.fixture
def next_like_project(tmp_path):
    """A project whose build output dwarfs (and sorts before) its source."""
    src = tmp_path / "app" / "src" / "lib"
    src.mkdir(parents=True)
    (src / "obligations.ts").write_text(
        "export async function syncTenantObligations(tenantId: string) {\n"
        "  return tenantId\n"
        "}\n",
        encoding="utf-8",
    )

    build = tmp_path / "app" / ".next" / "server"
    build.mkdir(parents=True)
    for i in range(30):
        (build / f"chunk{i}.js").write_text(
            "function a(e,t){return e+t}function l(n){return n}\n", encoding="utf-8"
        )

    (tmp_path / ".ai").mkdir()
    (tmp_path / ".ai" / ".indexignore").write_text(
        "# comment\nnode_modules\n.next\n", encoding="utf-8"
    )
    return tmp_path


def test_ignore_patterns_load_from_ai_dir(next_like_project):
    patterns = load_ignore_patterns(next_like_project)
    assert ".next" in patterns
    assert "node_modules" in patterns
    assert not any(p.startswith("#") for p in patterns)


def test_root_level_indexignore_wins(tmp_path):
    (tmp_path / ".ai").mkdir()
    (tmp_path / ".ai" / ".indexignore").write_text("legacy\n", encoding="utf-8")
    (tmp_path / ".indexignore").write_text("preferred\n", encoding="utf-8")
    assert load_ignore_patterns(tmp_path) == {"preferred"}


def test_scan_prunes_ignored_subtree(next_like_project):
    result = scan_files(next_like_project)
    rels = {p.relative_to(next_like_project).as_posix() for p in result.files}

    assert "app/src/lib/obligations.ts" in rels
    assert not any("/.next/" in r or r.startswith(".next/") for r in rels)
    assert result.complete


def test_symbol_graph_honours_indexignore(next_like_project):
    """The core regression: the graph must contain source, not build output."""
    graph = build_symbol_graph(next_like_project)

    assert graph.truncated is None
    defs = graph.defs_of("syncTenantObligations")
    assert [d.file_path for d in defs] == ["app/src/lib/obligations.ts"]
    # Nothing from the minified bundles leaked in.
    assert not any(d.file_path.startswith("app/.next") for d in graph.symbols.values())


def test_symbol_graph_and_indexer_agree_on_file_set(next_like_project, monkeypatch):
    """Both paths must select the same files — drift here caused the bug."""
    monkeypatch.setattr(config, "PROJECT_ROOT", next_like_project)
    patterns = load_ignore_patterns(next_like_project)

    indexer_files = {
        p.relative_to(next_like_project).as_posix()
        for p in CodebaseIndexer(MagicMock()).scan_indexable_files(
            next_like_project, ignored_dirs=set(), ignore_patterns=patterns
        )
    }
    graph_files = {
        p.relative_to(next_like_project).as_posix()
        for p in scan_files(next_like_project, extensions={".ts", ".tsx", ".js"}).files
    }

    assert graph_files <= indexer_files
    assert "app/src/lib/obligations.ts" in graph_files


def test_partial_graph_reports_why(next_like_project):
    """A truncated build must be visible, not look like an empty project."""
    graph = build_symbol_graph(next_like_project, max_files=0)

    assert graph.truncated is not None
    assert graph.symbol_count == 0
    assert "PARTIAL" in graph.status_line()


def test_complete_graph_status_says_so(next_like_project):
    graph = build_symbol_graph(next_like_project)
    assert "complete" in graph.status_line()


class TestIgnoreMatcher:
    def test_matches_nested_path_on_any_platform(self, tmp_path):
        matcher = IgnoreMatcher(tmp_path, {".next"})
        assert matcher.matches(tmp_path / "app" / ".next" / "server" / "a.js")
        assert not matcher.matches(tmp_path / "app" / "src" / "a.ts")

    def test_glob_patterns(self, tmp_path):
        matcher = IgnoreMatcher(tmp_path, {"*.min.js"})
        assert matcher.matches(tmp_path / "vendor" / "jquery.min.js")
        assert not matcher.matches(tmp_path / "vendor" / "jquery.js")

    def test_dir_pruning_uses_full_path(self, tmp_path):
        matcher = IgnoreMatcher(tmp_path, {"app/.next"})
        assert matcher.dir_ignored(tmp_path / "app" / ".next", ".next")
        assert not matcher.dir_ignored(tmp_path / "app" / "src", "src")

    def test_builtin_dirs_still_ignored_without_patterns(self, tmp_path):
        matcher = IgnoreMatcher(tmp_path, set())
        assert matcher.dir_ignored(tmp_path / "node_modules", "node_modules")


class TestMinifiedDetection:
    def test_detects_bundle(self):
        assert looks_minified("var a=1;" * 5000)

    def test_accepts_normal_source(self):
        assert not looks_minified("def f():\n    return 1\n" * 500)

    def test_empty_is_not_minified(self):
        assert not looks_minified("")
