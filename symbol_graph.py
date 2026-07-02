"""
Symbol graph — AST-level call / inherit / implement relationships.

Builds a directed graph of *symbols* (functions, classes, methods, interfaces)
rather than files:

  calls:         symbol → symbols it calls
  inherits:      class → parent classes
  implements:    class → interfaces it implements
  defined_by:    symbol → file_path

Every relationship is concrete:

  "greet_user"  calls  "validate_email"
  "UserService" implements  "IUserRepository"
  "AdminUser"   inherits  "BaseUser"

The graph is built once, saved to .ai/symbol_graph.json, and served via
tier L1_symbol in query_router.
"""

from __future__ import annotations

import json
import os
import threading
import time as _time_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from ast_splitter import _parse_lock
from config import get_tool_budget_seconds, safe_read_text
from incremental_indexing import atomic_write
from logger import get_logger

logger = get_logger()

# Guards the module-level graph cache (get_or_build / invalidate) so two
# threads don't build the graph twice or observe a half-swapped cache.
_graph_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SymbolDef:
    """A single symbol definition (function, class, method, interface)."""

    name: str
    kind: str  # "function", "class", "method", "interface"
    file_path: str
    line_start: int
    line_end: int
    parent_class: str | None = None  # for methods: name of enclosing class
    base_classes: list[str] = field(default_factory=list)  # for classes
    interfaces: list[str] = field(default_factory=list)  # for classes (implements)

    @property
    def qualified_id(self) -> str:
        """Unique id: `path::Class.name` — same-named symbols in different
        files/classes no longer collapse into one graph node."""
        owner = f"{self.parent_class}." if self.parent_class else ""
        return f"{self.file_path}::{owner}{self.name}"


@dataclass
class SymbolRef:
    """A reference from one symbol to another (call, inherit, implement)."""

    from_symbol: str
    to_symbol: str
    kind: str  # "call", "inherit", "implement"
    file_path: str
    line: int


# Bump when the on-disk graph layout changes; older files are rebuilt.
GRAPH_FORMAT_VERSION = 3


@dataclass
class SymbolGraph:
    """
    The full symbol-level relationship graph.

    Keying scheme (format v3):
      * `symbols` is keyed by qualified id (`path::Class.name`) — unique per
        definition, so same-named symbols across files stay distinct nodes.
      * Forward edges map a *qualified* source to *bare* target names (call
        targets are unresolved names by nature); reverse maps go from a bare
        name back to the qualified ids that reference it.
      * `by_name` resolves a bare name to all its definitions.
    """

    symbols: dict[str, SymbolDef] = field(default_factory=dict)  # qid → def
    # Forward maps: qualified id → bare target names
    calls: dict[str, set[str]] = field(default_factory=dict)
    inherits: dict[str, set[str]] = field(default_factory=dict)
    implements: dict[str, set[str]] = field(default_factory=dict)
    uses: dict[str, set[str]] = field(default_factory=dict)
    # Derived indexes (rebuilt by reverse_indexes, not persisted)
    by_name: dict[str, list[str]] = field(default_factory=dict)  # name → [qid]
    _callers_of: dict[str, set[str]] = field(default_factory=dict)  # name → caller qids
    _implementors_of: dict[str, set[str]] = field(default_factory=dict)  # name → qids
    _subclasses_of: dict[str, set[str]] = field(default_factory=dict)  # name → qids
    _built_at: float = 0.0
    _file_count: int = 0

    def reverse_indexes(self) -> None:
        """Build derived indexes (by_name, callers_of, implementors_of, subclasses_of)."""
        self.by_name.clear()
        for qid, sym in self.symbols.items():
            self.by_name.setdefault(sym.name, []).append(qid)

        self._callers_of.clear()
        for caller_qid, callees in self.calls.items():
            for callee_name in callees:
                self._callers_of.setdefault(callee_name, set()).add(caller_qid)

        self._implementors_of.clear()
        for cls_qid, ifaces in self.implements.items():
            for iface_name in ifaces:
                self._implementors_of.setdefault(iface_name, set()).add(cls_qid)

        self._subclasses_of.clear()
        for child_qid, parents in self.inherits.items():
            for parent_name in parents:
                self._subclasses_of.setdefault(parent_name, set()).add(child_qid)

        # Also fill uses from all relationship types
        self.uses.clear()
        for src, targets in self.calls.items():
            self.uses.setdefault(src, set()).update(targets)
        for src, targets in self.inherits.items():
            self.uses.setdefault(src, set()).update(targets)
        for src, targets in self.implements.items():
            self.uses.setdefault(src, set()).update(targets)

    def defs_of(self, name: str) -> list[SymbolDef]:
        """All definitions of a bare symbol name (may be several files/classes)."""
        return [self.symbols[qid] for qid in self.by_name.get(name, []) if qid in self.symbols]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "version": GRAPH_FORMAT_VERSION,
            "symbols": {
                qid: {
                    "name": s.name,
                    "kind": s.kind,
                    "file_path": s.file_path,
                    "line_start": s.line_start,
                    "line_end": s.line_end,
                    "parent_class": s.parent_class,
                    "base_classes": s.base_classes,
                    "interfaces": s.interfaces,
                }
                for qid, s in self.symbols.items()
            },
            "calls": {k: sorted(v) for k, v in self.calls.items()},
            "inherits": {k: sorted(v) for k, v in self.inherits.items()},
            "implements": {k: sorted(v) for k, v in self.implements.items()},
            "built_at": self._built_at,
            "file_count": self._file_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SymbolGraph:
        """Deserialize from JSON dict (format v3)."""
        g = cls()
        for qid, sdata in data.get("symbols", {}).items():
            g.symbols[qid] = SymbolDef(
                name=sdata["name"],
                kind=sdata["kind"],
                file_path=sdata["file_path"],
                line_start=sdata.get("line_start", 0),
                line_end=sdata.get("line_end", 0),
                parent_class=sdata.get("parent_class"),
                base_classes=sdata.get("base_classes", []),
                interfaces=sdata.get("interfaces", []),
            )
        g.calls = {k: set(v) for k, v in data.get("calls", {}).items()}
        g.inherits = {k: set(v) for k, v in data.get("inherits", {}).items()}
        g.implements = {k: set(v) for k, v in data.get("implements", {}).items()}
        g._built_at = data.get("built_at", 0.0)
        g._file_count = data.get("file_count", 0)
        g.reverse_indexes()
        return g

    @property
    def symbol_count(self) -> int:
        return len(self.symbols)

    @property
    def call_count(self) -> int:
        return sum(len(v) for v in self.calls.values())

    @property
    def inherit_count(self) -> int:
        return sum(len(v) for v in self.inherits.values())

    @property
    def implement_count(self) -> int:
        return sum(len(v) for v in self.implements.values())


