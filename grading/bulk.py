"""
Bulk grading of a whole group's lab submissions from the admin panel.

Two parts live here.

`evaluate_student` is the grading decision for one student, factored out of
the HTTP endpoint so that the single-student endpoint (`grade_lab`) and the
bulk run share one implementation and cannot drift apart. It performs the
GitHub and CI work itself but never touches Google Sheets: everything it
needs from the spreadsheet arrives through a `SheetContext` produced by a
caller-supplied provider, invoked lazily - only once CI evaluation has
produced a result worth writing. That laziness is what lets `grade_lab` keep
returning repository and CI errors without opening a Sheets connection.

The rest is the background job that walks a group, mirroring the job
machinery of `propagate.py`: an in-memory store guarded by a lock, one
running job per (course, group, lab), and a poll endpoint. Job state is
deliberately not persisted - the backend runs as a single uvicorn worker, so
a restart loses the report but not the work: grades are flushed to the sheet
in batches as the run proceeds.
"""
import logging
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .github_client import GitHubClient
from .grader import LabGrader, GradeStatus
from .penalty import calculate_penalty, format_grade_with_penalty, PenaltyStrategy
from .score import format_grade_with_score, format_score
from .sheets_client import (
    calculate_lab_column,
    can_overwrite_cell,
    cell_from_grid,
    column_values_from_grid,
    find_lab_column_in_grid,
    get_decimal_separator,
    get_deadline_from_grid,
    get_student_order_from_grid,
)
from .taskid import calculate_expected_taskid

logger = logging.getLogger(__name__)

CELL_PROTECTED_MESSAGE = (
    "⚠️ Работа уже была проверена ранее. Обратитесь к преподавателю для пересдачи."
)

# How many finished jobs to keep around for GET /admin/bulk-grade-jobs/{id}.
MAX_JOBS_KEPT = 20

# Cells buffered before a batch write. Flushing as the run proceeds means a
# crash, a restart or a cancellation keeps the grades already decided.
WRITE_BATCH_SIZE = 10

# Two header rows precede student data in every group sheet.
FIRST_DATA_ROW = 3


class BulkGradingError(Exception):
    """
    Raised when a step shared by the whole run fails before any per-student
    work can happen (spreadsheet columns missing, org repos unavailable).
    Distinct from a per-student error, which never aborts the whole job.
    """
    pass


@dataclass
class SheetContext:
    """Everything `evaluate_student` needs from the spreadsheet."""
    current_cell_value: str = ""
    student_order: int | None = None
    deadline: datetime | None = None
    decimal_separator: str = "."


@dataclass
class StudentOutcome:
    """Outcome of grading one student."""
    status: str  # updated | rejected | pending | error
    cell_value: str | None = None  # Value to write: "v", "x", "v-3", "v@10,5-3"
    message: str = ""
    passed: str | None = None  # "3/4 тестов пройдено"
    checks: list[str] = field(default_factory=list)
    score: str | None = None  # Formatted score, if extracted
    current_grade: str | None = None  # Existing cell value when rejected
    error_code: str | None = None  # For programmatic error handling


def taskid_column(
    course_info: dict[str, Any],
    lab_config: dict[str, Any],
) -> int | None:
    """
    Resolve the 1-based column holding student order numbers for TASKID checks.

    Args:
        course_info: Course configuration dict from YAML
        lab_config: Lab configuration dict from YAML

    Returns:
        1-based column number, or None when the TASKID check does not apply to
        this lab (no `task-id-column`, no `taskid-max`, or `ignore-task-id`)
    """
    column = course_info.get("google", {}).get("task-id-column")
    if column is None:
        return None

    if lab_config.get("taskid-max") is None:
        return None

    if lab_config.get("ignore-task-id", False):
        return None

    # 0-based in config, 1-based for gspread
    return column + 1


