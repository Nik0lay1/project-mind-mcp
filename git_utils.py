"""Git utilities for ProjectMind MCP Server."""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import git

from exceptions import GitError
from logger import get_logger

logger = get_logger()


@dataclass
class CommitInfo:
    """Represents a git commit."""

    hash: str
    short_hash: str
    message: str
    author: str
    date: datetime

    @classmethod
    def from_commit(cls, commit: git.Commit) -> "CommitInfo":
        return cls(
            hash=commit.hexsha,
            short_hash=commit.hexsha[:7],
            message=commit.message.strip(),
            author=commit.author.name,
            date=datetime.fromtimestamp(commit.committed_date),
        )

    @property
    def first_line(self) -> str:
        return self.message.split("\n")[0][:100]

    @property
    def date_str(self) -> str:
        return self.date.strftime("%Y-%m-%d %H:%M")

    @property
    def date_short(self) -> str:
        return self.date.strftime("%Y-%m-%d")


class GitRepository:
    """Wrapper for git repository operations."""

    def __init__(self, path: str | None = None):
        self._repo: git.Repo | None = None
        self._path = path or os.getcwd()

    def _get_repo(self) -> git.Repo:
        if self._repo is None:
            try:
                self._repo = git.Repo(self._path, search_parent_directories=True)
            except git.InvalidGitRepositoryError as e:
                raise GitError("Current directory is not a git repository.") from e
            except Exception as e:
                raise GitError(f"Error accessing git repository: {e}") from e
        return self._repo

    def get_commits(
        self, max_count: int = 30, since_days: int | None = None
    ) -> list[CommitInfo]:
        repo = self._get_repo()
        commits = []

        cutoff_date = None
        if since_days is not None:
            cutoff_date = datetime.now() - timedelta(days=since_days)

        for commit in repo.iter_commits(max_count=max_count):
            commit_info = CommitInfo.from_commit(commit)
            if cutoff_date and commit_info.date < cutoff_date:
                break
            commits.append(commit_info)

        return commits

    def get_commits_by_author(
        self, commits: list[CommitInfo]
    ) -> dict[str, list[CommitInfo]]:
        authors: dict[str, list[CommitInfo]] = {}
        for commit in commits:
            if commit.author not in authors:
                authors[commit.author] = []
            authors[commit.author].append(commit)
        return authors

    def get_author_stats(self, commits: list[CommitInfo]) -> dict[str, int]:
        stats: dict[str, int] = {}
        for commit in commits:
            stats[commit.author] = stats.get(commit.author, 0) + 1
        return dict(sorted(stats.items(), key=lambda x: x[1], reverse=True))

    def format_commits_summary(
        self, commits: list[CommitInfo], max_display: int = 10
    ) -> list[str]:
        lines = []
        for commit in commits[:max_display]:
            lines.append(
                f"- **{commit.date_str}** [{commit.short_hash}]: {commit.first_line}"
            )
        if len(commits) > max_display:
            lines.append(f"\n... and {len(commits) - max_display} more commits")
        return lines

    def format_author_stats(self, stats: dict[str, int]) -> list[str]:
        return [f"- {author}: {count} commits" for author, count in stats.items()]
