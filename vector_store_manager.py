import hashlib
import json
from typing import Any

import config
from bm25_index import BM25Index, reciprocal_rank_fusion
from cache_manager import TTLCache
from logger import get_logger

logger = get_logger()

# Cached probe for the optional vector stack ([vector] extra). The core
# install runs BM25 + annotations + symbol graph only.
_vector_checked = False
_vector_available = False


def vector_stack_available() -> bool:
    """True when chromadb + sentence-transformers are importable."""
    global _vector_checked, _vector_available
    if not _vector_checked:
        import importlib.util

        _vector_available = (
            importlib.util.find_spec("chromadb") is not None
            and importlib.util.find_spec("sentence_transformers") is not None
        )
        _vector_checked = True
        if not _vector_available:
            logger.info(
                "Vector stack not installed — running in BM25-only mode "
                "(install `projectmind-mcp[vector]` to enable semantic embeddings)"
            )
    return _vector_available


class VectorStoreManager:
    """
    Manages ChromaDB vector store operations.
    Encapsulates client, collection, and embedding function management.
    """

    def __init__(self, collection_name: str = "project_codebase") -> None:
        """
        Initialize vector store manager.

        Args:
            collection_name: Name of the ChromaDB collection
        """
        import time as _time

        self.collection_name = collection_name
        self.chroma_client: Any = None
        self.collection: Any = None
        self.embedding_fn: Any = None
        self._initialized = False
        self._query_cache = TTLCache(ttl_seconds=300, max_size=100)
        self._bm25_index = BM25Index(config.BM25_INDEX_PATH)
        self._annotations_merged = False
        self._last_query_at: float = 0.0
        self._loaded_at: float = 0.0
        self._time = _time
        import threading

        self._init_lock = threading.Lock()

    def is_loaded(self) -> bool:
        """Returns True iff the embedding model + collection are currently loaded.

        Cheap, never triggers initialisation. Used by the maintenance daemon and
        diagnostic tools.
        """
        return bool(self._initialized and self.collection is not None)

    def unload_model(self) -> None:
        """Releases the SentenceTransformer model and the Chroma collection.

        Subsequent queries will lazily reload via `get_collection()`.
        """
        with self._init_lock:
            self.embedding_fn = None
            self.collection = None
            self._initialized = False
            self._query_cache.clear()

    def initialize(self) -> bool:
        """
        Initializes ChromaDB client, embedding function, and collection.

        Returns:
            True if initialization successful, False otherwise
        """
        if not vector_stack_available():
            return False

        if self._initialized and self.collection is not None:
            return True

        with self._init_lock:
            if self._initialized and self.collection is not None:
                return True

            logger.info("Initializing Vector Store (this may take 30-60 seconds on first run)...")

            try:
                import chromadb
                from chromadb.utils import embedding_functions
                from sentence_transformers import SentenceTransformer

                class LocalSentenceTransformerEmbeddingFunction(embedding_functions.EmbeddingFunction):  # type: ignore[type-arg]
                    def __init__(self, model_name: str) -> None:
                        revision = config.get_model_revision()
                        logger.info(
                            f"Loading SentenceTransformer model '{model_name}' @ {revision[:12]}..."
                        )
                        # Try the local cache first. A plain load issues ~20 HTTP
                        # round-trips to the Hub even when every file is already
                        # cached, so a slow or offline network turned "load the
                        # cached model" into a multi-minute stall.
                        import torch

                        device = "cuda" if torch.cuda.is_available() else "cpu"
                        try:
                            self.model = SentenceTransformer(
                                model_name, revision=revision, device=device, local_files_only=True
                            )
                            logger.info(f"Model loaded from local cache (device: {device})")
                        except Exception as cache_miss:
                            logger.info(
                                f"Model not in local cache ({cache_miss}); "
                                "downloading from the Hub (one-off, ~330 MB)..."
                            )
                            self.model = SentenceTransformer(
                                model_name, revision=revision, device=device
                            )
                            logger.info(
                                f"Model downloaded and loaded successfully (device: {device})"
                            )

                    def __call__(self, input: list[str]) -> list[list[float]]:  # type: ignore[override]
                        return self.model.encode(input, show_progress_bar=False).tolist()  # type: ignore[return-value]

                self.chroma_client = chromadb.PersistentClient(path=str(config.VECTOR_STORE_DIR))
                logger.info("ChromaDB client initialized")
                self.embedding_fn = LocalSentenceTransformerEmbeddingFunction(config.MODEL_NAME)
                self.collection = self.chroma_client.get_or_create_collection(
                    name=self.collection_name,
                    embedding_function=self.embedding_fn,
                    metadata={"hnsw:space": "cosine"},
                )

                self._initialized = True
                self._loaded_at = self._time.time()
                logger.info("Vector Store initialized successfully")
                self._bm25_index.load()
                self.merge_annotations_into_bm25()
                return True
            except Exception as e:
                logger.error(f"Failed to initialize ChromaDB: {e}", exc_info=True)
                return False

    def get_collection(self) -> Any:
        """
        Gets the collection, initializing if needed.

        Returns:
            ChromaDB collection or None if initialization failed
        """
        if not self._initialized:
            if not self.initialize():
                return None
        return self.collection

    def clear_collection(self) -> str | None:
        """
        Clears the current collection by deleting and recreating it.

        Returns:
            Error message if failed, None if successful
        """
        if not vector_stack_available():
            # BM25-only mode: "the collection" is the keyword corpus.
            self._bm25_index.clear()
            self._annotations_merged = False
            self._query_cache.clear()
            logger.info("BM25 index cleared (vector stack not installed)")
            return None

        # Initialize (or re-initialize after unload_model) so the collection is
        # recreated with the configured embedding function, not Chroma's default.
        if not self.chroma_client or self.embedding_fn is None:
            if not self.initialize():
                return "ChromaDB client not initialized"

        try:
            self.chroma_client.delete_collection(self.collection_name)
            self.collection = self.chroma_client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )
            self._bm25_index.clear()
            self._annotations_merged = False
            self._query_cache.clear()
            logger.info(f"Collection '{self.collection_name}' cleared successfully")
            return None
        except Exception as e:
            error_msg = f"Error clearing collection: {e}"
            logger.error(error_msg)
            return error_msg

    def get_count(self) -> int | None:
        """
        Gets the number of items in the collection.

        Returns:
            Number of items, or None if collection not initialized
        """
        if not self._initialized or self.collection is None:
            return None

        try:
            return self.collection.count()
        except Exception as e:
            logger.error(f"Error getting collection count: {e}")
            return None

    def query(
        self,
        query_texts: list[str],
        n_results: int = 5,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Queries the vector store with caching.

        Args:
            query_texts: List of query strings
            n_results: Number of results to return
            where: Optional metadata filter
            where_document: Optional document content filter

        Returns:
            Query results or None if query failed
        """
        cache_key = self._generate_cache_key(query_texts, n_results, where, where_document)
        cached_result = self._query_cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Cache hit for query: {query_texts[0][:50]}...")
            return cached_result

        coll = self.get_collection()
        if coll is None:
            return None

        try:
            result: dict[str, Any] = coll.query(
                query_texts=query_texts,
                n_results=n_results,
                where=where,
                where_document=where_document,
            )
            self._query_cache.put(cache_key, result)
            self._last_query_at = self._time.time()
            return result
        except Exception as e:
            logger.error(f"Error querying collection: {e}", exc_info=True)
            return None

    def _generate_cache_key(
        self,
        query_texts: list[str],
        n_results: int,
        where: dict[str, Any] | None,
        where_document: dict[str, Any] | None,
    ) -> str:
        """
        Generates a unique cache key for query parameters.

        Args:
            query_texts: List of query strings
            n_results: Number of results
            where: Metadata filter
            where_document: Document filter

        Returns:
            Hash string for cache key
        """
        key_data = {
            "query_texts": query_texts,
            "n_results": n_results,
            "where": where,
            "where_document": where_document,
        }
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    def get_query_cache_stats(self) -> dict[str, Any]:
        """
        Returns query cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        return self._query_cache.get_stats()

    def upsert(self, documents: list[str], metadatas: list[dict], ids: list[str]) -> bool:
        """
        Upserts documents into the collection.

        Args:
            documents: List of document texts
            metadatas: List of metadata dicts
            ids: List of document IDs

        Returns:
            True if successful, False otherwise
        """
        coll = self.get_collection()
        if coll is None:
            return False

        try:
            coll.upsert(documents=documents, metadatas=metadatas, ids=ids)
            self._query_cache.clear()
            return True
        except Exception as e:
            logger.error(f"Error upserting to collection: {e}", exc_info=True)
            return False

    def delete_by_source(self, source: str) -> bool:
        """
        Deletes all chunks whose metadata `source` matches, so re-indexing a
        changed file (or dropping a deleted one) leaves no stale chunks behind.

        Returns:
            True if the delete call succeeded (including no-op), False on error.
        """
        coll = self.get_collection()
        if coll is None:
            return False
        try:
            coll.delete(where={"source": source})
            self._query_cache.clear()
            return True
        except Exception as e:
            logger.error(f"Error deleting chunks for {source}: {e}", exc_info=True)
            return False

    def get_all_documents(self) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        """
        Fetches all documents from ChromaDB for BM25 rebuild.

        Returns:
            Tuple of (ids, texts, metadatas)
        """
        coll = self.get_collection()
        if coll is None:
            return [], [], []
        try:
            total = coll.count()
            if total == 0:
                return [], [], []
            result = coll.get(include=["documents", "metadatas"])
            ids: list[str] = result.get("ids", [])
            docs: list[str] = result.get("documents", []) or []
            metas: list[dict[str, Any]] = result.get("metadatas", []) or []
            return ids, docs, metas
        except Exception as e:
            logger.error(f"Error fetching all documents: {e}")
            return [], [], []

    def _ensure_bm25_ready(self) -> None:
        """Loads the BM25 corpus from disk and merges annotation docs (idempotent)."""
        if not self._bm25_index.has_corpus:
            self._bm25_index.load()
        self.merge_annotations_into_bm25()

    def merge_annotations_into_bm25(self, force: bool = False) -> int:
        """
        Injects AI-authored file annotations into the BM25 corpus as synthetic
        documents, so natural-language queries match them via keyword search.

        Idempotent per session (guarded by a flag); pass force=True after the
        annotation store changed.
        """
        if self._annotations_merged and not force:
            return 0
        try:
            from annotations import get_store

            ids, texts, metas = get_store().as_documents()
        except Exception as e:
            logger.warning(f"Could not merge annotations into BM25: {e}")
            return 0
        count = 0
        for doc_id, text, meta in zip(ids, texts, metas, strict=True):
            if self._bm25_index.update_source(
                meta["source"], [doc_id], [text], [meta], allow_empty=True
            ):
                count += 1
        self._annotations_merged = True
        if count:
            self._query_cache.clear()
        return count

    def upsert_bm25_annotation(self, source: str, doc_id: str, text: str, meta: dict) -> None:
        """
        Live-update one annotation in the BM25 corpus (matrix rebuilds lazily
        on the next search). Not persisted here — annotations.json is the
        source of truth and is re-merged on every load/rebuild.
        """
        self._ensure_bm25_ready()
        self._bm25_index.update_source(source, [doc_id], [text], [meta], allow_empty=True)
        self._query_cache.clear()

    def rebuild_bm25(self) -> None:
        """Rebuilds BM25 index from all ChromaDB documents + annotations, persists it."""
        ids, docs, metas = self.get_all_documents()
        self._bm25_index.build(ids, docs, metas)
        self._annotations_merged = False
        self.merge_annotations_into_bm25()
        if not self._bm25_index.has_corpus:
            logger.warning("No documents to build BM25 index from")
            return
        self._bm25_index.save()
        self._query_cache.clear()

    def update_bm25_source(
        self, source: str, ids: list[str], texts: list[str], metadatas: list[dict]
    ) -> bool:
        """
        Incrementally replaces one file's documents in the BM25 corpus.

        In vector mode an unloaded corpus returns False so the caller falls
        back to a full rebuild from the vector store. In BM25-only mode there
        is nothing to rebuild from, so growing an empty corpus is allowed.
        """
        if not self._bm25_index.has_corpus:
            self._bm25_index.load()
            self.merge_annotations_into_bm25()
        return self._bm25_index.update_source(
            source, ids, texts, metadatas, allow_empty=not vector_stack_available()
        )

    def remove_bm25_source(self, source: str) -> None:
        """Drops one deleted file's documents from the BM25 corpus."""
        self._bm25_index.remove_source(source)

    def finalize_bm25(self, incremental_ok: bool = False) -> None:
        """
        Rebuilds the BM25 matrix after an indexing run.

        With `incremental_ok=True` and a loaded corpus, rebuilds from memory —
        no full ChromaDB fetch, which on large repos turns every small
        `index_changed_files` call from minutes into seconds.
        """
        if incremental_ok and self._bm25_index.has_corpus:
            self.merge_annotations_into_bm25(force=True)
            self._bm25_index.rebuild_from_corpus()
            self._bm25_index.save()
            self._query_cache.clear()
        else:
            self.rebuild_bm25()

    def hybrid_query(
        self,
        query_texts: list[str],
        n_results: int = 5,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Hybrid search: combines vector (ChromaDB) + keyword (BM25) via Reciprocal Rank Fusion.
        Falls back to pure vector search when metadata filters are present or BM25 is not ready.

        Args:
            query_texts: List of query strings (uses first element)
            n_results: Number of results to return
            where: Optional metadata filter (disables BM25)
            where_document: Optional document content filter (disables BM25)

        Returns:
            Query results in ChromaDB format or None if failed
        """
        if not vector_stack_available():
            # BM25-only mode: keyword search over chunks + annotations.
            if where or where_document:
                return None  # metadata filters require the vector store
            self._ensure_bm25_ready()
            items = self._bm25_index.search(query_texts[0], n=n_results)
            self._last_query_at = self._time.time()
            return {
                "ids": [[it["id"] for it in items]],
                "documents": [[it["text"] for it in items]],
                "metadatas": [[it["metadata"] for it in items]],
                # No measured semantic distance in keyword-only mode
                "distances": [[0.5 for _ in items]],
            }

        if where or where_document or not self._bm25_index.is_ready:
            return self.query(query_texts, n_results, where, where_document)

        cache_key = "hybrid_" + self._generate_cache_key(query_texts, n_results, None, None)
        cached = self._query_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Hybrid cache hit for: {query_texts[0][:50]}")
            return cached

        fetch_n = min(n_results * 3, 50)
        query_text = query_texts[0]

        vector_raw = self.query(query_texts, n_results=fetch_n)
        vector_items: list[dict[str, Any]] = []
        if vector_raw and vector_raw.get("ids") and vector_raw["ids"][0]:
            for i, doc_id in enumerate(vector_raw["ids"][0]):
                vector_items.append(
                    {
                        "id": doc_id,
                        "text": (
                            (vector_raw.get("documents") or [[]])[0][i]
                            if vector_raw.get("documents")
                            else ""
                        ),
                        "metadata": (
                            (vector_raw.get("metadatas") or [[]])[0][i]
                            if vector_raw.get("metadatas")
                            else {}
                        ),
                        "distance": (
                            (vector_raw.get("distances") or [[]])[0][i]
                            if vector_raw.get("distances")
                            else 0.0
                        ),
                    }
                )

        bm25_items = self._bm25_index.search(query_text, n=fetch_n)

        merged = reciprocal_rank_fusion(vector_items, bm25_items, n=n_results)

        result: dict[str, Any] = {
            "ids": [[item["id"] for item in merged]],
            "documents": [[item["text"] for item in merged]],
            "metadatas": [[item["metadata"] for item in merged]],
            # BM25-only hits have no measured semantic distance; use a neutral
            # 0.5 so they are not reported as "100% relevant" (distance 0).
            "distances": [[item.get("distance", 0.5) for item in merged]],
        }
        self._query_cache.put(cache_key, result)
        return result
