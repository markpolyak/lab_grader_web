"""Google Sheets store for daily queue tabs and their history."""

from __future__ import annotations

import logging

from dataclasses import dataclass
from datetime import date
import time
from typing import Any, Iterable

from gspread.exceptions import APIError, WorksheetNotFound

from .errors import QueueError
from . import cache
from .locks import locked_course

logger = logging.getLogger(__name__)

QUEUE_SHEET_PREFIX = "Очередь-"
HISTORY_SHEET = "История_очереди"

QUEUE_HEADERS = ["ПОЗИЦИЯ", "ГРУППА", "ФИО", "ЛР", "Пар№", "GitHub", "Время", "Статус"]
HISTORY_HEADERS = ["Дата", "ПОЗИЦИЯ", "ГРУППА", "ФИО", "ЛР", "Пар№", "GitHub", "Время", "Статус"]

# Column letters in the daily queue sheet (1-based index into QUEUE_HEADERS).
STATUS_COL = len(QUEUE_HEADERS)  # «Статус» is the last column
STATUS_COL_LETTER = chr(ord("A") + STATUS_COL - 1)
STATUS_COL_INDEX = STATUS_COL - 1  # 0-based, for Sheets API ranges

# Дневной лист создаётся на QUEUE_SHEET_ROWS строк: диапазоны dropdown /
# условного форматирования / фильтра задаются до этой строки (задача #19).
QUEUE_SHEET_ROWS = 200

# Колонка «ФИО» задана фиксированной шириной (автофита по содержимому в
# Sheets API нет), хватает на полное имя и отчество.
FIO_COL_INDEX = QUEUE_HEADERS.index("ФИО")
FIO_COLUMN_WIDTH_PX = 240

STATUS_WAITING = "ждёт"
STATUS_PASSED = "сдал"
STATUS_FAILED = "не_сдал"
STATUS_ABSENT = "нет"
STATUS_WITHDRAWN = "отозван"
STATUS_NOT_REACHED = "не_дошло"
STATUS_CALLED = "вызван"

ACTIVE_STATUSES = {
    STATUS_WAITING,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_ABSENT,
    STATUS_WITHDRAWN,
}
ADMIN_STATUSES = {STATUS_PASSED, STATUS_FAILED, STATUS_ABSENT}

# Статусы для dropdown на листе, порядок = порядок в выпадающем списке.
# «вызван» (вызов следующего, PR #15) — преподаватель может выставить его
# и руками в таблице.
QUEUE_STATUS_CHOICES = [
    STATUS_WAITING,
    STATUS_CALLED,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_ABSENT,
    STATUS_WITHDRAWN,
]

# Фон ячейки «Статус» по значению (условное форматирование, задача #19).
# Значение без цвета остаётся без подсветки («ждёт» — дефолтный белый).
STATUS_BG_COLORS = {
    STATUS_CALLED: "#e8f0fe",
    STATUS_PASSED: "#e6f4ea",
    STATUS_FAILED: "#fce8e6",
    STATUS_ABSENT: "#e8eaed",
    STATUS_WITHDRAWN: "#fef7e0",
}


def _hex_to_rgb(hex_color: str) -> dict[str, float]:
    """'#e6f4ea' -> {'red': 0.9, 'green': 0.95, 'blue': 0.91} (Sheets API)."""
    value = hex_color.lstrip("#")
    return {
        "red": int(value[0:2], 16) / 255.0,
        "green": int(value[2:4], 16) / 255.0,
        "blue": int(value[4:6], 16) / 255.0,
    }


