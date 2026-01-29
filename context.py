"""Application context for dependency injection."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codebase_indexer import CodebaseIndexer
    from git_utils import GitRepository
    from memory_manager import MemoryManager
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


_app_context: AppContext | None = None


def get_context() -> AppContext:
    """
    Gets the global application context, creating it if necessary.

    Returns:
        Global AppContext instance
    """
    global _app_context
    if _app_context is None:
        _app_context = AppContext.create_default()
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
