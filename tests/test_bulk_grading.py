"""
Tests for bulk grading (grading/bulk.py).

Covers repository discovery, student matching by full name, the shared
grading decision, the background job store and the orchestrator.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.bulk import (
    BulkGradingError,
    BulkJob,
    BulkResult,
    NameMatchError,
    SheetContext,
    StudentOutcome,
    evaluate_student,
    extract_full_name,
    filter_lab_repos,
    find_row_by_full_name,
    normalize_full_name,
    repo_name_for,
    resolve_github_cell,
    run_bulk_grading,
    taskid_column,
    try_start_bulk_job,
    get_bulk_job,
    request_bulk_job_cancel,
    _jobs,
    _running_keys,
)
from grading.grader import GradeResult, GradeStatus, CIEvaluation
from grading.sheets_client import (
    cell_from_grid,
    column_values_from_grid,
    find_lab_column_in_grid,
    get_deadline_from_grid,
    get_student_order_from_grid,
    parse_deadline,
)


class TestFilterLabRepos:
    """Selecting a lab's repositories out of an organization."""

    def test_extracts_username_after_prefix(self):
        repos = ["os-task1-alice", "os-task1-bob", "other-repo"]
        assert filter_lab_repos(repos, "os-task1") == {
            "alice": "os-task1-alice",
            "bob": "os-task1-bob",
        }

    def test_prefix_collision_with_longer_lab_number(self):
        """os-task1 must not swallow os-task10 repositories."""
        repos = ["os-task1-alice", "os-task10-bob", "os-task11-carol"]

        assert filter_lab_repos(repos, "os-task1") == {"alice": "os-task1-alice"}
        assert filter_lab_repos(repos, "os-task10") == {"bob": "os-task10-bob"}

    def test_username_with_dashes(self):
        assert filter_lab_repos(["os-task1-jane-doe"], "os-task1") == {
            "jane-doe": "os-task1-jane-doe"
        }

    def test_template_repo_without_username_is_skipped(self):
        assert filter_lab_repos(["os-task1", "os-task1-"], "os-task1") == {}

    def test_empty_prefix_matches_nothing(self):
        assert filter_lab_repos(["os-task1-alice"], "") == {}

    def test_no_repos(self):
        assert filter_lab_repos([], "os-task1") == {}


class TestExtractFullName:
    """Reading the student's name out of the name file."""

    def test_first_line(self):
        assert extract_full_name("Иванов Иван Иванович\nЛР1\n") == "Иванов Иван Иванович"

    def test_skips_leading_blank_lines(self):
        assert extract_full_name("\n\n  Петров Пётр  \n") == "Петров Пётр"

    def test_crlf_line_endings(self):
        assert extract_full_name("Сидоров Сидор\r\nтекст") == "Сидоров Сидор"

    def test_empty_file(self):
        assert extract_full_name("") is None

    def test_whitespace_only_file(self):
        assert extract_full_name("   \n\t\n") is None

    def test_missing_file(self):
        assert extract_full_name(None) is None


class TestNormalizeFullName:
    """Name normalization used for matching."""

    def test_collapses_whitespace(self):
        assert normalize_full_name("Иванов  Иван\tИванович") == "иванов иван иванович"

    def test_case_insensitive(self):
        assert normalize_full_name("ИВАНОВ Иван") == normalize_full_name("иванов иван")

    def test_yo_equals_ye(self):
        assert normalize_full_name("Алёшин Пётр") == normalize_full_name("Алешин Петр")

    def test_non_breaking_space(self):
        assert normalize_full_name("Иванов Иван") == "иванов иван"

    def test_empty(self):
        assert normalize_full_name("") == ""
        assert normalize_full_name(None) == ""


class TestFindRowByFullName:
    """Resolving a name to a spreadsheet row."""

    NAMES = ["Иванов Иван Иванович", "Петров Пётр Петрович", "Сидоров Сидор"]

    def test_exact_match(self):
        assert find_row_by_full_name(self.NAMES, "Петров Пётр Петрович") == 4

    def test_first_row_is_three(self):
        """Two header rows precede student data."""
        assert find_row_by_full_name(self.NAMES, "Иванов Иван Иванович") == 3

    def test_match_after_normalization(self):
        assert find_row_by_full_name(self.NAMES, "петров  петр   петрович") == 4

    def test_unmatched_raises(self):
        with pytest.raises(NameMatchError) as exc:
            find_row_by_full_name(self.NAMES, "Неизвестный Студент")
        assert exc.value.code == "unmatched"

    def test_empty_name_raises_unmatched(self):
        with pytest.raises(NameMatchError) as exc:
            find_row_by_full_name(self.NAMES, "   ")
        assert exc.value.code == "unmatched"

    def test_ambiguous_raises(self):
        names = ["Иванов Иван", "Петров Пётр", "Иванов Иван"]
        with pytest.raises(NameMatchError) as exc:
            find_row_by_full_name(names, "Иванов Иван")
        assert exc.value.code == "ambiguous"
        assert "3" in exc.value.message and "5" in exc.value.message

    def test_surname_alone_does_not_match(self):
        """Fuzzy matching is deliberately not attempted."""
        with pytest.raises(NameMatchError):
            find_row_by_full_name(self.NAMES, "Иванов")


