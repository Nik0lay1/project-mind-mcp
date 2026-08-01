"""
Background indexer for progressive, non-blocking codebase indexing.

This module provides a singleton BackgroundIndexer that runs indexing
in a daemon thread and writes progress to .ai/index_progress.json every
N files, so the MCP tool can return immediately and the AI can poll
get_index_progress() to track completion.

Status lifecycle:
  idle → scanning → initializing_model → indexing → finalizing → done
                                                               ↘ error
"""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from file_scanner import ScanResult, load_ignore_patterns, scan_files
from incremental_indexing import atomic_write
from logger import get_logger

logger = get_logger()

# Write progress to disk every N files processed
_PROGRESS_INTERVAL = 50

# Key in .ai/ where progress is persisted
_PROGRESS_FILENAME = "index_progress.json"

# Scan cap shared by _scan_files and the deleted-file pruning guard
_SCAN_MAX_FILES = 20000


def _progress_path() -> Path:
    """Returns the path to the progress file (respects reconfigure())."""
    return config.AI_DIR / _PROGRESS_FILENAME


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


def _write_progress(data: dict[str, Any]) -> None:
    """Atomically persist progress dict to disk."""
    try:
        config.AI_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write(_progress_path(), json.dumps(data, indent=2, default=str))
    except Exception as exc:
        logger.warning(f"BackgroundIndexer: could not write progress: {exc}")


def _read_progress() -> dict[str, Any] | None:
    """Read the last-written progress dict, or None if missing/corrupt."""
    path = _progress_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Singleton BackgroundIndexer
# ---------------------------------------------------------------------------