@dataclass(frozen=True)
class QueueRow:
    position: int | None
    group: str
    fio: str
    lab: str
    session_no: int | None
    status: str
    row_index: int
    github: str = ""
    signup_time: str = ""



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
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 1,
                    "endRowIndex": QUEUE_SHEET_ROWS,
                    "startColumnIndex": STATUS_COL_INDEX,
                    "endColumnIndex": STATUS_COL_INDEX + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": status}
                            for status in QUEUE_STATUS_CHOICES
                        ],
                    },
                    "showCustomUi": True,
                    "strict": True,
                },
            }
        })
        # Условное форматирование ячейки «Статус» (задача #19): по правилу на
        # значение, диапазон до QUEUE_SHEET_ROWS. Только на клетку статуса,
        # не на строку — по решению в задаче.
        for status, color in STATUS_BG_COLORS.items():
            requests.append({
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": worksheet.id,
                            "startRowIndex": 1,
                            "endRowIndex": QUEUE_SHEET_ROWS,
                            "startColumnIndex": STATUS_COL_INDEX,
                            "endColumnIndex": STATUS_COL_INDEX + 1,
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": status}],
                            },
                            "format": {"backgroundColor": _hex_to_rgb(color)},
                        },
                    },
                    "index": 0,
                }
            })
        # Ширина колонки «ФИО» — под полное имя (задача #19): автофита по
        # содержимому в Sheets API нет, задаём фиксированную ширину.
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": FIO_COL_INDEX,
                    "endIndex": FIO_COL_INDEX + 1,
                },
                "properties": {"pixelSize": FIO_COLUMN_WIDTH_PX},
                "fields": "pixelSize",
            }
        })

    if queue:
        # Автофильтр по заголовкам (задача #19, п.3а): сортировка кликом и
        # фильтр по значениям, которые реально есть в колонке. Диапазон до
        # QUEUE_SHEET_ROWS. НЕ values.append-friendly проблем: записи
        # пишутся range-update'ом по вычисленному row_index.
        requests.append({
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": 0,
                        "endRowIndex": QUEUE_SHEET_ROWS,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(QUEUE_HEADERS),
                    }
                }
            }
        })

    spreadsheet.batch_update({"requests": requests})


# Кэш worksheet-объектов + валидации заголовков (план оптимизации, П.8/П.9).
# Ключ по id(spreadsheet): у нагрузочного стенда и прод-объектов разные
# инстансы — ключ не переживает пересоздание объекта. TTL 60 c ограничивает
# стухание при внешнем удалении/пересоздании листа.
_ws_cache: dict[tuple[int, str], tuple[float, object]] = {}
_validated_headers: dict[tuple[int, str], float] = {}
_WS_TTL_SECONDS = 60.0


def reset_ws_cache() -> None:
    """Сброс worksheet-кэша и валидации заголовков (тесты/смена окружения)."""
    _ws_cache.clear()
    _validated_headers.clear()


def _ensure_sheet(
    spreadsheet,
    title: str,
    headers: list[str],
    queue: bool,
    group_ids: Iterable[str] = (),
):
    key = (id(spreadsheet), title)
    now = time.monotonic()

    entry = _ws_cache.get(key)
    if entry is not None and now - entry[0] < _WS_TTL_SECONDS:
        worksheet = entry[1]
        validated_at = _validated_headers.get(key)
        if validated_at is not None and now - validated_at < _WS_TTL_SECONDS:
            return worksheet
        try:
            actual_headers = worksheet.row_values(1)
        except APIError:
            # лист удалён/пересоздан извне — сброс и пересоздание
            _ws_cache.pop(key, None)
            _validated_headers.pop(key, None)
            return _ensure_sheet(spreadsheet, title, headers, queue, group_ids)
        if actual_headers[: len(headers)] != headers or len(actual_headers) > len(headers):
            raise QueueError(
                "SHEETS_SCHEMA_MISMATCH",
                f"Неверные заголовки листа {title!r}: ожидаются {headers!r}",
            )
        _validated_headers[key] = now
        return worksheet

    worksheet = _get_worksheet(spreadsheet, title)
    if worksheet is None:
        for ws in spreadsheet.worksheets():
            if ws.title.startswith(title + "_conflict"):
                spreadsheet.batch_update({
                    "requests": [{"deleteSheet": {"sheetId": ws.id}}]
                })
        worksheet = spreadsheet.add_worksheet(
            title=title,
            rows=QUEUE_SHEET_ROWS,
            cols=len(headers),
        )
        _setup_new_sheet(spreadsheet, worksheet, headers, queue, group_ids)
    else:
        actual_headers = worksheet.row_values(1)
        if actual_headers[: len(headers)] != headers or len(actual_headers) > len(headers):
            raise QueueError(
                "SHEETS_SCHEMA_MISMATCH",
                f"Неверные заголовки листа {title!r}: ожидаются {headers!r}",
            )

    _ws_cache[key] = (now, worksheet)
    _validated_headers[key] = now
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
                status=padded[7].strip(),
                row_index=row_index,
                github=padded[5].strip(),
                signup_time=padded[6].strip(),
            )
        )
    return rows


