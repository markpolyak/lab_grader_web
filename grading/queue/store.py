"""Google Sheets store for daily queue tabs and their history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from gspread.exceptions import WorksheetNotFound

from .errors import QueueError
from .locks import locked_course

QUEUE_SHEET_PREFIX = "Очередь-"
HISTORY_SHEET = "История_очереди"

QUEUE_HEADERS = ["ПОЗИЦИЯ", "ГРУППА", "ФИО", "ЛР", "Пар№", "Статус"]
HISTORY_HEADERS = ["Дата", "ПОЗИЦИЯ", "ГРУППА", "ФИО", "ЛР", "Пар№", "Статус"]

STATUS_WAITING = "ждёт"
STATUS_PASSED = "сдал"
STATUS_FAILED = "не_сдал"
STATUS_ABSENT = "нет"
STATUS_WITHDRAWN = "отозван"
STATUS_NOT_REACHED = "не_дошло"

ACTIVE_STATUSES = {
    STATUS_WAITING,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_ABSENT,
    STATUS_WITHDRAWN,
}
ADMIN_STATUSES = {STATUS_PASSED, STATUS_FAILED, STATUS_ABSENT}


@dataclass(frozen=True)
class QueueRow:
    position: int | None
    group: str
    fio: str
    lab: str
    session_no: int | None
    status: str
    row_index: int



# Sheet name helpers



def queue_sheet_name(day: date) -> str:
    return f"{QUEUE_SHEET_PREFIX}{day.isoformat()}"


def parse_date_from_sheet_name(sheet_name: str) -> date | None:
    if not sheet_name.startswith(QUEUE_SHEET_PREFIX):
        return None
    try:
        return date.fromisoformat(sheet_name[len(QUEUE_SHEET_PREFIX):])
    except ValueError:
        return None


def find_past_queue_sheets(spreadsheet, today: date) -> list[str]:
    """Names of ``Очередь-YYYY-MM-DD`` sheets whose date is strictly before *today*."""
    past = []
    for worksheet in spreadsheet.worksheets():
        sheet_date = parse_date_from_sheet_name(worksheet.title)
        if sheet_date is not None and sheet_date < today:
            past.append((sheet_date, worksheet.title))
    return [title for _, title in sorted(past)]



# Sheet creation and setup



def _get_worksheet(spreadsheet, title: str):
    try:
        return spreadsheet.worksheet(title)
    except WorksheetNotFound:
        return None


def _filter_view(title: str, sheet_id: int, criteria: dict[int, str]) -> dict:
    return {
        "addFilterView": {
            "filter": {
                "title": title,
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(QUEUE_HEADERS),
                },
                "criteria": {
                    str(column): {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": value}],
                        }
                    }
                    for column, value in criteria.items()
                },
            }
        }
    }


def _setup_new_sheet(
    spreadsheet,
    worksheet,
    headers: list[str],
    queue: bool,
    group_ids: Iterable[str] = (),
) -> None:
    for column, header in enumerate(headers, start=1):
        worksheet.update_cell(1, column, header)

    protected_range = {
        "addProtectedRange": {
            "protectedRange": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(headers),
                },
                "description": f"{worksheet.title}: headers",
                "warningOnly": False,
            }
        }
    }
    requests = [protected_range]

    if queue:
        requests.append(
            _filter_view("Все ждёт", worksheet.id, {5: STATUS_WAITING})
        )
        requests.append(
            _filter_view("Сдано", worksheet.id, {5: STATUS_PASSED})
        )
        requests.append(
            _filter_view("Не сдал", worksheet.id, {5: STATUS_FAILED})
        )
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 1,
                    "startColumnIndex": 5,
                    "endColumnIndex": 6,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": STATUS_WAITING},
                            {"userEnteredValue": STATUS_PASSED},
                            {"userEnteredValue": STATUS_FAILED},
                            {"userEnteredValue": STATUS_ABSENT},
                            {"userEnteredValue": STATUS_WITHDRAWN},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": True,
                },
            }
        })
        for group_id in sorted({str(g) for g in group_ids}):
            requests.append(
                _filter_view(f"Группа {group_id}", worksheet.id, {1: group_id})
            )
        for session_no in ["1", "2", "3", "4", "5", "6"]:
            requests.append(
                _filter_view(f"Пара {session_no}", worksheet.id, {4: session_no})
            )

    spreadsheet.batch_update({"requests": requests})


def _ensure_sheet(
    spreadsheet,
    title: str,
    headers: list[str],
    queue: bool,
    group_ids: Iterable[str] = (),
):
    worksheet = _get_worksheet(spreadsheet, title)
    if worksheet is None:
        for ws in spreadsheet.worksheets():
            if ws.title.startswith(title + "_conflict"):
                spreadsheet.batch_update({
                    "requests": [{"deleteSheet": {"sheetId": ws.id}}]
                })
        worksheet = spreadsheet.add_worksheet(
            title=title,
            rows=100,
            cols=len(headers),
        )
        _setup_new_sheet(spreadsheet, worksheet, headers, queue, group_ids)
        return worksheet

    actual_headers = worksheet.row_values(1)
    if actual_headers[: len(headers)] != headers or len(actual_headers) > len(headers):
        raise QueueError(
            "SHEETS_SCHEMA_MISMATCH",
            f"Неверные заголовки листа {title!r}: ожидаются {headers!r}",
        )
    return worksheet


def ensure_today_sheet(
    spreadsheet,
    today: date,
    group_ids: Iterable[str] = (),
):
    """Create or validate today's active queue tab."""
    return _ensure_sheet(
        spreadsheet,
        queue_sheet_name(today),
        QUEUE_HEADERS,
        queue=True,
        group_ids=group_ids,
    )