class TestResolveGithubCell:
    """Policy for writing the GitHub username into the sheet."""

    def test_empty_cell_is_written(self):
        assert resolve_github_cell("", "alice") == (True, None)

    def test_whitespace_cell_is_written(self):
        assert resolve_github_cell("   ", "alice") == (True, None)

    def test_same_username_is_not_rewritten(self):
        assert resolve_github_cell("alice", "alice") == (False, None)

    def test_same_username_different_case(self):
        should_write, conflict = resolve_github_cell("Alice", "alice")
        assert should_write is False
        assert conflict is None

    def test_different_username_is_a_conflict(self):
        should_write, conflict = resolve_github_cell("bob", "alice")
        assert should_write is False
        assert "bob" in conflict and "alice" in conflict


class TestGridHelpers:
    """In-memory equivalents of the per-cell Sheets helpers."""

    GRID = [
        ["", "", "", "19.11.2025"],
        ["№", "ФИО", "GitHub", "ЛР1"],
        ["1", "Иванов Иван", "alice", "v"],
        ["2", "Петров Пётр", "bob"],
    ]

    def test_cell_from_grid(self):
        assert cell_from_grid(self.GRID, 3, 3) == "alice"

    def test_cell_beyond_row_length(self):
        """Trailing empty cells are omitted by gspread."""
        assert cell_from_grid(self.GRID, 4, 4) == ""

    def test_cell_beyond_grid(self):
        assert cell_from_grid(self.GRID, 99, 1) == ""
        assert cell_from_grid(self.GRID, 0, 1) == ""

    def test_column_values_pads_missing_cells(self):
        assert column_values_from_grid(self.GRID, 4, start_row=3) == ["v", ""]

    def test_column_values_from_top(self):
        assert column_values_from_grid(self.GRID, 3) == ["", "GitHub", "alice", "bob"]

    def test_find_lab_column(self):
        assert find_lab_column_in_grid(self.GRID, "ЛР1") == 4

    def test_find_lab_column_missing(self):
        assert find_lab_column_in_grid(self.GRID, "ЛР9") is None

    def test_find_lab_column_empty_name(self):
        assert find_lab_column_in_grid(self.GRID, "") is None

    def test_deadline_from_grid(self):
        deadline = get_deadline_from_grid(self.GRID, 4, deadline_row=1, timezone_str="UTC+3")
        assert deadline.year == 2025 and deadline.month == 11 and deadline.day == 19
        # A date without a time means the end of that day
        assert (deadline.hour, deadline.minute) == (23, 59)
        assert deadline.tzinfo is not None

    def test_deadline_missing(self):
        assert get_deadline_from_grid(self.GRID, 2, deadline_row=1) is None

    def test_student_order_from_grid(self):
        assert get_student_order_from_grid(self.GRID, 3, 1) == 1
        assert get_student_order_from_grid(self.GRID, 4, 1) == 2

    def test_student_order_unparseable(self):
        assert get_student_order_from_grid(self.GRID, 2, 1) is None

    def test_parse_deadline_matches_sheet_version(self):
        assert parse_deadline("15.03.2025 23:59").hour == 23
        assert parse_deadline("") is None
        assert parse_deadline("не дата") is None


class TestTaskidColumn:
    """When the TASKID check applies."""

    COURSE = {"google": {"task-id-column": 0}}

    def test_configured(self):
        assert taskid_column(self.COURSE, {"taskid-max": 20}) == 1

    def test_no_column_in_course(self):
        assert taskid_column({"google": {}}, {"taskid-max": 20}) is None

    def test_no_taskid_max_in_lab(self):
        assert taskid_column(self.COURSE, {}) is None

    def test_ignore_task_id(self):
        assert taskid_column(self.COURSE, {"taskid-max": 20, "ignore-task-id": True}) is None

    def test_team_lab_never_checks_taskid(self):
        """A team has no position in the sheet to derive a variant from."""
        assert taskid_column(self.COURSE, {"taskid-max": 20, "team": {}}) is None
        assert taskid_column(self.COURSE, {"taskid-max": 20, "team": None}) is None