def repo_name_for(lab_config: dict[str, Any], username: str) -> str:
    """
    Build a student's repository name by the project's naming convention.

    Args:
        lab_config: Lab configuration dict from YAML
        username: Student's GitHub username

    Returns:
        Repository name, e.g. "os-task2-student1"
    """
    return f"{lab_config.get('github-prefix', '')}-{username}"


def evaluate_student(
    grader: LabGrader,
    org: str,
    username: str,
    lab_config: dict[str, Any],
    course_info: dict[str, Any],
    sheet_context: Callable[[], SheetContext],
) -> StudentOutcome:
    """
    Grade one student's repository.

    Steps:
    1. Repository checks (required files, workflows, commits)
    2. Forbidden file modifications
    3. CI evaluation and score extraction
    4. TASKID validation (if configured)
    5. Penalty calculation (if a deadline is set)
    6. Grade formatting and cell protection check

    Args:
        grader: Configured LabGrader
        org: GitHub organization
        username: Student's GitHub username
        lab_config: Lab configuration dict from YAML
        course_info: Course configuration dict from YAML
        sheet_context: Callable returning the spreadsheet context. Invoked at
            most once, and only after CI evaluation succeeds, so callers may
            defer opening a Sheets connection until then.

    Returns:
        StudentOutcome. For status "updated", `cell_value` is what should be
        written to the grade cell; the caller performs the write.
    """
    repo_name = repo_name_for(lab_config, username)
    logger.info(f"Evaluating repository: {org}/{repo_name}")

    # Step 1: Repository checks (required files, workflows, commits)
    repo_error = grader.check_repository(org, repo_name, lab_config)
    if repo_error:
        logger.warning(f"Repository check failed: {repo_error.message}")
        return StudentOutcome(
            status="error",
            message=repo_error.message,
            error_code=repo_error.error_code,
        )

    # Step 2: Forbidden file modifications
    forbidden_error = grader.check_forbidden_files(org, repo_name, lab_config)
    if forbidden_error:
        logger.warning(f"Forbidden modification: {forbidden_error.message}")
        return StudentOutcome(
            status="error",
            message=forbidden_error.message,
            error_code=forbidden_error.error_code,
        )

    # Step 3: CI evaluation
    ci_evaluation = grader._evaluate_ci_internal(org, repo_name, lab_config)

    if ci_evaluation.grade_result.status == GradeStatus.ERROR:
        logger.warning(f"CI error: {ci_evaluation.grade_result.message}")
        return StudentOutcome(
            status="error",
            message=ci_evaluation.grade_result.message,
            error_code=ci_evaluation.grade_result.error_code,
        )

    if ci_evaluation.grade_result.status == GradeStatus.PENDING:
        logger.info(f"CI pending: {ci_evaluation.grade_result.message}")
        return StudentOutcome(
            status="pending",
            message=ci_evaluation.grade_result.message,
            passed=ci_evaluation.grade_result.passed,
            checks=ci_evaluation.grade_result.checks,
        )

    # CI evaluation is complete - the spreadsheet is needed from here on
    context = sheet_context()

    final_result = ci_evaluation.grade_result.result  # "v" or "x"
    final_message = ci_evaluation.grade_result.message
    score_value = ci_evaluation.score
    decimal_separator = context.decimal_separator

    # Steps 4-5: additional checks only make sense when CI passed
    if ci_evaluation.ci_passed:
        if taskid_column(course_info, lab_config) is not None and context.student_order is not None:
            taskid_shift = lab_config.get("taskid-shift", 0)
            taskid_max = lab_config.get("taskid-max")
            expected_taskid = calculate_expected_taskid(
                context.student_order, taskid_shift, taskid_max
            )
            logger.info(
                f"Expected TASKID: {expected_taskid} "
                f"(order={context.student_order}, shift={taskid_shift}, max={taskid_max})"
            )

            taskid_error = grader.check_taskid(
                org, repo_name,
                ci_evaluation.successful_runs,
                expected_taskid,
            )
            if taskid_error:
                logger.warning(f"TASKID error: {taskid_error.message}")
                return StudentOutcome(
                    status="error",
                    message=taskid_error.message,
                    error_code=taskid_error.error_code,
                )

        penalty = 0
        if context.deadline and ci_evaluation.latest_success_time:
            penalty_max = lab_config.get("penalty-max", 0)
            strategy_name = lab_config.get("penalty-strategy", "weekly")
            try:
                strategy = PenaltyStrategy(strategy_name)
            except ValueError:
                strategy = PenaltyStrategy.WEEKLY

            penalty = calculate_penalty(
                completed_at=ci_evaluation.latest_success_time,
                deadline=context.deadline,
                penalty_max=penalty_max,
                strategy=strategy,
            )

            if penalty > 0:
                logger.info(f"Calculated penalty: {penalty}")

        # Step 6: format the grade with score and penalty
        if score_value is not None:
            final_result = format_grade_with_score(
                "v", score_value, penalty, decimal_separator
            )
            logger.info(f"Formatted grade with score: {final_result}")

            formatted_score = format_score(score_value, decimal_separator)
            if penalty > 0:
                final_message = (
                    f"Результат CI: ✅ Все проверки пройдены "
                    f"(Баллы: {formatted_score}, штраф: -{penalty})"
                )
            else:
                final_message = (
                    f"Результат CI: ✅ Все проверки пройдены (Баллы: {formatted_score})"
                )
        elif penalty > 0:
            final_result = format_grade_with_penalty("v", penalty)
            final_message = f"Результат CI: ✅ Все проверки пройдены (штраф: -{penalty})"
            logger.info(f"Applied penalty {penalty} for late submission: {final_result}")

    formatted_score = (
        format_score(score_value, decimal_separator) if score_value is not None else None
    )

    # Cell protection
    if not can_overwrite_cell(context.current_cell_value):
        logger.warning(
            f"Update rejected: cell already contains '{context.current_cell_value}'"
        )
        return StudentOutcome(
            status="rejected",
            cell_value=context.current_cell_value,
            message=CELL_PROTECTED_MESSAGE,
            passed=ci_evaluation.grade_result.passed,
            checks=ci_evaluation.grade_result.checks,
            score=formatted_score,
            current_grade=context.current_cell_value,
        )

    return StudentOutcome(
        status="updated",
        cell_value=final_result,
        message=final_message,
        passed=ci_evaluation.grade_result.passed,
        checks=ci_evaluation.grade_result.checks,
        score=formatted_score,
    )


