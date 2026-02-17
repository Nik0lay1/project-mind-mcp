"""Static code intelligence: conventions, import graph, TODOs, dependencies. No ML dependencies."""

import json
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


def get_dependencies_with_depth(
    file_path: str, graph: dict[str, list[str]], depth: int = 2, direction: str = "downstream"
) -> dict[str, int]:
    """
    Traverses dependency graph from file_path up to specified depth.

    Args:
        file_path: Starting file path (normalized, forward slashes)
        graph: Import graph (file -> [imported files])
        depth: Maximum depth to traverse (1-5)
        direction: "downstream" (what it imports) or "upstream" (what imports it)

    Returns:
        Dict mapping file_path -> distance from origin
    """
    if depth < 1 or depth > 5:
        depth = 2

    # Build reverse graph for upstream traversal
    if direction == "upstream":
        reverse_graph: dict[str, list[str]] = {}
        for source, targets in graph.items():
            for target in targets:
                if target not in reverse_graph:
                    reverse_graph[target] = []
                reverse_graph[target].append(source)
        working_graph = reverse_graph
    else:
        working_graph = graph

    # BFS traversal
    visited: dict[str, int] = {file_path: 0}
    queue: list[tuple[str, int]] = [(file_path, 0)]

    while queue:
        current, dist = queue.pop(0)

        if dist >= depth:
            continue

        neighbors = working_graph.get(current, [])
        for neighbor in neighbors:
            if neighbor not in visited:
                visited[neighbor] = dist + 1
                queue.append((neighbor, dist + 1))

    # Remove origin file
    visited.pop(file_path, None)
    return visited


def find_dependency_path(
    from_file: str, to_file: str, graph: dict[str, list[str]], max_depth: int = 10
) -> list[str] | None:
    """
    Finds shortest path between two files in dependency graph.

    Args:
        from_file: Source file path
        to_file: Target file path
        graph: Import graph
        max_depth: Maximum search depth

    Returns:
        List of file paths forming the dependency chain, or None if no path
    """
    if from_file == to_file:
        return [from_file]

    # BFS with path tracking
    visited: set[str] = {from_file}
    queue: list[tuple[str, list[str]]] = [(from_file, [from_file])]

    while queue:
        current, path = queue.pop(0)

        if len(path) > max_depth:
            continue

        for neighbor in graph.get(current, []):
            if neighbor == to_file:
                return path + [neighbor]

            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return None


def get_module_cluster(
    file_path: str, root: Path, similarity_threshold: float = 0.3, max_cluster_size: int = 20
) -> dict[str, float]:
    """
    Finds files that are closely related to the given file based on shared dependencies.

    Args:
        file_path: Target file path (normalized)
        root: Project root
        similarity_threshold: Minimum Jaccard similarity (0.0-1.0)
        max_cluster_size: Maximum number of related files to return

    Returns:
        Dict mapping file_path -> similarity_score, sorted by score descending
    """
    graph = build_import_graph(root, max_files=3000)

    norm_path = file_path.replace("\\", "/")
    if norm_path not in graph:
        return {}

    # Get all dependencies (imports + importers)
    target_imports = set(graph.get(norm_path, []))
    target_importers = {src for src, targets in graph.items() if norm_path in targets}
    target_deps = target_imports | target_importers

    if not target_deps:
        return {}

    # Calculate Jaccard similarity with all other files
    similarities: dict[str, float] = {}

    for other_file, other_imports in graph.items():
        if other_file == norm_path:
            continue

        other_importers = {src for src, targets in graph.items() if other_file in targets}
        other_deps = set(other_imports) | other_importers

        if not other_deps:
            continue

        # Jaccard similarity: intersection / union
        intersection = len(target_deps & other_deps)
        union = len(target_deps | other_deps)

        if union > 0:
            similarity = intersection / union
            if similarity >= similarity_threshold:
                similarities[other_file] = similarity

    # Sort by similarity and limit size
    sorted_similar = sorted(similarities.items(), key=lambda x: x[1], reverse=True)
    return dict(sorted_similar[:max_cluster_size])


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


def check_dependencies(root: Path) -> str:
    """Parses dependency files and reports versions, duplicates, potential issues."""
    sections: list[str] = []

    py_deps = _check_python_deps(root)
    if py_deps:
        sections.append(py_deps)

    js_deps = _check_js_deps(root)
    if js_deps:
        sections.append(js_deps)

    go_deps = _check_go_deps(root)
    if go_deps:
        sections.append(go_deps)

    rust_deps = _check_rust_deps(root)
    if rust_deps:
        sections.append(rust_deps)

    if not sections:
        return "No dependency files found (pyproject.toml, requirements.txt, package.json, go.mod, Cargo.toml)."

    header = "# DEPENDENCY HEALTH CHECK\n"
    return header + "\n\n".join(sections)


