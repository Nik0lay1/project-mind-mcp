import pickle
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
    def __init__(self, persist_path: Path) -> None:
        self.persist_path = persist_path
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._metadatas: list[dict[str, Any]] = []
        self._bm25: Any = None

    @property
    def is_ready(self) -> bool:
        return self._bm25 is not None and len(self._ids) > 0

    def build(self, ids: list[str], texts: list[str], metadatas: list[dict[str, Any]]) -> None:
        try:
            from rank_bm25 import BM25Okapi

            self._ids = ids
            self._texts = texts
            self._metadatas = metadatas
            tokenized = [t.lower().split() for t in texts]
            self._bm25 = BM25Okapi(tokenized)
            logger.info(f"BM25 index built with {len(ids)} documents")
        except Exception as e:
            logger.error(f"Failed to build BM25 index: {e}")

    def search(self, query: str, n: int) -> list[dict[str, Any]]:
        if not self.is_ready:
            return []
        try:
            tokens = query.lower().split()
            scores = self._bm25.get_scores(tokens)
            top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
            return [
                {
                    "id": self._ids[i],
                    "text": self._texts[i],
                    "metadata": self._metadatas[i],
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
            data = {
                "ids": self._ids,
                "texts": self._texts,
                "metadatas": self._metadatas,
                "bm25": self._bm25,
            }
            with open(self.persist_path, "wb") as f:
                pickle.dump(data, f)
            logger.info(f"BM25 index saved ({len(self._ids)} docs)")
        except Exception as e:
            logger.error(f"Failed to save BM25 index: {e}")

    def load(self) -> bool:
        if not self.persist_path.exists():
            return False
        try:
            with open(self.persist_path, "rb") as f:
                data = pickle.load(f)
            self._ids = data["ids"]
            self._texts = data["texts"]
            self._metadatas = data["metadatas"]
            self._bm25 = data["bm25"]
            logger.info(f"BM25 index loaded ({len(self._ids)} documents)")
            return True
        except Exception as e:
            logger.warning(f"Failed to load BM25 index: {e}")
            return False

    def clear(self) -> None:
        self._ids = []
        self._texts = []
        self._metadatas = []
        self._bm25 = None
        if self.persist_path.exists():
            try:
                self.persist_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete BM25 index file: {e}")