def ensure_history_sheet(spreadsheet):
    """Create or validate the append-only history tab."""
    return _ensure_sheet(spreadsheet, HISTORY_SHEET, HISTORY_HEADERS, queue=False)



# Reading



def _safe_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (ValueError, TypeError):
        return None


def read_queue_rows(worksheet) -> list[QueueRow]:
    values = worksheet.get_all_values()
    rows: list[QueueRow] = []
    for row_index, values_row in enumerate(values[1:], start=2):
        if not values_row or not any(values_row):
            continue
        padded = values_row + [""] * (len(QUEUE_HEADERS) - len(values_row))
        rows.append(
            QueueRow(
                position=_safe_int(padded[0]),
                group=padded[1].strip(),
                fio=padded[2].strip(),
                lab=padded[3].strip(),
                session_no=_safe_int(padded[4]),
                status=padded[5].strip(),
                row_index=row_index,
            )
        )
    return rows


def list_waiting(
    rows: Iterable[QueueRow],
    group: str,
    lab: str,
    session_no: int | None = None,
) -> list[QueueRow]:
    """Waiting entries for *(group, lab)*, optionally filtered by session number."""
    return sorted(
        (
            row
            for row in rows
            if row.group == group
            and row.lab == lab
            and row.status == STATUS_WAITING
            and (session_no is None or row.session_no == session_no)
        ),
        key=lambda row: (row.position or 0, row.row_index),
    )


def find_waiting(
    rows: Iterable[QueueRow],
    group: str,
    lab: str,
    fio: str,
    session_no: int | None = None,
) -> QueueRow | None:
    matches = [
        row
        for row in rows
        if row.group == group
        and row.lab == lab
        and row.fio == fio
        and row.status == STATUS_WAITING
        and (session_no is None or row.session_no == session_no)
    ]
    if len(matches) > 1:
        raise QueueError(
            "AMBIGUOUS_STUDENT",
            "Найдено несколько ожидающих записей с этим ФИО",
        )
    return matches[0] if matches else None


def _unique_row(
    rows: Iterable[QueueRow],
    group: str,
    lab: str,
    fio: str,
    session_no: int | None = None,
) -> QueueRow:
    matches = [
        row
        for row in rows
        if row.group == group
        and row.lab == lab
        and row.fio == fio
        and row.status == STATUS_WAITING
        and (session_no is None or row.session_no == session_no)
    ]
    if not matches:
        raise QueueError("NOT_IN_QUEUE", "Ожидающая запись не найдена")
    if len(matches) > 1:
        raise QueueError(
            "AMBIGUOUS_STUDENT",
            "Найдено несколько ожидающих записей с этим ФИО",
        )
    return matches[0]