# ---------------------------------------------------------------------------
# Language-specific AST extraction
# ---------------------------------------------------------------------------

# File extension → tree-sitter language key (reuses ast_splitter.LANGUAGE_MAP)
LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
}

# AST node types that define a symbol
SYMBOL_DEF_TYPES: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "arrow_function": "function",
        "method_definition": "method",
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "method_signature": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "arrow_function": "function",
    },
    "tsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "method_signature": "method",
        "interface_declaration": "interface",
        "arrow_function": "function",
    },
    "java": {
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "constructor_declaration": "method",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "class",  # Go structs
    },
    "rust": {
        "function_item": "function",
        "struct_item": "class",
        "enum_item": "class",
        "trait_item": "interface",
        "impl_item": "class",
    },
    "ruby": {
        "method": "method",
        "class": "class",
        "module": "class",
    },
}

# Call-like node types per language
CALL_NODE_TYPES: dict[str, list[str]] = {
    "python": ["call"],
    "javascript": ["call_expression"],
    "typescript": ["call_expression"],
    "tsx": ["call_expression"],
    "java": ["method_invocation"],
    "go": ["call_expression"],
    "rust": ["call_expression", "macro_invocation"],
    "ruby": ["call", "command"],
}


# ---------------------------------------------------------------------------
# Parser loading (reuses ast_splitter logic)
# ---------------------------------------------------------------------------

_parsers: dict[str, Any] = {}


def _get_parser(language: str) -> Any | None:
    """Get or create a tree-sitter parser for the given language."""
    if language in _parsers:
        return _parsers[language]

    try:
        from tree_sitter import Language, Parser

        lang: Any = None

        if language == "python":
            import tree_sitter_python as mod

            lang = Language(mod.language())
        elif language == "javascript":
            import tree_sitter_javascript as mod

            lang = Language(mod.language())
        elif language in ("typescript", "tsx"):
            import tree_sitter_typescript as mod

            if language == "typescript":
                lang = Language(mod.language_typescript())
            else:
                lang = Language(mod.language_tsx())
        elif language == "java":
            import tree_sitter_java as mod

            lang = Language(mod.language())
        elif language == "go":
            import tree_sitter_go as mod

            lang = Language(mod.language())
        elif language == "rust":
            import tree_sitter_rust as mod

            lang = Language(mod.language())
        elif language == "ruby":
            import tree_sitter_ruby as mod

            lang = Language(mod.language())
        else:
            _parsers[language] = None
            return None

        parser = Parser(lang)
        _parsers[language] = parser
        return parser

    except Exception as e:
        logger.warning(f"Could not load tree-sitter parser for {language}: {e}")
        _parsers[language] = None
        return None


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _get_node_name(node: Any, source: bytes) -> str:
    """Extract the name of a node (function name, class name, etc.)."""
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier"):
            return child.text.decode("utf-8", errors="replace")
    return ""


