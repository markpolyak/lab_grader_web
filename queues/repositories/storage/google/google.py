"""Google Sheets adapter for final lesson queue snapshots."""
import asyncio
from copy import deepcopy
from threading import Lock
from typing import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gspread

from ....domain.lesson import Lesson
from .rows import HEADERS, snapshot_rows
from .formatting import block_requests


class GoogleRepo:
    """One shared instance per history sheet in a single backend process.

    The injected factory supplies authentication and HTTP timeouts (for example,
    main.sheets_client). This adapter never removes lessons from memory.
    """

    def __init__(
        self,
        client_factory: Callable[[], gspread.Client],
        spreadsheet_id: str,
    ) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("Укажите ID таблицы истории")
        self._client_factory = client_factory
        self._spreadsheet_id = spreadsheet_id
        self._lock = Lock()

    async def save(
        self,
        lesson: Lesson,
        *,
        course_name: str,
    ) -> bool:
        """Save a final snapshot; return False if it was already saved.

        Network errors propagate to the caller so memory can be retained and
        saving retried. Google I/O runs outside the application's event loop.
        Later calls do not replace an already archived snapshot.
        """
        rows = snapshot_rows(deepcopy(lesson), course_name=course_name)
        local_date = lesson.starts_at.astimezone(ZoneInfo("Europe/Moscow")).date()
        monday = local_date - timedelta(days=local_date.weekday())
        sunday = monday + timedelta(days=6)
        title = f"{monday:%d.%m.%Y}–{sunday:%d.%m.%Y}"
        return await asyncio.to_thread(self._save_rows, title, rows)

    def _save_rows(self, title: str, rows: list[list]) -> bool:
        with self._lock:
            spreadsheet = self._client_factory().open_by_key(self._spreadsheet_id)
            try:
                sheet = spreadsheet.worksheet(title)
            except gspread.WorksheetNotFound:
                sheet = spreadsheet.add_worksheet(
                    title=title, rows=1000, cols=len(HEADERS),
                )
            existing = sheet.get_all_values()
            while existing and not any(existing[-1]):
                existing.pop()
            if existing and (not existing[0] or not existing[0][0].startswith("Занятие: ")):
                raise ValueError("Лист курса уже содержит данные другого формата")
            course_label = rows[2][0].split(" · Аудитория:")[0]
            for index, row in enumerate(existing):
                if row and row[0].startswith("Занятие: "):
                    if index + 2 >= len(existing) or not existing[index + 2] or not existing[index + 2][0].startswith(course_label + " · Аудитория:"):
                        raise ValueError("Для другого курса нужна отдельная таблица истории")
            # A readable date/time + kind/groups pair identifies a published block.
            # No identifiers or hidden metadata are written to the spreadsheet.
            for index, row in enumerate(existing[:-1]):
                if row and row[0] == rows[0][0] and existing[index + 1] and existing[index + 1][0] == rows[1][0]:
                    return False
            # Keep blocks chronological even when older failed saves are retried.
            incoming = datetime.strptime(rows[0][0][9:27], "%d.%m.%Y · %H:%M")
            position = next((i for i, row in enumerate(existing)
                if row and row[0].startswith("Занятие: ")
                and datetime.strptime(row[0][9:27], "%d.%m.%Y · %H:%M") > incoming), None)
            start = position if position is not None else (len(existing) + 2 if existing else 0)
            requests = []
            if position is not None:
                requests.append({"insertDimension": {"range": {
                    "sheetId": sheet.id, "dimension": "ROWS", "startIndex": start,
                    "endIndex": start + len(rows) + 2,
                }, "inheritFromBefore": False}})
            requests.extend(block_requests(
                sheet.id, start, rows, row_count=sheet.row_count, col_count=sheet.col_count,
            ))
            if position is not None:
                requests[1]["updateSheetProperties"]["properties"]["gridProperties"]["rowCount"] = max(sheet.row_count + len(rows) + 2, start + len(rows) + 2)
            spreadsheet.batch_update({"requests": requests})
            return True