def find_in_history(
    history_worksheet,
    group: str,
    fio: str,
    lab: str,
    status: str,
) -> bool:
    """Whether *(group, fio, lab, status)* already exists in the history sheet."""
    values = history_worksheet.get_all_values()
    return any(
        len(row) >= 7
        and row[2].strip() == group
        and row[3].strip() == fio
        and row[4].strip() == lab
        and row[6].strip() == status
        for row in values[1:]
    )


def list_today(
    spreadsheet,
    today: date,
    group: str,
    lab: str,
    group_ids: Iterable[str] = (),
    session_no: int | None = None,
) -> list[QueueRow]:
    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        return list_waiting(
            read_queue_rows(worksheet), group, lab, session_no
        )



# Writing



def _write_cells(worksheet, updates: list[dict]) -> None:
    if updates:
        worksheet.batch_update(updates, value_input_option="RAW")


def _renumber_waiting(
    worksheet,
    group: str,
    lab: str,
    session_no: int | None = None,
) -> None:
    rows = read_queue_rows(worksheet)
    waiting = list_waiting(rows, group, lab, session_no)
    updates = [
        {"range": f"A{row.row_index}", "values": [[position]]}
        for position, row in enumerate(waiting, start=1)
        if row.position != position
    ]
    _write_cells(worksheet, updates)


def register(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    lab: str,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
) -> int:
    """Append a waiting row and return its 1-based position."""
    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = read_queue_rows(worksheet)
        # Duplicate check ignores session_no: a student cannot be waiting
        # twice for the same lab on the same day, even across sessions.
        already_waiting = [
            row
            for row in rows
            if row.group == group
            and row.lab == lab
            and row.fio == fio
            and row.status == STATUS_WAITING
        ]
        if already_waiting:
            raise QueueError("ALREADY_IN_QUEUE", "Участник уже ожидает")

        waiting = list_waiting(rows, group, lab, session_no)
        position = len(waiting) + 1
        worksheet.append_row(
            [position, group, fio, lab, session_no or "", STATUS_WAITING],
            value_input_option="RAW",
            table_range="A1:F",
        )
        return position


def withdraw(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    lab: str,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
) -> None:
    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = read_queue_rows(worksheet)
        matching = [
            row
            for row in rows
            if row.group == group and row.lab == lab and row.fio == fio
        ]
        waiting = [row for row in matching if row.status == STATUS_WAITING]
        if not waiting:
            if matching:
                raise QueueError("NOT_WAITING", "Запись больше не ожидает")
            raise QueueError("NOT_IN_QUEUE", "Ожидающая запись не найдена")
        if len(waiting) > 1:
            raise QueueError(
                "AMBIGUOUS_STUDENT",
                "Найдено несколько ожидающих записей с этим ФИО",
            )
        row = waiting[0]
        _write_cells(
            worksheet,
            [{"range": f"F{row.row_index}", "values": [[STATUS_WITHDRAWN]]}],
        )
        _renumber_waiting(worksheet, group, lab, session_no)


def shift_down(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    lab: str,
    delta: int,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
) -> tuple[int, int]:
    if isinstance(delta, bool) or not isinstance(delta, int) or delta < 1:
        raise QueueError(
            "INVALID_SHIFT", "Сдвиг вниз должен быть целым числом не меньше 1"
        )

    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        waiting = list_waiting(
            read_queue_rows(worksheet), group, lab, session_no
        )
        row = _unique_row(waiting, group, lab, fio, session_no)
        current_index = waiting.index(row)
        new_index = min(current_index + delta, len(waiting) - 1)
        if new_index == current_index:
            raise QueueError("INVALID_SHIFT", "Участник уже в конце очереди")

        reordered = list(waiting)
        reordered.insert(new_index, reordered.pop(current_index))
        updates = [
            {"range": f"A{entry.row_index}", "values": [[position]]}
            for position, entry in enumerate(reordered, start=1)
        ]
        _write_cells(worksheet, updates)
        return current_index + 1, new_index + 1


