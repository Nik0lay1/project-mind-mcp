"""Custom exceptions for ProjectMind MCP Server."""


class ProjectMindError(Exception):
    """Base exception for all ProjectMind errors."""

    pass


class IndexError(ProjectMindError):
    """Errors related to codebase indexing."""

    pass


class SearchError(ProjectMindError):
    """Errors related to vector search operations."""

    pass


class MemoryError(ProjectMindError):
    """Errors related to memory file operations."""

    pass


class ConfigError(ProjectMindError):
    """Errors related to configuration."""

    pass


class VectorStoreError(ProjectMindError):
    """Errors related to vector store operations."""

    pass


class GitError(ProjectMindError):
    """Errors related to git operations."""

    pass


class ValidationError(ProjectMindError):
    """Errors related to input validation."""

    pass