class BackgroundIndexer:
    """
    Singleton that runs codebase indexing in a background daemon thread.

    Only one indexing job may be active at a time. A new call to start()
    while a job is running is a no-op (returns False).

    Usage:
        BackgroundIndexer.start(force=False)   # fire-and-forget
        BackgroundIndexer.is_running()         # quick status check
        BackgroundIndexer.get_progress()       # dict with detailed state
        BackgroundIndexer.cancel()             # request graceful stop
    """

    _lock: threading.Lock = threading.Lock()
    _thread: threading.Thread | None = None
    _cancel_event: threading.Event = threading.Event()

    # In-memory mirror of the last written progress (avoids disk re-read in
    # the same process when the thread and the MCP tool share memory).
    # Guarded by _progress_lock: mutated by the daemon thread, copied by tools.
    _progress: dict[str, Any] = {}
    _progress_lock: threading.Lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    @classmethod
    def start(
        cls, force: bool = False, preempt: bool = False, preempt_timeout: float = 15.0
    ) -> bool:
        """
        Start background indexing unless one is already running.

        Args:
            force: If True, clears the existing index before indexing.
            preempt: If True, cancel a job that is already running and take its
                place. Without this an explicitly requested rebuild could be
                refused indefinitely by a job stuck in its final phase.
            preempt_timeout: How long to wait for the cancelled job to stop.

        Returns:
            True if a new job was started, False if one was already running
            (and could not be preempted).
        """
        if preempt:
            running = cls._preempt(preempt_timeout)
            if running:
                logger.warning(
                    "BackgroundIndexer: running job did not stop within "
                    f"{preempt_timeout:.0f}s; not starting a new one"
                )
                return False

        with cls._lock:
            if cls._thread is not None and cls._thread.is_alive():
                logger.info("BackgroundIndexer: job already running, ignoring start()")
                return False

            cls._cancel_event.clear()

            # Snapshot config paths at job-start time so reconfigure() later
            # doesn't affect a running job.
            root_dir = config.PROJECT_ROOT
            ai_dir = config.AI_DIR

            cls._thread = threading.Thread(
                target=cls._run,
                args=(root_dir, ai_dir, force),
                name="bg-indexer",
                daemon=True,
            )
            cls._thread.start()
            logger.info(f"BackgroundIndexer: started (force={force}, root={root_dir})")
            return True

    @classmethod
    def is_running(cls) -> bool:
        """Returns True if the background thread is alive."""
        with cls._lock:
            return cls._thread is not None and cls._thread.is_alive()

    @classmethod
    def _preempt(cls, timeout: float) -> bool:
        """
        Cancel the running job and wait for it to finish.

        Returns:
            True if a job is *still* running after the wait.
        """
        with cls._lock:
            thread = cls._thread
            if thread is None or not thread.is_alive():
                return False
        logger.info("BackgroundIndexer: preempting the running job")
        cls._cancel_event.set()
        thread.join(timeout=timeout)
        return thread.is_alive()

    @classmethod
    def get_progress(cls) -> dict[str, Any]:
        """
        Returns the latest progress dict for the *current* project root.

        Prefers the in-memory mirror; falls back to disk read if the mirror
        is empty (e.g. called from a different process / after restart).

        The mirror is only trusted when it belongs to the active root:
        `set_project_root()` used to leave the previous project's run in the
        mirror, so a fresh project reported someone else's stale (and failed)
        indexing job.
        """
        with cls._progress_lock:
            mirror = dict(cls._progress) if cls._progress else None

        if mirror is not None:
            mirror_root = mirror.get("root_dir")
            if mirror_root is None or Path(mirror_root) == config.PROJECT_ROOT:
                return mirror

        on_disk = _read_progress()
        if on_disk:
            return on_disk
        return {"status": "idle"}

    @classmethod
    def reset_progress(cls) -> None:
        """Drop the in-memory mirror (call when the project root changes)."""
        with cls._progress_lock:
            cls._progress = {}

    @classmethod
    def cancel(cls) -> bool:
        """
        Request the running job to stop at the next checkpoint.

        Returns:
            True if a cancellation was requested, False if no job running.
        """
        if cls.is_running():
            cls._cancel_event.set()
            logger.info("BackgroundIndexer: cancellation requested")
            return True
        return False

    # -----------------------------------------------------------------------
    # Internal — runs inside the daemon thread
    # -----------------------------------------------------------------------

    @classmethod
    def _update(cls, **kwargs: Any) -> None:
        """Update in-memory progress and persist to disk."""
        with cls._progress_lock:
            cls._progress.update(kwargs)
            cls._progress["updated_at"] = datetime.now(timezone.utc).isoformat()
            snapshot = dict(cls._progress)
        _write_progress(snapshot)

    @classmethod
    def _add_warning(cls, message: str) -> None:
        """
        Record a non-fatal problem with the run.

        Warnings survive into the persisted progress file so `health()` and
        `get_index_progress()` can show that a "done" run was in fact degraded.
        """
        logger.warning(f"BackgroundIndexer: {message}")
        with cls._progress_lock:
            warnings = list(cls._progress.get("warnings") or [])
            if message not in warnings:
                warnings.append(message)
            cls._progress["warnings"] = warnings
            cls._progress["updated_at"] = datetime.now(timezone.utc).isoformat()
            snapshot = dict(cls._progress)
        _write_progress(snapshot)

    @classmethod
    def _run(cls, root_dir: Path, ai_dir: Path, force: bool) -> None:
        """Main indexing routine executed in the background thread."""
        started_at = datetime.now(timezone.utc).isoformat()

        with cls._progress_lock:
            cls._progress = {
                "status": "scanning",
                "phase": "scan",
                "force": force,
                "files_total": 0,
                "files_done": 0,
                "chunks_done": 0,
                "started_at": started_at,
                "updated_at": started_at,
                "eta_seconds": None,
                "last_error": None,
                "warnings": [],
                "symbol_graph": None,
                "root_dir": str(root_dir),
            }
            snapshot = dict(cls._progress)
        _write_progress(snapshot)

        try:
            cls._do_index(root_dir, force)
        except Exception as exc:
            err_msg = traceback.format_exc()
            logger.error(f"BackgroundIndexer: unhandled exception:\n{err_msg}")
            cls._update(status="error", last_error=str(exc))

    @classmethod
    def _do_index(cls, root_dir: Path, force: bool) -> None:
        """Actual indexing logic — called from _run()."""
        from code_intelligence import invalidate_import_graph_cache
        from codebase_indexer import CodebaseIndexer
        from config import get_ignored_dirs
        from incremental_indexing import IndexMetadata

        # Build and save the manifest in the background thread first
        try:
            from manifest import build_manifest, save_manifest

            logger.info("BackgroundIndexer: building project manifest...")
            manifest = build_manifest(root_dir)
            save_manifest(manifest)
            logger.info("BackgroundIndexer: project manifest built and saved successfully")
        except Exception as e:
            logger.warning(f"BackgroundIndexer: failed to build manifest: {e}")

        # ── Phase 1: scan files ─────────────────────────────────────────────
        ignored_dirs = get_ignored_dirs()
        ignore_patterns = _load_ignore_patterns(root_dir)

        cls._update(status="scanning", phase="scan")

        scan = _scan_files(root_dir, ignored_dirs, ignore_patterns)
        indexable_files = scan.files
        scan_truncated = not scan.complete
        if scan_truncated:
            cls._add_warning(f"File scan incomplete: {scan.truncated}")
        if cls._cancel_event.is_set():
            cls._update(status="idle", last_error="Cancelled during scan")
            return

        from codebase_indexer import MAX_FILES_PER_INDEX

        total_files = len(indexable_files)
        if total_files > MAX_FILES_PER_INDEX:
            logger.warning(
                f"BackgroundIndexer: limiting to {MAX_FILES_PER_INDEX} of {total_files} files"
            )
            indexable_files = indexable_files[:MAX_FILES_PER_INDEX]

        cls._update(
            status="initializing_model",
            phase="model_init",
            files_total=len(indexable_files),
        )

        # ── Phase 2: initialise vector store (loads SentenceTransformer) ────
        from context import get_context
        from vector_store_manager import vector_stack_available

        ctx = get_context()
        bm25_direct = not vector_stack_available()

        if bm25_direct:
            logger.info("BackgroundIndexer: vector stack not installed — BM25-only indexing")
        else:
            # Initialise BEFORE clearing: clear_collection on a cold client would
            # fail (or recreate the collection without the embedding function).
            collection = ctx.vector_store.get_collection()
            if collection is None:
                cls._update(status="error", last_error="Failed to initialise vector store.")
                return

        if force:
            logger.info("BackgroundIndexer: clearing existing index (force=True)")
            err = ctx.vector_store.clear_collection()
            if err:
                cls._update(status="error", last_error=err)
                return

        if cls._cancel_event.is_set():
            cls._update(status="idle", last_error="Cancelled after model init")
            return

        # ── Phase 3: index files in batches ─────────────────────────────────
        cls._update(status="indexing", phase="embedding")

        from config import get_max_memory_bytes
        from memory_limited_indexer import MemoryLimitedIndexer

        max_memory = get_max_memory_bytes()

        indexer = CodebaseIndexer(ctx.vector_store)
        upserter = indexer._create_batch_upserter()
        mem_indexer = MemoryLimitedIndexer(max_memory, upserter)
        metadata = IndexMetadata()
        if force:
            metadata.metadata = {}

        file_count = 0
        import time as _time

        t_start = _time.monotonic()

        for i, file_path in enumerate(indexable_files):
            if cls._cancel_event.is_set():
                cls._update(status="idle", last_error="Cancelled during indexing")
                return

            on_chunks = None
            if bm25_direct:

                def on_chunks(
                    texts: list[str], metas: list[dict], cids: list[str], _fp=file_path
                ) -> None:
                    ctx.vector_store.update_bm25_source(str(_fp), cids, texts, metas)

            if indexer.process_file_with_metadata(
                file_path, mem_indexer, metadata, delete_stale=not force, on_chunks=on_chunks
            ):
                file_count += 1

            # Report progress every _PROGRESS_INTERVAL files
            if (i + 1) % _PROGRESS_INTERVAL == 0 or i == len(indexable_files) - 1:
                elapsed = _time.monotonic() - t_start
                rate = file_count / elapsed if elapsed > 0 else 0
                remaining = len(indexable_files) - (i + 1)
                eta = int(remaining / rate) if rate > 0 else None
                stats = mem_indexer.get_stats()
                cls._update(
                    files_done=i + 1,
                    files_total=len(indexable_files),
                    chunks_done=stats.get("total_chunks", 0),
                    eta_seconds=eta,
                )

        # ── Phase 4: flush + BM25 rebuild ───────────────────────────────────
        cls._update(status="finalizing", phase="bm25", eta_seconds=None)

        try:
            mem_indexer.flush()
        except Exception:
            pass  # upserter.failed is set; handled below

        if upserter.failed:
            cls._update(
                status="error",
                last_error=(
                    "Some chunks could not be written to the vector store. "
                    "Index metadata was not saved; affected files will be "
                    "re-indexed on the next run."
                ),
            )
            return

        # Only prune "deleted" files when the scan saw the whole tree — a
        # truncated scan would misclassify live files beyond the cap as deleted.
        if not scan_truncated:
            # Shared with the foreground path so both stores (vector *and*
            # BM25) forget the file — clearing only one left deleted files
            # answering keyword searches.
            removed = indexer._prune_deleted(metadata, indexable_files)
            if removed:
                logger.info(f"BackgroundIndexer: removed chunks of {removed} deleted files")
        metadata.save()

        logger.info("BackgroundIndexer: rebuilding BM25 index...")
        ctx.vector_store.finalize_bm25(incremental_ok=bm25_direct)

        invalidate_import_graph_cache()

        # Refresh the symbol graph while we're already in a background thread,
        # so search-time callers get it for free via peek_symbol_graph().
        if cls._cancel_event.is_set():
            cls._update(status="idle", last_error="Cancelled before symbol graph rebuild")
            return
        cls._update(status="finalizing", phase="symbol_graph")
        try:
            from symbol_graph import get_or_build_symbol_graph

            logger.info("BackgroundIndexer: rebuilding symbol graph...")
            graph = get_or_build_symbol_graph(force=True)
            # A silent `except: log.warning` here is what kept a symbol graph
            # built entirely from build output invisible: find_symbol just
            # answered "no symbols" forever. Failures and partial builds are
            # now part of the reported job state.
            if graph.truncated:
                cls._add_warning(f"Symbol graph is incomplete: {graph.truncated}")
            elif graph.symbol_count == 0:
                cls._add_warning(
                    "Symbol graph built but contains no symbols — check that the "
                    "project's source directories are not excluded by .indexignore."
                )
            cls._update(symbol_graph=graph.status_line())
        except Exception as e:
            logger.error(f"BackgroundIndexer: symbol graph rebuild failed: {e}", exc_info=True)
            cls._add_warning(f"Symbol graph rebuild failed: {e}")
            cls._update(symbol_graph=f"failed: {e}")

        stats = mem_indexer.get_stats()
        cls._update(
            status="done",
            phase="done",
            files_done=len(indexable_files),
            files_total=len(indexable_files),
            chunks_done=stats.get("total_chunks", 0),
            eta_seconds=0,
        )
        logger.info(
            f"BackgroundIndexer: done — {file_count} files, "
            f"{stats.get('total_chunks', 0)} chunks"
        )


