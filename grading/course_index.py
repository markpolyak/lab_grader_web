"""
Reading the course index and the course configs it points at.

All reading goes through a CourseStore (grading/course_store.py): the
`courses/` directory locally, a private GitHub repository on the server
(docs/COURSE_STORAGE_PLAN.md). Nothing here opens files itself.

`main.iter_course_configs()` is the server's entry point into this module and
the single enumeration point of the project; `scripts/join-link.py` uses the
functions here directly, because importing `main` would require the server's
whole environment just to print a link.
"""
import logging

import yaml

from grading.course_store import INDEX_PATH, StoredFile

logger = logging.getLogger(__name__)

INDEX_FILENAME = INDEX_PATH


def parse_course_index(stored: StoredFile | None) -> dict:
    """
    Parse index.yaml as read from the store.

    Raises:
        RuntimeError: the file is missing, unparseable or has no `courses` list
    """
    if stored is None:
        raise RuntimeError(f"Course index file not found: {INDEX_FILENAME}")
    try:
        index_data = yaml.safe_load(stored.content)
    except yaml.YAMLError as e:
        raise RuntimeError(f"Invalid index.yaml: {e}")

    if not isinstance(index_data, dict) or not isinstance(index_data.get("courses"), list):
        raise RuntimeError("Invalid index.yaml structure: missing 'courses' key")

    return index_data


def load_course_index(store) -> dict:
    """Load and validate the course index (see parse_course_index)."""
    return parse_course_index(store.read(INDEX_FILENAME))


def course_meta(entry: dict) -> dict:
    """Index metadata attached to a course config as `_meta`."""
    return {
        "status": entry.get("status", "active"),
        "priority": entry.get("priority", 0),
        "featured": entry.get("featured", False),
        "filename": entry["file"],
        "logo": entry.get("logo", "/assets/default.png"),
    }


def read_course_file(store, entry: dict) -> dict | None:
    """
    Read one course file named by an index entry.

    Returns:
        The `course` mapping with `_meta` filled in, or None if the file is
        missing, unparseable or not a course config (logged, never fatal -
        one broken file must not take the whole list down).
    """
    stored = store.read(entry["file"])
    if stored is None:
        logger.warning(f"Course file {entry['file']} not found, skipping")
        return None

    try:
        data = yaml.safe_load(stored.content)
    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML in {entry['file']}: {e}")
        return None

    if not isinstance(data, dict) or not isinstance(data.get("course"), dict):
        logger.warning(f"Skipping file {entry['file']}: invalid structure")
        return None

    course_info = data["course"]
    course_info["_meta"] = course_meta(entry)
    return course_info


def iter_course_configs(store):
    """Yield (course_id, course_info) for every course in the index."""
    for entry in load_course_index(store).get("courses", []):
        if not isinstance(entry, dict):
            continue
        course_id = entry.get("id")
        if not course_id or not entry.get("file"):
            continue
        course_info = read_course_file(store, entry)
        if course_info is None:
            continue
        yield course_id, course_info


def course_files(store) -> set[str]:
    """Course YAML files at the top level of the store (index.yaml excluded)."""
    return {
        path for path in store.list()
        if "/" not in path and path.endswith((".yaml", ".yml")) and path != INDEX_FILENAME
    }


def index_errors(index_data: dict, existing_files: set[str]) -> list[str]:
    """
    Problems that make an index unusable: duplicate ids, entries without id or
    file, files the index points at that do not exist. Checked at startup and
    before writing a changed index (§6 of the plan).
    """
    errors = []
    ids = []
    for entry in index_data.get("courses", []):
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("file"):
            errors.append(f"Index entry without id or file: {entry!r}")
            continue
        ids.append(entry["id"])
        if entry["file"] not in existing_files:
            errors.append(f"File referenced in index but not found: {entry['file']}")

    duplicates = sorted({x for x in ids if ids.count(x) > 1})
    if duplicates:
        errors.append(f"Duplicate course IDs in index: {duplicates}")
    return errors
