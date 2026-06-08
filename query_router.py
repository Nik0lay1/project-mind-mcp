"""
Tier-aware query router.

Tier escalation policy:
  L0 (manifest)   — paths + symbols. Always tried first. ~50 ms.
  L1 (BM25)       — keyword/lexical. Loaded only when L0 is insufficient.
  L2 (vector)     — semantic. Loaded only on explicit semantic intent or
                    when L0+L1 produced nothing useful.

Intents:
  "overview"  -> L0 only
  "lookup"    -> L0 then L1
  "semantic"  -> L0 then L1 then L2
  "deep"      -> L0 then L1 then L2 with relaxed thresholds and more results
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import config
from logger import get_logger

logger = get_logger()


VALID_INTENTS = ("overview", "lookup", "semantic", "deep")


@dataclass
class QueryHit:
    source: str
    score: float
    tier: str
    snippet: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "score": round(self.score, 3),
            "tier": self.tier,
            "snippet": self.snippet,
            "extra": self.extra,
        }


@dataclass
class QueryResult:
    query: str
    intent: str
    hits: list[QueryHit]
    tiers_used: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "intent": self.intent,
            "tiers_used": self.tiers_used,
            "hit_count": len(self.hits),
            "hits": [h.to_dict() for h in self.hits],
            "notes": self.notes,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# QUERY: {self.query}",
            f"**Intent**: {self.intent}",
            f"**Tiers used**: {', '.join(self.tiers_used) or 'none'}",
            f"**Hits**: {len(self.hits)}",
            "",
        ]
        if self.notes:
            lines.append("## Notes")
            for n in self.notes:
                lines.append(f"- {n}")
            lines.append("")
        for i, h in enumerate(self.hits, 1):
            lines.append(f"## {i}. `{h.source}` _(tier={h.tier}, score={h.score:.2f})_")
            if h.snippet:
                lines.append("```")
                lines.append(h.snippet[:1200])
                lines.append("```")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier handlers
# ---------------------------------------------------------------------------


def _tier_l0(query: str, n: int) -> list[QueryHit]:
    from manifest import find_files_by_keyword, get_or_build_manifest

    try:
        m = get_or_build_manifest()
    except Exception as e:
        logger.warning(f"L0 manifest load failed: {e}")
        return []
    entries = find_files_by_keyword(m, query, limit=n)
    hits: list[QueryHit] = []
    for entry in entries:
        snippet = ", ".join(entry.symbols[:8]) if entry.symbols else ""
        hits.append(
            QueryHit(
                source=entry.path,
                score=1.0,
                tier="L0",
                snippet=snippet,
                extra={"size": entry.size, "lang": entry.lang},
            )
        )
    return hits


def _tier_l1(query: str, n: int) -> list[QueryHit]:
    """BM25 / keyword. Loads BM25 index but NOT the embedding model."""
    try:
        from bm25_index import BM25Index
    except Exception as e:
        logger.warning(f"BM25 unavailable: {e}")
        return []
    try:
        idx = BM25Index(config.BM25_INDEX_PATH)
        idx.load()
        if not idx.is_ready:
            return []
        items = idx.search(query, n=n)
    except Exception as e:
        logger.warning(f"BM25 search failed: {e}")
        return []
    hits: list[QueryHit] = []
    for it in items:
        meta = it.get("metadata") or {}
        hits.append(
            QueryHit(
                source=meta.get("source") or it.get("id", ""),
                score=float(it.get("score", 0.0)),
                tier="L1",
                snippet=(it.get("text") or "")[:600],
                extra={"id": it.get("id")},
            )
        )
    return hits


_L2_TIMEOUT_SECONDS = 20.0


def _tier_l2(query: str, n: int) -> list[QueryHit]:
    """Semantic vector search. Only runs if model is already loaded (avoids MCP timeout)."""
    import concurrent.futures

    try:
        from context import get_context

        ctx = get_context()
        vs = ctx.vector_store

        if not vs.is_loaded():
            logger.debug("L2 skipped: embedding model not loaded yet")
            return []

        coll = vs.get_collection()
        if coll is None:
            return []

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(vs.hybrid_query, [query], n)
                raw = future.result(timeout=_L2_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            logger.warning(f"L2 hybrid_query timed out after {_L2_TIMEOUT_SECONDS}s")
            return []
    except Exception as e:
        logger.warning(f"L2 vector query failed: {e}")
        return []
    if not raw or not raw.get("ids") or not raw["ids"][0]:
        return []
    hits: list[QueryHit] = []
    ids = raw["ids"][0]
    docs = (raw.get("documents") or [[]])[0]
    metas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    for i, doc_id in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        dist = distances[i] if i < len(distances) else 0.0
        score = max(0.0, 1.0 - float(dist))
        hits.append(
            QueryHit(
                source=meta.get("source", doc_id),
                score=score,
                tier="L2",
                snippet=(docs[i] if i < len(docs) else "")[:800],
                extra={"id": doc_id, "distance": dist},
            )
        )
    return hits


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


_RRF_K = 60


def _merge_hits(buckets: list[list[QueryHit]], limit: int, k: int = _RRF_K) -> list[QueryHit]:
    """
    Fuse hits from multiple tiers (L0/L1/L2) into a single ranking.

    Tier scores live on incompatible scales (L0 is a flat 1.0, L1 is a raw
    unbounded BM25 score, L2 is 0..1 similarity), so ranking by raw score let
    one tier dominate the others. Instead we use Reciprocal Rank Fusion (RRF),
    which depends only on each item's *rank within its own tier* and is therefore
    scale-invariant. The fused value is normalized to 0..1 (1.0 = top-ranked in
    every contributing tier) so downstream confidence thresholds stay meaningful.
    """
    rrf: dict[str, float] = {}
    best: dict[str, QueryHit] = {}
    contributing = 0
    for bucket in buckets:
        if not bucket:
            continue
        contributing += 1
        for rank, h in enumerate(bucket):
            rrf[h.source] = rrf.get(h.source, 0.0) + 1.0 / (k + rank + 1)
            current = best.get(h.source)
            if current is None or h.score > current.score:
                best[h.source] = h
    if not rrf:
        return []
    max_possible = contributing / (k + 1)
    ordered = sorted(rrf, key=lambda s: rrf[s], reverse=True)
    out: list[QueryHit] = []
    for source in ordered[:limit]:
        base = best[source]
        norm = rrf[source] / max_possible if max_possible > 0 else 0.0
        out.append(
            QueryHit(
                source=base.source,
                score=round(norm, 4),
                tier=base.tier,
                snippet=base.snippet,
                extra=base.extra,
            )
        )
    return out


def query(
    user_query: str,
    intent: str = "lookup",
    n_results: int = 8,
) -> QueryResult:
    """
    Routes a user query through the appropriate tiers.

    Args:
        user_query: Free-form query text
        intent: One of "overview" | "lookup" | "semantic" | "deep"
        n_results: Final number of hits to return
    """
    if not user_query or not user_query.strip():
        return QueryResult(
            query=user_query,
            intent=intent,
            hits=[],
            tiers_used=[],
            notes=["empty query"],
        )

    intent = intent.strip().lower()
    if intent not in VALID_INTENTS:
        intent = "lookup"

    tiers_used: list[str] = []
    notes: list[str] = []
    buckets: list[list[QueryHit]] = []

    # L0
    l0 = _tier_l0(user_query, n=max(n_results * 2, 12))
    if l0:
        tiers_used.append("L0")
        buckets.append(l0)

    if intent == "overview":
        merged = _merge_hits(buckets, n_results)
        if not merged:
            notes.append("no L0 matches; try a more specific keyword")
        return QueryResult(user_query, intent, merged, tiers_used, notes)

    # Decide whether to escalate to L1
    need_more = intent in ("semantic", "deep") or len(l0) < max(3, n_results // 2)
    if need_more:
        l1 = _tier_l1(user_query, n=max(n_results * 2, 12))
        if l1:
            tiers_used.append("L1")
            buckets.append(l1)
        elif intent == "lookup":
            notes.append("BM25 index empty; run `index_codebase()` for keyword search")

    # Decide whether to escalate to L2
    if intent in ("semantic", "deep"):
        merged_so_far = _merge_hits(buckets, n_results)
        weak_signal = not merged_so_far or merged_so_far[0].score < 0.4 or intent == "deep"
        if weak_signal:
            try:
                from context import get_context as _get_ctx

                _l2_loaded = _get_ctx().vector_store.is_loaded()
            except Exception:
                _l2_loaded = False
            if not _l2_loaded:
                notes.append(
                    "L2 (vector) skipped: embedding model not loaded yet — "
                    "run `index_codebase()` to load it"
                )
            else:
                l2_n = max(n_results * 2, 12) if intent == "deep" else n_results
                l2 = _tier_l2(user_query, n=l2_n)
                if l2:
                    tiers_used.append("L2")
                    buckets.append(l2)
                else:
                    notes.append("vector tier unavailable; index may be empty")

    final = _merge_hits(buckets, n_results)
    if not final:
        notes.append(
            "no hits across L0/L1/L2 — index might be missing; "
            "try `index_codebase()` or refine the query"
        )
    return QueryResult(user_query, intent, final, tiers_used, notes)