def _parse_pyproject_deps(content: str) -> list[tuple[str, str]]:
    deps: list[tuple[str, str]] = []
    in_deps = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("dependencies") and "=" in stripped:
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("]"):
                break
            match = re.search(r'"([^"]+)"', stripped)
            if match:
                raw = match.group(1)
                parts = re.split(r"[>=<!\s;]", raw, maxsplit=1)
                name = parts[0].strip()
                version = raw[len(name) :].strip() if len(parts) > 0 else ""
                deps.append((name, version))
    return deps


def _check_python_deps(root: Path) -> str | None:
    deps: list[tuple[str, str]] = []
    source = ""

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = safe_read_text(pyproject)
            deps = _parse_pyproject_deps(content)
            source = "pyproject.toml"
        except Exception:
            pass

    req_file = root / "requirements.txt"
    if req_file.exists():
        try:
            content = safe_read_text(req_file)
            for line in content.split("\n"):
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                parts = re.split(r"[>=<!\s]", line, maxsplit=1)
                name = parts[0].strip()
                version = line[len(name) :].strip() if len(parts) > 0 else ""
                deps.append((name, version))
            source = source + " + requirements.txt" if source else "requirements.txt"
        except Exception:
            pass

    if not deps:
        return None

    lines = [f"## Python Dependencies ({source})"]
    lines.append(f"**Total**: {len(deps)}\n")

    unpinned = [(n, v) for n, v in deps if not v or v.startswith(">=") or v.startswith(">")]
    pinned = [(n, v) for n, v in deps if v and v.startswith("==")]
    ranged = [(n, v) for n, v in deps if v and not v.startswith("==") and v not in ("", ">", ">=")]

    if pinned:
        lines.append(f"**Pinned** (==): {len(pinned)}")
    if ranged:
        lines.append(f"**Ranged**: {len(ranged)}")
    if unpinned:
        lines.append(f"**Unpinned/loose**: {len(unpinned)}")

    names = [n.lower() for n, _ in deps]
    dupes = [n for n, c in Counter(names).items() if c > 1]
    if dupes:
        lines.append(f"\n**Duplicates found**: {', '.join(dupes)}")

    lines.append("\n### All Dependencies")
    for name, version in sorted(deps, key=lambda x: x[0].lower()):
        v_str = f" `{version}`" if version else " *(no version constraint)*"
        lines.append(f"- **{name}**{v_str}")

    return "\n".join(lines)


def _check_js_deps(root: Path) -> str | None:
    pkg_file = root / "package.json"
    if not pkg_file.exists():
        return None

    try:
        content = safe_read_text(pkg_file)
        data = json.loads(content)
    except Exception:
        return None

    prod_deps: dict[str, str] = data.get("dependencies", {})
    dev_deps: dict[str, str] = data.get("devDependencies", {})
    peer_deps: dict[str, str] = data.get("peerDependencies", {})

    if not prod_deps and not dev_deps:
        return None

    lines = ["## JavaScript/Node.js Dependencies (package.json)"]
    total = len(prod_deps) + len(dev_deps)
    lines.append(f"**Total**: {total} ({len(prod_deps)} prod, {len(dev_deps)} dev)")
    if peer_deps:
        lines.append(f"**Peer**: {len(peer_deps)}")

    all_names = list(prod_deps.keys()) + list(dev_deps.keys())
    dupes = [n for n, c in Counter(all_names).items() if c > 1]
    if dupes:
        lines.append(f"\n**In both prod & dev**: {', '.join(dupes)}")

    all_versions = {**prod_deps, **dev_deps}
    caret = sum(1 for v in all_versions.values() if v.startswith("^"))
    tilde = sum(1 for v in all_versions.values() if v.startswith("~"))
    exact = sum(1 for v in all_versions.values() if v and v[0].isdigit())

    if caret or tilde or exact:
        parts = []
        if caret:
            parts.append(f"^minor: {caret}")
        if tilde:
            parts.append(f"~patch: {tilde}")
        if exact:
            parts.append(f"exact: {exact}")
        lines.append(f"**Version strategy**: {', '.join(parts)}")

    if prod_deps:
        lines.append("\n### Production")
        for name in sorted(prod_deps.keys()):
            lines.append(f"- **{name}**: `{prod_deps[name]}`")
    if dev_deps:
        lines.append("\n### Development")
        for name in sorted(dev_deps.keys()):
            lines.append(f"- **{name}**: `{dev_deps[name]}`")

    lock_files = []
    for lf in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"):
        if (root / lf).exists():
            lock_files.append(lf)
    if lock_files:
        lines.append(f"\n**Lock file**: {', '.join(lock_files)}")
    else:
        lines.append("\n**Lock file**: MISSING (recommend running npm install / yarn)")

    return "\n".join(lines)


