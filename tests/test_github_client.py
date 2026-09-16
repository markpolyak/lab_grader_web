"""
Unit tests for grading/github_client.py

Tests GitHub API client with mocked HTTP responses.
"""
import json
import pytest
import requests
import responses
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.github_client import (
    GitHubClient,
    CommitInfo,
    check_forbidden_modifications,
    get_default_forbidden_patterns,
    is_rate_limited,
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


class TestGitHubClientListForks:
    """Tests for list_forks method."""

    @responses.activate
    def test_lists_forks_across_two_pages(self):
        """per_page defaults to 30 on GitHub's side - without paging through,
        a course with 200 students would silently lose most of the forks (issue #52)."""
        page1 = [{"name": f"os-task1-student{i}"} for i in range(100)]
        page2 = [{"name": "os-task1-student100"}]
        responses.add(
            responses.GET,
            "https://api.github.com/repos/owner/template/forks",
            json=page1,
            status=200,
            match=[responses.matchers.query_param_matcher({"per_page": "100", "page": "1"})],
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/owner/template/forks",
            json=page2,
            status=200,
            match=[responses.matchers.query_param_matcher({"per_page": "100", "page": "2"})],
        )
        client = GitHubClient("test_token")
        forks = client.list_forks("owner", "template")
        assert len(forks) == 101
        assert forks[-1]["name"] == "os-task1-student100"

    @responses.activate
    def test_list_forks_error_returns_none(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/owner/template/forks",
            json={"message": "Not Found"},
            status=404,
        )
        client = GitHubClient("test_token")
        assert client.list_forks("owner", "template") is None


class TestGitHubClientListOrgRepos:
    """Tests for list_org_repos method."""

    @responses.activate
    def test_lists_org_repos_across_two_pages(self):
        page1 = [{"name": f"repo{i}"} for i in range(100)]
        page2 = [{"name": "repo100"}]
        responses.add(
            responses.GET,
            "https://api.github.com/orgs/test-org/repos",
            json=page1,
            status=200,
            match=[responses.matchers.query_param_matcher({"per_page": "100", "page": "1"})],
        )
        responses.add(
            responses.GET,
            "https://api.github.com/orgs/test-org/repos",
            json=page2,
            status=200,
            match=[responses.matchers.query_param_matcher({"per_page": "100", "page": "2"})],
        )
        client = GitHubClient("test_token")
        repos = client.list_org_repos("test-org")
        assert len(repos) == 101

    @responses.activate
    def test_single_short_page_stops_after_one_request(self):
        call = responses.add(
            responses.GET,
            "https://api.github.com/orgs/test-org/repos",
            json=[{"name": "repo1"}],
            status=200,
        )
        client = GitHubClient("test_token")
        repos = client.list_org_repos("test-org")
        assert len(repos) == 1
        assert call.call_count == 1


class TestGitHubClientGetFileContent:
    """Tests for get_file_content method (used by bulk grading)."""

    URL = "https://api.github.com/repos/test-org/test-repo/contents/info.md"

    def _file_payload(self, text, **overrides):
        import base64
        payload = {
            "type": "file",
            "encoding": "base64",
            "size": len(text.encode("utf-8")),
            "content": base64.b64encode(text.encode("utf-8")).decode(),
        }
        payload.update(overrides)
        return payload

    @responses.activate
    def test_decodes_utf8(self):
        """Cyrillic content comes back intact."""
        responses.add(
            responses.GET, self.URL,
            json=self._file_payload("Иванов Иван\nЛР1"),
            status=200
        )
        client = GitHubClient("test_token")
        assert client.get_file_content("test-org", "test-repo", "info.md") == "Иванов Иван\nЛР1"

    @responses.activate
    def test_strips_bom(self):
        """A UTF-8 BOM must not end up glued to the student's surname."""
        responses.add(
            responses.GET, self.URL,
            json=self._file_payload("\ufeffИванов Иван"),
            status=200
        )
        client = GitHubClient("test_token")
        assert client.get_file_content("test-org", "test-repo", "info.md") == "Иванов Иван"

    @responses.activate
    def test_missing_file(self):
        """A missing file returns None."""
        responses.add(responses.GET, self.URL, json={"message": "Not Found"}, status=404)
        client = GitHubClient("test_token")
        assert client.get_file_content("test-org", "test-repo", "info.md") is None

    @responses.activate
    def test_directory_returns_none(self):
        """A directory path answers with a list of entries, not content."""
        responses.add(responses.GET, self.URL, json=[{"name": "a.md"}], status=200)
        client = GitHubClient("test_token")
        assert client.get_file_content("test-org", "test-repo", "info.md") is None

    @responses.activate
    def test_oversized_file_is_skipped(self):
        """Files above the size limit are not decoded."""
        responses.add(
            responses.GET, self.URL,
            json=self._file_payload("Иванов Иван", size=10 * 1024 * 1024),
            status=200
        )
        client = GitHubClient("test_token")
        assert client.get_file_content("test-org", "test-repo", "info.md") is None

    @responses.activate
    def test_unsupported_encoding_returns_none(self):
        """GitHub omits the body of large files, answering encoding "none"."""
        responses.add(
            responses.GET, self.URL,
            json={"type": "file", "encoding": "none", "size": 4, "content": ""},
            status=200
        )
        client = GitHubClient("test_token")
        assert client.get_file_content("test-org", "test-repo", "info.md") is None

    @responses.activate
    def test_non_utf8_content_returns_none(self):
        """Binary content that is not valid UTF-8 returns None, never raises."""
        import base64
        responses.add(
            responses.GET, self.URL,
            json={
                "type": "file",
                "encoding": "base64",
                "size": 4,
                "content": base64.b64encode(b"\xff\xfe\x00\x01").decode(),
            },
            status=200
        )
        client = GitHubClient("test_token")
        assert client.get_file_content("test-org", "test-repo", "info.md") is None


class TestGitHubClientCreatePullRequest:
    """Tests for create_pull_request method."""

    @responses.activate
    def test_posts_head_base_title_body(self):
        call = responses.add(
            responses.POST,
            "https://api.github.com/repos/org/os-task1-student1/pulls",
            json={"html_url": "https://github.com/org/os-task1-student1/pull/1"},
            status=201,
        )
        client = GitHubClient("test_token")
        resp = client.create_pull_request(
            "org", "os-task1-student1",
            head="template-owner:main", base="main",
            title="Обновление стартового кода", body="body text",
        )

        assert resp.status_code == 201
        assert json.loads(call.calls[0].request.body) == {
            "head": "template-owner:main",
            "base": "main",
            "title": "Обновление стартового кода",
            "body": "body text",
        }


class TestGitHubClientListPullRequests:
    """Tests for list_pull_requests method."""

    @responses.activate
    def test_filters_by_head_and_state(self):
        call = responses.add(
            responses.GET,
            "https://api.github.com/repos/org/os-task1-student1/pulls",
            json=[{"html_url": "https://github.com/org/os-task1-student1/pull/1"}],
            status=200,
        )
        client = GitHubClient("test_token")
        prs = client.list_pull_requests("org", "os-task1-student1", head="template-owner:main", state="open")

        assert len(prs) == 1
        request_params = call.calls[0].request.params
        assert request_params["head"] == "template-owner:main"
        assert request_params["state"] == "open"

    @responses.activate
    def test_error_returns_none(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/repo/pulls",
            json={"message": "Not Found"},
            status=404,
        )
        client = GitHubClient("test_token")
        assert client.list_pull_requests("org", "repo") is None


class TestIsRateLimited:
    """Tests for the shared is_rate_limited helper."""

    @responses.activate
    def test_retry_after_header_means_rate_limited(self):
        responses.add(
            responses.POST,
            "https://api.github.com/repos/org/repo/pulls",
            json={"message": "secondary rate limit"},
            status=403,
            headers={"Retry-After": "30"},
        )
        client = GitHubClient("test_token")
        resp = client.create_pull_request("org", "repo", head="a:b", base="main", title="t", body="b")
        assert is_rate_limited(resp) is True

    @responses.activate
    def test_permission_denied_is_not_rate_limited(self):
        responses.add(
            responses.POST,
            "https://api.github.com/repos/org/repo/pulls",
            json={"message": "Must have admin rights"},
            status=403,
        )
        client = GitHubClient("test_token")
        resp = client.create_pull_request("org", "repo", head="a:b", base="main", title="t", body="b")
        assert is_rate_limited(resp) is False


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


class TestGitHubClientRefs:
    """Tests for get_ref / create_ref / update_ref (issue #52)."""

    @responses.activate
    def test_get_ref_returns_object_sha(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/template/git/ref/heads/master",
            json={"ref": "refs/heads/master", "object": {"sha": "abc123"}},
            status=200,
        )
        client = GitHubClient("test_token")

        assert client.get_ref("org", "template", "heads/master")["object"]["sha"] == "abc123"

    @responses.activate
    def test_get_ref_missing_returns_none(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/template/git/ref/heads/nope",
            json={"message": "Not Found"},
            status=404,
        )
        client = GitHubClient("test_token")

        assert client.get_ref("org", "template", "heads/nope") is None

    @responses.activate
    def test_compare_commits_returns_ahead_by_and_files(self):
        call = responses.add(
            responses.GET,
            "https://api.github.com/repos/org/os-task1-student1/compare/main...abc123",
            json={"status": "diverged", "ahead_by": 2, "behind_by": 1, "files": [{"filename": "lab1.cpp"}]},
            status=200,
        )
        client = GitHubClient("test_token")

        comparison = client.compare_commits("org", "os-task1-student1", "main", "abc123")

        assert comparison["ahead_by"] == 2
        assert comparison["files"] == [{"filename": "lab1.cpp"}]
        assert "per_page=1" in call.calls[0].request.url

    @responses.activate
    def test_compare_commits_failure_returns_none(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/org/os-task1-student1/compare/main...abc123",
            json={"message": "Not Found"},
            status=404,
        )
        client = GitHubClient("test_token")

        assert client.compare_commits("org", "os-task1-student1", "main", "abc123") is None

    @responses.activate
    def test_create_ref_posts_full_ref_and_sha(self):
        call = responses.add(
            responses.POST,
            "https://api.github.com/repos/org/os-task1-student1/git/refs",
            json={"ref": "refs/heads/template-update"},
            status=201,
        )
        client = GitHubClient("test_token")
        resp = client.create_ref("org", "os-task1-student1", "refs/heads/template-update", "abc123")

        assert resp.status_code == 201
        assert json.loads(call.calls[0].request.body) == {
            "ref": "refs/heads/template-update",
            "sha": "abc123",
        }

    @responses.activate
    def test_update_ref_patches_with_force(self):
        call = responses.add(
            responses.PATCH,
            "https://api.github.com/repos/org/os-task1-student1/git/refs/heads/template-update",
            json={"ref": "refs/heads/template-update"},
            status=200,
        )
        client = GitHubClient("test_token")
        resp = client.update_ref("org", "os-task1-student1", "heads/template-update", "abc123")

        assert resp.status_code == 200
        assert json.loads(call.calls[0].request.body) == {"sha": "abc123", "force": True}


class TestListCollaborators:
    """Roster reading for team labs (docs/TEAM_ASSIGNMENTS_PLAN.md §9.2)."""

    @responses.activate
    def test_returns_collaborators_with_permissions(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/os-task5-team-1/collaborators",
            json=[
                {"login": "alice", "permissions": {"push": True, "admin": False}},
                {"login": "owner", "permissions": {"push": True, "admin": True}},
            ],
            status=200,
        )
        client = GitHubClient("test_token")

        result = client.list_collaborators("test-org", "os-task5-team-1")

        assert [entry["login"] for entry in result] == ["alice", "owner"]
        assert responses.calls[0].request.params["affiliation"] == "direct"

    @responses.activate
    def test_affiliation_is_passed_through(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/repo/collaborators",
            json=[],
            status=200,
        )
        GitHubClient("test_token").list_collaborators("test-org", "repo", affiliation="all")

        assert responses.calls[0].request.params["affiliation"] == "all"

    @responses.activate
    def test_error_returns_none(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/repo/collaborators",
            status=404,
        )
        assert GitHubClient("test_token").list_collaborators("test-org", "repo") is None


class TestGitHubClientGetJobLogs:
    """
    Скачивание логов задания (перенесено из PR #42, закрытого в пользу этой
    ветки). У метода свой, более длинный таймаут: логи качаются целиком и
    весят мегабайты. Сетевая ошибка здесь не должна ронять проверку - без
    логов не извлекутся баллы и TASKID, но результат CI уже известен.
    """

    LOGS_URL = "https://api.github.com/repos/org/repo/actions/jobs/12345/logs"

    @responses.activate
    def test_logs_are_returned_as_text(self):
        responses.add(responses.GET, self.LOGS_URL, body="TASKID=5\nDone.", status=200)

        logs = GitHubClient("test_token").get_job_logs("org", "repo", 12345)

        assert logs is not None and "TASKID=5" in logs

    @responses.activate
    def test_http_error_returns_none(self):
        responses.add(responses.GET, self.LOGS_URL, json={"message": "Not Found"}, status=404)

        assert GitHubClient("test_token").get_job_logs("org", "repo", 12345) is None

    @responses.activate
    def test_network_error_returns_none_instead_of_raising(self):
        import requests as requests_lib

        responses.add(
            responses.GET,
            self.LOGS_URL,
            body=requests_lib.exceptions.ConnectionError("Remote end closed connection"),
        )

        assert GitHubClient("test_token").get_job_logs("org", "repo", 12345) is None

    @responses.activate
    def test_logs_use_their_own_longer_timeout(self):
        responses.add(responses.GET, self.LOGS_URL, body="ok", status=200)
        client = GitHubClient("test_token")

        with patch("grading.github_client.requests.get", wraps=requests.get) as spy:
            client.get_job_logs("org", "repo", 12345)

        assert spy.call_args.kwargs["timeout"] == GitHubClient.LOGS_TIMEOUT
        assert GitHubClient.LOGS_TIMEOUT > GitHubClient.DEFAULT_TIMEOUT


class TestEveryRequestHasATimeout:
    """
    Страховка от возврата исходной проблемы PR #42: вызов requests без
    timeout ждёт ответа бесконечно, и одно зависшее соединение останавливает
    проверку целой группы - сторожевого таймера у массовой проверки нет.
    Проверяется весь модуль разбором исходника, а не перечислением методов:
    новый метод без таймаута тоже будет пойман.
    """

    def test_no_request_without_timeout(self):
        import ast
        import inspect

        import grading.github_client as module

        tree = ast.parse(inspect.getsource(module))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
                continue
            if func.value.id != "requests":
                continue
            if func.attr not in {"get", "post", "put", "patch", "delete", "request"}:
                continue
            if not any(kw.arg == "timeout" for kw in node.keywords):
                offenders.append(f"requests.{func.attr} на строке {node.lineno}")

        assert offenders == [], "вызовы без timeout: " + ", ".join(offenders)