class TestRepoNameFor:
    def test_builds_conventional_name(self):
        assert repo_name_for({"github-prefix": "os-task2"}, "alice") == "os-task2-alice"


def _grade_result(status, result=None, message="", error_code=None, passed=None):
    return GradeResult(
        status=status,
        result=result,
        message=message,
        passed=passed,
        error_code=error_code,
    )


def _grader_mock(ci_evaluation, repo_error=None, forbidden_error=None, taskid_error=None):
    grader = MagicMock()
    grader.check_repository.return_value = repo_error
    grader.check_forbidden_files.return_value = forbidden_error
    grader._evaluate_ci_internal.return_value = ci_evaluation
    grader.check_taskid.return_value = taskid_error
    return grader


class TestEvaluateStudent:
    """The grading decision shared by the endpoint and the bulk run."""

    LAB = {"github-prefix": "os-task1", "short-name": "ЛР1"}
    COURSE = {"github": {"organization": "test-org"}, "google": {}}

    def test_passing_ci_gives_v(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "v", "Результат CI: ✅ Все проверки пройдены"),
            ci_passed=True,
        )
        outcome = evaluate_student(
            _grader_mock(ci), "test-org", "alice", self.LAB, self.COURSE,
            lambda: SheetContext(),
        )
        assert outcome.status == "updated"
        assert outcome.cell_value == "v"

    def test_failing_ci_gives_x(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "x", "Результат CI: ❌ Обнаружены ошибки"),
            ci_passed=False,
        )
        outcome = evaluate_student(
            _grader_mock(ci), "test-org", "alice", self.LAB, self.COURSE,
            lambda: SheetContext(),
        )
        assert outcome.status == "updated"
        assert outcome.cell_value == "x"

    def test_repository_error_short_circuits(self):
        repo_error = _grade_result(
            GradeStatus.ERROR, message="Нет коммитов в репозитории", error_code="NO_COMMITS"
        )
        grader = _grader_mock(None, repo_error=repo_error)

        outcome = evaluate_student(
            grader, "test-org", "alice", self.LAB, self.COURSE, lambda: SheetContext(),
        )
        assert outcome.status == "error"
        assert outcome.error_code == "NO_COMMITS"
        grader._evaluate_ci_internal.assert_not_called()

    def test_forbidden_modification_short_circuits(self):
        forbidden = _grade_result(
            GradeStatus.ERROR, message="🚨 Нельзя изменять test_main.py",
            error_code="FORBIDDEN_MODIFICATION",
        )
        grader = _grader_mock(None, forbidden_error=forbidden)

        outcome = evaluate_student(
            grader, "test-org", "alice", self.LAB, self.COURSE, lambda: SheetContext(),
        )
        assert outcome.status == "error"
        assert outcome.error_code == "FORBIDDEN_MODIFICATION"

    def test_sheet_context_not_requested_on_error(self):
        """Callers may defer opening a Sheets connection until a grade exists."""
        repo_error = _grade_result(GradeStatus.ERROR, message="нет файла", error_code="MISSING_FILES")
        provider = MagicMock()

        evaluate_student(
            _grader_mock(None, repo_error=repo_error),
            "test-org", "alice", self.LAB, self.COURSE, provider,
        )
        provider.assert_not_called()

    def test_sheet_context_not_requested_when_pending(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.PENDING, message="CI-проверки ещё выполняются ⏳"),
            ci_passed=False,
        )
        provider = MagicMock()

        outcome = evaluate_student(
            _grader_mock(ci), "test-org", "alice", self.LAB, self.COURSE, provider,
        )
        assert outcome.status == "pending"
        provider.assert_not_called()

    def test_protected_cell_is_rejected(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "v", "ok"),
            ci_passed=True,
        )
        outcome = evaluate_student(
            _grader_mock(ci), "test-org", "alice", self.LAB, self.COURSE,
            lambda: SheetContext(current_cell_value="v"),
        )
        assert outcome.status == "rejected"
        assert outcome.current_grade == "v"

    def test_x_in_cell_can_be_overwritten(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "v", "ok"),
            ci_passed=True,
        )
        outcome = evaluate_student(
            _grader_mock(ci), "test-org", "alice", self.LAB, self.COURSE,
            lambda: SheetContext(current_cell_value="x"),
        )
        assert outcome.status == "updated"

    def test_penalty_applied_for_late_submission(self):
        deadline = datetime(2025, 3, 15, 23, 59)
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "v", "ok"),
            ci_passed=True,
            latest_success_time=deadline + timedelta(days=8),
        )
        lab = dict(self.LAB, **{"penalty-max": 9})

        outcome = evaluate_student(
            _grader_mock(ci), "test-org", "alice", lab, self.COURSE,
            lambda: SheetContext(deadline=deadline),
        )
        assert outcome.cell_value == "v-2"
        assert "штраф" in outcome.message

    def test_score_formatted_with_sheet_separator(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "v", "ok"),
            ci_passed=True,
            score="10.5",
        )
        outcome = evaluate_student(
            _grader_mock(ci), "test-org", "alice", self.LAB, self.COURSE,
            lambda: SheetContext(decimal_separator=","),
        )
        assert outcome.cell_value == "v@10,5"
        assert outcome.score == "10,5"

    def test_taskid_checked_when_configured(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "v", "ok"),
            ci_passed=True,
        )
        taskid_error = _grade_result(
            GradeStatus.ERROR, message="Неверный вариант", error_code="WRONG_TASKID"
        )
        grader = _grader_mock(ci, taskid_error=taskid_error)
        course = {"github": {"organization": "test-org"}, "google": {"task-id-column": 0}}
        lab = dict(self.LAB, **{"taskid-max": 20, "taskid-shift": 4})

        outcome = evaluate_student(
            grader, "test-org", "alice", lab, course,
            lambda: SheetContext(student_order=3),
        )
        assert outcome.status == "error"
        assert outcome.error_code == "WRONG_TASKID"
        grader.check_taskid.assert_called_once()

    def test_taskid_not_checked_when_ignored(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "v", "ok"),
            ci_passed=True,
        )
        grader = _grader_mock(ci)
        course = {"github": {"organization": "test-org"}, "google": {"task-id-column": 0}}
        lab = dict(self.LAB, **{"taskid-max": 20, "ignore-task-id": True})

        evaluate_student(
            grader, "test-org", "alice", lab, course,
            lambda: SheetContext(student_order=3),
        )
        grader.check_taskid.assert_not_called()

    def test_no_taskid_check_when_ci_failed(self):
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "x", "ошибки"),
            ci_passed=False,
        )
        grader = _grader_mock(ci)
        course = {"github": {"organization": "test-org"}, "google": {"task-id-column": 0}}
        lab = dict(self.LAB, **{"taskid-max": 20})

        outcome = evaluate_student(
            grader, "test-org", "alice", lab, course,
            lambda: SheetContext(student_order=3),
        )
        assert outcome.cell_value == "x"
        grader.check_taskid.assert_not_called()