# ---------------------------------------------------------------------------
# Helpers used only inside this module
# ---------------------------------------------------------------------------


def _scan_files(
    root_dir: Path,
    ignored_dirs: set[str],
    ignore_patterns: set[str],
) -> ScanResult:
    """Scan without constructing a VectorStoreManager (avoids the model load)."""
    return scan_files(
        root_dir,
        ignore_patterns=ignore_patterns,
        ignored_dirs=ignored_dirs,
        max_files=_SCAN_MAX_FILES,
    )


def _load_ignore_patterns(root_dir: Path) -> set[str]:
    """Reads .indexignore (root-level preferred, .ai/ fallback)."""
    return load_ignore_patterns(root_dir)


def format_progress_markdown(data: dict[str, Any]) -> str:
    """
    Converts a progress dict into a human-readable Markdown string
    suitable for returning from an MCP tool.
    """
    status = data.get("status", "unknown")
    phase = data.get("phase", "")
    files_done = data.get("files_done", 0)
    files_total = data.get("files_total", 0)
    chunks = data.get("chunks_done", 0)
    eta = data.get("eta_seconds")
    started_at = data.get("started_at", "")
    updated_at = data.get("updated_at", "")
    last_error = data.get("last_error")
    root_dir = data.get("root_dir", "")

    icon_map = {
        "idle": "💤",
        "scanning": "🔍",
        "initializing_model": "🧠",
        "indexing": "⚙️",
        "finalizing": "🔄",
        "done": "✅",
        "error": "❌",
    }
    icon = icon_map.get(status, "❓")

    pct = int(files_done / files_total * 100) if files_total > 0 else 0
    bar_filled = int(pct / 5)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    lines = [
        f"## {icon} Indexing Status: `{status}`",
        f"**Phase**: {phase}" if phase else "",
        f"**Project**: `{root_dir}`" if root_dir else "",
        "",
        f"**Progress**: [{bar}] {pct}%",
        f"**Files**: {files_done} / {files_total}",
        f"**Chunks indexed**: {chunks:,}",
    ]

    if eta is not None and status not in ("done", "idle", "error"):
        if eta > 60:
            lines.append(f"**ETA**: ~{eta // 60} min {eta % 60} sec")
        else:
            lines.append(f"**ETA**: ~{eta} sec")

    if started_at:
        lines.append(f"**Started**: {started_at}")
    if updated_at:
        lines.append(f"**Last update**: {updated_at}")

    symbol_graph = data.get("symbol_graph")
    if symbol_graph:
        lines.append(f"**Symbol graph**: {symbol_graph}")

    if last_error:
        lines.append(f"\n⚠️ **Error**: {last_error}")

    warnings = data.get("warnings") or []
    if warnings:
        lines.append("\n⚠️ **Warnings**:")
        lines.extend(f"- {w}" for w in warnings)

    if status == "done":
        if warnings:
            lines.append(
                "\n⚠️ Indexing finished, but with the warnings above — search results "
                "may be incomplete."
            )
        else:
            lines.append(
                "\n✅ Indexing complete! You can now use `search_codebase()` for semantic search."
            )
    elif status == "initializing_model":
        lines.append("\n_Loading SentenceTransformer model — this takes 30–60 s on first run._")
    elif status in ("scanning", "indexing", "finalizing"):
        lines.append("\n_Call `get_index_progress()` again to refresh._")

    return "\n".join(ln for ln in lines if ln is not None)
