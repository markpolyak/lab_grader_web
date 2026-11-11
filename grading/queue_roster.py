"""
Read-only access to the course spreadsheet for the defense queue.

The queue needs three facts about a student - full name, group (= worksheet
title) and the cells of their labs - and needs them only when the student
signs in or signs up, never while the page polls. Registration asks one more:
whether a GitHub login is already taken by a row of the course, since the
GitHub column is the student's identity in the queue. This module reads and
NEVER writes: the queue organizes the order of defenses, grades stay the
teacher's business (customer's answer in issue #62).
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import gspread

from .schedule.base import normalize_text
from .sheets_client import cell_from_grid, find_lab_column_in_grid

logger = logging.getLogger(__name__)

# Сколько живёт прочитанная таблица. В начале пары вся группа входит почти
# одновременно, а квота Sheets - 60 чтений в минуту на весь сервис: без кэша
# 30 входов по 3 чтения её бы выбили.
ROSTER_CACHE_TTL_SECONDS = 60

# lookup(fresh=True): студент мог только что получить оценку, но одну
# таблицу всё равно перечитываем не чаще раза в 10 с.
ROSTER_FRESH_AFTER_SECONDS = 10

# Сколько ждать, пока ту же таблицу дочитает другой запрос. Не дождались -
# берём копию не старше ROSTER_CACHE_TTL_SECONDS (или allow_stale_seconds),
# иначе отказ.
ROSTER_WAIT_SECONDS = 3

# Row 1 holds the "GitHub" header, row 2 the lab short names.
FIRST_DATA_ROW = 3
GITHUB_HEADER = "GitHub"
DEFAULT_NAME_COLUMN = 1


@dataclass(frozen=True)
class StudentRecord:
    """One student's row, as found in the course spreadsheet."""
    github: str                 # as written in the GitHub column / returned by GitHub
    login: str                  # casefolded, the queue's identity key
    name: str                   # full name from student-name-column
    group: str                  # normalized worksheet title
    labs: dict[str, str] = field(default_factory=dict)   # short-name -> raw cell value


