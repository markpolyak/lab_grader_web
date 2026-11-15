from datetime import date

import pytest
from gspread.exceptions import WorksheetNotFound

from grading.queue.errors import QueueError
from grading.queue.store import (
    HISTORY_HEADERS,
    QUEUE_HEADERS,
    QUEUE_SHEET_ROWS,
    QUEUE_STATUS_CHOICES,
    STATUS_ABSENT,
    STATUS_BG_COLORS,
    STATUS_CALLED,
    STATUS_COL_INDEX,
    STATUS_FAILED,
    STATUS_NOT_REACHED,
    STATUS_PASSED,
    STATUS_WAITING,
    STATUS_WITHDRAWN,
    ensure_history_sheet,
    ensure_today_sheet,
    ensure_today_sheet as store_ensure_today_sheet,
    find_in_history,
    find_past_queue_sheets,
    read_queue_rows,
    register,
    set_status,
    shift_down,
    snapshot_sheet,
    withdraw,
    call_next,
    uncall,
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
        # real gspread writes the whole values grid starting at the range
        for update in updates:
            cell_range = update["range"]
            column = ord(cell_range[0].upper()) - ord("A") + 1
            row = int(cell_range[1:])
            for i, row_vals in enumerate(update["values"]):
                for j, value in enumerate(row_vals):
                    self.update_cell(row + i, column + j, value)


class FakeWorksheetNotFound(WorksheetNotFound):
    pass


class FakeSpreadsheet:
    def __init__(self):
        self.id = "spreadsheet"
        self.sheets = {}
        self.next_id = 1
        self.metadata_requests = []
        self.created_sheets = []

    def worksheet(self, title):
        if title not in self.sheets:
            raise FakeWorksheetNotFound(title)
        return self.sheets[title]

    def worksheets(self):
        return list(self.sheets.values())

    def add_worksheet(self, title, rows, cols):
        self.created_sheets.append((title, rows, cols))
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
    from grading.queue.cache import reset_queue_cache
    reset_queue_cache()


def _add_queue(spreadsheet, entries, title="Очередь-2026-11-08"):
    """Create a queue sheet pre-filled with rows.

    Each entry is ``(group, fio, lab, status, position)``; Пар№, GitHub and
    Время are left empty so that the 8-column layout matches QUEUE_HEADERS.
    """
    sheet = FakeWorksheet(title, spreadsheet.next_id, [QUEUE_HEADERS])
    spreadsheet.next_id += 1
    spreadsheet.sheets[sheet.title] = sheet
    for group, fio, lab, status, position in entries:
        sheet.append_row([position, group, fio, lab, "", "", "", status])
    return sheet


def test_ensure_sheets_uses_exact_headers_and_protects_data():
    spreadsheet = FakeSpreadsheet()
    active = ensure_today_sheet(spreadsheet, date(2026, 11, 8), ["4431"])
    history = ensure_history_sheet(spreadsheet)

    assert active.row_values(1) == QUEUE_HEADERS
    assert history.row_values(1) == HISTORY_HEADERS
    requests = spreadsheet.metadata_requests
    assert any("addProtectedRange" in item for item in requests)
    # задача #19: filter views заменены нативным автофильтром
    assert sum("addFilterView" in item for item in requests) == 0
    # задача #19: dropdown со всеми статусами, цвет статуса, автофильтр,
    # лист-«таблица», диапазон до 200 строк
    assert ("Очередь-2026-11-08", 200, len(QUEUE_HEADERS)) in spreadsheet.created_sheets
    assert ("История_очереди", 200, len(HISTORY_HEADERS)) in spreadsheet.created_sheets
    validation = next(item["setDataValidation"] for item in requests if "setDataValidation" in item)
    assert validation["range"]["endRowIndex"] == QUEUE_SHEET_ROWS
    dropdown = [v["userEnteredValue"] for v in validation["rule"]["condition"]["values"]]
    assert dropdown == QUEUE_STATUS_CHOICES
    assert "вызван" in dropdown
    format_rules = [item["addConditionalFormatRule"]["rule"] for item in requests if "addConditionalFormatRule" in item]
    assert len(format_rules) == len(STATUS_BG_COLORS)
    rule_ranges = [r["ranges"][0] for r in format_rules]
    assert all(
        rng["startRowIndex"] == 1
        and rng["endRowIndex"] == QUEUE_SHEET_ROWS
        and rng["startColumnIndex"] == STATUS_COL_INDEX
        for rng in rule_ranges
    )
    rule_conditions = [r["booleanRule"]["condition"]["values"][0]["userEnteredValue"] for r in format_rules]
    assert sorted(rule_conditions) == sorted(STATUS_BG_COLORS)
    dims = [item["updateDimensionProperties"] for item in requests if "updateDimensionProperties" in item]
    fio_dim = next(d for d in dims if d["range"]["startIndex"] == QUEUE_HEADERS.index("ФИО"))
    assert fio_dim["range"]["dimension"] == "COLUMNS"
    assert fio_dim["range"]["endIndex"] == QUEUE_HEADERS.index("ФИО") + 1
    assert fio_dim["properties"]["pixelSize"] == 240
    # автофильтр всегда (задача #19, п.3а) — таблицы больше нет
    assert any("setBasicFilter" in item for item in requests)
    assert all("addTable" not in item for item in requests)


def test_register_appends_waiting_row_and_blocks_duplicate_waiting_fio():
    spreadsheet = FakeSpreadsheet()

    assert register(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", "ЛР1", github="ivanov", signup_time="10:30") == 1
    assert register(spreadsheet, date(2026, 11, 8), "4431", "Петров Пётр", "ЛР1") == 2
    with pytest.raises(QueueError, match="ALREADY_IN_QUEUE"):
        register(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", "ЛР1")

    sheet = spreadsheet.sheets["Очередь-2026-11-08"]
    assert sheet.row_values(1) == QUEUE_HEADERS
    assert sheet.row_values(2) == ["1", "4431", "Иванов Иван", "ЛР1", "", "ivanov", "10:30", STATUS_WAITING]


def test_register_numbers_the_pair_queue_across_labs():
    """Issue #62 review: одна пара = одна очередь.

    Лаба — только метка (что сдаёт участник); нумерация общая по
    (группа, пара), записи разных лаб не имеют отдельных очередей.
    Разные пары нумеруются независимо, каждая с 1.
    """
    spreadsheet = FakeSpreadsheet()
    today = date(2026, 11, 8)

    # Пара 2: Новиков (ЛР2), следом Морозова (ЛР1) — сквозная нумерация пары.
    assert register(spreadsheet, today, "4431", "Новиков", "ЛР2", session_no=2) == 1
    assert register(spreadsheet, today, "4431", "Морозова", "ЛР1", session_no=2) == 2
    # Пара 1 нумеруется независимо, снова с 1.
    assert register(spreadsheet, today, "4431", "Сидорова", "ЛР1", session_no=1) == 1

    sheet = spreadsheet.sheets["Очередь-2026-11-08"]
    rows = read_queue_rows(sheet)
    # Постоянная сортировка: после добавления лист лежит в порядке
    # (группа, пара, позиция) — Сидорова (пара 1) поднялась выше пары 2.
    assert [(row.fio, row.lab, row.session_no, row.position) for row in rows] == [
        ("Сидорова", "ЛР1", 1, 1),
        ("Новиков", "ЛР2", 2, 1),
        ("Морозова", "ЛР1", 2, 2),
    ]


def test_register_resorts_disordered_sheet():
    """Постоянная сортировка: физический беспорядок, накопившийся до фикса
    (пара 9 записана раньше пары 1 и лежит выше), исправляется следующей
    регистрацией — лист всегда отсортирован до и после добавления."""
    spreadsheet = FakeSpreadsheet()
    today = date(2026, 11, 8)
    sheet = FakeWorksheet("Очередь-2026-11-08", spreadsheet.next_id, [QUEUE_HEADERS])
    spreadsheet.next_id += 1
    spreadsheet.sheets[sheet.title] = sheet
    sheet.append_row([1, "4432", "Новиков", "ЛР2", 9, "Asteruss", "23:29", STATUS_PASSED])
    sheet.append_row([1, "4432", "Волков", "ЛР2", 1, "Gerasimov", "23:29", STATUS_WITHDRAWN])
    sheet.append_row([3, "4432", "Морозова", "ЛР2", 9, "hikara33", "23:32", STATUS_WAITING])

    # Новая запись в пару 9 (последний тикет пары): сама по себе она порядок
    # не ломает, но лист уже разупорядочен — регистрация его пересортирует.
    assert register(spreadsheet, today, "4432", "Петров", "ЛР2", session_no=9) == 4

    rows = read_queue_rows(sheet)
    assert [(row.fio, row.session_no, row.position) for row in rows] == [
        ("Волков", 1, 1),
        ("Новиков", 9, 1),
        ("Морозова", 9, 3),
        ("Петров", 9, 4),
    ]


def test_call_next_is_pair_scoped_not_lab_scoped():
    """Вызов «следующего» идёт по очереди пары, лаба записи не важна."""
    spreadsheet = FakeSpreadsheet()
    today = date(2026, 11, 8)
    sheet = _add_queue(
        spreadsheet,
        [
            ("4431", "Новиков", "ЛР2", STATUS_WAITING, 1),
            ("4431", "Морозова", "ЛР1", STATUS_WAITING, 2),
        ],
    )

    called = call_next(spreadsheet, today, "4431")
    assert called.fio == "Новиков"

    set_status(spreadsheet, today, "4431", "Новиков", STATUS_PASSED)
    called = call_next(spreadsheet, today, "4431")
    assert called.fio == "Морозова"

    rows = read_queue_rows(sheet)
    statuses = {row.fio: (row.position, row.status) for row in rows}
    assert statuses["Новиков"] == (1, STATUS_PASSED)
    assert statuses["Морозова"] == (2, STATUS_CALLED)


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

    assert shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов", 1) == (1, 2)
    rows = read_queue_rows(sheet)
    # Лист пересортирован (постоянная сортировка): тикеты по порядку.
    assert [(row.fio, row.position) for row in rows] == [
        ("Петров", 1),
        ("Иванов", 2),
        ("Сидоров", 3),
    ]
    assert shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов", 99) == (2, 3)
    with pytest.raises(QueueError, match="INVALID_SHIFT"):
        shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов", 1)
    with pytest.raises(QueueError, match="INVALID_SHIFT"):
        shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов", 0)


def test_shift_down_rotates_tickets_within_segment_only():
    """Сдвиг переставляет билеты только внутри задетого сегмента ждущих:
    вызванные/завершённые строки сохраняют свои номера, дублей не возникает,
    дырки от отозванных не схлопываются."""
    spreadsheet = FakeSpreadsheet()
    today = date(2026, 11, 8)
    sheet = _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_CALLED, 1),
            ("4431", "Петров", "ЛР1", STATUS_WAITING, 2),
            ("4431", "Сидоров", "ЛР1", STATUS_WAITING, 3),
            ("4431", "Кузнецов", "ЛР1", STATUS_WAITING, 5),  # дырка: №4 отозван
        ],
    )

    old, new = shift_down(spreadsheet, today, "4431", "Петров", 2)
    assert (old, new) == (1, 3)

    rows = read_queue_rows(sheet)
    # Лист пересортирован (постоянная сортировка) в порядке тикетов пары:
    # Иванов(1, вызван) -> Сидоров(2) -> Кузнецов(3) -> Петров(5).
    assert [(row.fio, row.position, row.status) for row in rows] == [
        ("Иванов", 1, STATUS_CALLED),      # билет вызванного не тронут
        ("Сидоров", 2, STATUS_WAITING),    # занял билет Петрова
        ("Кузнецов", 3, STATUS_WAITING),   # занял билет Сидорова
        ("Петров", 5, STATUS_WAITING),     # взял последний билет сегмента
    ]


