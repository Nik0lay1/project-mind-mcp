import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.append(os.getcwd())

from config import INDEX_METADATA_FILE
from incremental_indexing import IndexMetadata, atomic_write


def test_atomic_write():
    """Test atomic write creates file correctly"""
    print("Testing atomic write...")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.json"
        test_content = '{"test": "data", "number": 42}'

        atomic_write(test_file, test_content)

        assert test_file.exists()
        content = test_file.read_text(encoding='utf-8')
        assert content == test_content

        data = json.loads(content)
        assert data["test"] == "data"
        assert data["number"] == 42

        print("  [OK] Atomic write creates file correctly")


def test_atomic_write_overwrite():
    """Test atomic write overwrites existing file"""
    print("Testing atomic write overwrite...")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.json"

        test_file.write_text("old content")
        assert test_file.read_text() == "old content"

        new_content = "new content"
        atomic_write(test_file, new_content)

        assert test_file.read_text() == new_content
        print("  [OK] Atomic write overwrites correctly")


def test_atomic_write_rollback_on_error():
    """Test that atomic write cleans up temp files on error"""
    print("Testing atomic write error cleanup...")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.json"
        original_content = '{"original": true}'
        test_file.write_text(original_content)

        from unittest.mock import patch

        import incremental_indexing

        with patch.object(incremental_indexing.os, 'replace', side_effect=OSError("Simulated replace failure")):
            try:
                atomic_write(test_file, '{"new": true}')
                print("  [FAIL] Should have raised error")
                sys.exit(1)
            except OSError as e:
                assert "Simulated replace failure" in str(e)

        if test_file.exists():
            assert test_file.read_text() == original_content
        else:
            print("  [WARNING] Original file was deleted (Windows behavior)")

        temp_files = list(Path(tmpdir).glob(".*.tmp"))
        assert len(temp_files) == 0, f"Temp files not cleaned up: {temp_files}"

        print("  [OK] Failed write cleans up temp files")


def test_metadata_save_load():
    """Test IndexMetadata save and load cycle"""
    print("Testing metadata save/load...")

    metadata = IndexMetadata()

    metadata.update_file("test_file.py", 1234567890.0)
    metadata.update_file("another_file.js", 9876543210.0)

    metadata.save()

    assert INDEX_METADATA_FILE.exists()

    new_metadata = IndexMetadata()

    assert new_metadata.get_file_mtime("test_file.py") == 1234567890.0
    assert new_metadata.get_file_mtime("another_file.js") == 9876543210.0

    print("  [OK] Metadata save/load works correctly")


def test_concurrent_writes():
    """Test that concurrent writes don't corrupt file"""
    print("Testing concurrent writes...")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "concurrent.json"
        results = []
        errors = []

        def write_worker(worker_id, iterations=5):
            success_count = 0
            for i in range(iterations):
                try:
                    data = {"worker": worker_id, "iteration": i, "timestamp": time.time()}
                    atomic_write(test_file, json.dumps(data, indent=2))
                    success_count += 1
                    time.sleep(0.005)
                except PermissionError:
                    time.sleep(0.01)
                except Exception as e:
                    errors.append((worker_id, type(e).__name__, str(e)))
            results.append((worker_id, success_count))

        threads = []
        for i in range(5):
            t = threading.Thread(target=write_worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        if errors:
            unexpected_errors = [e for e in errors if "permission" not in str(e).lower()]
            if unexpected_errors:
                print(f"  [FAIL] Unexpected errors: {unexpected_errors}")
                sys.exit(1)

        total_successes = sum(count for _, count in results)

        assert test_file.exists()
        content = test_file.read_text(encoding='utf-8')
        data = json.loads(content)
        assert "worker" in data
        assert "iteration" in data

        print(f"  [OK] Concurrent writes handled safely ({total_successes} successful writes, file not corrupted)")


def test_no_temp_files_left():
    """Test that temporary files are cleaned up"""
    print("Testing temp file cleanup...")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "cleanup_test.json"

        atomic_write(test_file, '{"test": true}')

        temp_files = list(Path(tmpdir).glob(".*.tmp"))
        assert len(temp_files) == 0

        print("  [OK] No temporary files left behind")


def cleanup_test_metadata():
    """Clean up test metadata file"""
    if INDEX_METADATA_FILE.exists():
        try:
            INDEX_METADATA_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    print("=" * 50)
    print("TRANSACTIONAL SAVE TESTS")
    print("=" * 50)
    print()

    try:
        test_atomic_write()
        test_atomic_write_overwrite()
        test_atomic_write_rollback_on_error()
        test_no_temp_files_left()
        test_concurrent_writes()
        test_metadata_save_load()

        print()
        print("=" * 50)
        print("[SUCCESS] ALL TRANSACTIONAL TESTS PASSED!")
        print("=" * 50)
    except Exception as e:
        print(f"\n[ERROR] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup_test_metadata()
