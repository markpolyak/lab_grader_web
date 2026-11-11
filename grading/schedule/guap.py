"""
Schedule source `guap`: the timetable of one SUAI (ГУАП) teacher from
https://guap.ru/rasp.

    queue:
        schedule:
            source: guap
            teacher-id: 39614                        # ?pr= of the teacher on guap.ru/rasp
            subject: Операционные системы           # optional, exact after normalization
            lesson-types: [Лабораторное занятие]     # optional, this is the default

One page (`?pr=<teacher-id>`) lists every weekly slot of the teacher: day,
pair, "every week" or ▲/▼. The site prints no dates, so they are laid out
from the header of the same page ("Сегодня четверг, 5 ноября 2026 г.",
"▼ 10 нижняя (четная) неделя"): the parity stays right in any year as long as
the page is from the same academic year. Lesson times are Moscow time, as on
the site; the course's timezone must keep them within one local day (see
COURSE_UTC_OFFSETS).

The site is slow and sometimes down (timeouts were observed), so:
  * one request at a time downloads the page, within a time budget for the
    whole exchange (FETCH_BUDGET_SECONDS); the others answer from the copy
    they have at once, or without a copy wait at most FETCH_WAIT_SECONDS and
    answer FETCH_FAILED, so a hanging site holds up one request for a few
    seconds, not the whole queue;
  * a downloaded page serves every course of the teacher for CACHE_TTL;
  * after a failed download the site is left alone for RETRY_AFTER_ERROR;
  * a page is accepted only if it really is this teacher's timetable - an
    empty search form, "nothing found" or a redesigned page never replaces a
    good copy;
  * the last good page is kept in ctx.snapshots (the queue's SQLite): after a
    restart or while the site is down the queue keeps working from it for
    SNAPSHOT_MAX_AGE, and other backend processes pick a newer copy up from
    there without going to the site themselves.

HTML parsing itself lives in grading/schedule/suai_rasp.py.
"""
import logging
import re
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, tzinfo
from time import monotonic

import requests

from . import suai_rasp
from .base import (
    ERROR_BAD_PAGE,
    ERROR_FETCH_FAILED,
    ERROR_NO_LESSONS,
    ERROR_SNAPSHOT_TOO_OLD,
    SOURCE_TZ_MSK,
    STATE_OK,
    STATE_STALE,
    STATE_UNAVAILABLE,
    Lesson,
    ScheduleConfigError,
    ScheduleStatus,
    ScheduleUnavailable,
    SnapshotStore,
    SourceContext,
    normalize_text,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "guap"

RASP_URL = "https://guap.ru/rasp"
USER_AGENT = "lab_grader_web defense queue"
# Страницу качает запрос очереди, и его ответа ждёт студент или
# преподаватель, а фронтенд сдаётся через 10 с. Обычно страница приходит за
# доли секунды; без таймаута запрос висел бы на наблюдавшемся connect timeout 30 с.
HTTP_TIMEOUT = (3, 4)
# Таймаут чтения действует на каждое чтение из сокета, и сайт, который отдаёт
# заголовки или страницу по байту, держал бы загрузку сколько угодно - отсюда
# общий срок. Загрузку ведёт отдельный поток, запрос ждёт его не дольше
# FETCH_BUDGET_SECONDS (см. _fetch_html).
FETCH_BUDGET_SECONDS = 5
READ_CHUNK = 64 * 1024
# За срок на быстрой линии можно набрать сотни мегабайт; расписание намного меньше.
MAX_PAGE_BYTES = 2 * 1024 * 1024
# Сколько запрос без копии ждёт страницу, которую уже качает другой запрос.
FETCH_WAIT_SECONDS = 1

# Сборки расписания выходят раз в несколько недель, полчаса - с большим запасом.
CACHE_TTL = timedelta(minutes=30)
RETRY_AFTER_ERROR = timedelta(minutes=5)
# Дольше работать по старой копии нельзя: за две недели расписание могло
# смениться, а преподаватель всегда может открыть запись вручную.
SNAPSHOT_MAX_AGE = timedelta(days=14)

# Пары ГУАП идут с 09:30 до 21:50 по Москве, а очередь считает дни в поясе
# курса. В этих поясах все пары укладываются в один местный день; дальше
# пара пересекала бы полночь, и запись гасла бы посреди неё.
COURSE_UTC_OFFSETS = (timedelta(hours=-6), timedelta(hours=5))

DEFAULT_LESSON_TYPES = ("Лабораторное занятие",)

_CONFIG_KEYS = {"source", "teacher-id", "subject", "lesson-types"}

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_TODAY_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _teacher_id(value) -> int:
    if value is None:
        raise ScheduleConfigError(
            "queue.schedule.teacher-id обязателен для source: guap "
            "(число из адреса https://guap.ru/rasp?pr=...)"
        )
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()) and int(value) > 0:
        return int(value)
    raise ScheduleConfigError(
        f"Некорректное значение queue.schedule.teacher-id: {value!r} "
        "(ожидается число из адреса https://guap.ru/rasp?pr=...)"
    )


