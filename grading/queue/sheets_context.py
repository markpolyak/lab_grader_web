"""Read-only student/lab context from an existing group worksheet."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from grading import SheetContext, get_decimal_separator, taskid_column
from grading.schedule.manual import _parse_date
from grading.sheets_client import parse_deadline

from .errors import QueueError


@dataclass(frozen=True)
class QueueSheetContext:
    spreadsheet: Any
    worksheet: Any
    grade_context: SheetContext
    row_index: int
    lab_column: int
    fio: str
    current_cell_value: str


def _cell(grid: list[list[str]], row_index: int, column_index: int) -> str:
    if row_index < 0 or row_index >= len(grid):
        return ""
    row = grid[row_index]
    return row[column_index] if 0 <= column_index < len(row) else ""


def load_sheet_context(
    sheets_client_factory: Callable[[], Any],
    course_info: dict[str, Any],
    group_id: str,
    lab_config: dict[str, Any],
    username: str,
) -> QueueSheetContext:
    """Resolve one participant and lab cell without writing to the group sheet."""
    google_config = course_info.get("google", {})
    spreadsheet_id = google_config.get("spreadsheet")
    if not spreadsheet_id:
        raise QueueError("SHEETS_UNAVAILABLE", "В конфиге курса нет Google Sheets ID")

    try:
        spreadsheet = sheets_client_factory().open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(str(group_id))
        grid = worksheet.get_all_values()
    except Exception as error:
        raise QueueError("SHEETS_UNAVAILABLE", "Не удалось открыть таблицу группы") from error

    header = grid[0] if grid else []
    try:
        github_column = header.index("GitHub")
    except ValueError as error:
        raise QueueError("SHEETS_UNAVAILABLE", "В листе группы нет колонки GitHub") from error

    username_key = (username or "").strip().casefold()
    matching_rows = [
        index for index, row in enumerate(grid[2:], start=2)
        if _cell(grid, index, github_column).strip().casefold() == username_key
    ]
    if not matching_rows:
        raise QueueError("NOT_IN_GROUP", "GitHub-логин не найден в выбранной группе")
    if len(matching_rows) > 1:
        raise QueueError("AMBIGUOUS_STUDENT", "GitHub-логин найден в нескольких строках группы")

    row_offset = matching_rows[0]
    row_index = row_offset + 1
    name_column = google_config.get("student-name-column", 1)
    if isinstance(name_column, bool) or not isinstance(name_column, int) or name_column < 0:
        raise QueueError("SHEETS_UNAVAILABLE", "Некорректная student-name-column")
    fio = _cell(grid, row_offset, name_column).strip()
    if not fio:
        raise QueueError("NOT_IN_GROUP", "Для студента не заполнено ФИО в листе группы")

    lab_short_name = str(lab_config.get("short-name", "")).strip()
    if not lab_short_name:
        raise QueueError("SHEETS_UNAVAILABLE", "У лабораторной не задан short-name")
    lab_header = grid[1] if len(grid) > 1 else []
    try:
        lab_column = lab_header.index(lab_short_name) + 1
    except ValueError as error:
        raise QueueError(
            "SHEETS_UNAVAILABLE",
            f"В листе группы нет колонки {lab_short_name!r}",
        ) from error

    current_cell_value = _cell(grid, row_offset, lab_column - 1).strip()
    task_column = taskid_column(course_info, lab_config)
    student_order = None
    if task_column is not None:
        raw_order = _cell(grid, row_offset, task_column - 1).strip()
        try:
            student_order = int(raw_order) if raw_order else None
        except ValueError:
            student_order = None

    deadline_value = _cell(grid, 0, lab_column - 1)
    deadline = parse_deadline(deadline_value, course_info.get("timezone"))
    return QueueSheetContext(
        spreadsheet=spreadsheet,
        worksheet=worksheet,
        grade_context=SheetContext(
            current_cell_value=current_cell_value,
            student_order=student_order,
            deadline=deadline,
            decimal_separator=get_decimal_separator(spreadsheet),
        ),
        row_index=row_index,
        lab_column=lab_column,
        fio=fio,
        current_cell_value=current_cell_value,
    )
