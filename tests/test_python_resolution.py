import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code_intelligence import (
    _python_import_candidates,
    _python_search_roots,
    _resolve_import_to_file,
)


def _write(path: Path, content: str = "x = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestSearchRoots:
    def test_root_only_when_no_src(self, tmp_path: Path):
        assert _python_search_roots(tmp_path) == [tmp_path]

    def test_includes_src_dir(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        roots = _python_search_roots(tmp_path)
        assert tmp_path / "src" in roots


class TestSrcLayoutResolution:
    def test_absolute_import_resolves_under_src(self, tmp_path: Path):
        _write(tmp_path / "src" / "mypkg" / "foo.py")
        consumer = tmp_path / "src" / "mypkg" / "bar.py"
        _write(consumer, "from mypkg.foo import x\n")
        target = _resolve_import_to_file("mypkg.foo", consumer, tmp_path, ".py")
        assert target == (tmp_path / "src" / "mypkg" / "foo.py").resolve()

    def test_package_init_resolves_under_src(self, tmp_path: Path):
        _write(tmp_path / "src" / "mypkg" / "__init__.py")
        consumer = tmp_path / "app.py"
        _write(consumer, "import mypkg\n")
        target = _resolve_import_to_file("mypkg", consumer, tmp_path, ".py")
        assert target == (tmp_path / "src" / "mypkg" / "__init__.py").resolve()


class TestRelativeImports:
    def test_single_dot_module(self, tmp_path: Path):
        _write(tmp_path / "pkg" / "sibling.py")
        consumer = tmp_path / "pkg" / "main.py"
        _write(consumer, "from .sibling import y\n")
        target = _resolve_import_to_file(".sibling", consumer, tmp_path, ".py")
        assert target == (tmp_path / "pkg" / "sibling.py").resolve()

    def test_double_dot_parent_package(self, tmp_path: Path):
        _write(tmp_path / "pkg" / "util.py")
        consumer = tmp_path / "pkg" / "sub" / "main.py"
        _write(consumer, "from ..util import z\n")
        target = _resolve_import_to_file("..util", consumer, tmp_path, ".py")
        assert target == (tmp_path / "pkg" / "util.py").resolve()

    def test_bare_dot_resolves_package_init(self, tmp_path: Path):
        _write(tmp_path / "pkg" / "__init__.py")
        consumer = tmp_path / "pkg" / "main.py"
        _write(consumer, "from . import thing\n")
        target = _resolve_import_to_file(".", consumer, tmp_path, ".py")
        assert target == (tmp_path / "pkg" / "__init__.py").resolve()


class TestNoFalsePositives:
    def test_unresolvable_import_returns_none(self, tmp_path: Path):
        consumer = tmp_path / "app.py"
        _write(consumer, "import nonexistent.module\n")
        assert _resolve_import_to_file("nonexistent.module", consumer, tmp_path, ".py") is None

    def test_relative_escaping_root_is_rejected(self, tmp_path: Path):
        outside = tmp_path.parent / "outside_target_xyz.py"
        try:
            _write(outside)
            consumer = tmp_path / "main.py"
            _write(consumer, "from ..outside_target_xyz import w\n")
            assert (
                _resolve_import_to_file("..outside_target_xyz", consumer, tmp_path, ".py") is None
            )
        finally:
            if outside.exists():
                outside.unlink()


class TestCandidatesHelper:
    def test_absolute_candidates_include_src(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        consumer = tmp_path / "a.py"
        cands = _python_import_candidates("pkg.mod", consumer, tmp_path)
        assert any("src" in str(c) for c in cands)
