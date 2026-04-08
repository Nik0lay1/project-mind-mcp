"""Git utilities for ProjectMind MCP Server."""

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
        message = commit.message
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        author_name = commit.author.name if commit.author and commit.author.name else "Unknown"
        return cls(
            hash=commit.hexsha,
            short_hash=commit.hexsha[:7],
            message=message.strip(),
            author=author_name,
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
        if path is None:
            import config

            self._path = str(config.PROJECT_ROOT)
        else:
            self._path = path

    def _get_repo(self) -> git.Repo:
        if self._repo is None:
            try:
                self._repo = git.Repo(self._path, search_parent_directories=True)
            except git.InvalidGitRepositoryError as e:
                raise GitError("Current directory is not a git repository.") from e
            except Exception as e:
                raise GitError(f"Error accessing git repository: {e}") from e
        return self._repo

    def get_commits(self, max_count: int = 30, since_days: int | None = None) -> list[CommitInfo]:
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

    def get_commits_by_author(self, commits: list[CommitInfo]) -> dict[str, list[CommitInfo]]:
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

    def format_commits_summary(self, commits: list[CommitInfo], max_display: int = 10) -> list[str]:
        lines = []
        for commit in commits[:max_display]:
            lines.append(f"- **{commit.date_str}** [{commit.short_hash}]: {commit.first_line}")
        if len(commits) > max_display:
            lines.append(f"\n... and {len(commits) - max_display} more commits")
        return lines

    def format_author_stats(self, stats: dict[str, int]) -> list[str]:
        return [f"- {author}: {count} commits" for author, count in stats.items()]

    def get_file_commits(self, file_path: str, max_count: int = 5) -> list[CommitInfo]:
        repo = self._get_repo()
        commits = []
        try:
            for commit in repo.iter_commits(paths=file_path, max_count=max_count):
                commits.append(CommitInfo.from_commit(commit))
        except Exception:
            pass
        return commits

    def get_recently_changed_files(
        self, days: int = 7, max_files: int = 50
    ) -> dict[str, CommitInfo]:
        repo = self._get_repo()
        result: dict[str, CommitInfo] = {}
        cutoff = datetime.now() - timedelta(days=days)
        try:
            for commit in repo.iter_commits(max_count=200):
                info = CommitInfo.from_commit(commit)
                if info.date < cutoff:
                    break
                for path in commit.stats.files:
                    key = str(path)
                    if key not in result:
                        result[key] = info
                        if len(result) >= max_files:
                            return result
        except Exception:
            pass
        return result

    def get_active_branch(self) -> str:
        try:
            repo = self._get_repo()
            return str(repo.active_branch)
        except Exception:
            return "unknown"

    def get_total_commit_count(self, max_scan: int = 500) -> int:
        try:
            repo = self._get_repo()
            return sum(1 for _ in repo.iter_commits(max_count=max_scan))
        except Exception:
            return 0
