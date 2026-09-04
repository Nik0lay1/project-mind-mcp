"""Application context for dependency injection."""

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codebase_indexer import CodebaseIndexer
    from git_utils import GitRepository
    from memory_manager import MemoryManager
    from symbol_graph import SymbolGraph
    from vector_store_manager import VectorStoreManager


@dataclass
class AppContext:
    """
    Application context holding all service instances.
    Provides dependency injection for MCP tools.
    """

    vector_store: "VectorStoreManager"
    memory_manager: "MemoryManager"
    indexer: "CodebaseIndexer"
    git_repo: "GitRepository | None" = None
    _symbol_graph: "SymbolGraph | None" = None

    @classmethod
    def create_default(cls) -> "AppContext":
        """
        Creates default application context with standard configuration.

        Returns:
            Configured AppContext instance
        """
        from codebase_indexer import CodebaseIndexer
        from git_utils import GitRepository
        from memory_manager import MemoryManager
        from vector_store_manager import VectorStoreManager

        vector_store = VectorStoreManager()
        memory_manager = MemoryManager()
        indexer = CodebaseIndexer(vector_store)

        try:
            git_repo = GitRepository()
        except Exception:
            git_repo = None

        return cls(
            vector_store=vector_store,
            memory_manager=memory_manager,
            indexer=indexer,
            git_repo=git_repo,
        )

    def get_symbol_graph(self) -> "SymbolGraph":
        """
        Returns the SymbolGraph, building it lazily if not yet loaded.

        The symbol graph provides AST-level call / inherit / implement
        relationships between symbols (functions, classes, methods).

        Returns:
            SymbolGraph instance (may be empty if build fails).
        """
        import warmup

        warmup.ensure_loaded()
        with _symbol_graph_lock:
            if self._symbol_graph is None:
                from symbol_graph import get_or_build_symbol_graph

                self._symbol_graph = get_or_build_symbol_graph(force=False)
            return self._symbol_graph


_app_context: AppContext | None = None
_context_lock = threading.Lock()
_symbol_graph_lock = threading.Lock()


def get_context() -> AppContext:
    """
    Gets the global application context, creating it if necessary.
    Also ensures startup initialization has been performed.

    Thread-safe: MCP tool threads and background indexer threads may race
    here; only one AppContext (and one ChromaDB client) must ever be created.

    Returns:
        Global AppContext instance
    """
    global _app_context
    if _app_context is not None:
        return _app_context

    # Outside _context_lock, and before create_default() imports anything: the
    # heavy imports must happen in one thread at a time or CPython's per-module
    # locks deadlock. See warmup.py.
    import warmup

    warmup.ensure_loaded()

    with _context_lock:
        if _app_context is None:
            from mcp_server import ensure_startup

            ensure_startup()
            _app_context = AppContext.create_default()

            # Preload the embedding model in the background so it is ready for
            # queries. Skipped under pytest: short-lived test processes exit
            # while the daemon thread is mid-download/init, which aborts the
            # interpreter (pthread "exception not rethrown" core dump).
            import os

            if "PYTEST_CURRENT_TEST" not in os.environ:
                from logger import setup_logger

                logger = setup_logger()

                def load_model_bg(ctx: AppContext) -> None:
                    try:
                        logger.info("Starting background loading of embedding model...")
                        ctx.vector_store.initialize()
                    except Exception as e:
                        logger.error(f"Failed to initialize vector store in background: {e}")

                threading.Thread(target=load_model_bg, args=(_app_context,), daemon=True).start()

    return _app_context


def set_context(context: AppContext) -> None:
    """
    Sets the global application context.
    Useful for testing with mock dependencies.

    Args:
        context: AppContext instance to use
    """
    global _app_context
    _app_context = context


def reset_context() -> None:
    """Resets the global context to None. Useful for testing."""
    global _app_context
    _app_context = None
    invalidate_symbol_graph_cache_if_loaded()


def invalidate_symbol_graph_cache_if_loaded() -> None:
    """
    Drops the cached symbol graph, but only if `symbol_graph` is already imported.

    Importing that module pulls in tree-sitter and every language grammar, which
    costs ~18 s on a cold process. `session_init` used to pay that on the plain
    `import` line alone — enough on its own to blow past the client's 30 s tool
    timeout before any work started. When the module was never loaded there is
    no in-memory cache to invalidate, so skipping the import is not just faster,
    it is equivalent.
    """
    import sys

    module = sys.modules.get("symbol_graph")
    if module is None:
        return
    try:
        module.invalidate_symbol_graph_cache()
    except Exception:
        pass
