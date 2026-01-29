"""Tests for git_utils module."""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exceptions import GitError
from git_utils import CommitInfo, GitRepository


class TestCommitInfo:
    """Tests for CommitInfo dataclass."""

    def test_from_commit(self):
        """Test creating CommitInfo from git commit."""
        mock_commit = MagicMock()
        mock_commit.hexsha = "abc1234567890"
        mock_commit.message = "Fix bug\n\nDetails here"
        mock_commit.author.name = "Test Author"
        mock_commit.committed_date = datetime(2024, 1, 15, 10, 30).timestamp()

        info = CommitInfo.from_commit(mock_commit)

        assert info.hash == "abc1234567890"
        assert info.short_hash == "abc1234"
        assert info.message == "Fix bug\n\nDetails here"
        assert info.author == "Test Author"

    def test_first_line_truncation(self):
        """Test that first_line truncates long messages."""
        info = CommitInfo(
            hash="abc123",
            short_hash="abc",
            message="x" * 150,
            author="Test",
            date=datetime.now(),
        )

        assert len(info.first_line) <= 100

    def test_date_formats(self):
        """Test date formatting properties."""
        dt = datetime(2024, 3, 15, 14, 30)
        info = CommitInfo(
            hash="abc123",
            short_hash="abc",
            message="Test",
            author="Test",
            date=dt,
        )

        assert info.date_str == "2024-03-15 14:30"
        assert info.date_short == "2024-03-15"


class TestGitRepository:
    """Tests for GitRepository class."""

    def test_invalid_repository(self):
        """Test error when not in a git repository."""
        with patch("git_utils.git.Repo") as mock_repo:
            import git

            mock_repo.side_effect = git.InvalidGitRepositoryError()

            repo = GitRepository("/fake/path")
            with pytest.raises(GitError) as exc_info:
                repo.get_commits()

            assert "not a git repository" in str(exc_info.value).lower()

    def test_get_commits(self):
        """Test getting commits from repository."""
        with patch("git_utils.git.Repo") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo

            mock_commit = MagicMock()
            mock_commit.hexsha = "abc1234567890"
            mock_commit.message = "Test commit"
            mock_commit.author.name = "Author"
            mock_commit.committed_date = datetime.now().timestamp()

            mock_repo.iter_commits.return_value = [mock_commit]

            repo = GitRepository()
            commits = repo.get_commits(max_count=10)

            assert len(commits) == 1
            assert commits[0].short_hash == "abc1234"

    def test_get_commits_with_since_days(self):
        """Test getting commits filtered by date."""
        with patch("git_utils.git.Repo") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo_class.return_value = mock_repo

            recent_commit = MagicMock()
            recent_commit.hexsha = "recent123"
            recent_commit.message = "Recent"
            recent_commit.author.name = "Author"
            recent_commit.committed_date = datetime.now().timestamp()

            old_commit = MagicMock()
            old_commit.hexsha = "old456789"
            old_commit.message = "Old"
            old_commit.author.name = "Author"
            old_commit.committed_date = (datetime.now() - timedelta(days=30)).timestamp()

            mock_repo.iter_commits.return_value = [recent_commit, old_commit]

            repo = GitRepository()
            commits = repo.get_commits(max_count=10, since_days=7)

            assert len(commits) == 1
            assert commits[0].short_hash == "recent1"

    def test_get_author_stats(self):
        """Test author statistics calculation."""
        commits = [
            CommitInfo("a", "a", "msg", "Alice", datetime.now()),
            CommitInfo("b", "b", "msg", "Alice", datetime.now()),
            CommitInfo("c", "c", "msg", "Bob", datetime.now()),
        ]

        repo = GitRepository.__new__(GitRepository)
        stats = repo.get_author_stats(commits)

        assert stats["Alice"] == 2
        assert stats["Bob"] == 1
        assert list(stats.keys())[0] == "Alice"

    def test_format_commits_summary(self):
        """Test commit summary formatting."""
        commits = [
            CommitInfo("a", "abc", "First commit", "Alice", datetime(2024, 1, 1, 10, 0)),
            CommitInfo("b", "def", "Second commit", "Bob", datetime(2024, 1, 2, 11, 0)),
        ]

        repo = GitRepository.__new__(GitRepository)
        lines = repo.format_commits_summary(commits, max_display=2)

        assert len(lines) == 2
        assert "abc" in lines[0]
        assert "First commit" in lines[0]

    def test_format_commits_summary_truncation(self):
        """Test commit summary truncation."""
        commits = [
            CommitInfo("a", f"h{i}", f"Commit {i}", "Author", datetime.now())
            for i in range(15)
        ]

        repo = GitRepository.__new__(GitRepository)
        lines = repo.format_commits_summary(commits, max_display=5)

        assert len(lines) == 6
        assert "... and 10 more commits" in lines[-1]

    def test_format_author_stats(self):
        """Test author stats formatting."""
        stats = {"Alice": 5, "Bob": 3}

        repo = GitRepository.__new__(GitRepository)
        lines = repo.format_author_stats(stats)

        assert len(lines) == 2
        assert "Alice: 5 commits" in lines[0]
        assert "Bob: 3 commits" in lines[1]
