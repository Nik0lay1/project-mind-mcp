import fnmatch
import os
import sys
from pathlib import Path
from typing import Any

_MCP_SERVER_DIR = Path(__file__).resolve().parent


def find_project_root() -> Path:
    """
    Intelligently finds the project root directory.

    Detection strategy:
    1. Check command-line argument --project-root
    2. Check environment variables (WORKSPACE_FOLDER, PROJECT_ROOT, PROJECT_PATH)
    3. Search upward from CWD for project markers, skipping the MCP server's own directory,
       the user's home directory, and drive roots.
    4. Fall back to current working directory (if safe) or the MCP server's own directory.

    Returns:
        Path to project root directory
    """
    for i, arg in enumerate(sys.argv):
        if arg == "--project-root" and i + 1 < len(sys.argv):
            project_path = Path(sys.argv[i + 1]).resolve()
            if project_path.exists():
                return project_path

    for env_var in ["WORKSPACE_FOLDER", "PROJECT_ROOT", "PROJECT_PATH"]:
        if env_path := os.getenv(env_var):
            project_path = Path(env_path).resolve()
            if project_path.exists():
                return project_path

    try:
        home = Path.home().resolve()
    except Exception:
        home = None

    current = Path.cwd().resolve()
    project_markers = [
        ".git",
        ".ai",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        ".project",
        ".vscode",
    ]

    for _ in range(10):
        # Do not treat home directory or drive root as a project root
        if current != _MCP_SERVER_DIR and current != home and current.parent != current:
            for marker in project_markers:
                if (current / marker).exists():
                    return current

        parent = current.parent
        if parent == current:
            break
        current = parent

    # Fall back to CWD only if it is not the home directory or a drive root
    cwd = Path.cwd().resolve()
    if cwd != home and cwd.parent != cwd:
        return cwd
    return _MCP_SERVER_DIR


PROJECT_ROOT = find_project_root()

_file_cache = None

AI_DIR = PROJECT_ROOT / ".ai"
MEMORY_FILE = AI_DIR / "memory.md"
VECTOR_STORE_DIR = AI_DIR / "vector_store"
INDEX_IGNORE_FILE = AI_DIR / ".indexignore"
INDEX_METADATA_FILE = AI_DIR / "index_metadata.json"
# JSON on purpose: the file lives in the target project's .ai/ dir, so a
# pickle here would mean executing untrusted input on load.
BM25_INDEX_PATH = AI_DIR / "bm25_index.json"
MEMORY_HISTORY_DIR = AI_DIR / "memory_history"
LOG_FILE = AI_DIR / "projectmind.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

MCP_SERVER_DIR = _MCP_SERVER_DIR


def resolve_index_ignore_file() -> Path:
    """
    Resolves the active .indexignore file.

    Preference order:
    1. `<PROJECT_ROOT>/.indexignore`  (user-friendly, next to .gitignore)
    2. `<PROJECT_ROOT>/.ai/.indexignore`  (legacy location)
    """
    root_level = PROJECT_ROOT / ".indexignore"
    if root_level.exists():
        return root_level
    return INDEX_IGNORE_FILE


def reconfigure(new_root: Path) -> None:
    global PROJECT_ROOT, AI_DIR, MEMORY_FILE, VECTOR_STORE_DIR
    global INDEX_IGNORE_FILE, INDEX_METADATA_FILE, BM25_INDEX_PATH, MEMORY_HISTORY_DIR, LOG_FILE
    PROJECT_ROOT = new_root.resolve()
    AI_DIR = PROJECT_ROOT / ".ai"
    MEMORY_FILE = AI_DIR / "memory.md"
    VECTOR_STORE_DIR = AI_DIR / "vector_store"
    INDEX_IGNORE_FILE = AI_DIR / ".indexignore"
    INDEX_METADATA_FILE = AI_DIR / "index_metadata.json"
    BM25_INDEX_PATH = AI_DIR / "bm25_index.json"
    MEMORY_HISTORY_DIR = AI_DIR / "memory_history"
    LOG_FILE = AI_DIR / "projectmind.log"


def is_mcp_server_dir(path: Path) -> bool:
    """Returns True if `path` is the directory where ProjectMind MCP itself lives."""
    try:
        return path.resolve() == MCP_SERVER_DIR
    except OSError:
        return False


MODEL_NAME = "flax-sentence-embeddings/st-codesearch-distilroberta-base"

# Pinned model commit. Without it sentence-transformers resolves `main` on every
# load, and when upstream moves the branch the Hub treats it as a new revision:
# on Windows (no symlinks) that means re-downloading the full 328 MB weights
# into a second cache snapshot, silently, mid-session. A pinned revision also
# keeps embeddings reproducible — the same code cannot suddenly index against
# different weights. Override with PROJECTMIND_MODEL_REVISION, or set it to
# "main" to opt back into whatever upstream currently publishes.
MODEL_REVISION = "65b0f39bfa41c59993f62b57447c942e371b7135"

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 150
BATCH_SIZE = 100
MAX_FILE_SIZE_MB = 10
MAX_MEMORY_MB = 100
IMPORT_GRAPH_MAX_FILES = 8000
TOOL_SOFT_BUDGET_SECONDS = 20

DEFAULT_IGNORED_DIRS: set[str] = {
    ".git",
    "node_modules",
    ".ai",
    "venv",
    ".venv",
    "__pycache__",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "target",
    "vendor",
    "bin",
    "obj",
    "out",
    "logs",
    "tmp",
    "temp",
    ".cache",
    ".gradle",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    ".coverage",
    ".tox",
}

