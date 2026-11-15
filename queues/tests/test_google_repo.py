import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock

import gspread
import pytest

from queues.domain.lesson import Lesson
from queues.domain.student import Student
from queues.repositories.storage.google import GoogleRepo
from queues.repositories.storage.google.rows import HEADERS, snapshot_rows


@pytest.fixture
def lesson():
    return Lesson(starts_at=datetime(2026, 11, 9, 10, tzinfo=timezone.utc),
                  ends_at=datetime(2026, 11, 9, 11, tzinfo=timezone.utc),
                  groups=("4431",), kind="ЛР", room="1")


@pytest.fixture
def storage():
    client = Mock()
    spreadsheet = client.open_by_key.return_value
    sheet = spreadsheet.worksheet.return_value
    sheet.get_all_values.return_value = []
    sheet.id, sheet.row_count, sheet.col_count = 7, 1000, 5
    return GoogleRepo(lambda: client, "test-sheet"), client, spreadsheet, sheet


def save(repo, lesson, name="ОС"):
    return asyncio.run(repo.save(lesson, course_name=name))


def update(spreadsheet):
    requests = spreadsheet.batch_update.call_args.args[0]["requests"]
    return next(r["updateCells"] for r in requests if "updateCells" in r)


def test_readable_block_and_literal_strings(storage, lesson):
    repo, client, spreadsheet, sheet = storage
    lesson.queue.closed = False
    first = lesson.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1")
    lesson.queue.add_entry(Student("Пётр", "Петров", "", "petr"), "2", "=ЛР2")
    assert save(repo, lesson)
    spreadsheet.worksheet.assert_called_once_with("09.11.2026–15.11.2026")
    rows = update(spreadsheet)["rows"]
    values = [[next(iter(cell["userEnteredValue"].values())) for cell in row["values"]] for row in rows]
    assert values[0] == ["Занятие: 09.11.2026 · 13:00–14:00"]
    assert values[3] == HEADERS
    assert values[4][1:3] == ["Иванов Иван", "ivan"]
    assert values[5][3] == "=ЛР2"
    assert rows[5]["values"][3]["userEnteredValue"] == {"stringValue": "=ЛР2"}
    assert str(lesson.id) not in str(values)
    assert str(first.id) not in str(values)
    assert len(lesson.queue.entries) == 2


def test_create_course_sheet_and_empty_queue(storage, lesson):
    repo, _, spreadsheet, sheet = storage
    spreadsheet.worksheet.side_effect = gspread.WorksheetNotFound("missing")
    spreadsheet.add_worksheet.return_value = sheet
    sheet.get_all_values.return_value = [[]]
    assert save(repo, lesson, "Операционные системы")
    spreadsheet.add_worksheet.assert_called_once_with(title="09.11.2026–15.11.2026", rows=1000, cols=5)
    assert len(update(spreadsheet)["rows"]) == 5
    assert snapshot_rows(lesson, course_name="ОС")[-1][1] == "Очередь пустая"


def test_retry_and_room_change_do_not_duplicate(storage, lesson):
    repo, _, spreadsheet, sheet = storage
    spreadsheet.batch_update.side_effect = TimeoutError("response lost")
    with pytest.raises(TimeoutError):
        save(repo, lesson)
    sheet.get_all_values.return_value = snapshot_rows(lesson, course_name="ОС")
    lesson.room = "2"
    assert not save(repo, lesson)
    spreadsheet.batch_update.assert_called_once()


def test_new_lesson_has_two_blank_rows_before_it(storage, lesson):
    repo, _, spreadsheet, sheet = storage
    old = snapshot_rows(lesson, course_name="ОС")
    sheet.get_all_values.return_value = old
    lesson.starts_at += timedelta(days=1)
    lesson.ends_at += timedelta(days=1)
    assert save(repo, lesson)
    assert update(spreadsheet)["start"]["rowIndex"] == len(old) + 2
    assert sum("mergeCells" in r for r in spreadsheet.batch_update.call_args.args[0]["requests"]) == 3


def test_foreign_sheet_content_is_not_overwritten(storage, lesson):
    repo, _, spreadsheet, sheet = storage
    sheet.get_all_values.return_value = [["lesson_id", "course_name"]]
    with pytest.raises(ValueError, match="другого формата"):
        save(repo, lesson)
    spreadsheet.batch_update.assert_not_called()


def test_older_lesson_is_inserted_before_newer_block(storage, lesson):
    repo, _, spreadsheet, sheet = storage
    from copy import deepcopy
    later = deepcopy(lesson)
    later.starts_at += timedelta(days=1)
    later.ends_at += timedelta(days=1)
    sheet.get_all_values.return_value = snapshot_rows(later, course_name="ОС")
    assert save(repo, lesson)
    requests = spreadsheet.batch_update.call_args.args[0]["requests"]
    assert requests[0]["insertDimension"]["range"]["startIndex"] == 0
    assert requests[0]["insertDimension"]["range"]["endIndex"] == 7
    assert update(spreadsheet)["start"]["rowIndex"] == 0


def test_courses_do_not_mix_in_same_document(storage, lesson):
    repo, _, spreadsheet, sheet = storage
    sheet.get_all_values.return_value = snapshot_rows(lesson, course_name="Сети")
    with pytest.raises(ValueError, match="отдельная таблица"):
        save(repo, lesson)
    spreadsheet.batch_update.assert_not_called()


def test_week_title_uses_moscow_date(storage, lesson):
    repo, _, spreadsheet, sheet = storage
    lesson.starts_at = datetime(2026, 11, 8, 22, tzinfo=timezone.utc)
    lesson.ends_at = lesson.starts_at + timedelta(hours=1)
    assert save(repo, lesson)
    spreadsheet.worksheet.assert_called_once_with("09.11.2026–15.11.2026")
