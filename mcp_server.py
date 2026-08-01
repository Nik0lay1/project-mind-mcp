import os
import sys
import threading
from pathlib import Path
from time import time

from mcp.server.fastmcp import FastMCP

import config
from config import (
    MCP_SERVER_DIR,
    get_file_cache_stats,
    get_ignored_dirs,
    is_dir_ignored,
    is_mcp_server_dir,
    reconfigure,
    resolve_index_ignore_file,
    validate_path,
)
from context import get_context, reset_context
from exceptions import GitError
from git_utils import CommitInfo, GitRepository
from logger import setup_logger

logger = setup_logger()


def log(message: str) -> None:
    """Backward compatible log function"""
    logger.info(message)


def startup_check() -> None:
    log("=" * 60)
    log("ProjectMind MCP Server Starting...")
    log(f"Project Root (detected): {config.PROJECT_ROOT}")
    log(f"Current Working Directory: {Path.cwd()}")
    log(f"MCP Server Location: {Path(__file__).parent}")
    log("=" * 60)

    try:
        if not config.AI_DIR.exists():
            config.AI_DIR.mkdir(parents=True)
            log(f"Created {config.AI_DIR}")
    except (OSError, PermissionError) as e:
        log(
            f"Warning: Could not create {config.AI_DIR}: {e}. Server will continue if directory exists."
        )

    try:
        git_dir = config.PROJECT_ROOT / ".git"
        if git_dir.exists():
            gitignore_path = config.PROJECT_ROOT / ".gitignore"
            ai_ignored = False
            pycache_ignored = False

            if gitignore_path.exists():
                content = gitignore_path.read_text(encoding="utf-8", errors="replace")
                # Line-based check: a substring test would match ".aider*",
                # "*.aiff" etc. and silently skip adding the real .ai/ entry.
                entries = {ln.strip().rstrip("/") for ln in content.splitlines()}
                if ".ai" in entries:
                    ai_ignored = True
                if "__pycache__" in entries:
                    pycache_ignored = True

                if not ai_ignored or not pycache_ignored:
                    with open(gitignore_path, "a") as f:
                        if not content.endswith("\n") and content:
                            f.write("\n")
                        if not ai_ignored:
                            f.write(".ai/\n")
                            log("Added .ai/ to .gitignore")
                        if not pycache_ignored:
                            f.write("__pycache__/\n")
                            log("Added __pycache__/ to .gitignore")
            else:
                with open(gitignore_path, "w") as f:
                    f.write(".ai/\n__pycache__/\n")
                log("Created .gitignore with .ai/ and __pycache__/")
    except (OSError, PermissionError) as e:
        log(f"Warning: Could not modify .gitignore: {e}")

    try:
        if not config.MEMORY_FILE.exists():
            template = """# Project Memory

## Status
- [ ] Initial Setup

## Tech Stack
- Language: Python
- Framework:

## Recent Decisions
- Project initialized.
"""
            config.MEMORY_FILE.write_text(template)
            log(f"Created {config.MEMORY_FILE}")
    except (OSError, PermissionError) as e:
        log(f"Warning: Could not create {config.MEMORY_FILE}: {e}")


_startup_done = False
_startup_lock = threading.Lock()


def ensure_startup() -> None:
    """Performs startup initialization if not already done."""
    global _startup_done
    if _startup_done:
        return
    with _startup_lock:
        if _startup_done:
            return
        startup_check()
        _startup_done = True


mcp = FastMCP("ProjectMind")


def _check_model_loaded() -> str | None:
    """Ensures the embedding model is loaded, initializing it if needed.

    Must be called *after* _check_index_ready() (which confirms the SQLite DB exists).
    """
    from vector_store_manager import vector_stack_available

    if not vector_stack_available():
        return None  # BM25-only mode — no model to load
    try:
        vs = get_context().vector_store
        if not vs.is_loaded():
            log("Embedding model not loaded yet. Initializing now...")
            if not vs.initialize():
                return "⚠️ Failed to initialize the embedding model vector store."
    except Exception as e:
        return f"⚠️ Error initializing embedding model: {e}"
    return None


def _check_index_ready() -> str | None:
    """Returns an error message string if the index is not ready, or None if OK."""
    import sqlite3

    from vector_store_manager import vector_stack_available

    if not vector_stack_available():
        # BM25-only mode: the keyword corpus is the index
        if config.BM25_INDEX_PATH.exists():
            return None
        return (
            "⚠️ INDEX NOT BUILT (BM25-only mode). Run `index_codebase()` first "
            "to build the keyword index, then retry this tool."
        )

    vector_db_path = config.VECTOR_STORE_DIR / "chroma.sqlite3"
    if not vector_db_path.exists():
        return (
            "⚠️ INDEX NOT BUILT. You must run `index_codebase()` first before using search tools.\n"
            "Steps:\n1. Call `index_codebase()` to build the index\n2. Then retry this tool."
        )
    try:
        conn = sqlite3.connect(str(vector_db_path))
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        if count == 0:
            return (
                "⚠️ INDEX IS EMPTY. The vector store exists but has no data.\n"
                "Steps:\n1. Call `index_codebase(force=True)` to rebuild the index\n2. Then retry this tool."
            )
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            return (
                "⚠️ INDEX IS BUSY. The vector store is currently being written "
                "(indexing in progress). Check `get_index_progress()` and retry shortly."
            )
        return (
            "⚠️ INDEX IS UNREADABLE. The vector store may be corrupted.\n"
            "Steps:\n1. Call `index_codebase(force=True)` to rebuild the index\n2. Then retry this tool."
        )
    except Exception:
        return (
            "⚠️ INDEX IS UNREADABLE. The vector store may be corrupted.\n"
            "Steps:\n1. Call `index_codebase(force=True)` to rebuild the index\n2. Then retry this tool."
        )
    return None


def _source_rel_path(meta: dict) -> str | None:
    """
    Returns the chunk's source file as a project-relative posix path.

    Chunk metadata stores the path under "source" (as an absolute OS path at
    index time); the import graph and manifest use relative posix paths, so
    normalize before any graph lookup or cross-tier comparison.
    """
    src = meta.get("source") or meta.get("file_path")
    if not src:
        return None
    try:
        p = Path(str(src))
        if p.is_absolute():
            return p.resolve().relative_to(config.PROJECT_ROOT.resolve()).as_posix()
        return p.as_posix()
    except Exception:
        return str(src).replace("\\", "/")


def _stop_background_indexing(timeout_seconds: float = 15.0) -> str | None:
    """
    Cancels a running background index job and waits (bounded) for it to stop.

    Must be called before reconfigure(): a job started for project A would
    otherwise keep writing A's chunks into project B's freshly switched
    vector store.

    Returns:
        A human-readable note when a job was cancelled, else None.
    """
    try:
        from background_indexer import BackgroundIndexer

        if not BackgroundIndexer.is_running():
            return None
        BackgroundIndexer.cancel()
        thread = BackgroundIndexer._thread
        if thread is not None:
            thread.join(timeout=timeout_seconds)
        if BackgroundIndexer.is_running():
            return (
                "⚠️ A background indexing job for the previous project was cancelled "
                "but is still winding down; re-run `index_codebase()` for this project."
            )
        return "Background indexing job for the previous project was cancelled."
    except Exception:
        return None


def _reset_indexing_state_for_new_root() -> None:
    """
    Forget the previous project's indexing job state.

    The in-memory progress mirror is process-wide, so without this a freshly
    selected project reported the *previous* project's run — including its
    failures — as if it were its own.
    """
    try:
        from background_indexer import BackgroundIndexer

        BackgroundIndexer.reset_progress()
    except Exception:
        pass
    try:
        from symbol_graph import invalidate_symbol_graph_cache

        invalidate_symbol_graph_cache()
    except Exception:
        pass


@mcp.tool()
def set_project_root(path: str) -> str:
    """
    Sets the target project root directory.
    Call this FIRST when working with a project that is not auto-detected.

    Args:
        path: Absolute path to the project root directory.

    Returns:
        Confirmation message with the new project root.
    """
    global _startup_done
    target = Path(path).resolve()
    if not target.exists():
        return f"Error: Path does not exist: {path}"
    if not target.is_dir():
        return f"Error: Path is not a directory: {path}"

    cancel_note = _stop_background_indexing()

    reconfigure(target)
    reset_context()
    _reset_indexing_state_for_new_root()
    _startup_done = False
    ensure_startup()
    log(f"Project root changed to: {config.PROJECT_ROOT}")

    msg = f"Project root set to: {config.PROJECT_ROOT}"
    if cancel_note:
        msg += f"\n{cancel_note}"
    if is_mcp_server_dir(target):
        msg += (
            "\n\n⚠️ Warning: This path is the ProjectMind MCP server's OWN directory. "
            "If you intended to index a different project, call `set_project_root` "
            "again with the absolute path to that project."
        )
    return msg


def _count_index_chunks() -> int | None:
    """Returns chunk count or None if the index is missing/unreadable."""
    import sqlite3

    from vector_store_manager import vector_stack_available

    if not vector_stack_available():
        # BM25-only mode: count keyword-corpus documents instead
        try:
            import json as _json

            if not config.BM25_INDEX_PATH.exists():
                return None
            data = _json.loads(config.BM25_INDEX_PATH.read_text(encoding="utf-8"))
            return len(data.get("ids", []))
        except Exception:
            return None

    vector_db_path = config.VECTOR_STORE_DIR / "chroma.sqlite3"
    if not vector_db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(vector_db_path))
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        return int(count)
    except Exception:
        return None


def _server_version() -> str:
    try:
        from importlib.metadata import version

        try:
            return version("projectmind-mcp")
        except Exception:
            return version("projectmind")
    except Exception:
        try:
            import tomllib

            data = tomllib.loads((MCP_SERVER_DIR / "pyproject.toml").read_text(encoding="utf-8"))
            return str(data.get("project", {}).get("version", "unknown"))
        except Exception:
            return "unknown"


def _symbol_graph_status() -> str:
    """One-line symbol-graph state for health output (never builds the graph)."""
    try:
        from symbol_graph import peek_symbol_graph

        graph = peek_symbol_graph()
    except Exception as exc:
        return f"unavailable ({exc})"
    if graph is None:
        return "not built yet — run `index_codebase()`"
    return graph.status_line()


def _background_index_warnings() -> list[str]:
    """Warnings recorded by the last background indexing run, if any."""
    try:
        from background_indexer import BackgroundIndexer

        prog = BackgroundIndexer.get_progress()
    except Exception:
        return []
    warnings = list(prog.get("warnings") or [])
    if prog.get("status") == "error" and prog.get("last_error"):
        warnings.insert(0, str(prog["last_error"]))
    return warnings


@mcp.tool()
def health() -> str:
    """
    Lightweight readiness check for the ProjectMind MCP server.

    Reports: server version, active project root, memory file status, index status,
    and whether the configured project is the MCP server's own directory.
    Does NOT initialize the vector store or embeddings model.

    Returns:
        Markdown-formatted health report.
    """
    ensure_startup()

    version_str = _server_version()
    root = config.PROJECT_ROOT
    memory_exists = config.MEMORY_FILE.exists()
    chunks = _count_index_chunks()
    own_dir = is_mcp_server_dir(root)

    parts = [
        "# ProjectMind — Health",
        f"- **Version**: {version_str}",
        f"- **MCP server dir**: `{MCP_SERVER_DIR}`",
        f"- **Project root**: `{root}`",
        f"- **Memory file**: {'found' if memory_exists else 'missing'} (`{config.MEMORY_FILE}`)",
        f"- **Vector index**: {('empty' if chunks == 0 else f'{chunks} chunks') if chunks is not None else 'not initialized'}",
        f"- **Index ignore file**: `{resolve_index_ignore_file()}`",
        f"- **Symbol graph**: {_symbol_graph_status()}",
    ]
    bg_warnings = _background_index_warnings()
    if bg_warnings:
        parts.append("\n⚠️ **Last indexing run reported problems:**")
        parts.extend(f"- {w}" for w in bg_warnings)
    if own_dir:
        parts.append(
            "\n⚠️ The project root points at the MCP server's own directory. "
            "If that was not intended, call `set_project_root(<absolute path>)`."
        )
    if chunks is None:
        parts.append("\nℹ️ Run `index_codebase()` to build the semantic index.")
    return "\n".join(parts)


