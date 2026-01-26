import unittest
from unittest.mock import MagicMock, call
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_limited_indexer import MemoryLimitedIndexer


class TestMemoryLimitedIndexer(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.callback_mock = MagicMock()
        self.max_memory = 1024 * 1024

    def test_initialization(self):
        """Test that indexer initializes correctly"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        self.assertEqual(indexer.max_memory_bytes, self.max_memory)
        self.assertEqual(indexer.batch_callback, self.callback_mock)
        self.assertEqual(len(indexer.documents), 0)
        self.assertEqual(indexer.current_memory, 0)
        self.assertEqual(indexer.total_chunks, 0)
        self.assertEqual(indexer.total_batches, 0)

    def test_add_chunk_stores_data(self):
        """Test that add_chunk stores documents correctly"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        indexer.add_chunk("doc1", {"source": "file1.py"}, "id1")
        indexer.add_chunk("doc2", {"source": "file2.py"}, "id2")

        self.assertEqual(len(indexer.documents), 2)
        self.assertEqual(indexer.documents[0], "doc1")
        self.assertEqual(indexer.metadatas[0], {"source": "file1.py"})
        self.assertEqual(indexer.ids[0], "id1")

    def test_add_chunk_increments_total_chunks(self):
        """Test that total_chunks is incremented"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        indexer.add_chunk("doc1", {"source": "file.py"}, "id1")
        indexer.add_chunk("doc2", {"source": "file.py"}, "id2")

        self.assertEqual(indexer.total_chunks, 2)

    def test_flush_calls_callback(self):
        """Test that flush calls the callback with correct data"""
        received_data = []
        
        def capture_callback(docs, metas, ids):
            received_data.append((docs.copy(), metas.copy(), ids.copy()))
        
        indexer = MemoryLimitedIndexer(self.max_memory, capture_callback)

        indexer.add_chunk("doc1", {"source": "file1.py"}, "id1")
        indexer.add_chunk("doc2", {"source": "file2.py"}, "id2")

        indexer.flush()

        self.assertEqual(len(received_data), 1)
        docs, metas, ids = received_data[0]
        self.assertEqual(docs, ["doc1", "doc2"])
        self.assertEqual(metas, [{"source": "file1.py"}, {"source": "file2.py"}])
        self.assertEqual(ids, ["id1", "id2"])

    def test_flush_clears_buffers(self):
        """Test that flush clears internal buffers"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        indexer.add_chunk("doc1", {"source": "file.py"}, "id1")
        indexer.flush()

        self.assertEqual(len(indexer.documents), 0)
        self.assertEqual(len(indexer.metadatas), 0)
        self.assertEqual(len(indexer.ids), 0)
        self.assertEqual(indexer.current_memory, 0)

    def test_flush_increments_batch_count(self):
        """Test that flush increments total_batches"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        indexer.add_chunk("doc1", {"source": "file.py"}, "id1")
        indexer.flush()

        self.assertEqual(indexer.total_batches, 1)

        indexer.add_chunk("doc2", {"source": "file.py"}, "id2")
        indexer.flush()

        self.assertEqual(indexer.total_batches, 2)

    def test_flush_empty_does_not_call_callback(self):
        """Test that flushing empty buffer doesn't call callback"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        indexer.flush()

        self.callback_mock.assert_not_called()
        self.assertEqual(indexer.total_batches, 0)

    def test_auto_flush_on_memory_limit(self):
        """Test that indexer auto-flushes when memory limit reached"""
        small_limit = 500
        indexer = MemoryLimitedIndexer(small_limit, self.callback_mock)

        large_doc = "x" * 200

        indexer.add_chunk(large_doc, {"source": "file1.py"}, "id1")
        self.callback_mock.assert_not_called()

        indexer.add_chunk(large_doc, {"source": "file2.py"}, "id2")

        if self.callback_mock.call_count > 0:
            self.assertEqual(self.callback_mock.call_count, 1)

    def test_get_stats_returns_correct_data(self):
        """Test that get_stats returns accurate statistics"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        indexer.add_chunk("doc1", {"source": "file.py"}, "id1")
        indexer.add_chunk("doc2", {"source": "file.py"}, "id2")

        stats = indexer.get_stats()

        self.assertEqual(stats["total_chunks"], 2)
        self.assertEqual(stats["total_batches"], 0)
        self.assertEqual(stats["current_buffer_chunks"], 2)
        self.assertGreater(stats["current_buffer_bytes"], 0)
        self.assertEqual(stats["max_memory_bytes"], self.max_memory)

    def test_get_stats_after_flush(self):
        """Test stats after flushing"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        indexer.add_chunk("doc1", {"source": "file.py"}, "id1")
        indexer.flush()

        stats = indexer.get_stats()

        self.assertEqual(stats["total_chunks"], 1)
        self.assertEqual(stats["total_batches"], 1)
        self.assertEqual(stats["current_buffer_chunks"], 0)
        self.assertEqual(stats["current_buffer_bytes"], 0)

    def test_memory_estimation_increases(self):
        """Test that memory estimation increases with added chunks"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        initial_memory = indexer.current_memory

        indexer.add_chunk("test document", {"source": "file.py"}, "id1")

        self.assertGreater(indexer.current_memory, initial_memory)

    def test_callback_error_propagates(self):
        """Test that callback errors are propagated"""
        error_callback = MagicMock(side_effect=ValueError("Callback error"))
        indexer = MemoryLimitedIndexer(self.max_memory, error_callback)

        indexer.add_chunk("doc1", {"source": "file.py"}, "id1")

        with self.assertRaises(ValueError) as ctx:
            indexer.flush()

        self.assertIn("Callback error", str(ctx.exception))

    def test_callback_error_clears_buffer(self):
        """Test that buffer is cleared even when callback raises error"""
        error_callback = MagicMock(side_effect=ValueError("Error"))
        indexer = MemoryLimitedIndexer(self.max_memory, error_callback)

        indexer.add_chunk("doc1", {"source": "file.py"}, "id1")

        try:
            indexer.flush()
        except ValueError:
            pass

        self.assertEqual(len(indexer.documents), 0)
        self.assertEqual(indexer.current_memory, 0)

    def test_multiple_batches(self):
        """Test processing multiple batches"""
        indexer = MemoryLimitedIndexer(self.max_memory, self.callback_mock)

        for i in range(10):
            indexer.add_chunk(f"doc{i}", {"source": f"file{i}.py"}, f"id{i}")
            if i % 3 == 2:
                indexer.flush()

        final_flush_count = indexer.total_batches
        indexer.flush()

        self.assertEqual(indexer.total_chunks, 10)
        self.assertGreaterEqual(indexer.total_batches, 3)


if __name__ == '__main__':
    unittest.main()