# ---------------------------------------------------------------------------
# Repository discovery and student matching ("by file" mode)
# ---------------------------------------------------------------------------


def filter_lab_repos(repo_names: list[str], prefix: str) -> dict[str, str]:
    """
    Select the repositories belonging to one lab and extract usernames.

    The dash after the prefix is required, so the prefix "os-task1" does not
    swallow "os-task10-student1".

    Args:
        repo_names: All repository names in the organization
        prefix: Lab's github-prefix

    Returns:
        Mapping of GitHub username -> repository name

    Examples:
        >>> filter_lab_repos(["os-task1-alice", "os-task10-bob"], "os-task1")
        {'alice': 'os-task1-alice'}
        >>> filter_lab_repos(["os-task1-jane-doe"], "os-task1")
        {'jane-doe': 'os-task1-jane-doe'}
    """
    if not prefix:
        return {}

    matched: dict[str, str] = {}
    marker = f"{prefix}-"

    for name in repo_names:
        if not name.startswith(marker):
            continue

        username = name[len(marker):]
        if not username:
            continue

        matched[username] = name

    return matched


def extract_full_name(file_content: str | None) -> str | None:
    """
    Extract a student's full name from the contents of their name file.

    The name is the first non-empty line of the file.

    Args:
        file_content: Decoded file content, or None if the file is unavailable

    Returns:
        The name with surrounding whitespace stripped, or None if the file is
        empty or holds only blank lines

    Examples:
        >>> extract_full_name("Иванов Иван Иванович\\nЛР1\\n")
        'Иванов Иван Иванович'
        >>> extract_full_name("\\r\\n  Петров Пётр  \\r\\n")
        'Петров Пётр'
        >>> extract_full_name("   ") is None
        True
    """
    if not file_content:
        return None

    for line in file_content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped

    return None


