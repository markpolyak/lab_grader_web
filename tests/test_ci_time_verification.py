"""
CI times and results are taken from workflow runs, not from check runs.

A workflow in the student's own repository can rewrite the `completed_at` of
finished check runs (and create successful ones) through the Checks API with
its GITHUB_TOKEN. Workflow runs' `run_started_at` and `path` are set by GitHub
and cannot be changed that way.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import responses

from grading.bulk import SheetContext, evaluate_student
from grading.ci_checker import CheckRun, WorkflowRun, verify_successful_runs
from grading.github_client import CommitInfo, GitHubClient
from grading.grader import GradeStatus, LabGrader, clear_template_workflows_cache

DEADLINE = datetime(2026, 11, 1, 23, 59, tzinfo=timezone.utc)
BEFORE_DEADLINE = "2026-10-30T12:00:00Z"
TWO_WEEKS_LATE = "2026-11-14T12:00:00Z"
TEMPLATE_WORKFLOW = ".github/workflows/classroom.yml"
LAB = {
    "github-prefix": "lab1",
    "template-repo": "teacher/lab1-template",
    "ci": {"workflows": ["run-autograding-tests"]},
    "penalty-max": 5,
}


def check_run(completed_at=BEFORE_DEADLINE, suite=1, name="run-autograding-tests"):
    return {
        "id": 100 + suite, "name": name, "conclusion": "success",
        "html_url": f"https://github.com/x/{suite}", "completed_at": completed_at,
        "check_suite": {"id": suite},
    }


def workflow_run(started_at, suite=1, path=TEMPLATE_WORKFLOW, attempt=1):
    return {
        "id": 200 + suite, "check_suite_id": suite, "path": path,
        "run_started_at": started_at, "head_branch": "main", "run_attempt": attempt,
    }


@pytest.fixture(autouse=True)
def fresh_template_cache():
    clear_template_workflows_cache()
    yield
    clear_template_workflows_cache()


@pytest.fixture
def github():
    gh = MagicMock(spec=GitHubClient)
    gh.check_required_files.return_value = []
    gh.has_workflows_directory.return_value = True
    gh.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
    gh.list_workflow_files.return_value = [TEMPLATE_WORKFLOW]
    return gh


def grade(github, deadline=DEADLINE, lab=LAB):
    return LabGrader(github).grade("org", "student", lab, current_cell_value="", deadline=deadline)


def bulk(github, deadline=DEADLINE, lab=LAB):
    return evaluate_student(
        LabGrader(github), "org", "student", lab, {},
        lambda: SheetContext(current_cell_value="", deadline=deadline),
    )


class TestForgedTime:
    def test_rewritten_completed_at_does_not_hide_a_late_run(self, github):
        # The check run claims it finished before the deadline; the workflow
        # run behind it started two weeks after.
        github.get_check_runs.return_value = [check_run(completed_at=BEFORE_DEADLINE)]
        github.get_workflow_runs.return_value = [workflow_run(TWO_WEEKS_LATE)]

        result = grade(github)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v-2"

    def test_single_and_bulk_grading_agree(self, github):
        github.get_check_runs.return_value = [check_run(completed_at=BEFORE_DEADLINE)]
        github.get_workflow_runs.return_value = [workflow_run(TWO_WEEKS_LATE)]

        assert bulk(github).cell_value == grade(github).result == "v-2"

    def test_on_time_run_has_no_penalty(self, github):
        github.get_check_runs.return_value = [check_run(completed_at=TWO_WEEKS_LATE)]
        github.get_workflow_runs.return_value = [workflow_run(BEFORE_DEADLINE)]

        assert grade(github).result == "v"

    def test_rerun_after_deadline_counts_from_the_rerun(self, github):
        github.get_check_runs.return_value = [check_run(completed_at=BEFORE_DEADLINE)]
        github.get_workflow_runs.return_value = [workflow_run(TWO_WEEKS_LATE, attempt=2)]

        assert grade(github).result == "v-2"

    def test_latest_of_several_runs_is_used(self, github):
        github.get_check_runs.return_value = [
            check_run(suite=1), check_run(suite=2, name="lint"),
        ]
        github.get_workflow_runs.return_value = [
            workflow_run(BEFORE_DEADLINE, suite=1), workflow_run(TWO_WEEKS_LATE, suite=2),
        ]
        lab = {**LAB, "ci": {"workflows": ["run-autograding-tests", "lint"]}}

        assert grade(github, lab=lab).result == "v-2"


class TestUnverifiedPass:
    @pytest.mark.parametrize("deadline", [DEADLINE, None])
    def test_check_run_without_workflow_run_is_refused(self, github, deadline):
        # A check run created through the Checks API has no workflow run of
        # its own suite behind it.
        github.get_check_runs.return_value = [check_run(suite=7)]
        github.get_workflow_runs.return_value = [workflow_run(BEFORE_DEADLINE, suite=1)]

        result = grade(github, deadline=deadline)
        outcome = bulk(github, deadline=deadline)

        assert result.status == GradeStatus.ERROR
        assert result.error_code == "CI_UNVERIFIED"
        assert result.result is None
        assert "обратитесь к преподавателю" in result.message.lower()
        assert outcome.status == "error"
        assert outcome.error_code == "CI_UNVERIFIED"
        assert outcome.cell_value is None

    @pytest.mark.parametrize("deadline", [DEADLINE, None])
    def test_run_from_a_workflow_not_in_the_template_is_refused(self, github, deadline):
        github.get_check_runs.return_value = [check_run()]
        github.get_workflow_runs.return_value = [
            workflow_run(BEFORE_DEADLINE, path=".github/workflows/fake.yml")
        ]

        result = grade(github, deadline=deadline)

        assert result.error_code == "CI_UNVERIFIED"
        assert ".github/workflows/fake.yml" in result.message

    def test_workflow_runs_unavailable_is_refused(self, github):
        github.get_check_runs.return_value = [check_run()]
        github.get_workflow_runs.return_value = None

        assert grade(github).error_code == "CI_UNVERIFIED"

    def test_template_workflows_unavailable_is_refused(self, github):
        github.get_check_runs.return_value = [check_run()]
        github.get_workflow_runs.return_value = [workflow_run(BEFORE_DEADLINE)]
        github.list_workflow_files.return_value = None

        assert grade(github).error_code == "CI_UNVERIFIED"

    def test_failed_ci_needs_no_verification(self, github):
        failed = {**check_run(), "conclusion": "failure"}
        github.get_check_runs.return_value = [failed]

        result = grade(github)

        assert result.result == "x"
        github.get_workflow_runs.assert_not_called()


class TestTemplateCheck:
    def test_lab_without_template_skips_the_file_check(self, github):
        lab = {k: v for k, v in LAB.items() if k != "template-repo"}
        github.get_check_runs.return_value = [check_run()]
        github.get_workflow_runs.return_value = [
            workflow_run(BEFORE_DEADLINE, path=".github/workflows/anything.yml")
        ]

        assert grade(github, lab=lab).result == "v"
        github.list_workflow_files.assert_not_called()

    def test_template_without_workflows_skips_the_file_check(self, github):
        github.list_workflow_files.return_value = []
        github.get_check_runs.return_value = [check_run()]
        github.get_workflow_runs.return_value = [
            workflow_run(BEFORE_DEADLINE, path=".github/workflows/student.yml")
        ]

        assert grade(github).result == "v"

    def test_ref_suffix_of_run_path_is_ignored(self, github):
        github.get_check_runs.return_value = [check_run()]
        github.get_workflow_runs.return_value = [
            workflow_run(BEFORE_DEADLINE, path=TEMPLATE_WORKFLOW + "@refs/heads/main")
        ]

        assert grade(github).result == "v"

    def test_template_listing_is_cached(self, github):
        github.get_check_runs.return_value = [check_run()]
        github.get_workflow_runs.return_value = [workflow_run(BEFORE_DEADLINE)]

        grade(github)
        grade(github)

        github.list_workflow_files.assert_called_once_with("teacher", "lab1-template")


def test_verification_keeps_evidence_of_both_times():
    check = CheckRun("t", "success", "u", datetime(2026, 10, 30, tzinfo=timezone.utc), id=5, check_suite_id=1)
    run = WorkflowRun(9, 1, TEMPLATE_WORKFLOW, datetime(2026, 11, 14, tzinfo=timezone.utc), "main", 1)

    result = verify_successful_runs([check], [run], {TEMPLATE_WORKFLOW})

    assert result.success_time == datetime(2026, 11, 14, tzinfo=timezone.utc)
    assert result.problems == []
    assert result.evidence[0]["completed_at"].startswith("2026-10-30")
    assert result.evidence[0]["run_started_at"].startswith("2026-11-14")
    assert result.evidence[0]["workflow_run_id"] == 9


class TestGitHubClient:
    @responses.activate
    def test_get_workflow_runs_follows_pages(self):
        url = "https://api.github.com/repos/o/r/actions/runs"
        responses.add(responses.GET, url, json={"workflow_runs": [{"id": i} for i in range(100)]})
        responses.add(responses.GET, url, json={"workflow_runs": [{"id": 100}]})

        runs = GitHubClient("t").get_workflow_runs("o", "r", "abc")

        assert len(runs) == 101
        assert "head_sha=abc" in responses.calls[0].request.url

    @responses.activate
    def test_get_workflow_runs_error(self):
        responses.add(responses.GET, "https://api.github.com/repos/o/r/actions/runs", status=500)

        assert GitHubClient("t").get_workflow_runs("o", "r", "abc") is None

    @responses.activate
    def test_list_workflow_files(self):
        url = "https://api.github.com/repos/o/t/contents/.github/workflows"
        responses.add(responses.GET, url, json=[
            {"type": "file", "path": ".github/workflows/a.yml"},
            {"type": "dir", "path": ".github/workflows/sub"},
        ])

        assert GitHubClient("t").list_workflow_files("o", "t") == [".github/workflows/a.yml"]

    @responses.activate
    def test_list_workflow_files_missing_directory(self):
        url = "https://api.github.com/repos/o/t/contents/.github/workflows"
        responses.add(responses.GET, url, status=404)

        assert GitHubClient("t").list_workflow_files("o", "t") == []
