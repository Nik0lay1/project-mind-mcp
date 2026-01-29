import os
from collections.abc import Callable
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    BATCH_SIZE,
    BINARY_EXTENSIONS,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    INDEXABLE_EXTENSIONS,
    get_max_file_size_bytes,
    get_max_memory_bytes,
    safe_read_text,
)
from incremental_indexing import IndexMetadata
from logger import get_logger
from memory_limited_indexer import MemoryLimitedIndexer
from vector_store_manager import VectorStoreManager

logger = get_logger()

BatchUpsertCallback = Callable[[list[str], list[dict], list[str]], None]


class CodebaseIndexer:
    """
    Manages codebase indexing operations.
    Encapsulates file scanning, chunking, and indexing logic.
    """

    def __init__(self, vector_store: VectorStoreManager):
        """
        Initialize codebase indexer.

        Args:
            vector_store: VectorStoreManager instance for storing chunks
        """
        self.vector_store = vector_store
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
        )

    def _create_batch_upsert_callback(self) -> BatchUpsertCallback:
        """
        Creates a callback function for batch upserting documents.

        Returns:
            Callback function for MemoryLimitedIndexer
        """

        def batch_upsert(
            documents: list[str], metadatas: list[dict], ids: list[str]
        ) -> None:
            for i in range(0, len(documents), BATCH_SIZE):
                end = min(i + BATCH_SIZE, len(documents))
                self.vector_store.upsert(
                    documents=documents[i:end],
                    metadatas=metadatas[i:end],
                    ids=ids[i:end],
                )

        return batch_upsert

    def should_index_file(self, file_path: Path, ignore_patterns: set[str]) -> bool:
        """
        Determines if a file should be indexed.

        Args:
            file_path: Path to check
            ignore_patterns: Patterns to ignore

        Returns:
            True if file should be indexed
        """
        if file_path.suffix in BINARY_EXTENSIONS:
            return False

        if file_path.suffix and file_path.suffix not in INDEXABLE_EXTENSIONS:
            return False

        file_str = str(file_path)
        for pattern in ignore_patterns:
            if pattern in file_str:
                return False

        try:
            file_size = file_path.stat().st_size
            if file_size > get_max_file_size_bytes():
                logger.info(f"Skipping {file_path}: exceeds max file size")
                return False
        except Exception:
            return False

        return True

    def scan_indexable_files(
        self, root_dir: Path, ignored_dirs: set[str], ignore_patterns: set[str]
    ) -> list[Path]:
        """
        Scans directory tree and returns list of indexable files.

        Args:
            root_dir: Root directory to scan
            ignored_dirs: Directories to skip
            ignore_patterns: File patterns to ignore

        Returns:
            List of indexable file paths
        """
        indexable_files = []

        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in ignored_dirs]

            for file in files:
                file_path = Path(root) / file
                if self.should_index_file(file_path, ignore_patterns):
                    indexable_files.append(file_path)

        return indexable_files

    def process_file_to_chunks(self, file_path: Path, indexer: MemoryLimitedIndexer) -> bool:
        """
        Processes a single file: reads, splits into chunks, adds to indexer.

        Args:
            file_path: File to process
            indexer: Memory-limited indexer to add chunks to

        Returns:
            True if file was successfully processed
        """
        try:
            content = safe_read_text(file_path)
            if not content.strip():
                return False

            chunks = self.text_splitter.split_text(content)

            for i, chunk in enumerate(chunks):
                indexer.add_chunk(
                    chunk, {"source": str(file_path), "chunk_index": i}, f"{file_path}_{i}"
                )

            return True
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Skipping {file_path}: encoding error - {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error processing {file_path}: {e}", exc_info=True)
            return False

    def process_file_with_metadata(
        self, file_path: Path, indexer: MemoryLimitedIndexer, metadata: IndexMetadata
    ) -> bool:
        """
        Processes a file and updates its metadata.

        Args:
            file_path: File to process
            indexer: Memory-limited indexer
            metadata: Index metadata to update

        Returns:
            True if file was successfully processed
        """
        if not self.process_file_to_chunks(file_path, indexer):
            return False

        try:
            mtime = file_path.stat().st_mtime
            metadata.update_file(str(file_path), mtime)
            return True
        except Exception as e:
            logger.error(f"Error updating metadata for {file_path}: {e}")
            return False

    def index_all(
        self, root_dir: Path, ignored_dirs: set[str], ignore_patterns: set[str], force: bool = False
    ) -> str:
        """
        Indexes entire codebase.

        Args:
            root_dir: Root directory to index
            ignored_dirs: Directories to skip
            ignore_patterns: File patterns to ignore
            force: If True, clears existing index first

        Returns:
            Status message with indexing stats
        """
        if force:
            logger.info("Clearing existing index...")
            error = self.vector_store.clear_collection()
            if error:
                return error

        max_memory = get_max_memory_bytes()
        indexer = MemoryLimitedIndexer(max_memory, self._create_batch_upsert_callback())

        logger.info(f"Scanning files (memory limit: {max_memory / 1024 / 1024:.0f} MB)...")

        indexable_files = self.scan_indexable_files(root_dir, ignored_dirs, ignore_patterns)
        file_count = 0

        for file_path in indexable_files:
            if self.process_file_to_chunks(file_path, indexer):
                file_count += 1

        indexer.flush()

        stats = indexer.get_stats()
        return f"Indexed {file_count} files ({stats['total_chunks']} chunks in {stats['total_batches']} batches)."

    def index_changed(
        self, root_dir: Path, ignored_dirs: set[str], ignore_patterns: set[str]
    ) -> str:
        """
        Indexes only changed files (incremental indexing).

        Args:
            root_dir: Root directory to scan
            ignored_dirs: Directories to skip
            ignore_patterns: File patterns to ignore

        Returns:
            Status message with indexing stats
        """
        metadata = IndexMetadata()

        all_files = self.scan_indexable_files(root_dir, ignored_dirs, ignore_patterns)
        changed_files = metadata.get_changed_files(all_files)

        if not changed_files:
            return "No changed files to index."

        max_memory = get_max_memory_bytes()
        indexer = MemoryLimitedIndexer(max_memory, self._create_batch_upsert_callback())

        logger.info(
            f"Found {len(changed_files)} changed files (memory limit: {max_memory / 1024 / 1024:.0f} MB)..."
        )
        file_count = 0

        for file_path in changed_files:
            if self.process_file_with_metadata(file_path, indexer, metadata):
                file_count += 1

        indexer.flush()

        existing_files = {str(f) for f in all_files}
        metadata.remove_deleted_files(existing_files)
        metadata.save()

        stats = indexer.get_stats()
        return f"Incrementally indexed {file_count} changed files ({stats['total_chunks']} chunks in {stats['total_batches']} batches)."