@pytest.fixture(autouse=True)
def clean_job_store():
    """Same isolation as tests/test_admin_endpoints.py does for propagate jobs."""
    _jobs.clear()
    _running_keys.clear()
    yield
    _jobs.clear()
    _running_keys.clear()


class TestBulkJobStore:
    """Lifecycle of background jobs, mirroring propagate's job store."""

    def test_start_and_get(self):
        job = try_start_bulk_job("c", "g", "ЛР1", "by_sheet", False, None)

        assert get_bulk_job(job.job_id) is job
        assert job.status == "running"
        assert job.started_at

    def test_get_unknown_job(self):
        assert get_bulk_job("nope") is None

    def test_second_job_for_same_group_and_lab_is_refused(self):
        first = try_start_bulk_job("c", "g", "ЛР1", "by_sheet", False, None)

        assert try_start_bulk_job("c", "g", "ЛР1", "by_sheet", False, None) is None
        assert first.status == "running"

    def test_other_group_or_lab_may_run_concurrently(self):
        try_start_bulk_job("c", "g", "ЛР1", "by_sheet", False, None)

        assert try_start_bulk_job("c", "g2", "ЛР1", "by_sheet", False, None) is not None
        assert try_start_bulk_job("c", "g", "ЛР2", "by_sheet", False, None) is not None

    def test_finished_job_releases_the_slot(self, bulk_setup):
        job = try_start_bulk_job("c", "g", "ЛР1", "by_sheet", False, None)
        _run(job, bulk_setup)

        assert job.status == "done"
        assert try_start_bulk_job("c", "g", "ЛР1", "by_sheet", False, None) is not None

    def test_request_cancel(self):
        job = try_start_bulk_job("c", "g", "ЛР1", "by_sheet", False, None)

        assert request_bulk_job_cancel(job.job_id) is job
        assert job.cancel_requested is True

    def test_request_cancel_unknown_job(self):
        assert request_bulk_job_cancel("nope") is None

    def test_to_dict_counts_statuses(self):
        job = BulkJob(job_id="j", course_id="c", group_id="g", lab_id="ЛР1", mode="by_sheet")
        job.results = [
            BulkResult(status="updated"), BulkResult(status="updated"),
            BulkResult(status="error"),
        ]

        payload = job.to_dict()
        assert payload["counts"] == {"updated": 2, "error": 1}
        assert len(payload["results"]) == 3


