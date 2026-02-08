import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from time import time

from mcp.server.fastmcp import FastMCP

from config import (
    AI_DIR,
    INDEX_IGNORE_FILE,
    MEMORY_FILE,
    PROJECT_ROOT,
    get_file_cache_stats,
    get_ignored_dirs,
    validate_path,
)
from context import get_context
from exceptions import GitError
from git_utils import GitRepository
from logger import setup_logger

logger = setup_logger()


def log(message: str) -> None:
    """Backward compatible log function"""
    logger.info(message)


def startup_check() -> None:
    log("=" * 60)
    log("ProjectMind MCP Server Starting...")
    log(f"Project Root (detected): {PROJECT_ROOT}")
    log(f"Current Working Directory: {Path.cwd()}")
    log(f"MCP Server Location: {Path(__file__).parent}")
    log("=" * 60)

    try:
        if not AI_DIR.exists():
            AI_DIR.mkdir(parents=True)
            log(f"Created {AI_DIR}")
    except (OSError, PermissionError) as e:
        log(f"Warning: Could not create {AI_DIR}: {e}. Server will continue if directory exists.")

    try:
        gitignore_path = PROJECT_ROOT / ".gitignore"
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
        if not MEMORY_FILE.exists():
            template = """# Project Memory

## Status
- [ ] Initial Setup

## Tech Stack
- Language: Python
- Framework:

## Recent Decisions
- Project initialized.
"""
            MEMORY_FILE.write_text(template)
            log(f"Created {MEMORY_FILE}")
    except (OSError, PermissionError) as e:
        log(f"Warning: Could not create {MEMORY_FILE}: {e}")


startup_check()

mcp = FastMCP("ProjectMind")


