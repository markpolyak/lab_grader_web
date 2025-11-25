"""
GitHub API client for lab grading.

This module provides a client for interacting with GitHub API
to check repositories, commits, and CI status.
"""
import requests
from dataclasses import dataclass
from typing import Any


@dataclass
class CommitInfo:
    """Information about a commit."""
    sha: str
    files: list[dict[str, Any]]  # List of {filename, status, ...}
    all_modified_files: list[str] | None = None  # All files modified across all commits


@dataclass
class RepoCheckResult:
    """Result of repository checks."""
    exists: bool
    missing_files: list[str]
    has_workflows: bool
    error: str | None = None


class GitHubClientError(Exception):
    """Base exception for GitHub client errors."""
    pass


class GitHubClient:
    """Client for GitHub API operations."""

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str):
        """
        Initialize GitHub client.

        Args:
            token: GitHub personal access token or app token
        """
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

    def user_exists(self, username: str) -> bool:
        """
        Check if a GitHub user exists.

        Args:
            username: GitHub username to check

        Returns:
            True if user exists, False otherwise
        """
        url = f"{self.BASE_URL}/users/{username}"
        resp = requests.get(url, headers=self.headers)
        return resp.status_code == 200

    def file_exists(self, org: str, repo: str, path: str) -> bool:
        """
        Check if a file exists in a repository.

        Args:
            org: Organization or user name
            repo: Repository name
            path: File path within repository

        Returns:
            True if file exists, False otherwise
        """
        url = f"{self.BASE_URL}/repos/{org}/{repo}/contents/{path}"
        resp = requests.get(url, headers=self.headers)
        return resp.status_code == 200

    def check_required_files(
        self,
        org: str,
        repo: str,
        required_files: list[str]
    ) -> list[str]:
        """
        Check which required files are missing from repository.

        Args:
            org: Organization or user name
            repo: Repository name
            required_files: List of file paths to check

        Returns:
            List of missing file paths (empty if all exist)
        """
        missing = []
        for file_path in required_files:
            if not self.file_exists(org, repo, file_path):
                missing.append(file_path)
        return missing

    def has_workflows_directory(self, org: str, repo: str) -> bool:
        """
        Check if repository has .github/workflows directory.

        Args:
            org: Organization or user name
            repo: Repository name

        Returns:
            True if workflows directory exists
        """
        return self.file_exists(org, repo, ".github/workflows")

    def get_latest_commit(self, org: str, repo: str) -> CommitInfo | None:
        """
        Get the latest commit from repository.

        Args:
            org: Organization or user name
            repo: Repository name

        Returns:
            CommitInfo with SHA and modified files, or None if no commits
        """
        # Get commits list
        commits_url = f"{self.BASE_URL}/repos/{org}/{repo}/commits"
        commits_resp = requests.get(commits_url, headers=self.headers)

        if commits_resp.status_code != 200:
            return None

        commits_data = commits_resp.json()
        if not commits_data:
            return None

        latest_sha = commits_data[0]["sha"]

        # Get commit details with files
        commit_url = f"{self.BASE_URL}/repos/{org}/{repo}/commits/{latest_sha}"
        commit_resp = requests.get(commit_url, headers=self.headers)

        if commit_resp.status_code != 200:
            return CommitInfo(sha=latest_sha, files=[])

        commit_data = commit_resp.json()
        return CommitInfo(
            sha=latest_sha,
            files=commit_data.get("files", [])
        )

    def get_check_runs(
        self,
        org: str,
        repo: str,
        commit_sha: str
    ) -> list[dict[str, Any]] | None:
        """
        Get CI check runs for a commit.

        Args:
            org: Organization or user name
            repo: Repository name
            commit_sha: Commit SHA to get checks for

        Returns:
            List of check run dicts from GitHub API, or None on error
        """
        url = f"{self.BASE_URL}/repos/{org}/{repo}/commits/{commit_sha}/check-runs"
        resp = requests.get(url, headers=self.headers)

        if resp.status_code != 200:
            return None

        return resp.json().get("check_runs", [])

    def get_job_logs(self, org: str, repo: str, job_id: int) -> str | None:
        """
        Get logs for a specific workflow job.

        Args:
            org: Organization or user name
            repo: Repository name
            job_id: Job ID from check run

        Returns:
            Log text or None if not available
        """
        url = f"{self.BASE_URL}/repos/{org}/{repo}/actions/jobs/{job_id}/logs"
        resp = requests.get(url, headers=self.headers)

        if resp.status_code != 200:
            return None

        return resp.text

    def get_all_modified_files(self, org: str, repo: str) -> list[str]:
        """
        Get all files that were modified across all commits in the repository.

        This iterates through all commits and collects unique file paths
        that were added, modified, or removed.

        Args:
            org: Organization or user name
            repo: Repository name

        Returns:
            List of unique file paths that were modified in any commit
        """
        modified_files: set[str] = set()

        # Get all commits (paginated)
        page = 1
        per_page = 100
        while True:
            url = f"{self.BASE_URL}/repos/{org}/{repo}/commits"
            params = {"page": page, "per_page": per_page}
            resp = requests.get(url, headers=self.headers, params=params)

            if resp.status_code != 200:
                break

            commits = resp.json()
            if not commits:
                break

            # Get details for each commit to see modified files
            for commit_summary in commits:
                commit_sha = commit_summary["sha"]
                commit_url = f"{self.BASE_URL}/repos/{org}/{repo}/commits/{commit_sha}"
                commit_resp = requests.get(commit_url, headers=self.headers)

                if commit_resp.status_code == 200:
                    commit_data = commit_resp.json()
                    for file_info in commit_data.get("files", []):
                        filename = file_info.get("filename", "")
                        if filename:
                            modified_files.add(filename)

            # Check if there are more pages
            if len(commits) < per_page:
                break
            page += 1

        return list(modified_files)


