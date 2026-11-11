"""
Schedule sources of the defense queue, looked up by module name.

The course YAML names its source (`queue.schedule.source: guap`) and the
source is the module grading/schedule/<source>.py. A module is a source when
it has

    SOURCE_NAME = "<source>"                      # same as the file name
    def build(cfg: dict, ctx: SourceContext)       # -> object with name,
                                                   #    lessons_on(day, now), status(now)

build() parses and validates its section and must not touch the network:
with `ctx.snapshots=None` the call is the config check (startup, course
editor). A source that keeps a module-level cache exposes `reset_cache()`,
which reset_schedule_caches() calls for tests.

So another university is connected by adding one file here and naming it in
the course config - neither this registry nor the queue changes.

`queue.schedule.changes` (cancelled and extra lessons, changes.py) belongs to
the registry: it is taken out before build() and applied to any source.

A course without `queue.schedule` gets AllDaySource: one queue for the whole
day in the course's timezone, open to every group.
"""
import importlib
import re
import sys
from datetime import date, datetime, time, timedelta, tzinfo

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
    ScheduleSource,
    ScheduleStatus,
    ScheduleUnavailable,
    SnapshotStore,
    SourceContext,
    merge_adjacent,
)
from .changes import ScheduleChanges, parse_changes

__all__ = [
    "ALL_DAY_SOURCE",
    "AllDaySource",
    "ERROR_BAD_PAGE",
    "ERROR_FETCH_FAILED",
    "ERROR_NO_LESSONS",
    "ERROR_SNAPSHOT_TOO_OLD",
    "Lesson",
    "SOURCE_TZ_MSK",
    "STATE_OK",
    "STATE_STALE",
    "STATE_UNAVAILABLE",
    "ScheduleConfigError",
    "ScheduleSource",
    "ScheduleStatus",
    "ScheduleUnavailable",
    "SnapshotStore",
    "SourceContext",
    "build_schedule_source",
    "merge_adjacent",
    "reset_schedule_caches",
]

ALL_DAY_SOURCE = "all_day"
ALL_DAY_KEY = "all-day"

# Имя модуля источника: только то, что может быть файлом в этом пакете.
SOURCE_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")


class AllDaySource:
    """No schedule: one lesson from midnight to midnight, for every group."""

    name = ALL_DAY_SOURCE

    def __init__(self, course_tz: tzinfo):
        self.course_tz = course_tz

    def lessons_on(self, day: date, now: datetime) -> list[Lesson]:
        starts_at = datetime.combine(day, time(0, 0), tzinfo=self.course_tz)
        return [Lesson(
            key=ALL_DAY_KEY,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=1),
            groups=(),
        )]

    def status(self, now: datetime) -> ScheduleStatus:
        return ScheduleStatus(self.name)


class _MergedSource:
    """
    A source with the course's `changes` (changes.py) applied and double lessons glued together.

    Done here, not in each module, so that a new source cannot forget it
    (see merge_adjacent for why a double lab must be one queue).
    """

    def __init__(self, source, changes: ScheduleChanges):
        self.source = source
        self.changes = changes
        self.name = source.name

    def lessons_on(self, day: date, now: datetime) -> list[Lesson]:
        return merge_adjacent(self.changes.lessons_on(self.source, day, now))

    def status(self, now: datetime) -> ScheduleStatus:
        return self.source.status(now)


def _source_module(name):
    if not isinstance(name, str) or not SOURCE_NAME_RE.fullmatch(name):
        raise ScheduleConfigError(f"Неизвестный источник расписания queue.schedule.source: {name!r}")
    module_name = f"{__name__}.{name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name != module_name:
            # Модуль есть, но сам не импортируется (нет зависимости) - это
            # ошибка развёртывания, а не конфига курса.
            raise
        raise ScheduleConfigError(f"Неизвестный источник расписания queue.schedule.source: {name!r}") from None
    if getattr(module, "SOURCE_NAME", None) != name or not callable(getattr(module, "build", None)):
        raise ScheduleConfigError(
            f"Неизвестный источник расписания queue.schedule.source: {name!r} "
            f"(модуль {module_name} не является источником расписания)"
        )
    return module


def build_schedule_source(schedule_cfg: dict | None, ctx: SourceContext):
    """
    The schedule source of a course.

    Args:
        schedule_cfg: the course's `queue.schedule` section, None if absent
        ctx: snapshot store (None = config check, no network) and course timezone

    Returns:
        An object with `name`, `lessons_on(day, now)` and `status(now)`;
        lessons_on() returns lessons with `changes` applied and adjacent ones merged

    Raises:
        ScheduleConfigError: unknown source or malformed section
    """
    if schedule_cfg is None:
        return AllDaySource(ctx.course_tz)
    if not isinstance(schedule_cfg, dict):
        raise ScheduleConfigError(
            f"Некорректное значение queue.schedule: {schedule_cfg!r} (ожидается словарь с ключом source)"
        )
    module = _source_module(schedule_cfg.get("source"))
    source_cfg = schedule_cfg
    if "changes" in schedule_cfg:
        # Поправки - общий ключ реестра: модуль источника о них не знает.
        source_cfg = {k: v for k, v in schedule_cfg.items() if k != "changes"}
    source = module.build(source_cfg, ctx)
    return _MergedSource(source, parse_changes(schedule_cfg.get("changes"), ctx))


def reset_schedule_caches() -> None:
    """Drop the module-level caches of every loaded source (used by tests)."""
    prefix = f"{__name__}."
    for module_name, module in list(sys.modules.items()):
        if module_name.startswith(prefix):
            reset = getattr(module, "reset_cache", None)
            if callable(reset):
                reset()