def test_withdraw_and_set_status_preserve_positions():
    spreadsheet = FakeSpreadsheet()
    sheet = _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
            ("4431", "Петров", "ЛР1", STATUS_WAITING, 2),
            ("4431", "Сидоров", "ЛР1", STATUS_WAITING, 3),
        ],
    )

    withdraw(spreadsheet, date(2026, 11, 8), "4431", "Иванов")
    set_status(spreadsheet, date(2026, 11, 8), "4431", "Петров", STATUS_PASSED)
    rows = read_queue_rows(sheet)

    # Позиции — билеты: ничто не перенумеровывается автоматически,
    # меняются только явными действиями (запись, возврат в конец, сдвиг).
    assert [(row.fio, row.position, row.status) for row in rows] == [
        ("Иванов", 1, STATUS_WITHDRAWN),
        ("Петров", 2, STATUS_PASSED),
        ("Сидоров", 3, STATUS_WAITING),
    ]


def test_status_validation_and_waiting_only_transitions():
    spreadsheet = FakeSpreadsheet()
    _add_queue(spreadsheet, [("4431", "Иванов", "ЛР1", STATUS_ABSENT, 1)])

    with pytest.raises(QueueError, match="NOT_WAITING"):
        set_status(spreadsheet, date(2026, 11, 8), "4431", "Иванов", STATUS_FAILED)
    with pytest.raises(QueueError, match="INVALID_STATUS"):
        set_status(spreadsheet, date(2026, 11, 8), "4431", "Иванов", STATUS_WAITING)


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
    assert append_rows[0]["values"][8]["userEnteredValue"]["stringValue"] == STATUS_NOT_REACHED
    assert append_rows[1]["values"][8]["userEnteredValue"]["stringValue"] == STATUS_FAILED
    assert history.row_values(1) == HISTORY_HEADERS


