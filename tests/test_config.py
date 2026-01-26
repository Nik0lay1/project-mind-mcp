import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    MAX_FILE_SIZE_MB,
    MAX_MEMORY_MB,
    PROJECT_ROOT,
    get_ignored_dirs,
    get_max_file_size_bytes,
    get_max_memory_bytes,
    safe_read_text,
    validate_path,
)


class TestValidatePath(unittest.TestCase):
    def test_validate_path_accepts_valid_relative_path(self):
        """Test that valid relative paths are accepted"""
        result = validate_path("config.py")
        self.assertEqual(result, PROJECT_ROOT / "config.py")

    def test_validate_path_accepts_current_directory(self):
        """Test that current directory is accepted"""
        result = validate_path(".")
        self.assertEqual(result, PROJECT_ROOT)

    def test_validate_path_rejects_parent_traversal(self):
        """Test that parent directory traversal is rejected"""
        with self.assertRaises(ValueError) as ctx:
            validate_path("../../etc/passwd")
        self.assertIn("outside project root", str(ctx.exception))

    def test_validate_path_rejects_empty_string(self):
        """Test that empty string is rejected"""
        with self.assertRaises(ValueError):
            validate_path("")

    def test_validate_path_rejects_none(self):
        """Test that None is rejected"""
        with self.assertRaises((ValueError, TypeError)):
            validate_path(None)

    def test_validate_path_rejects_non_string(self):
        """Test that non-string input is rejected"""
        with self.assertRaises(ValueError):
            validate_path(123)


class TestSafeReadText(unittest.TestCase):
    @patch('pathlib.Path.read_text')
    def test_safe_read_text_utf8(self, mock_read_text):
        """Test reading UTF-8 file successfully"""
        mock_read_text.return_value = "Hello World"
        result = safe_read_text(Path("test.txt"))
        self.assertEqual(result, "Hello World")
        mock_read_text.assert_called_once_with(encoding='utf-8')

    @patch('pathlib.Path.read_text')
    def test_safe_read_text_fallback_to_latin1(self, mock_read_text):
        """Test fallback to Latin-1 when UTF-8 fails"""
        mock_read_text.side_effect = [
            UnicodeDecodeError('utf-8', b'', 0, 1, 'invalid'),
            UnicodeDecodeError('utf-8-sig', b'', 0, 1, 'invalid'),
            "Café"
        ]
        result = safe_read_text(Path("test.txt"))
        self.assertEqual(result, "Café")
        self.assertEqual(mock_read_text.call_count, 3)

    @patch('pathlib.Path.read_text')
    def test_safe_read_text_raises_on_all_encoding_failures(self, mock_read_text):
        """Test that UnicodeDecodeError is raised when all encodings fail"""
        mock_read_text.side_effect = UnicodeDecodeError('utf-8', b'', 0, 1, 'invalid')
        with self.assertRaises(UnicodeDecodeError):
            safe_read_text(Path("test.txt"))

    @patch('pathlib.Path.read_text')
    def test_safe_read_text_raises_io_error(self, mock_read_text):
        """Test that IOError is raised on file read failure"""
        mock_read_text.side_effect = PermissionError("Access denied")
        with self.assertRaises(IOError):
            safe_read_text(Path("test.txt"))


class TestConfigFunctions(unittest.TestCase):
    @patch.dict(os.environ, {'PROJECTMIND_MAX_FILE_SIZE_MB': '50'})
    def test_get_max_file_size_bytes_from_env(self):
        """Test reading max file size from environment variable"""
        result = get_max_file_size_bytes()
        self.assertEqual(result, 50 * 1024 * 1024)

    @patch.dict(os.environ, {}, clear=True)
    def test_get_max_file_size_bytes_default(self):
        """Test default max file size"""
        result = get_max_file_size_bytes()
        self.assertEqual(result, MAX_FILE_SIZE_MB * 1024 * 1024)

    @patch.dict(os.environ, {'PROJECTMIND_MAX_FILE_SIZE_MB': 'invalid'})
    def test_get_max_file_size_bytes_invalid_env(self):
        """Test fallback to default on invalid env value"""
        result = get_max_file_size_bytes()
        self.assertEqual(result, MAX_FILE_SIZE_MB * 1024 * 1024)

    @patch.dict(os.environ, {'PROJECTMIND_MAX_MEMORY_MB': '200'})
    def test_get_max_memory_bytes_from_env(self):
        """Test reading max memory from environment variable"""
        result = get_max_memory_bytes()
        self.assertEqual(result, 200 * 1024 * 1024)

    @patch.dict(os.environ, {}, clear=True)
    def test_get_max_memory_bytes_default(self):
        """Test default max memory"""
        result = get_max_memory_bytes()
        self.assertEqual(result, MAX_MEMORY_MB * 1024 * 1024)

    def test_get_ignored_dirs_returns_copy(self):
        """Test that get_ignored_dirs returns a copy, not reference"""
        dirs1 = get_ignored_dirs()
        dirs2 = get_ignored_dirs()
        self.assertIsNot(dirs1, dirs2)
        self.assertEqual(dirs1, dirs2)

    def test_get_ignored_dirs_contains_common_dirs(self):
        """Test that ignored dirs contain common directories"""
        dirs = get_ignored_dirs()
        self.assertIn('.git', dirs)
        self.assertIn('node_modules', dirs)
        self.assertIn('__pycache__', dirs)


if __name__ == '__main__':
    unittest.main()
