"""Queue eligibility through the shared grading decision."""

from __future__ import annotations

import re
from typing import Any, Callable

from grading.bulk import StudentOutcome, evaluate_student
from grading.grader import LabGrader

from .errors import QueueError
from .sheets_context import QueueSheetContext

_PASSING_GRADE_RE = re.compile(
    r"v(?:-\d+|@\d+(?:[.,]\d+)?(?:-\d+)?)?",
    re.IGNORECASE,
)


def is_passing_grade(value: str | None) -> bool:
    """Whether a cell value represents a CI-passed (but not defended) grade."""
    return bool(value and _PASSING_GRADE_RE.fullmatch(value.strip()))


def _is_already_defended(cell_value: str) -> bool:
    """``vv`` (case-insensitive) means the lab was already defended."""
    return (cell_value or "").strip().lower().startswith("vv")


def ensure_ci_passed(
    grader: LabGrader,
    org: str,
    username: str,
    lab_config: dict[str, Any],
    course_info: dict[str, Any],
    context_loader: Callable[[], QueueSheetContext],
    repo_name: str | None = None,
    history_worksheet: Any | None = None,
    group: str = "",
) -> tuple[QueueSheetContext, StudentOutcome]:
    """Run the same grade decision used by ``/grade`` and enforce CI pass.

    The verdict comes from ``outcome.passed``, not from the cell value or
    the outcome status — a ``rejected`` outcome (cell protected) may still
    carry ``passed == True`` when the student already has ``v`` from a
    previous run and CI is green on the current HEAD.

    Before calling ``evaluate_student`` the function checks two early-exit
    conditions that do not require a GitHub round-trip:

    * ``vv`` in the grade cell → ``ALREADY_PASSED``.
    * ``сдал`` in the queue history → ``ALREADY_PASSED``.

    Raises ``QueueError`` with a stable code on any non-passing outcome.
    """
    target = context_loader()

    if _is_already_defended(target.current_cell_value):
        raise QueueError("ALREADY_PASSED", "Лабораторная уже сдана")

    if history_worksheet is not None and group:
        from .store import STATUS_PASSED, find_in_history

        lab_short_name = str(lab_config.get("short-name", ""))
        if find_in_history(history_worksheet, group, target.fio, lab_short_name, STATUS_PASSED):
            raise QueueError("ALREADY_PASSED", "Лабораторная уже сдана")

    outcome = evaluate_student(
        grader,
        org,
        username,
        lab_config,
        course_info,
        lambda: target.grade_context,
        repo_name=repo_name,
    )

    if outcome.status == "pending":
        raise QueueError("CI_PENDING", outcome.message)

    if outcome.status == "error":
        raise QueueError("CI_CHECK_FAILED", outcome.message)

    if outcome.status == "rejected":
        if outcome.passed:
            return target, outcome
        raise QueueError("CI_NOT_PASSED", outcome.message or "CI не пройден")

    if outcome.status != "updated" or outcome.cell_value is None:
        raise QueueError(
            "CI_CHECK_FAILED",
            outcome.message or "Неизвестный результат проверки",
        )

    try:
        target.worksheet.update_cell(
            target.row_index,
            target.lab_column,
            outcome.cell_value,
        )
    except Exception as error:
        raise QueueError(
            "SHEETS_UNAVAILABLE",
            "Не удалось записать результат проверки",
        ) from error

    if not outcome.passed:
        raise QueueError("CI_NOT_PASSED", outcome.message)

    return target, outcome
