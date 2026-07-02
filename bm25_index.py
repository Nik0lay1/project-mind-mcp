import json
import threading
from pathlib import Path
from typing import Any

from logger import get_logger

logger = get_logger()

_RRF_K = 60


def reciprocal_rank_fusion(
    vector_results: list[dict[str, Any]],
    bm25_results: list[dict[str, Any]],
    n: int,
) -> list[dict[str, Any]]:
    rrf_scores: dict[str, float] = {}
    id_to_data: dict[str, dict[str, Any]] = {}

    for rank, item in enumerate(vector_results):
        doc_id = item["id"]
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
        id_to_data[doc_id] = item

    for rank, item in enumerate(bm25_results):
        doc_id = item["id"]
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
        if doc_id not in id_to_data:
            id_to_data[doc_id] = item

    sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[:n]
    return [id_to_data[doc_id] for doc_id in sorted_ids if doc_id in id_to_data]


class BM25Index:
    """
    BM25 keyword index over the vector-store corpus.

    Persistence is JSON (never pickle — the index file lives inside the *target*
    project's .ai/ directory, so unpickling it would execute untrusted input).
    All state swaps happen atomically under a lock so a search running
    concurrently with a rebuild always sees a consistent (ids, texts, bm25) set.
    """

    def __init__(self, persist_path: Path) -> None:
        self.persist_path = persist_path
        self._lock = threading.Lock()
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._metadatas: list[dict[str, Any]] = []
        self._bm25: Any = None

    @property
    def is_ready(self) -> bool:
        return self._bm25 is not None and len(self._ids) > 0

    @property
    def has_corpus(self) -> bool:
        """True when documents are loaded in memory (matrix may lag behind)."""
        return len(self._ids) > 0

    def build(self, ids: list[str], texts: list[str], metadatas: list[dict[str, Any]]) -> None:
        try:
            from rank_bm25 import BM25Okapi

            # Build everything into locals first, then swap under the lock so
            # a failed build or concurrent search never sees mismatched arrays.
            new_ids = list(ids)
            new_texts = list(texts)
            new_metas = list(metadatas)
            tokenized = [t.lower().split() for t in new_texts]
            new_bm25 = BM25Okapi(tokenized) if tokenized else None

            with self._lock:
                self._ids = new_ids
                self._texts = new_texts
                self._metadatas = new_metas
                self._bm25 = new_bm25
            logger.info(f"BM25 index built with {len(new_ids)} documents")
        except Exception as e:
            logger.error(f"Failed to build BM25 index: {e}")

    def update_source(
        self,
        source: str,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> bool:
        """
        Replace all documents of one source file in the in-memory corpus.

        Cheap corpus surgery for incremental indexing: drops the file's old
        rows and appends the new ones WITHOUT touching the BM25 matrix —
        call rebuild_from_corpus() once after all files are updated.

        Returns:
            False when the in-memory corpus is not loaded (caller should fall
            back to a full rebuild from the vector store).
        """
        with self._lock:
            if not self._ids:
                return False
            keep = [
                i
                for i, meta in enumerate(self._metadatas)
                if (meta or {}).get("source") != source
            ]
            if len(keep) != len(self._ids):
                self._ids = [self._ids[i] for i in keep]
                self._texts = [self._texts[i] for i in keep]
                self._metadatas = [self._metadatas[i] for i in keep]
            self._ids.extend(ids)
            self._texts.extend(texts)
            self._metadatas.extend(metadatas)
            # Corpus and matrix are now out of sync until rebuild_from_corpus().
            self._bm25 = None
        return True

    def remove_source(self, source: str) -> None:
        """Drop all documents of a deleted source file from the corpus."""
        self.update_source(source, [], [], [])

    def rebuild_from_corpus(self) -> None:
        """Rebuild the BM25 matrix from the current in-memory corpus."""
        with self._lock:
            ids = list(self._ids)
            texts = list(self._texts)
            metas = list(self._metadatas)
        self.build(ids, texts, metas)

    def search(self, query: str, n: int) -> list[dict[str, Any]]:
        with self._lock:
            bm25 = self._bm25
            ids = self._ids
            texts = self._texts
            metadatas = self._metadatas
        if bm25 is None or not ids:
            return []
        try:
            tokens = query.lower().split()
            scores = bm25.get_scores(tokens)
            top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
            return [
                {
                    "id": ids[i],
                    "text": texts[i],
                    "metadata": metadatas[i],
                    "score": float(scores[i]),
                }
                for i in top_indices
                if scores[i] > 0
            ]
        except Exception as e:
            logger.error(f"BM25 search failed: {e}")
            return []

    def save(self) -> None:
        try:
            from incremental_indexing import atomic_write

            with self._lock:
                data = {
                    "version": 2,
                    "ids": self._ids,
                    "texts": self._texts,
                    "metadatas": self._metadatas,
                }
            atomic_write(self.persist_path, json.dumps(data, ensure_ascii=False))
            logger.info(f"BM25 index saved ({len(data['ids'])} docs)")
        except Exception as e:
            logger.error(f"Failed to save BM25 index: {e}")

    def load(self) -> bool:
        if not self.persist_path.exists():
            return False
        try:
            with open(self.persist_path, encoding="utf-8") as f:
                data = json.load(f)
            ids = data["ids"]
            texts = data["texts"]
            metadatas = data["metadatas"]
        except Exception as e:
            logger.warning(f"Failed to load BM25 index (will rebuild on next indexing): {e}")
            return False
        self.build(ids, texts, metadatas)
        return self.is_ready

    def clear(self) -> None:
        with self._lock:
            self._ids = []
            self._texts = []
            self._metadatas = []
            self._bm25 = None
        for stale in (self.persist_path, self.persist_path.with_suffix(".pkl")):
            if stale.exists():
                try:
                    stale.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete BM25 index file {stale.name}: {e}")