def test_snapshot_maps_called_entry_to_not_reached():
    spreadsheet = FakeSpreadsheet()
    active = _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_CALLED, 1),
            ("4431", "Петров", "ЛР1", STATUS_WAITING, 2),
        ],
    )
    active.title = "Очередь-2026-11-07"
    spreadsheet.sheets = {active.title: active}
    ensure_history_sheet(spreadsheet)
    spreadsheet.metadata_requests.clear()

    assert snapshot_sheet(spreadsheet, active.title) == 2
    append_rows = spreadsheet.metadata_requests[0]["appendCells"]["rows"]
    # И ждущий, и вызванный без результата к концу дня — "не дошёл".
    assert append_rows[0]["values"][-1]["userEnteredValue"]["stringValue"] == STATUS_NOT_REACHED
    assert append_rows[1]["values"][-1]["userEnteredValue"]["stringValue"] == STATUS_NOT_REACHED


def test_call_next_and_uncall_keep_single_active_entry():
    spreadsheet = FakeSpreadsheet()
    today = date(2026, 11, 8)
    sheet = _add_queue(
        spreadsheet,
        [
            ("4431", "Иванов", "ЛР1", STATUS_WAITING, 1),
            ("4431", "Петров", "ЛР1", STATUS_WAITING, 2),
            ("4431", "Сидоров", "ЛР1", STATUS_WAITING, 3),
        ],
    )

    called = call_next(spreadsheet, today, "4431")
    assert called.fio == "Иванов"

    rows = read_queue_rows(sheet)
    statuses = {row.fio: (row.position, row.status) for row in rows}
    # Позиции — билеты: вызванный и ждущие свои номера не меняют.
    assert statuses["Иванов"] == (1, STATUS_CALLED)
    assert statuses["Петров"] == (2, STATUS_WAITING)
    assert statuses["Сидоров"] == (3, STATUS_WAITING)

    # Повторная запись вызванным запрещена: одна активная запись на день.
    with pytest.raises(QueueError, match="ALREADY_IN_QUEUE"):
        register(spreadsheet, today, "4431", "Иванов", "ЛР1")

    uncall(spreadsheet, today, "4431", "Иванов")
    rows = read_queue_rows(sheet)
    statuses = {row.fio: (row.position, row.status) for row in rows}
    # Возврат в конец очереди — новый билет max+1; остальные не тронуты.
    assert statuses["Иванов"] == (4, STATUS_WAITING)
    assert statuses["Петров"] == (2, STATUS_WAITING)
    assert statuses["Сидоров"] == (3, STATUS_WAITING)


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
        row[8] for row in history.get_all_values()[1:]
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
    history.append_row(["2026-11-07", "1", "4431", "Иванов", "ЛР1", "", "", "", STATUS_PASSED])
    history.append_row(["2026-11-07", "2", "4431", "Петров", "ЛР1", "", "", "", STATUS_FAILED])
    spreadsheet.sheets[HISTORY_SHEET] = history

    assert find_in_history(history, "4431", "Иванов", "ЛР1", STATUS_PASSED) is True
    assert find_in_history(history, "4431", "Иванов", "ЛР1", STATUS_FAILED) is False
    assert find_in_history(history, "4431", "Петров", "ЛР1", STATUS_FAILED) is True
    assert find_in_history(history, "4432", "Иванов", "ЛР1", STATUS_PASSED) is False


