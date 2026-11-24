"""
The `.github/` files of the graded commit must match some version of the lab
template's: CI verification trusts runs by workflow path, so editing the file
itself (tests replaced by `exit 0`) must be caught by contents.
"""
from unittest.mock import MagicMock

import pytest
import responses

from grading.bulk import SheetContext, evaluate_student
from grading.github_client import CommitInfo, GitHubClient
from grading.grader import GradeStatus, LabGrader, clear_template_workflows_cache

WORKFLOW = ".github/workflows/classroom.yml"
LAB = {
    "github-prefix": "lab1",
    "template-repo": "teacher/lab1-template",
    "ci": {"workflows": ["run-autograding-tests"]},
}
TEMPLATE_HISTORY = {
    "t2": {WORKFLOW: "blob-new", ".github/actions/run/action.yml": "action-1",
           ".github/.keep": "keep-1"},
    "t1": {WORKFLOW: "blob-old", ".github/actions/run/action.yml": "action-1"},
}
CONTENTS = {
    "blob-new": b"jobs:\n  test: pytest\n",
    "blob-old": b"jobs:\n  test: python -m pytest\n",
    "blob-crlf": b"jobs:\r\n  test: pytest\r\n",
    "action-1": b"runs: pytest\n",
    "edited": b"jobs:\n  test: exit 0\n",
}


@pytest.fixture(autouse=True)
def fresh_template_cache():
    clear_template_workflows_cache()
    yield
    clear_template_workflows_cache()


def make_github(student_files):
    gh = MagicMock(spec=GitHubClient)
    gh.check_required_files.return_value = []
    gh.has_workflows_directory.return_value = True
    gh.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
    gh.list_commits_touching.return_value = list(TEMPLATE_HISTORY)
    gh.get_check_runs.return_value = [{
        "id": 1, "name": "run-autograding-tests", "conclusion": "success",
        "html_url": "u", "completed_at": "2026-10-30T12:00:00Z", "check_suite": {"id": 1},
    }]
    gh.get_workflow_runs.return_value = [{
        "id": 2, "check_suite_id": 1, "path": WORKFLOW,
        "run_started_at": "2026-10-30T12:00:00Z", "head_branch": "main", "run_attempt": 1,
    }]
    gh.list_workflow_files.return_value = [WORKFLOW]

    def blobs(owner, repo, ref):
        if owner == "teacher":
            return TEMPLATE_HISTORY[ref]
        return student_files

    gh.get_github_dir_blobs.side_effect = blobs
    gh.get_blob.side_effect = lambda owner, repo, sha: CONTENTS.get(sha)
    return gh


def grade(gh, lab=LAB):
    return LabGrader(gh).grade("org", "student", lab, current_cell_value="")


def bulk(gh, lab=LAB):
    return evaluate_student(
        LabGrader(gh), "org", "student", lab, {},
        lambda: SheetContext(current_cell_value="", deadline=None),
    )


class TestModifiedFiles:
    def test_unchanged_files_pass(self):
        gh = make_github({WORKFLOW: "blob-new", ".github/actions/run/action.yml": "action-1"})

        assert grade(gh).result == "v"

    def test_older_template_version_passes(self):
        gh = make_github({WORKFLOW: "blob-old"})

        assert grade(gh).result == "v"

    @pytest.mark.parametrize("path", [WORKFLOW, ".github/actions/run/action.yml"])
    def test_modified_file_is_refused(self, path):
        files = {WORKFLOW: "blob-new", ".github/actions/run/action.yml": "action-1"}
        gh = make_github({**files, path: "edited"})

        result = grade(gh)
        outcome = bulk(gh)

        assert result.status == GradeStatus.ERROR
        assert result.error_code == "FORBIDDEN_MODIFICATION"
        assert path in result.message
        assert outcome.status == "error"
        assert outcome.error_code == "FORBIDDEN_MODIFICATION"
        assert outcome.cell_value is None

    def test_refusal_happens_before_ci_is_read(self):
        gh = make_github({WORKFLOW: "edited"})

        grade(gh)

        gh.get_check_runs.assert_not_called()

    def test_graded_commit_is_compared(self):
        gh = make_github({WORKFLOW: "blob-new"})

        grade(gh)

        gh.get_github_dir_blobs.assert_any_call("org", "lab1-student", "abc123")

    def test_line_endings_are_ignored(self):
        gh = make_github({WORKFLOW: "blob-crlf"})

        assert grade(gh).result == "v"

    def test_non_ci_files_are_not_compared(self):
        gh = make_github({WORKFLOW: "blob-new", ".github/.keep": "edited"})

        assert grade(gh).result == "v"

    def test_blob_unavailable_is_refused(self):
        gh = make_github({WORKFLOW: "unknown-blob"})

        assert grade(gh).error_code == "CI_UNVERIFIED"

    def test_files_the_template_never_had_are_allowed(self):
        gh = make_github({WORKFLOW: "blob-new", ".github/workflows/mine.yml": "x"})

        assert grade(gh).result == "v"

    def test_deleted_file_is_not_a_modification(self):
        # Without the template's workflow there is no CI run to count.
        gh = make_github({})

        assert grade(gh).error_code != "FORBIDDEN_MODIFICATION"