def _get_named_children(node: Any) -> list[Any]:
    """Return named children (skip syntax nodes like 'def', 'class', etc.)."""
    return [c for c in node.children if c.is_named]


def _find_call_name(call_node: Any, source: bytes) -> str | None:
    """Given a call_expression node, extract the function name being called."""
    # For Python: call → primary → identifier
    # For JS/TS: call_expression → member_expression | identifier
    for child in call_node.children:
        if child.type in ("identifier", "property_identifier"):
            return child.text.decode("utf-8", errors="replace")
    # Try to find the function part
    func_part = call_node.child_by_field_name("function")
    if func_part is not None:
        if func_part.type == "identifier":
            return func_part.text.decode("utf-8", errors="replace")
        if func_part.type == "member_expression":
            obj = func_part.child_by_field_name("object")
            prop = func_part.child_by_field_name("property")
            if obj is not None and prop is not None:
                obj_name = obj.text.decode("utf-8", errors="replace")
                prop_name = prop.text.decode("utf-8", errors="replace")
                if obj_name != "self" and obj_name != "this":
                    # obj.method() — we store the method for now; call resolution
                    # could map it to ClassName.method if we have enough info
                    pass
                return prop_name
        if func_part.type == "attribute":
            # Python obj.method()
            obj = func_part.child_by_field_name("object")
            attr = func_part.child_by_field_name("attribute")
            if attr is not None:
                return attr.text.decode("utf-8", errors="replace")
    return None


def _get_imported_names(root_node: Any, source: bytes) -> set[str]:
    """Extract all names imported in a file (Python import statements)."""
    imported: set[str] = set()
    for node in root_node.children:
        if node.type == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        imported.add(name_node.text.decode("utf-8", errors="replace"))
                    else:
                        # Simple import
                        txt = child.text.decode("utf-8", errors="replace").strip()
                        imported.add(txt.split(".")[0])
        elif node.type == "import_from_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        imported.add(name_node.text.decode("utf-8", errors="replace"))
                    else:
                        txt = child.text.decode("utf-8", errors="replace").strip()
                        imported.add(txt)
    return imported


def _get_class_bases(class_node: Any, source: bytes) -> list[str]:
    """Extract base class names from a class definition (all supported languages)."""
    bases: list[str] = []

    def collect_names(n: Any) -> None:
        if n.type in (
            "identifier",
            "attribute",
            "type_identifier",
            "constant",
            "scoped_identifier",
            "scoped_type_identifier",
            "nested_type_identifier",
        ):
            bases.append(n.text.decode("utf-8", errors="replace"))
            return
        for c in n.children:
            collect_names(c)

    for child in class_node.children:
        # Python: class X(Base, ...)
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type in ("identifier", "attribute"):
                    bases.append(arg.text.decode("utf-8", errors="replace"))
        # Java: class X extends Base / Ruby: class X < Base / TS interface extends
        elif child.type in ("superclass", "extends_clause"):
            collect_names(child)
        # JS/TS: class X extends Base [implements I] — heritage wrapper node;
        # the implements part is handled by _get_implements_interfaces.
        elif child.type == "class_heritage":
            for sub in child.children:
                if sub.type == "implements_clause":
                    continue
                collect_names(sub)
    return bases


def _get_implements_interfaces(class_node: Any, source: bytes) -> list[str]:
    """Extract implemented interface names from a class (JS/TS/Java)."""
    ifaces: list[str] = []
    # TS: class X implements I { ... }
    for child in class_node.children:
        if child.type == "implements_clause":
            for item in child.children:
                if item.type in ("type_identifier", "identifier"):
                    ifaces.append(item.text.decode("utf-8", errors="replace"))
    # Java: class X implements I { ... }
    for child in class_node.children:
        if child.type == "super_interfaces":
            for item in child.children:
                if item.type in ("type_identifier", "identifier"):
                    ifaces.append(item.text.decode("utf-8", errors="replace"))
    return ifaces


