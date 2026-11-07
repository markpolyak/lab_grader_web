"""Manual schedule source: reads `schedules/<course_id>.yaml` and serves
sessions for any group in the file.

The "manual" name is a misnomer — it's actually the universal YAML reader
used by *every* source after a refresh. After `source: guap` fetches raw
lessons and writes them back into the YAML, the file is read by this same
module on every subsequent request. There is no in-memory distinction
between "freshly parsed from GUAP" and "typed by hand": both end up as rows
in `groups.<id>.slots` and are served identically.

YAML structure — one file per course, all groups of the course inside:

    course-id: os-2026                 # sanity check; must match course id

    groups:
      "4432":
        timezone: "UTC+3"
        source: guap                    # who originally produced these slots
        guap_group_id: "4432"           # code on guap.ru/rasp (for refresh button)

        auto_open_minutes_before: 15
        auto_close_minutes_after: 10

        slots:
          - id: "mon-1"
            recurrence: {day: monday, week: all}
            start: "13:00"
            end: "14:30"
            labs: ["1", "2"]           # null/missing = all, [] = closed
            room: "301"
            subject: "Операционные системы"
          - id: "extra-lab3"
            date: "2026-11-20"          # one-off slot
            start: "17:00"
            end: "18:30"
            labs: ["3"]
            note: "Доп. консультация"

        corrections:
          - id: "corr-lab2-move"
            description: "Перенос ЛР2 на 17:00"
            date: "2026-11-09"
            remove_slots:
              - slot_id: "mon-1"
                labs: ["2"]            # remove only lab "2" from slot "mon-1"
            add_slots:
              - id: "mon-1-lab2-moved"
                start: "17:00"
                end: "18:30"
                labs: ["2"]
                room: "301"

        queue_settings:
          max_students_per_slot: 30
          allow_multiple_labs: true
          auto_requeue_on_no_show: false
          all_lab_ids: ["1", "2", "3", "quiz"]

      "4433":
        ...                             # second group, same structure
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from .base import ScheduleSource, ScheduledSession, ScheduleConfigError
from .registry import register_source


# YAML weekday codes -> Python weekday() (Mon=0 .. Sun=6)
WEEKDAYS = {
    'monday': 0, 'mon': 0,
    'tuesday': 1, 'tue': 1,
    'wednesday': 2, 'wed': 2,
    'thursday': 3, 'thu': 3,
    'friday': 4, 'fri': 4,
    'saturday': 5, 'sat': 5,
    'sunday': 6, 'sun': 6,
}


# ----------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------
def _parse_time(value, where: str) -> time:
    if value is None:
        raise ScheduleConfigError(f"{where}: `start` and `end` are required")
    try:
        return time.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ScheduleConfigError(f"{where}: time must be HH:MM, got {value!r}")


def _parse_date(value, where: str) -> date:
    if value is None:
        raise ScheduleConfigError(f"{where}: `date` is required")
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ScheduleConfigError(f"{where}: date must be YYYY-MM-DD, got {value!r}")


def _validate_slot(s: dict, where: str, allow_no_date: bool = False) -> None:
    """Validate one slot dict. Raises ScheduleConfigError on bad data.

    `allow_no_date=True` is used for slots inside `corrections.add_slots`:
    those slots inherit the date from the correction, so they may omit both
    `date` and `recurrence`.
    """
    if not isinstance(s, dict):
        raise ScheduleConfigError(f"{where}: slot must be a dict, got {type(s).__name__}")
    if not s.get('id'):
        raise ScheduleConfigError(f"{where}: `id` is required (unique within group)")

    has_date = s.get('date') is not None
    has_recur = s.get('recurrence') is not None
    if not has_date and not has_recur and not allow_no_date:
        raise ScheduleConfigError(
            f"{where}: slot {s.get('id')!r} must have either `date` or `recurrence`"
        )
    if has_date and has_recur:
        raise ScheduleConfigError(
            f"{where}: slot {s.get('id')!r} cannot have both `date` and `recurrence`"
        )

    if has_recur:
        rec = s['recurrence']
        if not isinstance(rec, dict):
            raise ScheduleConfigError(f"{where}: `recurrence` must be a dict")
        day = rec.get('day')
        if day not in WEEKDAYS:
            raise ScheduleConfigError(
                f"{where}: `recurrence.day` must be one of {list(WEEKDAYS)}, got {day!r}"
            )
        week = rec.get('week', 'all')
        if week not in ('all', 'odd', 'even'):
            raise ScheduleConfigError(
                f"{where}: `recurrence.week` must be all|odd|even, got {week!r}"
            )

    # start/end required
    _parse_time(s.get('start'), f"{where} (slot {s.get('id')!r})")
    _parse_time(s.get('end'), f"{where} (slot {s.get('id')!r})")
    start_t = _parse_time(s.get('start'), where)
    end_t = _parse_time(s.get('end'), where)
    if end_t <= start_t:
        raise ScheduleConfigError(
            f"{where}: slot {s.get('id')!r} `end` must be after `start`"
        )

    # labs: list, None, or absent
    labs = s.get('labs')
    if labs is not None and not isinstance(labs, list):
        raise ScheduleConfigError(
            f"{where}: slot {s.get('id')!r} `labs` must be a list, null, or absent"
        )
    if isinstance(labs, list):
        for x in labs:
            if not isinstance(x, str):
                raise ScheduleConfigError(
                    f"{where}: slot {s.get('id')!r} `labs` items must be strings"
                )


def _validate_correction(c: dict, where: str) -> None:
    if not isinstance(c, dict):
        raise ScheduleConfigError(f"{where}: correction must be a dict")
    if not c.get('id'):
        raise ScheduleConfigError(f"{where}: correction `id` is required")
    _parse_date(c.get('date'), f"{where} (correction {c.get('id')!r})")

    for key in ('remove_slots', 'add_slots'):
        items = c.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            raise ScheduleConfigError(
                f"{where}: correction {c.get('id')!r} `{key}` must be a list"
            )
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ScheduleConfigError(
                    f"{where}: correction {c.get('id')!r} {key}[{i}] must be a dict"
                )
            if key == 'remove_slots':
                if not item.get('slot_id'):
                    raise ScheduleConfigError(
                        f"{where}: remove_slots[{i}] requires `slot_id`"
                    )
                labs = item.get('labs')
                if labs is not None and not isinstance(labs, list):
                    raise ScheduleConfigError(
                        f"{where}: remove_slots[{i}].labs must be a list or null"
                    )
            else:  # add_slots — same shape as a slot, but date is optional
                # (the date comes from the correction's `date` field).
                _validate_slot(
                    item,
                    f"{where} (correction {c.get('id')!r} add_slots[{i}])",
                    allow_no_date=True,
                )


def _validate_group(group_id: str, group_cfg: dict, where: str) -> None:
    """Validate one group's block: source, slots, corrections."""
    if not isinstance(group_cfg, dict):
        raise ScheduleConfigError(f"{where}: group must be a mapping")

    slots = group_cfg.get('slots')
    if not isinstance(slots, list):
        raise ScheduleConfigError(
            f"{where}: `slots` must be a list (use [] for an empty group, "
            "or call the refresh endpoint to populate from GUAP)"
        )

    seen_ids: set[str] = set()
    for i, s in enumerate(slots):
        _validate_slot(s, f"{where} slots[{i}]")
        if s['id'] in seen_ids:
            raise ScheduleConfigError(
                f"{where} slots[{i}]: duplicate slot id {s['id']!r}"
            )
        seen_ids.add(s['id'])

    corrections = group_cfg.get('corrections', [])
    if corrections is not None and not isinstance(corrections, list):
        raise ScheduleConfigError(f"{where}: `corrections` must be a list")
    for i, c in enumerate(corrections or []):
        _validate_correction(c, f"{where} corrections[{i}]")


