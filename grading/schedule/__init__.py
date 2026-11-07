"""Schedule sources and parsing for per-course YAML files in `schedules/`.

The schedule filename is derived from the course ID. The schedule YAML carries
mandatory course and group IDs alongside defaults, slots, corrections, and
queue settings.
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
            (without extension). The required `course-id` field must match.

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

    # The identifier ties the schedule file to the course that references it.
    file_course_id = data.get('course-id')
    if file_course_id is None or not str(file_course_id).strip():
        raise ScheduleConfigError(f"Schedule file {path}: `course-id` is required")
    if str(file_course_id) != str(course_id):
        raise ScheduleConfigError(
            f"Schedule file {path}: `course-id: {file_course_id!r}` does not "
            f"match the course it's attached to ({course_id!r})."
        )

    groups = data.get('groups')
    if not isinstance(groups, dict):
        raise ScheduleConfigError(
            f"Schedule file {path}: `groups` must be a mapping "
            "(group_id -> {source, slots, ...})"
        )

    defaults = {
        key: data[key]
        for key in (
            "source",
            "timezone",
            "auto_open_minutes_before",
            "auto_close_minutes_after",
        )
        if key in data
    }
    sources: dict[str, ManualScheduleSource] = {}
    for group_id, group_cfg in groups.items():
        where = f"group {group_id!r}"
        if not isinstance(group_cfg, dict):
            raise ScheduleConfigError(f"{where}: group must be a mapping")
        configured_id = group_cfg.get("group_id")
        if configured_id is None or not str(configured_id).strip():
            raise ScheduleConfigError(f"{where}: `group_id` is required")
        if str(configured_id) != str(group_id):
            raise ScheduleConfigError(
                f"{where}: `group_id: {configured_id!r}` must match its groups key"
            )
        if not str(group_id).isdigit():
            raise ScheduleConfigError(f"{where}: group IDs must contain only digits")
        merged_config = {**defaults, **group_cfg}
        _validate_group(str(group_id), merged_config, where)
        sources[str(group_id)] = ManualScheduleSource(
            course_id=str(course_id),
            group_id=str(group_id),
            group_cfg=merged_config,
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


def parse_course_schedule(
    course_id: str,
    schedules_dir: str | Path = "schedules",
) -> dict[str, ManualScheduleSource]:
    """Load `schedules/<course_id>.yaml`."""
    schedule_path = schedule_path_for_course(course_id, schedules_dir)
    if not schedule_path.exists():
        raise ScheduleConfigError(f"Schedule file not found: {schedule_path}")
    return parse_schedule_file(schedule_path, course_id=course_id)


def schedule_path_for_course(
    course_id: str,
    schedules_dir: str | Path = "schedules",
) -> Path:
    """Resolve the schedule filename from the stable course ID."""
    if not course_id or Path(course_id).name != course_id or course_id in {".", ".."}:
        raise ScheduleConfigError(f"Invalid course ID for schedule path: {course_id!r}")
    return Path(schedules_dir) / f"{course_id}.yaml"


def load_schedule_data(
    course_id: str,
    schedules_dir: str | Path = "schedules",
) -> tuple[Path, dict | None]:
    """Load the schedule YAML if it exists and verify its required course ID."""
    path = schedule_path_for_course(course_id, schedules_dir)
    if not path.exists():
        return path, None
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except OSError as e:
        raise ScheduleConfigError(f"Could not read schedule file {path}: {e}")
    except yaml.YAMLError as e:
        raise ScheduleConfigError(f"YAML parse error in {path}: {e}")
    if not isinstance(data, dict):
        raise ScheduleConfigError(f"Schedule file {path}: top-level must be a mapping")
    if str(data.get("course-id", "")).strip() != str(course_id):
        raise ScheduleConfigError(
            f"Schedule file {path}: required `course-id` must match {course_id!r}"
        )
    return path, data


def manual_course_groups(schedule_data: dict) -> list[dict[str, str]]:
    """Read manually added group IDs from the schedule file."""
    groups = schedule_data.get("manual-groups", [])
    if not isinstance(groups, list):
        raise ScheduleConfigError("`manual-groups` must be a list in the schedule file")

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(groups):
        if isinstance(entry, dict):
            group_id = entry.get("id")
            guap_group_id = entry.get("guap_group_id") or group_id
        else:
            group_id = entry
            guap_group_id = entry
        if group_id is None or not str(group_id).strip():
            raise ScheduleConfigError(
                f"`manual-groups[{index}]` requires a non-empty id"
            )
        group_id = str(group_id).strip()
        if group_id in seen:
            raise ScheduleConfigError(
                f"`manual-groups` contains duplicate group {group_id!r}"
            )
        seen.add(group_id)
        result.append({"id": group_id, "guap_group_id": str(guap_group_id).strip()})
    return result


def refresh_all_groups(
    course_id: str,
    schedule_file_path: str | Path,
    groups: list[dict[str, str]],
    snapshot_path: str = "data/schedule.json",
) -> dict[str, object]:
    """Refresh numeric groups into `schedules/<course_id>.yaml`, creating it if needed."""
    from .guap import GuapScheduleSource

    import os
    import tempfile

    config_path = Path(schedule_file_path)
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            raise ScheduleConfigError(f"Failed to read schedule file {config_path}: {e}")
        if not isinstance(config_data, dict):
            raise ScheduleConfigError(f"Schedule file {config_path}: top-level must be a mapping")
        if str(config_data.get("course-id", "")).strip() != str(course_id):
            raise ScheduleConfigError(
                f"Schedule file {config_path}: required `course-id` must match {course_id!r}"
            )
    else:
        config_data = {"course-id": course_id}

    config_data.setdefault("source", "guap")
    config_data.setdefault("timezone", "UTC+3")
    config_data.setdefault("auto_open_minutes_before", 15)
    config_data.setdefault("auto_close_minutes_after", 10)
    schedule_groups = config_data.setdefault("groups", {})
    if not isinstance(schedule_groups, dict):
        raise ScheduleConfigError("`groups` must be a mapping in the schedule file")
    manual_groups = config_data.setdefault("manual-groups", [])
    if not isinstance(manual_groups, list):
        raise ScheduleConfigError("`manual-groups` must be a list in the schedule file")

    refreshed: dict[str, int] = {}
    errors: dict[str, str] = {}
    for group in groups:
        group_id = group["id"]
        guap_group_id = group.get("guap_group_id") or group_id
        if not str(group_id).isdigit():
            errors[group_id] = "group_id must contain only digits"
            continue
        group_config = schedule_groups.get(group_id)
        if group_config is None:
            group_config = {"group_id": group_id, "slots": []}
            schedule_groups[group_id] = group_config
        if not isinstance(group_config, dict):
            errors[group_id] = f"group {group_id!r} must be a mapping"
            continue
        configured_group_id = group_config.get("group_id")
        if configured_group_id is None or str(configured_group_id).strip() != group_id:
            errors[group_id] = f"group {group_id!r} requires matching `group_id`"
            continue
        try:
            source = GuapScheduleSource(
                group_code=guap_group_id,
                snapshot_path=snapshot_path,
                course_id=course_id,
            )
            raw_lessons = source.fetch_raw(guap_group_id)
            slots = source.to_yaml_sessions(raw_lessons)

            group_config["slots"] = slots
            group_config["guap_group_id"] = guap_group_id
            schedule_groups[group_id] = group_config
            refreshed[group_id] = len(slots)
        except Exception as e:
            errors[group_id] = f"{type(e).__name__}: {e}"

    new_yaml = yaml.safe_dump(
        config_data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    directory = os.path.dirname(os.path.abspath(config_path))
    os.makedirs(directory, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=directory,
        prefix=".course-",
        suffix=".yaml.tmp",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(new_yaml)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, config_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return {
        "course_id": course_id,
        "filename": config_path.name,
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
    'parse_course_schedule',
    'schedule_path_for_course',
    'load_schedule_data',
    'manual_course_groups',
    'refresh_all_groups',
    'iter_schedule_files',
    'warn_about_schedule',
    # constants re-exported for tests
    'WEEKDAYS',
]
