from datetime import date

import pytest
from gspread.exceptions import WorksheetNotFound

from grading.queue.errors import QueueError
from grading.queue.store import (
    HISTORY_HEADERS,
    QUEUE_HEADERS,
    STATUS_ABSENT,
    STATUS_FAILED,
    STATUS_NOT_REACHED,
    STATUS_PASSED,
    STATUS_WAITING,
    STATUS_WITHDRAWN,
    ensure_history_sheet,
    ensure_today_sheet,
    find_in_history,
    find_past_queue_sheets,
    read_queue_rows,
    register,
    set_status,
    shift_down,
    snapshot_sheet,
    withdraw,
)
from grading.queue.locks import reset_locks


class FakeWorksheet:
    def __init__(self, title, sheet_id, rows=()):
        self.title = title
        self.id = sheet_id
        self.values = [list(row) for row in rows]

    def row_values(self, row):
        return self.values[row - 1] if len(self.values) >= row else []

    def get_all_values(self):
        return [list(row) for row in self.values]

    def update_cell(self, row, column, value):
        while len(self.values) < row:
            self.values.append([])
        while len(self.values[row - 1]) < column:
            self.values[row - 1].append("")
        self.values[row - 1][column - 1] = str(value)

    def append_row(self, values, **_kwargs):
        self.values.append([str(value) for value in values])

    def batch_update(self, updates, **_kwargs):
        for update in updates:
            cell_range = update["range"]
            column = ord(cell_range[0].upper()) - ord("A") + 1
            row = int(cell_range[1:])
            self.update_cell(row, column, update["values"][0][0])


class FakeWorksheetNotFound(WorksheetNotFound):
    pass


class FakeSpreadsheet:
    def __init__(self):
        self.id = "spreadsheet"
        self.sheets = {}
        self.next_id = 1
        self.metadata_requests = []

    def worksheet(self, title):
        if title not in self.sheets:
            raise FakeWorksheetNotFound(title)
        return self.sheets[title]

    def worksheets(self):
        return list(self.sheets.values())

    def add_worksheet(self, title, rows, cols):
        sheet = FakeWorksheet(title, self.next_id)
        self.next_id += 1
        self.sheets[title] = sheet
        return sheet

    def batch_update(self, body):
        self.metadata_requests.extend(body["requests"])
        for request in body["requests"]:
            delete = request.get("deleteSheet")
            if delete:
                title = next(
                    key for key, sheet in self.sheets.items()
                    if sheet.id == delete["sheetId"]
                )
                del self.sheets[title]
            append = request.get("appendCells")
            if append:
                target = next(
                    sheet for sheet in self.sheets.values()
                    if sheet.id == append["sheetId"]
                )
                for row in append["rows"]:
                    values = [
                        cell["userEnteredValue"]["stringValue"]
                        for cell in row["values"]
                    ]
                    target.append_row(values)


@pytest.fixture(autouse=True)
def reset_state():
    reset_locks()


def _add_queue(spreadsheet, entries, title="Очередь-2026-11-08"):
    """Create a queue sheet pre-filled with rows.

    Each entry is ``(group, fio, lab, status, position)``; Пар№ is left
    empty so that the 6-column layout matches QUEUE_HEADERS.
    """
    sheet = FakeWorksheet(title, spreadsheet.next_id, [QUEUE_HEADERS])
    spreadsheet.next_id += 1
    spreadsheet.sheets[sheet.title] = sheet
    for group, fio, lab, status, position in entries:
        sheet.append_row([position, group, fio, lab, "", status])
    return sheet


def test_ensure_sheets_uses_exact_headers_and_protects_data():
    spreadsheet = FakeSpreadsheet()
    active = ensure_today_sheet(spreadsheet, date(2026, 11, 8), ["4431"])
    history = ensure_history_sheet(spreadsheet)

    assert active.row_values(1) == QUEUE_HEADERS
    assert history.row_values(1) == HISTORY_HEADERS
    requests = spreadsheet.metadata_requests
    assert any("addProtectedRange" in item for item in requests)
    assert sum("addFilterView" in item for item in requests) == 10


