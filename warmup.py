"""
The process's heavy imports, done once, on the main thread.

`import numpy` (reached through chromadb, sentence_transformers and
langchain_text_splitters) loads `numpy._core._multiarray_umath` — a C extension
that pulls OpenBLAS in with it. On Windows, loading that extension from a
background thread of this server wedges inside `LoadLibrary` and never returns.
Captured from a hung server, unchanged across fourteen stack dumps spanning
nine minutes:

    thread pm-warm-imports
      warmup.py -> import symbol_graph -> ast_splitter -> langchain_text_splitters
        -> numpy/_core/overrides.py:8  from numpy._core._multiarray_umath import (
        -> importlib._bootstrap_external:1293 create_module        [never returns]

    thread AnyIO worker (search_codebase) -> waiting
    thread pm-warm-project                -> waiting

The same import on the main thread takes ~10 s. That asymmetry is the whole
bug: whichever thread first needed embeddings did the import, and when that
happened to be the background indexer or a tool worker, the server stopped
answering — the shape the user saw as "no response or progress for 1800s".

So the imports are no longer lazy and no longer threaded. `ensure_loaded()` runs
once from `mcp_server.main()`, on the main thread, before the server starts
serving. Everything that later needs these modules still calls it, and gets an
immediate no-op — which is what makes the call sites safe to leave in place.
"""

from __future__ import annotations

import threading
import time

from logger import get_logger

logger = get_logger()

_lock = threading.RLock()
_loaded = False


def ensure_loaded() -> None:
    """
    Import the heavy modules if that has not happened yet.

    MUST be reached from the main thread first (`mcp_server.main()` does this).
    Later calls from any thread are a dict lookup. Never raises: a missing
    optional dependency leaves the process in BM25-only mode, which the callers
    already handle.
    """
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        started = time.monotonic()
        if threading.current_thread() is not threading.main_thread():
            logger.warning(
                "Heavy imports are being loaded from a background thread "
                f"({threading.current_thread().name}); on Windows this can hang "
                "inside the numpy extension loader. mcp_server.main() should "
                "have loaded them on the main thread already."
            )
        try:
            import symbol_graph  # noqa: F401  (tree-sitter grammars)
        except Exception as exc:
            logger.warning(f"Symbol-graph module could not be imported: {exc}")
        try:
            from vector_store_manager import vector_stack_available

            if vector_stack_available():
                import chromadb  # noqa: F401
                import sentence_transformers  # noqa: F401  (numpy/torch)
        except Exception as exc:
            logger.warning(f"Vector stack could not be imported: {exc}")
        _loaded = True
        logger.info(f"Heavy imports loaded in {time.monotonic() - started:.1f}s")


def is_loaded() -> bool:
    """True once the heavy imports have completed."""
    return _loaded