IGNORED_DIR_PATTERNS: set[str] = {
    "*.egg-info",
}

BINARY_EXTENSIONS: set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".class",
    ".exe",
    ".bin",
    ".obj",
    ".o",
    ".a",
    ".lib",
    ".dylib",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".ico",
    ".svg",
    ".mp4",
    ".mp3",
    ".wav",
    ".avi",
    ".mov",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
}

CODE_EXTENSIONS: set[str] = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".r",
    ".m",
    ".mm",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
}

TEXT_EXTENSIONS: set[str] = {
    ".txt",
    ".md",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".css",
    ".scss",
    ".sass",
    ".sql",
    ".graphql",
    ".proto",
}

INDEXABLE_EXTENSIONS = CODE_EXTENSIONS | TEXT_EXTENSIONS


def get_max_file_size_bytes() -> int:
    env_size = os.getenv("PROJECTMIND_MAX_FILE_SIZE_MB")
    if env_size:
        try:
            return int(env_size) * 1024 * 1024
        except ValueError:
            pass
    return MAX_FILE_SIZE_MB * 1024 * 1024


def get_model_revision() -> str:
    """Model commit to load. Override via PROJECTMIND_MODEL_REVISION."""
    return os.getenv("PROJECTMIND_MODEL_REVISION") or MODEL_REVISION


def get_max_memory_bytes() -> int:
    """
    Get maximum memory limit for document processing in bytes.
    Can be overridden via PROJECTMIND_MAX_MEMORY_MB environment variable.
    """
    env_size = os.getenv("PROJECTMIND_MAX_MEMORY_MB")
    if env_size:
        try:
            return int(env_size) * 1024 * 1024
        except ValueError:
            pass
    return MAX_MEMORY_MB * 1024 * 1024


def get_import_graph_max_files() -> int:
    """
    Maximum number of code files scanned when building the import graph.
    Can be overridden via PROJECTMIND_IMPORT_GRAPH_MAX_FILES environment variable.
    """
    env_val = os.getenv("PROJECTMIND_IMPORT_GRAPH_MAX_FILES")
    if env_val:
        try:
            parsed = int(env_val)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return IMPORT_GRAPH_MAX_FILES


def get_tool_budget_seconds() -> float:
    """
    Soft wall-clock budget (seconds) for long-running analysis tools so they
    return partial results instead of timing out the MCP call.
    Can be overridden via PROJECTMIND_TOOL_BUDGET_SECONDS environment variable.
    """
    env_val = os.getenv("PROJECTMIND_TOOL_BUDGET_SECONDS")
    if env_val:
        try:
            parsed = float(env_val)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return float(TOOL_SOFT_BUDGET_SECONDS)


def get_ignored_dirs() -> set[str]:
    return DEFAULT_IGNORED_DIRS.copy()


def is_dir_ignored(dir_name: str) -> bool:
    if dir_name in DEFAULT_IGNORED_DIRS:
        return True
    return any(fnmatch.fnmatch(dir_name, pat) for pat in IGNORED_DIR_PATTERNS)


def validate_path(path: str) -> Path:
    """
    Validates that a path is within the project root directory.
    Prevents path traversal attacks.

    Args:
        path: Path string to validate. "." refers to PROJECT_ROOT.

    Returns:
        Resolved Path object

    Raises:
        ValueError: If path is outside project root or invalid
    """
    try:
        if not path or not isinstance(path, str):
            raise ValueError("Path must be a non-empty string")

        # Handle "." as project root, not current working directory
        if path == ".":
            return PROJECT_ROOT

        # Resolve path relative to PROJECT_ROOT if it's not absolute
        if not Path(path).is_absolute():
            target_path = (PROJECT_ROOT / path).resolve()
        else:
            target_path = Path(path).resolve()

        if not target_path.is_relative_to(PROJECT_ROOT):
            raise ValueError(
                f"Path '{path}' is outside project root. "
                f"Only paths within {PROJECT_ROOT} are allowed."
            )

        return target_path
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Invalid path '{path}': {e}") from e


def safe_read_text(file_path: Path) -> str:
    """
    Safely reads text file with automatic encoding detection.
    Tries multiple encodings instead of ignoring errors.
    Uses FileCache for improved performance.

    Args:
        file_path: Path to the file to read

    Returns:
        File content as string

    Raises:
        UnicodeDecodeError: If file cannot be decoded with any supported encoding
        IOError: If file cannot be read
    """
    global _file_cache
    if _file_cache is None:
        from cache_manager import FileCache

        _file_cache = FileCache(capacity=50)

    cached_content = _file_cache.get(file_path)
    if cached_content is not None:
        return cached_content

    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"]

    for encoding in encodings:
        try:
            content = file_path.read_text(encoding=encoding)
            _file_cache.put(file_path, content)
            return content
        except UnicodeDecodeError:
            continue
        except Exception as e:
            raise OSError(f"Error reading file {file_path}: {e}") from e

    raise UnicodeDecodeError(
        "multi-encoding",
        b"",
        0,
        1,
        f"Cannot decode {file_path} with any supported encoding: {encodings}",
    )


def get_file_cache_stats() -> dict[str, Any]:
    """
    Returns file cache statistics.

    Returns:
        Dictionary with cache statistics
    """
    global _file_cache
    if _file_cache is None:
        from cache_manager import FileCache

        _file_cache = FileCache(capacity=50)
    return _file_cache.get_stats()