def test_register_appends_waiting_row_and_blocks_duplicate_waiting_fio():
    spreadsheet = FakeSpreadsheet()

    assert register(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", "ЛР1") == 1
    assert register(spreadsheet, date(2026, 11, 8), "4431", "Петров Пётр", "ЛР1") == 2
    with pytest.raises(QueueError, match="ALREADY_IN_QUEUE"):
        register(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", "ЛР1")

    sheet = spreadsheet.sheets["Очередь-2026-11-08"]
    assert sheet.row_values(1) == QUEUE_HEADERS
    assert sheet.row_values(2) == ["1", "4431", "Иванов Иван", "ЛР1", "", STATUS_WAITING]


def test_shift_down_only_for_waiting_entries_and_clamps_at_end():
    spreadsheet = FakeSpreadsheet()
    sheet = _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
            ("4431", "Петров", "ЛР1", STATUS_WAITING, 2),
            ("4431", "Сидоров", "ЛР1", STATUS_WAITING, 3),
        ],
    )

    assert shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов", "ЛР1", 1) == (1, 2)
    rows = read_queue_rows(sheet)
    assert [(row.fio, row.position) for row in rows] == [
        ("Иванов", 2),
        ("Петров", 1),
        ("Сидоров", 3),
    ]
    assert shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов", "ЛР1", 99) == (2, 3)
    with pytest.raises(QueueError, match="INVALID_SHIFT"):
        shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов", "ЛР1", 1)
    with pytest.raises(QueueError, match="INVALID_SHIFT"):
        shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов", "ЛР1", 0)


def test_withdraw_and_set_status_leave_history_rows_and_renumber_waiting():
    spreadsheet = FakeSpreadsheet()
    sheet = _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
            ("4431", "Петров", "ЛР1", STATUS_WAITING, 2),
            ("4431", "Сидоров", "ЛР1", STATUS_WAITING, 3),
        ],
    )

    withdraw(spreadsheet, date(2026, 11, 8), "4431", "Иванов", "ЛР1")
    set_status(spreadsheet, date(2026, 11, 8), "4431", "Петров", "ЛР1", STATUS_PASSED)
    rows = read_queue_rows(sheet)

    assert [(row.fio, row.position, row.status) for row in rows] == [
        ("Иванов", 1, STATUS_WITHDRAWN),
        ("Петров", 1, STATUS_PASSED),
        ("Сидоров", 1, STATUS_WAITING),
    ]
    assert rows[0].position == 1
    assert rows[1].position == 1


def test_status_validation_and_waiting_only_transitions():
    spreadsheet = FakeSpreadsheet()
    _add_queue(spreadsheet, [("4431", "Иванов", "ЛР1", STATUS_ABSENT, 1)])

    with pytest.raises(QueueError, match="NOT_WAITING"):
        set_status(spreadsheet, date(2026, 11, 8), "4431", "Иванов", "ЛР1", STATUS_FAILED)
    with pytest.raises(QueueError, match="INVALID_STATUS"):
        set_status(spreadsheet, date(2026, 11, 8), "4431", "Иванов", "ЛР1", STATUS_WAITING)


def test_snapshot_writes_history_and_deletes_daily_tab_atomically():
    spreadsheet = FakeSpreadsheet()
    active = _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
            ("4431", "Петров", "ЛР1", STATUS_FAILED, 2),
        ],
    )
    active.title = "Очередь-2026-11-07"
    spreadsheet.sheets = {active.title: active}
    history = ensure_history_sheet(spreadsheet)
    spreadsheet.metadata_requests.clear()

    assert snapshot_sheet(spreadsheet, active.title) == 2
    requests = spreadsheet.metadata_requests
    assert len(requests) == 2
    assert "appendCells" in requests[0]
    assert "deleteSheet" in requests[1]
    assert active.title not in spreadsheet.sheets
    append_rows = requests[0]["appendCells"]["rows"]
    assert append_rows[0]["values"][-1]["userEnteredValue"]["stringValue"] == STATUS_NOT_REACHED
    assert append_rows[1]["values"][-1]["userEnteredValue"]["stringValue"] == STATUS_FAILED
    assert history.row_values(1) == HISTORY_HEADERS


