"""
File annotations — AI-authored summaries that make keyword search semantic.

The design inverts the usual RAG architecture: instead of a small embedding
model guessing what code means, the *client* LLM (which actually understands
the code) writes a short natural-language summary + keywords per file. Those
annotations are indexed into the BM25 corpus and served as a cheap query tier,
so "where is authentication handled?" matches the summary text with plain
keyword search — no vector database required.

Storage: `.ai/annotations.json` (atomic writes, UTF-8, project-relative posix
keys). Each entry remembers the file's mtime at annotation time so stale
annotations (file changed since) can be surfaced for re-annotation.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from incremental_indexing import atomic_write
from logger import get_logger

logger = get_logger()

ANNOTATIONS_FILENAME = "annotations.json"
ANNOTATIONS_VERSION = 1

# Cap on files returned by unannotated scans (safety on huge repos)
_SCAN_CAP = 20000

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _annotations_path() -> Path:
    return config.AI_DIR / ANNOTATIONS_FILENAME


def _rel_posix(path: str | Path) -> str:
    """Normalize any path to a project-relative posix string."""
    p = Path(path)
    try:
        if p.is_absolute():
            return p.resolve().relative_to(config.PROJECT_ROOT.resolve()).as_posix()
        return p.as_posix()
    except Exception:
        return str(path).replace("\\", "/")


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2}


@dataclass
class Annotation:
    path: str  # project-relative posix
    summary: str
    keywords: list[str] = field(default_factory=list)
    annotated_mtime: float = 0.0
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "keywords": self.keywords,
            "annotated_mtime": self.annotated_mtime,
            "updated_at": self.updated_at,
        }

    def search_text(self) -> str:
        """Text indexed into BM25 for this annotation."""
        stem = Path(self.path).stem
        return f"{self.path} {stem} {self.summary} {' '.join(self.keywords)}"

    @property
    def doc_id(self) -> str:
        return f"annot::{self.path}"


class AnnotationStore:
    """Thread-safe store over `.ai/annotations.json`."""

    def __init__(self, path: Path | None = None):
        self._path = path if path is not None else _annotations_path()
        self._lock = threading.RLock()
        self._files: dict[str, Annotation] = {}
        self._loaded_mtime: float = -1.0
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._files = {}
                self._loaded_mtime = -1.0
                return
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                files = {}
                for rel, entry in (data.get("files") or {}).items():
                    files[rel] = Annotation(
                        path=rel,
                        summary=str(entry.get("summary", "")),
                        keywords=[str(k) for k in entry.get("keywords", [])],
                        annotated_mtime=float(entry.get("annotated_mtime", 0.0) or 0.0),
                        updated_at=str(entry.get("updated_at", "")),
                    )
                self._files = files
                self._loaded_mtime = self._path.stat().st_mtime
            except Exception as e:
                logger.warning(f"Could not load annotations: {e}")
                self._files = {}

    def refresh_if_changed(self) -> None:
        """Re-read from disk when another process/session updated the file."""
        with self._lock:
            try:
                current = self._path.stat().st_mtime if self._path.exists() else -1.0
            except OSError:
                return
            if current != self._loaded_mtime:
                self._load()

    def _save(self) -> None:
        with self._lock:
            data = {
                "version": ANNOTATIONS_VERSION,
                "files": {rel: a.to_dict() for rel, a in sorted(self._files.items())},
            }
            atomic_write(self._path, json.dumps(data, indent=2, ensure_ascii=False))
            try:
                self._loaded_mtime = self._path.stat().st_mtime
            except OSError:
                pass

    # -- API ----------------------------------------------------------------

    def set(self, path: str, summary: str, keywords: list[str] | None = None) -> Annotation:
        """Save/replace one file's annotation. Path must exist inside the project."""
        target = config.validate_path(path)
        if not target.is_file():
            raise ValueError(f"Not a file: {path}")
        rel = _rel_posix(target)
        try:
            mtime = target.stat().st_mtime
        except OSError:
            mtime = 0.0
        ann = Annotation(
            path=rel,
            summary=summary.strip(),
            keywords=[k.strip() for k in (keywords or []) if k.strip()],
            annotated_mtime=mtime,
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._lock:
            self._files[rel] = ann
            self._save()
        return ann

    def remove(self, path: str) -> bool:
        rel = _rel_posix(path)
        with self._lock:
            if rel in self._files:
                del self._files[rel]
                self._save()
                return True
            return False

    def get(self, path: str) -> Annotation | None:
        self.refresh_if_changed()
        return self._files.get(_rel_posix(path))

    def all(self) -> dict[str, Annotation]:
        self.refresh_if_changed()
        with self._lock:
            return dict(self._files)

    def count(self) -> int:
        self.refresh_if_changed()
        return len(self._files)

    def is_stale(self, ann: Annotation) -> bool:
        """True when the file changed after it was annotated (or vanished)."""
        target = config.PROJECT_ROOT / ann.path
        try:
            return target.stat().st_mtime != ann.annotated_mtime
        except OSError:
            return True

    def unannotated(self, limit: int = 20) -> tuple[list[str], list[str], int]:
        """
        Scan indexable code files and report annotation coverage.

        Returns:
            (missing, stale, total_scanned): relative posix paths of files with
            no annotation, files whose annotation is stale, and the number of
            code files scanned.
        """
        self.refresh_if_changed()
        missing: list[str] = []
        stale: list[str] = []
        scanned = 0
        root = config.PROJECT_ROOT

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not config.is_dir_ignored(d)]
            for fname in filenames:
                fp = Path(dirpath) / fname
                suffix = fp.suffix.lower()
                # Annotations target source code, not configs/docs
                if suffix not in config.CODE_EXTENSIONS:
                    continue
                scanned += 1
                if scanned > _SCAN_CAP:
                    return missing, stale, scanned
                rel = _rel_posix(fp)
                ann = self._files.get(rel)
                if ann is None:
                    if len(missing) < limit:
                        missing.append(rel)
                elif self.is_stale(ann):
                    if len(stale) < limit:
                        stale.append(rel)
        return missing, stale, scanned

    # -- search integration --------------------------------------------------

    def as_documents(self) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        """Annotation docs for the BM25 corpus: (ids, texts, metadatas)."""
        self.refresh_if_changed()
        ids: list[str] = []
        texts: list[str] = []
        metas: list[dict[str, Any]] = []
        with self._lock:
            for ann in self._files.values():
                if not ann.summary:
                    continue
                ids.append(ann.doc_id)
                texts.append(ann.search_text())
                metas.append(
                    {
                        "source": ann.path,
                        "symbol_type": "annotation",
                        "symbol_name": Path(ann.path).stem,
                    }
                )
        return ids, texts, metas

    def search(self, query: str, n: int = 8) -> list[dict[str, Any]]:
        """
        Cheap keyword-overlap scoring over annotations (query-tier friendly:
        no BM25 matrix or vector store needed, runs in microseconds).
        """
        self.refresh_if_changed()
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scored: list[tuple[float, Annotation]] = []
        with self._lock:
            for ann in self._files.values():
                path_tokens = _tokenize(ann.path)
                kw_tokens = _tokenize(" ".join(ann.keywords))
                sum_tokens = _tokenize(ann.summary)
                score = 0.0
                for tok in q_tokens:
                    if tok in kw_tokens:
                        score += 3.0
                    if tok in path_tokens:
                        score += 2.0
                    if tok in sum_tokens:
                        score += 1.0
                matched = sum(
                    1 for t in q_tokens if t in kw_tokens or t in path_tokens or t in sum_tokens
                )
                score += 2.0 * (matched / len(q_tokens))
                if matched:
                    scored.append((score, ann))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:n]
        max_score = top[0][0] if top else 1.0
        return [
            {
                "source": ann.path,
                "score": round(0.5 + 0.5 * (s / max_score), 4) if max_score else 0.5,
                "tier": "L0_annot",
                "snippet": ann.summary[:300],
                "extra": {"keywords": ann.keywords, "updated_at": ann.updated_at},
            }
            for s, ann in top
        ]


# Module-level singleton (per project root; reset via get_store cache key)
_store: AnnotationStore | None = None
_store_path: Path | None = None
_store_lock = threading.Lock()


def get_store() -> AnnotationStore:
    """Returns the AnnotationStore for the current project root."""
    global _store, _store_path
    with _store_lock:
        current = _annotations_path()
        if _store is None or _store_path != current:
            _store = AnnotationStore(current)
            _store_path = current
        return _store