@pytest.fixture
def bulk_setup():
    """Worksheet, spreadsheet and configs for orchestrator tests."""
    # Row 1 carries the left-hand headers and the lab deadline, row 2 the lab
    # short name; student data starts at row 3 - as grade_lab reads them.
    grid = [
        ["№", "ФИО", "GitHub", ""],
        ["", "", "", "ЛР1"],
        ["1", "Иванов Иван", "alice", ""],
        ["2", "Петров Пётр", "bob", ""],
        ["3", "Сидоров Сидор", "", ""],
    ]

    worksheet = MagicMock()
    worksheet.get_all_values.return_value = grid

    spreadsheet = MagicMock()
    spreadsheet.fetch_sheet_metadata.return_value = {"properties": {"locale": "en_US"}}

    return {
        "grid": grid,
        "worksheet": worksheet,
        "spreadsheet": spreadsheet,
        "course_info": {
            "github": {"organization": "test-org"},
            "google": {"spreadsheet": "sid", "student-name-column": 1},
        },
        "lab_config": {"github-prefix": "os-task1", "short-name": "ЛР1"},
    }


def _passing_grader():
    ci = CIEvaluation(
        grade_result=_grade_result(GradeStatus.UPDATED, "v", "Результат CI: ✅ Все проверки пройдены"),
        ci_passed=True,
    )
    return _grader_mock(ci)


def _job(mode="by_sheet", dry_run=False, name_file=None):
    return BulkJob(
        job_id="j", course_id="c", group_id="g", lab_id="ЛР1",
        mode=mode, dry_run=dry_run, name_file=name_file,
    )


def _run(job, setup, grader=None, github_client=None):
    run_bulk_grading(
        job,
        grader or _passing_grader(),
        github_client or MagicMock(),
        setup["worksheet"],
        setup["spreadsheet"],
        setup["course_info"],
        setup["lab_config"],
        1,
    )


def _written_cells(worksheet):
    """Collect (range, value) pairs from every batch_update call."""
    written = []
    for call in worksheet.batch_update.call_args_list:
        for entry in call.args[0]:
            written.append((entry["range"], entry["values"][0][0]))
    return written


