"""
Self-healing maintenance daemon for ProjectMind.

Runs a single background thread that periodically:
  - Garbage-collects stale chunks (deleted files, mtime drift)
  - Compacts the ChromaDB SQLite when it exceeds size threshold
  - Rotates / truncates the projectmind log if it grows too large
  - Drops cold caches (file cache, query cache) on memory pressure
  - Unloads the sentence-transformer model after idle period
  - Refreshes the L0 manifest when stale

All tasks are idempotent and best-effort: failures are logged but never
crash the server. Each task has its own interval and last-run timestamp;
they do not block MCP tools.

State is kept in `.ai/maintenance_state.json` so successive runs respect
the schedule across restarts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from logger import get_logger

logger = get_logger()

STATE_FILENAME = "maintenance_state.json"

# Default intervals (seconds)
INTERVAL_MANIFEST_REFRESH = 5 * 60  # 5 min
INTERVAL_STALE_CHUNK_GC = 60 * 60  # 1 h
INTERVAL_DB_COMPACTION = 24 * 60 * 60  # 24 h
INTERVAL_LOG_TRUNCATE = 6 * 60 * 60  # 6 h
INTERVAL_MODEL_UNLOAD_CHECK = 5 * 60  # 5 min
INTERVAL_CACHE_PRESSURE = 60  # 1 min

DAEMON_TICK_SECONDS = 30

DB_COMPACTION_THRESHOLD_MB = 200
DB_HARD_LIMIT_MB = 800  # Above this, trigger emergency reindex suggestion
LOG_TRUNCATE_THRESHOLD_MB = 8


def _env_int(key: str, default: int) -> int:
    """Read an integer from environment or return default."""
    val = os.getenv(key)
    if val is None:
        return default
    try:
        parsed = int(val)
        return parsed if parsed > 0 else default
    except ValueError:
        return default


MODEL_IDLE_UNLOAD_SECONDS = _env_int("PROJECTMIND_MODEL_IDLE_UNLOAD_SECONDS", 60 * 60)  # 1 h
MEMORY_PRESSURE_THRESHOLD_MB = 500  # process RSS above which we drop caches


@dataclass
class MaintenanceState:
    last_manifest_refresh: float = 0.0
    last_stale_gc: float = 0.0
    last_db_compaction: float = 0.0
    last_log_truncate: float = 0.0
    last_model_unload_check: float = 0.0
    last_cache_pressure_check: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_manifest_refresh": self.last_manifest_refresh,
            "last_stale_gc": self.last_stale_gc,
            "last_db_compaction": self.last_db_compaction,
            "last_log_truncate": self.last_log_truncate,
            "last_model_unload_check": self.last_model_unload_check,
            "last_cache_pressure_check": self.last_cache_pressure_check,
            "history": self.history[-50:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaintenanceState:
        return cls(
            last_manifest_refresh=data.get("last_manifest_refresh", 0.0),
            last_stale_gc=data.get("last_stale_gc", 0.0),
            last_db_compaction=data.get("last_db_compaction", 0.0),
            last_log_truncate=data.get("last_log_truncate", 0.0),
            last_model_unload_check=data.get("last_model_unload_check", 0.0),
            last_cache_pressure_check=data.get("last_cache_pressure_check", 0.0),
            history=data.get("history", []) or [],
        )


def _state_path() -> Path:
    return config.AI_DIR / STATE_FILENAME


def load_state() -> MaintenanceState:
    p = _state_path()
    if not p.exists():
        return MaintenanceState()
    try:
        return MaintenanceState.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception as e:
        logger.warning(f"maintenance_state unreadable: {e}")
        return MaintenanceState()


def save_state(state: MaintenanceState) -> None:
    try:
        from incremental_indexing import atomic_write

        config.AI_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write(_state_path(), json.dumps(state.to_dict(), indent=2))
    except Exception as e:
        logger.warning(f"could not save maintenance_state: {e}")


def _record(state: MaintenanceState, task: str, ok: bool, detail: str = "") -> None:
    state.history.append({"ts": time.time(), "task": task, "ok": ok, "detail": detail[:240]})


# ---------------------------------------------------------------------------
# Individual maintenance tasks
# ---------------------------------------------------------------------------


def task_refresh_manifest(state: MaintenanceState) -> str:
    """Rebuild the L0 manifest if stale. Cheap; never loads the vector store."""
    from manifest import get_or_build_manifest

    try:
        m = get_or_build_manifest()
        msg = f"manifest fresh ({m.stats.indexed_files} files, " f"built in {m.duration_ms} ms)"
        _record(state, "manifest_refresh", True, msg)
        return msg
    except Exception as e:
        msg = f"manifest refresh failed: {e}"
        logger.warning(msg)
        _record(state, "manifest_refresh", False, str(e))
        return msg


def _vector_db_path() -> Path:
    return config.VECTOR_STORE_DIR / "chroma.sqlite3"


def _vector_db_size_mb() -> float:
    p = _vector_db_path()
    if not p.exists():
        return 0.0
    try:
        return p.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def task_gc_stale_chunks(state: MaintenanceState) -> str:
    """
    Removes embeddings whose `source` no longer exists on disk.

    Will only touch the vector store if it is ALREADY initialised — never
    triggers a cold load just to GC.
    """
    _app_context = _get_app_context()
    if _app_context is None or not getattr(_app_context.vector_store, "_initialized", False):
        msg = "skipped (vector store not loaded)"
        _record(state, "stale_gc", True, msg)
        return msg

    coll = _app_context.vector_store.get_collection()
    if coll is None:
        msg = "skipped (collection unavailable)"
        _record(state, "stale_gc", True, msg)
        return msg

    try:
        all_data = coll.get(include=["metadatas"])
        ids = all_data.get("ids", []) or []
        metas = all_data.get("metadatas", []) or []
        root = config.PROJECT_ROOT
        to_delete: list[str] = []
        for cid, meta in zip(ids, metas, strict=False):
            src = (meta or {}).get("source")
            if not src:
                continue
            p = Path(src)
            if not p.is_absolute():
                p = root / src
            if not p.exists():
                to_delete.append(cid)

        if to_delete:
            coll.delete(ids=to_delete)
            msg = f"pruned {len(to_delete)} orphan chunks"
            logger.info(msg)
        else:
            msg = "no orphan chunks"
        _record(state, "stale_gc", True, msg)
        return msg
    except Exception as e:
        msg = f"stale_gc failed: {e}"
        logger.warning(msg)
        _record(state, "stale_gc", False, str(e))
        return msg


def task_compact_db(state: MaintenanceState) -> str:
    """Runs SQLite VACUUM on the ChromaDB store if it exceeds threshold."""
    p = _vector_db_path()
    if not p.exists():
        msg = "skipped (no DB)"
        _record(state, "db_compaction", True, msg)
        return msg

    size_before = _vector_db_size_mb()
    if size_before < DB_COMPACTION_THRESHOLD_MB:
        msg = f"skipped (DB only {size_before:.1f} MB)"
        _record(state, "db_compaction", True, msg)
        return msg

    try:
        # Best-effort: VACUUM only when nothing else holds the connection.
        conn = sqlite3.connect(str(p), timeout=2.0)
        try:
            conn.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()
        size_after = _vector_db_size_mb()
        msg = f"VACUUM {size_before:.1f}->{size_after:.1f} MB"
        logger.info(msg)
        _record(state, "db_compaction", True, msg)

        if size_after > DB_HARD_LIMIT_MB:
            warn = (
                f"vector store still {size_after:.1f} MB after VACUUM "
                f"(>{DB_HARD_LIMIT_MB} MB). Consider `prune_index(force=True)`."
            )
            logger.warning(warn)
        return msg
    except sqlite3.OperationalError as e:
        msg = f"VACUUM busy ({e})"
        _record(state, "db_compaction", False, msg)
        return msg
    except Exception as e:
        msg = f"compaction failed: {e}"
        logger.warning(msg)
        _record(state, "db_compaction", False, str(e))
        return msg


def task_truncate_log(state: MaintenanceState) -> str:
    """Truncate the active log file when it exceeds threshold (RotatingFileHandler also runs)."""
    log_path = config.LOG_FILE
    if not log_path.exists():
        msg = "skipped (no log)"
        _record(state, "log_truncate", True, msg)
        return msg
    try:
        size_mb = log_path.stat().st_size / (1024 * 1024)
        if size_mb < LOG_TRUNCATE_THRESHOLD_MB:
            msg = f"skipped (log {size_mb:.1f} MB)"
            _record(state, "log_truncate", True, msg)
            return msg
        # Keep last 1 MB tail
        tail_bytes = 1 * 1024 * 1024
        with open(log_path, "rb") as f:
            f.seek(max(0, log_path.stat().st_size - tail_bytes))
            tail = f.read()
        with open(log_path, "wb") as f:
            f.write(b"... [truncated by maintenance daemon]\n")
            f.write(tail)
        msg = f"log truncated ({size_mb:.1f} MB -> ~1 MB)"
        logger.info(msg)
        _record(state, "log_truncate", True, msg)
        return msg
    except Exception as e:
        msg = f"log truncate failed: {e}"
        _record(state, "log_truncate", False, str(e))
        return msg


def _get_app_context() -> Any:
    """Return _app_context from context module, or None. Never raises."""
    try:
        import importlib

        ctx_mod = importlib.import_module("context")
        return getattr(ctx_mod, "_app_context", None)
    except Exception:
        return None


def _process_rss_mb() -> float:
    try:
        import importlib

        resource = importlib.import_module("resource")
        rss_kb = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # On macOS RSS is in bytes; on Linux in KB. Heuristic.
        if rss_kb > 10**9:
            return rss_kb / (1024 * 1024)
        return rss_kb / 1024
    except Exception:
        pass
    try:
        import importlib

        psutil = importlib.import_module("psutil")
        return float(psutil.Process(os.getpid()).memory_info().rss) / (1024 * 1024)
    except Exception:
        return 0.0


def task_unload_idle_model(state: MaintenanceState) -> str:
    """Drop the embedding model if it has been idle for too long."""
    _app_context = _get_app_context()
    if _app_context is None:
        msg = "skipped (no context)"
        _record(state, "model_unload", True, msg)
        return msg
    vs = _app_context.vector_store
    if not getattr(vs, "_initialized", False) or vs.embedding_fn is None:
        msg = "skipped (model not loaded)"
        _record(state, "model_unload", True, msg)
        return msg

    last = float(getattr(vs, "_last_query_at", 0.0) or 0.0)
    if last <= 0:
        msg = "skipped (no query timestamp)"
        _record(state, "model_unload", True, msg)
        return msg
    idle = time.time() - last
    if idle < MODEL_IDLE_UNLOAD_SECONDS:
        msg = f"model active (idle {int(idle)}s)"
        _record(state, "model_unload", True, msg)
        return msg

    try:
        if hasattr(vs, "unload_model"):
            vs.unload_model()
        else:
            vs.embedding_fn = None
            vs.collection = None
            vs._initialized = False
        import gc

        gc.collect()
        msg = f"unloaded model after {int(idle)}s idle"
        logger.info(msg)
        _record(state, "model_unload", True, msg)
        return msg
    except Exception as e:
        msg = f"model unload failed: {e}"
        _record(state, "model_unload", False, str(e))
        return msg


def task_relieve_memory_pressure(state: MaintenanceState) -> str:
    """Drop in-process caches if RSS is above threshold."""
    rss = _process_rss_mb()
    if rss <= 0:
        msg = "skipped (RSS unknown)"
        _record(state, "cache_pressure", True, msg)
        return msg
    if rss < MEMORY_PRESSURE_THRESHOLD_MB:
        msg = f"OK (RSS {rss:.0f} MB)"
        _record(state, "cache_pressure", True, msg)
        return msg

    actions: list[str] = []
    try:
        import config as _cfg
        from config import get_file_cache_stats  # noqa: F401  (force module load)

        if getattr(_cfg, "_file_cache", None):
            _cfg._file_cache = None
            actions.append("file_cache")
    except Exception:
        pass
    try:
        _app_context = _get_app_context()
        if _app_context is not None and hasattr(_app_context.vector_store, "_query_cache"):
            qc = _app_context.vector_store._query_cache
            if hasattr(qc, "clear"):
                qc.clear()
            else:
                qc.cache.clear()
            actions.append("query_cache")
    except Exception:
        pass

    import gc

    gc.collect()
    msg = f"RSS {rss:.0f} MB high; dropped: {', '.join(actions) or 'none'}"
    logger.info(msg)
    _record(state, "cache_pressure", True, msg)
    return msg


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


@dataclass
class _Task:
    name: str
    interval: float
    fn: Callable[[MaintenanceState], str]
    last_attr: str


_TASKS: list[_Task] = [
    _Task(
        "manifest_refresh",
        INTERVAL_MANIFEST_REFRESH,
        task_refresh_manifest,
        "last_manifest_refresh",
    ),
    _Task("stale_gc", INTERVAL_STALE_CHUNK_GC, task_gc_stale_chunks, "last_stale_gc"),
    _Task("db_compaction", INTERVAL_DB_COMPACTION, task_compact_db, "last_db_compaction"),
    _Task("log_truncate", INTERVAL_LOG_TRUNCATE, task_truncate_log, "last_log_truncate"),
    _Task(
        "model_unload",
        INTERVAL_MODEL_UNLOAD_CHECK,
        task_unload_idle_model,
        "last_model_unload_check",
    ),
    _Task(
        "cache_pressure",
        INTERVAL_CACHE_PRESSURE,
        task_relieve_memory_pressure,
        "last_cache_pressure_check",
    ),
]


_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()


def _loop() -> None:
    state = load_state()
    logger.info("Maintenance daemon started")
    while not _stop.is_set():
        try:
            now = time.time()
            for task in _TASKS:
                last = float(getattr(state, task.last_attr, 0.0) or 0.0)
                if (now - last) < task.interval:
                    continue
                try:
                    task.fn(state)
                finally:
                    setattr(state, task.last_attr, time.time())
                    save_state(state)
                if _stop.is_set():
                    break
        except Exception as e:
            logger.warning(f"Maintenance loop iteration failed: {e}")
        _stop.wait(DAEMON_TICK_SECONDS)
    logger.info("Maintenance daemon stopped")


def start_daemon() -> bool:
    """Idempotent. Returns True if a thread was started, False if already running."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="ProjectMindMaintenance", daemon=True)
        _thread.start()
        return True


