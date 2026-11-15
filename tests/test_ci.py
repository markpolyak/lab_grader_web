from types import SimpleNamespace

import pytest

from grading.bulk import StudentOutcome
from grading.queue import ci
from grading.queue.errors import QueueError
from grading.queue.sheets_context import QueueSheetContext


@pytest.fixture
def queue_context():
    writes = []
    return QueueSheetContext(
        spreadsheet=object(),
        worksheet=SimpleNamespace(update_cell=lambda *args: writes.append(args)),
        grade_context=object(),
        row_index=4,
        lab_column=3,
        fio="Иванов Иван",
        current_cell_value="",
    ), writes


@pytest.mark.parametrize(
    "value",
    ["v", "v-3", "v@10.5", "v@10,5", "v@10,5-3"],
)
def test_passing_grade_formats(value):
    assert ci.is_passing_grade(value)


@pytest.mark.parametrize("value", [None, "x", "vv", "v?", "passed"])
def test_non_passing_grade_formats(value):
    assert not ci.is_passing_grade(value)


def test_ensure_ci_passed_updated_passing_returns_without_writing_grade_cell(
    monkeypatch, queue_context
):
    context, writes = queue_context
    outcome = StudentOutcome(
        status="updated", cell_value="v@8,5", message="passed", ci_passed=True, passed="CI пройден"
    )
    calls = {}

    def fake_evaluate(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        assert args[5]() is context.grade_context
        return outcome

    monkeypatch.setattr(ci, "evaluate_student", fake_evaluate)
    returned_context, returned_outcome = ci.ensure_ci_passed(
        grader=object(),
        org="org",
        username="student",
        lab_config={"short-name": "ЛР1"},
        course_info={},
        context_loader=lambda: context,
        repo_name="team-repo",
    )

    assert returned_context is context
    assert returned_outcome is outcome
    assert writes == []
    assert calls["kwargs"]["repo_name"] == "team-repo"


def test_ensure_ci_passed_failed_ci_writes_nothing_to_grade_cell(
    monkeypatch, queue_context
):
    context, writes = queue_context
    monkeypatch.setattr(
        ci,
        "evaluate_student",
        lambda *_args, **_kwargs: StudentOutcome(
            status="updated", cell_value="x", message="failed", ci_passed=False
        ),
    )

    with pytest.raises(QueueError, match="CI_NOT_PASSED"):
        ci.ensure_ci_passed(
            object(), "org", "student", {}, {}, lambda: context
        )
    assert writes == []


def test_ensure_ci_passed_never_modifies_existing_grade_cell(
    monkeypatch, queue_context
):
    """Regression for review defect #2: queue registration must not touch the
    teacher's evaluation cell — even when CI passes and ``/grade`` would have
    written ``v``. The reviewer's repro: cell contains ``?``, CI green,
    POST /queue/register → the cell must stay ``?``.
    """
    context, writes = queue_context
    context_with_question_mark = QueueSheetContext(
        spreadsheet=context.spreadsheet,
        worksheet=context.worksheet,
        grade_context=context.grade_context,
        row_index=context.row_index,
        lab_column=context.lab_column,
        fio=context.fio,
        current_cell_value="?",
    )
    monkeypatch.setattr(
        ci,
        "evaluate_student",
        lambda *_args, **_kwargs: StudentOutcome(
            status="updated", cell_value="v", message="CI пройден", ci_passed=True
        ),
    )

    returned_context, returned_outcome = ci.ensure_ci_passed(
        object(), "org", "student", {}, {}, lambda: context_with_question_mark
    )
    assert returned_outcome.ci_passed is True
    assert writes == []


def test_ensure_ci_passed_does_not_write_pending_or_error(monkeypatch, queue_context):
    context, writes = queue_context
    for status, code in (("pending", "CI_PENDING"), ("error", "CI_CHECK_FAILED")):
        monkeypatch.setattr(
            ci,
            "evaluate_student",
            lambda *_args, _status=status, **_kwargs: StudentOutcome(
                status=_status, cell_value=None, message=_status, ci_passed=False
            ),
        )
        with pytest.raises(QueueError, match=code):
            ci.ensure_ci_passed(
                object(), "org", "student", {}, {}, lambda: context
            )
    assert writes == []


def test_ensure_ci_passed_rejected_with_passed_allows_registration(
    monkeypatch, queue_context
):
    """A rejected outcome (cell protected) with ci_passed=True should not block."""
    context, writes = queue_context
    monkeypatch.setattr(
        ci,
        "evaluate_student",
        lambda *_args, **_kwargs: StudentOutcome(
            status="rejected", cell_value="v", message="cell protected", ci_passed=True
        ),
    )

    returned_context, returned_outcome = ci.ensure_ci_passed(
        object(), "org", "student", {}, {}, lambda: context
    )
    assert returned_outcome.ci_passed is True
    assert writes == []


def test_ensure_ci_passed_rejected_without_passed_raises_ci_not_passed(
    monkeypatch, queue_context
):
    context, writes = queue_context
    monkeypatch.setattr(
        ci,
        "evaluate_student",
        lambda *_args, **_kwargs: StudentOutcome(
            status="rejected", cell_value="v", message="CI failed", ci_passed=False
        ),
    )

    with pytest.raises(QueueError, match="CI_NOT_PASSED"):
        ci.ensure_ci_passed(
            object(), "org", "student", {}, {}, lambda: context
        )
    assert writes == []


def test_ensure_ci_passed_blocks_when_cell_has_vv(queue_context):
    context, _writes = queue_context
    context_with_vv = QueueSheetContext(
        spreadsheet=context.spreadsheet,
        worksheet=context.worksheet,
        grade_context=context.grade_context,
        row_index=context.row_index,
        lab_column=context.lab_column,
        fio=context.fio,
        current_cell_value="vv@10",
    )

    with pytest.raises(QueueError, match="ALREADY_PASSED"):
        ci.ensure_ci_passed(
            object(), "org", "student", {}, {}, lambda: context_with_vv
        )


def test_ensure_ci_passed_rejected_with_truthy_passed_string_but_ci_passed_false_blocks(
    monkeypatch, queue_context
):
    """Regression for review defect #1: ``StudentOutcome.passed`` is a summary
    string (e.g. "0/4 тестов пройдено"), not a bool. A failed CI may still
    produce a non-empty (truthy) string; the verdict must come from
    ``ci_passed`` (bool), not from truthiness of ``passed``.
    """
    context, writes = queue_context
    monkeypatch.setattr(
        ci,
        "evaluate_student",
        lambda *_args, **_kwargs: StudentOutcome(
            status="rejected",
            cell_value="v",
            message="cell protected",
            passed="0/4 тестов пройдено",  # truthy string, but CI failed
            ci_passed=False,
        ),
    )
    with pytest.raises(QueueError, match="CI_NOT_PASSED"):
        ci.ensure_ci_passed(
            object(), "org", "student", {}, {}, lambda: context
        )
    assert writes == []
