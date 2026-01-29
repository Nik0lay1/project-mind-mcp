"""Tests for context module."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context import AppContext, get_context, reset_context, set_context


@pytest.fixture(autouse=True)
def clean_context():
    """Reset context before and after each test."""
    reset_context()
    yield
    reset_context()


class TestAppContext:
    """Tests for AppContext dataclass."""

    def test_create_with_all_dependencies(self):
        """Test creating context with all dependencies."""
        mock_vector_store = MagicMock()
        mock_memory_manager = MagicMock()
        mock_indexer = MagicMock()
        mock_git_repo = MagicMock()

        ctx = AppContext(
            vector_store=mock_vector_store,
            memory_manager=mock_memory_manager,
            indexer=mock_indexer,
            git_repo=mock_git_repo,
        )

        assert ctx.vector_store is mock_vector_store
        assert ctx.memory_manager is mock_memory_manager
        assert ctx.indexer is mock_indexer
        assert ctx.git_repo is mock_git_repo

    def test_git_repo_optional(self):
        """Test that git_repo is optional."""
        ctx = AppContext(
            vector_store=MagicMock(),
            memory_manager=MagicMock(),
            indexer=MagicMock(),
        )

        assert ctx.git_repo is None


class TestContextFunctions:
    """Tests for context management functions."""

    def test_set_and_get_context(self):
        """Test setting and getting context."""
        mock_ctx = AppContext(
            vector_store=MagicMock(),
            memory_manager=MagicMock(),
            indexer=MagicMock(),
        )

        set_context(mock_ctx)
        result = get_context()

        assert result is mock_ctx

    def test_reset_context(self):
        """Test resetting context."""
        mock_ctx = AppContext(
            vector_store=MagicMock(),
            memory_manager=MagicMock(),
            indexer=MagicMock(),
        )

        set_context(mock_ctx)
        reset_context()

        new_ctx = get_context()
        assert new_ctx is not mock_ctx

    def test_get_context_creates_default(self):
        """Test that get_context creates default context if none exists."""
        ctx = get_context()

        assert ctx is not None
        assert ctx.vector_store is not None
        assert ctx.memory_manager is not None
        assert ctx.indexer is not None

    def test_get_context_returns_same_instance(self):
        """Test that get_context returns the same instance."""
        ctx1 = get_context()
        ctx2 = get_context()

        assert ctx1 is ctx2


class TestAppContextCreateDefault:
    """Tests for AppContext.create_default()."""

    def test_create_default_initializes_all_services(self):
        """Test that create_default initializes all required services."""
        ctx = AppContext.create_default()

        assert ctx.vector_store is not None
        assert ctx.memory_manager is not None
        assert ctx.indexer is not None
