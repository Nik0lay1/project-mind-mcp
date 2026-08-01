"""
Single source of truth for *which files ProjectMind looks at*.

Historically every consumer walked the tree itself:

  * ``codebase_indexer.scan_indexable_files``  — applied ``.indexignore``
  * ``background_indexer._scan_files``         — applied ``.indexignore``
  * ``symbol_graph.build_symbol_graph``        — did **not**
  * ``manifest.build_manifest``                — did **not**

The copies drifted, and the ones that skipped ``.indexignore`` happily walked
into build output (``app/.next``, ``dist``, vendored bundles). Because such
directories usually sort first and hold tens of thousands of minified files,
those consumers burned their whole file/time budget on generated code and
returned an empty-looking result for the real source tree.

Everything now goes through :func:`scan_files`, so a pattern added to
``.indexignore`` takes effect for the vector index, the symbol graph and the
manifest at the same time.

Ignore semantics (unchanged, just applied consistently):
  * substring match against the project-relative POSIX path,
  * plus glob match (``*.min.js``) against the path and the basename,
  * directories are matched *as directories* and pruned, so an ignored subtree
    is never descended into.
"""

from __future__ import annotations

import fnmatch
import os
import time as _time_module
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import config
from logger import get_logger

logger = get_logger()

# Safety cap: how many files a single scan may collect before it reports itself
# truncated. Kept in sync with the indexer's historic 20k cap.
DEFAULT_SCAN_MAX_FILES = 20000

# A "line" longer than this means the file is almost certainly generated or
# minified. tree-sitter can spend minutes on a single such line, so callers that
# parse (symbol graph, AST splitter) skip them regardless of ignore settings.
MINIFIED_LINE_THRESHOLD = 5000


@dataclass
class ScanResult:
    """Outcome of a tree scan — files plus *why* it stopped, if it did."""

    files: list[Path] = field(default_factory=list)
    #: None when the scan saw the whole tree; otherwise a human-readable reason.
    truncated: str | None = None
    files_seen: int = 0
    dirs_pruned: int = 0
    elapsed_seconds: float = 0.0

    @property
    def complete(self) -> bool:
        return self.truncated is None

    def __len__(self) -> int:
        return len(self.files)

    def __iter__(self):
        return iter(self.files)


# ---------------------------------------------------------------------------
# Ignore patterns
# ---------------------------------------------------------------------------


