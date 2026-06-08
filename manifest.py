"""
L0 Manifest — fast, lightweight project manifest.

A single JSON file at `.ai/manifest.json` that captures:
  - File inventory (path, size, mtime, language, top-level symbols)
  - Module summary (top directories with counts and total size)
  - Project stats (file type distribution, hot paths, totals)

Built incrementally from filesystem walk. NEVER loads ChromaDB or
sentence-transformer model. Closes ~80% of "what is this project"
queries in < 50ms.

The manifest is rebuilt automatically when its mtime is older than the
oldest indexable file. Stale entries are pruned on every refresh.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import config
from logger import get_logger

logger = get_logger()

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
MAX_FILES_TO_SCAN = 20000
MAX_SYMBOLS_PER_FILE = 12
HOT_PATH_LIMIT = 15

_PY_TOP_LEVEL = re.compile(r"^(?:class|def|async def)\s+([A-Za-z_][\w]*)", re.MULTILINE)
_JS_TOP_LEVEL = re.compile(
    r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class|const|let|var)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_GENERIC_TOP_LEVEL = re.compile(
    r"^(?:public|private|protected|static|abstract|final|fn|func|class|struct|"
    r"interface|enum|trait|impl|type|def)\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)


def _detect_language(suffix: str) -> str:
    suffix = suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".java": "java",
        ".kt": "kotlin",
        ".scala": "scala",
        ".rb": "ruby",
        ".go": "go",
        ".rs": "rust",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".php": "php",
        ".swift": "swift",
        ".sh": "shell",
        ".bash": "shell",
        ".sql": "sql",
        ".md": "markdown",
        ".rst": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".html": "html",
        ".css": "css",
        ".scss": "css",
    }
    return mapping.get(suffix, "other")


def _extract_symbols(content: str, language: str) -> list[str]:
    """Top-level symbol extraction across the whole file.

    The call site only invokes this for files <= 256 KB, so scanning the full
    content (rather than just the first 200 lines) stays cheap while making
    symbols that live lower in a module visible to the L0 manifest.
    """
    if language == "python":
        matches = _PY_TOP_LEVEL.findall(content)
    elif language in ("javascript", "typescript"):
        matches = _JS_TOP_LEVEL.findall(content)
    else:
        matches = _GENERIC_TOP_LEVEL.findall(content)

    seen: list[str] = []
    for m in matches:
        if m not in seen:
            seen.append(m)
        if len(seen) >= MAX_SYMBOLS_PER_FILE:
            break
    return seen


@dataclass
class FileEntry:
    path: str
    size: int
    mtime: float
    lang: str
    symbols: list[str] = field(default_factory=list)


@dataclass
class ModuleEntry:
    name: str
    files: int
    size: int


@dataclass
class ManifestStats:
    total_files: int = 0
    indexed_files: int = 0
    total_size_bytes: int = 0
    by_language: dict[str, int] = field(default_factory=dict)
    by_extension: dict[str, int] = field(default_factory=dict)
    hot_paths: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    version: int = MANIFEST_VERSION
    project_root: str = ""
    generated_at: float = 0.0
    duration_ms: int = 0
    files: list[FileEntry] = field(default_factory=list)
    modules: list[ModuleEntry] = field(default_factory=list)
    stats: ManifestStats = field(default_factory=ManifestStats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "project_root": self.project_root,
            "generated_at": self.generated_at,
            "duration_ms": self.duration_ms,
            "files": [asdict(f) for f in self.files],
            "modules": [asdict(m) for m in self.modules],
            "stats": asdict(self.stats),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        files = [FileEntry(**f) for f in data.get("files", [])]
        modules = [ModuleEntry(**m) for m in data.get("modules", [])]
        stats_data = data.get("stats", {}) or {}
        stats = ManifestStats(
            total_files=stats_data.get("total_files", 0),
            indexed_files=stats_data.get("indexed_files", 0),
            total_size_bytes=stats_data.get("total_size_bytes", 0),
            by_language=stats_data.get("by_language", {}) or {},
            by_extension=stats_data.get("by_extension", {}) or {},
            hot_paths=stats_data.get("hot_paths", []) or [],
            config_files=stats_data.get("config_files", []) or [],
        )
        return cls(
            version=data.get("version", MANIFEST_VERSION),
            project_root=data.get("project_root", ""),
            generated_at=data.get("generated_at", 0.0),
            duration_ms=data.get("duration_ms", 0),
            files=files,
            modules=modules,
            stats=stats,
        )


def _manifest_path() -> Path:
    return config.AI_DIR / MANIFEST_FILENAME


def _config_files_in_root(root: Path) -> list[str]:
    candidates = [
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        "Pipfile",
        "package.json",
        "tsconfig.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "composer.json",
        "Gemfile",
        "Dockerfile",
        "docker-compose.yml",
        ".gitignore",
        ".env.example",
    ]
    return [c for c in candidates if (root / c).exists()]


def build_manifest(
    root: Path | None = None,
    *,
    extract_symbols: bool = True,
    max_files: int = MAX_FILES_TO_SCAN,
) -> Manifest:
    """
    Walks the project tree once, building a fresh manifest.

    Args:
        root: Project root (defaults to config.PROJECT_ROOT).
        extract_symbols: If True, reads each file (<= 256 KB) and captures its
            top-level symbols. Set False for an ultra-fast paths-only scan.
        max_files: Hard safety cap on scanned files.

    Returns:
        Fully populated Manifest.
    """
    if root is None:
        root = config.PROJECT_ROOT

    started = time.monotonic()
    manifest = Manifest(project_root=str(root), generated_at=time.time())

    indexable = config.INDEXABLE_EXTENSIONS
    binary = config.BINARY_EXTENSIONS

    module_counts: dict[str, ModuleEntry] = {}
    total_files = 0
    indexed_files = 0
    total_size = 0
    by_language: dict[str, int] = {}
    by_extension: dict[str, int] = {}
    hot_files: list[tuple[str, int]] = []  # (path, size) for hot_paths

    for cur_dir, dir_names, files in os.walk(root):
        dir_names[:] = [d for d in dir_names if not config.is_dir_ignored(d)]

        for fname in files:
            if total_files >= max_files:
                break
            total_files += 1
            full = Path(cur_dir) / fname
            try:
                stat = full.stat()
            except OSError:
                continue
            size = stat.st_size
            total_size += size

            suffix = full.suffix.lower()
            by_extension[suffix] = by_extension.get(suffix, 0) + 1

            if suffix in binary:
                continue
            if suffix and suffix not in indexable:
                continue

            indexed_files += 1
            lang = _detect_language(suffix)
            by_language[lang] = by_language.get(lang, 0) + 1

            try:
                rel = full.relative_to(root).as_posix()
            except ValueError:
                rel = full.as_posix()

            top_module = rel.split("/", 1)[0] if "/" in rel else "(root)"
            entry = module_counts.get(top_module)
            if entry is None:
                module_counts[top_module] = ModuleEntry(name=top_module, files=1, size=size)
            else:
                entry.files += 1
                entry.size += size

            symbols: list[str] = []
            if extract_symbols and size <= 256 * 1024:
                try:
                    content = full.read_text(encoding="utf-8", errors="ignore")
                    symbols = _extract_symbols(content, lang)
                except Exception:
                    symbols = []

            manifest.files.append(
                FileEntry(
                    path=rel,
                    size=size,
                    mtime=stat.st_mtime,
                    lang=lang,
                    symbols=symbols,
                )
            )
            hot_files.append((rel, size))

        if total_files >= max_files:
            logger.warning(f"Manifest scan capped at {max_files} files")
            break

    hot_files.sort(key=lambda x: x[1], reverse=True)
    hot_paths = [p for p, _ in hot_files[:HOT_PATH_LIMIT]]

    manifest.modules = sorted(module_counts.values(), key=lambda m: m.files, reverse=True)
    manifest.stats = ManifestStats(
        total_files=total_files,
        indexed_files=indexed_files,
        total_size_bytes=total_size,
        by_language=by_language,
        by_extension=dict(sorted(by_extension.items(), key=lambda x: x[1], reverse=True)),
        hot_paths=hot_paths,
        config_files=_config_files_in_root(root),
    )
    manifest.duration_ms = int((time.monotonic() - started) * 1000)
    return manifest


def save_manifest(manifest: Manifest) -> Path:
    """Atomically writes manifest to .ai/manifest.json."""
    from incremental_indexing import atomic_write

    config.AI_DIR.mkdir(parents=True, exist_ok=True)
    target = _manifest_path()
    atomic_write(target, json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
    logger.info(
        f"Manifest saved: {len(manifest.files)} files, "
        f"{manifest.duration_ms}ms, {target.stat().st_size // 1024} KB"
    )
    return target


def load_manifest() -> Manifest | None:
    """Loads the persisted manifest. Returns None if missing or invalid."""
    target = _manifest_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Manifest unreadable: {e}")
        return None
    if data.get("version") != MANIFEST_VERSION:
        logger.info(
            f"Manifest version mismatch (got {data.get('version')}, expected {MANIFEST_VERSION})"
        )
        return None
    if data.get("project_root") and data["project_root"] != str(config.PROJECT_ROOT):
        logger.info("Manifest project_root mismatch — will rebuild")
        return None
    try:
        return Manifest.from_dict(data)
    except Exception as e:
        logger.warning(f"Manifest deserialisation failed: {e}")
        return None


def is_manifest_stale(manifest: Manifest, root: Path | None = None) -> bool:
    """
    Returns True when at least one indexable file has mtime > manifest.generated_at,
    or the file count differs by > 5%.
    """
    if root is None:
        root = config.PROJECT_ROOT

    threshold = manifest.generated_at
    if threshold <= 0:
        return True

    indexable = config.INDEXABLE_EXTENSIONS
    binary = config.BINARY_EXTENSIONS
    fresh_count = 0
    found_change = False

    for cur_dir, dir_names, files in os.walk(root):
        dir_names[:] = [d for d in dir_names if not config.is_dir_ignored(d)]
        for fname in files:
            full = Path(cur_dir) / fname
            suffix = full.suffix.lower()
            if suffix in binary:
                continue
            if suffix and suffix not in indexable:
                continue
            fresh_count += 1
            try:
                if full.stat().st_mtime > threshold:
                    found_change = True
                    break
            except OSError:
                continue
        if found_change:
            break

    if found_change:
        return True

    indexed = manifest.stats.indexed_files or 1
    drift = abs(indexed - fresh_count) / indexed
    return drift > 0.05


def get_or_build_manifest(*, force: bool = False) -> Manifest:
    """
    High-level entry point. Returns a current Manifest, building if missing/stale.
    """
    if not force:
        existing = load_manifest()
        if existing is not None and not is_manifest_stale(existing):
            return existing

    manifest = build_manifest()
    try:
        save_manifest(manifest)
    except Exception as e:
        logger.warning(f"Could not persist manifest: {e}")
    return manifest


def quick_overview_from_manifest(manifest: Manifest) -> str:
    """Produces a fast, human-readable overview from a Manifest only — no IO."""
    parts: list[str] = [f"# PROJECT OVERVIEW: {Path(manifest.project_root).name}"]
    parts.append(f"**Root**: `{manifest.project_root}`")
    parts.append(
        f"**Files**: {manifest.stats.total_files} total, "
        f"{manifest.stats.indexed_files} indexable, "
        f"{manifest.stats.total_size_bytes / (1024 * 1024):.1f} MB"
    )

    if manifest.stats.config_files:
        parts.append("")
        parts.append("## Config Files")
        for cfg in manifest.stats.config_files:
            parts.append(f"- `{cfg}`")

    if manifest.stats.by_language:
        parts.append("")
        parts.append("## Languages")
        top_langs = sorted(manifest.stats.by_language.items(), key=lambda x: x[1], reverse=True)[:6]
        for lang, count in top_langs:
            parts.append(f"- {lang}: {count} files")

    if manifest.modules:
        parts.append("")
        parts.append("## Top Modules")
        for mod in manifest.modules[:10]:
            mb = mod.size / (1024 * 1024)
            parts.append(f"- `{mod.name}/` — {mod.files} files ({mb:.1f} MB)")

    if manifest.stats.hot_paths:
        parts.append("")
        parts.append("## Largest Files")
        for hp in manifest.stats.hot_paths[:8]:
            parts.append(f"- `{hp}`")

    parts.append("")
    parts.append(
        f"_Generated in {manifest.duration_ms} ms. "
        f"Drill deeper with `explore_directory`, `get_file_summary`, or `query`._"
    )
    return "\n".join(parts)


def find_files_by_keyword(manifest: Manifest, keyword: str, limit: int = 20) -> list[FileEntry]:
    """L0 lexical search across paths and symbols. No tokenisation, just substring."""
    keyword_lc = keyword.lower()
    hits: list[tuple[int, FileEntry]] = []
    for entry in manifest.files:
        score = 0
        path_lc = entry.path.lower()
        if keyword_lc in path_lc:
            score += 5
            if path_lc.endswith(f"/{keyword_lc}") or path_lc.split("/")[-1] == keyword_lc:
                score += 10
        for sym in entry.symbols:
            if keyword_lc in sym.lower():
                score += 3
                if sym.lower() == keyword_lc:
                    score += 7
        if score > 0:
            hits.append((score, entry))
    hits.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in hits[:limit]]
