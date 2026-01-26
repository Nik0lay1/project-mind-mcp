import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cache_manager import FileCache, LRUCache, TTLCache


class TestLRUCache(unittest.TestCase):
    def test_lru_cache_basic_operations(self):
        """Test basic get/put operations"""
        cache = LRUCache(capacity=3)

        cache.put("key1", "value1")
        cache.put("key2", "value2")

        self.assertEqual(cache.get("key1"), "value1")
        self.assertEqual(cache.get("key2"), "value2")

    def test_lru_cache_eviction(self):
        """Test that LRU eviction works correctly"""
        cache = LRUCache(capacity=2)

        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.put("key3", "value3")

        self.assertIsNone(cache.get("key1"))
        self.assertEqual(cache.get("key2"), "value2")
        self.assertEqual(cache.get("key3"), "value3")

    def test_lru_cache_update_existing(self):
        """Test updating existing key"""
        cache = LRUCache(capacity=2)

        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.put("key1", "updated1")

        self.assertEqual(cache.get("key1"), "updated1")
        self.assertEqual(cache.get("key2"), "value2")

    def test_lru_cache_access_order(self):
        """Test that accessing an item moves it to end"""
        cache = LRUCache(capacity=2)

        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.get("key1")
        cache.put("key3", "value3")

        self.assertEqual(cache.get("key1"), "value1")
        self.assertIsNone(cache.get("key2"))

    def test_lru_cache_stats(self):
        """Test cache statistics tracking"""
        cache = LRUCache(capacity=2)

        cache.put("key1", "value1")
        cache.get("key1")
        cache.get("key2")

        stats = cache.get_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["size"], 1)
        self.assertEqual(stats["capacity"], 2)

    def test_lru_cache_clear(self):
        """Test clearing cache"""
        cache = LRUCache(capacity=2)

        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.clear()

        self.assertIsNone(cache.get("key1"))
        self.assertEqual(cache.get_stats()["size"], 0)


class TestTTLCache(unittest.TestCase):
    def test_ttl_cache_basic_operations(self):
        """Test basic get/put operations"""
        cache = TTLCache(ttl_seconds=10, max_size=3)

        cache.put("key1", "value1")
        cache.put("key2", "value2")

        self.assertEqual(cache.get("key1"), "value1")
        self.assertEqual(cache.get("key2"), "value2")

    @patch('time.time')
    def test_ttl_cache_expiration(self, mock_time):
        """Test that items expire after TTL"""
        cache = TTLCache(ttl_seconds=5, max_size=10)

        mock_time.return_value = 100.0
        cache.put("key1", "value1")

        mock_time.return_value = 104.0
        self.assertEqual(cache.get("key1"), "value1")

        mock_time.return_value = 106.0
        self.assertIsNone(cache.get("key1"))

    def test_ttl_cache_max_size_eviction(self):
        """Test eviction when max size is reached"""
        cache = TTLCache(ttl_seconds=60, max_size=2)

        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.put("key3", "value3")

        stats = cache.get_stats()
        self.assertEqual(stats["size"], 2)

    @patch('time.time')
    def test_ttl_cache_cleanup_expired(self, mock_time):
        """Test manual cleanup of expired items"""
        cache = TTLCache(ttl_seconds=5, max_size=10)

        mock_time.return_value = 100.0
        cache.put("key1", "value1")
        cache.put("key2", "value2")

        mock_time.return_value = 110.0
        removed = cache.cleanup_expired()

        self.assertEqual(removed, 2)
        self.assertEqual(cache.get_stats()["size"], 0)

    def test_ttl_cache_stats(self):
        """Test cache statistics tracking"""
        cache = TTLCache(ttl_seconds=60, max_size=10)

        cache.put("key1", "value1")
        cache.get("key1")
        cache.get("key2")

        stats = cache.get_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["size"], 1)
        self.assertEqual(stats["ttl_seconds"], 60)

    def test_ttl_cache_clear(self):
        """Test clearing cache"""
        cache = TTLCache(ttl_seconds=60, max_size=10)

        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.clear()

        self.assertIsNone(cache.get("key1"))
        self.assertEqual(cache.get_stats()["size"], 0)


class TestFileCache(unittest.TestCase):
    @patch('pathlib.Path.stat')
    def test_file_cache_basic_operations(self, mock_stat):
        """Test basic file cache operations"""
        cache = FileCache(capacity=3)

        mock_stat_result = MagicMock()
        mock_stat_result.st_mtime = 123.45
        mock_stat.return_value = mock_stat_result

        file_path = Path("test.txt")
        cache.put(file_path, "file content")

        result = cache.get(file_path)
        self.assertEqual(result, "file content")

    @patch('pathlib.Path.stat')
    def test_file_cache_invalidation_on_mtime_change(self, mock_stat):
        """Test that cache is invalidated when file is modified"""
        cache = FileCache(capacity=3)

        mock_stat_result = MagicMock()
        mock_stat_result.st_mtime = 123.45
        mock_stat.return_value = mock_stat_result

        file_path = Path("test.txt")
        cache.put(file_path, "old content")

        self.assertEqual(cache.get(file_path), "old content")

        mock_stat_result.st_mtime = 124.45
        self.assertIsNone(cache.get(file_path))

    @patch('pathlib.Path.stat')
    def test_file_cache_miss_on_stat_error(self, mock_stat):
        """Test that cache returns None on stat errors"""
        cache = FileCache(capacity=3)

        mock_stat.side_effect = FileNotFoundError()

        file_path = Path("nonexistent.txt")
        result = cache.get(file_path)

        self.assertIsNone(result)

    @patch('pathlib.Path.stat')
    def test_file_cache_stats(self, mock_stat):
        """Test file cache statistics"""
        cache = FileCache(capacity=3)

        mock_stat_result = MagicMock()
        mock_stat_result.st_mtime = 123.45
        mock_stat.return_value = mock_stat_result

        file_path = Path("test.txt")
        cache.put(file_path, "content")

        result1 = cache.get(file_path)
        self.assertEqual(result1, "content")

        stats = cache.get_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["capacity"], 3)

    @patch('pathlib.Path.stat')
    def test_file_cache_clear(self, mock_stat):
        """Test clearing file cache"""
        cache = FileCache(capacity=3)

        mock_stat_result = MagicMock()
        mock_stat_result.st_mtime = 123.45
        mock_stat.return_value = mock_stat_result

        file_path = Path("test.txt")
        cache.put(file_path, "content")
        cache.clear()

        self.assertIsNone(cache.get(file_path))


if __name__ == '__main__':
    unittest.main()
