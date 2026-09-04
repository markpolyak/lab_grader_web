"""
Unit tests for grading/github_client.py

Tests GitHub API client with mocked HTTP responses.
"""
import json
import pytest
import responses
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.github_client import (
    GitHubClient,
    CommitInfo,
    check_forbidden_modifications,
    get_default_forbidden_patterns,
)


class TestGitHubClientUserExists:
    """Tests for user_exists method."""

    @responses.activate
    def test_user_exists(self):
        """User exists returns True."""
        responses.add(
            responses.GET,
            "https://api.github.com/users/testuser",
            json={"login": "testuser"},
            status=200
        )
        client = GitHubClient("test_token")
        assert client.user_exists("testuser") is True

    @responses.activate
    def test_user_not_exists(self):
        """Non-existent user returns False."""
        responses.add(
            responses.GET,
            "https://api.github.com/users/nonexistent",
            json={"message": "Not Found"},
            status=404
        )
        client = GitHubClient("test_token")
        assert client.user_exists("nonexistent") is False


class TestGitHubClientFileExists:
    """Tests for file_exists method."""

    @responses.activate
    def test_file_exists(self):
        """Existing file returns True."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/contents/test.py",
            json={"name": "test.py"},
            status=200
        )
        client = GitHubClient("test_token")
        assert client.file_exists("org", "repo", "test.py") is True

    @responses.activate
    def test_file_not_exists(self):
        """Non-existent file returns False."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/contents/missing.py",
            json={"message": "Not Found"},
            status=404
        )
        client = GitHubClient("test_token")
        assert client.file_exists("org", "repo", "missing.py") is False


class TestGitHubClientCheckRequiredFiles:
    """Tests for check_required_files method."""

    @responses.activate
    def test_all_files_exist(self):
        """All required files exist returns empty list."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/contents/file1.py",
            json={"name": "file1.py"},
            status=200
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/contents/file2.py",
            json={"name": "file2.py"},
            status=200
        )
        client = GitHubClient("test_token")
        missing = client.check_required_files("org", "repo", ["file1.py", "file2.py"])
        assert missing == []

    @responses.activate
    def test_some_files_missing(self):
        """Missing files returned in list."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/contents/exists.py",
            json={"name": "exists.py"},
            status=200
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/contents/missing.py",
            json={"message": "Not Found"},
            status=404
        )
        client = GitHubClient("test_token")
        missing = client.check_required_files("org", "repo", ["exists.py", "missing.py"])
        assert missing == ["missing.py"]

    @responses.activate
    def test_empty_required_files(self):
        """Empty required files list returns empty."""
        client = GitHubClient("test_token")
        missing = client.check_required_files("org", "repo", [])
        assert missing == []


class TestGitHubClientHasWorkflows:
    """Tests for has_workflows_directory method."""

    @responses.activate
    def test_has_workflows(self):
        """Workflows directory exists."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/contents/.github/workflows",
            json=[{"name": "test.yml"}],
            status=200
        )
        client = GitHubClient("test_token")
        assert client.has_workflows_directory("org", "repo") is True

    @responses.activate
    def test_no_workflows(self):
        """Workflows directory missing."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/contents/.github/workflows",
            json={"message": "Not Found"},
            status=404
        )
        client = GitHubClient("test_token")
        assert client.has_workflows_directory("org", "repo") is False


class TestGitHubClientGetLatestCommit:
    """Tests for get_latest_commit method."""

    @responses.activate
    def test_get_latest_commit(self):
        """Get latest commit with files."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits",
            json=[{"sha": "abc123"}],
            status=200
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits/abc123",
            json={
                "sha": "abc123",
                "files": [
                    {"filename": "main.py", "status": "modified"}
                ]
            },
            status=200
        )
        client = GitHubClient("test_token")
        commit = client.get_latest_commit("org", "repo")

        assert commit is not None
        assert commit.sha == "abc123"
        assert len(commit.files) == 1
        assert commit.files[0]["filename"] == "main.py"

    @responses.activate
    def test_no_commits(self):
        """No commits returns None."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits",
            json=[],
            status=200
        )
        client = GitHubClient("test_token")
        commit = client.get_latest_commit("org", "repo")
        assert commit is None

    @responses.activate
    def test_commits_api_error(self):
        """API error returns None."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits",
            json={"message": "Not Found"},
            status=404
        )
        client = GitHubClient("test_token")
        commit = client.get_latest_commit("org", "repo")
        assert commit is None


class TestGitHubClientGetCheckRuns:
    """Tests for get_check_runs method."""

    @responses.activate
    def test_get_check_runs(self):
        """Get check runs for commit."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits/abc123/check-runs",
            json={
                "check_runs": [
                    {"name": "test", "conclusion": "success"},
                    {"name": "lint", "conclusion": "failure"}
                ]
            },
            status=200
        )
        client = GitHubClient("test_token")
        runs = client.get_check_runs("org", "repo", "abc123")

        assert runs is not None
        assert len(runs) == 2
        assert runs[0]["name"] == "test"

    @responses.activate
    def test_check_runs_api_error(self):
        """API error returns None."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits/abc123/check-runs",
            json={"message": "Not Found"},
            status=404
        )
        client = GitHubClient("test_token")
        runs = client.get_check_runs("org", "repo", "abc123")
        assert runs is None

    @responses.activate
    def test_empty_check_runs(self):
        """Empty check runs returned as empty list."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits/abc123/check-runs",
            json={"check_runs": []},
            status=200
        )
        client = GitHubClient("test_token")
        runs = client.get_check_runs("org", "repo", "abc123")
        assert runs == []


