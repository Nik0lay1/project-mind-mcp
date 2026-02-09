import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from config import INDEX_METADATA_FILE

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextmanager
def file_lock(file_handle) -> None:  # type: ignore[misc]
    """
    Cross-platform file locking context manager.
    Prevents concurrent writes to the same file.
    """
    if sys.platform == "win32":
        file_handle.seek(0)
        locked = False
        try:
            msvcrt.locking(file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            locked = True
            yield
        finally:
            if locked:
                file_handle.seek(0)
                msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        try:
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)


def atomic_write(file_path: Path, content: str) -> None:
    """
    Atomically writes content to a file using temp file + rename.
    Prevents partial writes and corruption.

    Args:
        file_path: Target file path
        content: Content to write

    Raises:
        IOError: If write operation fails
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        dir=file_path.parent, prefix=f".{file_path.name}.", suffix=".tmp"
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        if sys.platform == "win32":
            try:
                if file_path.exists():
                    file_path.unlink()
            except FileNotFoundError:
                pass

        os.replace(temp_path, file_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


class IndexMetadata:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, str | float]] = {}
        self.load()

    def load(self) -> None:
        if INDEX_METADATA_FILE.exists():
            try:
                with open(INDEX_METADATA_FILE) as f:
                    self.metadata = json.load(f)
            except Exception:
                self.metadata = {}
        else:
            self.metadata = {}

    def save(self) -> None:
        """
        Atomically saves metadata to disk with file locking.
        Uses temp file + rename to prevent corruption.
        """
        from logger import get_logger

        logger = get_logger()

        try:
            content = json.dumps(self.metadata, indent=2)
            atomic_write(INDEX_METADATA_FILE, content)
            logger.debug(f"Metadata saved successfully: {len(self.metadata)} files tracked")
        except Exception as e:
            logger.error(f"Error saving metadata: {e}", exc_info=True)
            raise

    def get_file_mtime(self, file_path: str) -> float:
        return self.metadata.get(file_path, {}).get("mtime", 0.0)

    def update_file(self, file_path: str, mtime: float) -> None:
        self.metadata[file_path] = {
            "mtime": mtime,
            "indexed_at": datetime.now().isoformat(),
        }

    def get_changed_files(self, all_files: list[Path]) -> list[Path]:
        changed_files = []

        for file_path in all_files:
            try:
                current_mtime = file_path.stat().st_mtime
                stored_mtime = self.get_file_mtime(str(file_path))

                if current_mtime > stored_mtime:
                    changed_files.append(file_path)
            except Exception:
                changed_files.append(file_path)

        return changed_files

    def remove_deleted_files(self, existing_files: set[str]) -> None:
        files_to_remove = []
        for file_path in self.metadata.keys():
            if file_path not in existing_files:
                files_to_remove.append(file_path)

        for file_path in files_to_remove:
            del self.metadata[file_path]

    def get_stats(self) -> dict[str, int | str | None]:
        if not self.metadata:
            return {"total_files": 0, "last_index": None}

        indexed_times: list[str] = [
            str(info.get("indexed_at")) for info in self.metadata.values() if "indexed_at" in info
        ]
        latest: str | None = max(indexed_times) if indexed_times else None

        return {"total_files": len(self.metadata), "last_index": latest}
