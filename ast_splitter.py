from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import CHUNK_OVERLAP, CHUNK_SIZE
from file_scanner import looks_minified
from logger import get_logger

logger = get_logger()

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

TOP_LEVEL_NODES: dict[str, list[str]] = {
    "python": ["function_definition", "class_definition", "decorated_definition"],
    "javascript": [
        "function_declaration",
        "class_declaration",
        "export_statement",
        "lexical_declaration",
    ],
    "typescript": [
        "function_declaration",
        "class_declaration",
        "export_statement",
        "interface_declaration",
        "type_alias_declaration",
    ],
    "tsx": [
        "function_declaration",
        "class_declaration",
        "export_statement",
        "interface_declaration",
    ],
    "java": ["class_declaration", "interface_declaration", "enum_declaration"],
    "go": ["function_declaration", "method_declaration", "type_declaration"],
    "rust": ["function_item", "impl_item", "struct_item", "enum_item", "trait_item"],
    "ruby": ["method", "class", "module"],
}

METHOD_NODES: dict[str, list[str]] = {
    "python": ["function_definition"],
    "javascript": ["method_definition", "function_declaration", "arrow_function"],
    "typescript": ["method_definition", "method_signature", "function_declaration"],
    "tsx": ["method_definition", "method_signature", "function_declaration"],
    "java": ["method_declaration", "constructor_declaration"],
    "go": ["method_declaration", "function_declaration"],
    "rust": ["function_item"],
    "ruby": ["method"],
}

NAME_FIELD: dict[str, str] = {
    "python": "name",
    "javascript": "name",
    "typescript": "name",
    "tsx": "name",
    "java": "name",
    "go": "name",
    "rust": "name",
    "ruby": "name",
}

_parsers: dict[str, Any] = {}

# tree-sitter Parser objects are NOT thread-safe: concurrent parse() calls on
# a shared parser can crash the interpreter. All parser creation and parsing
# is serialized through this lock (parsing is cheap next to embedding).
_parse_lock = threading.RLock()


def _get_parser(language: str) -> Any | None:
    with _parse_lock:
        return _get_parser_locked(language)


def _get_parser_locked(language: str) -> Any | None:
    if language in _parsers:
        return _parsers[language]

    try:
        from tree_sitter import Language, Parser

        if language == "python":
            import tree_sitter_python as mod
        elif language == "javascript":
            import tree_sitter_javascript as mod
        elif language == "typescript":
            import tree_sitter_typescript as mod

            lang = Language(mod.language_typescript())
            parser = Parser(lang)
            _parsers[language] = parser
            return parser
        elif language == "tsx":
            import tree_sitter_typescript as mod

            lang = Language(mod.language_tsx())
            parser = Parser(lang)
            _parsers[language] = parser
            return parser
        elif language == "java":
            import tree_sitter_java as mod
        elif language == "go":
            import tree_sitter_go as mod
        elif language == "rust":
            import tree_sitter_rust as mod
        elif language == "ruby":
            import tree_sitter_ruby as mod
        else:
            return None

        lang = Language(mod.language())
        parser = Parser(lang)
        _parsers[language] = parser
        return parser

    except Exception as e:
        logger.warning(f"Could not load tree-sitter parser for {language}: {e}")
        _parsers[language] = None
        return None


def _get_node_name(node: Any, source: bytes) -> str:
    for child in node.children:
        if child.type == "identifier" or child.type == "name":
            return child.text.decode("utf-8", errors="replace")
    if node.start_byte < len(source):
        first_line = source[node.start_byte : node.start_byte + 80].decode(
            "utf-8", errors="replace"
        )
        return first_line.split("\n")[0].strip()[:60]
    return "unknown"


def _extract_class_chunks(
    class_node: Any,
    source: bytes,
    language: str,
    file_path: Path,
    text_splitter: RecursiveCharacterTextSplitter,
) -> list[dict[str, Any]]:
    chunks = []
    class_name = _get_node_name(class_node, source)
    method_types = METHOD_NODES.get(language, [])

    class_text = class_node.text.decode("utf-8", errors="replace")
    body_node = None
    for child in class_node.children:
        if child.type in ("block", "class_body", "declaration_list"):
            body_node = child
            break

    if body_node is None:
        chunks.extend(
            _make_text_chunks(
                class_text,
                str(file_path),
                "class",
                class_name,
                None,
                class_node.start_point[0] + 1,
                class_node.end_point[0] + 1,
                text_splitter,
            )
        )
        return chunks

    has_methods = False
    for child in body_node.children:
        if child.type in method_types:
            has_methods = True
            method_name = _get_node_name(child, source)
            method_text = child.text.decode("utf-8", errors="replace")
            context_prefix = f"# Class: {class_name}\n"
            full_text = context_prefix + method_text
            chunks.extend(
                _make_text_chunks(
                    full_text,
                    str(file_path),
                    "method",
                    method_name,
                    class_name,
                    child.start_point[0] + 1,
                    child.end_point[0] + 1,
                    text_splitter,
                )
            )

    if not has_methods:
        chunks.extend(
            _make_text_chunks(
                class_text,
                str(file_path),
                "class",
                class_name,
                None,
                class_node.start_point[0] + 1,
                class_node.end_point[0] + 1,
                text_splitter,
            )
        )

    return chunks