@mcp.tool()
def session_init(project_path: str = "") -> str:
    """
    Single-call session bootstrap. Call this FIRST in every AI session.

    Performs:
      1. Sets project root (if `project_path` is provided).
      2. Warns if the chosen root is the MCP server's own directory.
      3. Reads `.ai/memory.md` (only the index/headings — full text on demand).
      4. Refreshes the lightweight L0 manifest.
      5. Reports vector-index status (without loading it).
      6. Starts the self-healing maintenance daemon.

    The heavy vector store / embedding model is NEVER loaded here — it is
    lazily loaded only when a semantic query needs it. This keeps
    `session_init` well under the MCP 30s tool timeout even on multi-GB
    repositories.

    Args:
        project_path: Absolute path to the target project. Leave empty to use
                      auto-detection (CWD + project markers).

    Returns:
        Consolidated Markdown brief summarising project root, memory, and index state.
    """
    global _startup_done

    sections: list[str] = ["# ProjectMind — Session Init"]

    if project_path and project_path.strip():
        target = Path(project_path).expanduser().resolve()
        if not target.exists():
            return f"Error: project_path does not exist: {project_path}"
        if not target.is_dir():
            return f"Error: project_path is not a directory: {project_path}"
        if target != config.PROJECT_ROOT:
            cancel_note = _stop_background_indexing()
            if cancel_note:
                sections.append(cancel_note)
        reconfigure(target)
        reset_context()
        _reset_indexing_state_for_new_root()
        _startup_done = False
        ensure_startup()
        sections.append(f"**Project root** set to: `{config.PROJECT_ROOT}`")
    else:
        ensure_startup()
        sections.append(
            f"**Project root** (auto-detected): `{config.PROJECT_ROOT}`\n"
            "_Tip: pass `project_path` explicitly to avoid ambiguous auto-detection._"
        )
    if is_mcp_server_dir(config.PROJECT_ROOT):
        sections.append(
            "⚠️ The active project root is the MCP server's OWN directory "
            f"(`{MCP_SERVER_DIR}`). If this was not intentional, call "
            "`session_init(project_path='<absolute path>')` again."
        )

    chunks = _count_index_chunks()
    if chunks is None:
        # Index DB doesn't exist yet — auto-start background indexing
        try:
            from background_indexer import BackgroundIndexer

            if not BackgroundIndexer.is_running():
                BackgroundIndexer.start(force=False)
                sections.append(
                    "**Index status**: 🔄 Not built yet — background indexing started automatically.\n"
                    "_Call `get_index_progress()` to track progress._"
                )
            else:
                prog = BackgroundIndexer.get_progress()
                done = prog.get("files_done", 0)
                total = prog.get("files_total", 0)
                sections.append(
                    f"**Index status**: ⏳ Indexing in progress ({done}/{total} files).\n"
                    "_Call `get_index_progress()` to track progress._"
                )
        except Exception as _e:
            sections.append(
                f"**Index status**: not initialized. Run `index_codebase()` to build it. (auto-start failed: {_e})"
            )
    elif chunks == 0:
        # DB exists but is empty — auto-start if not already running
        try:
            from background_indexer import BackgroundIndexer

            if not BackgroundIndexer.is_running():
                BackgroundIndexer.start(force=True)
                sections.append(
                    "**Index status**: 🔄 Index empty — background re-indexing started automatically.\n"
                    "_Call `get_index_progress()` to track progress._"
                )
            else:
                prog = BackgroundIndexer.get_progress()
                done = prog.get("files_done", 0)
                total = prog.get("files_total", 0)
                sections.append(
                    f"**Index status**: ⏳ Re-indexing in progress ({done}/{total} files).\n"
                    "_Call `get_index_progress()` to track progress._"
                )
        except Exception as _e:
            sections.append(
                f"**Index status**: empty. Run `index_codebase(force=True)` to rebuild. (auto-start failed: {_e})"
            )
    else:
        sections.append(f"**Index status**: {chunks} chunks (loaded lazily).")

    # Manifest (L0) — load existing from disk, do not build synchronously.
    try:
        from manifest import load_manifest, quick_overview_from_manifest

        m = load_manifest()
        if m:
            sections.append(
                "## Manifest\n"
                f"- {m.stats.indexed_files} indexable files / {m.stats.total_files} total\n"
                f"- {m.stats.total_size_bytes / (1024 * 1024):.1f} MB, "
                f"refreshed in {m.duration_ms} ms\n"
                f"- top modules: " + ", ".join(f"`{mod.name}/`" for mod in m.modules[:5])
            )
            sections.append("### Quick overview\n" + quick_overview_from_manifest(m))
        else:
            sections.append(
                "## Manifest\n" "_Manifest not built yet. It is being built in the background..._"
            )
    except Exception as e:
        sections.append(f"## Manifest\n_skipped: {e}_")

    # Memory: only headings + first lines so we don't dump everything up front.
    try:
        if config.MEMORY_FILE.exists():
            sections.append("## Memory (index)\n" + _memory_index_markdown())
        else:
            sections.append("## Memory\n_No memory file yet. It will be created on first update._")
    except Exception as e:
        sections.append(f"## Memory\n_Error reading memory: {e}_")

    # Self-healing daemon
    try:
        from maintenance import start_daemon

        started = start_daemon()
        sections.append(
            "## Maintenance\n" + ("daemon started" if started else "daemon already running")
        )
    except Exception as e:
        sections.append(f"## Maintenance\n_Could not start daemon: {e}_")

    sections.append(
        "_Tip: use `query(text, intent='overview'|'lookup'|'semantic'|'deep')` "
        "for tier-aware search; `read_memory_section(name)` for targeted memory access._"
    )
    return "\n\n".join(sections)


def _memory_index_markdown() -> str:
    """Returns just the headings of memory.md plus a 1-line preview each."""
    try:
        text = config.MEMORY_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"_unreadable: {e}_"
    lines = text.split("\n")
    out: list[str] = []
    pending: tuple[str, int] | None = None  # (heading, line_index)
    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            if pending is not None:
                out.append(f"- {pending[0]}")
            pending = (ln.strip(), i)
    if pending is not None:
        out.append(f"- {pending[0]}")
    if not out:
        return "_empty_"
    out.append(
        f"\n_{len(lines)} lines total. Use `read_memory_section(name)` to expand a section._"
    )
    return "\n".join(out)


