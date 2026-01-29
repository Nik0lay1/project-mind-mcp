"""Tests for search functionality."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context import AppContext, reset_context, set_context


@pytest.fixture(autouse=True)
def reset_app_context():
    """Reset context before and after each test."""
    reset_context()
    yield
    reset_context()


@pytest.fixture
def mock_vector_store():
    """Create a mock vector store."""
    store = MagicMock()
    store.get_collection.return_value = MagicMock()
    store.get_count.return_value = 100
    store.query.return_value = {
        "documents": [["def hello():\n    pass", "class World:\n    pass"]],
        "metadatas": [[{"source": "test.py"}, {"source": "world.py"}]],
        "distances": [[0.1, 0.3]],
    }
    store.get_query_cache_stats.return_value = {
        "hits": 5,
        "misses": 10,
        "size": 5,
        "max_size": 100,
        "expirations": 0,
        "ttl_seconds": 300,
        "hit_rate": "33.33%",
    }
    return store


@pytest.fixture
def mock_memory_manager():
    """Create a mock memory manager."""
    manager = MagicMock()
    manager.read.return_value = "# Test Memory"
    return manager


@pytest.fixture
def mock_indexer():
    """Create a mock indexer."""
    return MagicMock()


@pytest.fixture
def mock_context(mock_vector_store, mock_memory_manager, mock_indexer):
    """Create and set mock application context."""
    ctx = AppContext(
        vector_store=mock_vector_store,
        memory_manager=mock_memory_manager,
        indexer=mock_indexer,
        git_repo=None,
    )
    set_context(ctx)
    return ctx


class TestSearchCodebase:
    """Tests for search_codebase function."""

    def test_search_empty_query(self, mock_context):
        """Test that empty query returns error."""
        from mcp_server import search_codebase

        result = search_codebase("")
        assert "Error" in result
        assert "empty" in result.lower()

    def test_search_whitespace_query(self, mock_context):
        """Test that whitespace-only query returns error."""
        from mcp_server import search_codebase

        result = search_codebase("   ")
        assert "Error" in result

    def test_search_negative_n_results(self, mock_context):
        """Test that negative n_results returns error."""
        from mcp_server import search_codebase

        result = search_codebase("test", n_results=-1)
        assert "Error" in result
        assert "greater than 0" in result

    def test_search_exceeds_max_results(self, mock_context):
        """Test that n_results > 50 returns error."""
        from mcp_server import search_codebase

        result = search_codebase("test", n_results=100)
        assert "Error" in result
        assert "50" in result

    def test_search_valid_query(self, mock_context, mock_vector_store):
        """Test successful search with valid query."""
        from mcp_server import search_codebase

        result = search_codebase("hello", n_results=5)
        assert "test.py" in result
        mock_vector_store.query.assert_called_once()

    def test_search_no_matches(self, mock_context, mock_vector_store):
        """Test search with no matches."""
        from mcp_server import search_codebase

        mock_vector_store.query.return_value = {"documents": [[]], "metadatas": [[]]}
        result = search_codebase("nonexistent")
        assert "No matches found" in result

    def test_search_vector_store_not_initialized(self, mock_context, mock_vector_store):
        """Test search when vector store returns None."""
        from mcp_server import search_codebase

        mock_vector_store.query.return_value = None
        result = search_codebase("test")
        assert "not initialized" in result.lower()


class TestSearchCodebaseAdvanced:
    """Tests for search_codebase_advanced function."""

    def test_advanced_search_empty_query(self, mock_context):
        """Test that empty query returns error."""
        from mcp_server import search_codebase_advanced

        result = search_codebase_advanced("")
        assert "Error" in result

    def test_advanced_search_invalid_relevance(self, mock_context):
        """Test that invalid min_relevance returns error."""
        from mcp_server import search_codebase_advanced

        result = search_codebase_advanced("test", min_relevance=1.5)
        assert "Error" in result
        assert "0 and 1" in result

    def test_advanced_search_file_type_filter(self, mock_context, mock_vector_store):
        """Test search with file type filter."""
        from mcp_server import search_codebase_advanced

        result = search_codebase_advanced("test", file_types=[".py"])
        assert "test.py" in result or "world.py" in result

    def test_advanced_search_exclude_dirs(self, mock_context, mock_vector_store):
        """Test search with directory exclusion."""
        from mcp_server import search_codebase_advanced

        mock_vector_store.query.return_value = {
            "documents": [["code in tests", "code in src"]],
            "metadatas": [[{"source": "tests/test.py"}, {"source": "src/main.py"}]],
            "distances": [[0.1, 0.2]],
        }
        result = search_codebase_advanced("test", exclude_dirs=["tests"])
        assert "src/main.py" in result
        assert "tests/test.py" not in result


class TestGetIndexStats:
    """Tests for get_index_stats function."""

    def test_get_stats_success(self, mock_context, mock_vector_store):
        """Test successful stats retrieval."""
        from mcp_server import get_index_stats

        result = get_index_stats()
        assert "100" in result
        assert "chunks" in result.lower()

    def test_get_stats_not_initialized(self, mock_context, mock_vector_store):
        """Test stats when vector store not initialized."""
        from mcp_server import get_index_stats

        mock_vector_store.get_count.return_value = None
        result = get_index_stats()
        assert "not initialized" in result.lower()


class TestGetCacheStats:
    """Tests for get_cache_stats function."""

    def test_cache_stats_format(self, mock_context):
        """Test cache stats output format."""
        from mcp_server import get_cache_stats

        with patch("mcp_server.get_file_cache_stats") as mock_file_stats:
            mock_file_stats.return_value = {
                "hits": 10,
                "misses": 5,
                "hit_rate": "66.67%",
                "size": 10,
                "capacity": 50,
            }
            result = get_cache_stats()

        assert "CACHE STATISTICS" in result
        assert "File Cache" in result
        assert "Query Cache" in result
        assert "Hit Rate" in result