def load_index_ignore_patterns() -> set[str]:
    if not INDEX_IGNORE_FILE.exists():
        return set()

    try:
        patterns = set()
        with open(INDEX_IGNORE_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.add(line)
        return patterns
    except Exception as e:
        log(f"Error reading .indexignore: {e}")
        return set()


@mcp.resource("project://memory")
def get_project_memory() -> str:
    ctx = get_context()
    return ctx.memory_manager.read()


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

    ctx = get_context()
    return ctx.memory_manager.read(max_lines=max_lines)


@mcp.tool()
def update_memory(content: str, section: str = "Recent Decisions") -> str:
    ctx = get_context()
    return ctx.memory_manager.update(content, section)


@mcp.tool()
def clear_memory(keep_template: bool = True) -> str:
    ctx = get_context()
    return ctx.memory_manager.clear(keep_template)


@mcp.tool()
def delete_memory_section(section_name: str) -> str:
    ctx = get_context()
    return ctx.memory_manager.delete_section(section_name)


@mcp.tool()
def index_codebase(force: bool = False) -> str:
    ctx = get_context()
    if ctx.vector_store.get_collection() is None:
        return "Failed to initialize vector store."

    root_dir = PROJECT_ROOT
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

        output = []
        if results["documents"]:
            for i in range(len(results["documents"][0])):
                doc = results["documents"][0][i]
                meta = results["metadatas"][0][i]
                source = meta.get("source", "unknown")
                output.append(f"--- {source} ---\n{doc}\n")

        return "\n".join(output) if output else "No matches found."
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

    if not MEMORY_FILE.exists():
        return "Memory file not found."

    try:
        current_memory = MEMORY_FILE.read_text()

        header = "## Development Log (Git)"
        if header not in current_memory:
            with open(MEMORY_FILE, "a") as f:
                f.write(f"\n\n{header}\n")
            current_memory += f"\n\n{header}\n"

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

        with open(MEMORY_FILE, "a") as f:
            for entry in new_entries:
                f.write(f"{entry}\n")

        return f"Ingested {len(new_entries)} new commits into memory."
    except Exception as e:
        log(f"Error ingesting git history: {e}")
        return f"Error ingesting git history: {e}"


@mcp.tool()
def get_index_stats() -> str:
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

        root = PROJECT_ROOT
        ignored_dirs = get_ignored_dirs()
        py_files = 0
        js_files = 0

        for _root_path, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ignored_dirs]
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

        pyproject_path = PROJECT_ROOT / "pyproject.toml"
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

        requirements_path = PROJECT_ROOT / "requirements.txt"
        if not tech_stack and requirements_path.exists():
            content = requirements_path.read_text()
            tech_stack.append("## Python Project")
            tech_stack.append("\n**Dependencies:**")
            for line in content.split("\n"):
                if line.strip() and not line.startswith("#"):
                    tech_stack.append(f"- {line.strip()}")

        package_json_path = PROJECT_ROOT / "package.json"
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

        cargo_path = PROJECT_ROOT / "Cargo.toml"
        if cargo_path.exists():
            tech_stack.append("\n## Rust Project")

        gomod_path = PROJECT_ROOT / "go.mod"
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
        root = PROJECT_ROOT
        ignored_dirs = get_ignored_dirs()

        structure = []
        structure.append("# PROJECT STRUCTURE\n")

        dirs_by_depth = {}
        for item in root.iterdir():
            if item.is_dir() and item.name not in ignored_dirs:
                try:
                    count = sum(1 for _ in item.rglob("*") if _.is_file())
                    dirs_by_depth[item.name] = count
                except (PermissionError, OSError):
                    continue

        sorted_dirs = sorted(dirs_by_depth.items(), key=lambda x: x[1], reverse=True)[:10]

        structure.append("## Main Directories (by size)")
        for dir_name, count in sorted_dirs:
            structure.append(f"- `{dir_name}/` ({count} items)")

        file_types: dict[str, int] = {}
        for _root_path, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ignored_dirs]

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
            if Path(cfg).exists():
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

    root_dir = PROJECT_ROOT
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
        for exc_dir in exclude_dirs:
            if exc_dir in source:
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

                relevance = 1 - (distance / 2)

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
        ignored_dirs = get_ignored_dirs()
        py_files = [f for f in py_files if not any(ig in str(f) for ig in ignored_dirs)]

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
        ignored_dirs = get_ignored_dirs()
        py_files = [f for f in py_files if not any(ig in str(f) for ig in ignored_dirs)]

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
                sys.stdout = StringIO()

                pylint_output = Run([str(py_file), "--output-format=text"], exit=False)

                sys.stdout = old_stdout

                if hasattr(pylint_output.linter.stats, "by_msg"):
                    for msg_type in pylint_output.linter.stats.by_msg:
                        count = pylint_output.linter.stats.by_msg[msg_type]
                        if "convention" in msg_type.lower():
                            issues_summary["convention"] += count
                        elif "refactor" in msg_type.lower():
                            issues_summary["refactor"] += count
                        elif "warning" in msg_type.lower():
                            issues_summary["warning"] += count
                        elif "error" in msg_type.lower():
                            issues_summary["error"] += count

            except Exception:
                sys.stdout = old_stdout
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
            results.append("\n✅ No major issues found!")

        return "\n".join(results)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error analyzing quality: {e}"


@mcp.tool()
def get_test_coverage_info() -> str:
    try:
        coverage_file = Path(".coverage")
        htmlcov_dir = Path("htmlcov")

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
    ctx = get_context()
    return ctx.memory_manager.save_version(description)


@mcp.tool()
def list_memory_versions() -> str:
    ctx = get_context()
    return ctx.memory_manager.list_versions()