# Ubiquitous builtin / stdlib / method names that would flood the call graph
# with meaningless edges if recorded.
_COMMON_BUILTIN_CALLS: frozenset[str] = frozenset(
    {
        # Python builtins
        "print", "len", "str", "int", "float", "bool", "list", "dict", "set",
        "tuple", "frozenset", "range", "enumerate", "zip", "map", "filter",
        "sorted", "reversed", "isinstance", "issubclass", "hasattr", "getattr",
        "setattr", "delattr", "super", "type", "repr", "id", "min", "max",
        "sum", "abs", "round", "open", "iter", "next", "format", "vars", "dir",
        "callable", "hash", "input", "property", "staticmethod", "classmethod",
        # common method-call noise
        "append", "extend", "insert", "remove", "pop", "clear", "copy",
        "update", "get", "keys", "values", "items", "add", "discard", "join",
        "split", "strip", "lstrip", "rstrip", "replace", "startswith",
        "endswith", "lower", "upper", "encode", "decode", "find", "index",
        "count", "sort", "reverse", "read", "write", "close", "flush",
        # JS/TS builtins & noise
        "require", "parseInt", "parseFloat", "log", "warn", "error", "info",
        "debug", "push", "shift", "unshift", "slice", "splice", "concat",
        "includes", "indexOf", "forEach", "reduce", "then", "catch", "finally",
        "resolve", "reject", "stringify", "parse", "assign", "freeze",
        "toString",
        # misc cross-language
        "assert", "panic", "println", "printf", "sprintf",
    }
)


def _collect_calls(node: Any, source: bytes, language: str) -> list[tuple[str, int]]:
    """
    Collect (call_name, line) pairs from an AST node.

    Nested symbol definitions are NOT descended into — their calls belong to
    the nested symbol itself, otherwise a class would appear to "call"
    everything any of its methods call (double attribution).
    """
    call_types = set(CALL_NODE_TYPES.get(language, []))
    def_types = set(SYMBOL_DEF_TYPES.get(language, {}))
    calls: list[tuple[str, int]] = []

    def walk(n: Any) -> None:
        if n.type in call_types:
            name = _find_call_name(n, source)
            if name:
                calls.append((name, n.start_point[0] + 1))
        for child in n.children:
            if child.type in def_types:
                continue
            walk(child)

    walk(node)
    return calls


# ---------------------------------------------------------------------------
# Per-file symbol extraction
# ---------------------------------------------------------------------------


def _extract_symbols_from_file(
    file_path: Path,
    language: str,
    parser: Any,
    source: bytes,
    rel_path: str | None = None,
) -> tuple[dict[str, SymbolDef], list[SymbolRef]]:
    """
    Extract all symbol definitions and references from a single file.

    Args:
        rel_path: Project-relative posix path stored in the graph, so symbol
            hits fuse with the other search tiers (which use relative posix
            paths) instead of absolute Windows paths. Falls back to file_path.
    """
    stored_path = rel_path if rel_path is not None else str(file_path)
    symbols: dict[str, SymbolDef] = {}
    local_names: set[str] = set()  # bare names defined in this file
    refs: list[SymbolRef] = []
    with _parse_lock:
        tree = parser.parse(source)
    root = tree.root_node

    def_types = SYMBOL_DEF_TYPES.get(language, {})
    imported_names: set[str] = set()

    # For Python, collect imports
    if language == "python":
        imported_names = _get_imported_names(root, source)

    def _walk(node: Any, parent_class: str | None = None) -> None:
        node_type = node.type
        kind = def_types.get(node_type)
        if kind:
            name = _get_node_name(node, source)
            if not name:
                for child in node.children:
                    _walk(child, parent_class)
                return

            # Build SymbolDef
            sd_kind = kind
            if kind == "method" and parent_class:
                sd_kind = "method"

            line_start = node.start_point[0] + 1
            line_end = node.end_point[0] + 1

            bases: list[str] = []
            ifaces: list[str] = []

            if kind == "class":
                bases = _get_class_bases(node, source)
                ifaces = _get_implements_interfaces(node, source)

            sym_def = SymbolDef(
                name=name,
                kind=sd_kind,
                file_path=stored_path,
                line_start=line_start,
                line_end=line_end,
                parent_class=parent_class,
                base_classes=bases,
                interfaces=ifaces,
            )
            qid = sym_def.qualified_id
            if qid in symbols:
                # Same name at same scope (overloads, conditional defs):
                # disambiguate by line so neither definition is lost.
                qid = f"{qid}#{line_start}"
            symbols[qid] = sym_def
            local_names.add(name)

            # Record inherit refs
            for base in bases:
                refs.append(
                    SymbolRef(
                        from_symbol=qid,
                        to_symbol=base,
                        kind="inherit",
                        file_path=stored_path,
                        line=line_start,
                    )
                )

            # Record implement refs
            for iface in ifaces:
                refs.append(
                    SymbolRef(
                        from_symbol=qid,
                        to_symbol=iface,
                        kind="implement",
                        file_path=stored_path,
                        line=line_start,
                    )
                )

            # Collect call refs from this node's body
            call_names = _collect_calls(node, source, language)
            for cname, call_line in call_names:
                if cname in _COMMON_BUILTIN_CALLS:
                    continue
                # Keep known symbols, imported names, and plausible identifiers
                if (
                    cname in local_names
                    or cname in imported_names
                    or (cname.isidentifier() and len(cname) > 2)
                ):
                    refs.append(
                        SymbolRef(
                            from_symbol=qid,
                            to_symbol=cname,
                            kind="call",
                            file_path=stored_path,
                            line=call_line,
                        )
                    )

            # Recurse into children for nested definitions (methods in class body)
            body = node.child_by_field_name("body")
            if body:
                new_parent = name if kind in ("class", "interface") else parent_class
                for child in body.children:
                    _walk(child, new_parent)
            else:
                for child in node.children:
                    if child.type in ("block", "class_body", "declaration_list"):
                        new_parent = name if kind in ("class", "interface") else parent_class
                        for c2 in child.children:
                            _walk(c2, new_parent)
                    elif child.type == "body":
                        for c2 in child.children:
                            _walk(c2, parent_class)
        else:
            # Walk children (for call expressions at module level)
            for child in node.children:
                _walk(child, parent_class)

    for node in root.children:
        _walk(node, None)

    return symbols, refs


# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------


def build_symbol_graph(
    root_dir: Path,
    max_files: int | None = None,
    budget_seconds: float | None = None,
) -> SymbolGraph:
    """
    Build a SymbolGraph by parsing every indexable source file.

    Args:
        root_dir: Project root directory.
        max_files: Max files to process (None = config default).
        budget_seconds: Max wall-clock seconds (None = config default).

    Returns:
        Populated SymbolGraph (may be partial if budget exhausted).
    """
    if max_files is None:
        max_files = _get_max_files()
    if budget_seconds is None:
        budget_seconds = get_tool_budget_seconds()

    graph = SymbolGraph()
    t_start = _time_module.monotonic()
    file_count = 0
    skipped_no_parser = 0

    try:
        from config import (
            BINARY_EXTENSIONS,
            INDEXABLE_EXTENSIONS,
            get_ignored_dirs,
            get_max_file_size_bytes,
            is_dir_ignored,
        )
    except ImportError:
        logger.error("Cannot import config; build_symbol_graph aborted")
        return graph

    ignored_dirs = get_ignored_dirs()
    max_size = get_max_file_size_bytes()

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Filter ignored dirs
        dirnames[:] = [
            d for d in dirnames if d not in ignored_dirs and not is_dir_ignored(d)
        ]

        for fname in filenames:
            if file_count >= max_files:
                logger.info(
                    f"Symbol graph: hit max_files={max_files}, stopping scan"
                )
                graph._file_count = file_count
                _finalize_graph(graph)
                return graph

            elapsed = _time_module.monotonic() - t_start
            if elapsed > budget_seconds:
                logger.info(
                    f"Symbol graph: budget {budget_seconds:.0f}s exhausted "
                    f"after {file_count} files"
                )
                graph._file_count = file_count
                _finalize_graph(graph)
                return graph

            fp = Path(dirpath) / fname
            suffix = fp.suffix.lower()

            if suffix in BINARY_EXTENSIONS or suffix not in INDEXABLE_EXTENSIONS:
                continue

            try:
                if fp.stat().st_size > max_size:
                    continue
            except OSError:
                continue

            language = LANGUAGE_MAP.get(suffix)
            if language is None:
                continue

            parser = _get_parser(language)
            if parser is None:
                skipped_no_parser += 1
                continue

            # Read and parse
            try:
                content = safe_read_text(fp)
                if not content.strip():
                    continue
                source = content.encode("utf-8")
            except Exception:
                continue

            try:
                rel_path = fp.relative_to(root_dir).as_posix()
            except ValueError:
                rel_path = fp.as_posix()

            try:
                symbols, refs = _extract_symbols_from_file(
                    fp, language, parser, source, rel_path
                )
            except Exception as e:
                logger.debug(f"Symbol graph: parse error in {fp}: {e}")
                continue

            # Merge symbols
            graph.symbols.update(symbols)

            # Merge refs into graph
            for ref in refs:
                if ref.kind == "call":
                    graph.calls.setdefault(ref.from_symbol, set()).add(ref.to_symbol)
                elif ref.kind == "inherit":
                    graph.inherits.setdefault(ref.from_symbol, set()).add(
                        ref.to_symbol
                    )
                elif ref.kind == "implement":
                    graph.implements.setdefault(ref.from_symbol, set()).add(
                        ref.to_symbol
                    )

            file_count += 1

            if file_count % 200 == 0:
                logger.debug(
                    f"Symbol graph: {file_count} files processed, "
                    f"{len(graph.symbols)} symbols found"
                )

    graph._file_count = file_count
    if skipped_no_parser:
        logger.info(
            f"Symbol graph: skipped {skipped_no_parser} files "
            f"(no tree-sitter parser available)"
        )
    _finalize_graph(graph)
    return graph