class TestGitHubClientForkRepo:
    """Tests for fork_repo method."""

    @responses.activate
    def test_fork_repo_posts_organization_and_name(self):
        call = responses.add(
            responses.POST,
            "https://api.github.com/repos/owner/template/forks",
            json={},
            status=202,
        )
        client = GitHubClient("test_token")
        resp = client.fork_repo("owner", "template", "org", "os-task1-student1")

        assert resp.status_code == 202
        assert call.call_count == 1
        assert json.loads(call.calls[0].request.body) == {
            "organization": "org",
            "name": "os-task1-student1",
        }


class TestGitHubClientGetRepo:
    """Tests for get_repo method."""

    @responses.activate
    def test_get_repo_returns_json(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo",
            json={"full_name": "org/repo", "private": True},
            status=200,
        )
        client = GitHubClient("test_token")
        repo = client.get_repo("org", "repo")
        assert repo == {"full_name": "org/repo", "private": True}

    @responses.activate
    def test_get_repo_not_found_returns_none(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo",
            json={"message": "Not Found"},
            status=404,
        )
        client = GitHubClient("test_token")
        assert client.get_repo("org", "repo") is None


class TestGitHubClientUpdateRepo:
    """Tests for update_repo method."""

    @responses.activate
    def test_update_repo_patches_payload(self):
        call = responses.add(
            responses.PATCH,
            "https://api.github.com/repos/org/repo",
            json={},
            status=200,
        )
        client = GitHubClient("test_token")
        resp = client.update_repo("org", "repo", {"is_template": False})

        assert resp.status_code == 200
        assert json.loads(call.calls[0].request.body) == {"is_template": False}


class TestGitHubClientEnableActions:
    """Tests for enable_actions method."""

    @responses.activate
    def test_enable_actions_puts_enabled_true(self):
        call = responses.add(
            responses.PUT,
            "https://api.github.com/repos/org/repo/actions/permissions",
            status=204,
        )
        client = GitHubClient("test_token")
        resp = client.enable_actions("org", "repo")

        assert resp.status_code == 204
        assert json.loads(call.calls[0].request.body) == {"enabled": True}


class TestCheckForbiddenModifications:
    """Tests for check_forbidden_modifications function."""

    def test_no_forbidden_files(self):
        """No violations when files don't match patterns."""
        files = [
            {"filename": "main.py", "status": "modified"},
            {"filename": "utils.py", "status": "added"}
        ]
        violations = check_forbidden_modifications(files, ["test_main.py"])
        assert violations == []

    def test_exact_match(self):
        """Detect exact filename match."""
        files = [
            {"filename": "test_main.py", "status": "modified"}
        ]
        violations = check_forbidden_modifications(files, ["test_main.py"])
        assert violations == ["test_main.py"]

    def test_prefix_match(self):
        """Detect prefix match for directories."""
        files = [
            {"filename": "tests/test_example.py", "status": "modified"}
        ]
        violations = check_forbidden_modifications(files, ["tests/"])
        assert violations == ["tests/test_example.py"]

    def test_added_files_not_forbidden(self):
        """Added files are not considered violations."""
        files = [
            {"filename": "test_main.py", "status": "added"}
        ]
        violations = check_forbidden_modifications(files, ["test_main.py"])
        assert violations == []

    def test_removed_files_forbidden(self):
        """Removed files are violations."""
        files = [
            {"filename": "test_main.py", "status": "removed"}
        ]
        violations = check_forbidden_modifications(files, ["test_main.py"])
        assert violations == ["test_main.py"]

    def test_multiple_violations(self):
        """Multiple violations detected."""
        files = [
            {"filename": "test_main.py", "status": "modified"},
            {"filename": "tests/helper.py", "status": "removed"}
        ]
        violations = check_forbidden_modifications(files, ["test_main.py", "tests/"])
        assert len(violations) == 2
        assert "test_main.py" in violations
        assert "tests/helper.py" in violations


class TestGetDefaultForbiddenPatterns:
    """Tests for get_default_forbidden_patterns function."""

    def test_with_test_main(self):
        """test_main.py in required files triggers patterns."""
        patterns = get_default_forbidden_patterns(["test_main.py", "main.py"])
        assert "test_main.py" in patterns
        assert "tests/" in patterns

    def test_without_test_main(self):
        """No test_main.py means no default patterns."""
        patterns = get_default_forbidden_patterns(["main.py", "utils.py"])
        assert patterns == []

    def test_empty_required_files(self):
        """Empty required files means no patterns."""
        patterns = get_default_forbidden_patterns([])
        assert patterns == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
