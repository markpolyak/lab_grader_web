"""
Defense queue: who waits to defend which lab, and in what order (issue #62).

A queue is one *session*: a lesson of the course's schedule, a window the
teacher opened by hand, or - for a course without a schedule - the whole day.
Sessions are materialized lazily from the schedule the first time a day is
looked at (sync_day); entries are plain FIFO rows ordered by `seq`. Nothing
runs in the background and nothing reads the clock: signup windows and the
end of the day are computed from the `now` parameter, and whoever touches a
course first after a day is over closes what that day left behind
(_expire_past) - every change inside its own transaction, a read only when
there is something to close.

The queue only organizes the order of defenses. It never writes to Google
Sheets: grades stay the teacher's business (customer's answer in issue #62).
Whether a lab passed the service's check is decided by the caller from the
spreadsheet cell (lab_cell_status) before sign_up() is called.

Like grading/teams.py, this module knows nothing about FastAPI. Storage is
grading/queue_store.py; the schedule comes as `Lesson` objects from
grading/schedule/, so this module does not know which university they came
from.
"""
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from .queue_roster import StudentRecord
from .queue_store import QueueError, QueueStore
from .schedule.base import Lesson, ScheduleConfigError, SourceContext, normalize_text
from .schedule.changes import UnknownDay

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Course configuration (`course.queue`)
# ---------------------------------------------------------------------------

QUEUE_KEYS = ("enabled", "open-before-minutes", "defended-prefix", "schedule")

DEFAULT_OPEN_BEFORE_MINUTES = 15
MAX_OPEN_BEFORE_MINUTES = 180
DEFAULT_DEFENDED_PREFIX = "vv"

# Та же грамматика, что у дедлайнов (sheets_client.parse_deadline), но строго
# целиком: «UTC+3:30» или «MSK» - ошибка конфигурации, а не тихий UTC, иначе
# конец дня очереди уехал бы на 03:00 по Москве.
UTC_OFFSET_RE = re.compile(r"UTC([+-])(\d{1,2})")
MAX_UTC_OFFSET_HOURS = 14


class QueueConfigError(Exception):
    """The course's `queue` section is present but malformed."""


@dataclass(frozen=True)
class QueueSettings:
    """Parsed `queue` section of an enabled course."""
    open_before: timedelta           # signup opens this long before a lesson
    defended_prefix: str             # cell prefix meaning "already defended"
    tz: timezone                     # course timezone: days and windows are counted in it
    schedule_cfg: dict | None        # `queue.schedule`, None = one queue for the whole day


def queue_flag(course_info) -> bool:
    """
    Whether the course has the queue switched on.

    Never raises, like join_links.is_secret_lab: the course lists must not
    fail on a broken section. Problems are reported by parse_queue_config().
    """
    if not isinstance(course_info, dict):
        return False
    raw = course_info.get("queue")
    return isinstance(raw, dict) and raw.get("enabled") is True


def parse_utc_offset(text) -> timezone:
    """
    Parse the course `timezone` ("UTC+3", "UTC-5").

    Raises:
        QueueConfigError: anything else, the value is quoted in the message
    """
    match = UTC_OFFSET_RE.fullmatch(text.strip()) if isinstance(text, str) else None
    if match is None or int(match.group(2)) > MAX_UTC_OFFSET_HOURS:
        raise QueueConfigError(
            f"Для очереди нужен timezone курса в формате UTC+3 или UTC-5, сейчас: {text!r}"
        )
    hours = int(match.group(2))
    return timezone(timedelta(hours=-hours if match.group(1) == "-" else hours))


def _open_before(raw: dict) -> timedelta:
    value = raw.get("open-before-minutes", DEFAULT_OPEN_BEFORE_MINUTES)
    # bool is an int subclass, and `open-before-minutes: yes` in YAML is a bool.
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_OPEN_BEFORE_MINUTES
    ):
        raise QueueConfigError(
            f"Некорректное значение queue.open-before-minutes: {value!r} "
            f"(ожидается целое число от 0 до {MAX_OPEN_BEFORE_MINUTES})"
        )
    return timedelta(minutes=value)


def _defended_prefix(raw: dict) -> str:
    value = raw.get("defended-prefix", DEFAULT_DEFENDED_PREFIX)
    if not isinstance(value, str) or not value.strip():
        raise QueueConfigError(
            f"Некорректное значение queue.defended-prefix: {value!r} (ожидается непустая строка)"
        )
    return value.strip()


def _check_schedule(schedule_cfg: dict, tz: timezone) -> None:
    # Реестр импортирует модули вузов по имени источника, поэтому подгружаем
    # его только тогда, когда есть что проверять.
    from .schedule import build_schedule_source

    try:
        # snapshots=None - режим проверки: только разбор, без сети и без БД.
        build_schedule_source(schedule_cfg, SourceContext(snapshots=None, course_tz=tz))
    except ScheduleConfigError as e:
        raise QueueConfigError(f"queue.schedule: {e}") from e