def list_waiting(
    rows: Iterable[QueueRow],
    group: str,
    session_no: int | None = None,
) -> list[QueueRow]:
    return sorted(
        (
            row
            for row in rows
            if row.group == group
            and row.status == STATUS_WAITING
            and (session_no is None or row.session_no in (None, session_no))
        ),
        key=lambda row: (row.position or 0, row.row_index),
    )


def _identity_matches(
    row: QueueRow,
    github: str,
) -> bool:
    """Identity check for #15: a row belongs to the caller only when the
    GitHub logins agree. Legacy rows without a stored GitHub (and team rows,
    which are registered with an empty GitHub) fall back to matching by FIO.
    """
    return not github or not row.github or row.github == github


def find_waiting(
    rows: Iterable[QueueRow],
    group: str,
    fio: str,
    session_no: int | None = None,
    github: str = "",
) -> QueueRow | None:
    matches = [
        row
        for row in rows
        if row.group == group
        and row.fio == fio
        and row.status == STATUS_WAITING
        and (session_no is None or row.session_no in (None, session_no))
        and _identity_matches(row, github)
    ]
    if len(matches) > 1:
        raise QueueError(
            "AMBIGUOUS_STUDENT",
            "Найдено несколько ожидающих записей с этим ФИО",
        )
    return matches[0] if matches else None


def find_called(
    rows: Iterable[QueueRow],
    group: str,
    fio: str,
    session_no: int | None = None,
    github: str = "",
) -> QueueRow | None:
    """Called (but not yet finished) entry for *(group, fio)*, if any."""
    matches = [
        row
        for row in rows
        if row.group == group
        and row.fio == fio
        and row.status == STATUS_CALLED
        and (session_no is None or row.session_no in (None, session_no))
        and _identity_matches(row, github)
    ]
    if len(matches) > 1:
        raise QueueError(
            "AMBIGUOUS_STUDENT",
            "Найдено несколько вызванных записей с этим ФИО",
        )
    return matches[0] if matches else None


