import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manifest import MAX_SYMBOLS_PER_FILE, _extract_symbols


class TestSymbolDepth:
    def test_python_symbol_below_200_lines_is_captured(self):
        filler = "\n".join(f"x{i} = {i}" for i in range(300))
        content = filler + "\n\ndef deep_function():\n    return 1\n"
        symbols = _extract_symbols(content, "python")
        assert "deep_function" in symbols

    def test_class_after_200_lines_is_captured(self):
        filler = "\n".join("# comment" for _ in range(250))
        content = filler + "\n\nclass LateClass:\n    pass\n"
        symbols = _extract_symbols(content, "python")
        assert "LateClass" in symbols


class TestSymbolExtractionBasics:
    def test_python_top_level_defs_and_classes(self):
        content = "class A:\n    pass\n\ndef b():\n    pass\n\nasync def c():\n    pass\n"
        symbols = _extract_symbols(content, "python")
        assert symbols == ["A", "b", "c"]

    def test_javascript_symbols(self):
        content = "export function foo() {}\nconst bar = 1;\nclass Baz {}\n"
        symbols = _extract_symbols(content, "javascript")
        assert "foo" in symbols
        assert "bar" in symbols
        assert "Baz" in symbols

    def test_generic_language_symbols(self):
        content = "fn rust_fn() {}\nstruct Thing {}\n"
        symbols = _extract_symbols(content, "rust")
        assert "rust_fn" in symbols
        assert "Thing" in symbols

    def test_dedup_preserves_first_occurrence_order(self):
        content = "def a():\n    pass\ndef a():\n    pass\ndef b():\n    pass\n"
        assert _extract_symbols(content, "python") == ["a", "b"]

    def test_cap_is_respected(self):
        content = "\n".join(f"def f{i}():\n    pass" for i in range(MAX_SYMBOLS_PER_FILE + 10))
        symbols = _extract_symbols(content, "python")
        assert len(symbols) == MAX_SYMBOLS_PER_FILE