def _finalize_graph(graph: SymbolGraph) -> None:
    """Build reverse indexes and set timestamp."""
    graph.reverse_indexes()
    graph._built_at = _time_module.time()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_symbol_graph(graph: SymbolGraph, path: Path | None = None) -> None:
    """Save the symbol graph to disk as JSON."""
    if path is None:
        path = _graph_path()
    data = graph.to_dict()
    try:
        atomic_write(path, json.dumps(data, indent=2, default=str))
        logger.info(
            f"Symbol graph saved: {graph.symbol_count} symbols, "
            f"{graph.call_count} calls, "
            f"{graph.inherit_count} inherits, "
            f"{graph.implement_count} implements → {path}"
        )
    except Exception as e:
        logger.error(f"Failed to save symbol graph: {e}")


def load_symbol_graph(path: Path | None = None) -> SymbolGraph | None:
    """Load the symbol graph from disk, or None if missing/corrupt."""
    if path is None:
        path = _graph_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != GRAPH_FORMAT_VERSION:
            logger.info(
                "Symbol graph on disk uses an older format — will rebuild"
            )
            return None
        return SymbolGraph.from_dict(data)
    except Exception as e:
        logger.warning(f"Failed to load symbol graph: {e}")
        return None


def is_symbol_graph_stale(graph: SymbolGraph, root_dir: Path | None = None) -> bool:
    """Check if the graph is older than any source file (meaning rebuild needed)."""
    if root_dir is None:
        root_dir = config.PROJECT_ROOT
    graph_mtime = graph._built_at
    if graph_mtime == 0:
        return True

    # Preferred check: the incremental-index metadata tracks the mtime of
    # every indexed file — far more reliable than sampling top-level dirs.
    try:
        if config.INDEX_METADATA_FILE.exists():
            data = json.loads(config.INDEX_METADATA_FILE.read_text(encoding="utf-8"))
            if data:
                newest = max(
                    (float(info.get("mtime", 0.0) or 0.0) for info in data.values()),
                    default=0.0,
                )
                return newest > graph_mtime
    except Exception:
        pass  # fall back to the directory-sampling heuristic below
    # Quick check: scan a few top-level files
    check_count = 0
    try:
        for entry in os.scandir(root_dir):
            if check_count > 50:
                break
            try:
                if entry.is_file() and entry.stat().st_mtime > graph_mtime:
                    return True
            except OSError:
                continue
            check_count += 1
    except OSError:
        pass
    # Slightly deeper: check .py files in top-level directories
    try:
        for entry in os.scandir(root_dir):
            if entry.is_dir() and not entry.name.startswith("."):
                try:
                    for sub in os.scandir(entry.path):
                        if sub.is_file() and sub.name.endswith(".py"):
                            if sub.stat().st_mtime > graph_mtime:
                                return True
                            check_count += 1
                            if check_count > 100:
                                break
                except OSError:
                    continue
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# High-level API (lazy build)
# ---------------------------------------------------------------------------

_cached_graph: SymbolGraph | None = None
_cache_path: Path | None = None


def _graph_path() -> Path:
    return config.AI_DIR / "symbol_graph.json"


def _get_max_files() -> int:
    env_val = os.getenv("PROJECTMIND_SYMBOL_GRAPH_MAX_FILES")
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    return 8000


