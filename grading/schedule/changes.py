"""
Changes to a course's schedule written in advance: cancelled and extra lessons.

    queue:
        schedule:
            source: guap                  # any source: guap, manual, ...
            ...
            changes:
                - date: "2026-11-10"      # cancelled: no signup for these groups that day
                  cancel: true
                  groups: ["4432"]
                  start: "13:00"          # optional: only the lesson starting at this time
                - date: "2026-11-12"      # extra lesson: signup as on a regular one
                  groups: ["4431", "4432"]
                  start: "15:10"
                  end: "16:40"
                  lesson-type: Лабораторное занятие   # optional
                  subject: Операционные системы       # optional

The university's timetable has only regular lessons, so moved, cancelled and
extra lessons are listed here (issue #62, 06.11). The registry
(grading/schedule/__init__.py) takes `changes` out of the section before the
source module sees it and applies it to whatever the source returns, so no
source knows about changes. Dates and times are in the course's timezone.
Within a day the cancellations go first, then the extra lessons: a move is a
cancellation plus an extra lesson.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo

from .base import Lesson, ScheduleConfigError, SourceContext, normalize_text
# Даты, время и группы пишутся так же, как в ручном расписании.
from .manual import _date, _groups, _time

logger = logging.getLogger(__name__)

_CANCEL_KEYS = {"date", "cancel", "groups", "start"}
_EXTRA_KEYS = {"date", "cancel", "groups", "start", "end", "lesson-type", "subject"}


@dataclass(frozen=True)
class Cancel:
    """Lessons of `groups` on `day` (only the one starting at `start`, if set) are cancelled."""
    day: date
    groups: tuple[str, ...]
    start: time | None

    def takes(self, starts_at: datetime, course_tz: tzinfo) -> bool:
        """Whether it is about the lesson of its day starting at `starts_at` (any lesson without `start`)."""
        return self.start is None or starts_at.astimezone(course_tz).time() == self.start


@dataclass(frozen=True)
class Extra:
    """An extra lesson on `day`, in the course's timezone."""
    day: date
    groups: tuple[str, ...]
    start: time
    end: time
    lesson_type: str
    subject: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _text(value, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ScheduleConfigError(f"Некорректное значение {where}: {value!r} (ожидается строка)")
    return normalize_text(value)


def _change(raw, index: int) -> Cancel | Extra:
    where = f"queue.schedule.changes[{index}]"
    if not isinstance(raw, dict):
        raise ScheduleConfigError(f"Некорректное значение {where}: {raw!r} (ожидается словарь)")

    cancel = raw.get("cancel", False)
    if not isinstance(cancel, bool):
        raise ScheduleConfigError(f"Некорректное значение {where}.cancel: {cancel!r} (ожидается true или false)")
    allowed = _CANCEL_KEYS if cancel else _EXTRA_KEYS
    unknown = sorted(str(k) for k in raw if k not in allowed)
    if unknown:
        kind = "отмены (cancel: true)" if cancel else "дополнительного занятия"
        raise ScheduleConfigError(
            f"Неизвестные параметры {where}: {', '.join(unknown)} "
            f"(допустимы для {kind}: {', '.join(sorted(allowed))})"
        )

    required = ("date", "groups") if cancel else ("date", "groups", "start", "end")
    for key in required:
        if key not in raw:
            raise ScheduleConfigError(f"{where}.{key} обязателен")

    day = _date(raw["date"], f"{where}.date")
    groups = _groups(raw["groups"], f"{where}.groups")
    if cancel:
        start = None if raw.get("start") is None else _time(raw["start"], f"{where}.start")
        return Cancel(day, groups, start)

    start = _time(raw["start"], f"{where}.start")
    end = _time(raw["end"], f"{where}.end")
    if end <= start:
        raise ScheduleConfigError(f"{where}: end ({end:%H:%M}) должен быть позже start ({start:%H:%M})")
    return Extra(
        day, groups, start, end,
        lesson_type=_text(raw.get("lesson-type"), f"{where}.lesson-type"),
        subject=_text(raw.get("subject"), f"{where}.subject"),
    )


def parse_changes(raw, ctx: SourceContext) -> "ScheduleChanges":
    """
    Parse `queue.schedule.changes` (None or absent = no changes).

    Never touches the network: it is a part of the config check.

    Raises:
        ScheduleConfigError: not a list, unknown key, missing or malformed value
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ScheduleConfigError(
            f"Некорректное значение queue.schedule.changes: {raw!r} (ожидается список поправок)"
        )
    changes = [_change(item, i) for i, item in enumerate(raw)]
    return ScheduleChanges(
        cancels=tuple(c for c in changes if isinstance(c, Cancel)),
        extras=tuple(c for c in changes if isinstance(c, Extra)),
        course_tz=ctx.course_tz,
    )


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


class ScheduleChanges:
    """The changes of one course; see the module docstring."""

    def __init__(self, cancels: tuple[Cancel, ...], extras: tuple[Extra, ...], course_tz: tzinfo):
        self.cancels = cancels
        self.extras = extras
        self.course_tz = course_tz

    def _cancel(self, lesson: Lesson, cancel: Cancel) -> Lesson | None:
        if not cancel.takes(lesson.starts_at, self.course_tz):
            return lesson
        groups = [g for g in lesson.groups if g not in cancel.groups]
        if len(groups) == len(lesson.groups):
            return lesson
        if not groups:
            return None
        # У занятия нескольких групп отменённые убираются, и ключ занятия
        # строится заново - как если бы источник сразу отдал его без них.
        return Lesson.build(lesson.starts_at, lesson.ends_at, groups, lesson.lesson_type, lesson.subject)

    def extra_on(self, day: date) -> list[Lesson]:
        """Extra lessons of `day`."""
        return [
            Lesson.build(
                starts_at=datetime.combine(day, extra.start, tzinfo=self.course_tz),
                ends_at=datetime.combine(day, extra.end, tzinfo=self.course_tz),
                groups=extra.groups,
                lesson_type=extra.lesson_type,
                subject=extra.subject,
            )
            for extra in self.extras
            if extra.day == day
        ]

    def apply(self, day: date, lessons: list[Lesson]) -> list[Lesson]:
        """The source's lessons of `day` with the cancellations, then the extra lessons, of that day."""
        for cancel in self.cancels:
            if cancel.day == day:
                lessons = [kept for kept in (self._cancel(l, cancel) for l in lessons) if kept is not None]
        return [*lessons, *self.extra_on(day)]

    def lessons_on(self, source, day: date, now: datetime) -> list[Lesson]:
        """
        `source.lessons_on(day, now)` with the changes applied.

        If the source fails (ScheduleUnavailable, or anything a module of
        another university may raise), a day with extra lessons still gets
        them: they are planned in advance and must open while the
        university's site is down. Regular lessons of that day are then
        unknown, so their sessions stop taking new entries (those already
        queued stay) until the source answers again; the teacher sees the
        source's state and can open signup by hand.

        Raises:
            the source's exception, when the day has no extra lessons
            (what is still known of that day is unknown_day)
        """
        try:
            lessons = source.lessons_on(day, now)
        except Exception as e:
            extra = self.extra_on(day)
            if not extra:
                raise
            logger.warning(
                "Schedule source %s failed for %s (%s: %s); only the extra lessons of the config are used",
                source.name, day.isoformat(), type(e).__name__, e,
            )
            return extra
        return self.apply(day, lessons)

    def unknown_day(self, day: date) -> "UnknownDay | None":
        """The day's cancellations, for a day lessons_on raised for; None when it has none."""
        cancels = tuple(c for c in self.cancels if c.day == day)
        return UnknownDay(day, cancels, self.course_tz) if cancels else None


class UnknownDay:
    """
    Today's lessons when the source failed and the day has no extra lessons
    (main._today): none are known, only the day's cancellations.

    It holds no lessons, so no session is created from it. Passed on as the
    current keys, it closes signup only to the cancelled groups
    (defense_queue._in_schedule); the rest works as with an unknown
    schedule (None).
    """

    def __init__(self, day: date, cancels: tuple[Cancel, ...], course_tz: tzinfo):
        self.day = day
        self.cancels = cancels
        self.course_tz = course_tz

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def cancelled(self, day: date, starts_at: datetime, groups) -> set[str]:
        """
        Which of `groups` the cancellations take off a session of `day` starting at `starts_at`.

        Without the schedule a session is one lesson, like a double lesson
        written as one in `manual`: a cancellation with `start` takes it
        only when it starts at that time. So a cancelled second pair of a
        double lesson leaves its session open, a cancelled first pair
        closes it.
        """
        if day != self.day:
            return set()
        return {
            group
            for cancel in self.cancels if cancel.takes(starts_at, self.course_tz)
            for group in groups if group in cancel.groups
        }
