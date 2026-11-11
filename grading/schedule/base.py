"""
Common types of the schedule sources used by the defense queue.

A source turns a course's `queue.schedule` section into the lessons of one
day. The queue (grading/defense_queue.py) only sees `Lesson` objects and
never knows which university or which website they came from, so another
university is connected by adding one module next to this one (see
grading/schedule/__init__.py) and naming it in the course YAML.
"""
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Protocol


# Время на сайте ГУАП (и в ручном расписании ГУАП-курсов) - московское.
SOURCE_TZ_MSK = timezone(timedelta(hours=3))

_SPACES_RE = re.compile(r"\s+")


def normalize_text(value) -> str:
    """NBSP to a space, runs of whitespace collapsed, stripped. None -> ""."""
    if value is None:
        return ""
    return _SPACES_RE.sub(" ", str(value).replace("\xa0", " ")).strip()


def lesson_key(starts_at: datetime, ends_at: datetime, groups: tuple[str, ...],
               lesson_type: str, subject: str) -> str:
    """
    Identity of a lesson within one day. Times are in UTC: sources build them in different zones.

    Built from what the lesson *is*, so a lesson moved to another time or
    another group gets a different key: the queue then stops accepting new
    entries for the old one while keeping its existing entries until the end
    of the day (customer's answer about schedule changes in issue #62).
    """
    return (
        f"{starts_at.astimezone(timezone.utc):%H:%M}-{ends_at.astimezone(timezone.utc):%H:%M}"
        f"|{','.join(groups)}|{lesson_type}|{subject}"
    )


@dataclass(frozen=True)
class Lesson:
    """One lesson of one day, as the queue sees it."""
    key: str
    starts_at: datetime          # aware
    ends_at: datetime            # aware
    groups: tuple[str, ...]      # normalized and sorted; () for an all-day queue
    lesson_type: str = ""        # data of the source, e.g. "Лабораторное занятие"
    subject: str = ""            # data of the source, e.g. "Операционные системы"

    @classmethod
    def build(cls, starts_at: datetime, ends_at: datetime, groups, lesson_type: str = "",
              subject: str = "") -> "Lesson":
        """Normalize the fields and compute the key."""
        norm_groups = tuple(sorted({normalize_text(g) for g in groups if normalize_text(g)}))
        lesson_type = normalize_text(lesson_type)
        subject = normalize_text(subject)
        return cls(
            key=lesson_key(starts_at, ends_at, norm_groups, lesson_type, subject),
            starts_at=starts_at,
            ends_at=ends_at,
            groups=norm_groups,
            lesson_type=lesson_type,
            subject=subject,
        )


class ScheduleConfigError(ValueError):
    """The course's `queue.schedule` section is malformed."""


class ScheduleUnavailable(Exception):
    """The source cannot answer and has no usable saved copy."""


# ScheduleStatus.state
STATE_OK = "ok"
STATE_STALE = "stale"              # working from a saved copy
STATE_UNAVAILABLE = "unavailable"  # nothing to work from

# ScheduleStatus.error
ERROR_FETCH_FAILED = "FETCH_FAILED"
ERROR_BAD_PAGE = "BAD_PAGE"
ERROR_NO_LESSONS = "NO_LESSONS"
ERROR_SNAPSHOT_TOO_OLD = "SNAPSHOT_TOO_OLD"


@dataclass(frozen=True)
class ScheduleStatus:
    """What the teacher is told about the schedule on the admin page."""
    source: str                       # "guap" | "manual" | "all_day" | ...
    state: str = STATE_OK
    fetched_at: datetime | None = None
    error: str | None = None


class SnapshotStore(Protocol):
    """Where a network source keeps its last good copy (QueueStore implements it)."""

    def load_snapshot(self, key: str) -> tuple[datetime, str] | None: ...

    def save_snapshot(self, key: str, fetched_at: datetime, payload: str) -> None: ...

    def snapshot_fetched_at(self, key: str) -> datetime | None: ...


@dataclass
class SourceContext:
    """
    What a source gets besides its config section.

    `snapshots=None` is the config-check mode: build() must parse and
    validate the section without touching the network or the database.
    """
    snapshots: SnapshotStore | None
    course_tz: tzinfo


class ScheduleSource(Protocol):
    """Interface every source module's build() returns."""
    name: str

    def lessons_on(self, day: date, now: datetime) -> list[Lesson]:
        """Lessons of `day`; ScheduleUnavailable when the source cannot tell."""

    def status(self, now: datetime) -> ScheduleStatus:
        """State of the schedule for the teacher's page; never raises."""


# Самый длинный перерыв внутри сдвоенного занятия: в ГУАП между 3-й и 4-й
# парой обед 40 минут (14:30-15:10), а сдвоенные 3-4 пары бывают.
MERGE_MAX_GAP = timedelta(minutes=40)


def merge_adjacent(lessons: list[Lesson], max_gap: timedelta = MERGE_MAX_GAP) -> list[Lesson]:
    """
    Glue lessons of the same groups, type and subject into one when they
    follow each other with a break of at most `max_gap` or overlap.

    A double lab (two pairs in a row for one group) is one sitting for the
    teacher, so it must be one queue: otherwise students who sign up after the
    first pair start a second queue at place 1 while the first one still has
    people waiting. The same goes for overlapping lessons and for a lesson
    listed twice: the result is the union of their times, so a lesson that
    lies inside the previous one simply disappears into it.
    """
    ordered = sorted(lessons, key=lambda l: (l.groups, l.lesson_type, l.subject, l.starts_at))
    merged: list[Lesson] = []
    for lesson in ordered:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and prev.groups == lesson.groups
            and prev.lesson_type == lesson.lesson_type
            and prev.subject == lesson.subject
            and prev.starts_at.date() == lesson.starts_at.date()
            and lesson.starts_at - prev.ends_at <= max_gap
        ):
            if lesson.ends_at > prev.ends_at:
                merged[-1] = replace(
                    prev,
                    ends_at=lesson.ends_at,
                    key=lesson_key(prev.starts_at, lesson.ends_at, prev.groups, prev.lesson_type, prev.subject),
                )
        else:
            merged.append(lesson)
    return sorted(merged, key=lambda l: (l.starts_at, l.groups))