# ----------------------------------------------------------------------
# Slot resolution (date match + recurrence parity)
# ----------------------------------------------------------------------
def _slot_matches_date(slot: dict, day: date) -> bool:
    """Does this base slot fire on the given date?"""
    if slot.get('date'):
        try:
            return _parse_date(slot['date'], "") == day
        except ScheduleConfigError:
            return False
    rec = slot.get('recurrence')
    if not rec:
        return False
    day_name = rec.get('day')
    if WEEKDAYS.get(day_name) != day.weekday():
        return False
    week = rec.get('week', 'all')
    if week == 'all':
        return True
    # ISO week parity. odd = week number % 2 == 1; even = == 0.
    iso_week = day.isocalendar()[1]
    if week == 'odd':
        return iso_week % 2 == 1
    if week == 'even':
        return iso_week % 2 == 0
    return False


def _resolve_labs(slot_labs: list | None) -> list[str] | None:
    """Normalize a slot's `labs` field into the runtime representation.

      None  → all labs (queue endpoint resolves against course.labs)
      []    → closed (allowed_lab_ids = [])
      [...] → only these
    """
    if slot_labs is None:
        return None
    if not slot_labs:
        return []
    return list(slot_labs)


# ----------------------------------------------------------------------
# Source
# ----------------------------------------------------------------------
@register_source('manual')
class ManualScheduleSource(ScheduleSource):
    """Reads sessions for ONE group from a parsed schedules/<course>.yaml.

    A file holds all groups of a course, but each ManualScheduleSource
    instance is scoped to one group. The factory `parse_schedule_file` in
    __init__.py builds one source per (file, group_id) pair and returns
    them as a dict.
    """

    def __init__(
        self,
        course_id: str,
        group_id: str,
        group_cfg: dict,
    ):
        self._course_id = course_id
        self._group_id = str(group_id)

        if not isinstance(group_cfg, dict):
            raise ScheduleConfigError(
                f"Schedule file: group {group_id!r} must be a mapping"
            )

        self._source_name = str(group_cfg.get('source', 'manual'))
        self._guap_group_id = group_cfg.get('guap_group_id')
        self._timezone = group_cfg.get('timezone', 'UTC+3')

        self._auto_open_min = int(group_cfg.get('auto_open_minutes_before', 0))
        self._auto_close_min = int(group_cfg.get('auto_close_minutes_after', 0))

        self._slots = list(group_cfg.get('slots', []))
        self._corrections = list(group_cfg.get('corrections', []))
        self._queue_settings = dict(group_cfg.get('queue_settings', {}))

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------
    @property
    def guap_group_id(self) -> str | None:
        """The GUAP group code, used by the admin refresh endpoint."""
        return str(self._guap_group_id) if self._guap_group_id is not None else None

    @property
    def source_name(self) -> str:
        """Who originally produced these slots ('guap', 'manual', etc.)."""
        return self._source_name

    @property
    def timezone(self) -> str:
        return self._timezone

    @property
    def queue_settings(self) -> dict:
        """The `queue_settings:` block, as a dict. Used by the queue module
        to read max_students_per_slot, allow_multiple_labs, etc."""
        return self._queue_settings

    @property
    def all_lab_ids(self) -> list[str] | None:
        """Convenience: the list of all labs of this course, if declared
        in queue_settings. Used to expand `labs: null` slots when a
        correction needs to remove a specific lab."""
        ids = self._queue_settings.get('all_lab_ids')
        return list(ids) if isinstance(ids, list) else None

    # ------------------------------------------------------------------
    # ScheduleSource interface
    # ------------------------------------------------------------------
    def list_sessions(
        self,
        group_id: str,
        since: datetime,
        until: datetime,
    ) -> Iterable[ScheduledSession]:
        """Yield sessions whose starts_at is in [since, until).

        Algorithm per day:
          1. Collect base slots matching this weekday/date (and week parity).
          2. Apply `corrections` for this date:
             - remove_slots: drop entire slot, or remove specific labs.
             - add_slots: append new slots for this date.
          3. For each surviving slot, compute start/end datetimes + auto
             open/close times. Check window overlap; yield if it overlaps.
        """
        all_labs = self.all_lab_ids
        day = since.date()
        last = until.date()
        while day <= last:
            slots_for_day = self._resolve_day(day, all_labs)
            for s in slots_for_day:
                starts_at, ends_at = self._session_times(s, day)
                if starts_at < since or starts_at >= until:
                    continue
                yield ScheduledSession(
                    course_id=self._course_id,
                    group_id=str(group_id),
                    subject=s.get('subject') or 'Лабораторная',
                    starts_at=starts_at,
                    ends_at=ends_at,
                    auto_open_at=starts_at - timedelta(minutes=self._auto_open_min),
                    auto_close_at=ends_at + timedelta(minutes=self._auto_close_min),
                    source=self._source_name,
                    slot_id=s.get('id'),
                    allowed_lab_ids=_resolve_labs(s.get('labs')),
                    room=s.get('room'),
                    teacher=s.get('teacher'),
                    note=s.get('note'),
                    raw=s,
                )
            day += timedelta(days=1)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _resolve_day(self, day: date, all_labs: list[str] | None) -> list[dict]:
        """For one date: take base slots matching this day, then apply
        corrections for this date. Returns the final list of slot dicts.

        Note: corrections are applied by `slot_id`, which references the
        base slots' `id`. Added slots (in `add_slots`) don't need a
        matching `slot_id` in `remove_slots` — they're appended.
        """
        # 1. Base slots matching this day.
        result: list[dict] = []
        for s in self._slots:
            if _slot_matches_date(s, day):
                # Deep-copy to avoid mutating the parsed YAML on later days.
                s_copy = dict(s)
                # If labs is null (all) and a correction might want to remove
                # a specific lab, we materialize the list now using all_lab_ids.
                # Otherwise leave None to mean "all labs".
                if s_copy.get('labs') is None and all_labs is not None:
                    s_copy['labs'] = list(all_labs)
                result.append(s_copy)

        # 2. Find corrections for this date.
        for c in self._corrections:
            try:
                if _parse_date(c['date'], "") != day:
                    continue
            except ScheduleConfigError:
                continue

            # 2a. Remove slots / labs.
            for rem in c.get('remove_slots', []) or []:
                slot_id = rem.get('slot_id')
                labs_to_remove = rem.get('labs')
                if labs_to_remove is None:
                    # Remove the entire slot.
                    result = [s for s in result if s.get('id') != slot_id]
                else:
                    # Remove only the specified labs from this slot.
                    for s in result:
                        if s.get('id') == slot_id:
                            current = s.get('labs')
                            if current is None:
                                # Shouldn't happen — we materialized above.
                                continue
                            s['labs'] = [x for x in current if x not in labs_to_remove]

            # 2b. Add new slots for this date.
            for add in c.get('add_slots', []) or []:
                # add_slots entries are slot dicts, possibly without `date`
                # (the date is the correction's date).
                add_copy = dict(add)
                add_copy['date'] = c['date']
                result.append(add_copy)

        return result

    @staticmethod
    def _session_times(s: dict, day: date) -> tuple[datetime, datetime]:
        """Combine the slot's `start`/`end` times with the given date."""
        start_t = _parse_time(s.get('start'), "slot")
        end_t = _parse_time(s.get('end'), "slot")
        return (
            datetime.combine(day, start_t),
            datetime.combine(day, end_t),
        )