class RosterError(Exception):
    """The student cannot be identified in the spreadsheet."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code          # STUDENT_NOT_FOUND | STUDENT_AMBIGUOUS


class RosterUnavailable(Exception):
    """Google Sheets could not be read (network, quota, credentials)."""


# ---------------------------------------------------------------------------
# Pure lookup
# ---------------------------------------------------------------------------


def find_student_in_grids(
    grids: dict[str, list[list[str]]],
    github: str,
    name_col: int,
    lab_short_names,
) -> StudentRecord:
    """
    Find a student by GitHub login on the worksheets of one spreadsheet.

    Args:
        grids: worksheet title -> raw grid (rows may be ragged)
        github: GitHub login of the signed-in student
        name_col: `google.student-name-column`, 0-based
        lab_short_names: short names of the labs whose cells to collect

    Returns:
        StudentRecord; a lab whose column is missing on the sheet is left out
        of `labs`

    Raises:
        RosterError: STUDENT_NOT_FOUND, or STUDENT_AMBIGUOUS when the login
        is on more than one row (on one sheet or on several)
    """
    login = (github or "").strip().casefold()
    if not login:
        raise RosterError("STUDENT_NOT_FOUND", "empty GitHub login")

    matches: list[tuple[str, list[list[str]], int, str]] = []
    for title, grid in grids.items():
        header = grid[0] if grid else []
        if GITHUB_HEADER not in header:
            # Не лист группы (сводка, архив): столбца GitHub нет.
            continue
        github_col = header.index(GITHUB_HEADER) + 1

        for row in range(FIRST_DATA_ROW, len(grid) + 1):
            if normalize_text(cell_from_grid(grid, row, github_col)).casefold() != login:
                continue
            name = normalize_text(cell_from_grid(grid, row, name_col + 1))
            if not name:
                # Как в массовой проверке: строка без ФИО - не строка
                # студента, а итог или пометка под списком группы.
                continue
            matches.append((title, grid, row, name))

    if not matches:
        raise RosterError("STUDENT_NOT_FOUND", f"GitHub login {github!r} is not in the spreadsheet")
    if len(matches) > 1:
        places = ", ".join(f"{title!r} row {row}" for title, _, row, _ in matches)
        raise RosterError("STUDENT_AMBIGUOUS", f"GitHub login {github!r} is on several rows: {places}")

    title, grid, row, name = matches[0]
    labs: dict[str, str] = {}
    for short_name in lab_short_names:
        short_name = str(short_name)
        col = find_lab_column_in_grid(grid, short_name)
        if col is not None:
            labs[short_name] = str(cell_from_grid(grid, row, col))

    return StudentRecord(
        github=github.strip(),
        login=login,
        name=name,
        group=normalize_text(title),
        labs=labs,
    )


def login_in_grids(grids: dict[str, list[list[str]]], github: str) -> bool:
    """
    Whether a GitHub login is already in the GitHub column of any data row.

    Rows without a name count too: a login written anywhere in the column is
    taken. An empty login is never taken.
    """
    login = (github or "").strip().casefold()
    if not login:
        return False
    for grid in grids.values():
        header = grid[0] if grid else []
        if GITHUB_HEADER not in header:
            continue
        github_col = header.index(GITHUB_HEADER) + 1
        for row in range(FIRST_DATA_ROW, len(grid) + 1):
            if normalize_text(cell_from_grid(grid, row, github_col)).casefold() == login:
                return True
    return False


# ---------------------------------------------------------------------------
# Reading the spreadsheet
# ---------------------------------------------------------------------------


def fetch_course_grids(client, spreadsheet_id: str, info_sheet: str | None) -> dict[str, list[list[str]]]:
    """
    Read every worksheet of the course except the info sheet.

    Costs 3 Sheets API reads whatever the number of groups: open_by_key and
    worksheets() fetch the metadata, values_batch_get fetches all the grids.

    Returns:
        worksheet title -> grid, in the order of the worksheets
    """
    spreadsheet = client.open_by_key(spreadsheet_id)
    skip = normalize_text(info_sheet)
    titles = [ws.title for ws in spreadsheet.worksheets() if not skip or normalize_text(ws.title) != skip]
    if not titles:
        return {}

    resp = spreadsheet.values_batch_get([gspread.utils.absolute_range_name(t) for t in titles])
    value_ranges = resp["valueRanges"]
    if len(value_ranges) != len(titles):
        raise ValueError(f"values_batch_get returned {len(value_ranges)} ranges for {len(titles)} worksheets")
    # Пустой лист приходит без ключа "values".
    return {title: vr.get("values", []) for title, vr in zip(titles, value_ranges)}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

# spreadsheet_id -> (fetched_monotonic, fetched_wall, grids)
_grids_cache: dict[str, tuple[float, datetime, dict[str, list[list[str]]]]] = {}
# spreadsheet_id -> number of invalidate() calls; a read that started before
# an invalidate() must not put the old grids back into the cache.
_generations: dict[str, int] = {}
_cache_lock = threading.Lock()

_load_locks: dict[str, threading.Lock] = {}
_load_locks_mutex = threading.Lock()


def _load_lock(spreadsheet_id: str) -> threading.Lock:
    with _load_locks_mutex:
        lock = _load_locks.get(spreadsheet_id)
        if lock is None:
            lock = threading.Lock()
            _load_locks[spreadsheet_id] = lock
        return lock


def invalidate(spreadsheet_id: str) -> None:
    """Forget the cached grids of a spreadsheet after a grade was written. No reads."""
    spreadsheet_id = str(spreadsheet_id)
    with _cache_lock:
        _grids_cache.pop(spreadsheet_id, None)
        _generations[spreadsheet_id] = _generations.get(spreadsheet_id, 0) + 1


def reset_roster_cache() -> None:
    """Drop every cached spreadsheet and lock (used by tests)."""
    with _cache_lock:
        _grids_cache.clear()
        _generations.clear()
    with _load_locks_mutex:
        _load_locks.clear()


def _lab_short_names(course_info: dict) -> list[str]:
    labs = course_info.get("labs") or {}
    return [
        str(lab["short-name"])
        for lab in labs.values()
        if isinstance(lab, dict) and lab.get("short-name")
    ]


def _stale_allowed(age: float, allow_stale_seconds: float) -> bool:
    return allow_stale_seconds > 0 and age <= allow_stale_seconds


def _name_column(google: dict) -> int:
    value = google.get("student-name-column", DEFAULT_NAME_COLUMN)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        logger.warning(f"Invalid student-name-column {value!r}, using {DEFAULT_NAME_COLUMN}")
        return DEFAULT_NAME_COLUMN
    return value


class CourseRoster:
    """
    Finds students in course spreadsheets through a cache shared by the process.

    A whole spreadsheet is read at once and kept for ROSTER_CACHE_TTL_SECONDS;
    only one request per spreadsheet reads it at a time, the others wait for
    its result up to ROSTER_WAIT_SECONDS.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock

    def lookup(
        self,
        course_info: dict,
        github: str,
        client_factory: Callable[[], object],
        *,
        fresh: bool = False,
        allow_stale_seconds: float = 0,
    ) -> StudentRecord:
        """
        Find the signed-in student in the course spreadsheet.

        Args:
            course_info: course config (the `course` section)
            github: GitHub login taken from the signed session
            client_factory: returns an authorized gspread client; called
                only when the spreadsheet has to be read
            fresh: re-read the spreadsheet unless it was read in the last
                ROSTER_FRESH_AFTER_SECONDS (a grade may have just appeared)
            allow_stale_seconds: if Sheets fails or another request is still
                reading it, use a cached copy that is not older than this;
                0 - no copy older than ROSTER_CACHE_TTL_SECONDS

        Raises:
            RosterError: STUDENT_NOT_FOUND / STUDENT_AMBIGUOUS
            RosterUnavailable: the spreadsheet could not be read
        """
        google = course_info.get("google") or {}
        grids = self._course_grids(course_info, client_factory, fresh, allow_stale_seconds)
        return find_student_in_grids(grids, github, _name_column(google), _lab_short_names(course_info))

    def login_exists(
        self,
        course_info: dict,
        github: str,
        client_factory: Callable[[], object],
    ) -> bool | None:
        """
        Whether a GitHub login is already in the GitHub column of the course.

        Reads like lookup(fresh=True), without stale copies: a login can be
        taken by a row written a moment ago.

        Returns:
            True / False; None if the spreadsheet could not be read
        """
        if not (github or "").strip():
            return False
        try:
            grids = self._course_grids(course_info, client_factory, fresh=True, allow_stale_seconds=0)
        except RosterUnavailable:
            return None
        return login_in_grids(grids, github)

    def _course_grids(
        self,
        course_info: dict,
        client_factory: Callable[[], object],
        fresh: bool,
        allow_stale_seconds: float,
    ) -> dict[str, list[list[str]]]:
        google = course_info.get("google") or {}
        spreadsheet_id = google.get("spreadsheet")
        if not spreadsheet_id:
            logger.warning("Defense queue: course has no google.spreadsheet")
            raise RosterUnavailable("course has no google.spreadsheet")
        return self._grids(
            str(spreadsheet_id),
            google.get("info-sheet"),
            client_factory,
            max_age=ROSTER_FRESH_AFTER_SECONDS if fresh else ROSTER_CACHE_TTL_SECONDS,
            allow_stale_seconds=allow_stale_seconds,
        )

    def fetched_at(self, spreadsheet_id: str) -> datetime | None:
        """When the cached grids of a spreadsheet were read (aware, UTC), if cached."""
        with _cache_lock:
            entry = _grids_cache.get(str(spreadsheet_id))
        return entry[1] if entry is not None else None

    def _cached(self, spreadsheet_id: str):
        with _cache_lock:
            entry = _grids_cache.get(spreadsheet_id)
        if entry is None:
            return None, None
        return self.clock() - entry[0], entry[2]

    def _grids(
        self,
        spreadsheet_id: str,
        info_sheet: str | None,
        client_factory: Callable[[], object],
        max_age: float,
        allow_stale_seconds: float,
    ) -> dict[str, list[list[str]]]:
        age, grids = self._cached(spreadsheet_id)
        if grids is not None and age < max_age:
            return grids

        lock = _load_lock(spreadsheet_id)
        if not lock.acquire(timeout=ROSTER_WAIT_SECONDS):
            # Таблицу всё ещё читает другой запрос. Лучше ответить чуть
            # устаревшими данными, чем держать студента дольше, но не копией
            # любого возраста: кэш по сроку не удаляется.
            age, grids = self._cached(spreadsheet_id)
            if grids is not None and (age < ROSTER_CACHE_TTL_SECONDS or _stale_allowed(age, allow_stale_seconds)):
                return grids
            logger.warning(f"Defense queue: spreadsheet {spreadsheet_id} is still being read by another request")
            raise RosterUnavailable("spreadsheet is being read by another request")

        try:
            # Пока ждали, таблицу мог прочитать тот, кто держал замок.
            age, grids = self._cached(spreadsheet_id)
            if grids is not None and age < max_age:
                return grids

            with _cache_lock:
                generation = _generations.get(spreadsheet_id, 0)
            started = self.clock()
            started_wall = datetime.now(timezone.utc)
            try:
                fresh_grids = fetch_course_grids(client_factory(), spreadsheet_id, info_sheet)
            except Exception as e:
                logger.warning(f"Defense queue: could not read spreadsheet {spreadsheet_id}: {e!r}")
                if grids is not None and _stale_allowed(age, allow_stale_seconds):
                    return grids
                raise RosterUnavailable(str(e) or type(e).__name__) from e

            with _cache_lock:
                if _generations.get(spreadsheet_id, 0) == generation:
                    _grids_cache[spreadsheet_id] = (started, started_wall, fresh_grids)
            return fresh_grids
        finally:
            lock.release()
