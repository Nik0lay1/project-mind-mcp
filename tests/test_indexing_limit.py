import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from codebase_indexer import CodebaseIndexer


class TestIndexingLimit:
    """Tests for indexing limits."""

    @patch("os.walk")
    def test_scan_limit(self, mock_walk):
        """Test that scan_indexable_files respects the max_files limit."""
        # Mock os.walk to return many files
        # We simulate 2 directories, each with 10 files
        mock_walk.return_value = [
            ("/root", [], [f"file{i}.py" for i in range(10)]),
            ("/root/subdir", [], [f"subfile{i}.py" for i in range(10)]),
        ]

        # Mock vector store
        mock_store = MagicMock()
        indexer = CodebaseIndexer(mock_store)

        # Mock should_index_file to always return True
        indexer.should_index_file = MagicMock(return_value=True)

        # Set a small limit
        limit = 5

        # Run scan with limit
        files = indexer.scan_indexable_files(
            Path("/root"), ignored_dirs=set(), ignore_patterns=set(), max_files=limit
        )

        assert len(files) == limit
        assert len(files) < 20  # Should be less than total available files
        print(f"Scanned {len(files)} files with limit {limit}")
