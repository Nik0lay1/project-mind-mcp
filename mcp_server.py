import os
import sys
import threading
from pathlib import Path
from time import time

from mcp.server.fastmcp import FastMCP

import config
from config import (
    get_file_cache_stats,
    get_ignored_dirs,
    is_dir_ignored,
    reconfigure,
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
                content = gitignore_path.read_text()
                if ".ai/" in content or ".ai" in content:
                    ai_ignored = True
                if "__pycache__" in content:
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

    reconfigure(target)
    reset_context()
    _startup_done = False
    ensure_startup()
    log(f"Project root changed to: {config.PROJECT_ROOT}")
    return f"Project root set to: {config.PROJECT_ROOT}"


def load_index_ignore_patterns() -> set[str]:
    if not config.INDEX_IGNORE_FILE.exists():
        return set()

    try:
        patterns = set()
        with open(config.INDEX_IGNORE_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.add(line)
        return patterns
    except Exception as e:
        log(f"Error reading .indexignore: {e}")
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
        root = config.PROJECT_ROOT
        overview = [f"# PROJECT OVERVIEW: {root.name}\n"]
        overview.append(f"**Root**: `{root}`")

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

    memory_mentions = _search_memory_for(header.split("/")[-1] if "/" in header else header)
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

    if target.suffix in config.BINARY_EXTENSIONS:
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

    try:
        # First do semantic search
        ctx = get_context()
        coll = ctx.vector_store.get_collection()

        if coll is None:
            return "Vector store not initialized. Run index_codebase() first."

        results = ctx.vector_store.query(query_texts=[query], n_results=n_results)

        if not results or not results.get("documents") or not results["documents"][0]:
            return "No results found"

        # Extract matching files
        metadatas = results.get("metadatas", [[]])[0]
        matching_files = set()
        for meta in metadatas:
            if "file_path" in meta:
                matching_files.add(meta["file_path"])

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

    try:
        ctx = get_context()
        coll = ctx.vector_store.get_collection()

        if coll is None:
            return "Vector store not initialized. Run index_codebase() first."

        # Combine error and stacktrace for better search
        full_query = error_text
        if stacktrace:
            full_query = f"{error_text} {stacktrace}"

        # Search in code
        code_results = ctx.vector_store.query(query_texts=[full_query], n_results=n_results)

        # Search specifically for exception handling
        exception_query = f"exception error handling try catch {error_text}"
        exception_results = ctx.vector_store.query(
            query_texts=[exception_query], n_results=n_results
        )

        # Search for tests
        test_query = f"test {error_text}"
        test_results = ctx.vector_store.query(query_texts=[test_query], n_results=n_results)

        lines = ["# ERROR DEBUGGING SEARCH\n"]
        lines.append(f"Error: {error_text}\n")

        # Code matches
        if code_results and code_results.get("documents") and code_results["documents"][0]:
            metadatas = code_results.get("metadatas", [[]])[0]
            files = {meta.get("file_path", "") for meta in metadatas if meta.get("file_path")}

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
            files = {meta.get("file_path", "") for meta in metadatas if meta.get("file_path")}

            lines.append("## Error Handlers")
            for file in sorted(files):
                lines.append(f"- `{file}`")
            lines.append("")

        # Test matches
        if test_results and test_results.get("documents") and test_results["documents"][0]:
            metadatas = test_results.get("metadatas", [[]])[0]
            files = {meta.get("file_path", "") for meta in metadatas if meta.get("file_path")}
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
                        lines.append(f"- [{commit.sha[:7]}] {commit.first_line}")
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

    try:
        ctx = get_context()
        coll = ctx.vector_store.get_collection()

        if coll is None:
            return "Vector store not initialized. Run index_codebase() first."

        # Main search
        main_results = ctx.vector_store.query(query_texts=[feature_name], n_results=n_results)

        # Config search
        config_query = f"config configuration {feature_name}"
        config_results = ctx.vector_store.query(query_texts=[config_query], n_results=5)

        # Test search
        test_query = f"test {feature_name}"
        test_results = ctx.vector_store.query(query_texts=[test_query], n_results=5)

        lines = [f"# FEATURE SEARCH: {feature_name}\n"]

        # Main implementations
        if main_results and main_results.get("documents") and main_results["documents"][0]:
            metadatas = main_results.get("metadatas", [[]])[0]
            files = []
            for meta in metadatas:
                fp = meta.get("file_path", "")
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
            files = set()
            for meta in metadatas:
                fp = meta.get("file_path", "")
                if fp and any(
                    x in fp.lower()
                    for x in ["config", "settings", "env", ".json", ".yaml", ".toml"]
                ):
                    files.add(fp)

            if files:
                lines.append("## Configuration")
                for file in sorted(files):
                    lines.append(f"- `{file}`")
                lines.append("")

        # Tests
        if test_results and test_results.get("documents") and test_results["documents"][0]:
            metadatas = test_results.get("metadatas", [[]])[0]
            files = set()
            for meta in metadatas:
                fp = meta.get("file_path", "")
                if fp and ("test" in fp.lower() or "spec" in fp.lower()):
                    files.add(fp)

            if files:
                lines.append("## Tests")
                for file in sorted(files):
                    lines.append(f"- `{file}`")
                lines.append("")

        # Add dependency analysis if we found implementation files
        from code_intelligence import build_import_graph
        from code_intelligence import get_dependencies_with_depth as _get_deps

        if main_results and main_results.get("metadatas"):
            impl_files = [
                m.get("file_path") for m in main_results["metadatas"][0] if m.get("file_path")
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

    try:
        from code_intelligence import build_import_graph
        from code_intelligence import get_module_cluster as _get_cluster

        ctx = get_context()
        coll = ctx.vector_store.get_collection()

        if coll is None:
            return "Vector store not initialized. Run index_codebase() first."

        # Search for component
        results = ctx.vector_store.query(query_texts=[component], n_results=n_results)

        if not results or not results.get("documents") or not results["documents"][0]:
            return f"No results found for component: {component}"

        metadatas = results.get("metadatas", [[]])[0]
        main_files = [meta.get("file_path") for meta in metadatas if meta.get("file_path")]

        lines = [f"# ARCHITECTURE: {component}\n"]

        if main_files:
            lines.append("## Core Modules")
            for file in sorted(set(main_files)):
                lines.append(f"- `{file}`")
            lines.append("")

            # Build dependency graph
            graph = build_import_graph(config.PROJECT_ROOT)

            # Find related modules using clustering
            for file in main_files[:5]:
                if file in graph:
                    cluster = _get_cluster(
                        file, config.PROJECT_ROOT, similarity_threshold=0.4, max_cluster_size=10
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
        brief_parts: list[str] = []

        brief_parts.append(get_project_overview())
        brief_parts.append("")

        try:
            from code_intelligence import detect_conventions

            conventions = detect_conventions(config.PROJECT_ROOT)
            brief_parts.append(conventions)
            brief_parts.append("")
        except Exception:
            pass

        try:
            from code_intelligence import check_dependencies as _check_deps

            deps = _check_deps(config.PROJECT_ROOT)
            if "No dependency files" not in deps:
                brief_parts.append(deps)
                brief_parts.append("")
        except Exception:
            pass

        try:
            from code_intelligence import extract_todos

            todos = extract_todos(config.PROJECT_ROOT, max_files=2000)
            if "No TODO" not in todos:
                brief_parts.append(todos)
                brief_parts.append("")
        except Exception:
            pass

        full_brief = "\n".join(brief_parts)

        try:
            ctx = get_context()
            summary_lines = ["Auto-generated project onboarding brief."]

            try:
                from code_intelligence import detect_conventions

                conv = detect_conventions(config.PROJECT_ROOT)
                ctx.memory_manager.update(conv, section="Project Conventions")
            except Exception:
                pass

            try:
                from code_intelligence import check_dependencies as _check_deps

                deps = _check_deps(config.PROJECT_ROOT)
                if "No dependency files" not in deps:
                    ctx.memory_manager.update(deps, section="Dependencies")
            except Exception:
                pass

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
def index_codebase(force: bool = False) -> str:
    ctx = get_context()
    if ctx.vector_store.get_collection() is None:
        return "Failed to initialize vector store."

    root_dir = config.PROJECT_ROOT
    ignored_dirs = get_ignored_dirs()
    ignore_patterns = load_index_ignore_patterns()

    return ctx.indexer.index_all(root_dir, ignored_dirs, ignore_patterns, force)


@mcp.tool()
def search_codebase(query: str, n_results: int = 5) -> str:
    if not query or not query.strip():
        return "Error: Query cannot be empty."

    if n_results <= 0:
        return "Error: n_results must be greater than 0."

    if n_results > 50:
        return "Error: n_results cannot exceed 50."

    try:
        ctx = get_context()
        results = ctx.vector_store.query(query_texts=[query], n_results=n_results)

        if results is None:
            return "Vector store not initialized."

        if not results.get("documents") or not results["documents"][0]:
            return "No matches found."

        # Extract files and calculate coverage
        files = set()
        for meta in results.get("metadatas", [[]])[0]:
            if "file_path" in meta:
                files.add(meta["file_path"])

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
            file_path = meta.get("file_path", "")

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
    # Check if vector store exists without creating context
    vector_db_path = config.VECTOR_STORE_DIR / "chroma.sqlite3"
    if not vector_db_path.exists():
        return "Vector store not initialized. Run index_codebase() first."

    # Only create context if vector DB exists
    ctx = get_context()
    if not ctx.vector_store._initialized:
        return "Vector store not initialized. Run index_codebase() first."
    count = ctx.vector_store.get_count()
    if count is None:
        return "Vector store not initialized."
    return f"Vector store contains {count} chunks."


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
    ctx = get_context()
    if ctx.vector_store.get_collection() is None:
        return "Failed to initialize vector store."

    root_dir = config.PROJECT_ROOT
    ignored_dirs = get_ignored_dirs()
    ignore_patterns = load_index_ignore_patterns()

    return ctx.indexer.index_changed(root_dir, ignored_dirs, ignore_patterns)


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

    try:
        ctx = get_context()
        results = ctx.vector_store.query(query_texts=[query], n_results=n_results * 2)

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
def analyze_code_complexity(target_path: str = ".") -> str:
    try:
        from radon.complexity import cc_visit
    except ImportError:
        return "Error: radon not installed. Run: pip install radon"

    try:
        target = validate_path(target_path)
        if not target.exists():
            return f"Path not found: {target_path}"

        results = []
        results.append("# CODE COMPLEXITY ANALYSIS\n")

        py_files = list(target.rglob("*.py"))
        py_files = [
            f
            for f in py_files
            if not any(is_dir_ignored(p) for p in f.relative_to(target).parts[:-1])
        ]

        if not py_files:
            return "No Python files found"

        high_complexity = []
        total_complexity = 0
        file_count = 0

        for py_file in py_files[:50]:
            try:
                code = py_file.read_text(encoding="utf-8")

                complexity_results = cc_visit(code)
                if complexity_results:
                    for item in complexity_results:
                        if item.complexity > 10:
                            high_complexity.append((str(py_file), item.name, item.complexity))
                        total_complexity += item.complexity

                file_count += 1
            except Exception:
                continue

        if high_complexity:
            results.append("## High Complexity Functions (>10)")
            high_complexity.sort(key=lambda x: x[2], reverse=True)
            for file, name, complexity in high_complexity[:20]:
                results.append(f"- `{file}:{name}` - Complexity: {complexity}")

        avg_complexity = total_complexity / file_count if file_count > 0 else 0
        results.append("\n## Summary")
        results.append(f"- Files analyzed: {file_count}")
        results.append(f"- High complexity functions: {len(high_complexity)}")
        results.append(f"- Average complexity: {avg_complexity:.2f}")

        return "\n".join(results)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error analyzing complexity: {e}"


@mcp.tool()
def analyze_code_quality(target_path: str = ".", max_files: int = 10) -> str:
    try:
        from io import StringIO

        from pylint.lint import Run
    except ImportError:
        return "Error: pylint not installed. Run: pip install pylint"

    try:
        target = validate_path(target_path)
        if not target.exists():
            return f"Path not found: {target_path}"

        py_files = list(target.rglob("*.py"))
        py_files = [
            f
            for f in py_files
            if not any(is_dir_ignored(p) for p in f.relative_to(target).parts[:-1])
        ]

        if not py_files:
            return "No Python files found"

        results = []
        results.append("# CODE QUALITY ANALYSIS\n")

        files_to_check = py_files[:max_files]
        results.append(f"Analyzing {len(files_to_check)} files...\n")

        issues_summary = {"convention": 0, "refactor": 0, "warning": 0, "error": 0}

        for py_file in files_to_check:
            try:
                old_stdout = sys.stdout
                old_stderr = sys.stderr
                sys.stdout = StringIO()
                sys.stderr = StringIO()

                try:
                    pylint_output = Run([str(py_file), "--output-format=text"], exit=False)
                finally:
                    sys.stdout = old_stdout
                    sys.stderr = old_stderr

                stats = pylint_output.linter.stats
                if hasattr(stats, "convention"):
                    issues_summary["convention"] += stats.convention
                if hasattr(stats, "refactor"):
                    issues_summary["refactor"] += stats.refactor
                if hasattr(stats, "warning"):
                    issues_summary["warning"] += stats.warning
                if hasattr(stats, "error"):
                    issues_summary["error"] += stats.error

            except Exception:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
                continue

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


if __name__ == "__main__":
    ensure_startup()
    mcp.run()