def test_snapshot_is_idempotent_and_skips_already_moved_rows():
    spreadsheet = FakeSpreadsheet()
    active = _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
        ],
        title="Очередь-2026-11-07",
    )
    history = ensure_history_sheet(spreadsheet)
    spreadsheet.metadata_requests.clear()

    # First snapshot moves the row
    assert snapshot_sheet(spreadsheet, "Очередь-2026-11-07") == 1

    # Recreate the sheet (simulating a partial failure: rows in history but sheet not deleted)
    active2 = _add_queue(
        spreadsheet,
        [("4431", "Иванов", "ЛР1", STATUS_WAITING, 1)],
        title="Очередь-2026-11-07",
    )
    spreadsheet.metadata_requests.clear()

    # Second snapshot should find the row already in history and skip it
    assert snapshot_sheet(spreadsheet, "Очередь-2026-11-07") == 0
    # Sheet is still deleted even though no new rows were appended
    assert "Очередь-2026-11-07" not in spreadsheet.sheets


def test_snapshot_preserves_repeated_registrations_and_is_idempotent():
    spreadsheet = FakeSpreadsheet()
    _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WITHDRAWN, 1),
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
        ],
        title="Очередь-2026-11-07",
    )
    history = ensure_history_sheet(spreadsheet)

    assert snapshot_sheet(spreadsheet, "Очередь-2026-11-07") == 2
    assert [
        row[6] for row in history.get_all_values()[1:]
    ] == [STATUS_WITHDRAWN, STATUS_NOT_REACHED]

    _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WITHDRAWN, 1),
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
        ],
        title="Очередь-2026-11-07",
    )
    assert snapshot_sheet(spreadsheet, "Очередь-2026-11-07") == 0
    assert len(history.get_all_values()) == 3

    partially_copied = FakeSpreadsheet()
    _add_queue(
        partially_copied,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WITHDRAWN, 1),
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
        ],
        title="Очередь-2026-11-07",
    )
    partial_history = ensure_history_sheet(partially_copied)
    partial_history.append_row(
        ["2026-11-07", "1", "4431", "Иванов", "ЛР1", "", STATUS_NOT_REACHED]
    )

    assert snapshot_sheet(partially_copied, "Очередь-2026-11-07") == 1
    assert len(partial_history.get_all_values()) == 3


def test_snapshot_ignores_non_queue_tabs_and_future_dates():
    spreadsheet = FakeSpreadsheet()
    spreadsheet.sheets["Info"] = FakeWorksheet("Info", 1)
    spreadsheet.sheets["Очередь-2026-11-08"] = FakeWorksheet("Очередь-2026-11-08", 2)
    spreadsheet.sheets["Очередь-not-a-date"] = FakeWorksheet("Очередь-not-a-date", 3)

    assert find_past_queue_sheets(spreadsheet, date(2026, 11, 8)) == []
    assert find_past_queue_sheets(spreadsheet, date(2026, 11, 9)) == ["Очередь-2026-11-08"]


def test_find_in_history_checks_correct_columns():
    spreadsheet = FakeSpreadsheet()
    history = FakeWorksheet(HISTORY_SHEET := "История_очереди", 1, [HISTORY_HEADERS])
    history.append_row(["2026-11-07", "1", "4431", "Иванов", "ЛР1", "", STATUS_PASSED])
    history.append_row(["2026-11-07", "2", "4431", "Петров", "ЛР1", "", STATUS_FAILED])
    spreadsheet.sheets[HISTORY_SHEET] = history

    assert find_in_history(history, "4431", "Иванов", "ЛР1", STATUS_PASSED) is True
    assert find_in_history(history, "4431", "Иванов", "ЛР1", STATUS_FAILED) is False
    assert find_in_history(history, "4431", "Петров", "ЛР1", STATUS_FAILED) is True
    assert find_in_history(history, "4432", "Иванов", "ЛР1", STATUS_PASSED) is False