def get_or_build_symbol_graph(force: bool = False) -> SymbolGraph:
    """
    Returns the current SymbolGraph, building it if necessary.

    Args:
        force: If True, rebuilds even if the cached/stored graph exists.

    Returns:
        SymbolGraph instance (may be empty if build fails).
    """
    global _cached_graph, _cache_path

    with _graph_lock:
        graph_path = _graph_path()

        # Invalidate cache if path changed (reconfigure called)
        if _cache_path is not None and _cache_path != graph_path:
            _cached_graph = None
        _cache_path = graph_path

        if _cached_graph is not None and not force:
            return _cached_graph

        # Try loading from disk first
        if not force:
            loaded = load_symbol_graph(graph_path)
            if loaded is not None:
                # Check if stale
                if not is_symbol_graph_stale(loaded):
                    _cached_graph = loaded
                    return loaded
                logger.info("Symbol graph is stale; rebuilding...")

        # Build from scratch
        logger.info("Building symbol graph...")
        graph = build_symbol_graph(config.PROJECT_ROOT)

        # Save for next time
        save_symbol_graph(graph, graph_path)

        _cached_graph = graph
        return graph


def peek_symbol_graph() -> SymbolGraph | None:
    """
    Return the cached or persisted graph WITHOUT triggering a build.

    For latency-sensitive callers (query tiers) that must not pay the
    full-repo parse cost inside a search request. Returns None when no
    up-to-date graph is available yet.
    """
    global _cached_graph, _cache_path
    with _graph_lock:
        graph_path = _graph_path()
        if _cache_path == graph_path and _cached_graph is not None:
            return _cached_graph
        loaded = load_symbol_graph(graph_path)
        if loaded is not None:
            _cached_graph = loaded
            _cache_path = graph_path
        return loaded


def invalidate_symbol_graph_cache() -> None:
    """Reset the in-memory symbol graph cache."""
    global _cached_graph, _cache_path
    with _graph_lock:
        _cached_graph = None
        _cache_path = None


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


def _ensure_graph() -> SymbolGraph:
    """Get the graph, building lazily if needed."""
    return get_or_build_symbol_graph(force=False)


def _qid_info(g: SymbolGraph, qid: str, **extra: Any) -> dict[str, Any]:
    """Format a qualified id as a result row (falls back gracefully)."""
    sym = g.symbols.get(qid)
    row = {
        "symbol": sym.name if sym else qid,
        "qualified": qid,
        "kind": sym.kind if sym else "unknown",
        "file": sym.file_path if sym else "unknown",
        "line": sym.line_start if sym else 0,
    }
    row.update(extra)
    return row


def _name_info(g: SymbolGraph, name: str, **extra: Any) -> list[dict[str, Any]]:
    """Format a bare name as result rows — one per known definition."""
    defs = g.defs_of(name)
    if not defs:
        row = {"symbol": name, "qualified": None, "kind": "unknown", "file": "unknown", "line": 0}
        row.update(extra)
        return [row]
    rows = []
    for sym in defs:
        row = {
            "symbol": sym.name,
            "qualified": sym.qualified_id,
            "kind": sym.kind,
            "file": sym.file_path,
            "line": sym.line_start,
        }
        row.update(extra)
        rows.append(row)
    return rows


def find_callers(symbol_name: str) -> list[dict[str, Any]]:
    """Return all symbols that call `symbol_name`."""
    g = _ensure_graph()
    caller_qids = g._callers_of.get(symbol_name, set())
    return [_qid_info(g, qid) for qid in sorted(caller_qids)]


def find_callees(symbol_name: str) -> list[dict[str, Any]]:
    """Return all symbols called by any definition of `symbol_name`."""
    g = _ensure_graph()
    callee_names: set[str] = set()
    for qid in g.by_name.get(symbol_name, []):
        callee_names.update(g.calls.get(qid, set()))
    results: list[dict[str, Any]] = []
    for callee in sorted(callee_names):
        results.extend(_name_info(g, callee))
    return results


def find_implementors(interface_name: str) -> list[dict[str, Any]]:
    """Return all classes that implement `interface_name`."""
    g = _ensure_graph()
    impl_qids = g._implementors_of.get(interface_name, set())
    return [_qid_info(g, qid) for qid in sorted(impl_qids)]


def find_base_classes(class_name: str) -> list[dict[str, Any]]:
    """Return base (parent) classes of `class_name`."""
    g = _ensure_graph()
    base_names: set[str] = set()
    for qid in g.by_name.get(class_name, []):
        base_names.update(g.inherits.get(qid, set()))
    results: list[dict[str, Any]] = []
    for base in sorted(base_names):
        results.extend(_name_info(g, base))
    return results


def find_subclasses(class_name: str) -> list[dict[str, Any]]:
    """Return all classes that inherit from `class_name`."""
    g = _ensure_graph()
    sub_qids = g._subclasses_of.get(class_name, set())
    return [_qid_info(g, qid) for qid in sorted(sub_qids)]