def test_withdraw_and_shift_match_by_github_not_fio_for_same_name_students():
    """#15: two students sharing a FIO but not a GitHub login hold separate
    entries; neither may touch the other's row."""
    spreadsheet = FakeSpreadsheet()

    register(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", "ЛР1",
             github="c06-ivanov-a", signup_time="10:00")
    sheet = spreadsheet.sheets["Очередь-2026-11-08"]

    # Same FIO, different GitHub login: withdraw and shift must NOT match A's row.
    with pytest.raises(QueueError, match="NOT_IN_QUEUE"):
        withdraw(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван",
                 github="c06-ivanov-b")
    with pytest.raises(QueueError, match="NOT_IN_QUEUE"):
        shift_down(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", 1,
                   github="c06-ivanov-b")

    # A different FIO still matches only its own row (control).
    register(spreadsheet, date(2026, 11, 8), "4431", "Петрова Анна", "ЛР1",
             github="c06-petrova", signup_time="10:05")
    withdraw(spreadsheet, date(2026, 11, 8), "4431", "Петрова Анна",
             github="c06-petrova")

    rows = read_queue_rows(sheet)
    ivanov = [row for row in rows if row.fio == "Иванов Иван"][0]
    assert ivanov.status == STATUS_WAITING
    assert ivanov.github == "c06-ivanov-a"
    petrova = [row for row in rows if row.fio == "Петрова Анна"][0]
    assert petrova.status == STATUS_WITHDRAWN

    # The owner can still withdraw their own entry (github match).
    withdraw(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван",
             github="c06-ivanov-a")
    rows = read_queue_rows(sheet)
    ivanov = [row for row in rows if row.fio == "Иванов Иван"][0]
    assert ivanov.status == STATUS_WITHDRAWN


def test_register_same_fio_different_github_are_separate_entries():
    """#15: duplicate check is keyed by (fio, github) — two students with the
    same FIO can both wait, while the same login cannot register twice."""
    spreadsheet = FakeSpreadsheet()

    assert register(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", "ЛР1",
                    github="c06-ivanov-a") == 1
    assert register(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", "ЛР1",
                    github="c06-ivanov-b") == 2
    with pytest.raises(QueueError, match="ALREADY_IN_QUEUE"):
        register(spreadsheet, date(2026, 11, 8), "4431", "Иванов Иван", "ЛР1",
                 github="c06-ivanov-a")