def parse_queue_config(course_info: dict) -> QueueSettings | None:
    """
    Parse and validate the course's `queue` section, its schedule included (without network).

    Returns:
        QueueSettings, or None when the section is missing or the queue is off

    Raises:
        QueueConfigError: malformed section or unknown key, missing or bad
        course `timezone`, schedule section rejected by its source
    """
    raw = course_info.get("queue") if isinstance(course_info, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise QueueConfigError("Секция queue курса должна быть словарём")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise QueueConfigError(
            f"Некорректное значение queue.enabled: {enabled!r} (ожидается true или false)"
        )
    if not enabled:
        return None

    # Опечатка в ключе (opens-before-minutes) иначе молча дала бы значение
    # по умолчанию.
    unknown = [str(key) for key in raw if key not in QUEUE_KEYS]
    if unknown:
        raise QueueConfigError(
            f"Неизвестные ключи в секции queue: {', '.join(repr(k) for k in unknown)} "
            f"(допустимы: {', '.join(QUEUE_KEYS)})"
        )

    open_before = _open_before(raw)
    defended_prefix = _defended_prefix(raw)
    tz = parse_utc_offset(course_info.get("timezone"))

    schedule_cfg = raw.get("schedule")
    if schedule_cfg is not None:
        if not isinstance(schedule_cfg, dict):
            raise QueueConfigError("Секция queue.schedule должна быть словарём")
        _check_schedule(schedule_cfg, tz)

    return QueueSettings(
        open_before=open_before,
        defended_prefix=defended_prefix,
        tz=tz,
        schedule_cfg=schedule_cfg,
    )


def warn_about_queue_config(filename: str, course_info: dict) -> None:
    """
    Log problems in the course's `queue` section (see main.warn_about_join_links).

    Never raises: it runs at startup for every course, and one typo must not
    keep the service (or the test suite importing main) from starting. A
    malformed section is answered per request with QUEUE_MISCONFIGURED.
    """
    try:
        parse_queue_config(course_info)
    except QueueConfigError as e:
        logger.warning(f"{filename}: очередь на защиту - {e}")
    except Exception as e:
        logger.warning(f"{filename}: очередь на защиту - не удалось проверить конфигурацию: {e!r}")


# ---------------------------------------------------------------------------
# Lab cell
# ---------------------------------------------------------------------------

LAB_PASSED = "passed"
LAB_DEFENDED = "defended"
LAB_NOT_PASSED = "not_passed"


def lab_cell_status(value, defended_prefix: str = DEFAULT_DEFENDED_PREFIX) -> str:
    """
    What a spreadsheet cell says about a lab, for the queue.

    `v`, `v-1`, `v@10`, `v@10,5-3` - passed the service's check, the student
    may sign up; `defended_prefix` (`vv`, `vv-2`) - already defended;
    anything else, manual marks like `x`, `н/я` or `-` included, is not
    passed. Stricter than `not can_overwrite_cell(value)` on purpose: the
    queue trusts only what the service itself wrote.

    Returns:
        LAB_PASSED, LAB_DEFENDED or LAB_NOT_PASSED
    """
    cell = ("" if value is None else str(value)).strip().casefold()
    prefix = (defended_prefix or "").strip().casefold()
    if prefix and cell.startswith(prefix):
        return LAB_DEFENDED
    if cell.startswith("v"):
        return LAB_PASSED
    return LAB_NOT_PASSED


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

# Entry status
WAITING = "waiting"
CALLED = "called"
DEFENDED = "defended"
NOT_DEFENDED = "not_defended"
NO_SHOW = "no_show"
LEFT = "left"            # the student left the queue
EXPIRED = "expired"      # the day (or the teacher's reception) ended first

OUTCOMES = frozenset({DEFENDED, NOT_DEFENDED, NO_SHOW})

# Session kind
KIND_SCHEDULE = "schedule"
KIND_MANUAL = "manual"
KIND_ALL_DAY = "all_day"

# Signup state of a session (values of the "signup" field, not error codes)
SIGNUP_UPCOMING = "upcoming"
SIGNUP_OPEN = "open"
SIGNUP_CLOSED = "closed"


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"datetime must be timezone-aware: {value!r}")
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    """
    A moment as the queue stores and returns it: UTC ISO-8601 to the second.

    One format for every time in sessions and entries, so that the strings
    compare in SQL like the times do. Schedule snapshots keep microseconds
    (queue_store) and are never compared with these.
    """
    return _utc(value).isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def course_day(now: datetime, tz: timezone) -> date:
    """The course's calendar day at `now`."""
    return _utc(now).astimezone(tz).date()


def day_start(day: date, tz: timezone) -> datetime:
    """00:00 of `day` in the course timezone."""
    return datetime.combine(day, time(0), tzinfo=tz)


def parse_closes_at(value: str | None, now: datetime, tz: timezone) -> datetime | None:
    """
    "HH:MM" of the teacher's manual window -> that moment today in the course timezone.

    Returns:
        None for None or "" (the window lasts until the end of the day)

    Raises:
        QueueError: INVALID_TIME for anything that is not HH:MM
    """
    if value is None or value == "":
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip()) if isinstance(value, str) else None
    if match is None or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise QueueError("INVALID_TIME", f"expected HH:MM, got {value!r}")
    return datetime.combine(
        course_day(now, tz), time(int(match.group(1)), int(match.group(2))), tzinfo=tz
    )


# Период истории: даты только из этого диапазона (на краях календаря
# datetime переполняется), не длиннее HISTORY_MAX_DAYS дней, в ответе не
# больше HISTORY_LIMIT записей.
HISTORY_FIRST_DAY = date(2000, 1, 1)
HISTORY_LAST_DAY = date(2100, 12, 31)
HISTORY_MAX_DAYS = 400
HISTORY_LIMIT = 2000


def parse_history_day(value) -> date:
    """A day of the history period (date or YYYY-MM-DD); QueueError INVALID_PERIOD otherwise."""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise QueueError("INVALID_PERIOD") from None
    if isinstance(value, datetime) or not isinstance(value, date):
        raise QueueError("INVALID_PERIOD")
    if not HISTORY_FIRST_DAY <= value <= HISTORY_LAST_DAY:
        raise QueueError("INVALID_PERIOD")
    return value


def _day_str(day) -> str:
    if isinstance(day, datetime):
        raise TypeError("day must be a date, not a datetime")
    return day.isoformat() if isinstance(day, date) else str(day)