class TestSkippedOrUnverified:
    def test_lab_without_template_is_not_checked(self):
        gh = make_github({WORKFLOW: "edited"})
        lab = {k: v for k, v in LAB.items() if k != "template-repo"}

        grade(gh, lab=lab)

        gh.list_commits_touching.assert_not_called()

    def test_template_without_github_dir_is_not_checked(self):
        gh = make_github({WORKFLOW: "edited"})
        gh.list_commits_touching.return_value = []

        assert grade(gh).error_code != "FORBIDDEN_MODIFICATION"

    def test_template_history_unavailable_is_refused(self):
        gh = make_github({WORKFLOW: "blob-new"})
        gh.list_commits_touching.return_value = None

        result = grade(gh)

        assert result.error_code == "CI_UNVERIFIED"
        assert "обратитесь к преподавателю" in result.message.lower()

    def test_student_files_unavailable_is_refused(self):
        gh = make_github(None)

        assert grade(gh).error_code == "CI_UNVERIFIED"

    def test_template_history_is_cached(self):
        gh = make_github({WORKFLOW: "blob-new"})

        grade(gh)
        grade(gh)

        gh.list_commits_touching.assert_called_once_with("teacher", "lab1-template", ".github")


class TestGitHubClient:
    TREES = "https://api.github.com/repos/o/r/git/trees"

    @responses.activate
    def test_get_github_dir_blobs(self):
        responses.add(responses.GET, f"{self.TREES}/abc", json={"tree": [
            {"path": "src", "type": "tree", "sha": "s"},
            {"path": ".github", "type": "tree", "sha": "gh"},
        ]})
        responses.add(responses.GET, f"{self.TREES}/gh", json={"truncated": False, "tree": [
            {"path": "workflows", "type": "tree", "sha": "w"},
            {"path": "workflows/ci.yml", "type": "blob", "sha": "b1"},
        ]})

        blobs = GitHubClient("t").get_github_dir_blobs("o", "r", "abc")

        assert blobs == {".github/workflows/ci.yml": "b1"}
        assert "recursive=1" in responses.calls[1].request.url

    @responses.activate
    def test_no_github_dir(self):
        responses.add(responses.GET, f"{self.TREES}/abc", json={"tree": [
            {"path": "README.md", "type": "blob", "sha": "r"},
        ]})

        assert GitHubClient("t").get_github_dir_blobs("o", "r", "abc") == {}

    @responses.activate
    def test_truncated_listing_is_an_error(self):
        responses.add(responses.GET, f"{self.TREES}/abc", json={"tree": [
            {"path": ".github", "type": "tree", "sha": "gh"},
        ]})
        responses.add(responses.GET, f"{self.TREES}/gh", json={"truncated": True, "tree": []})

        assert GitHubClient("t").get_github_dir_blobs("o", "r", "abc") is None

    @responses.activate
    def test_tree_error(self):
        responses.add(responses.GET, f"{self.TREES}/abc", status=404)

        assert GitHubClient("t").get_github_dir_blobs("o", "r", "abc") is None

    @responses.activate
    def test_get_blob(self):
        url = "https://api.github.com/repos/o/r/git/blobs/b1"
        responses.add(responses.GET, url, json={"encoding": "base64", "content": "aGk=\n"})

        assert GitHubClient("t").get_blob("o", "r", "b1") == b"hi"

    @responses.activate
    def test_list_commits_touching(self):
        url = "https://api.github.com/repos/o/r/commits"
        responses.add(responses.GET, url, json=[{"sha": "c2"}, {"sha": "c1"}])

        assert GitHubClient("t").list_commits_touching("o", "r", ".github") == ["c2", "c1"]
        assert "path=.github" in responses.calls[0].request.url
