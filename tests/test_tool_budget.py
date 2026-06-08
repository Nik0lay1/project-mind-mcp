import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import code_intelligence
import config
from code_intelligence import _build_import_graph_uncached
from config import TOOL_SOFT_BUDGET_SECONDS, get_tool_budget_seconds


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestToolBudgetConfig:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("PROJECTMIND_TOOL_BUDGET_SECONDS", raising=False)
        assert get_tool_budget_seconds() == float(TOOL_SOFT_BUDGET_SECONDS)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("PROJECTMIND_TOOL_BUDGET_SECONDS", "5")
        assert get_tool_budget_seconds() == 5.0

    def test_fractional_env_override(self, monkeypatch):
        monkeypatch.setenv("PROJECTMIND_TOOL_BUDGET_SECONDS", "0.5")
        assert get_tool_budget_seconds() == 0.5

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PROJECTMIND_TOOL_BUDGET_SECONDS", "not-a-number")
        assert get_tool_budget_seconds() == float(TOOL_SOFT_BUDGET_SECONDS)

    def test_non_positive_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PROJECTMIND_TOOL_BUDGET_SECONDS", "0")
        assert get_tool_budget_seconds() == float(TOOL_SOFT_BUDGET_SECONDS)

    def test_negative_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PROJECTMIND_TOOL_BUDGET_SECONDS", "-3")
        assert get_tool_budget_seconds() == float(TOOL_SOFT_BUDGET_SECONDS)

    def test_returns_float_type(self, monkeypatch):
        monkeypatch.delenv("PROJECTMIND_TOOL_BUDGET_SECONDS", raising=False)
        assert isinstance(get_tool_budget_seconds(), float)


class TestImportGraphBudget:
    def test_zero_budget_stops_early(self, tmp_path: Path, monkeypatch):
        for i in range(8):
            _write(tmp_path / f"m{i}.py", "import os\n")
        monkeypatch.setattr(config, "TOOL_SOFT_BUDGET_SECONDS", 0)
        monkeypatch.delenv("PROJECTMIND_TOOL_BUDGET_SECONDS", raising=False)
        graph = _build_import_graph_uncached(tmp_path)
        assert len(graph) < 8

    def test_budget_emits_warning(self, tmp_path: Path, monkeypatch, caplog):
        import logging

        for i in range(4):
            _write(tmp_path / f"m{i}.py", "import os\n")
        monkeypatch.setattr(config, "TOOL_SOFT_BUDGET_SECONDS", 0)
        monkeypatch.delenv("PROJECTMIND_TOOL_BUDGET_SECONDS", raising=False)
        with caplog.at_level(logging.WARNING, logger=code_intelligence.logger.name):
            _build_import_graph_uncached(tmp_path)
        assert any("time budget" in r.message for r in caplog.records)

    def test_generous_budget_scans_all(self, tmp_path: Path, monkeypatch):
        _write(tmp_path / "a.py", "from b import x\n")
        _write(tmp_path / "b.py", "x = 1\n")
        monkeypatch.setenv("PROJECTMIND_TOOL_BUDGET_SECONDS", "120")
        graph = _build_import_graph_uncached(tmp_path)
        assert "a.py" in graph
        assert "b.py" in graph