@mcp.tool()
def restore_memory_version(timestamp: str) -> str:
    ctx = get_context()
    return ctx.memory_manager.restore_version(timestamp)


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
def platform_get_client_errors(
    base_url: str,
    limit: int = 100,
    offset: int = 0,
    error_type: str = "all",
    since: str | None = None,
    user_id: str | None = None,
) -> str:
    """
    Get client-side errors from the Agency Platform.

    Args:
        base_url: Platform URL (e.g., https://copri.ai)
        limit: Maximum number of errors to return (default: 100, max: 500)
        offset: Pagination offset
        error_type: Filter by type: 'error', 'unhandledrejection', 'console.error', 'all'
        since: ISO timestamp to fetch errors since (optional)
        user_id: Filter by specific user ID (optional)

    Returns:
        JSON response with errors, pagination and stats
    """
    admin_token = os.environ.get("PLATFORM_ADMIN_TOKEN")
    admin_username = os.environ.get("AUTH_USERNAME", "ua-man")

    if not admin_token:
        return "Error: PLATFORM_ADMIN_TOKEN environment variable not set"

    if limit < 1 or limit > 500:
        return "Error: limit must be between 1 and 500"

    if error_type not in ("error", "unhandledrejection", "console.error", "all"):
        return "Error: error_type must be 'error', 'unhandledrejection', 'console.error', or 'all'"

    try:
        params: dict[str, str | int] = {
            "limit": limit,
            "offset": offset,
            "type": error_type,
        }
        if since:
            params["since"] = since
        if user_id:
            params["userId"] = user_id

        query_string = urllib.parse.urlencode(params)
        url = f"{base_url.rstrip('/')}/api/admin/client-errors?{query_string}"

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {admin_token}",
                "x-username": admin_username,
                "Content-Type": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

        result_parts = []
        result_parts.append("# CLIENT ERRORS REPORT\n")

        if "stats" in data and "last24h" in data["stats"]:
            stats = data["stats"]["last24h"]
            result_parts.append("## Statistics (Last 24h)")
            result_parts.append(f"- **Total errors**: {stats.get('total', 0)}")
            result_parts.append(f"- **Errors**: {stats.get('errors', 0)}")
            result_parts.append(f"- **Unhandled rejections**: {stats.get('rejections', 0)}")
            result_parts.append(f"- **Console errors**: {stats.get('consoleErrors', 0)}")
            result_parts.append(f"- **Affected users**: {stats.get('affectedUsers', 0)}")
            result_parts.append(f"- **Sessions**: {stats.get('sessions', 0)}")
            result_parts.append("")

        if "pagination" in data:
            pag = data["pagination"]
            result_parts.append(
                f"## Pagination: {pag.get('returned', 0)} returned "
                f"(offset: {pag.get('offset', 0)}, limit: {pag.get('limit', 100)})\n"
            )

        errors = data.get("errors", [])
        if errors:
            result_parts.append("## Errors\n")
            for i, err in enumerate(errors, 1):
                result_parts.append(f"### {i}. {err.get('errorType', 'unknown')} - {err.get('username', 'unknown')}")
                result_parts.append(f"- **Message**: {err.get('message', 'N/A')}")
                result_parts.append(f"- **URL**: {err.get('url', 'N/A')}")
                if err.get("line"):
                    result_parts.append(f"- **Location**: line {err.get('line')}, col {err.get('column')}")
                result_parts.append(f"- **Time**: {err.get('clientTimestamp', err.get('createdAt', 'N/A'))}")
                if err.get("stack"):
                    stack_preview = err["stack"][:500] + "..." if len(err["stack"]) > 500 else err["stack"]
                    result_parts.append(f"- **Stack**:\n```\n{stack_preview}\n```")
                result_parts.append("")
        else:
            result_parts.append("No errors found.")

        return "\n".join(result_parts)

    except urllib.error.HTTPError as e:
        return f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL Error: {e.reason}"
    except json.JSONDecodeError as e:
        return f"JSON Parse Error: {e}"
    except Exception as e:
        log(f"Error fetching client errors: {e}")
        return f"Error: {e}"


@mcp.tool()
def platform_delete_old_client_errors(
    base_url: str,
    older_than_days: int = 30,
) -> str:
    """
    Delete old client-side errors from the Agency Platform.

    Args:
        base_url: Platform URL (e.g., https://copri.ai)
        older_than_days: Delete errors older than this many days (default: 30)

    Returns:
        Result message
    """
    admin_token = os.environ.get("PLATFORM_ADMIN_TOKEN")
    admin_username = os.environ.get("AUTH_USERNAME", "ua-man")

    if not admin_token:
        return "Error: PLATFORM_ADMIN_TOKEN environment variable not set"

    if older_than_days < 1:
        return "Error: older_than_days must be at least 1"

    try:
        url = f"{base_url.rstrip('/')}/api/admin/client-errors?olderThanDays={older_than_days}"

        req = urllib.request.Request(
            url,
            method="DELETE",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "x-username": admin_username,
                "Content-Type": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

        if data.get("success"):
            deleted = data.get("deleted", 0)
            return f"Successfully deleted {deleted} errors older than {older_than_days} days"
        else:
            return f"Failed to delete errors: {data.get('error', 'Unknown error')}"

    except urllib.error.HTTPError as e:
        return f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL Error: {e.reason}"
    except Exception as e:
        log(f"Error deleting client errors: {e}")
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run()
