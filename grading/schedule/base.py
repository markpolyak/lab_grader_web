"""Abstract interface for schedule sources.

A "schedule source" produces a list of upcoming defense slots (sessions)
for one group. Concrete sources: GUAP website parser, manual YAML config,
or any other university's portal.

Each session knows:
  - when it starts/ends (starts_at, ends_at)
  - when the queue for it opens/closes (auto_open_at, auto_close_at)
  - which labs can be defended in this slot (allowed_lab_ids)
  - the slot's stable id (slot_id), used by corrections to reference it

To add a new source, create `grading/schedule/<name>.py` with a class that
inherits `ScheduleSource`, decorate it with `@register_source('<name>')`,
and use `source: <name>` in `schedules/<course_id>.yaml` under the group.
No other code changes are needed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


class ScheduleError(Exception):
    """Base class for schedule-related errors."""


class ScheduleConfigError(ScheduleError):
    """A linked `schedules/*.yaml` file is invalid.

    Raised on startup (logged via warn_about_schedule, see main.py) and on
    any per-request call that needs the schedule. Same pattern as
    JoinConfigError / TeamConfigError.
    """


@dataclass(frozen=True)
class ScheduledSession:
    """One upcoming defense slot for one group, fully resolved to a date.

    A session is *not* yet an open queue — it is a slot in time. The queue
    is materialized (a sheet tab in the course's spreadsheet) lazily, on
    the first student join request inside the [auto_open_at, auto_close_at]
    window, by grading/queue/builder.py.

    `allowed_lab_ids` semantics:
      - None  → all labs of the course are allowed (slot's `labs` was null/absent)
      - []    → slot is closed for queue (slot's `labs` was an empty list)
      - [...] → only these lab ids may be defended in this slot
    """
    course_id: str
    group_id: str
    subject: str
    starts_at: datetime
    ends_at: datetime
    auto_open_at: datetime
    auto_close_at: datetime
    source: str = 'manual'
    slot_id: str | None = None
    allowed_lab_ids: list[str] | None = None
    room: str | None = None
    teacher: str | None = None
    note: str | None = None
    raw: dict = field(default_factory=dict)   # for debugging / ADR


class ScheduleSource(ABC):
    """Strategy interface. One implementation per university / source.

    Two responsibilities:
      1. `list_sessions(group_id, since, until)` — read the schedule and
         yield ScheduledSession objects whose starts_at falls in [since, until).
         Used by the queue builder and the "is queue open now?" check.
      2. `fetch_raw()` (optional) — for sources that can pull fresh data
         from an external system (GUAP website). Called by the admin
         "refresh schedule" endpoint; returns raw lesson objects that
         `to_yaml_sessions` then converts into the YAML structure saved
         to schedules/<course_id>.yaml.

    Manual sources don't have an external system to pull from, so they
    don't implement `fetch_raw` — calling it raises NotImplementedError,
    which the admin endpoint catches and turns into a 400.
    """

    name: str = ""   # set by @register_source

    @abstractmethod
    def list_sessions(
        self,
        group_id: str,
        since: datetime,
        until: datetime,
    ) -> Iterable[ScheduledSession]:
        """Yield sessions for `group_id` whose starts_at is in [since, until).
        Order: ascending by starts_at."""

    def fetch_raw(self, group_code: str) -> list:
        """Pull raw lessons from the external system (e.g. GUAP website).

        Default: not supported. Override in sources that have an external
        source. Returns whatever the underlying parser produces —
        `to_yaml_sessions` knows how to interpret it.
        """
        raise NotImplementedError(
            f"Source {self.name!r} does not support fetching from an "
            "external system. It is read-only (configured via YAML)."
        )

    def to_yaml_sessions(self, raw_lessons: list) -> list[dict]:
        """Convert raw lessons returned by `fetch_raw` into the YAML
        `slots:` structure written to schedules/<course_id>.yaml.

        Default: not supported. Override in sources that implement fetch_raw.
        """
        raise NotImplementedError(
            f"Source {self.name!r} does not support YAML serialization."
        )
