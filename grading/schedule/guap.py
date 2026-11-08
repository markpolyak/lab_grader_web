"""Fetch group-specific GUAP schedules and expose matching lab sessions."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable

from suai_rasp.fetch import Fetcher
from suai_rasp.model import LESSON_TIMES
from suai_rasp.parse import parse_directories, parse_lessons

from .base import ScheduleSource, ScheduledSession, ScheduleConfigError
from .registry import register_source


# Russian weekday names returned by the parser -> ISO weekday number.
GUAP_WEEKDAYS = {
    "Понедельник": 0, "Вторник": 1, "Среда": 2, "Четверг": 3,
    "Пятница": 4, "Суббота": 5, "Воскресенье": 6,
}

# Reverse map for to_yaml_sessions: 0..6 -> "monday".."sunday" (our YAML format).
ISO_TO_YAML_DAY = {
    0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday',
    4: 'friday', 5: 'saturday', 6: 'sunday',
}

# Short weekday codes for slot id generation.
ISO_TO_SHORT_DAY = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu',
                    4: 'fri', 5: 'sat', 6: 'sun'}

# Standard GUAP slot numbers -> (start, end), kept in sync with the parser.
GUAP_SLOTS = {
    slot: (time.fromisoformat(start), time.fromisoformat(end))
    for slot, (start, end) in LESSON_TIMES.items()
}

DEFAULT_LAB_KINDS = ("ЛР", "Лабораторная", "Лабораторное занятие")
def normalize_subject(value: str | None) -> str:
    """Normalize whitespace and case for exact course-title matching."""
    return " ".join(str(value or "").split()).casefold()


@register_source('guap')
class GuapScheduleSource(ScheduleSource):
    """Fetch one group's timetable from GUAP and select this course's labs.

    Usage:

        src = GuapScheduleSource(course_name="Операционные системы")
        sessions = list(src.list_sessions("4432", since, until))
    """

    def __init__(
        self,
        group_code: str | None = None,
        lab_kinds: tuple[str, ...] = DEFAULT_LAB_KINDS,
        course_id: str = "",
        course_name: str | None = None,
        auto_open_minutes: int = 15,
        auto_close_minutes: int = 10,
    ):
        self._group_code = group_code
        self._lab_kinds = tuple(lab_kinds)
        self._course_id = course_id
        self._course_name = normalize_subject(course_name)
        self._auto_open_minutes = auto_open_minutes
        self._auto_close_minutes = auto_close_minutes
        self._fetcher: Fetcher | None = None
        self._group_directory: dict[int, str] | None = None

    # ------------------------------------------------------------------
    # Public API for the admin refresh endpoint and live schedule requests
    # ------------------------------------------------------------------
    def fetch_raw(self, group_code: str | None = None) -> list:
        """Fetch just one group's timetable directly from GUAP."""
        gc = group_code or self._group_code
        if not gc:
            raise ScheduleConfigError(
                "GuapScheduleSource.fetch_raw: group_code is required "
                "(set `guap_group_id` in schedules/<course_id>.yaml under the group)"
            )
        if self._fetcher is None:
            self._fetcher = Fetcher(ttl=0)
        if self._group_directory is None:
            try:
                self._group_directory = parse_directories(self._fetcher.index())["groups"]
            except Exception as e:
                raise ScheduleConfigError(
                    f"Failed to load GUAP group directory: {type(e).__name__}: {e}"
                )

        group_ids = [
            group_id
            for group_id, name in self._group_directory.items()
            if normalize_subject(name) == normalize_subject(gc)
        ]
        if not group_ids:
            raise ScheduleConfigError(
                f"Group {gc!r} not found in GUAP directory. Verify the code "
                "on https://guap.ru/rasp."
            )
        if len(group_ids) > 1:
            raise ScheduleConfigError(f"Group {gc!r} is ambiguous in GUAP directory")
        try:
            return parse_lessons(self._fetcher.by_group(group_ids[0]))
        except Exception as e:
            raise ScheduleConfigError(
                f"Failed to fetch GUAP timetable for group {gc!r}: "
                f"{type(e).__name__}: {e}"
            )

    def to_yaml_sessions(self, raw_lessons: list) -> list[dict]:
        """Convert raw lessons into the YAML `slots:` structure used by
        schedules/<course_id>.yaml.

        Each returned dict has:
          id:          "guap-<weekday>-<slot>" (e.g. "guap-mon-3")
          recurrence:  {day: "monday", week: "all"}   (GUAP doesn't expose parity)
          start:       "HH:MM"
          end:         "HH:MM"
          subject:     "..."
          room:        "..."
          teacher:     "..."

        `labs` is omitted (= all labs of course). The teacher assigns labs
        manually after refresh via the admin editor, because GUAP doesn't
        know our lab keys.

        Only lab-kind lessons are included (kind in DEFAULT_LAB_KINDS);
        lectures and practices are filtered out — they don't generate
        defense slots.

        All values are explicitly coerced to str — the parser exposes some
        fields as `Ref` objects (a custom class), and yaml.dump would
        otherwise serialize them with !!python/object: tags that
        yaml.safe_load can't read back.
        """
        seen_keys: set[tuple[int, int]] = set()
        out: list[dict] = []
        for lesson in raw_lessons:
            kind = getattr(lesson, 'kind', None)
            if kind not in self._lab_kinds:
                continue
            weekday_ru = getattr(lesson, 'day', None)
            iso = GUAP_WEEKDAYS.get(weekday_ru)
            if iso is None:
                continue
            slot = getattr(lesson, 'slot', None)
            if slot is None:
                continue
            slot = int(slot)
            key = (iso, slot)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            start_t, end_t = GUAP_SLOTS.get(slot, (time(9, 0), time(21, 10)))
            out.append({
                'id': f"guap-{ISO_TO_SHORT_DAY[iso]}-{slot}",
                'recurrence': {'day': ISO_TO_YAML_DAY[iso], 'week': 'all'},
                'start': str(start_t.isoformat()),
                'end': str(end_t.isoformat()),
                'subject': str(getattr(lesson, 'subject', '') or ''),
                'room': str(getattr(lesson, 'room', '') or ''),
                'teacher': ", ".join(
                    str(teacher) for teacher in getattr(lesson, 'teachers', [])
                ),
            })
        return out

    # ------------------------------------------------------------------
    # ScheduleSource.list_sessions — live, group-specific GUAP requests
    # ------------------------------------------------------------------
    def list_sessions(
        self,
        group_id: str,
        since: datetime,
        until: datetime,
    ) -> Iterable[ScheduledSession]:
        lessons = self.fetch_raw(self._group_code or group_id)
        day = since.date()
        last = until.date()
        while day <= last:
            weekday = day.weekday()
            for lesson in lessons:
                if GUAP_WEEKDAYS.get(getattr(lesson, 'day', None)) != weekday:
                    continue
                if getattr(lesson, 'kind', None) not in self._lab_kinds:
                    continue
                subject = str(getattr(lesson, 'subject', '') or '')
                if self._course_name and normalize_subject(subject) != self._course_name:
                    continue
                starts_at, ends_at = self._lesson_times(lesson, day)
                if starts_at < since or starts_at >= until:
                    continue
                room_value = getattr(lesson, 'room', None)
                room = str(room_value) if room_value else None
                teachers = getattr(lesson, 'teachers', [])
                teacher = ", ".join(str(value) for value in teachers) or None
                yield ScheduledSession(
                    course_id=self._course_id,
                    group_id=group_id,
                    subject=subject or 'Лабораторная',
                    starts_at=starts_at,
                    ends_at=ends_at,
                    auto_open_at=starts_at - timedelta(minutes=self._auto_open_minutes),
                    auto_close_at=ends_at + timedelta(minutes=self._auto_close_minutes),
                    source='guap',
                    slot_id=f"guap-{ISO_TO_SHORT_DAY[weekday]}-{getattr(lesson, 'slot', '?')}",
                    allowed_lab_ids=None,  # GUAP doesn't know our lab ids
                    room=room,
                    teacher=teacher,
                    raw={'day': getattr(lesson, 'day', None),
                         'slot': getattr(lesson, 'slot', None),
                         'kind': getattr(lesson, 'kind', None)},
                )
            day += timedelta(days=1)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _lesson_times(self, lesson, day):
        start_str = getattr(lesson, 'start_time', None) or getattr(lesson, 'start', None)
        end_str = getattr(lesson, 'end_time', None) or getattr(lesson, 'end', None)
        if start_str and end_str:
            try:
                s = time.fromisoformat(str(start_str))
                e = time.fromisoformat(str(end_str))
                return datetime.combine(day, s), datetime.combine(day, e)
            except ValueError:
                pass
        slot = getattr(lesson, 'slot', None)
        if slot in GUAP_SLOTS:
            s, e = GUAP_SLOTS[slot]
            return datetime.combine(day, s), datetime.combine(day, e)
        return (datetime.combine(day, time(9, 0)),
                datetime.combine(day, time(21, 10)))