def _make_text_chunks(
    text: str,
    source_path: str,
    symbol_type: str,
    symbol_name: str,
    class_name: str | None,
    line_start: int,
    line_end: int,
    text_splitter: RecursiveCharacterTextSplitter,
) -> list[dict[str, Any]]:
    if len(text) <= CHUNK_SIZE:
        return [
            {
                "text": text,
                "metadata": {
                    "source": source_path,
                    "symbol_type": symbol_type,
                    "symbol_name": symbol_name,
                    "class_name": class_name or "",
                    "line_start": line_start,
                    "line_end": line_end,
                    "chunk_index": 0,
                },
            }
        ]

    sub_chunks = text_splitter.split_text(text)
    result = []
    for i, sub in enumerate(sub_chunks):
        result.append(
            {
                "text": sub,
                "metadata": {
                    "source": source_path,
                    "symbol_type": symbol_type,
                    "symbol_name": symbol_name,
                    "class_name": class_name or "",
                    "line_start": line_start,
                    "line_end": line_end,
                    "chunk_index": i,
                },
            }
        )
    return result


class ASTSplitter:
    def __init__(self) -> None:
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
        )

    def split(self, content: str, file_path: Path) -> list[dict[str, Any]]:
        language = LANGUAGE_MAP.get(file_path.suffix.lower())
        # Minified bundles are parsed as one enormous line: tree-sitter can sit
        # on it for minutes while holding the process-wide parse lock, stalling
        # every concurrent search. Text-split them instead.
        if language and looks_minified(content):
            logger.info(f"Skipping AST split for minified/generated file: {file_path}")
            language = None
        if language:
            parser = _get_parser(language)
            if parser:
                try:
                    return self._split_by_ast(content, language, parser, file_path)
                except Exception as e:
                    logger.warning(f"AST split failed for {file_path}, falling back: {e}")

        return self._split_by_text(content, file_path)

    def _split_by_ast(
        self, content: str, language: str, parser: Any, file_path: Path
    ) -> list[dict[str, Any]]:
        source = content.encode("utf-8")
        with _parse_lock:
            tree = parser.parse(source)
        root = tree.root_node

        top_types = TOP_LEVEL_NODES.get(language, [])
        chunks: list[dict[str, Any]] = []
        covered_ranges: list[tuple[int, int]] = []

        for node in root.children:
            actual_node = node
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type in ("function_definition", "class_definition"):
                        actual_node = child
                        break

            if node.type == "export_statement":
                for child in node.children:
                    if child.type in top_types:
                        actual_node = child
                        break

            if actual_node.type not in top_types and node.type not in top_types:
                continue

            node_text = node.text.decode("utf-8", errors="replace") if node.text else ""
            if not node_text.strip():
                continue

            covered_ranges.append((node.start_byte, node.end_byte))

            if actual_node.type == "class_definition" or actual_node.type == "class_declaration":
                chunks.extend(
                    _extract_class_chunks(
                        actual_node, source, language, file_path, self._text_splitter
                    )
                )
            else:
                symbol_name = _get_node_name(actual_node, source)
                chunks.extend(
                    _make_text_chunks(
                        node_text,
                        str(file_path),
                        "function",
                        symbol_name,
                        None,
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                        self._text_splitter,
                    )
                )

        if not chunks:
            return self._split_by_text(content, file_path)

        module_lines = []
        for node in root.children:
            is_covered = any(s <= node.start_byte < e for s, e in covered_ranges)
            if not is_covered and node.text:
                line = node.text.decode("utf-8", errors="replace").strip()
                if line:
                    module_lines.append(line)

        if module_lines:
            module_text = "\n".join(module_lines)
            chunks.extend(
                _make_text_chunks(
                    module_text,
                    str(file_path),
                    "module",
                    "module_level",
                    None,
                    1,
                    root.end_point[0] + 1,
                    self._text_splitter,
                )
            )

        return chunks

    def _split_by_text(self, content: str, file_path: Path) -> list[dict[str, Any]]:
        sub_chunks = self._text_splitter.split_text(content)
        return [
            {
                "text": chunk,
                "metadata": {
                    "source": str(file_path),
                    "symbol_type": "text",
                    "symbol_name": "",
                    "class_name": "",
                    "line_start": 0,
                    "line_end": 0,
                    "chunk_index": i,
                },
            }
            for i, chunk in enumerate(sub_chunks)
        ]