def load_index_ignore_patterns() -> set[str]:
    ignore_file = resolve_index_ignore_file()
    if not ignore_file.exists():
        return set()

    try:
        patterns = set()
        with open(ignore_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.add(line)
        return patterns
    except Exception as e:
        log(f"Error reading .indexignore at {ignore_file}: {e}")
        return set()


def _read_memory_sections() -> dict[str, str]:
    """Reads memory.md and returns sections as a dict. No vector store needed."""
    if not config.MEMORY_FILE.exists():
        return {}
    try:
        content = config.MEMORY_FILE.read_text(encoding="utf-8")
    except Exception:
        return {}

    sections: dict[str, str] = {}
    current_section = ""
    current_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("## "):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = line[3:].strip()
            current_lines = []
        elif current_section:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return sections


def _search_memory_for(keyword: str) -> list[str]:
    """Searches memory.md for lines mentioning a keyword. No vector store needed."""
    if not config.MEMORY_FILE.exists():
        return []
    try:
        content = config.MEMORY_FILE.read_text(encoding="utf-8")
    except Exception:
        return []

    keyword_lower = keyword.lower()
    matches = []
    for line in content.split("\n"):
        if keyword_lower in line.lower() and line.strip():
            matches.append(line.strip())
    return matches


def _get_git_repo_safe() -> GitRepository | None:
    """Returns GitRepository or None if not a git repo. Never raises."""
    try:
        repo = GitRepository()
        repo._get_repo()
        return repo
    except Exception:
        return None


@mcp.tool()
def get_project_overview() -> str:
    """
    Returns a fast, lightweight overview of the project.
    Does NOT require vector store or indexing.
    Use this FIRST to understand the project before diving deeper.

    Returns:
        Project name, tech stack, root directories, file type counts, config files.
    """
    ensure_startup()
    try:
        # Manifest first — closes ~80% of "what is this project" questions
        # in <50 ms without touching the vector store.
        try:
            from manifest import load_manifest, quick_overview_from_manifest

            m = load_manifest()
            if m:
                quick = quick_overview_from_manifest(m)
            else:
                quick = "_Manifest not built yet. It is being built in the background..._"
        except Exception:
            quick = ""

        root = config.PROJECT_ROOT
        overview = [f"# PROJECT OVERVIEW: {root.name}\n"]
        overview.append(f"**Root**: `{root}`")
        if quick:
            overview.append("\n" + quick)

        git_repo = _get_git_repo_safe()
        if git_repo:
            branch = git_repo.get_active_branch()
            total = git_repo.get_total_commit_count()
            overview.append(f"**Git**: branch `{branch}`, {total}+ commits")

        config_files = []
        tech_hints = []
        for name, label in [
            ("pyproject.toml", "Python (pyproject)"),
            ("setup.py", "Python (setup.py)"),
            ("requirements.txt", "Python (requirements)"),
            ("package.json", "Node.js"),
            ("Cargo.toml", "Rust"),
            ("go.mod", "Go"),
            ("pom.xml", "Java (Maven)"),
            ("build.gradle", "Java (Gradle)"),
            ("Gemfile", "Ruby"),
            ("composer.json", "PHP"),
            ("Dockerfile", "Docker"),
            ("docker-compose.yml", "Docker Compose"),
            (".gitignore", "Git"),
        ]:
            if (root / name).exists():
                config_files.append(name)
                if label not in ("Git",):
                    tech_hints.append(label)

        if tech_hints:
            overview.append(f"**Tech**: {', '.join(tech_hints)}")

        memory_sections = _read_memory_sections()
        if "Tech Stack" in memory_sections and memory_sections["Tech Stack"]:
            overview.append("\n## Tech Stack (from memory)")
            for line in memory_sections["Tech Stack"].split("\n")[:10]:
                if line.strip():
                    overview.append(line)

        if "Status" in memory_sections and memory_sections["Status"]:
            overview.append("\n## Status (from memory)")
            for line in memory_sections["Status"].split("\n")[:10]:
                if line.strip():
                    overview.append(line)

        overview.append("\n## Root Directories")
        try:
            dirs = []
            files_at_root = 0
            for entry in sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
                if entry.is_dir():
                    if not is_dir_ignored(entry.name) and not entry.name.startswith("."):
                        dirs.append(entry.name)
                else:
                    files_at_root += 1
            for d in dirs:
                overview.append(f"- `{d}/`")
            if files_at_root:
                overview.append(f"- ... and {files_at_root} files at root level")
        except PermissionError:
            overview.append("- (permission denied)")

        file_types: dict[str, int] = {}
        total_files = 0
        for _root_path, dir_names, files in os.walk(root):
            dir_names[:] = [d for d in dir_names if not is_dir_ignored(d)]
            for file in files:
                total_files += 1
                ext = Path(file).suffix.lower()
                if ext in config.INDEXABLE_EXTENSIONS:
                    file_types[ext] = file_types.get(ext, 0) + 1

        overview.append(f"\n## File Stats (total: {total_files})")
        for ext, count in sorted(file_types.items(), key=lambda x: x[1], reverse=True)[:10]:
            overview.append(f"- `{ext}`: {count}")

        if config_files:
            overview.append("\n## Config Files")
            for cfg in config_files:
                overview.append(f"- `{cfg}`")

        if git_repo:
            try:
                commits = git_repo.get_commits(max_count=5, since_days=7)
                if commits:
                    overview.append("\n## Recent Activity (last 7 days)")
                    for c in commits:
                        overview.append(f"- {c.date_short} [{c.short_hash}]: {c.first_line}")
            except GitError:
                pass

        recent_decisions = memory_sections.get("Recent Decisions", "")
        if recent_decisions:
            decision_lines = [ln for ln in recent_decisions.split("\n") if ln.strip()][:5]
            if decision_lines:
                overview.append("\n## Recent Decisions (from memory)")
                for line in decision_lines:
                    overview.append(line)

        overview.append(
            "\n*Use `explore_directory(path)` to drill into specific directories, "
            "`get_file_summary(path)` for file details.*"
        )

        return "\n".join(overview)
    except Exception as e:
        return f"Error generating overview: {e}"


@mcp.tool()
def explore_directory(path: str = ".", depth: int = 1, max_items: int = 100) -> str:
    """
    Lists files and subdirectories at the given path. Very fast, no indexing needed.
    Use this to navigate the project tree level by level.

    Args:
        path: Directory path relative to project root (use "." for root).
        depth: How many levels deep to show (1-3). Default 1.
        max_items: Maximum items to return. Default 100.

    Returns:
        Tree-like listing of the directory contents.
    """
    ensure_startup()

    if depth < 1:
        depth = 1
    if depth > 3:
        depth = 3
    if max_items < 1:
        max_items = 1
    if max_items > 500:
        max_items = 500

    try:
        target = validate_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not target.exists():
        return f"Path not found: {path}"
    if not target.is_dir():
        return f"Not a directory: {path}"

    git_repo = _get_git_repo_safe()
    recently_changed: dict[str, CommitInfo] = {}
    if git_repo:
        try:
            recently_changed = git_repo.get_recently_changed_files(days=14)
        except Exception:
            pass

    try:
        rel = target.relative_to(config.PROJECT_ROOT)
        header = str(rel) if str(rel) != "." else config.PROJECT_ROOT.name
    except ValueError:
        header = str(target)

    lines = [f"# {header}/\n"]
    count = [0]

    def _format_git_hint(entry_path: Path) -> str:
        if not recently_changed:
            return ""
        try:
            rel_path = str(entry_path.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
        except ValueError:
            return ""
        if rel_path in recently_changed:
            ci = recently_changed[rel_path]
            return f"  [changed {ci.date_short}: {ci.first_line[:40]}]"
        return ""

    def _walk(dir_path: Path, prefix: str, current_depth: int) -> None:
        if count[0] >= max_items:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            lines.append(f"{prefix}(permission denied)")
            return

        dirs_list = []
        files_list = []
        for entry in entries:
            if entry.is_dir():
                if not is_dir_ignored(entry.name) and not entry.name.startswith("."):
                    dirs_list.append(entry)
            else:
                files_list.append(entry)

        for d in dirs_list:
            if count[0] >= max_items:
                lines.append(f"{prefix}... (truncated)")
                return
            lines.append(f"{prefix}{d.name}/")
            count[0] += 1
            if current_depth < depth:
                _walk(d, prefix + "  ", current_depth + 1)

        for f in files_list:
            if count[0] >= max_items:
                lines.append(f"{prefix}... (truncated)")
                return
            try:
                size = f.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size / 1024 / 1024:.1f}MB"
            except OSError:
                size_str = "?"
            git_hint = _format_git_hint(f)
            lines.append(f"{prefix}{f.name}  ({size_str}){git_hint}")
            count[0] += 1

    _walk(target, "", 1)

    if count[0] == 0:
        lines.append("(empty directory)")

    _header_norm = header.replace("\\", "/")
    memory_mentions = _search_memory_for(
        _header_norm.split("/")[-1] if "/" in _header_norm else _header_norm
    )
    if memory_mentions:
        lines.append("\n## Notes from memory")
        for mention in memory_mentions[:5]:
            lines.append(f"- {mention}")

    return "\n".join(lines)


@mcp.tool()
def get_file_summary(path: str, max_lines: int = 50) -> str:
    """
    Returns a lightweight summary of a file: size, imports, top-level definitions,
    and the first N lines. Does NOT require indexing.

    Args:
        path: File path relative to project root.
        max_lines: Max lines of content to include (default 50).

    Returns:
        File metadata, structure, and preview.
    """
    ensure_startup()

    if max_lines < 0:
        max_lines = 0
    if max_lines > 500:
        max_lines = 500

    try:
        target = validate_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not target.exists():
        return f"File not found: {path}"
    if not target.is_file():
        return f"Not a file: {path}"

    try:
        stat = target.stat()
        size_kb = stat.st_size / 1024
    except OSError:
        size_kb = 0

    result = [f"# {target.name}\n"]
    result.append(f"**Path**: `{target.relative_to(config.PROJECT_ROOT)}`")
    result.append(f"**Size**: {size_kb:.1f} KB")
    result.append(f"**Extension**: `{target.suffix}`")

    git_repo = _get_git_repo_safe()
    if git_repo:
        try:
            rel_path = str(target.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
            file_commits = git_repo.get_file_commits(rel_path, max_count=5)
            if file_commits:
                result.append(
                    f"**Last changed**: {file_commits[0].date_str} by {file_commits[0].author}"
                )
                result.append(f"**Total changes**: {len(file_commits)}+ commits")
        except Exception:
            pass

    if target.suffix.lower() in config.BINARY_EXTENSIONS:
        result.append("\n(binary file — no preview)")
        return "\n".join(result)

    try:
        content = config.safe_read_text(target)
    except (UnicodeDecodeError, OSError) as e:
        result.append(f"\n(cannot read: {e})")
        return "\n".join(result)

    lines = content.split("\n")
    result.append(f"**Lines**: {len(lines)}")

    ext = target.suffix.lower()
    if ext == ".py":
        imports = []
        classes = []
        functions = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                imports.append(stripped)
            elif stripped.startswith("class ") and ":" in stripped:
                classes.append(stripped.split("(")[0].split(":")[0].replace("class ", "").strip())
            elif stripped.startswith("def ") and ":" in stripped:
                functions.append(stripped.split("(")[0].replace("def ", "").strip())

        if imports:
            result.append(f"\n**Imports** ({len(imports)}):")
            for imp in imports[:15]:
                result.append(f"  - `{imp}`")
            if len(imports) > 15:
                result.append(f"  - ... ({len(imports) - 15} more)")
        if classes:
            result.append(f"\n**Classes**: {', '.join(f'`{c}`' for c in classes)}")
        if functions:
            result.append(f"\n**Functions** ({len(functions)}):")
            for fn in functions[:20]:
                result.append(f"  - `{fn}`")
            if len(functions) > 20:
                result.append(f"  - ... ({len(functions) - 20} more)")

    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        imports = []
        exports = []
        functions = []
        for line in lines:
            stripped = line.strip()
            if (
                stripped.startswith("import ")
                or stripped.startswith("const ")
                and "require(" in stripped
            ):
                imports.append(stripped[:100])
            elif stripped.startswith("export "):
                exports.append(stripped[:100])
            elif "function " in stripped and (
                "function " == stripped[:9] or "async function" in stripped
            ):
                functions.append(stripped[:80])

        if imports:
            result.append(f"\n**Imports** ({len(imports)}):")
            for imp in imports[:10]:
                result.append(f"  - `{imp}`")
        if exports:
            result.append(f"\n**Exports** ({len(exports)}):")
            for exp in exports[:10]:
                result.append(f"  - `{exp}`")

    if git_repo:
        try:
            rel_path = str(target.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
            file_commits = git_repo.get_file_commits(rel_path, max_count=5)
            if file_commits:
                result.append("\n## Git History")
                for c in file_commits:
                    result.append(f"- {c.date_str} [{c.short_hash}] {c.first_line} ({c.author})")
        except Exception:
            pass

    memory_mentions = _search_memory_for(target.name)
    if memory_mentions:
        result.append("\n## Notes from memory")
        for mention in memory_mentions[:5]:
            result.append(f"- {mention}")

    if max_lines > 0:
        preview_lines = lines[:max_lines]
        result.append(f"\n## Preview (first {min(max_lines, len(lines))} lines)")
        result.append("```" + ext.lstrip("."))
        result.append("\n".join(preview_lines))
        result.append("```")
        if len(lines) > max_lines:
            result.append(f"\n... ({len(lines) - max_lines} more lines)")

    return "\n".join(result)


@mcp.tool()
def detect_project_conventions() -> str:
    """
    Auto-detects project conventions: naming style, test patterns, frameworks,
    linting/formatting tools, error handling, logging, architecture.
    Does NOT require indexing. Results can be saved to memory for future reference.

    Returns:
        Formatted conventions report.
    """
    ensure_startup()
    try:
        from code_intelligence import detect_conventions

        return detect_conventions(config.PROJECT_ROOT)
    except Exception as e:
        return f"Error detecting conventions: {e}"


@mcp.tool()
def get_context_brief(
    task: str,
    budget_tokens: int = 4000,
    hint_files: list[str] | None = None,
    hint_symbols: list[str] | None = None,
) -> str:
    """
    Deterministic CONTEXT BRIEF for a coding task — the scout pyramid, layer 0.
    Combines hybrid retrieval (vector/BM25), one-hop import-graph expansion,
    file skeletons from the manifest and git recency into a ranked, budget-
    packed markdown brief. Zero LLM calls, typically <500ms.

    Args:
        task: The coding task in natural language.
        budget_tokens: Approximate token budget for the brief (default 4000).
        hint_files: File paths explicitly mentioned in the task (client-extracted).
        hint_symbols: Identifiers/symbols mentioned in the task (client-extracted).

    Returns:
        Markdown brief: ranked files with symbols, import relations and
        top excerpts. Empty string when nothing relevant is indexed yet.
    """
    ensure_startup()
    try:
        from context_brief import build_context_brief

        return build_context_brief(task, budget_tokens, hint_files, hint_symbols)
    except Exception as e:
        return f"Error building context brief: {e}"


@mcp.tool()
def get_file_relations(path: str) -> str:
    """
    Shows import relationships for a file: what it imports, what imports it,
    and related test files. Built from static analysis (no indexing needed).

    Args:
        path: File path relative to project root.

    Returns:
        Import graph and impact assessment for the file.
    """
    ensure_startup()
    try:
        target = validate_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not target.exists():
        return f"File not found: {path}"
    if not target.is_file():
        return f"Not a file: {path}"

    try:
        from code_intelligence import get_file_relations as _get_relations

        rel_path = str(target.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
        return _get_relations(rel_path, config.PROJECT_ROOT)
    except Exception as e:
        return f"Error analyzing relations: {e}"


@mcp.tool()
def find_todos(tag: str | None = None) -> str:
    """
    Scans the codebase for TODO, FIXME, HACK, BUG, XXX comments.
    Does NOT require indexing.

    Args:
        tag: Optional filter by tag (e.g. "TODO", "FIXME"). None = all tags.

    Returns:
        Summary and list of all TODO-like comments with file locations.
    """
    ensure_startup()
    try:
        from code_intelligence import extract_todos

        return extract_todos(config.PROJECT_ROOT, tag_filter=tag)
    except Exception as e:
        return f"Error scanning TODOs: {e}"


@mcp.tool()
def check_dependencies() -> str:
    """
    Analyzes project dependencies: versions, pinning strategy, duplicates, lock files.
    Supports Python (pyproject.toml, requirements.txt), JS (package.json),
    Go (go.mod), and Rust (Cargo.toml). No indexing required.

    Returns:
        Dependency health report with version analysis.
    """
    ensure_startup()
    try:
        from code_intelligence import check_dependencies as _check_deps

        return _check_deps(config.PROJECT_ROOT)
    except Exception as e:
        return f"Error checking dependencies: {e}"


@mcp.tool()
def analyze_change_impact(path: str) -> str:
    """
    Predicts what breaks if you change a file. Uses import graph to find
    direct dependents, transitive impact, and related tests to run.

    Args:
        path: File path relative to project root.

    Returns:
        Impact analysis with risk assessment and test recommendations.
    """
    ensure_startup()
    try:
        target = validate_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not target.exists():
        return f"File not found: {path}"
    if not target.is_file():
        return f"Not a file: {path}"

    try:
        from code_intelligence import analyze_change_impact as _analyze_impact

        rel_path = str(target.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
        return _analyze_impact(rel_path, config.PROJECT_ROOT)
    except Exception as e:
        return f"Error analyzing impact: {e}"


@mcp.tool()
def get_dependencies_with_depth(
    file_path: str, depth: int = 2, direction: str = "downstream"
) -> str:
    """
    Gets file dependencies up to specified depth in the import graph.

    Args:
        file_path: File path relative to project root
        depth: How many levels deep to traverse (1-5, default 2)
        direction: "downstream" (what it imports) or "upstream" (what imports it)

    Returns:
        List of files with their distance from the target file
    """
    ensure_startup()

    if depth < 1 or depth > 5:
        return "Error: depth must be between 1 and 5"

    if direction not in ("downstream", "upstream"):
        return "Error: direction must be 'downstream' or 'upstream'"

    try:
        target = validate_path(file_path)
    except ValueError as e:
        return f"Error: {e}"

    if not target.exists():
        return f"File not found: {file_path}"

    try:
        from code_intelligence import build_import_graph
        from code_intelligence import get_dependencies_with_depth as _get_deps

        rel_path = str(target.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
        graph = build_import_graph(config.PROJECT_ROOT)

        if rel_path not in graph and direction == "downstream":
            return f"File not in import graph: {file_path}"

        deps = _get_deps(rel_path, graph, depth, direction)

        if not deps:
            dir_label = "imports" if direction == "downstream" else "importers"
            return f"No {dir_label} found within depth {depth}"

        lines = [f"# DEPENDENCIES ({direction.upper()}) - depth {depth}\n"]
        lines.append(f"Starting from: `{rel_path}`\n")

        # Group by distance
        by_distance: dict[int, list[str]] = {}
        for file, dist in deps.items():
            if dist not in by_distance:
                by_distance[dist] = []
            by_distance[dist].append(file)

        for dist in sorted(by_distance.keys()):
            files = sorted(by_distance[dist])
            lines.append(f"## Level {dist} ({len(files)} files)")
            for f in files:
                lines.append(f"- `{f}`")
            lines.append("")

        lines.append(f"**Total**: {len(deps)} files")
        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def find_dependency_path(from_file: str, to_file: str, max_depth: int = 10) -> str:
    """
    Finds the shortest dependency path between two files.

    Args:
        from_file: Source file path (relative to project root)
        to_file: Target file path (relative to project root)
        max_depth: Maximum search depth (default 10)

    Returns:
        Dependency chain from source to target, or message if no path found
    """
    ensure_startup()

    if max_depth < 1 or max_depth > 20:
        return "Error: max_depth must be between 1 and 20"

    try:
        source = validate_path(from_file)
        target = validate_path(to_file)
    except ValueError as e:
        return f"Error: {e}"

    if not source.exists():
        return f"Source file not found: {from_file}"
    if not target.exists():
        return f"Target file not found: {to_file}"

    try:
        from code_intelligence import build_import_graph
        from code_intelligence import find_dependency_path as _find_path

        source_rel = str(source.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
        target_rel = str(target.relative_to(config.PROJECT_ROOT)).replace("\\", "/")

        graph = build_import_graph(config.PROJECT_ROOT)
        path = _find_path(source_rel, target_rel, graph, max_depth)

        if path is None:
            return f"No dependency path found from `{from_file}` to `{to_file}` within depth {max_depth}"

        if len(path) == 1:
            return f"`{from_file}` and `{to_file}` are the same file"

        lines = ["# DEPENDENCY PATH\n"]
        lines.append(f"From: `{from_file}`")
        lines.append(f"To: `{to_file}`")
        lines.append(f"Distance: {len(path) - 1} steps\n")
        lines.append("## Path")

        for i, file in enumerate(path):
            if i < len(path) - 1:
                lines.append(f"{i + 1}. `{file}` →")
            else:
                lines.append(f"{i + 1}. `{file}`")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_module_cluster(
    file_path: str, similarity_threshold: float = 0.3, max_cluster_size: int = 20
) -> str:
    """
    Finds files closely related to the target based on shared dependencies.
    Uses Jaccard similarity to identify modules that work together.

    Args:
        file_path: File path relative to project root
        similarity_threshold: Minimum similarity score 0.0-1.0 (default 0.3)
        max_cluster_size: Maximum number of related files (default 20)

    Returns:
        List of related files sorted by similarity score
    """
    ensure_startup()

    if not 0.0 <= similarity_threshold <= 1.0:
        return "Error: similarity_threshold must be between 0.0 and 1.0"

    if max_cluster_size < 1 or max_cluster_size > 100:
        return "Error: max_cluster_size must be between 1 and 100"

    try:
        target = validate_path(file_path)
    except ValueError as e:
        return f"Error: {e}"

    if not target.exists():
        return f"File not found: {file_path}"

    try:
        from code_intelligence import get_module_cluster as _get_cluster

        rel_path = str(target.relative_to(config.PROJECT_ROOT)).replace("\\", "/")
        cluster = _get_cluster(
            rel_path, config.PROJECT_ROOT, similarity_threshold, max_cluster_size
        )

        if not cluster:
            return f"No related modules found with similarity >= {similarity_threshold}"

        lines = ["# MODULE CLUSTER\n"]
        lines.append(f"Target: `{file_path}`")
        lines.append(f"Similarity threshold: {similarity_threshold}")
        lines.append(f"Found: {len(cluster)} related files\n")
        lines.append("## Related Modules (by similarity)")

        for file, score in cluster.items():
            percentage = int(score * 100)
            lines.append(f"- `{file}` — {percentage}% similar")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def search_with_dependencies(
    query: str, n_results: int = 5, include_deps: bool = True, depth: int = 1
) -> str:
    """
    Searches codebase and optionally includes dependencies of matching files.
    Combines semantic search with structural dependency analysis.

    Args:
        query: Search query
        n_results: Number of semantic search results (default 5)
        include_deps: Whether to include dependencies (default True)
        depth: Dependency depth to include if include_deps=True (default 1)

    Returns:
        Search results with optional dependency context
    """
    ensure_startup()

    if not query or not query.strip():
        return "Error: query cannot be empty"

    if n_results < 1 or n_results > 50:
        return "Error: n_results must be between 1 and 50"

    if depth < 1 or depth > 3:
        return "Error: depth must be between 1 and 3"

    err = _check_index_ready()
    if err:
        return err

    err = _check_model_loaded()
    if err:
        return err

    try:
        # First do semantic search
        ctx = get_context()
        from vector_store_manager import vector_stack_available

        if vector_stack_available() and ctx.vector_store.get_collection() is None:
            return "Vector store not initialized. Run index_codebase() first."

        results = ctx.vector_store.hybrid_query(query_texts=[query], n_results=n_results)

        if not results or not results.get("documents") or not results["documents"][0]:
            return "No results found"

        # Extract matching files
        metadatas = results.get("metadatas", [[]])[0]
        matching_files = set()
        for meta in metadatas:
            rel = _source_rel_path(meta)
            if rel:
                matching_files.add(rel)

        lines = [f"# SEARCH RESULTS: {query}\n"]
        lines.append(f"Found {len(matching_files)} matching files\n")
        lines.append("## Direct Matches")

        for file in sorted(matching_files):
            lines.append(f"- `{file}`")

        # Optionally add dependencies
        if include_deps and matching_files:
            from code_intelligence import build_import_graph
            from code_intelligence import get_dependencies_with_depth as _get_deps

            graph = build_import_graph(config.PROJECT_ROOT)
            all_deps: set[str] = set()

            for file in matching_files:
                # Get both upstream and downstream
                downstream = _get_deps(file, graph, depth, "downstream")
                upstream = _get_deps(file, graph, depth, "upstream")
                all_deps.update(downstream.keys())
                all_deps.update(upstream.keys())

            # Remove files already in matches
            all_deps = all_deps - matching_files

            if all_deps:
                lines.append(f"\n## Related Dependencies (depth {depth})")
                lines.append(f"Found {len(all_deps)} additional files")
                for dep in sorted(all_deps)[:20]:  # Limit to 20
                    lines.append(f"- `{dep}`")

                if len(all_deps) > 20:
                    lines.append(f"\n... and {len(all_deps) - 20} more")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def search_for_errors(error_text: str, stacktrace: str = "", n_results: int = 5) -> str:
    """
    Specialized search for debugging errors. Automatically searches in:
    - Error handlers and exception code
    - Test files
    - Similar error patterns
    - Related git commits (if error recently introduced)

    Args:
        error_text: The error message or exception type
        stacktrace: Optional stacktrace for better context
        n_results: Number of results per category (default 5)

    Returns:
        Organized results focusing on debugging context
    """
    ensure_startup()

    if not error_text.strip():
        return "Error: error_text cannot be empty"

    err = _check_index_ready()
    if err:
        return err

    err = _check_model_loaded()
    if err:
        return err

    try:
        ctx = get_context()
        from vector_store_manager import vector_stack_available

        if vector_stack_available() and ctx.vector_store.get_collection() is None:
            return "Vector store not initialized. Run index_codebase() first."

        # Combine error and stacktrace for better search
        full_query = error_text
        if stacktrace:
            full_query = f"{error_text} {stacktrace}"

        # Search in code
        code_results = ctx.vector_store.hybrid_query(query_texts=[full_query], n_results=n_results)

        # Search specifically for exception handling
        exception_query = f"exception error handling try catch {error_text}"
        exception_results = ctx.vector_store.hybrid_query(
            query_texts=[exception_query], n_results=n_results
        )

        # Search for tests
        test_query = f"test {error_text}"
        test_results = ctx.vector_store.hybrid_query(query_texts=[test_query], n_results=n_results)

        lines = ["# ERROR DEBUGGING SEARCH\n"]
        lines.append(f"Error: {error_text}\n")

        # Code matches
        if code_results and code_results.get("documents") and code_results["documents"][0]:
            metadatas = code_results.get("metadatas", [[]])[0]
            files = {rel for meta in metadatas if (rel := _source_rel_path(meta))}

            lines.append("## Related Code")
            for file in sorted(files):
                lines.append(f"- `{file}`")
            lines.append("")

        # Exception handling matches
        if (
            exception_results
            and exception_results.get("documents")
            and exception_results["documents"][0]
        ):
            metadatas = exception_results.get("metadatas", [[]])[0]
            files = {rel for meta in metadatas if (rel := _source_rel_path(meta))}

            lines.append("## Error Handlers")
            for file in sorted(files):
                lines.append(f"- `{file}`")
            lines.append("")

        # Test matches
        if test_results and test_results.get("documents") and test_results["documents"][0]:
            metadatas = test_results.get("metadatas", [[]])[0]
            files = {rel for meta in metadatas if (rel := _source_rel_path(meta))}
            test_files = {f for f in files if "test" in f.lower() or "spec" in f.lower()}

            if test_files:
                lines.append("## Related Tests")
                for file in sorted(test_files):
                    lines.append(f"- `{file}`")
                lines.append("")

        # Git history if available
        if ctx.git_repo:
            try:
                commits = ctx.git_repo.get_commits(max_count=50, since_days=30)
                error_commits = [c for c in commits if error_text.lower() in c.message.lower()]

                if error_commits:
                    lines.append("## Recent Related Commits")
                    for commit in error_commits[:5]:
                        lines.append(f"- [{commit.hash[:7]}] {commit.first_line}")
                    lines.append("")
            except Exception:
                pass

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def search_for_feature(feature_name: str, n_results: int = 10) -> str:
    """
    Specialized search for understanding a feature. Automatically finds:
    - Entry points and main implementations
    - Configuration files
    - Related tests
    - Documentation

    Args:
        feature_name: Name or description of the feature
        n_results: Number of results per category (default 10)

    Returns:
        Organized results showing feature implementation structure
    """
    ensure_startup()

    if not feature_name.strip():
        return "Error: feature_name cannot be empty"

    err = _check_index_ready()
    if err:
        return err

    err = _check_model_loaded()
    if err:
        return err

    try:
        ctx = get_context()
        from vector_store_manager import vector_stack_available

        if vector_stack_available() and ctx.vector_store.get_collection() is None:
            return "Vector store not initialized. Run index_codebase() first."

        # Main search
        main_results = ctx.vector_store.hybrid_query(
            query_texts=[feature_name], n_results=n_results
        )

        # Config search
        config_query = f"config configuration {feature_name}"
        config_results = ctx.vector_store.hybrid_query(query_texts=[config_query], n_results=5)

        # Test search
        test_query = f"test {feature_name}"
        test_results = ctx.vector_store.hybrid_query(query_texts=[test_query], n_results=5)

        lines = [f"# FEATURE SEARCH: {feature_name}\n"]

        # Main implementations
        if main_results and main_results.get("documents") and main_results["documents"][0]:
            metadatas = main_results.get("metadatas", [[]])[0]
            files = []
            for meta in metadatas:
                fp = meta.get("source", "")
                if fp and "test" not in fp.lower() and "spec" not in fp.lower():
                    files.append(fp)

            if files:
                lines.append("## Main Implementation")
                for file in sorted(set(files)):
                    lines.append(f"- `{file}`")
                lines.append("")

        # Configuration files
        if config_results and config_results.get("documents") and config_results["documents"][0]:
            metadatas = config_results.get("metadatas", [[]])[0]
            config_files: set[str] = set()
            for meta in metadatas:
                fp = meta.get("source", "")
                if fp and any(
                    x in fp.lower()
                    for x in ["config", "settings", "env", ".json", ".yaml", ".toml"]
                ):
                    config_files.add(fp)

            if config_files:
                lines.append("## Configuration")
                for file in sorted(config_files):
                    lines.append(f"- `{file}`")
                lines.append("")

        # Tests
        if test_results and test_results.get("documents") and test_results["documents"][0]:
            metadatas = test_results.get("metadatas", [[]])[0]
            test_files: set[str] = set()
            for meta in metadatas:
                fp = meta.get("source", "")
                if fp and ("test" in fp.lower() or "spec" in fp.lower()):
                    test_files.add(fp)

            if test_files:
                lines.append("## Tests")
                for file in sorted(test_files):
                    lines.append(f"- `{file}`")
                lines.append("")

        # Add dependency analysis if we found implementation files
        from code_intelligence import build_import_graph
        from code_intelligence import get_dependencies_with_depth as _get_deps

        if main_results and main_results.get("metadatas"):
            impl_files = [
                rel for m in main_results["metadatas"][0] if (rel := _source_rel_path(m))
            ][:3]
            graph = build_import_graph(config.PROJECT_ROOT)

            # Find files with no upstream dependencies (potential entry points)
            for file in impl_files:
                if file in graph:
                    upstream = _get_deps(file, graph, depth=1, direction="upstream")
                    if not upstream:  # No one imports this = potential entry point
                        lines.append(f"## Potential Entry Point: `{file}`")
                        break

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def search_architecture(component: str, n_results: int = 10) -> str:
    """
    Specialized search for understanding architecture. Finds:
    - Core modules and entry points
    - Module dependencies
    - Configuration and setup
    - Architectural patterns

    Args:
        component: Component or module name (e.g., "auth", "database", "api")
        n_results: Number of results (default 10)

    Returns:
        Architectural overview with dependency relationships
    """
    ensure_startup()

    if not component.strip():
        return "Error: component cannot be empty"

    err = _check_index_ready()
    if err:
        return err

    err = _check_model_loaded()
    if err:
        return err

    try:
        from code_intelligence import build_import_graph
        from code_intelligence import get_module_cluster as _get_cluster

        ctx = get_context()
        from vector_store_manager import vector_stack_available

        if vector_stack_available() and ctx.vector_store.get_collection() is None:
            return "Vector store not initialized. Run index_codebase() first."

        # Search for component
        results = ctx.vector_store.hybrid_query(query_texts=[component], n_results=n_results)

        if not results or not results.get("documents") or not results["documents"][0]:
            return f"No results found for component: {component}"

        metadatas = results.get("metadatas", [[]])[0]
        main_files = [rel for meta in metadatas if (rel := _source_rel_path(meta))]

        lines = [f"# ARCHITECTURE: {component}\n"]

        if main_files:
            lines.append("## Core Modules")
            for file in sorted(set(main_files)):
                lines.append(f"- `{file}`")
            lines.append("")

            graph = build_import_graph(config.PROJECT_ROOT)

            for file in main_files[:5]:
                if file in graph:
                    cluster = _get_cluster(
                        file,
                        config.PROJECT_ROOT,
                        similarity_threshold=0.4,
                        max_cluster_size=10,
                        graph=graph,
                    )
                    if cluster:
                        lines.append(f"## Related to `{file}`")
                        for related, score in list(cluster.items())[:5]:
                            pct = int(score * 100)
                            lines.append(f"- `{related}` ({pct}% similar)")
                        lines.append("")
                        break

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def find_symbol(name: str, n_results: int = 8) -> str:
    """
    Finds where a function/class/method is defined using the AST symbol graph.
    Works without the vector index (no embedding model needed).

    Args:
        name: Symbol name — exact or partial (case-insensitive).
        n_results: Max results (default 8).

    Returns:
        Definitions with file:line plus caller/callee counts.
    """
    ensure_startup()
    if not name.strip():
        return "Error: name cannot be empty"
    try:
        from symbol_graph import SymbolGraphBusy, query_symbol_graph

        try:
            hits = query_symbol_graph(name.strip(), n_results=n_results)
        except SymbolGraphBusy as busy:
            return f"⏳ {busy}"
        if not hits:
            from symbol_graph import peek_symbol_graph

            graph = peek_symbol_graph()
            if graph is None:
                return (
                    f"No symbols matching '{name}': the symbol graph has not been "
                    "built yet. Run `index_codebase()` first."
                )
            if graph.truncated:
                # A partial graph must not masquerade as "this symbol does not
                # exist" — that is exactly how a broken build stayed invisible.
                return (
                    f"⚠️ No symbols matching '{name}', but the symbol graph is "
                    f"INCOMPLETE: {graph.truncated}\n"
                    f"Coverage so far: {graph.status_line()}\n"
                    "The symbol may exist in the unparsed part of the tree. "
                    "Exclude generated directories in `.indexignore`, then re-run "
                    "`index_codebase(force=True)`."
                )
            return f"No symbols matching '{name}' found. " f"Symbol graph: {graph.status_line()}."
        lines = [f"# SYMBOLS: {name}\n"]
        for h in hits:
            extra = h.get("extra", {})
            lines.append(
                f"- **{extra.get('symbol_name', name)}** ({extra.get('symbol_kind', '?')}) — "
                f"`{h.get('source', '?')}:{extra.get('line', 0)}` "
                f"[{extra.get('callers', 0)} callers / {extra.get('callees', 0)} callees]"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_symbol_relations(symbol: str, relation: str = "usages") -> str:
    """
    Returns AST-level relations of a symbol from the symbol graph.

    Args:
        symbol: Function/class/method name (bare, e.g. "UserService").
        relation: One of "usages" (default), "callers", "callees",
            "implementors", "subclasses", "bases", "info".

    Returns:
        Related symbols with file:line, or full info for relation="info".
    """
    ensure_startup()
    if not symbol.strip():
        return "Error: symbol cannot be empty"
    symbol = symbol.strip()
    relation = relation.strip().lower()
    try:
        import symbol_graph as sg

        if relation == "info":
            info = sg.get_symbol_info(symbol)
            if info is None:
                return f"Symbol '{symbol}' not found in the graph."
            lines = [
                f"# SYMBOL: {info['name']} ({info['kind']})",
                f"**Defined**: `{info['file']}:{info['lines']}`",
            ]
            if info.get("parent_class"):
                lines.append(f"**Class**: {info['parent_class']}")
            if info.get("base_classes"):
                lines.append(f"**Inherits**: {', '.join(info['base_classes'])}")
            if info.get("interfaces"):
                lines.append(f"**Implements**: {', '.join(info['interfaces'])}")
            if info.get("other_definitions"):
                lines.append("\n**Other definitions with the same name:**")
                for d in info["other_definitions"]:
                    lines.append(f"- `{d['file']}:{d['line']}`")
            for key in ("callers", "callees", "subclasses", "implementors"):
                vals = info.get(key) or []
                if vals:
                    lines.append(f"\n**{key.capitalize()}** ({len(vals)}):")
                    for v in vals[:15]:
                        lines.append(f"- {v}")
                    if len(vals) > 15:
                        lines.append(f"- ... and {len(vals) - 15} more")
            return "\n".join(lines)

        fetchers = {
            "usages": sg.find_usages,
            "callers": sg.find_callers,
            "callees": sg.find_callees,
            "implementors": sg.find_implementors,
            "subclasses": sg.find_subclasses,
            "bases": sg.find_base_classes,
        }
        fetch = fetchers.get(relation)
        if fetch is None:
            return f"Error: unknown relation '{relation}'. Use one of: {', '.join(fetchers)} or 'info'."

        rows = fetch(symbol)
        if not rows:
            return f"No {relation} found for '{symbol}'."
        lines = [f"# {relation.upper()} of `{symbol}` ({len(rows)})\n"]
        for r in rows[:40]:
            rel_note = f" ({r['relation']})" if r.get("relation") else ""
            lines.append(f"- **{r['symbol']}**{rel_note} ({r['kind']}) — `{r['file']}:{r['line']}`")
        if len(rows) > 40:
            lines.append(f"\n... and {len(rows) - 40} more")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def save_annotation(path: str, summary: str, keywords: str = "") -> str:
    """
    Saves an AI-authored annotation (summary + keywords) for a source file.

    Annotations make keyword search semantic WITHOUT embeddings: your summary
    is indexed into BM25, so natural-language queries like "where is auth
    handled" match it. Write annotations for files you have just read or
    changed — 1-2 sentences on what the file does and why it exists.

    Args:
        path: File path relative to project root (or absolute inside it).
        summary: 1-3 sentences: purpose, key responsibilities, notable design.
        keywords: Comma-separated search terms a developer might query
            (synonyms, domain words not present in identifiers).

    Returns:
        Confirmation with annotation coverage stats.
    """
    ensure_startup()
    if not summary.strip():
        return "Error: summary cannot be empty"
    try:
        from annotations import get_store

        store = get_store()
        kw = [k for k in (s.strip() for s in keywords.split(",")) if k]
        ann = store.set(path, summary, kw)

        # Live-update the keyword corpus so the annotation is searchable now
        try:
            ctx = get_context()
            ctx.vector_store.upsert_bm25_annotation(
                ann.path,
                ann.doc_id,
                ann.search_text(),
                {
                    "source": ann.path,
                    "symbol_type": "annotation",
                    "symbol_name": Path(ann.path).stem,
                },
            )
        except Exception as e:
            log(f"Annotation saved but BM25 update failed: {e}")

        total = store.count()
        return (
            f"Annotation saved for `{ann.path}` ({len(ann.keywords)} keywords). "
            f"Project now has {total} annotated files. "
            "It is immediately searchable via search_codebase()/query()."
        )
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error saving annotation: {e}"


@mcp.tool()
def get_annotations(path: str = "") -> str:
    """
    Reads file annotations.

    Args:
        path: Specific file to read the annotation for. Empty = coverage
            overview with all annotated files.

    Returns:
        The annotation (or coverage summary) in Markdown.
    """
    ensure_startup()
    try:
        from annotations import get_store

        store = get_store()
        if path.strip():
            ann = store.get(path)
            if ann is None:
                return f"No annotation for `{path}`. Use save_annotation() to add one."
            stale = " ⚠️ STALE (file changed since annotation)" if store.is_stale(ann) else ""
            lines = [
                f"# ANNOTATION: `{ann.path}`{stale}",
                "",
                ann.summary,
            ]
            if ann.keywords:
                lines.append(f"\n**Keywords**: {', '.join(ann.keywords)}")
            lines.append(f"**Updated**: {ann.updated_at}")
            return "\n".join(lines)

        anns = store.all()
        if not anns:
            return (
                "No annotations yet. Use `list_unannotated_files()` to see candidates "
                "and `save_annotation(path, summary, keywords)` to add them."
            )
        lines = [f"# ANNOTATIONS ({len(anns)} files)\n"]
        for rel in sorted(anns):
            ann = anns[rel]
            stale = " ⚠️" if store.is_stale(ann) else ""
            lines.append(f"- `{rel}`{stale} — {ann.summary[:100]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading annotations: {e}"


@mcp.tool()
def list_unannotated_files(limit: int = 20) -> str:
    """
    Lists code files that have no annotation (or a stale one), so you can
    annotate them with save_annotation().

    Recommended workflow: after finishing work on a file, save/refresh its
    annotation; periodically run this tool and annotate the backlog — that is
    what makes natural-language search over this project accurate.

    Args:
        limit: Max files per category (default 20).

    Returns:
        Missing and stale annotation lists + coverage stats.
    """
    ensure_startup()
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200
    try:
        from annotations import get_store

        store = get_store()
        missing, stale, scanned = store.unannotated(limit=limit)
        annotated = store.count()

        lines = ["# ANNOTATION COVERAGE\n"]
        lines.append(f"**Annotated**: {annotated} | **Code files scanned**: {scanned}")

        if missing:
            lines.append(f"\n## Missing ({len(missing)} shown)")
            for rel in missing:
                lines.append(f"- `{rel}`")
        if stale:
            lines.append(f"\n## Stale — file changed since annotation ({len(stale)} shown)")
            for rel in stale:
                lines.append(f"- `{rel}`")
        if not missing and not stale:
            lines.append("\n✅ Every scanned code file has an up-to-date annotation.")
        else:
            lines.append(
                "\n_Read each file and call " "`save_annotation(path, summary, keywords)` for it._"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error scanning annotations: {e}"


@mcp.tool()
def save_conventions_to_memory() -> str:
    """
    Detects project conventions and saves them to memory.md automatically.
    This persists conventions so AI assistants have context without re-scanning.

    Returns:
        Confirmation with summary of what was saved.
    """
    ensure_startup()
    try:
        from code_intelligence import detect_conventions

        report = detect_conventions(config.PROJECT_ROOT)
        ctx = get_context()
        ctx.memory_manager.update(report, section="Project Conventions")
        return f"Conventions saved to memory.\n\n{report}"
    except Exception as e:
        return f"Error saving conventions: {e}"


@mcp.tool()
def project_onboarding() -> str:
    """
    One-command full project briefing. Runs overview + conventions + dependencies +
    TODOs and compiles a comprehensive brief for onboarding a new developer or AI.
    Results are saved to memory for future reference.

    Returns:
        Complete project brief.
    """
    ensure_startup()
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[str, str] = {}

        def _run_overview() -> tuple[str, str]:
            return "overview", get_project_overview()

        def _run_conventions() -> tuple[str, str]:
            try:
                from code_intelligence import detect_conventions

                return "conventions", detect_conventions(config.PROJECT_ROOT)
            except Exception:
                return "conventions", ""

        def _run_deps() -> tuple[str, str]:
            try:
                from code_intelligence import check_dependencies as _check_deps

                return "deps", _check_deps(config.PROJECT_ROOT)
            except Exception:
                return "deps", ""

        def _run_todos() -> tuple[str, str]:
            try:
                from code_intelligence import extract_todos

                return "todos", extract_todos(config.PROJECT_ROOT, max_files=500)
            except Exception:
                return "todos", ""

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(_run_overview),
                executor.submit(_run_conventions),
                executor.submit(_run_deps),
                executor.submit(_run_todos),
            ]
            for future in as_completed(futures):
                key, val = future.result()
                results[key] = val

        brief_parts: list[str] = []
        brief_parts.append(results.get("overview", ""))
        brief_parts.append("")

        if conventions := results.get("conventions", ""):
            brief_parts.append(conventions)
            brief_parts.append("")

        if deps := results.get("deps", ""):
            if "No dependency files" not in deps:
                brief_parts.append(deps)
                brief_parts.append("")

        if todos := results.get("todos", ""):
            if "No TODO" not in todos:
                brief_parts.append(todos)
                brief_parts.append("")

        full_brief = "\n".join(brief_parts)

        try:
            ctx = get_context()
            summary_lines = ["Auto-generated project onboarding brief."]
            if conventions:
                ctx.memory_manager.update(conventions, section="Project Conventions")
            if deps and "No dependency files" not in deps:
                ctx.memory_manager.update(deps, section="Dependencies")
            summary_lines.append("Conventions and dependencies saved to memory.")
            ctx.memory_manager.update("\n".join(summary_lines), section="Onboarding")
        except Exception:
            pass

        return full_brief
    except Exception as e:
        return f"Error during onboarding: {e}"


@mcp.resource("project://memory")
def get_project_memory() -> str:
    # Direct file read to avoid blocking on vector store initialization
    if not config.MEMORY_FILE.exists():
        return "Memory file not found."
    try:
        return config.MEMORY_FILE.read_text()
    except Exception as e:
        logger.error(f"Error reading memory: {e}")
        return f"Error reading memory: {e}"


@mcp.tool()
def read_memory(max_lines: int | None = 100) -> str:
    """
    Read project memory.

    Args:
        max_lines: Maximum number of lines to return (default: 100).
                   Set to None for full content. Use smaller values for quick summaries.

    Returns:
        Memory content (possibly truncated)
    """
    if max_lines is not None and max_lines <= 0:
        return "Error: max_lines must be positive or None"

    # Direct file read without context initialization to avoid blocking
    if not config.MEMORY_FILE.exists():
        return "Memory file not found."

    try:
        content = config.MEMORY_FILE.read_text()

        if max_lines is None:
            return content

        lines = content.split("\n")
        if len(lines) <= max_lines:
            return content

        truncated = "\n".join(lines[:max_lines])
        remaining = len(lines) - max_lines
        return f"{truncated}\n\n... ({remaining} more lines truncated. Use read_memory(max_lines=None) for full content)"
    except Exception as e:
        logger.error(f"Error reading memory: {e}")
        return f"Error reading memory: {e}"


@mcp.tool()
def search_memory(query: str, n_results: int = 5) -> str:
    """
    Relevance-ranked memory retrieval. Returns the memory blocks most relevant
    to `query` (scored by keyword overlap) instead of the head of memory.md.

    Use this to pull targeted prior decisions/conventions/notes for the current
    task without loading the whole memory file.

    Args:
        query: Free-form text describing what you're looking for.
        n_results: Maximum number of memory blocks to return (default 5).

    Returns:
        Markdown with the top matching memory blocks, or a hint if none match.
    """
    from memory_manager import MemoryManager

    if not query or not query.strip():
        return "Error: query cannot be empty."

    mm = MemoryManager()
    blocks = mm.search_blocks(query, k=max(1, n_results))
    if not blocks:
        return (
            f"No memory blocks matched '{query}'. "
            "Use `read_memory_index()` to list available sections."
        )

    out: list[str] = [f"# MEMORY SEARCH: {query}", f"**Matches**: {len(blocks)}", ""]
    for i, (heading, score, text) in enumerate(blocks, 1):
        out.append(f"## {i}. {heading} _(score={score:.2f})_")
        if text:
            out.append(text)
        out.append("")
    return "\n".join(out)


@mcp.tool()
def update_memory(content: str, section: str = "Recent Decisions") -> str:
    # Use direct MemoryManager to avoid vector store initialization
    from memory_manager import MemoryManager

    mm = MemoryManager()
    return mm.update(content, section)


@mcp.tool()
def clear_memory(keep_template: bool = True) -> str:
    # Use direct MemoryManager to avoid vector store initialization
    from memory_manager import MemoryManager

    mm = MemoryManager()
    return mm.clear(keep_template)


@mcp.tool()
def delete_memory_section(section_name: str) -> str:
    # Use direct MemoryManager to avoid vector store initialization
    from memory_manager import MemoryManager

    mm = MemoryManager()
    return mm.delete_section(section_name)


@mcp.tool()
def index_codebase(force: bool = False, background: bool = True) -> str:
    """
    Indexes the entire codebase for semantic search.

    Limited to 5000 files per operation. For large codebases use index_changed_files() instead.

    By default runs in **background** mode: returns immediately with a project
    structure overview while the heavy embedding work continues in a daemon thread.
    Call `get_index_progress()` to poll completion status.

    Args:
        force: If True, clears existing index before indexing.
        background: If True (default), runs indexing in a background thread and
                    returns immediately with a project overview. If False, blocks
                    until indexing is fully complete (old behaviour — may timeout
                    on large codebases).

    Returns:
        - background=True:  instant project structure overview + status line
        - background=False: final indexing stats string
    """
    root_dir = config.PROJECT_ROOT
    warn_own_dir = ""
    if is_mcp_server_dir(root_dir):
        warn_own_dir = (
            "⚠️ Warning: indexing the ProjectMind MCP server's OWN directory "
            f"({root_dir}). If this was not intended, call "
            "`set_project_root('<absolute path to your project>')` first, "
            "then re-run `index_codebase`.\n\n"
        )

    # ── Background mode (default) ────────────────────────────────────────────
    if background:
        from background_indexer import BackgroundIndexer

        # An explicit force=True is a request to rebuild *now*: it preempts a
        # running job instead of being refused by one (a job wedged in its
        # final phase used to make force permanently unreachable).
        if BackgroundIndexer.is_running() and not force:
            prog = BackgroundIndexer.get_progress()
            done = prog.get("files_done", 0)
            total = prog.get("files_total", 0)
            pct = int(done / total * 100) if total else 0
            phase = prog.get("phase") or prog.get("status") or "?"
            return (
                f"{warn_own_dir}"
                f"⏳ Background indexing already in progress: {done}/{total} files ({pct}%), "
                f"phase `{phase}`.\n"
                "Call `get_index_progress()` to monitor, `index_codebase(force=True)` to "
                "cancel it and start a fresh full rebuild, or "
                "`index_codebase(background=False)` to wait for completion."
            )

        # Collect instant project structure before starting the heavy work
        structure_lines: list[str] = [f"# Indexing started for `{root_dir}`\n"]
        try:
            from manifest import load_manifest, quick_overview_from_manifest

            m = load_manifest()
            if m:
                structure_lines.append(
                    f"## Project Structure (instant)\n"
                    f"- **{m.stats.indexed_files}** indexable files / {m.stats.total_files} total\n"
                    f"- **{m.stats.total_size_bytes / (1024 * 1024):.1f} MB** total\n"
                    f"- Top modules: " + ", ".join(f"`{mod.name}/`" for mod in m.modules[:8])
                )
                structure_lines.append("### Overview\n" + quick_overview_from_manifest(m))
            else:
                structure_lines.append(
                    "## Project Structure (instant)\n"
                    "_Manifest not built yet. It is being built in the background..._"
                )
        except Exception as _e:
            structure_lines.append(f"_Structure overview unavailable: {_e}_")

        preempted = force and BackgroundIndexer.is_running()
        started = BackgroundIndexer.start(force=force, preempt=force)
        if started:
            status = "restarted (previous job cancelled)" if preempted else "started"
        else:
            status = (
                "NOT started — the running job did not stop in time; retry in a moment"
                if preempted
                else "already running"
            )
        structure_lines.append(
            f"\n---\n⏳ **Background indexing {status}.**\n"
            "Call `get_index_progress()` to check status and watch files being indexed.\n"
            "When done, `search_codebase()` and `search_for_feature()` will be available."
        )
        return warn_own_dir + "\n\n".join(structure_lines)

    # ── Synchronous mode (background=False) — legacy blocking behaviour ──────
    from code_intelligence import invalidate_import_graph_cache
    from vector_store_manager import vector_stack_available

    ctx = get_context()
    if vector_stack_available() and ctx.vector_store.get_collection() is None:
        return "Failed to initialize vector store."

    ignored_dirs = get_ignored_dirs()
    ignore_patterns = load_index_ignore_patterns()

    result = ctx.indexer.index_all(root_dir, ignored_dirs, ignore_patterns, force)
    invalidate_import_graph_cache()
    return warn_own_dir + result


@mcp.tool()
def get_index_progress() -> str:
    """
    Returns the current status of a background indexing operation started by
    `index_codebase()` (with background=True, which is the default).

    Statuses:
      - **idle**              — no indexing job has been run yet
      - **scanning**          — discovering indexable files (fast, ~1 s)
      - **initializing_model**— loading SentenceTransformer model (30–60 s first run)
      - **indexing**          — embedding and storing file chunks
      - **finalizing**        — flushing buffers and rebuilding BM25 index
      - **done**              — indexing complete, search tools are ready
      - **error**             — indexing failed (see `last_error` field)

    Returns:
        Markdown-formatted progress report with file counts, percentage, and ETA.
    """
    from background_indexer import BackgroundIndexer, format_progress_markdown

    data = BackgroundIndexer.get_progress()
    return format_progress_markdown(data)


@mcp.tool()
def search_codebase(query: str, n_results: int = 5) -> str:
    if not query or not query.strip():
        return "Error: Query cannot be empty."

    if n_results <= 0:
        return "Error: n_results must be greater than 0."

    if n_results > 50:
        return "Error: n_results cannot exceed 50."

    err = _check_index_ready()
    if err:
        return err

    err = _check_model_loaded()
    if err:
        return err

    try:
        ctx = get_context()
        results = ctx.vector_store.hybrid_query(query_texts=[query], n_results=n_results)

        if results is None:
            return "Vector store not initialized."

        if not results.get("documents") or not results["documents"][0]:
            return "No matches found."

        # Extract files and calculate coverage
        files = set()
        for meta in results.get("metadatas", [[]])[0]:
            if "source" in meta:
                files.add(meta["source"])

        # Check index coverage
        total_count = ctx.vector_store.get_count()
        coverage = "full" if total_count and total_count > 100 else "partial"

        # Calculate average relevance (distance)
        distances = results.get("distances", [[]])[0]
        avg_distance = sum(distances) / len(distances) if distances else 0
        confidence = max(0.0, min(1.0, 1.0 - avg_distance))  # Convert distance to confidence

        # Build output with metadata
        output = [f"# SEARCH: {query}\n"]
        output.append(f"**Results**: {len(results['documents'][0])}")
        output.append(f"**Confidence**: {int(confidence * 100)}%")
        output.append(f"**Coverage**: {coverage}")
        output.append(f"**Files**: {len(files)}\n")

        # Add results
        for i in range(len(results["documents"][0])):
            doc = results["documents"][0][i]
            meta = results["metadatas"][0][i]
            file_path = meta.get("source", "")

            relevance_score = 0
            if distances and i < len(distances):
                relevance_score = max(0, int((1 - distances[i]) * 100))

            output.append(f"## Result {i + 1} ({relevance_score}% relevant)")
            if file_path:
                output.append(f"**File**: `{file_path}`")
            output.append(f"```\n{doc}\n```\n")

        # Add suggestions based on results
        suggestions = []
        if confidence < 0.5:
            suggestions.append("Low confidence - consider refining your query")
        if len(files) < 3:
            suggestions.append("Few files matched - try broader search terms")
        if coverage == "partial":
            suggestions.append("Partial index coverage - run index_codebase() for complete results")

        if suggestions:
            output.append("## Suggestions")
            for s in suggestions:
                output.append(f"- {s}")

        return "\n".join(output)
    except Exception as e:
        log(f"Search error: {e}")
        return f"Error during search: {e}"


@mcp.tool()
def ingest_git_history(limit: int = 30) -> str:
    if limit <= 0:
        return "Error: Limit must be greater than 0."

    if limit > 1000:
        return "Error: Limit cannot exceed 1000."

    try:
        git_repo = GitRepository()
        commits = git_repo.get_commits(max_count=limit)
    except GitError as e:
        return str(e)

    ctx = get_context()
    current_memory = ctx.memory_manager.read(max_lines=None)
    if current_memory == "Memory file not found.":
        return "Memory file not found."

    try:
        header = "## Development Log (Git)"
        if header not in current_memory:
            ctx.memory_manager.update("", section="Development Log (Git)")
            current_memory = ctx.memory_manager.read(max_lines=None)

        new_entries = []
        for commit in commits:
            if commit.short_hash in current_memory:
                continue

            message = commit.message.replace("\n", " ")
            entry = f"- **{commit.date_str}** [{commit.short_hash}]: {message} (*{commit.author}*)"
            new_entries.append(entry)

        if not new_entries:
            return "No new commits found to ingest."

        new_entries.reverse()
        entries_text = "\n".join(new_entries)
        ctx.memory_manager.update(entries_text, section="Development Log (Git)")

        return f"Ingested {len(new_entries)} new commits into memory."
    except Exception as e:
        log(f"Error ingesting git history: {e}")
        return f"Error ingesting git history: {e}"


@mcp.tool()
def get_index_stats() -> str:
    """
    Returns statistics about the current vector store (number of chunks).
    This operation is very fast and doesn't trigger vector store initialization.
    """
    vector_db_path = config.VECTOR_STORE_DIR / "chroma.sqlite3"
    if not vector_db_path.exists():
        return "Vector store not initialized. Run index_codebase() first."

    try:
        import sqlite3

        conn = sqlite3.connect(str(vector_db_path))
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        return f"Vector store contains {count} chunks."
    except Exception as e:
        return f"Error reading vector store: {e}"


@mcp.tool()
def generate_project_summary() -> str:
    try:
        summary_parts = []

        summary_parts.append("# PROJECT SUMMARY\n")

        memory = read_memory()
        if memory and "Memory file not found" not in memory:
            summary_parts.append("## Current Memory State")
            lines = memory.split("\n")[:30]
            summary_parts.append("\n".join(lines))
            if len(memory.split("\n")) > 30:
                summary_parts.append("\n... (truncated, see full memory)\n")

        try:
            git_repo = GitRepository()
            commits = git_repo.get_commits(max_count=5)
            if commits:
                summary_parts.append("\n## Recent Activity (Last 5 Commits)")
                for commit in commits:
                    summary_parts.append(f"- {commit.date_short}: {commit.first_line[:80]}")
        except GitError:
            pass

        root = config.PROJECT_ROOT
        py_files = 0
        js_files = 0

        for _root_path, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not is_dir_ignored(d)]
            for file in files:
                if file.endswith(".py"):
                    py_files += 1
                elif file.endswith((".js", ".ts")):
                    js_files += 1

        summary_parts.append("\n## Codebase Stats")
        summary_parts.append(f"- Python files: {py_files}")
        summary_parts.append(f"- JavaScript/TypeScript files: {js_files}")

        stats = get_index_stats()
        summary_parts.append(f"- {stats}")

        return "\n".join(summary_parts)
    except Exception as e:
        return f"Error generating summary: {e}"


@mcp.tool()
def extract_tech_stack() -> str:
    try:
        tech_stack = []

        pyproject_path = config.PROJECT_ROOT / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text()
            tech_stack.append("## Python Project")
            if "dependencies" in content:
                tech_stack.append("\n**Dependencies:**")
                lines = content.split("\n")
                in_deps = False
                for line in lines:
                    if "dependencies = [" in line:
                        in_deps = True
                        continue
                    if in_deps:
                        if "]" in line:
                            break
                        if '"' in line:
                            tech_stack.append(f"- {line.strip()}")

        requirements_path = config.PROJECT_ROOT / "requirements.txt"
        if not tech_stack and requirements_path.exists():
            content = requirements_path.read_text()
            tech_stack.append("## Python Project")
            tech_stack.append("\n**Dependencies:**")
            for line in content.split("\n"):
                if line.strip() and not line.startswith("#"):
                    tech_stack.append(f"- {line.strip()}")

        package_json_path = config.PROJECT_ROOT / "package.json"
        if package_json_path.exists():
            import json

            with open(package_json_path) as f:
                data = json.load(f)
            tech_stack.append("\n## JavaScript/Node.js Project")
            if "dependencies" in data:
                tech_stack.append("\n**Dependencies:**")
                for dep, ver in list(data["dependencies"].items())[:15]:
                    tech_stack.append(f"- {dep}: {ver}")
                if len(data["dependencies"]) > 15:
                    tech_stack.append(f"... and {len(data['dependencies']) - 15} more")

        cargo_path = config.PROJECT_ROOT / "Cargo.toml"
        if cargo_path.exists():
            tech_stack.append("\n## Rust Project")

        gomod_path = config.PROJECT_ROOT / "go.mod"
        if gomod_path.exists():
            tech_stack.append("\n## Go Project")

        if not tech_stack:
            return "No standard dependency files found (pyproject.toml, package.json, etc.)"

        return "\n".join(tech_stack)
    except Exception as e:
        return f"Error extracting tech stack: {e}"


_structure_cache: str | None = None
_structure_cache_time: float = 0.0
_structure_cache_lock = threading.Lock()
STRUCTURE_CACHE_TTL = 300


@mcp.tool()
def analyze_project_structure() -> str:
    global _structure_cache, _structure_cache_time

    current_time = time()

    with _structure_cache_lock:
        if _structure_cache and (current_time - _structure_cache_time) < STRUCTURE_CACHE_TTL:
            return _structure_cache

    try:
        root = config.PROJECT_ROOT

        structure = []
        structure.append("# PROJECT STRUCTURE\n")

        dirs_by_depth = {}
        for item in root.iterdir():
            if item.is_dir() and not is_dir_ignored(item.name):
                try:
                    count = 0
                    for _r, _d, _f in os.walk(item):
                        _d[:] = [d for d in _d if not is_dir_ignored(d)]
                        count += len(_f)
                    dirs_by_depth[item.name] = count
                except (PermissionError, OSError):
                    continue

        sorted_dirs = sorted(dirs_by_depth.items(), key=lambda x: x[1], reverse=True)[:10]

        structure.append("## Main Directories (by size)")
        for dir_name, count in sorted_dirs:
            structure.append(f"- `{dir_name}/` ({count} items)")

        file_types: dict[str, int] = {}
        for _root_path, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not is_dir_ignored(d)]

            for file in files:
                ext = Path(file).suffix
                if ext in [
                    ".py",
                    ".js",
                    ".ts",
                    ".jsx",
                    ".tsx",
                    ".go",
                    ".rs",
                    ".java",
                    ".c",
                    ".cpp",
                ]:
                    file_types[ext] = file_types.get(ext, 0) + 1

        if file_types:
            structure.append("\n## File Types")
            for ext, count in sorted(file_types.items(), key=lambda x: x[1], reverse=True):
                structure.append(f"- `{ext}`: {count} files")

        config_files = []
        for cfg in [
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
            ".gitignore",
            "docker-compose.yml",
            "Dockerfile",
            ".env.example",
        ]:
            if (root / cfg).exists():
                config_files.append(cfg)

        if config_files:
            structure.append("\n## Configuration Files")
            for cfg in config_files:
                structure.append(f"- {cfg}")

        result = "\n".join(structure)

        with _structure_cache_lock:
            _structure_cache = result
            _structure_cache_time = current_time

        return result
    except Exception as e:
        return f"Error analyzing structure: {e}"


@mcp.tool()
def get_recent_changes_summary(days: int = 7) -> str:
    if days <= 0 or days > 365:
        return "Error: days must be between 1 and 365"

    try:
        git_repo = GitRepository()
        commits = git_repo.get_commits(max_count=100, since_days=days)
    except GitError as e:
        return str(e)

    if not commits:
        return f"No commits found in the last {days} days"

    try:
        summary = [f"# CHANGES IN LAST {days} DAYS\n"]
        summary.append(f"Total commits: {len(commits)}\n")

        author_stats = git_repo.get_author_stats(commits)
        summary.append("## Contributors")
        summary.extend(git_repo.format_author_stats(author_stats))

        summary.append("\n## Recent Commits")
        summary.extend(git_repo.format_commits_summary(commits, max_display=10))

        return "\n".join(summary)
    except Exception as e:
        return f"Error analyzing changes: {e}"


@mcp.tool()
def index_changed_files() -> str:
    """
    Incrementally indexes only changed files since last indexing.

    Faster than index_codebase — only processes files modified since last run.

    Returns:
        Status message with indexing stats
    """
    from code_intelligence import invalidate_import_graph_cache
    from vector_store_manager import vector_stack_available

    ctx = get_context()
    if vector_stack_available() and ctx.vector_store.get_collection() is None:
        return "Failed to initialize vector store."

    root_dir = config.PROJECT_ROOT
    ignored_dirs = get_ignored_dirs()
    ignore_patterns = load_index_ignore_patterns()

    result = ctx.indexer.index_changed(root_dir, ignored_dirs, ignore_patterns)
    invalidate_import_graph_cache()
    return result


def should_include_search_result(
    source: str,
    relevance: float,
    file_types: list[str] | None,
    exclude_dirs: list[str] | None,
    min_relevance: float,
) -> bool:
    """
    Determines if a search result should be included based on filters.

    Args:
        source: File path of the result
        relevance: Relevance score (0-1)
        file_types: Allowed file extensions (None = all)
        exclude_dirs: Directories to exclude (None = none)
        min_relevance: Minimum relevance threshold

    Returns:
        True if result passes all filters
    """
    if min_relevance > 0 and relevance < min_relevance:
        return False

    if file_types:
        file_ext = Path(source).suffix
        if file_ext not in file_types:
            return False

    if exclude_dirs:
        source_parts = Path(source).parts
        for exc_dir in exclude_dirs:
            if exc_dir in source_parts:
                return False

    return True


def format_search_result(source: str, document: str, relevance: float) -> str:
    """
    Formats a single search result for display.

    Args:
        source: Source file path
        document: Document content
        relevance: Relevance score

    Returns:
        Formatted result string
    """
    return f"--- {source} (relevance: {relevance:.2f}) ---\n{document}\n"


@mcp.tool()
def search_codebase_advanced(
    query: str,
    n_results: int = 5,
    file_types: list[str] | None = None,
    exclude_dirs: list[str] | None = None,
    min_relevance: float = 0.0,
) -> str:
    if not query or not query.strip():
        return "Error: Query cannot be empty."

    if n_results <= 0:
        return "Error: n_results must be greater than 0."

    if n_results > 50:
        return "Error: n_results cannot exceed 50."

    if min_relevance < 0 or min_relevance > 1:
        return "Error: min_relevance must be between 0 and 1."

    err = _check_index_ready()
    if err:
        return err

    err = _check_model_loaded()
    if err:
        return err

    try:
        ctx = get_context()
        results = ctx.vector_store.hybrid_query(query_texts=[query], n_results=n_results * 2)

        if results is None:
            return "Vector store not initialized."

        output = []
        if results["documents"]:
            for i in range(len(results["documents"][0])):
                doc = results["documents"][0][i]
                meta = results["metadatas"][0][i]
                source = meta.get("source", "unknown")
                distance = results.get("distances", [[]])[0][i] if "distances" in results else 0

                relevance = max(0.0, 1.0 - (distance / 2.0))

                if should_include_search_result(
                    source, relevance, file_types, exclude_dirs, min_relevance
                ):
                    output.append(format_search_result(source, doc, relevance))

                if len(output) >= n_results:
                    break

        return "\n".join(output) if output else "No matches found."
    except Exception as e:
        log(f"Search error: {e}")
        return f"Error during search: {e}"


@mcp.tool()
def auto_update_memory_from_commits(days: int = 7, auto_summarize: bool = True) -> str:
    if days <= 0 or days > 90:
        return "Error: days must be between 1 and 90"

    try:
        git_repo = GitRepository()
        commits = git_repo.get_commits(max_count=100, since_days=days)
    except GitError as e:
        return str(e)

    if not commits:
        return f"No commits found in the last {days} days"

    try:
        if auto_summarize and len(commits) > 5:
            summary_lines = [f"## Auto-Summary ({days} days)"]
            summary_lines.append(f"Total commits: {len(commits)}")

            author_stats = git_repo.get_author_stats(commits)
            summary_lines.append("\n**Contributors:**")
            summary_lines.extend(git_repo.format_author_stats(author_stats))

            summary_lines.append("\n**Key Changes:**")
            for commit in commits[:10]:
                summary_lines.append(f"- {commit.first_line}")

            summary_text = "\n".join(summary_lines)
            update_memory(summary_text, section="Recent Activity")

            return f"Auto-summarized {len(commits)} commits into memory"
        else:
            ingested = ingest_git_history(limit=len(commits))
            return f"Auto-update: {ingested}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def analyze_code_complexity(target_path: str = ".", mode: str = "quick") -> str:
    """Analyze cyclomatic complexity. mode='quick' (100 files) or 'deep' (1000)."""
    quick = (mode or "quick").lower() != "deep"
    file_cap = 100 if quick else 1000
    try:
        target = validate_path(target_path)
        if not target.exists():
            return f"Path not found: {target_path}"

        from code_intelligence import _LANGUAGE_MAP, compute_file_complexity_ast

        results = ["# CODE COMPLEXITY ANALYSIS\n"]
        high_complexity: list[tuple[str, str, int]] = []
        total_complexity = 0
        total_functions = 0
        file_count = 0
        lang_counts: dict[str, int] = {}

        supported_exts = set(_LANGUAGE_MAP.keys())
        all_files: list[Path] = []
        for root_dir, dir_names, filenames in os.walk(target):
            dir_names[:] = [d for d in dir_names if not is_dir_ignored(d)]
            for fname in filenames:
                fpath = Path(root_dir) / fname
                if fpath.suffix.lower() in supported_exts:
                    all_files.append(fpath)

        if not all_files:
            return "No supported files found (Python, JS, TS, Java, Go, Rust, Ruby)"

        import time

        from config import get_tool_budget_seconds

        time_budget = get_tool_budget_seconds()
        start = time.monotonic()
        stopped_early = False

        for src_file in all_files[:file_cap]:
            if file_count > 0 and (time.monotonic() - start) > time_budget:
                stopped_early = True
                break
            lang = _LANGUAGE_MAP.get(src_file.suffix.lower(), "unknown")

            if lang == "python":
                try:
                    from radon.complexity import cc_visit

                    code = src_file.read_text(encoding="utf-8", errors="replace")
                    cc_results = cc_visit(code)
                    if cc_results:
                        for item in cc_results:
                            if item.complexity > 10:
                                high_complexity.append((str(src_file), item.name, item.complexity))
                            total_complexity += item.complexity
                            total_functions += 1
                        file_count += 1
                        lang_counts[lang] = lang_counts.get(lang, 0) + 1
                    continue
                except (ImportError, Exception):
                    pass

            funcs = compute_file_complexity_ast(src_file)
            if funcs:
                for name, _line, complexity in funcs:
                    if complexity > 10:
                        high_complexity.append((str(src_file), name, complexity))
                    total_complexity += complexity
                    total_functions += 1
                file_count += 1
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

        if not file_count:
            return "# CODE COMPLEXITY ANALYSIS\n\nNo functions found to analyze"

        if high_complexity:
            results.append("## High Complexity Functions (>10)")
            high_complexity.sort(key=lambda x: x[2], reverse=True)
            for file, name, complexity in high_complexity[:20]:
                results.append(f"- `{file}:{name}` — Complexity: {complexity}")
            results.append("")

        avg = total_complexity / total_functions if total_functions else 0
        results.append("## Summary")
        if stopped_early:
            results.append(
                f"_Stopped after {file_count}/{min(len(all_files), file_cap)} files "
                f"(~{time_budget:.0f}s budget) to avoid a timeout. Raise "
                "PROJECTMIND_TOOL_BUDGET_SECONDS or narrow target_path for full coverage._\n"
            )
        results.append(f"- Files analyzed: {file_count}")
        results.append(f"- Functions analyzed: {total_functions}")
        results.append(f"- High complexity (>10): {len(high_complexity)}")
        results.append(f"- Average complexity: {avg:.2f}")
        results.append(
            f"- By language: {', '.join(f'{lang}: {cnt}' for lang, cnt in sorted(lang_counts.items()))}"
        )

        return "\n".join(results)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error analyzing complexity: {e}"


@mcp.tool()
def analyze_code_quality(target_path: str = ".", max_files: int = 10, mode: str = "quick") -> str:
    """Pylint-based quality scan. mode='deep' raises max_files cap to 100."""
    if (mode or "quick").lower() == "deep":
        max_files = max(max_files, 100)
    try:
        import pylint  # noqa: F401
    except ImportError:
        return "Error: pylint not installed. Run: pip install pylint"

    try:
        target = validate_path(target_path)
        if not target.exists():
            return f"Path not found: {target_path}"

        py_files: list[Path] = []
        for root_dir, dir_names, filenames in os.walk(target):
            dir_names[:] = [d for d in dir_names if not is_dir_ignored(d)]
            for fname in filenames:
                if fname.endswith(".py"):
                    py_files.append(Path(root_dir) / fname)

        if not py_files:
            return "No Python files found"

        results = []
        results.append("# CODE QUALITY ANALYSIS\n")

        files_to_check = py_files[:max_files]
        results.append(f"Analyzing {len(files_to_check)} files...\n")

        issues_summary = {"convention": 0, "refactor": 0, "warning": 0, "error": 0}

        import json as _json
        import subprocess
        import time

        from config import get_tool_budget_seconds

        time_budget = get_tool_budget_seconds()
        start = time.monotonic()
        analyzed = 0
        stopped_early = False

        # pylint runs in a SUBPROCESS: running it in-process required swapping
        # the global sys.stdout, which risks corrupting the MCP stdio protocol
        # when any other thread writes concurrently.
        batch_size = 10
        for batch_start in range(0, len(files_to_check), batch_size):
            remaining = time_budget - (time.monotonic() - start)
            if analyzed > 0 and remaining <= 0:
                stopped_early = True
                break
            batch = files_to_check[batch_start : batch_start + batch_size]
            cmd = [
                sys.executable,
                "-m",
                "pylint",
                "--output-format=json",
                "--score=n",
                *[str(f) for f in batch],
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=max(remaining, 10.0),
                    cwd=str(config.PROJECT_ROOT),
                )
            except subprocess.TimeoutExpired:
                stopped_early = True
                break
            except Exception:
                continue

            try:
                messages = _json.loads(proc.stdout or "[]")
            except ValueError:
                messages = []
            for msg in messages:
                mtype = str(msg.get("type", "")).lower()
                if mtype == "fatal":
                    mtype = "error"
                if mtype in issues_summary:
                    issues_summary[mtype] += 1
            analyzed += len(batch)

        if stopped_early:
            results.append(
                f"_Stopped after {analyzed}/{len(files_to_check)} files "
                f"(~{time_budget:.0f}s budget) to avoid a timeout. pylint is slow "
                "per file; raise PROJECTMIND_TOOL_BUDGET_SECONDS or narrow "
                "target_path for full coverage._\n"
            )
        results.append("## Issues Summary")
        results.append(f"- Errors: {issues_summary['error']}")
        results.append(f"- Warnings: {issues_summary['warning']}")
        results.append(f"- Refactoring suggestions: {issues_summary['refactor']}")
        results.append(f"- Convention issues: {issues_summary['convention']}")

        total_issues = sum(issues_summary.values())
        if total_issues > 0:
            results.append(f"\n**Total issues found**: {total_issues}")
            results.append("\nRun pylint directly for detailed reports.")
        else:
            results.append("\nNo major issues found!")

        return "\n".join(results)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error analyzing quality: {e}"


@mcp.tool()
def get_test_coverage_info() -> str:
    try:
        coverage_file = config.PROJECT_ROOT / ".coverage"
        htmlcov_dir = config.PROJECT_ROOT / "htmlcov"

        if not coverage_file.exists() and not htmlcov_dir.exists():
            return "No coverage data found. Run: pytest --cov=. --cov-report=html"

        results = []
        results.append("# TEST COVERAGE INFO\n")

        if htmlcov_dir.exists():
            index_file = htmlcov_dir / "index.html"
            if index_file.exists():
                content = index_file.read_text()

                if "pc_cov" in content:
                    import re

                    match = re.search(r'<span class="pc_cov">(\d+)%</span>', content)
                    if match:
                        coverage = match.group(1)
                        results.append(f"**Overall Coverage**: {coverage}%\n")

                results.append("Coverage report available at: htmlcov/index.html")

        if coverage_file.exists():
            results.append(f"\nCoverage data file found: {coverage_file}")
            results.append("Run: coverage report")

        return "\n".join(results) if results else "Coverage data exists but couldn't parse it"
    except Exception as e:
        return f"Error reading coverage: {e}"


@mcp.tool()
def save_memory_version(description: str = "") -> str:
    # Use direct MemoryManager to avoid vector store initialization
    from memory_manager import MemoryManager

    mm = MemoryManager()
    return mm.save_version(description)


@mcp.tool()
def list_memory_versions() -> str:
    # Use direct MemoryManager to avoid vector store initialization
    from memory_manager import MemoryManager

    mm = MemoryManager()
    return mm.list_versions()


@mcp.tool()
def restore_memory_version(timestamp: str) -> str:
    # Use direct MemoryManager to avoid vector store initialization
    from memory_manager import MemoryManager

    mm = MemoryManager()
    return mm.restore_version(timestamp)


@mcp.tool()
def get_cache_stats() -> str:
    """
    Returns performance statistics for all caches.

    Returns:
        Formatted string with cache statistics
    """
    ctx = get_context()
    file_stats = get_file_cache_stats()
    query_stats = ctx.vector_store.get_query_cache_stats()

    result = "# CACHE STATISTICS\n\n"
    result += "## File Cache (safe_read_text)\n"
    result += f"- **Hits**: {file_stats['hits']}\n"
    result += f"- **Misses**: {file_stats['misses']}\n"
    result += f"- **Hit Rate**: {file_stats['hit_rate']}\n"
    result += f"- **Size**: {file_stats['size']}/{file_stats['capacity']}\n\n"

    result += "## Query Cache (vector search)\n"
    result += f"- **Hits**: {query_stats['hits']}\n"
    result += f"- **Misses**: {query_stats['misses']}\n"
    result += f"- **Hit Rate**: {query_stats['hit_rate']}\n"
    result += f"- **Size**: {query_stats['size']}/{query_stats['max_size']}\n"
    result += f"- **Expirations**: {query_stats['expirations']}\n"
    result += f"- **TTL**: {query_stats['ttl_seconds']}s\n"

    return result


@mcp.tool()
def query(text: str, intent: str = "lookup", n_results: int = 8) -> str:
    """
    Tier-aware project query. Cheaper than `search_codebase`.

    Routes through L0 (manifest), then L1 (BM25), then L2 (vector embeddings),
    escalating only when the previous tier yields a weak signal.

    Args:
        text: Free-form query.
        intent: "overview" | "lookup" | "semantic" | "deep".
                - overview: L0 only — paths and symbols. Always sub-second.
                - lookup:   L0 + L1 — keyword/lexical search.
                - semantic: L0 + L1 + L2 — pulls in embeddings if signal is weak.
                - deep:     L0 + L1 + L2 — relaxed thresholds, more results.
        n_results: Final number of merged hits to return (1-50).
    """
    n_results = max(1, min(int(n_results or 8), 50))
    try:
        from query_router import query as _q

        result = _q(text, intent=intent, n_results=n_results)
        return result.to_markdown()
    except Exception as e:
        return f"Error in query(): {e}"


@mcp.tool()
def read_memory_index() -> str:
    """
    Returns just the section headings from `.ai/memory.md` plus a short tip.

    Use this for an initial cheap scan; then call `read_memory_section(name)`
    to expand only the section you actually need.
    """
    if not config.MEMORY_FILE.exists():
        return "Memory file not found."
    return _memory_index_markdown()


@mcp.tool()
def read_memory_section(section_name: str, max_lines: int = 200) -> str:
    """
    Returns the contents of a single `## Heading` section from memory.md.

    Args:
        section_name: Case-insensitive section heading (without leading `#`).
        max_lines: Max lines of body to return (truncated with a tip).
    """
    if not section_name or not section_name.strip():
        return "Error: section_name is required."
    if not config.MEMORY_FILE.exists():
        return "Memory file not found."
    try:
        text = config.MEMORY_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"Error reading memory: {e}"

    target = section_name.strip().lower()
    lines = text.split("\n")
    body: list[str] = []
    in_section = False
    section_level = 0
    for ln in lines:
        if ln.startswith("#"):
            stripped = ln.lstrip("#").strip()
            level = len(ln) - len(ln.lstrip("#"))
            if in_section and level <= section_level:
                break
            if not in_section and stripped.lower() == target:
                in_section = True
                section_level = level
                body.append(ln)
                continue
        if in_section:
            body.append(ln)
    if not body:
        return f"Section '{section_name}' not found. Use `read_memory_index()` to list."
    if len(body) > max_lines:
        kept = "\n".join(body[:max_lines])
        return (
            kept + f"\n\n_... {len(body) - max_lines} more lines truncated. "
            "Re-call with larger `max_lines` or use `read_memory(max_lines=None)`._"
        )
    return "\n".join(body)


@mcp.tool()
def maintenance_status() -> str:
    """Reports the self-healing daemon's status, schedule, and recent history."""
    try:
        from maintenance import get_status

        s = get_status()
        lines = ["# MAINTENANCE STATUS"]
        lines.append(f"- **Daemon alive**: {s['daemon_alive']}")
        lines.append(f"- **Vector DB**: {s['vector_db_mb']} MB")
        lines.append(f"- **Log**: {s['log_mb']} MB")
        lines.append(f"- **Process RSS**: {s['process_rss_mb']} MB")
        lines.append("\n## Schedule")
        for t in s["schedule"]:
            age = t["last_run_age_s"]
            lines.append(
                f"- `{t['task']}` — last {age}s ago, next in {t['next_in_s']}s "
                f"(interval {t['interval_s']}s)"
            )
        if s["recent_history"]:
            lines.append("\n## Recent")
            for h in s["recent_history"][:10]:
                ok = "OK" if h["ok"] else "FAIL"
                lines.append(f"- [{ok}] `{h['task']}`: {h['detail']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def maintenance_run() -> str:
    """Synchronously run every self-healing task once. Returns a per-task report."""
    try:
        from maintenance import run_all_now

        results = run_all_now()
        out = ["# MAINTENANCE RUN"]
        for k, v in results.items():
            out.append(f"- **{k}**: {v}")
        return "\n".join(out)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def prune_index(force: bool = False) -> str:
    """
    Aggressively prunes the vector index: drops orphan chunks and runs VACUUM.
    Pass `force=True` to delete and rebuild the entire collection from scratch.
    """
    from maintenance import load_state, save_state, task_compact_db, task_gc_stale_chunks

    state = load_state()
    out = ["# PRUNE INDEX"]
    if force:
        try:
            ctx = get_context()
            err = ctx.vector_store.clear_collection()
            if err:
                out.append(f"- clear: {err}")
            else:
                # Reset incremental-index metadata too, otherwise the next
                # index_changed_files() sees "no changed files" on an empty store.
                try:
                    from incremental_indexing import IndexMetadata

                    meta = IndexMetadata()
                    meta.metadata = {}
                    meta.save()
                    out.append("- index metadata reset")
                except Exception as e:
                    out.append(f"- index metadata reset failed: {e}")
                out.append("- collection cleared (run `index_codebase()` to rebuild)")
        except Exception as e:
            out.append(f"- clear failed: {e}")
    out.append(f"- gc: {task_gc_stale_chunks(state)}")
    out.append(f"- vacuum: {task_compact_db(state)}")
    save_state(state)
    return "\n".join(out)


def _ensure_default_indexignore() -> None:
    """Creates a sensible default `.indexignore` if none exists, only on first run."""
    target = config.AI_DIR / ".indexignore"
    root_target = config.PROJECT_ROOT / ".indexignore"
    if root_target.exists() or target.exists():
        return
    try:
        config.AI_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "# ProjectMind index-ignore patterns (substring match on full path).\n"
            "# Edit freely; one pattern per line.\n"
            "node_modules\n"
            ".next\n"
            "dist\n"
            "build\n"
            "coverage\n"
            ".cache\n"
            ".turbo\n"
            ".vercel\n"
            "playwright-report\n"
            "__snapshots__\n"
            ".pytest_cache\n"
            ".mypy_cache\n"
            "*.min.js\n"
            "*.min.css\n"
            "package-lock.json\n"
            "yarn.lock\n"
            "pnpm-lock.yaml\n",
            encoding="utf-8",
        )
        log(f"Default .indexignore created at {target}")
    except Exception as e:
        log(f"Could not create default .indexignore: {e}")


def main() -> None:
    """Console entry point (`uvx projectmind-mcp` / `projectmind`)."""
    ensure_startup()
    _ensure_default_indexignore()
    try:
        from maintenance import start_daemon

        start_daemon()
    except Exception as e:
        log(f"Maintenance daemon could not be started: {e}")
    mcp.run()


if __name__ == "__main__":
    main()