def _check_go_deps(root: Path) -> str | None:
    gomod = root / "go.mod"
    if not gomod.exists():
        return None
    try:
        content = safe_read_text(gomod)
    except Exception:
        return None

    deps: list[tuple[str, str]] = []
    in_require = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        if in_require and stripped:
            parts = stripped.split()
            if len(parts) >= 2:
                deps.append((parts[0], parts[1]))

    if not deps:
        return None

    lines = ["## Go Dependencies (go.mod)"]
    lines.append(f"**Total**: {len(deps)}\n")
    for name, version in sorted(deps, key=lambda x: x[0]):
        lines.append(f"- **{name}**: `{version}`")

    if (root / "go.sum").exists():
        lines.append("\n**Lock file**: go.sum present")

    return "\n".join(lines)


def _check_rust_deps(root: Path) -> str | None:
    cargo = root / "Cargo.toml"
    if not cargo.exists():
        return None
    try:
        content = safe_read_text(cargo)
    except Exception:
        return None

    deps: list[tuple[str, str]] = []
    in_deps = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "[dependencies]":
            in_deps = True
            continue
        if in_deps and stripped.startswith("["):
            break
        if in_deps and "=" in stripped:
            parts = stripped.split("=", 1)
            name = parts[0].strip()
            version = parts[1].strip().strip('"').strip("'")
            deps.append((name, version))

    if not deps:
        return None

    lines = ["## Rust Dependencies (Cargo.toml)"]
    lines.append(f"**Total**: {len(deps)}\n")
    for name, version in sorted(deps, key=lambda x: x[0]):
        lines.append(f"- **{name}**: `{version}`")

    if (root / "Cargo.lock").exists():
        lines.append("\n**Lock file**: Cargo.lock present")

    return "\n".join(lines)


def analyze_change_impact(file_path: str, root: Path) -> str:
    """Analyzes what would be affected if a file changes, using the import graph."""
    graph = build_import_graph(root, max_files=3000)
    norm_path = file_path.replace("\\", "/")

    if norm_path not in graph:
        return f"File `{norm_path}` not found in import graph. Is it a code file in the project?"

    direct_dependents: list[str] = []
    for source, targets in graph.items():
        if norm_path in targets and source != norm_path:
            direct_dependents.append(source)

    transitive: set[str] = set()
    queue = list(direct_dependents)
    visited: set[str] = {norm_path}
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        transitive.add(current)
        for source, targets in graph.items():
            if current in targets and source not in visited:
                queue.append(source)

    code_files = _iter_code_files(root, max_files=3000)
    related_tests: list[str] = []
    file_stem = Path(norm_path).stem
    for fpath, _ in code_files:
        fname = fpath.name.lower()
        if file_stem.lower() in fname and ("test" in fname or "spec" in fname):
            try:
                related_tests.append(str(fpath.relative_to(root)).replace("\\", "/"))
            except ValueError:
                pass

    for dep in sorted(transitive):
        dep_stem = Path(dep).stem
        for fpath, _ in code_files:
            fname = fpath.name.lower()
            rel = str(fpath.relative_to(root)).replace("\\", "/")
            if (
                dep_stem.lower() in fname
                and ("test" in fname or "spec" in fname)
                and rel not in related_tests
            ):
                related_tests.append(rel)

    lines = [f"# CHANGE IMPACT ANALYSIS: `{norm_path}`\n"]

    if direct_dependents:
        lines.append(f"## Direct Dependents ({len(direct_dependents)})")
        for dep in sorted(direct_dependents):
            lines.append(f"- `{dep}`")
    else:
        lines.append("## Direct Dependents\n- (none -- this file is a leaf)")

    indirect = sorted(transitive - set(direct_dependents))
    if indirect:
        lines.append(f"\n## Transitive Impact ({len(indirect)} more)")
        for dep in indirect:
            lines.append(f"- `{dep}`")

    if related_tests:
        lines.append(f"\n## Tests to Run ({len(related_tests)})")
        for t in sorted(set(related_tests)):
            lines.append(f"- `{t}`")
    else:
        lines.append("\n## Tests to Run\n- (no related test files found)")

    total = len(transitive)
    if total >= 10:
        level = "CRITICAL"
    elif total >= 5:
        level = "HIGH"
    elif total >= 2:
        level = "MEDIUM"
    elif total >= 1:
        level = "LOW"
    else:
        level = "MINIMAL"

    lines.append("\n## Risk Assessment")
    lines.append(f"- **Impact level**: {level}")
    lines.append(f"- **Total affected files**: {total}")
    lines.append(f"- **Direct dependents**: {len(direct_dependents)}")
    lines.append(f"- **Transitive dependents**: {len(indirect)}")
    lines.append(f"- **Related tests**: {len(related_tests)}")

    if total >= 5:
        lines.append("\n*This file has wide impact. Consider thorough testing and careful review.*")

    return "\n".join(lines)