class TestRunBulkGradingBySheet:
    """Mode with an empty name file: grade whoever has a username."""

    def test_grades_students_with_usernames_only(self, bulk_setup):
        job = _job()
        _run(job, bulk_setup)

        assert job.status == "done"
        assert job.total == 2  # Сидоров has no GitHub username
        assert job.processed == 2
        assert [r.github for r in job.results] == ["alice", "bob"]

    def test_writes_grades_to_the_lab_column(self, bulk_setup):
        job = _job()
        _run(job, bulk_setup)

        assert _written_cells(bulk_setup["worksheet"]) == [("D3", "v"), ("D4", "v")]

    def test_dry_run_writes_nothing(self, bulk_setup):
        job = _job(dry_run=True)
        _run(job, bulk_setup)

        assert job.status == "done"
        assert [r.status for r in job.results] == ["updated", "updated"]
        bulk_setup["worksheet"].batch_update.assert_not_called()

    def test_protected_cell_is_reported_not_overwritten(self, bulk_setup):
        bulk_setup["grid"][2][3] = "v@8"
        job = _job()
        _run(job, bulk_setup)

        assert job.results[0].status == "rejected"
        assert job.results[0].grade == "v@8"
        assert _written_cells(bulk_setup["worksheet"]) == [("D4", "v")]

    def test_missing_github_column_fails_the_job(self, bulk_setup):
        bulk_setup["grid"][0][2] = "Гитхаб"
        job = _job()
        _run(job, bulk_setup)

        assert job.status == "failed"
        assert "GitHub" in job.error
        assert job.finished_at

    def test_missing_lab_column_fails_the_job(self, bulk_setup):
        bulk_setup["lab_config"]["short-name"] = "ЛР9"
        job = _job()
        _run(job, bulk_setup)

        assert job.status == "failed"
        assert "ЛР9" in job.error

    def test_lab_without_short_name_falls_back_to_column_offset(self, bulk_setup):
        del bulk_setup["lab_config"]["short-name"]
        bulk_setup["course_info"]["google"]["lab-column-offset"] = 3
        job = _job()
        _run(job, bulk_setup)

        # offset 3 + lab number 1 = column 4 = D
        assert _written_cells(bulk_setup["worksheet"]) == [("D3", "v"), ("D4", "v")]

    def test_error_on_one_student_does_not_stop_the_run(self, bulk_setup):
        grader = _passing_grader()
        grader.check_repository.side_effect = [
            _grade_result(GradeStatus.ERROR, message="Нет коммитов", error_code="NO_COMMITS"),
            None,
        ]
        job = _job()
        _run(job, bulk_setup, grader=grader)

        assert job.status == "done"
        assert [r.status for r in job.results] == ["error", "updated"]
        assert _written_cells(bulk_setup["worksheet"]) == [("D4", "v")]

    def test_unexpected_exception_is_reported_per_student(self, bulk_setup):
        grader = _passing_grader()
        grader.check_repository.side_effect = [RuntimeError("boom"), None]
        job = _job()
        _run(job, bulk_setup, grader=grader)

        assert job.status == "done"
        assert job.results[0].status == "error"
        assert "boom" in job.results[0].message

    def test_cancellation_stops_and_flushes(self, bulk_setup):
        job = _job()
        job.cancel_requested = True
        _run(job, bulk_setup)

        assert job.status == "cancelled"
        assert job.processed == 0
        bulk_setup["worksheet"].batch_update.assert_not_called()

    def test_deadline_and_penalty_come_from_the_grid(self, bulk_setup):
        bulk_setup["grid"][0][3] = "15.03.2025"  # deadline sits above the lab header
        bulk_setup["lab_config"]["penalty-max"] = 9
        bulk_setup["course_info"]["timezone"] = "UTC+3"

        from datetime import timezone as tz
        late = datetime(2025, 3, 30, 12, 0, tzinfo=tz(timedelta(hours=3)))
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.UPDATED, "v", "ok"),
            ci_passed=True,
            latest_success_time=late,
        )
        job = _job()
        _run(job, bulk_setup, grader=_grader_mock(ci))

        assert job.results[0].grade == "v-3"


