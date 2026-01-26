import os
from pathlib import Path
from typing import Set

PROJECT_ROOT = Path.cwd().resolve()

AI_DIR = Path(".ai")
MEMORY_FILE = AI_DIR / "memory.md"
VECTOR_STORE_DIR = AI_DIR / "vector_store"
INDEX_IGNORE_FILE = AI_DIR / ".indexignore"
INDEX_METADATA_FILE = AI_DIR / "index_metadata.json"
MEMORY_HISTORY_DIR = AI_DIR / "memory_history"
LOG_FILE = AI_DIR / "projectmind.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

MODEL_NAME = "all-MiniLM-L6-v2"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
BATCH_SIZE = 100
MAX_FILE_SIZE_MB = 10
MAX_MEMORY_MB = 100

DEFAULT_IGNORED_DIRS: Set[str] = {
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
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    ".coverage",
    ".tox",
    "*.egg-info",
}

BINARY_EXTENSIONS: Set[str] = {
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

CODE_EXTENSIONS: Set[str] = {
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

TEXT_EXTENSIONS: Set[str] = {
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


def get_ignored_dirs() -> Set[str]:
    return DEFAULT_IGNORED_DIRS.copy()


def validate_path(path: str) -> Path:
    """
    Validates that a path is within the project root directory.
    Prevents path traversal attacks.
    
    Args:
        path: Path string to validate
        
    Returns:
        Resolved Path object
        
    Raises:
        ValueError: If path is outside project root or invalid
    """
    try:
        if not path or not isinstance(path, str):
            raise ValueError("Path must be a non-empty string")
        
        target_path = Path(path).resolve()
        
        if not target_path.is_relative_to(PROJECT_ROOT):
            raise ValueError(
                f"Path '{path}' is outside project root. "
                f"Only paths within {PROJECT_ROOT} are allowed."
            )
        
        return target_path
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Invalid path '{path}': {e}")


def safe_read_text(file_path: Path) -> str:
    """
    Safely reads text file with automatic encoding detection.
    Tries multiple encodings instead of ignoring errors.
    
    Args:
        file_path: Path to the file to read
        
    Returns:
        File content as string
        
    Raises:
        UnicodeDecodeError: If file cannot be decoded with any supported encoding
        IOError: If file cannot be read
    """
    encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252', 'iso-8859-1']
    
    for encoding in encodings:
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except Exception as e:
            raise IOError(f"Error reading file {file_path}: {e}")
    
    raise UnicodeDecodeError(
        'multi-encoding',
        b'',
        0,
        1,
        f"Cannot decode {file_path} with any supported encoding: {encodings}"
    )
