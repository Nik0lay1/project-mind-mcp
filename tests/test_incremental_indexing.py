import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from incremental_indexing import IndexMetadata, atomic_write


class TestAtomicWrite(unittest.TestCase):
    @patch('incremental_indexing.tempfile.mkstemp')
    @patch('incremental_indexing.os.fdopen')
    @patch('incremental_indexing.os.replace')
    @patch('incremental_indexing.os.fsync')
    def test_atomic_write_creates_temp_and_replaces(self, mock_fsync, mock_replace, mock_fdopen, mock_mkstemp):
        """Test that atomic_write creates temp file and replaces original"""
        mock_mkstemp.return_value = (42, '/tmp/.test.json.tmp')
        mock_file = MagicMock()
        mock_fdopen.return_value.__enter__.return_value = mock_file

        test_path = Path('/test/test.json')
        test_content = '{"test": true}'

        atomic_write(test_path, test_content)

        mock_file.write.assert_called_once_with(test_content)
        mock_file.flush.assert_called_once()
        mock_fsync.assert_called_once()
        mock_replace.assert_called_once_with('/tmp/.test.json.tmp', test_path)

    @patch('incremental_indexing.tempfile.mkstemp')
    @patch('incremental_indexing.os.fdopen')
    @patch('incremental_indexing.os.replace')
    @patch('incremental_indexing.os.unlink')
    @patch('incremental_indexing.os.fsync')
    def test_atomic_write_cleanup_on_error(self, mock_fsync, mock_unlink, mock_replace, mock_fdopen, mock_mkstemp):
        """Test that temp file is cleaned up on error"""
        temp_path = '/tmp/.test.json.tmp'
        mock_mkstemp.return_value = (42, temp_path)
        mock_file = MagicMock()
        mock_file.fileno.return_value = 42
        mock_fdopen.return_value.__enter__.return_value = mock_file
        mock_replace.side_effect = OSError("Replace failed")

        with self.assertRaises(OSError):
            atomic_write(Path('/test/test.json'), 'content')

        mock_unlink.assert_called_once_with(temp_path)


class TestIndexMetadata(unittest.TestCase):
    @patch('incremental_indexing.INDEX_METADATA_FILE')
    def test_load_empty_when_file_not_exists(self, mock_file):
        """Test that empty metadata is loaded when file doesn't exist"""
        mock_file.exists.return_value = False

        metadata = IndexMetadata()

        self.assertEqual(metadata.metadata, {})

    @patch('incremental_indexing.INDEX_METADATA_FILE')
    @patch('builtins.open', new_callable=mock_open, read_data='{"test.py": {"mtime": 123.45}}')
    def test_load_existing_metadata(self, mock_file_open, mock_file):
        """Test loading existing metadata from file"""
        mock_file.exists.return_value = True

        metadata = IndexMetadata()

        self.assertIn('test.py', metadata.metadata)
        self.assertEqual(metadata.metadata['test.py']['mtime'], 123.45)

    @patch('incremental_indexing.INDEX_METADATA_FILE')
    @patch('builtins.open', side_effect=Exception("Read error"))
    def test_load_handles_error(self, mock_file_open, mock_file):
        """Test that load handles errors gracefully"""
        mock_file.exists.return_value = True

        metadata = IndexMetadata()

        self.assertEqual(metadata.metadata, {})

    @patch('incremental_indexing.atomic_write')
    @patch('logger.get_logger')
    def test_save_uses_atomic_write(self, mock_logger, mock_atomic_write):
        """Test that save uses atomic_write"""
        metadata = IndexMetadata()
        metadata.metadata = {"file1.py": {"mtime": 123.45}}

        metadata.save()

        mock_atomic_write.assert_called_once()
        args = mock_atomic_write.call_args
        content = args[0][1]
        parsed = json.loads(content)
        self.assertEqual(parsed["file1.py"]["mtime"], 123.45)

    def test_get_file_mtime_existing(self):
        """Test getting mtime for existing file"""
        metadata = IndexMetadata()
        metadata.metadata = {"test.py": {"mtime": 123.45}}

        mtime = metadata.get_file_mtime("test.py")

        self.assertEqual(mtime, 123.45)

    def test_get_file_mtime_non_existing(self):
        """Test getting mtime for non-existing file returns 0"""
        metadata = IndexMetadata()

        mtime = metadata.get_file_mtime("nonexistent.py")

        self.assertEqual(mtime, 0.0)

    def test_update_file(self):
        """Test updating file metadata"""
        metadata = IndexMetadata()

        metadata.update_file("test.py", 123.45)

        self.assertEqual(metadata.metadata["test.py"]["mtime"], 123.45)
        self.assertIn("indexed_at", metadata.metadata["test.py"])

    @patch('pathlib.Path.stat')
    def test_get_changed_files_detects_changes(self, mock_stat):
        """Test that get_changed_files detects modified files"""
        metadata = IndexMetadata()
        metadata.metadata = {"old.py": {"mtime": 100.0}}

        mock_stat_result = MagicMock()
        mock_stat_result.st_mtime = 200.0
        mock_stat.return_value = mock_stat_result

        files = [Path("old.py"), Path("new.py")]
        changed = metadata.get_changed_files(files)

        self.assertEqual(len(changed), 2)
        self.assertIn(Path("old.py"), changed)
        self.assertIn(Path("new.py"), changed)

    @patch('pathlib.Path.stat')
    def test_get_changed_files_skips_unchanged(self, mock_stat):
        """Test that unchanged files are not returned"""
        metadata = IndexMetadata()
        metadata.metadata = {"unchanged.py": {"mtime": 100.0}}

        mock_stat_result = MagicMock()
        mock_stat_result.st_mtime = 100.0
        mock_stat.return_value = mock_stat_result

        files = [Path("unchanged.py")]
        changed = metadata.get_changed_files(files)

        self.assertEqual(len(changed), 0)

    def test_remove_deleted_files(self):
        """Test removing deleted files from metadata"""
        metadata = IndexMetadata()
        metadata.metadata = {
            "exists.py": {"mtime": 100.0},
            "deleted.py": {"mtime": 100.0}
        }

        existing = {"exists.py"}
        metadata.remove_deleted_files(existing)

        self.assertIn("exists.py", metadata.metadata)
        self.assertNotIn("deleted.py", metadata.metadata)

    def test_get_stats(self):
        """Test getting statistics"""
        metadata = IndexMetadata()
        metadata.metadata = {
            "file1.py": {"mtime": 100.0, "indexed_at": "2024-01-01T10:00:00"},
            "file2.py": {"mtime": 200.0, "indexed_at": "2024-01-01T11:00:00"}
        }

        stats = metadata.get_stats()

        self.assertEqual(stats["total_files"], 2)
        self.assertEqual(stats["last_index"], "2024-01-01T11:00:00")

    def test_get_stats_empty(self):
        """Test getting stats for empty metadata"""
        metadata = IndexMetadata()

        stats = metadata.get_stats()

        self.assertEqual(stats["total_files"], 0)
        self.assertIsNone(stats["last_index"])


if __name__ == '__main__':
    unittest.main()
