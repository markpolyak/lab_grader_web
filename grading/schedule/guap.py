"""GUAP (guap.ru/rasp) schedule source.

This source is special: it has two halves.
  1. `fetch_raw(group_code)` — used by the admin "refresh schedule" endpoint.
     Runs `python -m suai_rasp dump --refresh` and loads the resulting
     schedule.json via `suai_rasp.model.Schedule.from_json`. Returns the
     parser's raw Lesson objects for the requested group.
  2. `to_yaml_sessions(raw_lessons)` — converts those Lesson objects into
     the YAML `slots:` structure that gets written back to
     schedules/<course_id>.yaml under the appropriate group.

After a refresh, the YAML file is the source of truth — `ManualScheduleSource`
reads it on every subsequent request. `GuapScheduleSource` is *not* used
to serve `list_sessions` in production; it's a one-shot fetcher invoked
only by the admin refresh endpoint.

The parser lives at https://github.com/markpolyak/suai-schedule-parser
and is wired in as a git submodule under vendor/suai-schedule-parser/.
We only use its read path (Schedule.from_json + by_group). The validate /
check_hypothesis / plan modules are intentionally NOT imported — they solve
a different problem (timetable conflict checking) and would pull in extra
dependencies we don't need.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Iterable

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

# Standard GUAP slot numbers -> (start, end) times. Used as a fallback when
# the parser doesn't give us explicit times (the slot number is the stable
# identifier GUAP uses).
GUAP_SLOTS = {
    1: (time(9, 0),   time(10, 30)),
    2: (time(10, 40), time(12, 10)),
    3: (time(13, 0),  time(14, 30)),
    4: (time(14, 40), time(16, 10)),
    5: (time(16, 20), time(17, 50)),
    6: (time(18, 0),  time(19, 30)),
    7: (time(19, 40), time(21, 10)),
}

DEFAULT_LAB_KINDS = ("ЛР", "Лабораторная", "Лабораторное занятие")
DEFAULT_SNAPSHOT = "data/schedule.json"


@register_source('guap')
class GuapScheduleSource(ScheduleSource):
    """GUAP schedule fetcher. Constructed by the admin refresh endpoint
    with the GUAP group code from schedules/<course_id>.yaml.

    Usage:

        src = GuapScheduleSource(group_code="4432", snapshot_path="data/schedule.json")
        raw = src.fetch_raw("4432")            # returns list of Lesson objects
        yaml_slots = src.to_yaml_sessions(raw)  # list of dicts for YAML

    `list_sessions` is *not* used in production — once fetched, lessons
    live in the YAML file and are served by ManualScheduleSource. We
    implement it anyway for completeness and for tests that want to
    bypass the YAML roundtrip.
    """

    def __init__(
        self,
        group_code: str | None = None,
        snapshot_path: str | None = None,
        lab_kinds: tuple[str, ...] = DEFAULT_LAB_KINDS,
        course_id: str = "",
        auto_open_minutes: int = 15,
        auto_close_minutes: int = 10,
    ):
        self._group_code = group_code
        self._snapshot_path = snapshot_path or DEFAULT_SNAPSHOT
        self._lab_kinds = tuple(lab_kinds)
        self._course_id = course_id
        self._auto_open_minutes = auto_open_minutes
        self._auto_close_minutes = auto_close_minutes
        # Lazy-loaded snapshot state.
        self._schedule = None
        self._group_internal_id = None

    # ------------------------------------------------------------------
    # Public API for the admin refresh endpoint
    # ------------------------------------------------------------------
    def fetch_raw(self, group_code: str | None = None) -> list:
        """Run `python -m suai_rasp dump --refresh` and return raw lessons
        for the group.

        group_code: GUAP group code, e.g. "4432". Falls back to the code
        passed at construction time.

        Returns: list of suai_rasp.model.Lesson objects (or whatever the
        parser exposes; we read attributes defensively in to_yaml_sessions).

        Raises ScheduleConfigError on any setup problem (parser not
        installed, snapshot missing, group not found). These are surfaced
        as 400s by the admin endpoint.
        """
        gc = group_code or self._group_code
        if not gc:
            raise ScheduleConfigError(
                "GuapScheduleSource.fetch_raw: group_code is required "
                "(set `guap_group_id` in schedules/<course_id>.yaml under the group)"
            )
        self._ensure_snapshot()
        sch, gid = self._load(gc)
        return list(sch.by_group.get(gid, []))

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
                'teacher': str(getattr(lesson, 'teacher', '') or ''),
            })
        return out

    # ------------------------------------------------------------------
    # ScheduleSource.list_sessions — used only for tests / direct reads
    # ------------------------------------------------------------------
    def list_sessions(
        self,
        group_id: str,
        since: datetime,
        until: datetime,
    ) -> Iterable[ScheduledSession]:
        self._ensure_snapshot()
        sch, gid = self._load(self._group_code or group_id)
        lessons = sch.by_group.get(gid, [])
        day = since.date()
        last = until.date()
        while day <= last:
            weekday = day.weekday()
            for lesson in lessons:
                if GUAP_WEEKDAYS.get(getattr(lesson, 'day', None)) != weekday:
                    continue
                if getattr(lesson, 'kind', None) not in self._lab_kinds:
                    continue
                starts_at, ends_at = self._lesson_times(lesson, day)
                if starts_at < since or starts_at >= until:
                    continue
                yield ScheduledSession(
                    course_id=self._course_id,
                    group_id=group_id,
                    subject=getattr(lesson, 'subject', 'Лабораторная') or 'Лабораторная',
                    starts_at=starts_at,
                    ends_at=ends_at,
                    auto_open_at=starts_at - timedelta(minutes=self._auto_open_minutes),
                    auto_close_at=ends_at + timedelta(minutes=self._auto_close_minutes),
                    source='guap',
                    slot_id=f"guap-{ISO_TO_SHORT_DAY[weekday]}-{getattr(lesson, 'slot', '?')}",
                    allowed_lab_ids=None,  # GUAP doesn't know our lab ids
                    room=getattr(lesson, 'room', None),
                    teacher=getattr(lesson, 'teacher', None),
                    raw={'day': getattr(lesson, 'day', None),
                         'slot': getattr(lesson, 'slot', None),
                         'kind': getattr(lesson, 'kind', None)},
                )
            day += timedelta(days=1)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ensure_snapshot(self) -> None:
        """If the snapshot file is missing, run `python -m suai_rasp dump
        --refresh` to build it. The dump takes ~30s on first run.

        In production this should be refreshed by a nightly cron, but for
        the admin "refresh" endpoint we want fresh data on demand, so we
        refresh here too. The parser HTTP-caches under .cache/, so a
        subsequent refresh within ~24h is fast.
        """
        path = Path(self._snapshot_path)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [sys.executable, "-m", "suai_rasp", "dump", "--refresh"],
                check=True,
                cwd=str(path.parent),
            )
        except subprocess.CalledProcessError as e:
            raise ScheduleConfigError(
                f"`suai_rasp dump --refresh` failed (exit {e.returncode}). "
                "Is suai-schedule-parser installed and on PYTHONPATH?"
            )
        except FileNotFoundError:
            raise ScheduleConfigError(
                "`python -m suai_rasp` not found. Install suai-schedule-parser "
                "or vendor it under vendor/suai-schedule-parser/."
            )
        produced = path.parent / "schedule.json"
        if produced.exists() and produced != path:
            produced.rename(path)
        if not path.exists():
            raise ScheduleConfigError(
                f"Snapshot was not created at {path}. Run "
                f"`python -m suai_rasp dump --refresh` manually to debug."
            )

    def _load(self, group_code: str):
        if self._schedule is not None and self._group_internal_id is not None:
            return self._schedule, self._group_internal_id
        try:
            from suai_rasp.model import Schedule
        except ImportError as e:
            raise ScheduleConfigError(
                "Cannot import `suai_rasp`. Either `pip install -e "
                "vendor/suai-schedule-parser` or ensure the vendored package "
                f"is on sys.path. Original error: {e}"
            )
        try:
            self._schedule = Schedule.from_json(self._snapshot_path)
        except FileNotFoundError:
            raise ScheduleConfigError(
                f"GUAP schedule snapshot not found at {self._snapshot_path}."
            )
        except Exception as e:
            raise ScheduleConfigError(
                f"Failed to load GUAP snapshot from {self._snapshot_path}: {e}"
            )
        try:
            self._group_internal_id = self._schedule.find_group(group_code)
        except Exception:
            raise ScheduleConfigError(
                f"Group {group_code!r} not found in snapshot. Verify the code "
                "on https://guap.ru/rasp."
            )
        return self._schedule, self._group_internal_id

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
