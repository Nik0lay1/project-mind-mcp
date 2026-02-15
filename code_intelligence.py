"""Static code intelligence: conventions, import graph, TODOs. No ML dependencies."""

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from config import (
    BINARY_EXTENSIONS,
    CODE_EXTENSIONS,
    INDEXABLE_EXTENSIONS,
    is_dir_ignored,
    safe_read_text,
)
from logger import get_logger

logger = get_logger()

TODO_PATTERN = re.compile(
    r"#\s*(TODO|FIXME|HACK|BUG|XXX|OPTIMIZE|NOTE|WARNING)\b[:\s]*(.*)",
    re.IGNORECASE,
)
TODO_PATTERN_SLASHCOMMENT = re.compile(
    r"//\s*(TODO|FIXME|HACK|BUG|XXX|OPTIMIZE|NOTE|WARNING)\b[:\s]*(.*)",
    re.IGNORECASE,
)

PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)
JS_IMPORT_RE = re.compile(
    r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))"""
)
GO_IMPORT_RE = re.compile(r'"([^"]+)"')
RUST_USE_RE = re.compile(r"^\s*use\s+([\w:]+)")


@dataclass
class TodoItem:
    file: str
    line_no: int
    tag: str
    text: str


@dataclass
class FileRelations:
    path: str
    imports_from: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)


@dataclass
class ProjectConventions:
    naming_style: str = "unknown"
    test_framework: str = "unknown"
    test_pattern: str = "unknown"
    import_style: str = "unknown"
    error_handling: str = "unknown"
    logging_style: str = "unknown"
    architecture: str = "unknown"
    linting_tools: list[str] = field(default_factory=list)
    formatting_tools: list[str] = field(default_factory=list)
    type_checking: str = "none"
    primary_language: str = "unknown"
    frameworks: list[str] = field(default_factory=list)


def _iter_code_files(root: Path, max_files: int = 5000) -> list[tuple[Path, str]]:
    """Yields (path, extension) for code files in project. Skips ignored dirs."""
    results = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not is_dir_ignored(d) and not d.startswith(".")]
        for fname in files:
            fpath = Path(dirpath) / fname
            ext = fpath.suffix.lower()
            if ext in INDEXABLE_EXTENSIONS and ext not in BINARY_EXTENSIONS:
                results.append((fpath, ext))
                if len(results) >= max_files:
                    return results
    return results


def detect_conventions(root: Path) -> str:
    """Detects project conventions from code patterns. No ML needed."""
    conv = ProjectConventions()
    code_files = _iter_code_files(root, max_files=2000)

    ext_counts: Counter[str] = Counter()
    for _, ext in code_files:
        ext_counts[ext] += 1

    if ext_counts:
        top_ext = ext_counts.most_common(1)[0][0]
        lang_map = {
            ".py": "Python",
            ".js": "JavaScript",
            ".ts": "TypeScript",
            ".tsx": "TypeScript (React)",
            ".jsx": "JavaScript (React)",
            ".go": "Go",
            ".rs": "Rust",
            ".java": "Java",
            ".rb": "Ruby",
            ".php": "PHP",
            ".cs": "C#",
            ".cpp": "C++",
            ".c": "C",
        }
        conv.primary_language = lang_map.get(top_ext, top_ext)

    _detect_naming(code_files, conv)
    _detect_test_patterns(root, code_files, conv)
    _detect_tooling(root, conv)
    _detect_frameworks(root, conv)
    _detect_error_and_logging(root, code_files, conv)
    _detect_architecture(root, conv)

    return _format_conventions(conv)


def _detect_naming(code_files: list[tuple[Path, str]], conv: ProjectConventions) -> None:
    snake_count = 0
    camel_count = 0
    kebab_count = 0

    for fpath, _ext in code_files[:500]:
        name = fpath.stem
        if "_" in name and name[0].islower():
            snake_count += 1
        elif name[0].islower() and any(c.isupper() for c in name[1:]):
            camel_count += 1
        elif "-" in name:
            kebab_count += 1

    total = snake_count + camel_count + kebab_count
    if total > 0:
        if snake_count > camel_count and snake_count > kebab_count:
            conv.naming_style = f"snake_case ({snake_count}/{total} files)"
        elif camel_count > snake_count and camel_count > kebab_count:
            conv.naming_style = f"camelCase ({camel_count}/{total} files)"
        elif kebab_count > 0:
            conv.naming_style = f"kebab-case ({kebab_count}/{total} files)"
        else:
            conv.naming_style = "mixed"


def _detect_test_patterns(
    root: Path,
    code_files: list[tuple[Path, str]],
    conv: ProjectConventions,
) -> None:
    test_files = [
        fpath
        for fpath, _ in code_files
        if "test" in fpath.name.lower() or "spec" in fpath.name.lower()
    ]

    if not test_files:
        conv.test_framework = "none detected"
        conv.test_pattern = "no test files found"
        return

    test_dirs = set()
    patterns = Counter[str]()
    for tf in test_files:
        rel = tf.relative_to(root)
        if len(rel.parts) > 1:
            test_dirs.add(rel.parts[0])
        name = tf.name.lower()
        if name.startswith("test_"):
            patterns["test_*.py"] += 1
        elif name.endswith("_test.py") or name.endswith("_test.go"):
            patterns["*_test.*"] += 1
        elif ".spec." in name:
            patterns["*.spec.*"] += 1
        elif ".test." in name:
            patterns["*.test.*"] += 1

    if patterns:
        conv.test_pattern = patterns.most_common(1)[0][0]

    if (root / "pytest.ini").exists() or (root / "conftest.py").exists():
        conv.test_framework = "pytest"
    elif (root / "pyproject.toml").exists():
        try:
            content = safe_read_text(root / "pyproject.toml")
            if "pytest" in content:
                conv.test_framework = "pytest"
        except Exception:
            pass

    if (root / "jest.config.js").exists() or (root / "jest.config.ts").exists():
        conv.test_framework = "jest"
    elif (root / "vitest.config.ts").exists() or (root / "vitest.config.js").exists():
        conv.test_framework = "vitest"
    elif (root / "package.json").exists():
        try:
            content = safe_read_text(root / "package.json")
            if "jest" in content:
                conv.test_framework = "jest"
            elif "vitest" in content:
                conv.test_framework = "vitest"
            elif "mocha" in content:
                conv.test_framework = "mocha"
        except Exception:
            pass

    if test_dirs:
        conv.test_pattern += f" (in: {', '.join(sorted(test_dirs)[:3])})"


def _detect_tooling(root: Path, conv: ProjectConventions) -> None:
    linting = []
    formatting = []

    if (root / "pyproject.toml").exists():
        try:
            content = safe_read_text(root / "pyproject.toml")
            if "[tool.ruff" in content:
                linting.append("ruff")
            if "[tool.black" in content:
                formatting.append("black")
            if "[tool.mypy" in content:
                conv.type_checking = "mypy"
            if "[tool.pylint" in content:
                linting.append("pylint")
            if "[tool.isort" in content:
                formatting.append("isort")
        except Exception:
            pass

    if (root / ".eslintrc.js").exists() or (root / ".eslintrc.json").exists():
        linting.append("eslint")
    if (root / ".prettierrc").exists() or (root / ".prettierrc.json").exists():
        formatting.append("prettier")
    if (root / "tsconfig.json").exists():
        conv.type_checking = "TypeScript"
    if (root / "biome.json").exists():
        linting.append("biome")
        formatting.append("biome")

    if (root / ".pre-commit-config.yaml").exists():
        linting.append("pre-commit")

    conv.linting_tools = linting
    conv.formatting_tools = formatting


def _detect_frameworks(root: Path, conv: ProjectConventions) -> None:
    frameworks = []

    if (root / "package.json").exists():
        try:
            content = safe_read_text(root / "package.json")
            for fw, name in [
                ('"next"', "Next.js"),
                ('"react"', "React"),
                ('"vue"', "Vue"),
                ('"svelte"', "Svelte"),
                ('"express"', "Express"),
                ('"fastify"', "Fastify"),
                ('"nestjs"', "NestJS"),
                ('"@nestjs/core"', "NestJS"),
                ('"nuxt"', "Nuxt"),
                ('"angular"', "Angular"),
                ('"@angular/core"', "Angular"),
            ]:
                if fw in content:
                    frameworks.append(name)
        except Exception:
            pass

    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        files_to_check = []
        if (root / "pyproject.toml").exists():
            files_to_check.append(root / "pyproject.toml")
        if (root / "requirements.txt").exists():
            files_to_check.append(root / "requirements.txt")
        for f in files_to_check:
            try:
                content = safe_read_text(f)
                for dep, name in [
                    ("fastapi", "FastAPI"),
                    ("django", "Django"),
                    ("flask", "Flask"),
                    ("starlette", "Starlette"),
                    ("celery", "Celery"),
                    ("sqlalchemy", "SQLAlchemy"),
                    ("alembic", "Alembic"),
                ]:
                    if dep in content.lower() and name not in frameworks:
                        frameworks.append(name)
            except Exception:
                pass

    conv.frameworks = frameworks


def _detect_error_and_logging(
    root: Path,
    code_files: list[tuple[Path, str]],
    conv: ProjectConventions,
) -> None:
    error_patterns = Counter[str]()
    logging_patterns = Counter[str]()

    sample = [f for f, ext in code_files if ext in CODE_EXTENSIONS][:200]

    for fpath in sample:
        try:
            content = safe_read_text(fpath)
        except Exception:
            continue

        if "raise " in content and "Exception" in content:
            error_patterns["custom exceptions (raise)"] += 1
        if "try:" in content or "try {" in content:
            error_patterns["try/catch blocks"] += 1
        if "Result<" in content or "Result::" in content:
            error_patterns["Result type"] += 1

        if "import logging" in content or "from logging" in content:
            logging_patterns["stdlib logging"] += 1
        if "getLogger" in content or "get_logger" in content:
            logging_patterns["getLogger pattern"] += 1
        if "console.log" in content or "console.error" in content:
            logging_patterns["console.log"] += 1
        if "winston" in content:
            logging_patterns["winston"] += 1
        if "pino" in content:
            logging_patterns["pino"] += 1
        if "structlog" in content:
            logging_patterns["structlog"] += 1
        if "loguru" in content:
            logging_patterns["loguru"] += 1

    if error_patterns:
        conv.error_handling = ", ".join(f"{k} ({v})" for k, v in error_patterns.most_common(3))
    if logging_patterns:
        conv.logging_style = ", ".join(f"{k} ({v})" for k, v in logging_patterns.most_common(3))


def _detect_architecture(root: Path, conv: ProjectConventions) -> None:
    markers = []
    if (root / "src").is_dir():
        markers.append("src/ directory")
    if (root / "lib").is_dir():
        markers.append("lib/ directory")
    if (root / "app").is_dir():
        markers.append("app/ directory")
    if (root / "packages").is_dir():
        markers.append("monorepo (packages/)")
    if (root / "apps").is_dir():
        markers.append("monorepo (apps/)")
    if (root / "services").is_dir():
        markers.append("microservices (services/)")

    workspaces = False
    if (root / "package.json").exists():
        try:
            content = safe_read_text(root / "package.json")
            if '"workspaces"' in content:
                workspaces = True
                markers.append("npm/yarn workspaces")
        except Exception:
            pass
    if (root / "pnpm-workspace.yaml").exists():
        markers.append("pnpm workspace")
        workspaces = True

    if workspaces:
        conv.architecture = "monorepo"
    elif markers:
        conv.architecture = ", ".join(markers)


def _format_conventions(conv: ProjectConventions) -> str:
    lines = ["# PROJECT CONVENTIONS\n"]
    lines.append(f"**Primary Language**: {conv.primary_language}")
    if conv.frameworks:
        lines.append(f"**Frameworks**: {', '.join(conv.frameworks)}")
    lines.append(f"**Architecture**: {conv.architecture}")
    lines.append("\n## Naming & Style")
    lines.append(f"- **File naming**: {conv.naming_style}")
    if conv.formatting_tools:
        lines.append(f"- **Formatting**: {', '.join(conv.formatting_tools)}")
    if conv.linting_tools:
        lines.append(f"- **Linting**: {', '.join(conv.linting_tools)}")
    lines.append(f"- **Type checking**: {conv.type_checking}")
    lines.append("\n## Testing")
    lines.append(f"- **Framework**: {conv.test_framework}")
    lines.append(f"- **Pattern**: {conv.test_pattern}")
    lines.append("\n## Error Handling & Logging")
    lines.append(f"- **Error handling**: {conv.error_handling}")
    lines.append(f"- **Logging**: {conv.logging_style}")
    return "\n".join(lines)


def _extract_imports_py(content: str) -> list[str]:
    imports = []
    for match in PY_IMPORT_RE.finditer(content):
        module = match.group(1) or match.group(2)
        if module:
            imports.append(module.split(".")[0])
    return imports


def _extract_imports_js(content: str) -> list[str]:
    imports = []
    for match in JS_IMPORT_RE.finditer(content):
        path = match.group(1) or match.group(2)
        if path:
            imports.append(path)
    return imports


def _resolve_import_to_file(imp: str, source_file: Path, root: Path, ext: str) -> Path | None:
    if ext == ".py":
        module_path = imp.replace(".", "/")
        candidates = [
            source_file.parent / (module_path + ".py"),
            root / (module_path + ".py"),
            source_file.parent / module_path / "__init__.py",
            root / module_path / "__init__.py",
        ]
        for c in candidates:
            resolved = c.resolve()
            if resolved.exists() and resolved != source_file.resolve():
                try:
                    resolved.relative_to(root.resolve())
                    return resolved
                except ValueError:
                    continue
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        if not imp.startswith("."):
            return None
        base = source_file.parent
        for suffix in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
            candidate = (base / (imp + suffix)).resolve()
            if candidate.exists() and candidate != source_file.resolve():
                return candidate
    return None


def build_import_graph(root: Path, max_files: int = 3000) -> dict[str, list[str]]:
    """Builds a mapping: file_path -> [list of files it imports from]."""
    code_files = _iter_code_files(root, max_files=max_files)
    graph: dict[str, list[str]] = {}

    for fpath, ext in code_files:
        if ext not in CODE_EXTENSIONS:
            continue
        try:
            content = safe_read_text(fpath)
        except Exception:
            continue

        imports_raw: list[str] = []
        if ext == ".py":
            imports_raw = _extract_imports_py(content)
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            imports_raw = _extract_imports_js(content)

        resolved = set()
        for imp in imports_raw:
            target = _resolve_import_to_file(imp, fpath, root, ext)
            if target:
                try:
                    resolved.add(str(target.relative_to(root)).replace("\\", "/"))
                except ValueError:
                    pass

        rel_path = str(fpath.relative_to(root)).replace("\\", "/")
        graph[rel_path] = sorted(resolved)

    return graph


def _find_related_tests(
    file_path: str, root: Path, code_files: list[tuple[Path, str]]
) -> list[str]:
    name = Path(file_path).stem
    related = []
    for fpath, _ in code_files:
        fname = fpath.name.lower()
        if name.lower() in fname and ("test" in fname or "spec" in fname):
            try:
                related.append(str(fpath.relative_to(root)).replace("\\", "/"))
            except ValueError:
                pass
    return related


def get_file_relations(file_path: str, root: Path) -> str:
    """Returns import relations for a file: imports from, imported by, related tests."""
    code_files = _iter_code_files(root, max_files=3000)
    graph = build_import_graph(root, max_files=3000)

    norm_path = file_path.replace("\\", "/")

    imports_from = graph.get(norm_path, [])

    imported_by = []
    for source, targets in graph.items():
        if norm_path in targets and source != norm_path:
            imported_by.append(source)

    related_tests = _find_related_tests(norm_path, root, code_files)

    lines = [f"# FILE RELATIONS: {norm_path}\n"]

    if imports_from:
        lines.append(f"## Imports From ({len(imports_from)})")
        for imp in sorted(imports_from):
            lines.append(f"- `{imp}`")
    else:
        lines.append("## Imports From\n- (no local imports detected)")

    if imported_by:
        lines.append(f"\n## Imported By ({len(imported_by)})")
        for imp in sorted(imported_by):
            lines.append(f"- `{imp}`")
    else:
        lines.append("\n## Imported By\n- (not imported by any local file)")

    if related_tests:
        lines.append(f"\n## Related Tests ({len(related_tests)})")
        for t in sorted(related_tests):
            lines.append(f"- `{t}`")
    else:
        lines.append("\n## Related Tests\n- (no test files found for this module)")

    impact = len(imported_by)
    if impact >= 5:
        lines.append(f"\n**Impact**: HIGH \u2014 {impact} files depend on this module")
    elif impact >= 2:
        lines.append(f"\n**Impact**: MEDIUM \u2014 {impact} files depend on this module")
    elif impact >= 1:
        lines.append(f"\n**Impact**: LOW \u2014 {impact} file depends on this module")

    return "\n".join(lines)


def extract_todos(root: Path, max_files: int = 3000, tag_filter: str | None = None) -> str:
    """Scans codebase for TODO/FIXME/HACK/BUG/XXX comments."""
    code_files = _iter_code_files(root, max_files=max_files)
    todos: list[TodoItem] = []

    for fpath, ext in code_files:
        if ext in BINARY_EXTENSIONS:
            continue
        try:
            content = safe_read_text(fpath)
        except Exception:
            continue

        rel_path = str(fpath.relative_to(root)).replace("\\", "/")

        for line_no, line in enumerate(content.split("\n"), 1):
            for p in (TODO_PATTERN, TODO_PATTERN_SLASHCOMMENT):
                match = p.search(line)
                if match:
                    tag = match.group(1).upper()
                    text = match.group(2).strip()
                    if tag_filter and tag != tag_filter.upper():
                        continue
                    todos.append(TodoItem(rel_path, line_no, tag, text))
                    break

    if not todos:
        return "No TODO/FIXME/HACK comments found in the codebase."

    tag_counts = Counter(t.tag for t in todos)
    file_counts = Counter(t.file for t in todos)

    lines = [f"# CODEBASE TODOs ({len(todos)} total)\n"]

    lines.append("## Summary")
    for tag, count in tag_counts.most_common():
        lines.append(f"- **{tag}**: {count}")

    lines.append(f"\n## By File (top {min(10, len(file_counts))})")
    for fname, count in file_counts.most_common(10):
        lines.append(f"- `{fname}`: {count} items")

    lines.append("\n## All Items")
    current_file = ""
    for todo in sorted(todos, key=lambda t: (t.file, t.line_no)):
        if todo.file != current_file:
            current_file = todo.file
            lines.append(f"\n### `{current_file}`")
        text_preview = todo.text[:120] if todo.text else "(no description)"
        lines.append(f"- **{todo.tag}** (line {todo.line_no}): {text_preview}")

    return "\n".join(lines)
