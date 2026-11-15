"""Fetch group-specific GUAP schedules and expose matching lab sessions."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from suai_rasp.fetch import Fetcher
from suai_rasp.model import LESSON_TIMES, WEEK_ANY, WEEK_EVEN, WEEK_ODD
from suai_rasp.parse import parse_directories, parse_lessons

from .base import ScheduleSource, ScheduledSession, ScheduleConfigError
from .manual import ManualScheduleSource
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
GUAP_WEEKS = {
    WEEK_ANY: "all",
    WEEK_ODD: "odd",
    WEEK_EVEN: "even",
}


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
        corrections: list[dict] | None = None,
        queue_settings: dict | None = None,
    ):
        self._group_code = group_code
        self._lab_kinds = tuple(lab_kinds)
        self._course_id = course_id
        self._course_name = normalize_subject(course_name)
        self._auto_open_minutes = auto_open_minutes
        self._auto_close_minutes = auto_close_minutes
        self._corrections = list(corrections or [])
        self._queue_settings = dict(queue_settings or {})
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
          recurrence:  {day: "monday", week: "all|odd|even"}
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
        seen_keys: set[tuple[int, int, int]] = set()
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
            week = getattr(lesson, 'week', WEEK_ANY)
            if week not in GUAP_WEEKS:
                week = WEEK_ANY
            key = (iso, slot, week)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            lesson_day = date(2024, 1, 1) + timedelta(days=iso)
            starts_at, ends_at = self._lesson_times(lesson, lesson_day)
            out.append({
                'id': self._slot_id(iso, slot, week),
                'recurrence': {
                    'day': ISO_TO_YAML_DAY[iso],
                    'week': GUAP_WEEKS[week],
                },
                'start': starts_at.time().isoformat(),
                'end': ends_at.time().isoformat(),
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
        if self._course_name:
            lessons = [
                lesson for lesson in lessons
                if normalize_subject(getattr(lesson, "subject", ""))
                == self._course_name
            ]
        slots = self.to_yaml_sessions(lessons)
        source = ManualScheduleSource(
            course_id=self._course_id,
            group_id=group_id,
            group_cfg={
                "source": "guap",
                "auto_open_minutes_before": self._auto_open_minutes,
                "auto_close_minutes_after": self._auto_close_minutes,
                "queue_settings": self._queue_settings,
                "slots": slots,
                "corrections": self._corrections_for_slots(slots),
            },
        )
        yield from source.list_sessions(group_id, since, until)

    @staticmethod
    def _slot_id(weekday: int, slot: int, week: int) -> str:
        base = f"guap-{ISO_TO_SHORT_DAY[weekday]}-{slot}"
        if week == WEEK_ODD:
            return f"{base}-odd"
        if week == WEEK_EVEN:
            return f"{base}-even"
        return base

    def _corrections_for_slots(self, slots: list[dict]) -> list[dict]:
        """Expand legacy slot IDs to parity-specific IDs after a refresh."""
        slot_ids = {slot["id"] for slot in slots}
        corrections = []
        for correction in self._corrections:
            copied = dict(correction)
            remove_slots = []
            for removal in correction.get("remove_slots", []) or []:
                copied_removal = dict(removal)
                slot_id = copied_removal.get("slot_id")
                if slot_id not in slot_ids:
                    matching_ids = [
                        candidate for candidate in slot_ids
                        if candidate.startswith(f"{slot_id}-")
                    ]
                    if matching_ids:
                        remove_slots.extend(
                            {**copied_removal, "slot_id": matching_id}
                            for matching_id in matching_ids
                        )
                        continue
                remove_slots.append(copied_removal)
            if "remove_slots" in correction:
                copied["remove_slots"] = remove_slots
            corrections.append(copied)
        return corrections

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
