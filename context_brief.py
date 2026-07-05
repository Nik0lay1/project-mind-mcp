"""Deterministic context brief for coding tasks — zero LLM calls.

The scout pyramid, layer 0. Given a task description (plus optional hints
extracted by the client: explicit file mentions, identifiers, stack frames),
assemble a compact, ranked CONTEXT BRIEF from data the index already has:

  1. hybrid retrieval (vector when available, BM25 otherwise) for the task text
     and hint symbols;
  2. one-hop expansion over the static import graph (dependents = centrality);
  3. file skeletons from the manifest (symbol names, no bodies);
  4. git/mtime recency boost;
  5. greedy packing into a token budget, best-first.

The frontier planner consumes this brief instead of reading raw files, so the
expensive model pays for conclusions, not dumps. Everything here is
best-effort: any missing subsystem (no vector store, no manifest, no git)
degrades to whatever remains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from logger import setup_logger

logger = setup_logger(__name__)

# Rough chars-per-token for mixed code/text; we only need budget-order accuracy.
_CHARS_PER_TOKEN = 4
_MAX_CANDIDATES = 24
_EXCERPT_CHARS = 600
_TOP_EXCERPTS = 5


@dataclass
class _Candidate:
    path: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    excerpt: str = ""
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _match_manifest_path(candidate: str, manifest_paths: list[str]) -> str | None:
    """Match a possibly-partial client hint (auth.ts, src/lib/url.ts) to an indexed path."""
    c = _norm(candidate).lower()
    exact = [p for p in manifest_paths if p.lower() == c]
    if exact:
        return exact[0]
    suffix = [p for p in manifest_paths if p.lower().endswith("/" + c) or p.lower().endswith(c)]
    if len(suffix) == 1:
        return suffix[0]
    if suffix:
        # Ambiguous bare name — prefer the shortest path (closest to root).
        return min(suffix, key=len)
    return None


def build_context_brief(
    task: str,
    budget_tokens: int = 4000,
    hint_files: list[str] | None = None,
    hint_symbols: list[str] | None = None,
) -> str:
    from context import get_context

    hint_files = hint_files or []
    hint_symbols = hint_symbols or []
    candidates: dict[str, _Candidate] = {}

    def cand(path: str) -> _Candidate:
        p = _norm(path)
        if p not in candidates:
            candidates[p] = _Candidate(path=p)
        return candidates[p]

    # ── Manifest: paths, skeletons, mtime ────────────────────────────────────
    manifest_paths: list[str] = []
    symbols_by_path: dict[str, list[str]] = {}
    mtime_by_path: dict[str, float] = {}
    try:
        from manifest import load_manifest

        m = load_manifest()
        if m:
            for entry in m.files:
                p = _norm(entry.path)
                manifest_paths.append(p)
                symbols_by_path[p] = list(entry.symbols or [])
                mtime_by_path[p] = float(entry.mtime or 0.0)
    except Exception as e:  # noqa: BLE001 — brief is best-effort by design
        logger.debug("context_brief: manifest unavailable: %s", e)

    # ── Retrieval: task text + hint symbols through hybrid query ────────────
    try:
        ctx = get_context()
        queries = [task[:500]] + [s for s in hint_symbols[:6] if s.strip()]
        res = ctx.vector_store.hybrid_query(query_texts=queries, n_results=6)
        if res:
            ids = res.get("ids") or []
            docs = res.get("documents") or []
            metas = res.get("metadatas") or []
            dists = res.get("distances") or []
            for qi in range(len(ids)):
                q_weight = 3.0 if qi == 0 else 1.2  # завдання важить більше за окремий символ
                for ri, _id in enumerate(ids[qi]):
                    meta = (metas[qi][ri] or {}) if ri < len(metas[qi]) else {}
                    source = _norm(str(meta.get("source", "")))
                    if not source:
                        continue
                    dist = float(dists[qi][ri]) if ri < len(dists[qi]) else 1.0
                    relevance = max(0.0, 1.0 - dist)
                    c = cand(source)
                    c.score += q_weight * relevance
                    if "retrieval" not in c.reasons:
                        c.reasons.append("retrieval")
                    doc = str(docs[qi][ri]) if ri < len(docs[qi]) else ""
                    if doc and len(doc) > len(c.excerpt):
                        c.excerpt = doc[:_EXCERPT_CHARS]
    except Exception as e:  # noqa: BLE001
        logger.debug("context_brief: hybrid retrieval failed: %s", e)

    # ── Explicit hints from the client are the strongest signal ─────────────
    for hf in hint_files[:10]:
        matched = _match_manifest_path(hf, manifest_paths) if manifest_paths else _norm(hf)
        if matched:
            c = cand(matched)
            c.score += 5.0
            c.reasons.append("mentioned in task")

    # ── Import graph: centrality + one-hop expansion ────────────────────────
    try:
        from code_intelligence import build_import_graph

        graph = build_import_graph(Path(config.PROJECT_ROOT))
        reverse: dict[str, list[str]] = {}
        for src, targets in graph.items():
            for t in targets:
                reverse.setdefault(_norm(t), []).append(_norm(src))

        for p, c in list(candidates.items()):
            c.imports = [_norm(t) for t in graph.get(p, [])][:8]
            c.imported_by = reverse.get(p, [])[:8]
            c.score += min(len(reverse.get(p, [])), 6) * 0.15  # центральність
            if c.score >= 3.0:
                # один хоп: сусіди сильних кандидатів — слабкі кандидати
                for n in (c.imports + c.imported_by)[:6]:
                    nc = cand(n)
                    if nc.score == 0.0:
                        nc.score = 0.5
                        nc.reasons.append(f"graph neighbor of {p}")
    except Exception as e:  # noqa: BLE001
        logger.debug("context_brief: import graph failed: %s", e)

    # ── Recency boost (git first, mtime fallback) ────────────────────────────
    try:
        ctx = get_context()
        if ctx.git_repo:
            recent = ctx.git_repo.get_recently_changed_files(days=14, max_files=50)
            for p in recent:
                np_ = _norm(p)
                if np_ in candidates:
                    candidates[np_].score += 0.8
                    candidates[np_].reasons.append("recently changed")
    except Exception as e:  # noqa: BLE001
        logger.debug("context_brief: git recency failed: %s", e)

    if not candidates:
        return ""

    for c in candidates.values():
        c.symbols = symbols_by_path.get(c.path, [])[:12]

    ranked = sorted(candidates.values(), key=lambda c: c.score, reverse=True)[:_MAX_CANDIDATES]

    # ── Greedy packing into the token budget ────────────────────────────────
    budget_chars = max(2000, budget_tokens * _CHARS_PER_TOKEN)
    lines: list[str] = ["## Ranked relevant files (deterministic scout)"]
    used = len(lines[0])
    excerpts_used = 0
    for i, c in enumerate(ranked, 1):
        block: list[str] = [f"\n### {i}. {c.path}  (score {c.score:.1f}; {', '.join(c.reasons) or 'candidate'})"]
        if c.symbols:
            block.append(f"symbols: {', '.join(c.symbols)}")
        if c.imported_by:
            block.append(f"imported by ({len(c.imported_by)}): {', '.join(c.imported_by[:5])}")
        if c.imports:
            block.append(f"imports: {', '.join(c.imports[:5])}")
        if c.excerpt and excerpts_used < _TOP_EXCERPTS:
            block.append("relevant excerpt:\n```\n" + c.excerpt.strip() + "\n```")
            excerpts_used += 1
        text = "\n".join(block)
        if used + len(text) > budget_chars:
            if i <= 3:  # топ-3 файли входять завжди, навіть якщо бюджет тісний
                text = text[: max(0, budget_chars - used)]
                lines.append(text)
            break
        lines.append(text)
        used += len(text)

    lines.append(
        "\n---\nBrief is deterministic (index + import graph, no LLM). "
        "Read files directly only if something essential is missing."
    )
    return "\n".join(lines)