# ---------------------------------------------------------------------------
# Rows -> JSON-ready dicts
# ---------------------------------------------------------------------------

def _session_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "day": row["day"],
        "kind": row["kind"],
        "lesson_key": row["lesson_key"],
        "lesson_type": row["lesson_type"],
        "subject": row["subject"],
        "groups": json.loads(row["groups"]),
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "signup_opens_at": row["signup_opens_at"],
        "signup_closes_at": row["signup_closes_at"],
        "day_ends_at": row["day_ends_at"],
        "closed_at": row["closed_at"],
        "created_at": row["created_at"],
    }


def _entry_out(row: sqlite3.Row, place: int | None) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "place": place,
        "status": row["status"],
        "name": row["student_name"],
        "group": row["group_name"],
        "github": row["github"],
        "lab": row["lab_name"],
        "lab_key": row["lab_key"],
        "created_at": row["created_at"],
        "called_at": row["called_at"],
        "finished_at": row["finished_at"],
    }


def _waiting_places(entries) -> dict[int, int]:
    """entry id -> place among the waiting ones (entries ordered by seq, id)."""
    places = {}
    for row in entries:
        if row["status"] == WAITING:
            places[row["id"]] = len(places) + 1
    return places


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

def _expire_past(conn: sqlite3.Connection, course_id: str, now: datetime) -> None:
    """
    Close the entries of days that are over: waiting/called -> expired.

    The first step of every change (and of a read that found _has_overdue):
    there is no background job, so whoever touches the course first after
    midnight closes the previous day, and the closing time is the end of
    that day, not the moment of the request.
    """
    conn.execute(
        """
        UPDATE entries
        SET status = 'expired',
            finished_at = (SELECT s.day_ends_at FROM sessions s WHERE s.id = entries.session_id)
        WHERE course_id = ?
          AND status IN ('waiting', 'called')
          AND session_id IN (SELECT id FROM sessions WHERE course_id = ? AND day_ends_at <= ?)
        """,
        (course_id, course_id, iso_utc(now)),
    )


def _has_overdue(conn: sqlite3.Connection, course_id: str, now: datetime) -> bool:
    """Whether _expire_past has anything to close: a cheap check by the partial index."""
    return conn.execute(
        """
        SELECT 1 FROM entries e JOIN sessions s ON s.id = e.session_id
        WHERE e.course_id = ? AND e.status IN ('waiting', 'called') AND s.day_ends_at <= ?
        LIMIT 1
        """,
        (course_id, iso_utc(now)),
    ).fetchone() is not None


def _missing_lessons(conn: sqlite3.Connection, course_id: str, day: str, lessons) -> list[Lesson]:
    """Today's lessons that have no session yet, each key once."""
    if not lessons:
        return []
    existing = {
        row["lesson_key"]
        for row in conn.execute(
            "SELECT lesson_key FROM sessions WHERE course_id = ? AND day = ? AND lesson_key IS NOT NULL",
            (course_id, day),
        )
    }
    missing = {}
    for lesson in lessons:
        if lesson.key not in existing:
            missing.setdefault(lesson.key, lesson)
    return list(missing.values())


def _create_sessions(
    conn: sqlite3.Connection,
    course_id: str,
    settings: QueueSettings,
    lessons: list[Lesson],
    now: datetime,
) -> None:
    # Без секции schedule единственное «занятие» - весь день.
    kind = KIND_ALL_DAY if settings.schedule_cfg is None else KIND_SCHEDULE
    day = course_day(now, settings.tz)
    start = day_start(day, settings.tz)
    end = start + timedelta(days=1)
    for lesson in lessons:
        if kind == KIND_ALL_DAY:
            opens, closes = start, end
        else:
            opens = max(lesson.starts_at - settings.open_before, start)
            closes = min(lesson.ends_at, end)
        # OR IGNORE: ту же сессию мог только что создать другой процесс.
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions (
                course_id, day, kind, lesson_key, lesson_type, subject, groups,
                starts_at, ends_at, signup_opens_at, signup_closes_at, day_ends_at,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                course_id, day.isoformat(), kind, lesson.key,
                lesson.lesson_type, lesson.subject, json.dumps(list(lesson.groups)),
                iso_utc(lesson.starts_at), iso_utc(lesson.ends_at),
                iso_utc(opens), iso_utc(closes), iso_utc(end), iso_utc(now),
            ),
        )


def _place(conn: sqlite3.Connection, row: sqlite3.Row) -> int | None:
    """1 + the number of waiting entries before this one in its session."""
    if row["status"] != WAITING:
        return None
    ahead = conn.execute(
        """
        SELECT COUNT(*) FROM entries
        WHERE session_id = ? AND status = 'waiting'
          AND (seq < ? OR (seq = ? AND id < ?))
        """,
        (row["session_id"], row["seq"], row["seq"], row["id"]),
    ).fetchone()[0]
    return ahead + 1


