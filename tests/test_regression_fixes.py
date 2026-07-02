"""
Regression tests for the v0.8.0 correctness overhaul.

Each test class pins one previously shipped bug so it cannot silently return:
- BM25 persistence was pickle (RCE vector) and non-atomic
- memory.md was read/written in the locale encoding (Cyrillic corruption)
- Python import extraction destroyed dotted/relative imports
- Failed upserts were swallowed while metadata was committed
- Symbol graph collapsed same-named symbols and let builtin noise through
"""

import json
from pathlib import Path

import pytest


class TestBM25JsonPersistence:
    def _make(self, tmp_path: Path):
        from bm25_index import BM25Index

        # 4+ docs: with fewer, BM25 idf is 0 and every score is filtered out
        idx = BM25Index(tmp_path / "bm25_index.json")
        idx.build(
            ["a_1", "b_1", "c_1", "d_1"],
            ["alpha beta gamma", "delta epsilon", "iota kappa", "mu nu xi"],
            [{"source": "a.py"}, {"source": "b.py"}, {"source": "c.py"}, {"source": "d.py"}],
        )
        return idx

    def test_save_is_json_not_pickle(self, tmp_path):
        idx = self._make(tmp_path)
        idx.save()
        raw = (tmp_path / "bm25_index.json").read_text(encoding="utf-8")
        data = json.loads(raw)  # raises if this were a pickle blob
        assert data["version"] == 2
        assert data["ids"] == ["a_1", "b_1", "c_1", "d_1"]

    def test_load_roundtrip_and_search(self, tmp_path):
        from bm25_index import BM25Index

        self._make(tmp_path).save()
        idx2 = BM25Index(tmp_path / "bm25_index.json")
        assert idx2.load() is True
        hits = idx2.search("alpha", n=5)
        assert hits and hits[0]["id"] == "a_1"

    def test_update_source_replaces_rows(self, tmp_path):
        idx = self._make(tmp_path)
        assert idx.update_source("a.py", ["a_2"], ["zeta eta"], [{"source": "a.py"}])
        idx.rebuild_from_corpus()
        assert not idx.search("alpha", n=5)
        assert idx.search("zeta", n=5)[0]["id"] == "a_2"

    def test_update_source_requires_corpus(self, tmp_path):
        from bm25_index import BM25Index

        empty = BM25Index(tmp_path / "bm25_index.json")
        assert empty.update_source("a.py", ["x"], ["y"], [{}]) is False

    def test_remove_source(self, tmp_path):
        idx = self._make(tmp_path)
        idx.remove_source("b.py")
        idx.rebuild_from_corpus()
        assert not idx.search("delta", n=5)
        assert idx.search("alpha", n=5)

    def test_clear_removes_legacy_pickle(self, tmp_path):
        legacy = tmp_path / "bm25_index.pkl"
        legacy.write_bytes(b"\x80\x04.")
        idx = self._make(tmp_path)
        idx.save()
        idx.clear()
        assert not legacy.exists()
        assert not (tmp_path / "bm25_index.json").exists()


class TestMemoryCyrillic:
    def test_cyrillic_roundtrip(self, tmp_path):
        from memory_manager import MemoryManager

        mf = tmp_path / "memory.md"
        mf.write_text("# Project Memory\n\n## Статус\n- готово\n", encoding="utf-8")
        mm = MemoryManager(memory_file=mf)

        content = mm.read(max_lines=None)
        assert "Статус" in content and "готово" in content

        mm.update("Рішення: використовуємо UTF-8 скрізь", section="Рішення")
        content = mm.read(max_lines=None)
        assert "використовуємо UTF-8 скрізь" in content

    def test_delete_section_exact_heading_match(self, tmp_path):
        from memory_manager import MemoryManager

        mf = tmp_path / "memory.md"
        mf.write_text(
            "# Project Memory\n\n## Status\n- a\n\n## Build Status\n- b\n",
            encoding="utf-8",
        )
        mm = MemoryManager(memory_file=mf)
        mm.delete_section("Status")
        content = mf.read_text(encoding="utf-8")
        # substring matching used to delete "Build Status" too
        assert "## Build Status" in content
        assert "\n## Status" not in content

    def test_delete_section_preserves_cyrillic(self, tmp_path):
        from memory_manager import MemoryManager

        mf = tmp_path / "memory.md"
        mf.write_text(
            "# Пам'ять\n\n## Видалити\n- зайве\n\n## Залишити\n- кирилиця — ок\n",
            encoding="utf-8",
        )
        mm = MemoryManager(memory_file=mf)
        mm.delete_section("Видалити")
        content = mf.read_text(encoding="utf-8")
        assert "зайве" not in content
        assert "кирилиця — ок" in content


class TestImportExtraction:
    def test_python_keeps_dotted_and_relative(self):
        from code_intelligence import _extract_imports_py

        src = "from utils.helpers import x\nfrom .sibling import y\nimport pkg.sub\n"
        imports = _extract_imports_py(src)
        assert "utils.helpers" in imports
        assert ".sibling" in imports
        assert "pkg.sub" in imports

    def test_js_multiline_side_effect_dynamic(self):
        from code_intelligence import _extract_imports_js

        src = (
            "import {\n  a,\n  b,\n} from './widgets';\n"
            "import './setup';\n"
            "const c = require('./legacy');\n"
            "const d = await import('./lazy');\n"
        )
        imports = _extract_imports_js(src)
        for expected in ("./widgets", "./setup", "./legacy", "./lazy"):
            assert expected in imports


