"""
The heavy imports must be done once, on the main thread, before serving.

Loading `numpy._core._multiarray_umath` from a background thread of this server
wedges inside the Windows extension loader and never returns, which took the
whole server down with it. `main()` therefore imports on the main thread, and
every later caller must find the work already done.
"""

from __future__ import annotations

import threading

import warmup


def test_ensure_loaded_is_idempotent_and_cheap_from_threads() -> None:
    warmup.ensure_loaded()
    assert warmup.is_loaded()

    # Whatever a worker thread does later must be a no-op, not another import.
    finished = threading.Event()

    def worker() -> None:
        warmup.ensure_loaded()
        finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    assert finished.is_set(), "ensure_loaded() blocked in a worker thread"


def test_heavy_modules_are_actually_imported() -> None:
    import sys

    warmup.ensure_loaded()
    assert "symbol_graph" in sys.modules

    from vector_store_manager import vector_stack_available

    if vector_stack_available():
        assert "sentence_transformers" in sys.modules
        assert "chromadb" in sys.modules


def test_main_loads_before_serving() -> None:
    """`main()` must call ensure_loaded() before mcp.run(), on the main thread."""
    import inspect

    import mcp_server

    source = inspect.getsource(mcp_server.main)
    assert "warmup.ensure_loaded()" in source
    assert source.index("warmup.ensure_loaded()") < source.index("mcp.run()")
    assert "ensure_loaded_async" not in source, "heavy imports must not be threaded"