def _row_by_id(conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Row | None:
    try:
        return conn.execute(sql, params).fetchone()
    except OverflowError:
        # id за пределами SQLite INTEGER (больше 2**63 - 1): такой строки
        # нет, а sqlite3 ответил бы исключением вместо пустого результата.
        return None


def _entry_row(conn: sqlite3.Connection, course_id: str, entry_id) -> sqlite3.Row:
    row = _row_by_id(conn, "SELECT * FROM entries WHERE id = ? AND course_id = ?", (entry_id, course_id))
    if row is None:
        raise QueueError("ENTRY_NOT_FOUND")
    return row


def _session_row(conn: sqlite3.Connection, course_id: str, session_id) -> sqlite3.Row:
    row = _row_by_id(conn, "SELECT * FROM sessions WHERE id = ? AND course_id = ?", (session_id, course_id))
    if row is None:
        raise QueueError("SESSION_NOT_FOUND")
    return row


def _active_row(conn: sqlite3.Connection, course_id: str, login: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM entries WHERE course_id = ? AND login = ? AND status IN ('waiting', 'called')",
        (course_id, login),
    ).fetchone()


def _entry_dict(conn: sqlite3.Connection, entry_id: int) -> dict:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return _entry_out(row, _place(conn, row))


def _session_dict(conn: sqlite3.Connection, session_id: int) -> dict:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _session_out(row)


def _day_sessions(conn: sqlite3.Connection, course_id: str, day: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sessions WHERE course_id = ? AND day = ? ORDER BY starts_at, id",
        (course_id, day),
    ).fetchall()


def _day_entries(conn: sqlite3.Connection, course_id: str, day: str,
                 active_only: bool = False) -> dict[int, list[sqlite3.Row]]:
    """
    session id -> its entries in queue order (seq, id).

    active_only: only waiting and called. The student page needs nothing
    else, and it is polled by the whole group at once: sqlite3 gives up the
    GIL on every fetched row, so thirty threads reading every finished entry
    of the day queue up behind each other.
    """
    status = "AND e.status IN ('waiting', 'called')" if active_only else ""
    rows = conn.execute(
        f"""
        SELECT e.* FROM entries e JOIN sessions s ON s.id = e.session_id
        WHERE s.course_id = ? AND s.day = ? {status}
        ORDER BY e.session_id, e.seq, e.id
        """,
        (course_id, day),
    ).fetchall()
    by_session: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        by_session.setdefault(row["session_id"], []).append(row)
    return by_session


def normalize_login(login: str | None) -> str:
    """A GitHub login as the queue stores it: logins are case-insensitive."""
    return (login or "").strip().casefold()


def _clean_groups(groups) -> list[str]:
    if not isinstance(groups, (list, tuple)):
        raise QueueError("INVALID_GROUPS")
    cleaned = set()
    for group in groups:
        if not isinstance(group, str) or not normalize_text(group):
            raise QueueError("INVALID_GROUPS")
        cleaned.add(normalize_text(group))
    return sorted(cleaned)


def _in_schedule(session: dict, current_keys: set[str] | UnknownDay | None, group: str | None = None) -> bool:
    """
    False for a session made from the schedule whose lesson is gone from today's one.

    The whole-day queue counts too: its key "all-day" disappears as soon as
    the course gets a schedule, and from then on signups follow the new
    schedule. Only a manual session does not depend on the schedule.
    When only the day's cancellations are known (UnknownDay), the lesson is
    gone for `group` if it is cancelled, and for everyone if all the
    session's groups are.
    """
    if session["kind"] == KIND_MANUAL or current_keys is None:
        return True
    if isinstance(current_keys, UnknownDay):
        cancelled = current_keys.cancelled(
            date.fromisoformat(session["day"]), _parse(session["starts_at"]), session["groups"]
        )
        return not cancelled or (group not in cancelled and cancelled != set(session["groups"]))
    return session["lesson_key"] in current_keys


def _is_shown(session: dict, entries: list, current_keys: set[str] | UnknownDay | None) -> bool:
    # Занятие пропало из расписания, и на него никто не записывался -
    # показывать нечего.
    return bool(entries) or _in_schedule(session, current_keys)


def _waiting_behind(conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row | None:
    """The waiting entry right behind this one in its session."""
    return conn.execute(
        """
        SELECT * FROM entries
        WHERE session_id = ? AND status = 'waiting'
          AND (seq > ? OR (seq = ? AND id > ?))
        ORDER BY seq, id LIMIT 1
        """,
        (row["session_id"], row["seq"], row["seq"], row["id"]),
    ).fetchone()


def _my_entry(conn: sqlite3.Connection, course_id: str, login: str) -> dict | None:
    """The student's active entry as the student page shows it."""
    row = _active_row(conn, course_id, login)
    if row is None:
        return None
    can_skip = row["status"] == WAITING and _waiting_behind(conn, row) is not None
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "place": _place(conn, row),
        "lab": row["lab_name"],
        "created_at": row["created_at"],
        "called_at": row["called_at"],
        "can_skip": can_skip,
    }


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

class DefenseQueue:
    """
    Queue operations of every course, on top of one QueueStore.

    Each change is one BEGIN IMMEDIATE transaction: two requests to the same
    queue, from two threads or two processes, run one after the other and see
    each other's result. A refusal is a QueueError with a stable code and
    changes nothing. Views only read and take the write lock for a moment
    only when there is something to write (see _reading).
    """

    def __init__(self, store: QueueStore):
        self._store = store

    @contextmanager
    def _reading(
        self,
        course_id: str,
        now: datetime,
        settings: QueueSettings | None = None,
        lessons: list[Lesson] | None = None,
    ):
        """
        A read transaction for the views, with what sync_day does folded in.

        Usually that is one read and nothing else. Only when a day that is
        over still has waiting/called entries, or one of today's `lessons`
        (with `settings`) has no session yet, a short BEGIN IMMEDIATE first
        closes the past days and creates the missing sessions, and the read
        starts after it.
        """
        day = None if settings is None else course_day(now, settings.tz).isoformat()
        with self._store.read() as conn:
            missing = [] if settings is None else _missing_lessons(conn, course_id, day, lessons)
            if not missing and not _has_overdue(conn, course_id, now):
                yield conn
                return
        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            if missing:
                _create_sessions(conn, course_id, settings, missing, now)
        with self._store.read() as conn:
            yield conn

    # -----------------------------------------------------------------------
    # Sessions of the day
    # -----------------------------------------------------------------------

    def sync_day(
        self,
        course_id: str,
        settings: QueueSettings,
        lessons: list[Lesson] | None,
        now: datetime,
    ) -> list[dict]:
        """
        Create the missing sessions of today's lessons and return the day's sessions.

        A session is never deleted or moved: when the schedule changes, the
        old one keeps its entries until the end of the day (it only stops
        taking new ones, see signup_state) and the new lesson gets its own.

        Args:
            lessons: today's lessons; None when the schedule is unknown right
                now - nothing is created then

        Returns:
            every session of the day, manual ones included, ordered by start
        """
        day = course_day(now, settings.tz).isoformat()
        with self._reading(course_id, now, settings, lessons) as conn:
            return [_session_out(row) for row in _day_sessions(conn, course_id, day)]

    @staticmethod
    def signup_state(
        session: dict,
        now: datetime,
        current_keys: set[str] | UnknownDay | None = None,
        group: str | None = None,
    ) -> str:
        """
        SIGNUP_UPCOMING, SIGNUP_OPEN or SIGNUP_CLOSED for a session dict.

        Signup is open on [signup_opens_at, signup_closes_at), unless the
        teacher closed it or - for any session but a manual one - the lesson
        is no longer in the schedule (`current_keys` = keys of today's
        lessons, None when the schedule is unknown and nothing can be
        concluded, UnknownDay when only the day's cancellations are known:
        then it is closed to a cancelled `group` too, see _in_schedule).
        """
        if session["closed_at"] or not _in_schedule(session, current_keys, group):
            return SIGNUP_CLOSED
        now = _utc(now)
        if now < _parse(session["signup_opens_at"]):
            return SIGNUP_UPCOMING
        if now < _parse(session["signup_closes_at"]):
            return SIGNUP_OPEN
        return SIGNUP_CLOSED

    # -----------------------------------------------------------------------
    # Student
    # -----------------------------------------------------------------------

    def sign_up(
        self,
        course_id: str,
        session_id: int,
        student: StudentRecord,
        lab_key: str,
        lab_name: str,
        now: datetime,
        current_keys: set[str] | UnknownDay | None = None,
    ) -> dict:
        """
        Put a student at the end of a session's queue.

        The caller has already checked the lab cell (lab_cell_status) and
        takes `student` from the spreadsheet, not from the request.

        Returns:
            the new entry (see day_view for the fields)

        Raises:
            QueueError: SESSION_NOT_FOUND, SIGNUP_CLOSED, NOT_YOUR_GROUP,
            ALREADY_IN_QUEUE, LAB_ALREADY_DEFENDED
        """
        login = normalize_login(student.login)
        group = normalize_text(student.group)
        lab_key = str(lab_key)

        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            session = _session_out(_session_row(conn, course_id, session_id))

            if self.signup_state(session, now, current_keys, group) != SIGNUP_OPEN:
                raise QueueError("SIGNUP_CLOSED")
            if session["groups"] and group not in session["groups"]:
                raise QueueError("NOT_YOUR_GROUP")
            if _active_row(conn, course_id, login) is not None:
                raise QueueError("ALREADY_IN_QUEUE")
            defended = conn.execute(
                "SELECT 1 FROM entries WHERE course_id = ? AND login = ? AND lab_key = ? AND status = 'defended'",
                (course_id, login, lab_key),
            ).fetchone()
            if defended is not None:
                raise QueueError("LAB_ALREADY_DEFENDED")

            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM entries WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0]
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO entries (
                        session_id, course_id, login, github, student_name, group_name,
                        lab_key, lab_name, seq, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?)
                    """,
                    (
                        session["id"], course_id, login, student.github, student.name, group,
                        lab_key, str(lab_name), seq, iso_utc(now),
                    ),
                )
            except sqlite3.IntegrityError as e:
                # Частичный UNIQUE-индекс: вторая активная запись студента.
                raise QueueError("ALREADY_IN_QUEUE") from e
            return _entry_dict(conn, cursor.lastrowid)

    def leave(self, course_id: str, login: str, now: datetime) -> dict:
        """
        The student leaves the queue (waiting -> left).

        A called student cannot leave: the teacher marks the outcome, so
        that the history shows how the defense ended.

        Raises:
            QueueError: NOT_IN_QUEUE, ALREADY_CALLED
        """
        login = normalize_login(login)
        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            row = _active_row(conn, course_id, login)
            if row is None:
                raise QueueError("NOT_IN_QUEUE")
            if row["status"] == CALLED:
                raise QueueError("ALREADY_CALLED")
            conn.execute(
                "UPDATE entries SET status = 'left', finished_at = ? WHERE id = ?",
                (iso_utc(now), row["id"]),
            )
            return _entry_dict(conn, row["id"])

    def skip(self, course_id: str, login: str, now: datetime) -> dict:
        """
        Let the next student go first: swap places with the one right behind.

        Moving forward is not allowed (issue #62).

        Raises:
            QueueError: NOT_IN_QUEUE, ALREADY_CALLED, NOTHING_TO_SKIP
        """
        login = normalize_login(login)
        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            row = _active_row(conn, course_id, login)
            if row is None:
                raise QueueError("NOT_IN_QUEUE")
            if row["status"] == CALLED:
                raise QueueError("ALREADY_CALLED")
            behind = _waiting_behind(conn, row)
            if behind is None:
                raise QueueError("NOTHING_TO_SKIP")
            conn.execute("UPDATE entries SET seq = ? WHERE id = ?", (behind["seq"], row["id"]))
            conn.execute("UPDATE entries SET seq = ? WHERE id = ?", (row["seq"], behind["id"]))
            return _entry_dict(conn, row["id"])

    # -----------------------------------------------------------------------
    # Teacher
    # -----------------------------------------------------------------------

    def call_next(
        self,
        course_id: str,
        session_id: int,
        now: datetime,
        expected_entry_id: int | None = None,
    ) -> dict:
        """
        Call the head of the queue (waiting -> called).

        Several students may be called at once (parallel defenses). With
        `expected_entry_id` - the head the teacher sees on the page - a
        repeated request or a second tab never calls an extra student: if
        that entry is already called, it is returned unchanged; if someone
        else is at the head now, the call is refused.

        Raises:
            QueueError: SESSION_NOT_FOUND, QUEUE_EMPTY, QUEUE_CHANGED
        """
        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            session = _session_row(conn, course_id, session_id)

            if expected_entry_id is not None:
                expected = _row_by_id(
                    conn,
                    "SELECT * FROM entries WHERE id = ? AND session_id = ?",
                    (expected_entry_id, session["id"]),
                )
                if expected is not None and expected["status"] == CALLED:
                    return _entry_out(expected, None)

            head = conn.execute(
                "SELECT * FROM entries WHERE session_id = ? AND status = 'waiting' ORDER BY seq, id LIMIT 1",
                (session["id"],),
            ).fetchone()
            if head is None:
                raise QueueError("QUEUE_EMPTY")
            if expected_entry_id is not None and head["id"] != expected_entry_id:
                raise QueueError("QUEUE_CHANGED")

            conn.execute(
                "UPDATE entries SET status = 'called', called_at = ? WHERE id = ?",
                (iso_utc(now), head["id"]),
            )
            return _entry_dict(conn, head["id"])

    def set_outcome(self, course_id: str, entry_id: int, outcome: str, now: datetime) -> dict:
        """
        Record how a defense ended: defended, not_defended or no_show.

        Allowed for a called entry, for a fix of an outcome already set, and
        for a called entry that expired unmarked at the end of the day. The
        same outcome twice changes nothing. A fix keeps the time of the first
        outcome and a late mark the end of that day: the defense is not dated
        by the click. "defended" is refused while the student is queued again
        for the same lab.

        Raises:
            QueueError: INVALID_OUTCOME, ENTRY_NOT_FOUND, ENTRY_NOT_CALLED,
            ALREADY_IN_QUEUE
        """
        if not isinstance(outcome, str) or outcome not in OUTCOMES:
            raise QueueError("INVALID_OUTCOME")

        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            row = _entry_row(conn, course_id, entry_id)
            status = row["status"]
            if status == outcome:
                return _entry_out(row, None)
            if not (
                status == CALLED
                or status in OUTCOMES
                or (status == EXPIRED and row["called_at"] is not None)
            ):
                raise QueueError("ENTRY_NOT_CALLED")
            if outcome == DEFENDED:
                active = _active_row(conn, course_id, row["login"])
                if active is not None and active["id"] != row["id"] and active["lab_key"] == row["lab_key"]:
                    raise QueueError("ALREADY_IN_QUEUE")
            finished = iso_utc(now) if status == CALLED or row["finished_at"] is None else row["finished_at"]
            conn.execute(
                "UPDATE entries SET status = ?, finished_at = ? WHERE id = ?",
                (outcome, finished, row["id"]),
            )
            return _entry_dict(conn, row["id"])

    def return_to_queue(self, course_id: str, entry_id: int, now: datetime) -> dict:
        """
        Put a called or no-show student back into the queue at their old place.

        Only on the same day. Refused if the student has meanwhile signed up
        again (one active entry per course) or defended this lab in another
        entry - the same rule as in sign_up.

        Raises:
            QueueError: ENTRY_NOT_FOUND, DAY_OVER, ENTRY_NOT_CALLED,
            LAB_ALREADY_DEFENDED, ALREADY_IN_QUEUE
        """
        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            row = _entry_row(conn, course_id, entry_id)
            session = _session_row(conn, course_id, row["session_id"])
            if _parse(session["day_ends_at"]) <= _utc(now):
                raise QueueError("DAY_OVER")
            if row["status"] not in (CALLED, NO_SHOW):
                raise QueueError("ENTRY_NOT_CALLED")
            defended = conn.execute(
                """
                SELECT 1 FROM entries
                WHERE course_id = ? AND login = ? AND lab_key = ? AND status = 'defended' AND id <> ?
                """,
                (course_id, row["login"], row["lab_key"], row["id"]),
            ).fetchone()
            if defended is not None:
                raise QueueError("LAB_ALREADY_DEFENDED")
            try:
                conn.execute(
                    "UPDATE entries SET status = 'waiting', called_at = NULL, finished_at = NULL WHERE id = ?",
                    (row["id"],),
                )
            except sqlite3.IntegrityError as e:
                raise QueueError("ALREADY_IN_QUEUE") from e
            return _entry_dict(conn, row["id"])

    def open_manual(
        self,
        course_id: str,
        settings: QueueSettings,
        groups: list[str],
        closes_at: datetime | None,
        now: datetime,
    ) -> dict:
        """
        Open signup by hand from now on (an extra or moved lesson).

        One manual session per set of groups per day: opening again for the
        same groups changes the window of the existing one (extends, shortens
        or reopens it), so those waiting keep their places instead of a
        second queue starting from place 1.

        Args:
            groups: groups allowed to sign up; [] = every group of the course
            closes_at: end of the signup window, None = end of the day

        Returns:
            the session (fields as in sync_day)

        Raises:
            QueueError: INVALID_GROUPS, INVALID_TIME
        """
        groups = _clean_groups(groups)
        day = course_day(now, settings.tz)
        end = day_start(day, settings.tz) + timedelta(days=1)
        closes = end if closes_at is None else closes_at
        if not _utc(now) < _utc(closes) <= end:
            raise QueueError("INVALID_TIME")

        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            for row in conn.execute(
                "SELECT * FROM sessions WHERE course_id = ? AND day = ? AND kind = 'manual' ORDER BY id DESC",
                (course_id, day.isoformat()),
            ).fetchall():
                if json.loads(row["groups"]) == groups:
                    # ends_at ручной сессии - конец её окна записи, как при создании.
                    conn.execute(
                        "UPDATE sessions SET signup_closes_at = ?, ends_at = ?, closed_at = NULL WHERE id = ?",
                        (iso_utc(closes), iso_utc(closes), row["id"]),
                    )
                    return _session_dict(conn, row["id"])

            cursor = conn.execute(
                """
                INSERT INTO sessions (
                    course_id, day, kind, lesson_key, groups,
                    starts_at, ends_at, signup_opens_at, signup_closes_at, day_ends_at, created_at
                ) VALUES (?, ?, 'manual', NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    course_id, day.isoformat(), json.dumps(groups),
                    iso_utc(now), iso_utc(closes), iso_utc(now), iso_utc(closes), iso_utc(end), iso_utc(now),
                ),
            )
            return _session_dict(conn, cursor.lastrowid)

    def close_signup(self, course_id: str, session_id: int, now: datetime) -> dict:
        """
        Stop new signups; those already waiting stay and can be called.

        Raises:
            QueueError: SESSION_NOT_FOUND
        """
        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            session = _session_row(conn, course_id, session_id)
            conn.execute(
                "UPDATE sessions SET closed_at = COALESCE(closed_at, ?) WHERE id = ?",
                (iso_utc(now), session["id"]),
            )
            return _session_dict(conn, session["id"])

    def reopen_signup(self, course_id: str, session_id: int, now: datetime) -> dict:
        """
        Undo close_signup, while the session's signup window has not ended.

        Raises:
            QueueError: SESSION_NOT_FOUND, SIGNUP_CLOSED
        """
        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            session = _session_row(conn, course_id, session_id)
            if _utc(now) >= _parse(session["signup_closes_at"]):
                raise QueueError("SIGNUP_CLOSED")
            conn.execute("UPDATE sessions SET closed_at = NULL WHERE id = ?", (session["id"],))
            return _session_dict(conn, session["id"])

    def finish_session(self, course_id: str, session_id: int, now: datetime) -> dict:
        """
        "Завершить приём": close signup and expire everyone still waiting.

        Called students stay called, so that their outcome can be marked.

        Raises:
            QueueError: SESSION_NOT_FOUND
        """
        with self._store.transaction() as conn:
            _expire_past(conn, course_id, now)
            session = _session_row(conn, course_id, session_id)
            conn.execute(
                "UPDATE sessions SET closed_at = COALESCE(closed_at, ?) WHERE id = ?",
                (iso_utc(now), session["id"]),
            )
            conn.execute(
                "UPDATE entries SET status = 'expired', finished_at = ? WHERE session_id = ? AND status = 'waiting'",
                (iso_utc(now), session["id"]),
            )
            return _session_dict(conn, session["id"])

    # -----------------------------------------------------------------------
    # Views
    # -----------------------------------------------------------------------

    def day_view(
        self,
        course_id: str,
        day,
        now: datetime,
        current_keys: set[str] | UnknownDay | None = None,
        *,
        settings: QueueSettings | None = None,
        lessons: list[Lesson] | None = None,
    ) -> list[dict]:
        """
        The day's sessions with every entry, for the teacher's page.

        A session has the fields of sync_day plus `signup`, `in_schedule`,
        `waiting_count`, `called_count` and `entries` in queue order; an entry
        has id, session_id, place (waiting only), status, name, group, github,
        lab, lab_key, created_at, called_at, finished_at. With `settings` and
        today's `lessons` the view also does what sync_day does, in one read.
        """
        day = _day_str(day)
        with self._reading(course_id, now, settings, lessons) as conn:
            sessions = _day_sessions(conn, course_id, day)
            entries = _day_entries(conn, course_id, day)

        result = []
        for row in sessions:
            session = _session_out(row)
            rows = entries.get(session["id"], [])
            if not _is_shown(session, rows, current_keys):
                continue
            places = _waiting_places(rows)
            session.update(
                signup=self.signup_state(session, now, current_keys),
                in_schedule=_in_schedule(session, current_keys),
                waiting_count=len(places),
                called_count=sum(1 for r in rows if r["status"] == CALLED),
                entries=[_entry_out(r, places.get(r["id"])) for r in rows],
            )
            result.append(session)
        return result

    def student_view(
        self,
        course_id: str,
        day,
        now: datetime,
        *,
        login: str | None = None,
        group: str | None = None,
        current_keys: set[str] | UnknownDay | None = None,
        settings: QueueSettings | None = None,
        lessons: list[Lesson] | None = None,
    ) -> dict:
        """
        What a student's page shows: {"sessions", "my_entry", "my_last_result"}.

        Names are listed only to a signed-in student (`login`, `group` from
        the queue cookie) and only in the sessions of their group (`for_me`):
        the called ones in the order of calling, then the waiting ones by
        place, without logins. An anonymous visitor gets only the counters
        (`entries: None`).
        `my_entry` is the active entry with its place and `can_skip`,
        `my_last_result` the latest finished entry of the day. `settings`
        and `lessons` - as in day_view.
        """
        day = _day_str(day)
        login = normalize_login(login) if login else None
        group = normalize_text(group) if group else None

        with self._reading(course_id, now, settings, lessons) as conn:
            sessions = _day_sessions(conn, course_id, day)
            entries = _day_entries(conn, course_id, day, active_only=True)
            my_entry = last = None
            if login:
                my_entry = _my_entry(conn, course_id, login)
                last = conn.execute(
                    """
                    SELECT e.* FROM entries e JOIN sessions s ON s.id = e.session_id
                    WHERE e.course_id = ? AND e.login = ? AND s.day = ?
                      AND e.status NOT IN ('waiting', 'called')
                    ORDER BY e.finished_at DESC, e.id DESC LIMIT 1
                    """,
                    (course_id, login, day),
                ).fetchone()

        out_sessions = []
        for row in sessions:
            session = _session_out(row)
            rows = entries.get(session["id"], [])
            if not _is_shown(session, rows, current_keys):
                continue
            places = _waiting_places(rows)
            called = [r for r in rows if r["status"] == CALLED]
            waiting = [r for r in rows if r["status"] == WAITING]
            for_me = login is not None and (not session["groups"] or group in session["groups"])

            listed = None
            if for_me:
                # Сначала вызванные (в порядке вызова), потом ждущие по месту.
                called.sort(key=lambda r: (r["called_at"] or "", r["id"]))
                listed = [
                    {
                        "place": places.get(r["id"]),
                        "status": r["status"],
                        "name": r["student_name"],
                        "group": r["group_name"],
                        "lab": r["lab_name"],
                        "is_me": r["login"] == login,
                    }
                    for r in called + waiting
                ]

            out_sessions.append({
                "id": session["id"],
                "kind": session["kind"],
                "lesson_type": session["lesson_type"],
                "subject": session["subject"],
                "groups": session["groups"],
                "starts_at": session["starts_at"],
                "ends_at": session["ends_at"],
                "signup_opens_at": session["signup_opens_at"],
                "signup_closes_at": session["signup_closes_at"],
                "signup": self.signup_state(session, now, current_keys, group),
                "for_me": for_me,
                "waiting_count": len(waiting),
                "called_count": len(called),
                "entries": listed,
            })

        return {
            "sessions": out_sessions,
            "my_entry": my_entry,
            "my_last_result": None if last is None else {
                "status": last["status"],
                "lab": last["lab_name"],
                "finished_at": last["finished_at"],
            },
        }

    def history(
        self,
        course_id: str,
        day_from,
        day_to,
        tz: timezone,
        now: datetime,
        *,
        group: str | None = None,
        query: str | None = None,
        lab_key: str | None = None,
        limit: int = HISTORY_LIMIT,
    ) -> tuple[list[dict], bool]:
        """
        Entries signed up between two days (inclusive, course timezone), newest first.

        An item has the entry fields of day_view plus `day`, `kind`,
        `lesson_type`, `subject` and `session_starts_at` of its session.
        `query` is a case-insensitive substring of the full name or the login.

        Returns:
            (at most `limit` entries, True when more of them matched)

        Raises:
            QueueError: INVALID_PERIOD - a day that is not a date or is out
            of HISTORY_FIRST_DAY..HISTORY_LAST_DAY, or a period longer than
            HISTORY_MAX_DAYS
        """
        day_from, day_to = parse_history_day(day_from), parse_history_day(day_to)
        if (day_to - day_from).days >= HISTORY_MAX_DAYS:
            raise QueueError("INVALID_PERIOD")
        lower = iso_utc(day_start(day_from, tz))
        upper = iso_utc(day_start(day_to, tz) + timedelta(days=1))
        group = normalize_text(group) if group else None
        lab_key = str(lab_key) if lab_key not in (None, "") else None
        needle = normalize_text(query).casefold() if query else None

        rows, truncated = [], False
        with self._reading(course_id, now) as conn:
            cursor = conn.execute(
                """
                SELECT e.*, s.day AS session_day, s.kind AS session_kind,
                       s.lesson_type AS session_lesson_type, s.subject AS session_subject,
                       s.starts_at AS session_starts_at
                FROM entries e JOIN sessions s ON s.id = e.session_id
                WHERE e.course_id = ? AND e.created_at >= ? AND e.created_at < ?
                  AND (? IS NULL OR e.group_name = ?)
                  AND (? IS NULL OR e.lab_key = ?)
                ORDER BY e.created_at DESC, e.id DESC
                """,
                (course_id, lower, upper, group, group, lab_key, lab_key),
            )
            # Поиск по ФИО - в Python (casefold кириллицы SQLite не умеет),
            # поэтому и лимит считается здесь.
            for row in cursor:
                if needle and needle not in row["student_name"].casefold() and needle not in row["login"]:
                    continue
                if len(rows) == limit:
                    truncated = True
                    break
                rows.append(row)
            cursor.close()

        result = []
        for row in rows:
            item = _entry_out(row, None)
            item.update(
                day=row["session_day"],
                kind=row["session_kind"],
                lesson_type=row["session_lesson_type"],
                subject=row["session_subject"],
                session_starts_at=row["session_starts_at"],
            )
            result.append(item)
        return result, truncated

    def defended_labs(self, course_id: str, login: str, now: datetime) -> set[str]:
        """Keys of the labs this student defended through the queue."""
        with self._reading(course_id, now) as conn:
            rows = conn.execute(
                "SELECT DISTINCT lab_key FROM entries WHERE course_id = ? AND login = ? AND status = 'defended'",
                (course_id, normalize_login(login)),
            ).fetchall()
        return {row["lab_key"] for row in rows}

    def active_entry(self, course_id: str, login: str, now: datetime) -> dict | None:
        """The student's waiting or called entry in the course (entry fields of day_view), or None."""
        with self._reading(course_id, now) as conn:
            row = _active_row(conn, course_id, normalize_login(login))
            return None if row is None else _entry_out(row, _place(conn, row))