def set_status(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    lab: str,
    status: str,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
) -> None:
    if status not in ADMIN_STATUSES:
        raise QueueError("INVALID_STATUS", f"Недопустимый статус: {status!r}")

    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = read_queue_rows(worksheet)
        matching = [
            row
            for row in rows
            if row.group == group and row.lab == lab and row.fio == fio
        ]
        waiting = [row for row in matching if row.status == STATUS_WAITING]
        if not waiting:
            if matching:
                raise QueueError("NOT_WAITING", "Запись больше не ожидает")
            raise QueueError("NOT_IN_QUEUE", "Ожидающая запись не найдена")
        if len(waiting) > 1:
            raise QueueError(
                "AMBIGUOUS_STUDENT",
                "Найдено несколько ожидающих записей с этим ФИО",
            )
        row = waiting[0]
        _write_cells(
            worksheet,
            [{"range": f"F{row.row_index}", "values": [[status]]}],
        )
        _renumber_waiting(worksheet, group, lab, session_no)



# Snapshot — idempotent transfer to history + sheet deletion



def _read_history_keys_for_date(
    history_worksheet,
    target_date: date,
) -> set[tuple[str, str, str]]:
    """Existing ``(group, fio, lab)`` triples in history for *target_date*."""
    values = history_worksheet.get_all_values()
    keys: set[tuple[str, str, str]] = set()
    for row in values[1:]:
        if len(row) < 5:
            continue
        if row[0].strip() == target_date.isoformat():
            keys.add((row[2].strip(), row[3].strip(), row[4].strip()))
    return keys


def snapshot_sheet(spreadsheet, sheet_name: str) -> int:
    """Atomically append a day's rows to history and delete its queue tab.

    Idempotent: rows already present in history (matched by
    ``(group, fio, lab)``) are skipped, so a retry after a partial
    failure does not duplicate data.

    The append and delete are sent as a single ``batch_update`` call
    (two requests: ``appendCells`` + ``deleteSheet``) so they are atomic
    from the Sheets API perspective.
    """
    with locked_course(spreadsheet.id):
        worksheet = _get_worksheet(spreadsheet, sheet_name)
        if worksheet is None:
            return 0

        sheet_date = parse_date_from_sheet_name(sheet_name)
        if sheet_date is None:
            raise QueueError(
                "INVALID_QUEUE_SHEET", f"Некорректное имя листа: {sheet_name!r}"
            )

        rows = read_queue_rows(worksheet)
        history = ensure_history_sheet(spreadsheet)
        existing_keys = _read_history_keys_for_date(history, sheet_date)

        new_rows: list[tuple[str, str, str, str, str, str, str]] = []
        for row in rows:
            key = (row.group, row.fio, row.lab)
            if key in existing_keys:
                continue
            final_status = (
                STATUS_NOT_REACHED if row.status == STATUS_WAITING else row.status
            )
            new_rows.append(
                (
                    sheet_date.isoformat(),        # 0: Дата
                    str(row.position or ""),        # 1: ПОЗИЦИЯ
                    row.group,                      # 2: ГРУППА
                    row.fio,                        # 3: ФИО
                    row.lab,                        # 4: ЛР
                    str(row.session_no or ""),      # 5: Пар№
                    final_status,                   # 6: Статус
                )
            )
            existing_keys.add(key)

        requests: list[dict] = []
        if new_rows:
            history_rows = [
                {
                    "values": [
                        {"userEnteredValue": {"stringValue": d}}, 
                        {"userEnteredValue": {"stringValue": p}}, 
                        {"userEnteredValue": {"stringValue": g}}, 
                        {"userEnteredValue": {"stringValue": f}}, 
                        {"userEnteredValue": {"stringValue": l}}, 
                        {"userEnteredValue": {"stringValue": sn}},
                        {"userEnteredValue": {"stringValue": s}}, 
                    ]
                }
                for d, p, g, f, l, sn, s in new_rows
            ]
            requests.append(
                {
                    "appendCells": {
                        "sheetId": history.id,
                        "rows": history_rows,
                        "fields": "userEnteredValue",
                    }
                }
            )
        requests.append({"deleteSheet": {"sheetId": worksheet.id}})
        spreadsheet.batch_update({"requests": requests})
        return len(new_rows)

def maybe_snapshot_past_days(spreadsheet, today: date) -> int:
    """Lazy snapshot triggered on the first queue request of a new day."""
    past_sheets = find_past_queue_sheets(spreadsheet, today)
    total = 0
    for sheet_name in past_sheets:
        total += snapshot_sheet(spreadsheet, sheet_name)
    return total


def manual_snapshot(spreadsheet, today: date) -> int:
    """Manual snapshot invoked by the admin endpoint."""
    return maybe_snapshot_past_days(spreadsheet, today)
