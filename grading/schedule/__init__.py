"""Schedule module: pluggable sources of lab-session times.

Public API:
    - ScheduleSource:           abstract interface (Strategy pattern).
    - ScheduledSession:         one upcoming defense slot (auto_open_at/close_at set).
    - register_source(name):    decorator that registers a new source.
    - parse_schedule_file():    read schedules/<course_id>.yaml → dict[group_id, ManualScheduleSource].
    - iter_schedule_files():   walk all schedules/*.yaml files (for startup warnings).
    - warn_about_schedule():    log problems at startup (mirrors warn_about_join_links).
    - ScheduleConfigError:      raised on bad config (same UX as JoinConfigError).

File layout:
    courses/<course_id>.yaml       — course config; references schedule file via
                                      `course.schedule-file: <name>.yaml`.
    schedules/<course_id>.yaml     — one file per course, all groups inside.

Architecture:
    - One file per course, multiple groups inside (per Artem + zrt decision
      in chat 07.11: "в один файл несколько групп, чтобы не плодить файлы").
    - `parse_schedule_file` reads the YAML, validates it, and returns a
      dict {group_id: ManualScheduleSource} — one source per group, all
      sharing the same file.
    - The course's YAML references the schedule file by name:

        course:
          name: ...
          schedule-file: os-2026.yaml   # → schedules/os-2026.yaml

    - GuapScheduleSource is only invoked by the admin "refresh" endpoint,
      which calls fetch_raw + to_yaml_sessions and writes the result back
      into the YAML file under groups.<id>.slots. After that, the
      ManualScheduleSource for that group reads it.

Adding a new university's schedule:
    1. Create `grading/schedule/<uni>.py` with a class inheriting ScheduleSource.
       Implement fetch_raw + to_yaml_sessions (for the admin refresh endpoint)
       and list_sessions (for direct reads, used by tests).
    2. Decorate it: `@register_source('<uni>')`.
    3. Import it here: `from . import <uni>`.
    4. Use `source: <uni>` under a group in schedules/<course_id>.yaml.
    No changes to main.py, queue code, or other sources.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Iterator

import yaml

# --- Make the vendored suai-schedule-parser importable as `suai_rasp` ---
_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # grading/schedule/ → root

_CANDIDATE_PARSER_PATHS = [
    _PROJECT_ROOT / "vendor" / "suai-schedule-parser",
    _PROJECT_ROOT / "grading" / "vendor" / "suai-schedule-parser",
    _PROJECT_ROOT / "vendor" / "suai_rasp",
]

for _candidate in _CANDIDATE_PARSER_PATHS:
    if (_candidate / "suai_rasp" / "__init__.py").exists():
        _parser_path = str(_candidate)
        if _parser_path not in sys.path:
            sys.path.insert(0, _parser_path)
        break

from .base import ScheduleSource, ScheduledSession, ScheduleError, ScheduleConfigError
from .registry import register_source, get_source_class, known_sources
from .manual import ManualScheduleSource, WEEKDAYS, _validate_group
from .guap import GuapScheduleSource

# Importing these modules triggers @register_source decorators.
# New sources added to the project should be imported here.
from . import manual  # noqa: F401  (registers 'manual')
from . import guap    # noqa: F401  (registers 'guap')

logger = logging.getLogger(__name__)


def parse_schedule_file(path: str | Path, course_id: str | None = None) -> dict[str, ManualScheduleSource]:
    """Read a schedules/<course_id>.yaml file and return one source per group.

    Args:
        path: absolute or relative path to the YAML file.
        course_id: expected course id. If None, derived from the file name
            (without extension). The file's `course-id:` field, if present,
            must match this — catches renames where the file was renamed
            but not its `course-id` field.

    Returns:
        dict {group_id: ManualScheduleSource}. Each source serves
        list_sessions(group_id, since, until) for its group, applying
        corrections from the same file.

    Raises:
        ScheduleConfigError: file is missing, malformed, or has invalid
            groups/slots/corrections. Same UX as parse_join_config.
    """
    p = Path(path)
    if not p.exists():
        raise ScheduleConfigError(f"Schedule file not found: {path}")
    if course_id is None:
        course_id = p.stem

    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ScheduleConfigError(f"YAML parse error in {path}: {e}")

    if not isinstance(data, dict):
        raise ScheduleConfigError(
            f"Schedule file {path}: top-level must be a mapping"
        )

    # Optional sanity check: course-id field, if present, must match.
    file_course_id = data.get('course-id')
    if file_course_id is not None and str(file_course_id) != str(course_id):
        raise ScheduleConfigError(
            f"Schedule file {path}: `course-id: {file_course_id!r}` does not "
            f"match the course it's attached to ({course_id!r})."
        )

    groups = data.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ScheduleConfigError(
            f"Schedule file {path}: `groups` must be a non-empty mapping "
            "(group_id -> {source, slots, ...})"
        )

    sources: dict[str, ManualScheduleSource] = {}
    for group_id, group_cfg in groups.items():
        where = f"group {group_id!r}"
        _validate_group(str(group_id), group_cfg, where)
        sources[str(group_id)] = ManualScheduleSource(
            course_id=str(course_id),
            group_id=str(group_id),
            group_cfg=group_cfg,
        )
    return sources


def load_schedule_for_group(
    course_id: str,
    group_id: str,
    schedules_dir: str | Path = "schedules",
) -> ManualScheduleSource | None:
    """Convenience: load a group's source by (course_id, group_id).

    Convention: the schedule file for course X is schedules/X.yaml.
    Returns None if the file doesn't exist (the group has no schedule
    on this course). Raises ScheduleConfigError if the file exists but
    is malformed, OR if the file exists but doesn't contain this group.
    """
    path = Path(schedules_dir) / f"{course_id}.yaml"
    if not path.exists():
        return None
    sources = parse_schedule_file(path, course_id=course_id)
    return sources.get(str(group_id))


def load_course_groups(
    course_id: str,
    courses_dir: str | Path = "courses",
) -> list[dict[str, str]]:
    """Read group IDs and GUAP group IDs from a course config."""
    path = Path(courses_dir) / f"{course_id}.yaml"
    if not path.exists():
        raise ScheduleConfigError(f"Course config not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ScheduleConfigError(f"YAML parse error in {path}: {e}")

    if not isinstance(data, dict) or not isinstance(data.get("course"), dict):
        raise ScheduleConfigError(f"Course config {path}: `course` must be a mapping")

    groups_raw = data["course"].get("groups", [])
    if groups_raw is None:
        groups_raw = []
    if not isinstance(groups_raw, list):
        raise ScheduleConfigError(f"Course config {path}: `course.groups` must be a list")

    groups: list[dict[str, str]] = []
    for index, group in enumerate(groups_raw):
        if not isinstance(group, dict):
            raise ScheduleConfigError(
                f"Course config {path}: `course.groups[{index}]` must be a mapping"
            )
        group_id = group.get("id")
        if not group_id:
            raise ScheduleConfigError(
                f"Course config {path}: `course.groups[{index}].id` is required"
            )
        guap_group_id = group.get("guap_group_id") or group_id
        groups.append({"id": str(group_id), "guap_group_id": str(guap_group_id)})
    return groups


def refresh_all_groups(
    course_id: str,
    courses_dir: str | Path = "courses",
    schedules_dir: str | Path = "schedules",
    snapshot_path: str = "data/schedule.json",
) -> dict[str, object]:
    """Refresh each configured group, retaining schedule corrections/settings."""
    from .guap import GuapScheduleSource

    groups = load_course_groups(course_id, courses_dir=courses_dir)
    if not groups:
        raise ScheduleConfigError(
            f"Course {course_id!r} has no groups declared. Add a `groups:` "
            f"list to courses/{course_id}.yaml with at least one group."
        )

    schedule_path = Path(schedules_dir) / f"{course_id}.yaml"
    existing_data: dict = {}
    if schedule_path.exists():
        try:
            with schedule_path.open("r", encoding="utf-8") as f:
                existing_data = yaml.safe_load(f) or {}
            if not isinstance(existing_data, dict):
                existing_data = {}
        except yaml.YAMLError:
            existing_data = {}

    schedule_groups = existing_data.setdefault("groups", {})
    if not isinstance(schedule_groups, dict):
        schedule_groups = {}
        existing_data["groups"] = schedule_groups
    existing_data.setdefault("course-id", course_id)

    refreshed: dict[str, int] = {}
    errors: dict[str, str] = {}
    for group in groups:
        group_id = group["id"]
        guap_group_id = group["guap_group_id"]
        try:
            source = GuapScheduleSource(
                group_code=guap_group_id,
                snapshot_path=snapshot_path,
                course_id=course_id,
            )
            raw_lessons = source.fetch_raw(guap_group_id)
            slots = source.to_yaml_sessions(raw_lessons)

            group_config = schedule_groups.get(group_id) or {}
            if not isinstance(group_config, dict):
                group_config = {}
            group_config["slots"] = slots
            group_config.setdefault("source", "guap")
            group_config["guap_group_id"] = guap_group_id
            group_config.setdefault("timezone", "UTC+3")
            group_config.setdefault("auto_open_minutes_before", 15)
            group_config.setdefault("auto_close_minutes_after", 10)
            schedule_groups[group_id] = group_config
            refreshed[group_id] = len(slots)
        except Exception as e:
            errors[group_id] = f"{type(e).__name__}: {e}"

    new_yaml = yaml.safe_dump(
        existing_data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    import os
    import tempfile

    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    directory = os.path.dirname(os.path.abspath(schedule_path))
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=directory,
        prefix=".schedule-",
        suffix=".yaml.tmp",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(new_yaml)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, schedule_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return {
        "course_id": course_id,
        "filename": schedule_path.name,
        "refreshed": refreshed,
        "errors": errors,
    }


def iter_schedule_files(
    schedules_dir: str | Path = "schedules",
) -> Iterator[tuple[str, Path]]:
    """Yield (course_id, path) for every schedules/*.yaml file.

    Used by warn_about_schedule on startup — same role as
    iter_course_configs but only for the schedules/ directory.
    """
    d = Path(schedules_dir)
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.yaml")):
        yield path.stem, path


def warn_about_schedule(schedules_dir: str | Path = "schedules") -> None:
    """Log schedule config problems at startup.

    Mirror of warn_about_join_links / warn_about_team_labs in main.py:
    a bad schedule file shouldn't crash the whole startup, but the
    teacher must see it in the log before a student triggers the endpoint.
    """
    for course_id, path in iter_schedule_files(schedules_dir):
        try:
            parse_schedule_file(path, course_id=course_id)
        except ScheduleConfigError as e:
            logger.warning(f"{path.name}: {e}")


__all__ = [
    # types
    'ScheduleSource',
    'ScheduledSession',
    'ScheduleError',
    'ScheduleConfigError',
    # registry
    'register_source',
    'get_source_class',
    'known_sources',
    # sources
    'ManualScheduleSource',
    'GuapScheduleSource',
    # factory + helpers
    'parse_schedule_file',
    'load_schedule_for_group',
    'load_course_groups',
    'refresh_all_groups',
    'iter_schedule_files',
    'warn_about_schedule',
    # constants re-exported for tests
    'WEEKDAYS',
]
