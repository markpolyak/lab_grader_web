"""
Schedule source `manual`: weekly lessons written in the course YAML.

    queue:
        schedule:
            source: manual
            from: "2026-09-01"            # optional, inclusive
            until: "2026-12-31"           # optional, inclusive
            week1-monday: "2026-08-31"    # required if some lesson has weeks: odd|even
            lessons:
                - groups: ["4431"]
                  weekday: mon            # mon tue wed thu fri sat sun
                  start: "15:10"          # quoted: unquoted 15:10 is the number 910 in YAML
                  end: "16:40"
                  weeks: every            # every | odd | even; week 1 is odd

Times are in the course's timezone. `lessons: []` is allowed: the queue then
opens only when the teacher opens it by hand.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from .base import Lesson, ScheduleConfigError, ScheduleStatus, SourceContext, normalize_text

SOURCE_NAME = "manual"

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

WEEKS_EVERY = "every"
WEEKS_ODD = "odd"
WEEKS_EVEN = "even"
WEEKS_VALUES = (WEEKS_EVERY, WEEKS_ODD, WEEKS_EVEN)

_CONFIG_KEYS = {"source", "from", "until", "week1-monday", "lessons"}
_LESSON_KEYS = {"groups", "weekday", "start", "end", "weeks"}

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


@dataclass(frozen=True)
class ManualLesson:
    """One weekly lesson of the config."""
    groups: tuple[str, ...]
    weekday: int          # 0 = Monday
    start: time
    end: time
    weeks: str            # WEEKS_EVERY | WEEKS_ODD | WEEKS_EVEN


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _date(value, where: str) -> date:
    # Дата без кавычек в YAML приходит как datetime.date, в кавычках - строкой.
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ScheduleConfigError(f"Некорректное значение {where}: {value!r} (ожидается дата ГГГГ-ММ-ДД)")


def _time(value, where: str) -> time:
    if isinstance(value, int) and not isinstance(value, bool):
        # YAML 1.1 читает 15:10 без кавычек как число 15*60+10 = 910.
        hint = ""
        if 0 <= value < 24 * 60:
            hint = f" (похоже, это {value // 60}:{value % 60:02d} без кавычек)"
        raise ScheduleConfigError(
            f"Некорректное значение {where}: {value!r}{hint}; возьмите время в кавычки, например \"15:10\""
        )
    if isinstance(value, str):
        m = _TIME_RE.fullmatch(value.strip())
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            return time(int(m.group(1)), int(m.group(2)))
    raise ScheduleConfigError(
        f"Некорректное значение {where}: {value!r} (ожидается время \"ЧЧ:ММ\" в кавычках, например \"15:10\")"
    )


def _groups(value, where: str) -> tuple[str, ...]:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ScheduleConfigError(
            f"Некорректное значение {where}: {value!r} (ожидается непустой список групп, например [\"4431\"])"
        )
    groups = []
    for g in value:
        name = normalize_text(g) if isinstance(g, (str, int)) and not isinstance(g, bool) else ""
        if not name:
            raise ScheduleConfigError(f"Некорректная группа в {where}: {g!r} (ожидается название листа таблицы)")
        groups.append(name)
    return tuple(sorted(set(groups)))


def _lesson(raw, index: int) -> ManualLesson:
    where = f"queue.schedule.lessons[{index}]"
    if not isinstance(raw, dict):
        raise ScheduleConfigError(f"Некорректное значение {where}: {raw!r} (ожидается словарь)")
    unknown = sorted(str(k) for k in raw if k not in _LESSON_KEYS)
    if unknown:
        raise ScheduleConfigError(
            f"Неизвестные параметры {where}: {', '.join(unknown)} "
            f"(допустимы: {', '.join(sorted(_LESSON_KEYS))})"
        )

    for key in ("groups", "weekday", "start", "end"):
        if key not in raw:
            raise ScheduleConfigError(f"{where}.{key} обязателен")

    weekday = raw["weekday"]
    if not isinstance(weekday, str) or weekday.strip().lower() not in WEEKDAYS:
        raise ScheduleConfigError(
            f"Некорректное значение {where}.weekday: {weekday!r} (ожидается одно из: {', '.join(WEEKDAYS)})"
        )

    start = _time(raw["start"], f"{where}.start")
    end = _time(raw["end"], f"{where}.end")
    if end <= start:
        raise ScheduleConfigError(f"{where}: end ({end:%H:%M}) должен быть позже start ({start:%H:%M})")

    weeks = raw.get("weeks", WEEKS_EVERY)
    if weeks not in WEEKS_VALUES:
        raise ScheduleConfigError(
            f"Некорректное значение {where}.weeks: {weeks!r} (ожидается одно из: {', '.join(WEEKS_VALUES)})"
        )

    return ManualLesson(
        groups=_groups(raw["groups"], f"{where}.groups"),
        weekday=WEEKDAYS[weekday.strip().lower()],
        start=start,
        end=end,
        weeks=weeks,
    )


def build(cfg: dict, ctx: SourceContext) -> "ManualSource":
    """
    Parse the `queue.schedule` section of a `source: manual` course.

    Raises:
        ScheduleConfigError: unknown key, missing or malformed value
    """
    unknown = sorted(str(k) for k in cfg if k not in _CONFIG_KEYS)
    if unknown:
        raise ScheduleConfigError(
            f"Неизвестные параметры queue.schedule для source: manual: {', '.join(unknown)} "
            f"(допустимы: {', '.join(sorted(_CONFIG_KEYS - {'source'}))})"
        )

    raw_lessons = cfg.get("lessons")
    if not isinstance(raw_lessons, list):
        raise ScheduleConfigError(
            f"Некорректное значение queue.schedule.lessons: {raw_lessons!r} "
            "(ожидается список занятий; [] - только ручное открытие записи)"
        )
    lessons = tuple(_lesson(raw, i) for i, raw in enumerate(raw_lessons))

    first_day = _date(cfg["from"], "queue.schedule.from") if cfg.get("from") is not None else None
    last_day = _date(cfg["until"], "queue.schedule.until") if cfg.get("until") is not None else None
    if first_day and last_day and last_day < first_day:
        raise ScheduleConfigError(
            f"queue.schedule.until ({last_day.isoformat()}) раньше queue.schedule.from ({first_day.isoformat()})"
        )

    week1_monday = None
    if cfg.get("week1-monday") is not None:
        week1_monday = _date(cfg["week1-monday"], "queue.schedule.week1-monday")
        if week1_monday.weekday() != 0:
            monday = week1_monday - timedelta(days=week1_monday.weekday())
            raise ScheduleConfigError(
                f"queue.schedule.week1-monday: {week1_monday.isoformat()} - не понедельник "
                f"(понедельник этой недели - {monday.isoformat()})"
            )
    if week1_monday is None and any(lesson.weeks != WEEKS_EVERY for lesson in lessons):
        raise ScheduleConfigError(
            "queue.schedule.week1-monday обязателен, если у занятия weeks: odd или even "
            "(понедельник первой учебной недели, например \"2026-08-31\")"
        )

    return ManualSource(lessons, first_day, last_day, week1_monday, ctx.course_tz)


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class ManualSource:
    """Weekly lessons from the course config, in the course's timezone."""

    name = SOURCE_NAME

    def __init__(self, lessons: tuple[ManualLesson, ...], first_day: date | None, last_day: date | None,
                 week1_monday: date | None, course_tz: tzinfo):
        self.lessons = lessons
        self.first_day = first_day
        self.last_day = last_day
        self.week1_monday = week1_monday
        self.course_tz = course_tz

    def week_number(self, day: date) -> int:
        """Study week of `day`; week 1 starts on week1-monday (odd)."""
        monday = day - timedelta(days=day.weekday())
        return (monday - self.week1_monday).days // 7 + 1

    def _week_matches(self, lesson: ManualLesson, day: date) -> bool:
        if lesson.weeks == WEEKS_EVERY:
            return True
        odd = self.week_number(day) % 2 == 1
        return odd == (lesson.weeks == WEEKS_ODD)

    def lessons_on(self, day: date, now: datetime) -> list[Lesson]:
        if self.first_day and day < self.first_day:
            return []
        if self.last_day and day > self.last_day:
            return []
        return [
            Lesson.build(
                starts_at=datetime.combine(day, lesson.start, tzinfo=self.course_tz),
                ends_at=datetime.combine(day, lesson.end, tzinfo=self.course_tz),
                groups=lesson.groups,
            )
            for lesson in self.lessons
            if lesson.weekday == day.weekday() and self._week_matches(lesson, day)
        ]

    def status(self, now: datetime) -> ScheduleStatus:
        return ScheduleStatus(self.name)
