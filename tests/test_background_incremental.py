import json
import time
from unittest.mock import MagicMock, patch

import config
from background_indexer import BackgroundIndexer, format_progress_markdown
from mcp_server import index_changed_files


class TestBackgroundIncremental:
    def test_stale_recovery_when_thread_dead(self, tmp_path, monkeypatch):
        """Verify get_progress marks active on-disk status as interrupted when thread is dead."""
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(config, "AI_DIR", tmp_path / ".ai")
        (tmp_path / ".ai").mkdir(parents=True, exist_ok=True)

        stale_progress = {
            "status": "finalizing",
            "phase": "bm25",
            "files_done": 50,
            "files_total": 50,
            "root_dir": str(tmp_path),
        }
        progress_file = tmp_path / ".ai" / "index_progress.json"
        progress_file.write_text(json.dumps(stale_progress), encoding="utf-8")

        BackgroundIndexer.reset_progress()
        assert not BackgroundIndexer.is_running()

        prog = BackgroundIndexer.get_progress()
        assert prog["status"] == "interrupted"
        assert "interrupted" in prog.get("last_error", "").lower()

        # Check markdown formatting
        md = format_progress_markdown(prog)
        assert "Indexing Status: `interrupted`" in md
        assert "Previous indexing run was interrupted" in md

    def test_start_incremental_starts_thread(self, tmp_path, monkeypatch):
        """Verify start_incremental spawns a daemon thread and reports progress."""
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(config, "AI_DIR", tmp_path / ".ai")
        (tmp_path / ".ai").mkdir(parents=True, exist_ok=True)

        BackgroundIndexer.reset_progress()

        # Mock _do_index_incremental to prevent heavy model loads during fast unit test
        with patch.object(BackgroundIndexer, "_do_index_incremental") as mock_do_inc:
            started = BackgroundIndexer.start_incremental(changed_files=[tmp_path / "foo.py"])
            assert started is True
            # Allow the daemon thread a moment to start
            time.sleep(0.05)
            mock_do_inc.assert_called_once()

    def test_index_changed_files_no_changes_fast_return(self, tmp_path, monkeypatch):
        """Verify index_changed_files returns instantly when there are no changes."""
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(config, "AI_DIR", tmp_path / ".ai")

        with (
            patch("file_scanner.scan_files") as mock_scan,
            patch("incremental_indexing.IndexMetadata") as mock_meta_cls,
        ):
            mock_scan.return_value = MagicMock(files=[], complete=True)
            mock_meta = MagicMock()
            mock_meta.metadata = {}
            mock_meta.get_changed_files.return_value = []
            mock_meta_cls.return_value = mock_meta

            t0 = time.perf_counter()
            result = index_changed_files(background=True)
            t_elapsed = time.perf_counter() - t0

            assert "No changed files to index" in result
            # Should be nearly instantaneous (< 0.1s)
            assert t_elapsed < 0.1

    def test_index_changed_files_background_mode_starts_job(self, tmp_path, monkeypatch):
        """Verify index_changed_files with changes returns immediately and starts background job."""
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(config, "AI_DIR", tmp_path / ".ai")

        dummy_file = tmp_path / "mod.py"

        with (
            patch("file_scanner.scan_files") as mock_scan,
            patch("incremental_indexing.IndexMetadata") as mock_meta_cls,
            patch.object(BackgroundIndexer, "is_running", return_value=False),
            patch.object(
                BackgroundIndexer, "start_incremental", return_value=True
            ) as mock_start_inc,
        ):
            mock_scan.return_value = MagicMock(files=[dummy_file], complete=True)
            mock_meta = MagicMock()
            mock_meta.metadata = {}
            mock_meta.get_changed_files.return_value = [dummy_file]
            mock_meta_cls.return_value = mock_meta

            t0 = time.perf_counter()
            result = index_changed_files(background=True)
            t_elapsed = time.perf_counter() - t0

            assert "Background incremental indexing started" in result
            assert "1 changed files" in result
            assert t_elapsed < 0.1
            mock_start_inc.assert_called_once_with(changed_files=[dummy_file])

    def test_index_changed_files_synchronous_mode(self, tmp_path, monkeypatch):
        """Verify background=False preserves synchronous execution."""
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(config, "AI_DIR", tmp_path / ".ai")

        mock_ctx = MagicMock()
        mock_ctx.indexer.index_changed.return_value = "Indexed 3 changed files"

        with (
            patch("context.get_context", return_value=mock_ctx),
            patch("vector_store_manager.vector_stack_available", return_value=False),
            patch("code_intelligence.invalidate_import_graph_cache"),
        ):
            result = index_changed_files(background=False)
            assert "Indexed 3 changed files" in result
            mock_ctx.indexer.index_changed.assert_called_once()