class TestIncrementalIndexing:
    def test_atomic_write_overwrites_existing(self, tmp_path):
        from incremental_indexing import atomic_write

        target = tmp_path / "data.json"
        atomic_write(target, "one")
        atomic_write(target, "two")
        assert target.read_text(encoding="utf-8") == "two"

    def test_remove_deleted_files_returns_removed(self):
        from incremental_indexing import IndexMetadata

        meta = IndexMetadata.__new__(IndexMetadata)
        meta.metadata = {"a.py": {"mtime": 1.0}, "b.py": {"mtime": 2.0}}
        removed = meta.remove_deleted_files({"a.py"})
        assert removed == ["b.py"]
        assert "b.py" not in meta.metadata

    def test_changed_detects_rewound_mtime(self, tmp_path):
        from incremental_indexing import IndexMetadata

        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        meta = IndexMetadata.__new__(IndexMetadata)
        # Stored mtime is NEWER than on disk (checkout of an older version):
        # the old `>` comparison missed this edit forever.
        meta.metadata = {str(f): {"mtime": f.stat().st_mtime + 100}}
        assert meta.get_changed_files([f]) == [f]


class TestUpsertFailureTracking:
    def test_failed_upsert_sets_flag_and_raises(self):
        from codebase_indexer import CodebaseIndexer

        class FakeStore:
            def upsert(self, documents, metadatas, ids):
                return False

        indexer = CodebaseIndexer.__new__(CodebaseIndexer)
        indexer.vector_store = FakeStore()
        upserter = indexer._create_batch_upserter()
        with pytest.raises(RuntimeError):
            upserter(["doc"], [{}], ["id1"])
        assert upserter.failed is True


class TestSymbolGraphV3:
    @pytest.fixture()
    def py_extract(self):
        import symbol_graph as sg

        parser = sg._get_parser("python")
        if parser is None:
            pytest.skip("tree-sitter python parser unavailable")

        def run(source: str, rel: str = "pkg/mod.py"):
            return sg._extract_symbols_from_file(
                Path(rel), "python", parser, source.encode("utf-8"), rel
            )

        return run

    def test_same_named_methods_stay_distinct(self, py_extract):
        src = (
            "class A:\n    def run(self):\n        pass\n\n"
            "class B:\n    def run(self):\n        pass\n"
        )
        symbols, _refs = py_extract(src)
        assert "pkg/mod.py::A.run" in symbols
        assert "pkg/mod.py::B.run" in symbols
        assert symbols["pkg/mod.py::A.run"].line_start != symbols["pkg/mod.py::B.run"].line_start

    def test_builtin_calls_filtered(self, py_extract):
        src = "def work():\n    print('x')\n    validate_input('x')\n"
        _symbols, refs = py_extract(src)
        called = {r.to_symbol for r in refs if r.kind == "call"}
        assert "validate_input" in called
        assert "print" not in called

    def test_no_double_attribution_of_method_calls(self, py_extract):
        src = "class Service:\n    def handle(self):\n        transform_data()\n"
        _symbols, refs = py_extract(src)
        class_calls = {
            r.to_symbol for r in refs if r.from_symbol == "pkg/mod.py::Service" and r.kind == "call"
        }
        method_calls = {
            r.to_symbol
            for r in refs
            if r.from_symbol == "pkg/mod.py::Service.handle" and r.kind == "call"
        }
        assert "transform_data" in method_calls
        assert "transform_data" not in class_calls

    def test_by_name_and_reverse_indexes(self):
        from symbol_graph import SymbolDef, SymbolGraph

        g = SymbolGraph()
        a = SymbolDef("run", "method", "a.py", 1, 2, parent_class="A")
        b = SymbolDef("run", "method", "b.py", 5, 6, parent_class="B")
        g.symbols = {a.qualified_id: a, b.qualified_id: b}
        g.calls = {a.qualified_id: {"helper"}}
        g.reverse_indexes()
        assert sorted(g.by_name["run"]) == ["a.py::A.run", "b.py::B.run"]
        assert g._callers_of["helper"] == {a.qualified_id}
        assert len(g.defs_of("run")) == 2

    def test_serialization_roundtrip_and_version(self):
        from symbol_graph import GRAPH_FORMAT_VERSION, SymbolDef, SymbolGraph

        g = SymbolGraph()
        d = SymbolDef("main", "function", "app.py", 1, 10)
        g.symbols = {d.qualified_id: d}
        g.calls = {d.qualified_id: {"helper"}}
        data = g.to_dict()
        assert data["version"] == GRAPH_FORMAT_VERSION
        g2 = SymbolGraph.from_dict(data)
        assert g2.symbols["app.py::main"].name == "main"
        assert g2.by_name["main"] == ["app.py::main"]