def _subject(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not normalize_text(value):
        raise ScheduleConfigError(
            f"Некорректное значение queue.schedule.subject: {value!r} (ожидается название предмета)"
        )
    return normalize_text(value)


def _lesson_types(value) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_LESSON_TYPES
    if isinstance(value, str):
        value = [value]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(t, str) and normalize_text(t) for t in value)
    ):
        raise ScheduleConfigError(
            f"Некорректное значение queue.schedule.lesson-types: {value!r} "
            "(ожидается непустой список типов занятий, например [Лабораторное занятие])"
        )
    return tuple(normalize_text(t) for t in value)


def _utc_name(offset: timedelta) -> str:
    return f"UTC{offset.total_seconds() / 3600:+g}"


def _check_course_tz(tz: tzinfo) -> None:
    offset = datetime.now(tz).utcoffset()
    low, high = COURSE_UTC_OFFSETS
    if offset is not None and not low <= offset <= high:
        raise ScheduleConfigError(
            f"source: guap не работает с timezone курса {_utc_name(offset)}: пары ГУАП "
            "(09:30-21:50 по Москве) должны укладываться в один день по поясу курса, "
            f"допустим timezone от {_utc_name(low)} до {_utc_name(high)}"
        )


def build(cfg: dict, ctx: SourceContext) -> "GuapSource":
    """
    Parse the `queue.schedule` section of a `source: guap` course.

    Never touches the network: with ctx.snapshots=None this is the config check.

    Raises:
        ScheduleConfigError: unknown key, missing or malformed value, or a
        course timezone outside COURSE_UTC_OFFSETS
    """
    unknown = sorted(str(k) for k in cfg if k not in _CONFIG_KEYS)
    if unknown:
        raise ScheduleConfigError(
            f"Неизвестные параметры queue.schedule для source: guap: {', '.join(unknown)} "
            f"(допустимы: {', '.join(sorted(_CONFIG_KEYS - {'source'}))})"
        )
    _check_course_tz(ctx.course_tz)
    return GuapSource(
        teacher_id=_teacher_id(cfg.get("teacher-id")),
        subject=_subject(cfg.get("subject")),
        lesson_types=_lesson_types(cfg.get("lesson-types")),
        snapshots=ctx.snapshots,
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


class PageRejected(Exception):
    """A downloaded page is not a usable timetable of the teacher."""

    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code          # ERROR_BAD_PAGE | ERROR_NO_LESSONS
        self.reason = reason


def parse_today(text: str | None) -> date | None:
    """'четверг, 5 ноября 2026' (the page header) -> date(2026, 11, 5); None if unrecognized."""
    m = _TODAY_RE.search(text or "")
    if not m or m.group(2).lower() not in _MONTHS:
        return None
    try:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_number(day: date, anchor_day: date, anchor_week: int) -> int:
    """Study week of `day`, counted from the week the page header gives for `anchor_day`."""
    return anchor_week + (_monday(day) - _monday(anchor_day)).days // 7


@dataclass(frozen=True)
class GuapPage:
    """An accepted timetable page, ready to be laid onto dates."""
    lessons: tuple[suai_rasp.RaspLesson, ...]   # in the grid and with a time
    anchor_day: date                            # "Сегодня ..." of the header
    anchor_week: int                            # "▼ 10 ..." of the header
    anchor_parity: int                          # WEEK_ODD (▲ верхняя) | WEEK_EVEN (▼ нижняя)

    def parity_on(self, day: date) -> int:
        """
        WEEK_ODD or WEEK_EVEN for the week of `day`.

        Counted from the header's ▲/▼ (верхняя/нижняя) rather than from the
        parity of its number: the cards carry the same ▲/▼ marks. On every
        page seen so far the two agree ("▼ 10 нижняя").
        """
        if (week_number(day, self.anchor_day, self.anchor_week) - self.anchor_week) % 2 == 0:
            return self.anchor_parity
        return suai_rasp.WEEK_EVEN if self.anchor_parity == suai_rasp.WEEK_ODD else suai_rasp.WEEK_ODD


def parse_teacher_page(html: str, teacher_id: int, fetched_on: date) -> GuapPage:
    """
    Accept or reject a page of https://guap.ru/rasp?pr=<teacher_id>.

    A page is accepted only when it shows a filter result ("Фильтр: ..."),
    has the week header and has at least one lesson of this teacher in the
    grid. `fetched_on` (Moscow date of the download) is the anchor if the
    header has no readable "Сегодня ..." date.

    Raises:
        PageRejected: ERROR_NO_LESSONS for "nothing found" or no lessons of
        the teacher, ERROR_BAD_PAGE for anything else
    """
    soup = suai_rasp.make_soup(html)
    kind, text = suai_rasp.parse_result_banner(soup)
    if kind == suai_rasp.BANNER_NO_MATCH:
        raise PageRejected(ERROR_NO_LESSONS, f"the site says {text!r}")
    if kind != suai_rasp.BANNER_FILTER:
        raise PageRejected(ERROR_BAD_PAGE, f"no filter result on the page ({text[:80]!r})")

    meta = suai_rasp.parse_meta(soup)
    if "week_number" not in meta:
        raise PageRejected(ERROR_BAD_PAGE, "no study week in the page header")

    lessons = tuple(
        lesson for lesson in suai_rasp.parse_lessons(soup)
        if lesson.in_grid and lesson.start and lesson.end
    )
    if not any(t.id == teacher_id for lesson in lessons for t in lesson.teachers):
        raise PageRejected(ERROR_NO_LESSONS, f"no lessons of pr={teacher_id} in the grid")

    return GuapPage(
        lessons=lessons,
        anchor_day=parse_today(meta.get("today")) or fetched_on,
        anchor_week=meta["week_number"],
        anchor_parity=meta["week_parity"],
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CacheEntry:
    page: GuapPage | None = None
    fetched_at: datetime | None = None    # when the page was downloaded (by anyone)
    error: str | None = None              # code of the last failed download, None after a success
    retry_at: datetime | None = None      # no downloads before this moment
    store_seen: datetime | None = None    # fetched_at of the saved copy last looked at


# "guap:pr=<id>" -> entry. Shared by every course of the same teacher.
_cache: dict[str, _CacheEntry] = {}
_cache_lock = threading.Lock()

_fetch_locks: dict[str, threading.Lock] = {}

# Потоки загрузки, не уложившиеся в срок. Поток висит на сокете, пока сайт не
# замолчит на таймаут чтения или не закроет соединение; пока он жив, новые
# загрузки не начинаются и зависшие потоки не копятся.
_stuck_fetches: list[threading.Thread] = []


def _get(key: str) -> _CacheEntry:
    with _cache_lock:
        return _cache.get(key) or _CacheEntry()


def _put(key: str, entry: _CacheEntry) -> None:
    with _cache_lock:
        _cache[key] = entry


def _fetch_lock(key: str) -> threading.Lock:
    with _cache_lock:
        lock = _fetch_locks.get(key)
        if lock is None:
            lock = _fetch_locks[key] = threading.Lock()
        return lock


def reset_cache() -> None:
    """Forget every downloaded page and pause (used by tests)."""
    with _cache_lock:
        _cache.clear()
        _fetch_locks.clear()
        _stuck_fetches.clear()


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


def _at(day: date, hhmm: str) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime.combine(day, time(int(hours), int(minutes)), tzinfo=SOURCE_TZ_MSK)


class GuapSource:
    """Lessons of one teacher from guap.ru/rasp (see the module docstring)."""

    name = SOURCE_NAME

    def __init__(self, teacher_id: int, subject: str | None, lesson_types: tuple[str, ...],
                 snapshots: SnapshotStore | None):
        self.teacher_id = teacher_id
        self.subject = subject
        self.lesson_types = lesson_types
        self.snapshots = snapshots
        self.key = f"guap:pr={teacher_id}"
        self.url = f"{RASP_URL}?pr={teacher_id}"

    # -- public interface ---------------------------------------------------

    def lessons_on(self, day: date, now: datetime) -> list[Lesson]:
        """
        Lessons of the teacher on `day` that pass the course's filters.

        Raises:
            ScheduleUnavailable: no page at all, or the saved one is older than
            SNAPSHOT_MAX_AGE; the message is the ScheduleStatus error code
        """
        entry = self._refresh(now)
        error = self._unusable(entry, now)
        if error:
            raise ScheduleUnavailable(error)

        page = entry.page
        weekday = suai_rasp.DAYS[day.weekday()]
        parity = page.parity_on(day)
        lessons = []
        for item in page.lessons:
            if item.day != weekday or item.week not in (suai_rasp.WEEK_ANY, parity):
                continue
            if not self._wanted(item):
                continue
            groups = [g.name for g in item.groups if normalize_text(g.name)]
            if not groups:
                # Без групп записывать некого (физкультура, «вне сетки» и т.п.).
                continue
            lessons.append(Lesson.build(
                starts_at=_at(day, item.start),
                ends_at=_at(day, item.end),
                groups=groups,
                lesson_type=item.kind,
                subject=item.subject,
            ))
        return lessons

    def status(self, now: datetime) -> ScheduleStatus:
        """
        State of the schedule for the admin page; never raises.

        Built from the cache and the saved copy only, never downloads:
        lessons_on() of the same request has already done it if it was due.
        """
        entry = self._take_saved(_get(self.key), now)
        error = self._unusable(entry, now)
        if error:
            return ScheduleStatus(self.name, STATE_UNAVAILABLE, entry.fetched_at, error)
        if entry.error:
            return ScheduleStatus(self.name, STATE_STALE, entry.fetched_at, entry.error)
        return ScheduleStatus(self.name, STATE_OK, entry.fetched_at, None)

    # -- filters ------------------------------------------------------------

    def _wanted(self, item: suai_rasp.RaspLesson) -> bool:
        if normalize_text(item.kind) not in self.lesson_types:
            return False
        if self.subject is not None and normalize_text(item.subject) != self.subject:
            return False
        return any(t.id == self.teacher_id for t in item.teachers)

    # -- cache, saved copy, download -----------------------------------------

    @staticmethod
    def _unusable(entry: _CacheEntry, now: datetime) -> str | None:
        if entry.page is None:
            return entry.error or ERROR_FETCH_FAILED
        if now - entry.fetched_at > SNAPSHOT_MAX_AGE:
            return ERROR_SNAPSHOT_TOO_OLD
        return None

    @staticmethod
    def _needs_download(entry: _CacheEntry, now: datetime) -> bool:
        if entry.retry_at is not None and now < entry.retry_at:
            return False
        return entry.page is None or now - entry.fetched_at >= CACHE_TTL

    def _refresh(self, now: datetime) -> _CacheEntry:
        entry = self._take_saved(_get(self.key), now)
        if not self._needs_download(entry, now):
            return entry

        lock = _fetch_lock(self.key)
        if not lock.acquire(blocking=False):
            # Страницу уже качает другой запрос. Есть копия - отвечаем по ней
            # сразу; нет - ждём недолго, а не дождались - FETCH_FAILED.
            if entry.page is not None:
                return entry
            if not lock.acquire(timeout=FETCH_WAIT_SECONDS):
                return self._give_up_waiting(now)
        try:
            entry = self._take_saved(_get(self.key), now)
            if self._needs_download(entry, now):
                entry = self._download(entry, now)
                _put(self.key, entry)
            return entry
        finally:
            lock.release()

    def _give_up_waiting(self, now: datetime) -> _CacheEntry:
        """
        No copy, and the download of another request is still running.

        The pause makes the next requests answer FETCH_FAILED at once instead
        of waiting again; the download, when it ends, replaces this entry.
        """
        logger.warning(
            "GUAP schedule %s: no answer within %s s; no saved copy", self.url, FETCH_WAIT_SECONDS,
        )
        with _cache_lock:
            entry = _cache.get(self.key) or _CacheEntry()
            if entry.page is None:
                entry = _cache[self.key] = replace(
                    entry, error=ERROR_FETCH_FAILED, retry_at=now + RETRY_AFTER_ERROR,
                )
        return entry

    def _take_saved(self, entry: _CacheEntry, now: datetime) -> _CacheEntry:
        """
        Pick up a saved copy newer than the cached page.

        After a restart that is the copy saved before it; with several backend
        processes, the one another process has just downloaded. One cheap
        `snapshot_fetched_at` per call, the page itself is read only when it
        is new.
        """
        if self.snapshots is None:
            return entry
        try:
            saved_at = self.snapshots.snapshot_fetched_at(self.key)
        except Exception as e:
            logger.warning("Could not check the saved GUAP schedule %s: %s", self.key, e)
            return entry
        if (
            saved_at is None
            or saved_at == entry.store_seen
            or (entry.fetched_at is not None and saved_at <= entry.fetched_at)
        ):
            return entry

        try:
            saved = self.snapshots.load_snapshot(self.key)
        except Exception as e:
            logger.warning("Could not read the saved GUAP schedule %s: %s", self.key, e)
            return entry
        if saved is None:
            return entry
        fetched_at, html = saved
        try:
            page = parse_teacher_page(html, self.teacher_id, fetched_at.astimezone(SOURCE_TZ_MSK).date())
        except Exception as e:
            logger.warning("Saved GUAP schedule %s is not usable: %s", self.key, e)
            entry = replace(entry, store_seen=saved_at)
        else:
            entry = replace(
                entry,
                page=page,
                fetched_at=fetched_at,
                # Свежую копию только что скачал другой процесс - сайт работает.
                error=None if now - fetched_at < CACHE_TTL else entry.error,
                store_seen=saved_at,
            )
        _put(self.key, entry)
        return entry

    def _fetch_html(self) -> str:
        """
        The page, downloaded within FETCH_BUDGET_SECONDS, headers included.

        requests has no deadline for the whole exchange, so the download runs
        in a short-lived thread and the request waits for it at most
        FETCH_BUDGET_SECONDS. A thread that misses the deadline is left to end
        on its own, and no other download starts until it does.

        Raises:
            PageRejected: ERROR_BAD_PAGE, the body is over MAX_PAGE_BYTES
            requests.RequestException, TimeoutError and the like: network
            error, HTTP status other than 200, the budget ran out, the
            previous download is still hanging
        """
        with _cache_lock:
            _stuck_fetches[:] = [t for t in _stuck_fetches if t.is_alive()]
            if _stuck_fetches:
                raise TimeoutError("the previous download has not ended yet")
        deadline = monotonic() + FETCH_BUDGET_SECONDS
        result = {}
        worker = threading.Thread(
            target=self._fetch_into, args=(result, deadline), name=f"{self.key} download", daemon=True,
        )
        worker.start()
        worker.join(FETCH_BUDGET_SECONDS)
        if worker.is_alive():
            with _cache_lock:
                _stuck_fetches.append(worker)
            raise TimeoutError(f"the page is not downloaded in {FETCH_BUDGET_SECONDS} s")
        if "error" in result:
            raise result["error"]
        return result["html"]

    def _fetch_into(self, result: dict, deadline: float) -> None:
        try:
            result["html"] = self._read_page(deadline)
        except Exception as e:
            result["error"] = e

    def _read_page(self, deadline: float) -> str:
        # Без сжатия: срок проверяется после каждого чтения из сокета, а
        # распаковка могла бы ждать много чтений подряд.
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        with requests.get(self.url, timeout=HTTP_TIMEOUT, headers=headers, stream=True) as resp:
            if resp.status_code != 200:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            read1 = getattr(resp.raw, "read1", None)
            if read1 is None:
                # urllib3 без read1: срок проверяется после каждых READ_CHUNK байт.
                chunks = resp.iter_content(READ_CHUNK)
            else:
                # read1 отдаёт то, что пришло за одно чтение из сокета.
                chunks = iter(lambda: read1(READ_CHUNK, decode_content=True) or b"", b"")
            body = bytearray()
            for chunk in chunks:
                body += chunk
                if len(body) > MAX_PAGE_BYTES:
                    raise PageRejected(ERROR_BAD_PAGE, f"the page is over {MAX_PAGE_BYTES} bytes")
                # Запрос уже не ждёт: дальше не читаем, чтобы поток закончился.
                if monotonic() > deadline:
                    raise TimeoutError(f"the page is not downloaded in {FETCH_BUDGET_SECONDS} s")
        # Сайт отдаёт UTF-8; не полагаемся на charset в заголовке ответа.
        return body.decode("utf-8", errors="replace")

    def _download(self, entry: _CacheEntry, now: datetime) -> _CacheEntry:
        try:
            html = self._fetch_html()
        except PageRejected as e:
            return self._failed(entry, now, e.code, e.reason)
        except Exception as e:
            return self._failed(entry, now, ERROR_FETCH_FAILED, f"{type(e).__name__}: {e}")
        try:
            page = parse_teacher_page(html, self.teacher_id, now.astimezone(SOURCE_TZ_MSK).date())
        except PageRejected as e:
            return self._failed(entry, now, e.code, e.reason)
        except Exception as e:
            logger.exception("Could not parse the GUAP schedule %s", self.key)
            return self._failed(entry, now, ERROR_BAD_PAGE, f"{type(e).__name__}: {e}")

        if self.snapshots is not None:
            try:
                self.snapshots.save_snapshot(self.key, now, html)
            except Exception as e:
                logger.warning("Could not save the GUAP schedule %s: %s", self.key, e)
        return _CacheEntry(page=page, fetched_at=now, store_seen=now)

    def _failed(self, entry: _CacheEntry, now: datetime, code: str, reason: str) -> _CacheEntry:
        if entry.page is not None:
            logger.warning(
                "GUAP schedule %s: %s (%s); using the copy of %s",
                self.url, code, reason, entry.fetched_at.isoformat(),
            )
        else:
            logger.warning("GUAP schedule %s: %s (%s); no saved copy", self.url, code, reason)
        return replace(entry, error=code, retry_at=now + RETRY_AFTER_ERROR)