def normalize_full_name(name: str | None) -> str:
    """
    Normalize a full name for comparison.

    Students type their name by hand into the name file, so comparison ignores
    letter case, collapses whitespace runs (including non-breaking spaces) and
    treats "ё" as "е".

    Args:
        name: Raw name string

    Returns:
        Normalized name, or "" for an empty input

    Examples:
        >>> normalize_full_name("Иванов  Иван\\tИванович")
        'иванов иван иванович'
        >>> normalize_full_name("Алёшин Алексей") == normalize_full_name("Алешин Алексей")
        True
    """
    if not name:
        return ""

    collapsed = " ".join(name.replace(" ", " ").split())
    return collapsed.casefold().replace("ё", "е")


class NameMatchError(Exception):
    """Raised when a full name cannot be resolved to exactly one sheet row."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def find_row_by_full_name(
    student_names: list[str],
    full_name: str,
    start_row: int = FIRST_DATA_ROW,
) -> int:
    """
    Find the spreadsheet row for a student by full name.

    Matching is exact after normalization (see normalize_full_name). Fuzzy
    matching is deliberately not attempted: a wrong match writes a grade
    against the wrong student.

    Args:
        student_names: Values of the student name column, starting at start_row
        full_name: Name read from the repository's name file
        start_row: 1-based row number of the first entry in student_names

    Returns:
        1-based row number

    Raises:
        NameMatchError: code "unmatched" when no row matches, "ambiguous" when
            several rows carry the same name
    """
    target = normalize_full_name(full_name)
    if not target:
        raise NameMatchError("unmatched", "ФИО не найдено в файле")

    matches = [
        start_row + idx
        for idx, value in enumerate(student_names)
        if normalize_full_name(value) == target
    ]

    if not matches:
        raise NameMatchError("unmatched", f"ФИО «{full_name}» не найдено в таблице")

    if len(matches) > 1:
        raise NameMatchError(
            "ambiguous",
            f"ФИО «{full_name}» встречается в таблице несколько раз "
            f"(строки {', '.join(str(row) for row in matches)})",
        )

    return matches[0]


def resolve_github_cell(existing: str, username: str) -> tuple[bool, str | None]:
    """
    Decide what to do with a student's GitHub cell in "by file" mode.

    Args:
        existing: Current cell value
        username: Username taken from the repository name

    Returns:
        (should_write, conflict_message). `should_write` is True when the cell
        is empty and the username has to be recorded. When the cell holds a
        different username, `conflict_message` explains the conflict and the
        student must not be graded under either name.

    Examples:
        >>> resolve_github_cell("", "alice")
        (True, None)
        >>> resolve_github_cell("Alice", "alice")
        (False, None)
    """
    current = (existing or "").strip()

    if not current:
        return True, None

    if current.casefold() == username.casefold():
        return False, None

    return False, (
        f"В таблице указан другой аккаунт GitHub: «{current}», "
        f"репозиторий принадлежит «{username}»"
    )


# ---------------------------------------------------------------------------
# Background job: store, planning and execution
# ---------------------------------------------------------------------------


@dataclass
class BulkResult:
    """Outcome for a single student, as shown in the run report."""
    status: str  # updated | rejected | pending | error | conflict | unmatched | ambiguous
    student_name: str | None = None
    github: str | None = None
    repo: str | None = None
    grade: str | None = None
    message: str = ""
    registered: bool = False  # GitHub username was written to the sheet

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "student_name": self.student_name,
            "github": self.github,
            "repo": self.repo,
            "grade": self.grade,
            "message": self.message,
            "registered": self.registered,
        }


@dataclass
class BulkJob:
    """State of one background bulk grading run."""
    job_id: str
    course_id: str
    group_id: str
    lab_id: str
    mode: str            # by_sheet | by_file
    dry_run: bool = False
    name_file: str | None = None
    status: str = "running"  # running | done | failed | cancelled
    started_at: str = ""
    finished_at: str | None = None
    total: int = 0
    processed: int = 0
    results: list[BulkResult] = field(default_factory=list)
    error: str | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1

        return {
            "job_id": self.job_id,
            "course_id": self.course_id,
            "group_id": self.group_id,
            "lab_id": self.lab_id,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "name_file": self.name_file,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "processed": self.processed,
            "counts": counts,
            "results": [r.to_dict() for r in self.results],
            "error": self.error,
        }


# Module-level job store, same shape and rationale as propagate.py: guarded by
# a lock because BackgroundTasks run in FastAPI's threadpool, and safe as a
# plain dict only as long as the backend stays a single uvicorn worker.
_jobs: "OrderedDict[str, BulkJob]" = OrderedDict()
_running_keys: set[tuple[str, str, str]] = set()
_jobs_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_bulk_job(job_id: str) -> BulkJob | None:
    """Look up a job by id (used by GET /admin/bulk-grade-jobs/{job_id})."""
    with _jobs_lock:
        return _jobs.get(job_id)


def _evict_old_jobs_locked() -> None:
    """Keep at most MAX_JOBS_KEPT jobs, oldest first. Must hold _jobs_lock."""
    if len(_jobs) <= MAX_JOBS_KEPT:
        return
    for job_id in list(_jobs.keys()):
        if len(_jobs) <= MAX_JOBS_KEPT:
            break
        # Never evict a running job - it would leave _running_keys pointing at
        # a job GET can no longer find.
        if _jobs[job_id].status == "running":
            continue
        del _jobs[job_id]


def try_start_bulk_job(
    course_id: str,
    group_id: str,
    lab_id: str,
    mode: str,
    dry_run: bool,
    name_file: str | None,
) -> BulkJob | None:
    """
    Atomically create and register a running job for (course, group, lab),
    unless one is already running for that same triple.

    Returns:
        The new BulkJob, or None if a job for this group and lab is already
        running (caller should respond HTTP 409).
    """
    with _jobs_lock:
        if (course_id, group_id, lab_id) in _running_keys:
            return None
        job = BulkJob(
            job_id=uuid.uuid4().hex,
            course_id=course_id,
            group_id=group_id,
            lab_id=lab_id,
            mode=mode,
            dry_run=dry_run,
            name_file=name_file,
            started_at=_now(),
        )
        _jobs[job.job_id] = job
        _running_keys.add((course_id, group_id, lab_id))
        _evict_old_jobs_locked()
        return job


def request_bulk_job_cancel(job_id: str) -> BulkJob | None:
    """
    Ask a running job to stop after the student it is currently on.

    Returns:
        The job, or None if there is no job with this id.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job and job.status == "running":
            job.cancel_requested = True
        return job