def _unique_row(
    rows: Iterable[QueueRow],
    group: str,
    fio: str,
    session_no: int | None = None,
    github: str = "",
) -> QueueRow:
    matches = [
        row
        for row in rows
        if row.group == group
        and row.fio == fio
        and row.status == STATUS_WAITING
        and (session_no is None or row.session_no in (None, session_no))
        and _identity_matches(row, github)
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
    values = cache.get_history_values(
        history_worksheet, history_worksheet.get_all_values
    )
    return any(
        len(row) >= len(HISTORY_HEADERS)
        and row[2].strip() == group
        and row[3].strip() == fio
        and row[4].strip() == lab
        and row[8].strip() == status
        for row in values[1:]
    )


def list_today(
    spreadsheet,
    today: date,
    group: str,
    group_ids: Iterable[str] = (),
    session_no: int | None = None,
) -> list[QueueRow]:
    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        return list_waiting(
            read_queue_rows(worksheet), group, session_no
        )



# Writing



def _write_cells(worksheet, updates: list[dict]) -> None:
    if updates:
        worksheet.batch_update(updates, value_input_option="RAW")


def register(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    lab: str,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
    github: str = "",
    signup_time: str = "",
) -> int:
    """Append a waiting row and return its 1-based position."""
    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = cache.get_queue_locked(
            spreadsheet.id, today.isoformat(),
            lambda: read_queue_rows(worksheet),
        )
        # A student has at most one active entry per day, across all labs
        # and sessions: they cannot defend two labs at once. "вызван" counts
        # as active — the teacher has called them, the defense has not ended.
        # To queue again they must first resolve the current entry ("сдал" /
        # "не явился" / "отозван"), then they rejoin at the end.
        already_active = [
            row
            for row in rows
            if row.group == group
            and row.fio == fio
            and row.status in (STATUS_WAITING, STATUS_CALLED)
            and _identity_matches(row, github)
        ]
        if already_active:
            raise QueueError("ALREADY_IN_QUEUE", "Участник уже ожидает")

        # Position after all existing entries in this (group, session) pair
        # queue, including finished ones, so new entries don't collide with
        # finished positions. The lab is only a label — the pair shares one
        # numbering (issue #62 review). Entries without a pair (Пар№ пуст)
        # видны во всех парах (как в list_waiting) — они тоже участвуют
        # в max, иначе пара получает вторую «позицию 1» рядом с такой строкой.
        in_scope = [
            row
            for row in rows
            if row.group == group
            and (session_no is None or row.session_no in (None, session_no))
        ]
        max_pos = max((row.position for row in in_scope if row.position), default=0)
        position = max_pos + 1
        # П.11 + Google Tables: запись строкой-диапазоном по вычисленному
        # row_index, БЕЗ values.append — append при пересечении с таблицей
        # ставит строку «после последней строки таблицы» и перезаписывает
        # данные (потеря записи). Диапазон таблицы (A1:H200) покрывает
        # вычисленный индекс — строка попадает внутрь таблицы.
        new_row_index = max((r.row_index for r in rows), default=1) + 1
        new_row = QueueRow(
            position=position, group=group, fio=fio, lab=lab,
            session_no=session_no, status=STATUS_WAITING,
            row_index=new_row_index, github=github, signup_time=signup_time,
        )
        # Постоянная сортировка: строка пишется в физический конец листа,
        # поэтому без пересортировки порядок сохраняется, только если лист
        # уже отсортирован и ключ новой строки не меньше последнего. Иначе
        # (запись в пару с меньшим номером, беспорядок от ручных правок)
        # лист пересортируется после записи — и до, и после добавления
        # лист остаётся отсортированным.
        keys = [_row_order_key(row) for row in rows]
        needs_sort = any(a > b for a, b in zip(keys, keys[1:])) or (
            bool(keys) and _row_order_key(new_row) < keys[-1]
        )
        _write_cells(
            worksheet,
            [{
                "range": f"A{new_row_index}",
                "values": [[
                    position, group, fio, lab, session_no or "",
                    github, signup_time, STATUS_WAITING,
                ]],
            }],
        )
        cache.append_row_to_cache(spreadsheet.id, today.isoformat(), new_row)
        if needs_sort:
            _sort_sheet_rows(spreadsheet, today.isoformat(), worksheet)
        return position


def _row_order_key(row: QueueRow) -> tuple:
    """Порядок постоянной сортировки листа: (группа, пара, позиция-тикет),
    при равенстве — текущая физическая строка (стабильность)."""
    return (
        row.group or "",
        row.session_no if row.session_no is not None else 0,
        row.position or 10**6,
        row.row_index,
    )


def _sort_sheet_rows(spreadsheet, today_str: str, worksheet) -> None:
    """Постоянная сортировка физических строк листа («сортировать
    постоянно»): строки переписываются в порядке
    (группа, пара, позиция-тикет) после мутаций, меняющих порядок
    (добавление в пару с меньшим ключом, сдвиг, возврат из вызванных).
    Остальные мутации порядок не ломают.

    Порядок строк в кэше = физический порядок, поэтому после пересортировки
    кэш строк инвалидируется.
    """
    rows = read_queue_rows(worksheet)
    if not rows:
        return

    ordered = sorted(rows, key=_row_order_key)
    if [r.row_index for r in ordered] == [r.row_index for r in rows]:
        return  # уже отсортировано

    updates = [
        {
            "range": f"A{physical_row}",
            "values": [[
                row.position if row.position is not None else "",
                row.group, row.fio, row.lab,
                row.session_no if row.session_no is not None else "",
                row.github, row.signup_time, row.status,
            ]],
        }
        for physical_row, row in enumerate(ordered, start=2)
    ]
    _write_cells(worksheet, updates)
    cache.invalidate_queue(spreadsheet.id, today_str)


def withdraw(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
    github: str = "",
) -> None:
    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = cache.get_queue_locked(
            spreadsheet.id, today.isoformat(),
            lambda: read_queue_rows(worksheet),
        )
        matching = [
            row
            for row in rows
            if row.group == group and row.fio == fio
            and _identity_matches(row, github)
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
            [{"range": f"{STATUS_COL_LETTER}{row.row_index}", "values": [[STATUS_WITHDRAWN]]}],
        )
        cache.invalidate_queue(spreadsheet.id, today.isoformat())


def shift_down(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    delta: int,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
    github: str = "",
) -> tuple[int, int]:
    """Move a waiting participant *delta* places back in the pair queue.

    Positions behave like tickets (issue #62 review): the move rotates
    tickets only within the affected segment — every entry the mover jumps
    over takes the ticket ahead of it, the mover takes the segment's last
    ticket. Tickets are permuted among waiting entries only, so called and
    finished rows keep theirs, no duplicates appear, and all other rows are
    untouched. Returns (old, new) 1-based ranks among waiting entries.
    """
    if isinstance(delta, bool) or not isinstance(delta, int) or delta < 1:
        raise QueueError(
            "INVALID_SHIFT", "Сдвиг вниз должен быть целым числом не меньше 1"
        )

    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = cache.get_queue_locked(
            spreadsheet.id, today.isoformat(),
            lambda: read_queue_rows(worksheet),
        )
        waiting = list_waiting(rows, group, session_no)
        row = _unique_row(waiting, group, fio, session_no, github=github)
        if session_no is None and row.session_no is not None:
            # Each pair has its own queue: shift is counted inside the
            # student's pair, not across all entries of the day.
            session_no = row.session_no
            waiting = list_waiting(rows, group, session_no)
        current_index = waiting.index(row)
        new_index = min(current_index + delta, len(waiting) - 1)
        if new_index == current_index:
            raise QueueError("INVALID_SHIFT", "Участник уже в конце очереди")

        window = waiting[current_index : new_index + 1]
        tickets = [entry.position for entry in window]
        rotated = window[1:] + window[:1]
        updates = [
            {"range": f"A{entry.row_index}", "values": [[ticket]]}
            for entry, ticket in zip(rotated, tickets)
        ]
        _write_cells(worksheet, updates)
        _sort_sheet_rows(spreadsheet, today.isoformat(), worksheet)
        cache.invalidate_queue(spreadsheet.id, today.isoformat())
        return current_index + 1, new_index + 1


def set_status(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    status: str,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
) -> None:
    if status not in ADMIN_STATUSES:
        raise QueueError("INVALID_STATUS", f"Недопустимый статус: {status!r}")

    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = cache.get_queue_locked(
            spreadsheet.id, today.isoformat(),
            lambda: read_queue_rows(worksheet),
        )
        matching = [
            row
            for row in rows
            if row.group == group and row.fio == fio
        ]
        # A defense result may be set for a waiting entry (called directly
        # from the sheet) or for a called one (finalizing a defense).
        active = [
            row
            for row in matching
            if row.status in (STATUS_WAITING, STATUS_CALLED)
        ]
        if not active:
            if matching:
                raise QueueError("NOT_WAITING", "Запись больше не ожидает")
            raise QueueError("NOT_IN_QUEUE", "Активная запись не найдена")
        if len(active) > 1:
            raise QueueError(
                "AMBIGUOUS_STUDENT",
                "Найдено несколько активных записей с этим ФИО",
            )
        row = active[0]
        _write_cells(
            worksheet,
            [{"range": f"{STATUS_COL_LETTER}{row.row_index}", "values": [[status]]}],
        )
        cache.invalidate_queue(spreadsheet.id, today.isoformat())


def call_next(
    spreadsheet,
    today: date,
    group: str,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
    fio: str | None = None,
) -> QueueRow:
    """Call the first waiting participant, or a specific one by *fio*.

    The queue is scoped per pair, not per lab (issue #62 review): the
    teacher calls the next participant of the group's pair queue regardless
    of which lab they are defending. Parallel calls are allowed: each call
    marks one waiting entry as "вызван" and it keeps its position —
    positions behave like tickets and are never renumbered, the next call
    simply picks the waiting entry with the smallest ticket. The called
    entry stays visible until a result is set.
    """
    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = cache.get_queue_locked(
            spreadsheet.id, today.isoformat(),
            lambda: read_queue_rows(worksheet),
        )
        waiting = list_waiting(rows, group, session_no)
        if fio is None:
            if not waiting:
                raise QueueError("NO_WAITING", "Нет ожидающих в очереди")
            row = waiting[0]
        else:
            row = _unique_row(waiting, group, fio, session_no)
        _write_cells(
            worksheet,
            [
                {"range": f"{STATUS_COL_LETTER}{row.row_index}", "values": [[STATUS_CALLED]]},
            ],
        )
        cache.invalidate_queue(spreadsheet.id, today.isoformat())
        return QueueRow(
            position=row.position,
            group=row.group,
            fio=row.fio,
            lab=row.lab,
            session_no=row.session_no,
            status=STATUS_CALLED,
            row_index=row.row_index,
        )


def uncall(
    spreadsheet,
    today: date,
    group: str,
    fio: str,
    session_no: int | None = None,
    group_ids: Iterable[str] = (),
) -> None:
    """Return a called entry back to the waiting queue, at the end."""
    with locked_course(spreadsheet.id):
        worksheet = ensure_today_sheet(spreadsheet, today, group_ids)
        rows = cache.get_queue_locked(
            spreadsheet.id, today.isoformat(),
            lambda: read_queue_rows(worksheet),
        )
        matching = [
            row
            for row in rows
            if row.group == group and row.fio == fio
        ]
        called = [row for row in matching if row.status == STATUS_CALLED]
        if not called:
            if matching:
                raise QueueError("NOT_CALLED", "Запись не вызвана")
            raise QueueError("NOT_IN_QUEUE", "Вызванная запись не найдена")
        if len(called) > 1:
            raise QueueError(
                "AMBIGUOUS_STUDENT",
                "Найдено несколько вызванных записей с этим ФИО",
            )
        row = called[0]
        # Back to the end of the pair queue: a fresh ticket (max + 1) like at
        # registration. Positions are never renumbered (issue #62 review).
        in_scope = [
            r for r in rows
            if r.group == group
            and (session_no is None or r.session_no in (None, session_no))
        ]
        end_position = max((r.position for r in in_scope if r.position), default=0) + 1
        _write_cells(
            worksheet,
            [
                {"range": f"A{row.row_index}", "values": [[end_position]]},
                {"range": f"{STATUS_COL_LETTER}{row.row_index}", "values": [[STATUS_WAITING]]},
            ],
        )
        _sort_sheet_rows(spreadsheet, today.isoformat(), worksheet)
        cache.invalidate_queue(spreadsheet.id, today.isoformat())



# Snapshot — idempotent transfer to history + sheet deletion



def _history_row_key(
    target_date: date,
    group: str,
    fio: str,
    lab: str,
    session_no: str,
) -> tuple[str, str, str, str, str]:
    return (target_date.isoformat(), group, fio, lab, session_no)


def _read_history_counts_for_date(
    history_worksheet,
    target_date: date,
) -> dict[tuple[str, str, str, str, str], int]:
    """Count history rows by date, student, lab, and session."""
    values = history_worksheet.get_all_values()
    counts: dict[tuple[str, str, str, str, str], int] = {}
    for row in values[1:]:
        if len(row) < 5:
            continue
        if row[0].strip() != target_date.isoformat():
            continue
        key = _history_row_key(
            target_date,
            row[2].strip(),
            row[3].strip(),
            row[4].strip(),
            row[5].strip() if len(row) > 5 else "",
        )
        counts[key] = counts.get(key, 0) + 1
    return counts


def snapshot_sheet(spreadsheet, sheet_name: str) -> int:
    """Atomically append a day's rows to history and delete its queue tab.

    Idempotent: existing history rows are matched as a multiset by
    ``(date, group, fio, lab, session)``. Repeated registrations are retained,
    while a retry after a partial failure does not duplicate rows.

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
        existing_counts = _read_history_counts_for_date(history, sheet_date)
        seen_counts: dict[tuple[str, str, str, str, str], int] = {}

        new_rows: list[tuple[str, str, str, str, str, str, str, str, str]] = []
        for row in rows:
            key = _history_row_key(
                sheet_date,
                row.group,
                row.fio,
                row.lab,
                str(row.session_no or ""),
            )
            seen_counts[key] = seen_counts.get(key, 0) + 1
            if seen_counts[key] <= existing_counts.get(key, 0):
                continue
            final_status = (
                STATUS_NOT_REACHED
                if row.status in (STATUS_WAITING, STATUS_CALLED)
                else row.status
            )
            new_rows.append(
                (
                    sheet_date.isoformat(),        # 0: Дата
                    str(row.position or ""),        # 1: ПОЗИЦИЯ
                    row.group,                      # 2: ГРУППА
                    row.fio,                        # 3: ФИО
                    row.lab,                        # 4: ЛР
                    str(row.session_no or ""),      # 5: Пар№
                    row.github,                     # 6: GitHub
                    row.signup_time,                # 7: Время
                    final_status,                   # 8: Статус
                )
            )

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
                        {"userEnteredValue": {"stringValue": gh}},
                        {"userEnteredValue": {"stringValue": t}},
                        {"userEnteredValue": {"stringValue": s}},
                    ]
                }
                for d, p, g, f, l, sn, gh, t, s in new_rows
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
        cache.invalidate_history(history)
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
