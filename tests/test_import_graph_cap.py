import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from code_intelligence import _build_import_graph_uncached
from config import IMPORT_GRAPH_MAX_FILES, get_import_graph_max_files


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestImportGraphMaxFilesConfig:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("PROJECTMIND_IMPORT_GRAPH_MAX_FILES", raising=False)
        assert get_import_graph_max_files() == IMPORT_GRAPH_MAX_FILES

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("PROJECTMIND_IMPORT_GRAPH_MAX_FILES", "123")
        assert get_import_graph_max_files() == 123

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PROJECTMIND_IMPORT_GRAPH_MAX_FILES", "not-a-number")
        assert get_import_graph_max_files() == IMPORT_GRAPH_MAX_FILES

    def test_non_positive_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PROJECTMIND_IMPORT_GRAPH_MAX_FILES", "0")
        assert get_import_graph_max_files() == IMPORT_GRAPH_MAX_FILES

    def test_default_is_higher_than_legacy_cap(self):
        assert IMPORT_GRAPH_MAX_FILES > 3000


class TestImportGraphCap:
    def test_cap_limits_scanned_files(self, tmp_path: Path):
        for i in range(6):
            _write(tmp_path / f"m{i}.py", "x = 1\n")
        graph = _build_import_graph_uncached(tmp_path, max_files=3)
        assert len(graph) == 3

    def test_none_uses_config_value(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(config, "IMPORT_GRAPH_MAX_FILES", 2)
        for i in range(5):
            _write(tmp_path / f"m{i}.py", "x = 1\n")
        graph = _build_import_graph_uncached(tmp_path, max_files=None)
        assert len(graph) == 2

    def test_truncation_logs_warning(self, tmp_path: Path, caplog):
        for i in range(4):
            _write(tmp_path / f"m{i}.py", "x = 1\n")
        with caplog.at_level("WARNING"):
            _build_import_graph_uncached(tmp_path, max_files=2)
        assert any("truncated" in r.message.lower() for r in caplog.records)

    def test_no_warning_when_under_cap(self, tmp_path: Path, caplog):
        _write(tmp_path / "only.py", "x = 1\n")
        with caplog.at_level("WARNING"):
            _build_import_graph_uncached(tmp_path, max_files=50)
        assert not any("truncated" in r.message.lower() for r in caplog.records)