def _finish_job_locked(job: BulkJob, status: str, error: str | None = None) -> None:
    job.status = status
    job.error = error
    job.finished_at = _now()
    _running_keys.discard((job.course_id, job.group_id, job.lab_id))


@dataclass
class _Target:
    """A student queued for grading."""
    row: int
    username: str
    student_name: str | None
    repo: str
    registered: bool = False  # Username was queued for writing into the sheet


def _plan_by_file(
    job: BulkJob,
    github_client: GitHubClient,
    org: str,
    lab_config: dict[str, Any],
    name_file: str,
    values: list[list[str]],
    student_col: int,
    github_col: int,
) -> tuple[list[_Target], list[tuple[int, int, str]]]:
    """
    Discover repositories and map them to spreadsheet rows via the name file.

    Rows that cannot be resolved are appended to `job.results` here - they are
    finished work, not something to retry per student.

    Returns:
        (targets, github_writes), where github_writes are (row, col, value)
        triples recording newly resolved GitHub usernames

    Raises:
        BulkGradingError: the organization's repositories are unavailable
    """
    prefix = lab_config.get("github-prefix", "")

    org_repos = github_client.list_org_repos(org)
    if org_repos is None:
        raise BulkGradingError("Не удалось получить список репозиториев организации")

    repos = filter_lab_repos([repo.get("name", "") for repo in org_repos], prefix)
    logger.info(f"Found {len(repos)} repositories with prefix '{prefix}' in {org}")

    student_names = column_values_from_grid(values, student_col, start_row=FIRST_DATA_ROW)

    targets: list[_Target] = []
    github_writes: list[tuple[int, int, str]] = []

    for username, repo in sorted(repos.items(), key=lambda pair: pair[0].casefold()):
        full_name = extract_full_name(
            github_client.get_file_content(org, repo, name_file)
        )
        if full_name is None:
            job.results.append(BulkResult(
                status="unmatched",
                github=username,
                repo=repo,
                message=f"Файл {name_file} не найден, пуст или не читается как текст",
            ))
            continue

        try:
            row = find_row_by_full_name(student_names, full_name, FIRST_DATA_ROW)
        except NameMatchError as e:
            job.results.append(BulkResult(
                status=e.code,
                student_name=full_name,
                github=username,
                repo=repo,
                message=e.message,
            ))
            continue

        should_write, conflict = resolve_github_cell(
            cell_from_grid(values, row, github_col), username
        )
        if conflict:
            job.results.append(BulkResult(
                status="conflict",
                student_name=full_name,
                github=username,
                repo=repo,
                message=conflict,
            ))
            continue

        if should_write:
            github_writes.append((row, github_col, username))

        targets.append(_Target(
            row=row,
            username=username,
            student_name=cell_from_grid(values, row, student_col) or full_name,
            repo=repo,
            registered=should_write,
        ))

    return targets, github_writes