def check_forbidden_modifications(
    commit_files: list[dict[str, Any]],
    forbidden_patterns: list[str]
) -> list[str]:
    """
    Check if any forbidden files were modified in a commit.

    Args:
        commit_files: List of file dicts from commit (with 'filename' and 'status')
        forbidden_patterns: List of forbidden file paths or prefixes

    Returns:
        List of forbidden files that were modified

    Examples:
        >>> files = [{"filename": "test_main.py", "status": "modified"}]
        >>> check_forbidden_modifications(files, ["test_main.py"])
        ['test_main.py']
    """
    violations = []

    for file_info in commit_files:
        filename = file_info.get("filename", "")
        status = file_info.get("status", "")

        # Only check removed or modified files
        if status not in ("removed", "modified"):
            continue

        for pattern in forbidden_patterns:
            # Exact match or prefix match (for directories like "tests/")
            if filename == pattern or filename.startswith(pattern):
                violations.append(filename)
                break

    return violations


def check_forbidden_files_in_list(
    all_files: list[str],
    forbidden_patterns: list[str]
) -> list[str]:
    """
    Check if any forbidden files are in the list of modified files.

    Unlike check_forbidden_modifications, this works with a simple list of filenames
    without status information. Use this when checking all files modified across
    all commits in a repository.

    Args:
        all_files: List of file paths that were modified
        forbidden_patterns: List of forbidden file paths or prefixes

    Returns:
        List of forbidden files that were found

    Examples:
        >>> check_forbidden_files_in_list(["test_main.py", "main.cpp"], ["test_main.py"])
        ['test_main.py']
        >>> check_forbidden_files_in_list(["tests/test1.py"], ["tests/"])
        ['tests/test1.py']
    """
    violations = []

    for filename in all_files:
        for pattern in forbidden_patterns:
            # Exact match or prefix match (for directories like "tests/")
            if filename == pattern or filename.startswith(pattern):
                violations.append(filename)
                break

    return violations


def get_default_forbidden_patterns(required_files: list[str]) -> list[str]:
    """
    Get default forbidden file patterns based on required files.

    If test_main.py is required, it and tests/ folder become forbidden.

    Args:
        required_files: List of required files from lab config

    Returns:
        List of forbidden patterns
    """
    patterns = []

    if "test_main.py" in required_files:
        patterns.append("test_main.py")
        patterns.append("tests/")

    return patterns
