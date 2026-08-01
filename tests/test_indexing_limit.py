from unittest.mock import MagicMock

from codebase_indexer import CodebaseIndexer


def _make_tree(root, dirs_and_counts):
    for subdir, count in dirs_and_counts:
        target = root / subdir if subdir else root
        target.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (target / f"file{i}.py").write_text("x = 1\n", encoding="utf-8")


class TestIndexingLimit:
    """Tests for indexing limits."""

    def test_scan_limit(self, tmp_path):
        """scan_indexable_files stops at max_files."""
        _make_tree(tmp_path, [("", 10), ("subdir", 10)])

        indexer = CodebaseIndexer(MagicMock())
        limit = 5

        files = indexer.scan_indexable_files(
            tmp_path, ignored_dirs=set(), ignore_patterns=set(), max_files=limit
        )

        assert len(files) == limit

    def test_scan_reports_truncation(self, tmp_path):
        """A capped scan says so, instead of looking like a complete result."""
        _make_tree(tmp_path, [("", 10)])

        indexer = CodebaseIndexer(MagicMock())

        capped = indexer.scan_files(
            tmp_path, ignored_dirs=set(), ignore_patterns=set(), max_files=4
        )
        assert not capped.complete
        assert "file limit" in capped.truncated

        full = indexer.scan_files(tmp_path, ignored_dirs=set(), ignore_patterns=set())
        assert full.complete
        assert full.truncated is None
        assert len(full.files) == 10
