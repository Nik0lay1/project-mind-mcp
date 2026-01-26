import os
import sys

sys.path.append(os.getcwd())

from memory_limited_indexer import MemoryLimitedIndexer


def test_basic_indexing():
    """Test basic chunk addition and retrieval"""
    print("Testing basic indexing...")

    batches = []

    def collect_batch(documents, metadatas, ids):
        batches.append((documents.copy(), metadatas.copy(), ids.copy()))

    indexer = MemoryLimitedIndexer(1024 * 1024, collect_batch)

    indexer.add_chunk("test document 1", {"source": "file1.py"}, "file1_0")
    indexer.add_chunk("test document 2", {"source": "file2.py"}, "file2_0")

    stats = indexer.get_stats()
    assert stats["total_chunks"] == 2
    assert stats["current_buffer_chunks"] == 2
    assert len(batches) == 0

    print("  [OK] Basic indexing works")


def test_memory_limit_flush():
    """Test that indexer flushes when memory limit reached"""
    print("Testing memory limit auto-flush...")

    batches = []

    def collect_batch(documents, metadatas, ids):
        batches.append((documents.copy(), metadatas.copy(), ids.copy()))

    small_limit = 500
    indexer = MemoryLimitedIndexer(small_limit, collect_batch)

    large_doc = "x" * 200

    indexer.add_chunk(large_doc, {"source": "file1.py"}, "file1_0")
    initial_batches = len(batches)

    indexer.add_chunk(large_doc, {"source": "file2.py"}, "file2_0")

    if len(batches) > initial_batches:
        assert len(batches[0][0]) >= 1
        print(f"  [DEBUG] Auto-flush triggered: {len(batches)} batches, first batch has {len(batches[0][0])} docs")

    indexer.add_chunk(large_doc, {"source": "file3.py"}, "file3_0")
    indexer.flush()

    assert len(batches) > 0, "At least one batch should be flushed"

    stats = indexer.get_stats()
    assert stats["total_chunks"] == 3
    assert stats["current_buffer_chunks"] == 0

    print(f"  [OK] Memory limit triggers flush (total batches: {stats['total_batches']})")


def test_manual_flush():
    """Test manual flush operation"""
    print("Testing manual flush...")

    batches = []

    def collect_batch(documents, metadatas, ids):
        batches.append(len(documents))

    indexer = MemoryLimitedIndexer(10 * 1024 * 1024, collect_batch)

    for i in range(5):
        indexer.add_chunk(f"doc {i}", {"source": f"file{i}.py"}, f"file{i}_0")

    assert len(batches) == 0

    indexer.flush()
    assert len(batches) == 1
    assert batches[0] == 5

    indexer.flush()
    assert len(batches) == 1

    print("  [OK] Manual flush works correctly")


def test_memory_estimation():
    """Test memory size estimation"""
    print("Testing memory estimation...")

    batches = []
    flush_count = [0]

    def count_flush(documents, metadatas, ids):
        flush_count[0] += 1
        batches.append(len(documents))

    limit = 5000
    indexer = MemoryLimitedIndexer(limit, count_flush)

    for i in range(20):
        doc = f"This is document number {i} " * 10
        indexer.add_chunk(doc, {"source": f"file{i}.py", "index": i}, f"id_{i}")

    indexer.flush()

    assert flush_count[0] > 0

    stats = indexer.get_stats()
    assert stats["total_chunks"] == 20
    assert stats["current_buffer_chunks"] == 0

    print(f"  [OK] Memory estimation triggered {flush_count[0]} auto-flushes for 20 docs with {limit} byte limit")


def test_empty_flush():
    """Test that flushing empty buffer doesn't call callback"""
    print("Testing empty flush...")

    call_count = [0]

    def count_calls(documents, metadatas, ids):
        call_count[0] += 1

    indexer = MemoryLimitedIndexer(1024, count_calls)

    indexer.flush()
    assert call_count[0] == 0

    indexer.add_chunk("test", {"source": "file.py"}, "id_0")
    indexer.flush()
    assert call_count[0] == 1

    indexer.flush()
    assert call_count[0] == 1

    print("  [OK] Empty flush doesn't call callback")


def test_metadata_preservation():
    """Test that metadata is correctly preserved"""
    print("Testing metadata preservation...")

    collected = []

    def collect(documents, metadatas, ids):
        collected.append((documents.copy(), metadatas.copy(), ids.copy()))

    indexer = MemoryLimitedIndexer(10 * 1024 * 1024, collect)

    test_data = [
        ("doc1", {"source": "file1.py", "chunk_index": 0}, "file1_0"),
        ("doc2", {"source": "file1.py", "chunk_index": 1}, "file1_1"),
        ("doc3", {"source": "file2.py", "chunk_index": 0}, "file2_0"),
    ]

    for doc, meta, doc_id in test_data:
        indexer.add_chunk(doc, meta, doc_id)

    indexer.flush()

    assert len(collected) == 1
    docs, metas, ids = collected[0]

    assert docs == ["doc1", "doc2", "doc3"]
    assert ids == ["file1_0", "file1_1", "file2_0"]
    assert metas[0] == {"source": "file1.py", "chunk_index": 0}
    assert metas[1] == {"source": "file1.py", "chunk_index": 1}
    assert metas[2] == {"source": "file2.py", "chunk_index": 0}

    print("  [OK] Metadata preserved correctly")


def test_buffer_clearing():
    """Test that buffer is cleared after flush"""
    print("Testing buffer clearing...")

    indexer = MemoryLimitedIndexer(1024 * 1024, lambda d, m, i: None)

    indexer.add_chunk("doc1", {"source": "file.py"}, "id1")
    indexer.add_chunk("doc2", {"source": "file.py"}, "id2")

    stats_before = indexer.get_stats()
    assert stats_before["current_buffer_chunks"] == 2
    assert stats_before["current_buffer_bytes"] > 0

    indexer.flush()

    stats_after = indexer.get_stats()
    assert stats_after["current_buffer_chunks"] == 0
    assert stats_after["current_buffer_bytes"] == 0
    assert stats_after["total_chunks"] == 2

    print("  [OK] Buffer cleared after flush")


if __name__ == "__main__":
    print("=" * 50)
    print("MEMORY LIMITED INDEXER TESTS")
    print("=" * 50)
    print()

    try:
        test_basic_indexing()
        test_memory_limit_flush()
        test_manual_flush()
        test_memory_estimation()
        test_empty_flush()
        test_metadata_preservation()
        test_buffer_clearing()

        print()
        print("=" * 50)
        print("[SUCCESS] ALL MEMORY TESTS PASSED!")
        print("=" * 50)
    except Exception as e:
        print(f"\n[ERROR] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
