"""
Tests for v0.9.0: AI-authored annotations + BM25-only mode (optional vector stack).
"""

import json

import pytest

import config


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Isolated project root with a couple of code files."""
    root = tmp_path / "proj"
    (root / ".ai").mkdir(parents=True)
    (root / "auth.py").write_text(
        "def verify_token(token):\n    return token == 'ok'\n", encoding="utf-8"
    )
    (root / "billing.py").write_text(
        "def charge(amount):\n    return amount * 2\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "AI_DIR", root / ".ai")
    monkeypatch.setattr(config, "BM25_INDEX_PATH", root / ".ai" / "bm25_index.json")
    monkeypatch.setattr(config, "INDEX_METADATA_FILE", root / ".ai" / "index_metadata.json")
    # Reset the annotation-store singleton so it picks up the new root
    import annotations as ann_mod

    ann_mod._store = None
    ann_mod._store_path = None
    return root


class TestAnnotationStore:
    def test_set_get_roundtrip(self, project):
        from annotations import AnnotationStore

        store = AnnotationStore(project / ".ai" / "annotations.json")
        ann = store.set("auth.py", "Токени авторизації і перевірка доступу", ["auth", "security"])
        assert ann.path == "auth.py"

        store2 = AnnotationStore(project / ".ai" / "annotations.json")
        loaded = store2.get("auth.py")
        assert loaded is not None
        assert "авторизації" in loaded.summary  # UTF-8 survives roundtrip
        assert loaded.keywords == ["auth", "security"]

    def test_rejects_paths_outside_project(self, project):
        from annotations import AnnotationStore

        store = AnnotationStore(project / ".ai" / "annotations.json")
        with pytest.raises(ValueError):
            store.set("../outside.py", "nope")

    def test_staleness_detection(self, project):
        from annotations import AnnotationStore

        store = AnnotationStore(project / ".ai" / "annotations.json")
        ann = store.set("auth.py", "old summary")
        assert store.is_stale(ann) is False
        # Simulate a later edit
        ann.annotated_mtime -= 100
        assert store.is_stale(ann) is True

    def test_unannotated_scan(self, project):
        from annotations import AnnotationStore

        store = AnnotationStore(project / ".ai" / "annotations.json")
        store.set("auth.py", "auth stuff")
        missing, stale, scanned = store.unannotated(limit=10)
        assert "billing.py" in missing
        assert "auth.py" not in missing
        assert scanned == 2

    def test_keyword_search_scores_summary_and_keywords(self, project):
        from annotations import AnnotationStore

        store = AnnotationStore(project / ".ai" / "annotations.json")
        store.set("auth.py", "Handles user authentication and token checks", ["login"])
        store.set("billing.py", "Charges customers for subscriptions", ["payments"])

        hits = store.search("user authentication login", n=5)
        assert hits and hits[0]["source"] == "auth.py"
        assert hits[0]["tier"] == "L0_annot"

        hits = store.search("payments subscriptions", n=5)
        assert hits and hits[0]["source"] == "billing.py"

    def test_as_documents_shape(self, project):
        from annotations import AnnotationStore

        store = AnnotationStore(project / ".ai" / "annotations.json")
        store.set("auth.py", "auth summary", ["kw"])
        ids, texts, metas = store.as_documents()
        assert ids == ["annot::auth.py"]
        assert "auth summary" in texts[0]
        assert metas[0]["source"] == "auth.py"
        assert metas[0]["symbol_type"] == "annotation"


class TestBM25OnlyMode:
    @pytest.fixture()
    def no_vector(self, monkeypatch):
        import vector_store_manager as vsm

        monkeypatch.setattr(vsm, "_vector_checked", True)
        monkeypatch.setattr(vsm, "_vector_available", False)
        return vsm

    def test_vector_stack_flag(self, no_vector):
        assert no_vector.vector_stack_available() is False

    def test_initialize_returns_false_gracefully(self, project, no_vector):
        vs = no_vector.VectorStoreManager()
        assert vs.initialize() is False

    def test_index_and_search_without_vector_stack(self, project, no_vector):
        from codebase_indexer import CodebaseIndexer

        vs = no_vector.VectorStoreManager()
        indexer = CodebaseIndexer(vs)
        result = indexer.index_all(project, set(), set(), force=False)
        assert "Indexed 2 files" in result

        raw = vs.hybrid_query(["verify token"], n_results=5)
        assert raw is not None and raw["ids"][0], "BM25-only search returned nothing"
        sources = [m.get("source", "") for m in raw["metadatas"][0]]
        assert any("auth.py" in s for s in sources)

    def test_annotations_searchable_in_bm25_only(self, project, no_vector):
        from annotations import get_store
        from codebase_indexer import CodebaseIndexer

        vs = no_vector.VectorStoreManager()
        indexer = CodebaseIndexer(vs)
        indexer.index_all(project, set(), set(), force=False)

        ann = get_store().set("billing.py", "Processes recurring invoices and refunds", ["refund"])
        vs.upsert_bm25_annotation(
            ann.path,
            ann.doc_id,
            ann.search_text(),
            {"source": ann.path, "symbol_type": "annotation", "symbol_name": "billing"},
        )

        raw = vs.hybrid_query(["recurring invoices refund"], n_results=5)
        sources = [m.get("source", "") for m in raw["metadatas"][0]]
        assert "billing.py" in sources

    def test_annotations_survive_reload_via_merge(self, project, no_vector):
        from annotations import get_store
        from codebase_indexer import CodebaseIndexer

        vs = no_vector.VectorStoreManager()
        indexer = CodebaseIndexer(vs)
        indexer.index_all(project, set(), set(), force=False)
        get_store().set("auth.py", "Validates bearer credentials", ["bearer"])

        # Fresh manager simulates a server restart: corpus loads from disk,
        # annotations re-merge from annotations.json
        vs2 = no_vector.VectorStoreManager()
        raw = vs2.hybrid_query(["bearer credentials"], n_results=5)
        assert raw is not None and raw["ids"][0]
        sources = [m.get("source", "") for m in raw["metadatas"][0]]
        assert "auth.py" in sources

    def test_where_filters_unsupported_without_vector(self, project, no_vector):
        vs = no_vector.VectorStoreManager()
        assert vs.hybrid_query(["x"], n_results=3, where={"source": "y"}) is None


class TestAnnotationQueryTier:
    def test_tier_annotations_returns_hits(self, project):
        from annotations import get_store
        from query_router import _tier_annotations

        get_store().set("auth.py", "Session lifecycle and OAuth handshakes", ["oauth"])
        hits = _tier_annotations("oauth handshake", n=5)
        assert hits and hits[0].source == "auth.py"
        assert hits[0].tier == "L0_annot"

    def test_persisted_file_is_utf8_json(self, project):
        from annotations import get_store

        get_store().set("auth.py", "Кирилична анотація", ["ключ"])
        raw = (project / ".ai" / "annotations.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        assert "Кирилична анотація" in raw  # ensure_ascii=False
        assert data["files"]["auth.py"]["keywords"] == ["ключ"]