def _plan_by_sheet(
    values: list[list[str]],
    student_col: int,
    github_col: int,
    lab_config: dict[str, Any],
) -> list[_Target]:
    """Queue every student who already has a GitHub username in the sheet."""
    targets: list[_Target] = []

    github_values = column_values_from_grid(values, github_col, start_row=FIRST_DATA_ROW)
    for idx, value in enumerate(github_values):
        username = (value or "").strip()
        if not username:
            continue

        row = FIRST_DATA_ROW + idx
        targets.append(_Target(
            row=row,
            username=username,
            student_name=cell_from_grid(values, row, student_col) or None,
            repo=repo_name_for(lab_config, username),
        ))

    return targets


def run_bulk_grading(
    job: BulkJob,
    grader: LabGrader,
    github_client: GitHubClient,
    worksheet,
    spreadsheet,
    course_info: dict[str, Any],
    lab_config: dict[str, Any],
    lab_number: int,
) -> None:
    """
    Execute a bulk grading job, updating `job` in place as it goes.

    Reads the whole worksheet once and resolves rows, columns, deadline and
    task IDs in memory. The per-cell helpers spend ~6 Sheets API requests per
    student, which for a group of 30 exceeds the 60 reads/minute quota.

    Students are processed sequentially: GitHub applies secondary rate limits
    to bursts of parallel requests from one token, and the run is backgrounded
    anyway.

    Args:
        job: Job to run and report progress into
        grader: Configured LabGrader
        github_client: Configured GitHubClient
        worksheet: gspread Worksheet for the group
        spreadsheet: gspread Spreadsheet (used for the locale)
        course_info: Course configuration dict from YAML
        lab_config: Lab configuration dict from YAML
        lab_number: Lab number from its config key, for the lab-column fallback
    """
    from gspread.utils import rowcol_to_a1

    org = course_info.get("github", {}).get("organization")
    pending: list[tuple[int, int, str]] = []

    def flush() -> None:
        """Write buffered cells to the spreadsheet in one API call."""
        if job.dry_run or not pending:
            pending.clear()
            return

        worksheet.batch_update([
            {"range": rowcol_to_a1(row, col), "values": [[value]]}
            for row, col, value in pending
        ])
        logger.info(f"Bulk job {job.job_id}: flushed {len(pending)} cell(s)")
        pending.clear()

    try:
        values = worksheet.get_all_values()
        decimal_separator = get_decimal_separator(spreadsheet)

        header_row = values[0] if values else []
        if "GitHub" not in header_row:
            raise BulkGradingError("Столбец 'GitHub' не найден в таблице")
        github_col = header_row.index("GitHub") + 1

        student_col = course_info.get("google", {}).get("student-name-column", 1) + 1

        lab_short_name = lab_config.get("short-name")
        if lab_short_name:
            lab_col = find_lab_column_in_grid(values, lab_short_name)
            if not lab_col:
                raise BulkGradingError(f"Столбец '{lab_short_name}' не найден в таблице")
        else:
            # Same fallback as grade_lab when a lab has no short-name.
            lab_offset = course_info.get("google", {}).get("lab-column-offset", 1)
            lab_col = calculate_lab_column(lab_number, lab_offset)

        deadline = get_deadline_from_grid(
            values, lab_col, deadline_row=1, timezone_str=course_info.get("timezone")
        )
        task_id_col = taskid_column(course_info, lab_config)

        if job.mode == "by_file":
            targets, github_writes = _plan_by_file(
                job, github_client, org, lab_config, job.name_file,
                values, student_col, github_col,
            )
            pending.extend(github_writes)
        else:
            targets = _plan_by_sheet(values, student_col, github_col, lab_config)

        # Rows rejected while planning are already done; count them as processed
        job.total = len(targets) + len(job.results)
        job.processed = len(job.results)
        logger.info(
            f"Bulk job {job.job_id}: {len(targets)} student(s) to grade, "
            f"{len(job.results)} rejected while planning"
        )

        cancelled = False
        for target in targets:
            if job.cancel_requested:
                logger.info(f"Bulk job {job.job_id}: cancellation requested")
                cancelled = True
                break

            def context_for(target=target) -> SheetContext:
                return SheetContext(
                    current_cell_value=cell_from_grid(values, target.row, lab_col),
                    student_order=(
                        get_student_order_from_grid(values, target.row, task_id_col)
                        if task_id_col is not None else None
                    ),
                    deadline=deadline,
                    decimal_separator=decimal_separator,
                )

            try:
                outcome = evaluate_student(
                    grader, org, target.username, lab_config, course_info, context_for,
                )
                result = BulkResult(
                    status=outcome.status,
                    student_name=target.student_name,
                    github=target.username,
                    repo=target.repo,
                    grade=outcome.cell_value if outcome.status in ("updated", "rejected") else None,
                    message=outcome.message,
                    registered=target.registered,
                )
                if outcome.status == "updated":
                    pending.append((target.row, lab_col, outcome.cell_value))
            except Exception as e:
                logger.exception(f"Bulk job {job.job_id}: error grading {target.username}")
                result = BulkResult(
                    status="error",
                    student_name=target.student_name,
                    github=target.username,
                    repo=target.repo,
                    message=f"Внутренняя ошибка при проверке: {e}",
                    registered=target.registered,
                )

            job.results.append(result)
            job.processed += 1

            if len(pending) >= WRITE_BATCH_SIZE:
                flush()

        flush()

        with _jobs_lock:
            _finish_job_locked(job, "cancelled" if cancelled else "done")

    except Exception as e:
        logger.exception(f"Bulk job {job.job_id} failed: {e}")
        try:
            flush()
        except Exception:
            logger.exception(f"Bulk job {job.job_id}: could not flush pending writes")
        with _jobs_lock:
            _finish_job_locked(job, "failed", str(e))
    finally:
        logger.info(
            f"Bulk job {job.job_id} finished with status '{job.status}': "
            f"{job.processed}/{job.total} processed"
        )
