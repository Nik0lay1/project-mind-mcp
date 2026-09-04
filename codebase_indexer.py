from collections.abc import Callable
from pathlib import Path

import config
from ast_splitter import ASTSplitter
from config import (
    BATCH_SIZE,
    BINARY_EXTENSIONS,
    INDEXABLE_EXTENSIONS,
    get_max_file_size_bytes,
    get_max_memory_bytes,
    safe_read_text,
)
from file_scanner import IgnoreMatcher, ScanResult, scan_files
from incremental_indexing import IndexMetadata
from logger import get_logger
from memory_limited_indexer import MemoryLimitedIndexer
from vector_store_manager import VectorStoreManager

logger = get_logger()

# Maximum files to index in a single index_all operation
# This prevents extremely long operations that can cause timeouts
MAX_FILES_PER_INDEX = 5000

# Progress reporting interval (every N files)
PROGRESS_REPORT_INTERVAL = 100

BatchUpsertCallback = Callable[[list[str], list[dict], list[str]], None]
ProgressCallback = Callable[[int, int], None]  # (files_done, files_total)


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
        self.splitter = ASTSplitter()

    class _BatchUpserter:
        """
        Batch-upsert callback that remembers failures. A failed upsert must not
        be silent: callers check `failed` and skip saving index metadata so the
        affected files are re-indexed on the next run instead of being lost.
        """

        def __init__(self, vector_store: VectorStoreManager) -> None:
            self.vector_store = vector_store
            self.failed = False

        def __call__(self, documents: list[str], metadatas: list[dict], ids: list[str]) -> None:
            from vector_store_manager import vector_stack_available

            if not vector_stack_available():
                return  # BM25-only mode: chunks reach BM25 via on_chunks
            for i in range(0, len(documents), BATCH_SIZE):
                end = min(i + BATCH_SIZE, len(documents))
                ok = self.vector_store.upsert(
                    documents=documents[i:end],
                    metadatas=metadatas[i:end],
                    ids=ids[i:end],
                )
                if not ok:
                    self.failed = True
                    raise RuntimeError("Vector store upsert failed")

    def _create_batch_upserter(self) -> "_BatchUpserter":
        return self._BatchUpserter(self.vector_store)

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

        # Same matcher the shared scanner uses, so a file rejected here is
        # exactly a file the scan would not have collected.
        if IgnoreMatcher(config.PROJECT_ROOT, ignore_patterns).matches(file_path):
            return False

        try:
            file_size = file_path.stat().st_size
            if file_size > get_max_file_size_bytes():
                logger.info(f"Skipping {file_path}: exceeds max file size")
                return False
        except Exception:
            return False

        return True

    def scan_files(
        self,
        root_dir: Path,
        ignored_dirs: set[str],
        ignore_patterns: set[str],
        max_files: int = 20000,
    ) -> ScanResult:
        """
        Scans the tree via the shared scanner, keeping truncation information.

        Prefer this over :meth:`scan_indexable_files` when the caller needs to
        know whether the whole tree was seen (e.g. before pruning "deleted"
        files, which a partial scan would get wrong).
        """
        result = scan_files(
            root_dir,
            ignore_patterns=ignore_patterns,
            ignored_dirs=ignored_dirs,
            max_files=max_files,
        )
        if result.truncated:
            logger.warning(f"Scan incomplete: {result.truncated}")
        return result

    def scan_indexable_files(
        self,
        root_dir: Path,
        ignored_dirs: set[str],
        ignore_patterns: set[str],
        max_files: int = 20000,
    ) -> list[Path]:
        """
        Scans directory tree and returns list of indexable files.

        Args:
            root_dir: Root directory to scan
            ignored_dirs: Directories to skip
            ignore_patterns: File patterns to ignore
            max_files: Maximum files to scan (safety limit)

        Returns:
            List of indexable file paths
        """
        return self.scan_files(root_dir, ignored_dirs, ignore_patterns, max_files).files

    def process_file_to_chunks(
        self,
        file_path: Path,
        indexer: MemoryLimitedIndexer,
        on_chunks: Callable[[list[str], list[dict], list[str]], None] | None = None,
    ) -> bool:
        """
        Processes a single file: reads, splits into AST-aware chunks, adds to indexer.

        Args:
            file_path: File to process
            indexer: Memory-limited indexer to add chunks to
            on_chunks: Optional callback receiving this file's
                (texts, metadatas, ids) — used for incremental BM25 updates.

        Returns:
            True if file was successfully processed
        """
        try:
            content = safe_read_text(file_path)
            if not content.strip():
                return True

            chunks = self.splitter.split(content, file_path)

            texts: list[str] = []
            metas: list[dict] = []
            ids: list[str] = []
            for chunk in chunks:
                text = chunk["text"]
                meta = chunk["metadata"]
                class_prefix = f"{meta['class_name']}_" if meta.get("class_name") else ""
                # line_start disambiguates same-named symbols in one file
                # (overloads, conditional defs) that would otherwise collide.
                chunk_id = (
                    f"{file_path}_{meta['symbol_type']}_{class_prefix}{meta['symbol_name']}"
                    f"_{meta.get('line_start', 0)}_{meta['chunk_index']}"
                )
                indexer.add_chunk(text, meta, chunk_id)
                if on_chunks is not None:
                    texts.append(text)
                    metas.append(meta)
                    ids.append(chunk_id)

            if on_chunks is not None:
                on_chunks(texts, metas, ids)

            return True
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Skipping {file_path}: encoding error - {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error processing {file_path}: {e}", exc_info=True)
            return False

    def process_file_with_metadata(
        self,
        file_path: Path,
        indexer: MemoryLimitedIndexer,
        metadata: IndexMetadata,
        delete_stale: bool = True,
        on_chunks: Callable[[list[str], list[dict], list[str]], None] | None = None,
    ) -> bool:
        """
        Processes a file and updates its metadata.

        Args:
            file_path: File to process
            indexer: Memory-limited indexer
            metadata: Index metadata to update
            delete_stale: Delete the file's previous chunks first, so renamed or
                removed symbols don't linger in the index forever.
            on_chunks: Optional per-file chunk callback (incremental BM25).

        Returns:
            True if file was successfully processed
        """
        try:
            # Stat before reading: if the file changes mid-read, the recorded
            # mtime is the pre-read one, so the newer edit is picked up next run.
            mtime = file_path.stat().st_mtime
        except Exception as e:
            logger.error(f"Error reading mtime for {file_path}: {e}")
            return False

        if delete_stale and str(file_path) in metadata.metadata:
            self.vector_store.delete_by_source(str(file_path))

        if not self.process_file_to_chunks(file_path, indexer, on_chunks=on_chunks):
            return False

        metadata.update_file(str(file_path), mtime)
        return True

    def _prune_deleted(self, metadata: IndexMetadata, existing: list[Path]) -> int:
        """
        Remove index entries for files that no longer exist on disk.

        Both stores must be cleared: dropping only the vector chunks leaves the
        file in the BM25 corpus, where it keeps matching keyword queries.

        Args:
            metadata: Index metadata to reconcile.
            existing: Every file the (complete) scan found.

        Returns:
            Number of deleted files removed from the index.
        """
        removed = metadata.remove_deleted_files({str(f) for f in existing})
        for removed_path in removed:
            self.vector_store.delete_by_source(removed_path)
            self.vector_store.remove_bm25_source(removed_path)
        if removed:
            logger.info(f"Removed chunks of {len(removed)} deleted files from the index")
        return len(removed)

    def index_all(
        self,
        root_dir: Path,
        ignored_dirs: set[str],
        ignore_patterns: set[str],
        force: bool = False,
        progress_callback: "ProgressCallback | None" = None,
    ) -> str:
        """
        Indexes entire codebase.

        Args:
            root_dir: Root directory to index
            ignored_dirs: Directories to skip
            ignore_patterns: File patterns to ignore
            force: If True, clears existing index first
            progress_callback: Optional callable(files_done, files_total) invoked
                every PROGRESS_REPORT_INTERVAL files so callers (e.g.
                BackgroundIndexer) can track live progress without polling.

        Returns:
            Status message with indexing stats
        """
        if force:
            logger.info("Clearing existing index...")
            error = self.vector_store.clear_collection()
            if error:
                return error

        metadata = IndexMetadata()
        if force:
            metadata.metadata = {}

        max_memory = get_max_memory_bytes()
        upserter = self._create_batch_upserter()
        indexer = MemoryLimitedIndexer(max_memory, upserter)

        logger.info(f"Scanning files (memory limit: {max_memory / 1024 / 1024:.0f} MB)...")

        scan = self.scan_files(root_dir, ignored_dirs, ignore_patterns)
        indexable_files = scan.files

        # Apply limit to prevent extremely long operations
        total_files = len(indexable_files)
        if total_files > MAX_FILES_PER_INDEX:
            logger.warning(f"Limiting index to {MAX_FILES_PER_INDEX} of {total_files} files")
            indexable_files = indexable_files[:MAX_FILES_PER_INDEX]

        file_count = 0

        # Without the vector stack, chunks flow straight into the BM25 corpus.
        from vector_store_manager import vector_stack_available

        bm25_direct = not vector_stack_available()

        for i, file_path in enumerate(indexable_files):
            on_chunks = None
            if bm25_direct:

                def on_chunks(
                    texts: list[str], metas: list[dict], cids: list[str], _fp=file_path
                ) -> None:
                    self.vector_store.update_bm25_source(str(_fp), cids, texts, metas)

            if self.process_file_with_metadata(
                file_path, indexer, metadata, delete_stale=not force, on_chunks=on_chunks
            ):
                file_count += 1
            # Progress reporting
            if file_count % PROGRESS_REPORT_INTERVAL == 0:
                logger.info(f"Progress: {file_count}/{len(indexable_files)} files processed...")
                if progress_callback is not None:
                    try:
                        progress_callback(i + 1, len(indexable_files))
                    except Exception:
                        pass

        try:
            indexer.flush()
        except Exception:
            pass  # upserter.failed is set; handled below

        if upserter.failed:
            return (
                "Indexing completed with errors: some chunks could not be written to the "
                "vector store. Index metadata was NOT saved, so affected files will be "
                "re-indexed on the next run. Check the log for details."
            )

        # Drop files that disappeared since the last run, so deleted sources
        # stop showing up in search results. Only safe when the scan saw the
        # whole tree and the file list was not capped — otherwise live files
        # beyond the cap would be misread as deleted.
        removed_count = 0
        if not force and scan.complete and total_files <= MAX_FILES_PER_INDEX:
            removed_count = self._prune_deleted(metadata, indexable_files)

        metadata.save()

        logger.info("Rebuilding BM25 index...")
        self.vector_store.finalize_bm25(incremental_ok=bm25_direct)

        stats = indexer.get_stats()
        warning = (
            "" if total_files <= MAX_FILES_PER_INDEX else f" (limited from {total_files} files)"
        )
        if removed_count:
            warning += f", removed {removed_count} deleted files"
        return f"Indexed {file_count} files ({stats['total_chunks']} chunks in {stats['total_batches']} batches){warning}."

    def index_changed(
        self,
        root_dir: Path,
        ignored_dirs: set[str],
        ignore_patterns: set[str],
        progress_callback: "ProgressCallback | None" = None,
    ) -> str:
        """
        Indexes only changed files (incremental indexing).

        Args:
            root_dir: Root directory to scan
            ignored_dirs: Directories to skip
            ignore_patterns: File patterns to ignore
            progress_callback: Optional callable(files_done, files_total) invoked
                every PROGRESS_REPORT_INTERVAL files.

        Returns:
            Status message with indexing stats
        """
        metadata = IndexMetadata()

        scan_cap = 20000
        scan = self.scan_files(root_dir, ignored_dirs, ignore_patterns, max_files=scan_cap)
        all_files = scan.files
        scan_truncated = not scan.complete
        changed_files = metadata.get_changed_files(all_files)

        if not changed_files:
            # Nothing was edited, but files may still have been *deleted* —
            # reconcile before returning, otherwise a removed file keeps
            # answering searches until someone runs prune_index by hand.
            removed = 0 if scan_truncated else self._prune_deleted(metadata, all_files)
            if removed:
                metadata.save()
                self.vector_store.finalize_bm25(incremental_ok=True)
                return f"No changed files to index. Removed {removed} deleted files from the index."
            return "No changed files to index."

        max_memory = get_max_memory_bytes()
        upserter = self._create_batch_upserter()
        indexer = MemoryLimitedIndexer(max_memory, upserter)

        logger.info(
            f"Found {len(changed_files)} changed files (memory limit: {max_memory / 1024 / 1024:.0f} MB)..."
        )
        file_count = 0

        # Incremental BM25: patch each file's rows in the in-memory corpus and
        # rebuild the matrix once at the end — no full ChromaDB fetch.
        bm25_incremental_ok = True

        for i, file_path in enumerate(changed_files):

            def _bm25_update(
                texts: list[str], metas: list[dict], ids: list[str], _fp=file_path
            ) -> None:
                nonlocal bm25_incremental_ok
                if not self.vector_store.update_bm25_source(str(_fp), ids, texts, metas):
                    bm25_incremental_ok = False

            if self.process_file_with_metadata(
                file_path, indexer, metadata, on_chunks=_bm25_update
            ):
                file_count += 1
            # Progress reporting
            if file_count % PROGRESS_REPORT_INTERVAL == 0:
                logger.info(f"Progress: {file_count}/{len(changed_files)} files processed...")
                if progress_callback is not None:
                    try:
                        progress_callback(i + 1, len(changed_files))
                    except Exception:
                        pass

        try:
            indexer.flush()
        except Exception:
            pass  # upserter.failed is set; handled below

        if upserter.failed:
            return (
                "Incremental indexing completed with errors: some chunks could not be "
                "written to the vector store. Index metadata was NOT saved, so affected "
                "files will be re-indexed on the next run. Check the log for details."
            )

        # Only prune "deleted" files when the scan saw the whole tree — a
        # truncated scan would misclassify live files beyond the cap as deleted.
        if not scan_truncated:
            self._prune_deleted(metadata, all_files)
        else:
            logger.warning(f"File scan hit the {scan_cap}-file cap; skipping deleted-file pruning")

        metadata.save()

        logger.info("Rebuilding BM25 index...")
        self.vector_store.finalize_bm25(incremental_ok=bm25_incremental_ok)

        stats = indexer.get_stats()
        return f"Incrementally indexed {file_count} changed files ({stats['total_chunks']} chunks in {stats['total_batches']} batches)."