def find_usages(symbol_name: str) -> list[dict[str, Any]]:
    """Return all symbols that use `symbol_name` in any way."""
    g = _ensure_graph()
    results: list[dict[str, Any]] = []
    for qid in sorted(g._callers_of.get(symbol_name, set())):
        results.append(_qid_info(g, qid, relation="calls"))
    for qid in sorted(g._subclasses_of.get(symbol_name, set())):
        results.append(_qid_info(g, qid, relation="inherits"))
    for qid in sorted(g._implementors_of.get(symbol_name, set())):
        results.append(_qid_info(g, qid, relation="implements"))
    return results


def find_entry_points(module_path: str) -> list[dict[str, Any]]:
    """Return public/exported symbols defined in a module."""
    g = _ensure_graph()
    # Graph paths are relative posix; normalize Windows-style input to match.
    module_path = module_path.replace("\\", "/")
    results: list[dict[str, Any]] = []
    for sym in g.symbols.values():
        sym_path = sym.file_path.replace("\\", "/")
        if sym_path == module_path or sym_path.endswith("/" + module_path):
            if sym.kind != "method" or sym.parent_class is None:
                results.append(
                    {
                        "symbol": sym.name,
                        "kind": sym.kind,
                        "file": sym.file_path,
                        "line": sym.line_start,
                    }
                )
    return sorted(results, key=lambda r: r["line"])


def get_symbol_info(symbol_name: str) -> dict[str, Any] | None:
    """
    Return full information about a symbol.

    Accepts a bare name (`run`) or a qualified id (`path::Class.run`). For a
    bare name with several definitions, the first is primary and the rest are
    listed under `other_definitions`.
    """
    g = _ensure_graph()
    if symbol_name in g.symbols:
        qids = [symbol_name]
    else:
        qids = g.by_name.get(symbol_name, [])
    if not qids:
        return None

    sym = g.symbols[qids[0]]
    callee_names: set[str] = set()
    for qid in qids:
        callee_names.update(g.calls.get(qid, set()))

    return {
        "name": sym.name,
        "qualified": qids[0],
        "kind": sym.kind,
        "file": sym.file_path,
        "lines": f"{sym.line_start}-{sym.line_end}",
        "parent_class": sym.parent_class,
        "base_classes": sym.base_classes,
        "interfaces": sym.interfaces,
        "other_definitions": [
            {"qualified": q, "file": g.symbols[q].file_path, "line": g.symbols[q].line_start}
            for q in qids[1:]
            if q in g.symbols
        ],
        "callers": sorted(g._callers_of.get(sym.name, set())),
        "callees": sorted(callee_names),
        "subclasses": sorted(g._subclasses_of.get(sym.name, set())),
        "implementors": sorted(g._implementors_of.get(sym.name, set())),
    }


def query_symbol_graph(
    query_text: str, n_results: int = 8
) -> list[dict[str, Any]]:
    """
    Search the symbol graph by name.

    Args:
        query_text: Text to search for in symbol names.
        n_results: Maximum results to return.

    Returns:
        List of hits compatible with query_router.QueryHit source data.
    """
    g = _ensure_graph()
    if not g.symbols:
        return []

    # Symbol keys are case-sensitive (e.g. class names); compare lowercased on
    # both sides so an exact match actually fires for capitalized names.
    ql = query_text.strip().lower()
    results: list[dict[str, Any]] = []

    def _make_hit(qid: str, sym: SymbolDef, score: float) -> dict[str, Any]:
        n_callers = len(g._callers_of.get(sym.name, set()))
        n_callees = len(g.calls.get(qid, set()))
        n_subs = len(g._subclasses_of.get(sym.name, set()))
        return {
            "source": sym.file_path,
            "score": score,
            "tier": "L1_symbol",
            "snippet": (
                f"{sym.kind} `{sym.name}` "
                f"({n_callers} callers, {n_callees} callees, {n_subs} subclasses)"
            ),
            "extra": {
                "symbol_name": sym.name,
                "symbol_qualified": qid,
                "symbol_kind": sym.kind,
                "line": sym.line_start,
                "callers": n_callers,
                "callees": n_callees,
            },
        }

    # Exact (case-insensitive) matches first
    exact_matched: set[str] = set()
    for qid, sym in g.symbols.items():
        if sym.name.lower() == ql:
            results.append(_make_hit(qid, sym, 1.0))
            exact_matched.add(qid)
            if len(results) >= n_results:
                return results

    # Substring / contains match
    for qid, sym in g.symbols.items():
        if qid in exact_matched:
            continue
        if ql in sym.name.lower():
            results.append(_make_hit(qid, sym, 0.75))
            if len(results) >= n_results:
                break

    return results
