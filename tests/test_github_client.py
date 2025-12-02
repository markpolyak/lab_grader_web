"""
Unit tests for grading/github_client.py

Tests GitHub API client with mocked HTTP responses.
"""
import pytest
import responses
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.github_client import (
    GitHubClient,
    CommitInfo,
    check_forbidden_modifications,
    check_forbidden_files_in_list,
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


class TestCheckForbiddenFilesInList:
    """Tests for check_forbidden_files_in_list function."""

    def test_no_violations(self):
        """No violations when files don't match patterns."""
        files = ["main.py", "utils.py", "lib/helper.py"]
        violations = check_forbidden_files_in_list(files, ["test_main.py", "tests/"])
        assert violations == []

    def test_exact_match(self):
        """Detect exact filename match."""
        files = ["main.py", "test_main.py", "utils.py"]
        violations = check_forbidden_files_in_list(files, ["test_main.py"])
        assert violations == ["test_main.py"]

    def test_prefix_match(self):
        """Detect prefix match for directories."""
        files = ["main.py", "tests/test_example.py", "tests/helper.py"]
        violations = check_forbidden_files_in_list(files, ["tests/"])
        assert len(violations) == 2
        assert "tests/test_example.py" in violations
        assert "tests/helper.py" in violations

    def test_multiple_patterns(self):
        """Multiple patterns detected."""
        files = ["test_main.py", "tests/test.py", "main.py"]
        violations = check_forbidden_files_in_list(files, ["test_main.py", "tests/"])
        assert len(violations) == 2
        assert "test_main.py" in violations
        assert "tests/test.py" in violations

    def test_empty_files_list(self):
        """Empty files list returns no violations."""
        violations = check_forbidden_files_in_list([], ["test_main.py"])
        assert violations == []

    def test_empty_patterns_list(self):
        """Empty patterns list returns no violations."""
        violations = check_forbidden_files_in_list(["test_main.py"], [])
        assert violations == []


class TestGitHubClientGetAllModifiedFiles:
    """Tests for get_all_modified_files method."""

    @responses.activate
    def test_single_commit(self):
        """Get modified files from single commit."""
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
                    {"filename": "main.py"},
                    {"filename": "test_main.py"}
                ]
            },
            status=200
        )
        client = GitHubClient("test_token")
        files = client.get_all_modified_files("org", "repo")

        assert len(files) == 2
        assert "main.py" in files
        assert "test_main.py" in files

    @responses.activate
    def test_multiple_commits(self):
        """Get modified files from multiple commits."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits",
            json=[{"sha": "abc123"}, {"sha": "def456"}],
            status=200
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits/abc123",
            json={
                "sha": "abc123",
                "files": [{"filename": "main.py"}]
            },
            status=200
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits/def456",
            json={
                "sha": "def456",
                "files": [{"filename": "test_main.py"}]
            },
            status=200
        )
        client = GitHubClient("test_token")
        files = client.get_all_modified_files("org", "repo")

        assert len(files) == 2
        assert "main.py" in files
        assert "test_main.py" in files

    @responses.activate
    def test_duplicate_files_across_commits(self):
        """Duplicate files across commits are deduplicated."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits",
            json=[{"sha": "abc123"}, {"sha": "def456"}],
            status=200
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits/abc123",
            json={
                "sha": "abc123",
                "files": [{"filename": "main.py"}]
            },
            status=200
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits/def456",
            json={
                "sha": "def456",
                "files": [{"filename": "main.py"}]  # Same file
            },
            status=200
        )
        client = GitHubClient("test_token")
        files = client.get_all_modified_files("org", "repo")

        assert len(files) == 1
        assert "main.py" in files

    @responses.activate
    def test_no_commits(self):
        """No commits returns empty list."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits",
            json=[],
            status=200
        )
        client = GitHubClient("test_token")
        files = client.get_all_modified_files("org", "repo")
        assert files == []

    @responses.activate
    def test_api_error(self):
        """API error returns empty list."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/commits",
            json={"message": "Not Found"},
            status=404
        )
        client = GitHubClient("test_token")
        files = client.get_all_modified_files("org", "repo")
        assert files == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
