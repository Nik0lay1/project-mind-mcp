import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any

from logger import get_logger

logger = get_logger()


class LRUCache:
    """
    Thread-safe Least Recently Used (LRU) cache.
    Automatically evicts least recently used items when capacity is reached.
    """

    def __init__(self, capacity: int = 100):
        """
        Initialize LRU cache.

        Args:
            capacity: Maximum number of items to cache
        """
        self.capacity = capacity
        self.cache: OrderedDict = OrderedDict()
        self.lock = Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        """
        Retrieves value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                self.hits += 1
                return self.cache[key]
            self.misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        """
        Adds or updates value in cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                if len(self.cache) >= self.capacity:
                    oldest_key = next(iter(self.cache))
                    del self.cache[oldest_key]
                    logger.debug(f"LRU cache evicted: {oldest_key}")
            self.cache[key] = value

    def clear(self) -> None:
        """Clears all cached items."""
        with self.lock:
            self.cache.clear()
            logger.debug("LRU cache cleared")

    def get_stats(self) -> dict[str, Any]:
        """
        Returns cache statistics.

        Returns:
            Dictionary with hits, misses, size, capacity, hit_rate
        """
        with self.lock:
            total = self.hits + self.misses
            hit_rate = (self.hits / total * 100) if total > 0 else 0
            return {
                "hits": self.hits,
                "misses": self.misses,
                "size": len(self.cache),
                "capacity": self.capacity,
                "hit_rate": f"{hit_rate:.2f}%"
            }


class TTLCache:
    """
    Time-To-Live (TTL) cache with automatic expiration.
    Items are automatically removed after specified TTL.
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 100):
        """
        Initialize TTL cache.

        Args:
            ttl_seconds: Time to live for cached items in seconds
            max_size: Maximum number of items to cache
        """
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self.cache: dict[str, tuple[Any, float]] = {}
        self.lock = Lock()
        self.hits = 0
        self.misses = 0
        self.expirations = 0

    def get(self, key: str) -> Any | None:
        """
        Retrieves value from cache if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found or expired
        """
        with self.lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl_seconds:
                    self.hits += 1
                    return value
                else:
                    del self.cache[key]
                    self.expirations += 1
                    logger.debug(f"TTL cache expired: {key}")
            self.misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        """
        Adds or updates value in cache with current timestamp.

        Args:
            key: Cache key
            value: Value to cache
        """
        with self.lock:
            if len(self.cache) >= self.max_size and key not in self.cache:
                self._evict_oldest()

            self.cache[key] = (value, time.time())

    def _evict_oldest(self) -> None:
        """Evicts the oldest item from cache."""
        if not self.cache:
            return

        oldest_key = min(self.cache.items(), key=lambda x: x[1][1])[0]
        del self.cache[oldest_key]
        logger.debug(f"TTL cache evicted oldest: {oldest_key}")

    def clear(self) -> None:
        """Clears all cached items."""
        with self.lock:
            self.cache.clear()
            logger.debug("TTL cache cleared")

    def cleanup_expired(self) -> int:
        """
        Removes all expired items from cache.

        Returns:
            Number of items removed
        """
        with self.lock:
            current_time = time.time()
            expired_keys = [
                key for key, (_, timestamp) in self.cache.items()
                if current_time - timestamp >= self.ttl_seconds
            ]

            for key in expired_keys:
                del self.cache[key]
                self.expirations += 1

            if expired_keys:
                logger.debug(f"TTL cache cleanup: removed {len(expired_keys)} expired items")

            return len(expired_keys)

    def get_stats(self) -> dict[str, Any]:
        """
        Returns cache statistics.

        Returns:
            Dictionary with hits, misses, size, expirations, hit_rate
        """
        with self.lock:
            total = self.hits + self.misses
            hit_rate = (self.hits / total * 100) if total > 0 else 0
            return {
                "hits": self.hits,
                "misses": self.misses,
                "size": len(self.cache),
                "max_size": self.max_size,
                "expirations": self.expirations,
                "ttl_seconds": self.ttl_seconds,
                "hit_rate": f"{hit_rate:.2f}%"
            }


class FileCache:
    """
    Specialized cache for file content with file modification time tracking.
    Automatically invalidates cache when file is modified.
    """

    def __init__(self, capacity: int = 50):
        """
        Initialize file cache.

        Args:
            capacity: Maximum number of files to cache
        """
        self.lru_cache = LRUCache(capacity)
        self.mtime_cache: dict[str, float] = {}
        self.lock = Lock()

    def get(self, file_path: Path) -> str | None:
        """
        Retrieves file content from cache if file hasn't been modified.

        Args:
            file_path: Path to file

        Returns:
            Cached file content or None if not cached or modified
        """
        try:
            key = str(file_path)
            current_mtime = file_path.stat().st_mtime

            with self.lock:
                cached_mtime = self.mtime_cache.get(key)

                if cached_mtime is not None and cached_mtime == current_mtime:
                    content = self.lru_cache.get(key)
                    if content is not None:
                        return content

                return None
        except Exception as e:
            logger.debug(f"Error checking file cache for {file_path}: {e}")
            return None

    def put(self, file_path: Path, content: str) -> None:
        """
        Caches file content with its modification time.

        Args:
            file_path: Path to file
            content: File content to cache
        """
        try:
            key = str(file_path)
            mtime = file_path.stat().st_mtime

            with self.lock:
                self.lru_cache.put(key, content)
                self.mtime_cache[key] = mtime
        except Exception as e:
            logger.debug(f"Error caching file {file_path}: {e}")

    def clear(self) -> None:
        """Clears all cached files."""
        with self.lock:
            self.lru_cache.clear()
            self.mtime_cache.clear()

    def get_stats(self) -> dict[str, Any]:
        """Returns file cache statistics."""
        return self.lru_cache.get_stats()