class TestRunBulkGradingByFile:
    """Mode with a name file: discover repos and match students by name."""

    def _github_client(self, repo_names, files):
        client = MagicMock()
        client.list_org_repos.return_value = [{"name": name} for name in repo_names]
        client.get_file_content.side_effect = lambda org, repo, path: files.get(repo)
        return client

    def test_registers_username_and_grades(self, bulk_setup):
        client = self._github_client(
            ["os-task1-carol"], {"os-task1-carol": "Сидоров Сидор\nЛР1"}
        )
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.status == "done"
        assert job.results[0].status == "updated"
        assert job.results[0].registered is True
        written = _written_cells(bulk_setup["worksheet"])
        assert ("C5", "carol") in written
        assert ("D5", "v") in written

    def test_existing_matching_username_is_not_rewritten(self, bulk_setup):
        client = self._github_client(["os-task1-alice"], {"os-task1-alice": "Иванов Иван"})
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.results[0].registered is False
        assert _written_cells(bulk_setup["worksheet"]) == [("D3", "v")]

    def test_conflicting_username_is_reported_and_not_graded(self, bulk_setup):
        """The row already names a different account, so nothing is touched."""
        client = self._github_client(["os-task1-mallory"], {"os-task1-mallory": "Иванов Иван"})
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.results[0].status == "conflict"
        assert job.total == 1 and job.processed == 1
        bulk_setup["worksheet"].batch_update.assert_not_called()

    def test_unknown_name_is_reported(self, bulk_setup):
        client = self._github_client(["os-task1-dave"], {"os-task1-dave": "Неизвестный Студент"})
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.results[0].status == "unmatched"
        assert "Неизвестный Студент" in job.results[0].message

    def test_missing_name_file_is_reported(self, bulk_setup):
        client = self._github_client(["os-task1-dave"], {})
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.results[0].status == "unmatched"
        assert "info.md" in job.results[0].message

    def test_repos_of_other_labs_are_ignored(self, bulk_setup):
        client = self._github_client(
            ["os-task1-alice", "os-task10-bob", "unrelated"],
            {"os-task1-alice": "Иванов Иван"},
        )
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.total == 1
        assert [r.github for r in job.results] == ["alice"]

    def test_unavailable_org_repos_fail_the_job(self, bulk_setup):
        client = MagicMock()
        client.list_org_repos.return_value = None
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.status == "failed"
        assert "репозиториев организации" in job.error

    def test_dry_run_does_not_register_usernames(self, bulk_setup):
        client = self._github_client(["os-task1-carol"], {"os-task1-carol": "Сидоров Сидор"})
        job = _job(mode="by_file", dry_run=True, name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.results[0].registered is True
        bulk_setup["worksheet"].batch_update.assert_not_called()

    def test_ambiguous_name_is_reported(self, bulk_setup):
        bulk_setup["grid"][4][1] = "Иванов Иван"  # duplicate name
        client = self._github_client(["os-task1-dave"], {"os-task1-dave": "Иванов Иван"})
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.results[0].status == "ambiguous"

    def test_planning_failures_count_toward_progress(self, bulk_setup):
        client = self._github_client(
            ["os-task1-alice", "os-task1-dave"],
            {"os-task1-alice": "Иванов Иван", "os-task1-dave": "Кто-то Другой"},
        )
        job = _job(mode="by_file", name_file="info.md")
        _run(job, bulk_setup, github_client=client)

        assert job.total == 2
        assert job.processed == 2


class TestRunBulkGradingTeamLab:
    """A team lab grades one repository per team (§10.3 of the team plan)."""

    @pytest.fixture(autouse=True)
    def clean_teams_state(self):
        from grading.teams import reset_teams_state

        reset_teams_state()
        yield
        reset_teams_state()

    def _team_setup(self, bulk_setup, size_max=None):
        bulk_setup["lab_config"]["team"] = {"size-max": size_max} if size_max else {}
        return bulk_setup

    def _github_client(self, teams):
        """teams: {slug: (description, [members])}"""
        client = MagicMock()
        prefix = "os-task1"
        client.list_org_repos.return_value = [
            {"name": f"{prefix}-{slug}", "description": description}
            for slug, (description, _members) in teams.items()
        ]
        rosters = {
            f"{prefix}-{slug}": [
                {"login": login, "permissions": {"push": True, "admin": False}}
                for login in members
            ]
            for slug, (_description, members) in teams.items()
        }
        client.list_collaborators.side_effect = lambda org, repo, affiliation="direct": (
            rosters.get(repo, [])
        )
        client.list_invitations.side_effect = lambda org, repo: []
        return client

    def test_repository_is_evaluated_once_per_team(self, bulk_setup):
        """alice and bob share team-1, so the repo is graded once, not twice."""
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": ("Пингвины", ["alice", "bob"])})
        grader = _passing_grader()
        job = _job()
        _run(job, setup, grader=grader, github_client=client)

        assert job.status == "done"
        assert grader.check_repository.call_count == 1
        assert grader._evaluate_ci_internal.call_count == 1

    def test_the_team_repository_is_the_one_graded(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": ("Пингвины", ["alice", "bob"])})
        grader = _passing_grader()
        job = _job()
        _run(job, setup, grader=grader, github_client=client)

        org, repo, _config = grader.check_repository.call_args.args
        assert repo == "os-task1-team-1"
        assert all(result.repo == "os-task1-team-1" for result in job.results)

    def test_the_grade_reaches_every_member(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": ("Пингвины", ["alice", "bob"])})
        job = _job()
        _run(job, setup, github_client=client)

        assert [r.status for r in job.results] == ["updated", "updated"]
        assert _written_cells(setup["worksheet"]) == [("D3", "v"), ("D4", "v")]
        assert [r.team for r in job.results] == ["Пингвины", "Пингвины"]

    def test_cell_protection_is_applied_per_member(self, bulk_setup):
        """Alice already has a grade; bob still gets his."""
        setup = self._team_setup(bulk_setup)
        setup["grid"][2][3] = "v@8"
        client = self._github_client({"team-1": ("Пингвины", ["alice", "bob"])})
        job = _job()
        _run(job, setup, github_client=client)

        assert job.results[0].status == "rejected"
        assert job.results[0].grade == "v@8"
        assert job.results[1].status == "updated"
        assert _written_cells(setup["worksheet"]) == [("D4", "v")]

    def test_student_without_a_team_is_reported(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": ("Пингвины", ["alice"])})
        grader = _passing_grader()
        job = _job()
        _run(job, setup, grader=grader, github_client=client)

        by_github = {r.github: r for r in job.results}
        assert by_github["bob"].status == "no_team"
        assert by_github["alice"].status == "updated"
        assert grader.check_repository.call_count == 1
        assert job.total == 2 and job.processed == 2

    def test_unreadable_roster_fails_the_run_instead_of_reporting_no_team(self, bulk_setup):
        """
        Regression: a team whose roster GitHub would not return used to be
        skipped silently, and its members were reported as never having joined
        a team - a false statement the teacher had no way to spot.
        """
        setup = self._team_setup(bulk_setup)
        client = self._github_client({
            "team-1": ("Пингвины", ["alice"]),
            "team-2": ("Тюлени", ["bob"]),
        })
        client.list_collaborators.side_effect = lambda org, repo, affiliation="direct": (
            None if repo == "os-task1-team-2" else
            [{"login": "alice", "permissions": {"push": True, "admin": False}}]
        )
        grader = _passing_grader()
        job = _job()
        _run(job, setup, grader=grader, github_client=client)

        assert job.status == "failed"
        assert "team-2" in job.error
        assert [r.status for r in job.results] == []
        assert grader.check_repository.call_count == 0

    def test_two_teams_are_graded_separately(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        client = self._github_client({
            "team-1": ("Пингвины", ["alice"]),
            "team-2": ("Тюлени", ["bob"]),
        })
        grader = _passing_grader()
        job = _job()
        _run(job, setup, grader=grader, github_client=client)

        assert grader.check_repository.call_count == 2
        assert {r.team for r in job.results} == {"Пингвины", "Тюлени"}

    def test_ci_error_is_copied_to_every_member(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": ("Пингвины", ["alice", "bob"])})
        grader = _passing_grader()
        grader.check_repository.return_value = _grade_result(
            GradeStatus.ERROR, message="Нет коммитов", error_code="NO_COMMITS"
        )
        job = _job()
        _run(job, setup, grader=grader, github_client=client)

        assert [r.status for r in job.results] == ["error", "error"]
        assert all("Нет коммитов" in r.message for r in job.results)
        setup["worksheet"].batch_update.assert_not_called()

    def test_pending_ci_is_copied_to_every_member(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": ("Пингвины", ["alice", "bob"])})
        ci = CIEvaluation(
            grade_result=_grade_result(GradeStatus.PENDING, message="CI ещё выполняется ⏳"),
            ci_passed=False,
        )
        job = _job()
        _run(job, setup, grader=_grader_mock(ci), github_client=client)

        assert [r.status for r in job.results] == ["pending", "pending"]

    def test_taskid_is_never_checked_for_a_team(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        setup["course_info"]["google"]["task-id-column"] = 0
        setup["lab_config"]["taskid-max"] = 20
        client = self._github_client({"team-1": ("Пингвины", ["alice", "bob"])})
        grader = _passing_grader()
        job = _job()
        _run(job, setup, grader=grader, github_client=client)

        grader.check_taskid.assert_not_called()
        assert [r.status for r in job.results] == ["updated", "updated"]

    def test_unavailable_teams_fail_the_job(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        client = MagicMock()
        client.list_org_repos.return_value = None
        job = _job()
        _run(job, setup, github_client=client)

        assert job.status == "failed"
        assert "команд" in job.error

    def test_by_file_mode_is_refused(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        job = _job(mode="by_file", name_file="info.md")
        _run(job, setup, github_client=MagicMock())

        assert job.status == "failed"
        assert "файлу с ФИО" in job.error

    def test_dry_run_writes_nothing(self, bulk_setup):
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": ("Пингвины", ["alice", "bob"])})
        job = _job(dry_run=True)
        _run(job, setup, github_client=client)

        assert [r.status for r in job.results] == ["updated", "updated"]
        setup["worksheet"].batch_update.assert_not_called()

    def test_pending_invitee_is_graded_too(self, bulk_setup):
        """A place is occupied by an invitation, and so is the grade row."""
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": ("Пингвины", ["alice"])})
        client.list_invitations.side_effect = lambda org, repo: (
            [{"id": 1, "invitee": {"login": "bob"}}] if repo == "os-task1-team-1" else []
        )
        job = _job()
        _run(job, setup, github_client=client)

        assert [r.status for r in job.results] == ["updated", "updated"]

    def test_slugless_team_falls_back_to_the_slug_as_a_name(self, bulk_setup):
        """A repository whose description was cleared still reports a team."""
        setup = self._team_setup(bulk_setup)
        client = self._github_client({"team-1": (None, ["alice", "bob"])})
        job = _job()
        _run(job, setup, github_client=client)

        assert [r.team for r in job.results] == ["team-1", "team-1"]
