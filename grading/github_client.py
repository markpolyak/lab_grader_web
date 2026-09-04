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


def is_rate_limited(resp: requests.Response) -> bool:
    """
    Detect a GitHub rate-limit response hiding behind a 403.

    GitHub answers both "you don't have permission" and "you hit a
    (secondary) rate limit" with HTTP 403 - distinguishing them matters
    because only the second one should be reported as retryable. Shared
    between grading/repo_provisioning.py and grading/propagate.py (issue #52
    asked for this to live in one place instead of being duplicated).
    """
    if resp.headers.get("Retry-After"):
        return True
    if resp.headers.get("X-RateLimit-Remaining") == "0":
        return True
    try:
        message = resp.json().get("message", "")
    except ValueError:
        message = ""
    return "rate limit" in message.lower()


class GitHubClient:
    """Client for GitHub API operations."""

    BASE_URL = "https://api.github.com"
    # Used by the /join repo-provisioning calls below, to match the timeout already
    # used for the OAuth requests in main.py (avoids a hung worker if api.github.com stalls).
    DEFAULT_TIMEOUT = 10

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

    def repo_exists(self, org: str, repo: str) -> bool:
        """
        Check if a repository exists.

        Args:
            org: Organization or user name
            repo: Repository name

        Returns:
            True if the repository exists, False otherwise
        """
        url = f"{self.BASE_URL}/repos/{org}/{repo}"
        resp = requests.get(url, headers=self.headers, timeout=self.DEFAULT_TIMEOUT)
        return resp.status_code == 200

    def create_repo_from_template(
        self,
        template_owner: str,
        template_repo: str,
        owner: str,
        name: str,
        private: bool = True,
    ) -> requests.Response:
        """
        Create a repository from a template repository.

        See https://docs.github.com/en/rest/repos/repos#create-a-repository-using-a-template

        Args:
            template_owner: Owner of the template repository
            template_repo: Name of the template repository
            owner: Organization (or user) that will own the new repository
            name: Name of the new repository
            private: Whether the new repository should be private

        Returns:
            The raw requests.Response (caller inspects status_code, since
            different non-2xx codes need different handling upstream)
        """
        url = f"{self.BASE_URL}/repos/{template_owner}/{template_repo}/generate"
        headers = {**self.headers, "Accept": "application/vnd.github+json"}
        payload = {"owner": owner, "name": name, "private": private}
        return requests.post(url, headers=headers, json=payload, timeout=self.DEFAULT_TIMEOUT)

    def fork_repo(self, owner: str, repo: str, org: str, name: str) -> requests.Response:
        """
        Fork a repository into an organization under a chosen name.

        See https://docs.github.com/en/rest/repos/forks#create-a-fork
        Passing `name` lets several forks of the same template repository
        coexist in one organization (the "one fork per account" limit that
        applies without it does not apply once `name` is given).

        Args:
            owner: Owner of the repository being forked (the template)
            repo: Name of the repository being forked (the template)
            org: Organization that will own the new fork
            name: Name of the new fork

        Returns:
            The raw requests.Response (expected success is 202 Accepted -
            fork creation is asynchronous on GitHub's side)
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/forks"
        payload = {"organization": org, "name": name}
        return requests.post(url, headers=self.headers, json=payload, timeout=self.DEFAULT_TIMEOUT)

    def get_repo(self, owner: str, repo: str) -> dict | None:
        """
        Get repository details.

        Args:
            owner: Organization or user name
            repo: Repository name

        Returns:
            Repository dict from the GitHub API, or None on error
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}"
        resp = requests.get(url, headers=self.headers, timeout=self.DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.json()

    def update_repo(self, owner: str, repo: str, payload: dict) -> requests.Response:
        """
        Update repository settings.

        Args:
            owner: Organization or user name
            repo: Repository name
            payload: Fields to update, e.g. {"is_template": False}

        Returns:
            The raw requests.Response
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}"
        return requests.patch(url, headers=self.headers, json=payload, timeout=self.DEFAULT_TIMEOUT)

    def enable_actions(self, owner: str, repo: str) -> requests.Response:
        """
        Enable GitHub Actions for a repository.

        Forks have Actions disabled by default and the disabled state isn't
        visible through the API (GET .../actions/permissions reports
        enabled=True even while the "workflows aren't being run" banner is
        still up), so callers must call this unconditionally after forking
        rather than checking first.

        Args:
            owner: Organization or user name
            repo: Repository name

        Returns:
            The raw requests.Response
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/actions/permissions"
        return requests.put(url, headers=self.headers, json={"enabled": True}, timeout=self.DEFAULT_TIMEOUT)

    def _get_all_pages(self, url: str, params: dict | None = None) -> list[dict[str, Any]] | None:
        """
        Follow GitHub's `per_page`/page pagination and collect every item.

        GitHub defaults to 30 items per page - without paging through, a
        course with 200 students would silently lose most of the forks/repos
        (see issue #52). Stops once a page comes back with fewer than
        `per_page` items (the standard "last page" signal).

        Returns:
            All items across every page, or None if any page request failed
        """
        per_page = 100
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = requests.get(
                url,
                headers=self.headers,
                params={**(params or {}), "per_page": per_page, "page": page},
                timeout=self.DEFAULT_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            page_items = resp.json()
            items.extend(page_items)
            if len(page_items) < per_page:
                return items
            page += 1

    def list_forks(self, owner: str, repo: str) -> list[dict[str, Any]] | None:
        """
        List all forks of a repository (all pages).

        See https://docs.github.com/en/rest/repos/forks#list-forks

        Args:
            owner: Owner of the (template) repository
            repo: Name of the (template) repository

        Returns:
            List of fork repo dicts, or None on error
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/forks"
        return self._get_all_pages(url)

    def list_org_repos(self, org: str) -> list[dict[str, Any]] | None:
        """
        List all repositories owned by an organization (all pages).

        See https://docs.github.com/en/rest/repos/repos#list-organization-repositories

        Args:
            org: Organization name

        Returns:
            List of repo dicts, or None on error
        """
        url = f"{self.BASE_URL}/orgs/{org}/repos"
        return self._get_all_pages(url)

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> requests.Response:
        """
        Open a pull request in a repository.

        See https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request

        Args:
            owner: Organization (or user) that owns the repository the PR is opened in
            repo: Name of the repository the PR is opened in
            head: Branch to merge from, as "owner:branch" for a cross-repo PR
                (e.g. the template's owner:default_branch when opening into a fork)
            base: Branch to merge into (the target repo's default branch)
            title: PR title
            body: PR body

        Returns:
            The raw requests.Response (caller inspects status_code - 201 is
            success, 422 covers both "up to date" and "PR already exists",
            see grading/propagate.py)
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/pulls"
        payload = {"head": head, "base": base, "title": title, "body": body}
        return requests.post(url, headers=self.headers, json=payload, timeout=self.DEFAULT_TIMEOUT)

    def list_pull_requests(
        self,
        owner: str,
        repo: str,
        head: str | None = None,
        state: str = "open",
    ) -> list[dict[str, Any]] | None:
        """
        List pull requests in a repository (all pages).

        See https://docs.github.com/en/rest/pulls/pulls#list-pull-requests

        Args:
            owner: Organization (or user) that owns the repository
            repo: Repository name
            head: Optional filter, as "owner:branch" (see create_pull_request)
            state: PR state filter ("open", "closed", or "all")

        Returns:
            List of pull request dicts, or None on error
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/pulls"
        params: dict[str, Any] = {"state": state}
        if head is not None:
            params["head"] = head
        return self._get_all_pages(url, params=params)

    def is_direct_collaborator(self, org: str, repo: str, username: str) -> bool:
        """
        Check whether a user already has direct collaborator access to a repository.

        Note: GitHub's docs don't document an `affiliation` param for this
        single-user "check collaborator" endpoint (only for the list-collaborators
        one) - it's used here anyway per docs/REPO_GENERATION_PLAN.md §4, which
        specifies this exact call. It's harmless for the current one-student-one-repo
        model; a future team-lab variant relying on "direct only" here should
        double check GitHub's actual behavior first.

        Args:
            org: Organization or user name
            repo: Repository name
            username: GitHub username to check

        Returns:
            True if the user is a collaborator (204 response)
        """
        url = f"{self.BASE_URL}/repos/{org}/{repo}/collaborators/{username}"
        resp = requests.get(
            url, headers=self.headers, params={"affiliation": "direct"}, timeout=self.DEFAULT_TIMEOUT
        )
        return resp.status_code == 204

    def list_invitations(self, org: str, repo: str) -> list[dict[str, Any]] | None:
        """
        List pending repository invitations.

        Args:
            org: Organization or user name
            repo: Repository name

        Returns:
            List of invitation dicts, or None on error
        """
        url = f"{self.BASE_URL}/repos/{org}/{repo}/invitations"
        resp = requests.get(url, headers=self.headers, timeout=self.DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.json()

    def delete_invitation(self, org: str, repo: str, invitation_id: int) -> bool:
        """
        Cancel a pending repository invitation.

        Args:
            org: Organization or user name
            repo: Repository name
            invitation_id: ID of the invitation to delete

        Returns:
            True if the invitation was deleted
        """
        url = f"{self.BASE_URL}/repos/{org}/{repo}/invitations/{invitation_id}"
        resp = requests.delete(url, headers=self.headers, timeout=self.DEFAULT_TIMEOUT)
        return resp.status_code == 204

    def add_collaborator(self, org: str, repo: str, username: str) -> requests.Response:
        """
        Invite (or directly add) a user as a repository collaborator.

        A fresh PUT after deleting a stale pending invitation is what actually
        re-sends the GitHub notification email - re-PUTting without deleting
        the old invitation first is a no-op for the notification.

        Args:
            org: Organization or user name
            repo: Repository name
            username: GitHub username to invite

        Returns:
            The raw requests.Response (201 = invitation created,
            204 = user already had access and was added directly)
        """
        url = f"{self.BASE_URL}/repos/{org}/{repo}/collaborators/{username}"
        return requests.put(url, headers=self.headers, timeout=self.DEFAULT_TIMEOUT)

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

        # GitHub API returns logs in UTF-8, but without proper charset in headers.
        # Force UTF-8 decoding to correctly handle Cyrillic and other non-ASCII characters.
        resp.encoding = 'utf-8'
        return resp.text


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