def stop_daemon(timeout: float = 2.0) -> None:
    global _thread
    with _lock:
        _stop.set()
        if _thread is not None:
            _thread.join(timeout)
            _thread = None


def run_all_now() -> dict[str, str]:
    """Synchronously runs every task once. Used by the `maintenance_run` MCP tool."""
    state = load_state()
    results: dict[str, str] = {}
    for task in _TASKS:
        try:
            results[task.name] = task.fn(state)
        except Exception as e:
            results[task.name] = f"failed: {e}"
        setattr(state, task.last_attr, time.time())
    save_state(state)
    return results


def get_status() -> dict[str, Any]:
    state = load_state()
    db_mb = _vector_db_size_mb()
    log_mb = config.LOG_FILE.stat().st_size / (1024 * 1024) if config.LOG_FILE.exists() else 0.0
    rss_mb = _process_rss_mb()
    now = time.time()
    schedule = []
    for task in _TASKS:
        last = float(getattr(state, task.last_attr, 0.0) or 0.0)
        next_in = max(0.0, task.interval - (now - last)) if last else 0.0
        schedule.append(
            {
                "task": task.name,
                "last_run_age_s": int(now - last) if last else None,
                "next_in_s": int(next_in),
                "interval_s": task.interval,
            }
        )
    return {
        "daemon_alive": _thread is not None and _thread.is_alive(),
        "vector_db_mb": round(db_mb, 1),
        "log_mb": round(log_mb, 1),
        "process_rss_mb": round(rss_mb, 1),
        "schedule": schedule,
        "recent_history": list(reversed(state.history[-15:])),
    }