def load_ignore_patterns(root: Path | None = None) -> set[str]:
    """
    Read ``.indexignore`` for *root*.

    Preference order (matches ``config.resolve_index_ignore_file``):
      1. ``<root>/.indexignore``     — user-friendly, next to .gitignore
      2. ``<root>/.ai/.indexignore`` — legacy location
    """
    if root is None:
        root = config.PROJECT_ROOT
    root = Path(root)

    for candidate in (root / ".indexignore", root / ".ai" / ".indexignore"):
        if not candidate.exists():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.warning(f"Could not read {candidate}: {exc}")
            continue
        return {ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")}
    return set()


class IgnoreMatcher:
    """
    Matches paths against ``.indexignore`` patterns and the built-in dir list.

    Paths are normalised to project-relative POSIX form before matching, so a
    pattern written as ``app/.next`` works on Windows too.
    """

    def __init__(self, root: Path, patterns: Iterable[str] | None = None) -> None:
        self.root = Path(root)
        pats = set(patterns or ())
        # Split once: globs need fnmatch, plain names are cheap substring tests.
        self._globs = {p for p in pats if any(c in p for c in "*?[")}
        self._plain = pats - self._globs

    def _rel_posix(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def matches_rel(self, rel_posix: str, name: str) -> bool:
        """Match an already-relative POSIX path (and its basename)."""
        for pat in self._plain:
            if pat in rel_posix:
                return True
        for pat in self._globs:
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_posix, pat):
                return True
        return False

    def matches(self, path: Path) -> bool:
        path = Path(path)
        return self.matches_rel(self._rel_posix(path), path.name)

    def dir_ignored(self, dir_path: Path, dir_name: str) -> bool:
        """
        True when a whole directory subtree should be skipped.

        Covers the built-in name list (``node_modules``, ``.git``, …) *and*
        ``.indexignore`` patterns — the latter is what the symbol graph was
        missing, which let it walk into ``app/.next``.
        """
        if config.is_dir_ignored(dir_name):
            return True
        rel = self._rel_posix(Path(dir_path))
        # Trailing slash so a pattern like "build" matches the directory itself
        # the same way it matches paths underneath it.
        return self.matches_rel(rel + "/", dir_name)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_files(
    root: Path,
    *,
    extensions: set[str] | None = None,
    ignore_patterns: Iterable[str] | None = None,
    ignored_dirs: set[str] | None = None,
    max_files: int | None = DEFAULT_SCAN_MAX_FILES,
    budget_seconds: float | None = None,
    max_file_size: int | None = None,
    allow_no_suffix: bool = True,
) -> ScanResult:
    """
    Walk *root* and return the files ProjectMind considers indexable.

    Args:
        root: Directory to walk.
        extensions: Restrict to these suffixes (lowercase, with dot).
            ``None`` means ``config.INDEXABLE_EXTENSIONS``.
        ignore_patterns: ``.indexignore`` patterns. ``None`` loads them from
            *root* — pass an explicit set only when they are already in hand.
        ignored_dirs: Extra directory *names* to skip, on top of the built-ins.
        max_files: Stop after this many matches (``None`` = unlimited).
        budget_seconds: Stop after this much wall-clock time (``None`` = no limit).
        max_file_size: Skip files bigger than this (``None`` = config default).
        allow_no_suffix: Keep extension-less files (Dockerfile, LICENSE…).
            Callers that must parse the file (symbol graph) pass False.

    Returns:
        ScanResult — always check ``.truncated`` before treating the file list
        as the complete picture.
    """
    root = Path(root)
    if extensions is None:
        extensions = config.INDEXABLE_EXTENSIONS
    if ignore_patterns is None:
        ignore_patterns = load_ignore_patterns(root)
    if max_file_size is None:
        max_file_size = config.get_max_file_size_bytes()

    matcher = IgnoreMatcher(root, ignore_patterns)
    extra_dirs = ignored_dirs or set()
    binary = config.BINARY_EXTENSIONS

    result = ScanResult()
    t_start = _time_module.monotonic()

    for dirpath, dirnames, filenames in os.walk(root):
        cur = Path(dirpath)

        kept: list[str] = []
        for d in dirnames:
            if d in extra_dirs or matcher.dir_ignored(cur / d, d):
                result.dirs_pruned += 1
            else:
                kept.append(d)
        dirnames[:] = kept

        for fname in filenames:
            result.files_seen += 1

            if max_files is not None and len(result.files) >= max_files:
                result.truncated = (
                    f"file limit reached ({max_files} files); "
                    "part of the tree was not scanned"
                )
                result.elapsed_seconds = _time_module.monotonic() - t_start
                return result

            if budget_seconds is not None and result.files_seen % 256 == 0:
                elapsed = _time_module.monotonic() - t_start
                if elapsed > budget_seconds:
                    result.truncated = (
                        f"time budget exhausted ({budget_seconds:.0f}s) after "
                        f"{len(result.files)} files; part of the tree was not scanned"
                    )
                    result.elapsed_seconds = elapsed
                    return result

            fp = cur / fname
            suffix = fp.suffix.lower()

            if suffix in binary:
                continue
            if suffix:
                if suffix not in extensions:
                    continue
            elif not allow_no_suffix:
                continue

            if matcher.matches(fp):
                continue

            try:
                if fp.stat().st_size > max_file_size:
                    continue
            except OSError:
                continue

            result.files.append(fp)

    result.elapsed_seconds = _time_module.monotonic() - t_start
    return result


def looks_minified(content: str) -> bool:
    """
    Cheap check for generated/minified sources.

    Bundlers emit megabyte-long single lines; tree-sitter can take minutes on
    one of them while holding the global parse lock. Skipping them keeps a
    stray bundle from stalling every search in the process.
    """
    if not content:
        return False
    head = content[:200_000]
    longest = max((len(ln) for ln in head.splitlines()), default=0)
    return longest > MINIFIED_LINE_THRESHOLD
