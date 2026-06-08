import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code_intelligence import (
    _build_import_graph_uncached,
    _build_js_resolver,
    _resolve_import_to_file,
    _strip_jsonc,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestStripJsonc:
    def test_removes_line_and_block_comments_and_trailing_commas(self):
        raw = """
        {
            // a line comment
            "compilerOptions": {
                /* block comment */
                "baseUrl": ".",
                "paths": { "@/*": ["src/*"], },
            },
        }
        """
        import json

        data = json.loads(_strip_jsonc(raw))
        assert data["compilerOptions"]["baseUrl"] == "."
        assert data["compilerOptions"]["paths"]["@/*"] == ["src/*"]

    def test_does_not_eat_urls_in_strings(self):
        raw = '{"homepage": "https://example.com/path"}'
        import json

        data = json.loads(_strip_jsonc(raw))
        assert data["homepage"] == "https://example.com/path"


class TestRelativeImports:
    def test_relative_import_resolves(self, tmp_path: Path):
        _write(tmp_path / "src" / "utils.ts", "export const x = 1;\n")
        src = tmp_path / "src" / "app.ts"
        _write(src, "import { x } from './utils';\n")

        target = _resolve_import_to_file("./utils", src, tmp_path, ".ts")
        assert target is not None
        assert target.name == "utils.ts"

    def test_relative_index_import_resolves(self, tmp_path: Path):
        _write(tmp_path / "src" / "lib" / "index.ts", "export const y = 2;\n")
        src = tmp_path / "src" / "app.ts"
        _write(src, "import { y } from './lib';\n")

        target = _resolve_import_to_file("./lib", src, tmp_path, ".ts")
        assert target is not None
        assert target.as_posix().endswith("src/lib/index.ts")


class TestTsconfigAliases:
    def test_alias_resolves_to_src(self, tmp_path: Path):
        _write(
            tmp_path / "tsconfig.json",
            '{ "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["src/*"] } } }',
        )
        _write(tmp_path / "src" / "utils.ts", "export const x = 1;\n")
        src = tmp_path / "src" / "feature" / "app.ts"
        _write(src, "import { x } from '@/utils';\n")

        resolver = _build_js_resolver(tmp_path)
        target = _resolve_import_to_file("@/utils", src, tmp_path, ".ts", resolver)
        assert target is not None
        assert target.as_posix().endswith("src/utils.ts")

    def test_non_alias_bare_import_is_ignored(self, tmp_path: Path):
        _write(
            tmp_path / "tsconfig.json",
            '{ "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["src/*"] } } }',
        )
        src = tmp_path / "src" / "app.ts"
        _write(src, "import React from 'react';\n")

        resolver = _build_js_resolver(tmp_path)
        target = _resolve_import_to_file("react", src, tmp_path, ".ts", resolver)
        assert target is None


class TestWorkspacePackages:
    def test_cross_package_name_resolves_via_main(self, tmp_path: Path):
        _write(
            tmp_path / "packages" / "ui" / "package.json",
            '{ "name": "@acme/ui", "main": "src/index.ts" }',
        )
        _write(tmp_path / "packages" / "ui" / "src" / "index.ts", "export const Btn = 1;\n")
        src = tmp_path / "packages" / "app" / "src" / "main.ts"
        _write(src, "import { Btn } from '@acme/ui';\n")

        resolver = _build_js_resolver(tmp_path)
        target = _resolve_import_to_file("@acme/ui", src, tmp_path, ".ts", resolver)
        assert target is not None
        assert target.as_posix().endswith("packages/ui/src/index.ts")

    def test_cross_package_subpath_resolves(self, tmp_path: Path):
        _write(tmp_path / "packages" / "ui" / "package.json", '{ "name": "@acme/ui" }')
        _write(tmp_path / "packages" / "ui" / "button.ts", "export const Btn = 1;\n")
        src = tmp_path / "packages" / "app" / "main.ts"
        _write(src, "import { Btn } from '@acme/ui/button';\n")

        resolver = _build_js_resolver(tmp_path)
        target = _resolve_import_to_file("@acme/ui/button", src, tmp_path, ".ts", resolver)
        assert target is not None
        assert target.as_posix().endswith("packages/ui/button.ts")


class TestFullGraph:
    def test_graph_includes_alias_and_workspace_edges(self, tmp_path: Path):
        _write(
            tmp_path / "tsconfig.json",
            '{ "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["packages/app/src/*"] } } }',
        )
        _write(
            tmp_path / "packages" / "ui" / "package.json",
            '{ "name": "@acme/ui", "main": "src/index.ts" }',
        )
        _write(tmp_path / "packages" / "ui" / "src" / "index.ts", "export const Btn = 1;\n")
        _write(tmp_path / "packages" / "app" / "src" / "helper.ts", "export const h = 1;\n")
        _write(
            tmp_path / "packages" / "app" / "src" / "main.ts",
            "import { Btn } from '@acme/ui';\nimport { h } from '@/helper';\n",
        )

        graph = _build_import_graph_uncached(tmp_path)
        main_edges = graph.get("packages/app/src/main.ts", [])
        assert "packages/ui/src/index.ts" in main_edges
        assert "packages/app/src/helper.ts" in main_edges

    def test_python_imports_still_work(self, tmp_path: Path):
        _write(tmp_path / "helper.py", "VALUE = 1\n")
        _write(tmp_path / "main.py", "from helper import VALUE\n")

        graph = _build_import_graph_uncached(tmp_path)
        assert "helper.py" in graph.get("main.py", [])
