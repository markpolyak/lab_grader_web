"""
Reading the course index and the course configs it points at.

One place reads `courses/index.yaml` and the `courses/*.yaml` files it names,
so that moving the configs into another store (a database, a private git
repository) is a change in one module rather than in every caller - see
docs/SECRET_JOIN_LINKS_PLAN.md §13.

`main.iter_course_configs()` is the server's entry point into this module and
the single enumeration point of the project; `scripts/join-link.py` uses the
functions here directly, because importing `main` would require the server's
whole environment just to print a link.
"""
import logging
import os

import yaml

logger = logging.getLogger(__name__)

INDEX_FILENAME = "index.yaml"


def index_path(courses_dir: str) -> str:
    return os.path.join(courses_dir, INDEX_FILENAME)


def load_course_index(index_file: str) -> dict:
    """
    Load and validate the course index file.

    Raises:
        RuntimeError: the file is missing or has no `courses` key
    """
    if not os.path.exists(index_file):
        raise RuntimeError(f"Course index file not found: {index_file}")

    with open(index_file, "r", encoding="utf-8") as f:
        index_data = yaml.safe_load(f)

    if not isinstance(index_data, dict) or "courses" not in index_data:
        raise RuntimeError("Invalid index.yaml structure: missing 'courses' key")

    return index_data


def course_meta(entry: dict) -> dict:
    """Index metadata attached to a course config as `_meta`."""
    return {
        "status": entry.get("status", "active"),
        "priority": entry.get("priority", 0),
        "featured": entry.get("featured", False),
        "filename": entry["file"],
        "logo": entry.get("logo", "/assets/default.png"),
    }


def read_course_file(courses_dir: str, entry: dict) -> dict | None:
    """
    Read one course file named by an index entry.

    Returns:
        The `course` mapping with `_meta` filled in, or None if the file is
        missing, unparseable or not a course config (logged, never fatal -
        one broken file must not take the whole list down).
    """
    file_path = os.path.join(courses_dir, entry["file"])
    if not os.path.exists(file_path):
        logger.warning(f"Course file {entry['file']} not found, skipping")
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML in {entry['file']}: {e}")
        return None

    if not isinstance(data, dict) or "course" not in data:
        logger.warning(f"Skipping file {entry['file']}: invalid structure")
        return None

    course_info = data["course"]
    course_info["_meta"] = course_meta(entry)
    return course_info


def iter_course_configs(courses_dir: str, index_file: str | None = None):
    """
    Yield (course_id, course_info) for every course in the index.

    Args:
        courses_dir: directory holding the course YAML files
        index_file: path to index.yaml (defaults to one inside `courses_dir`)
    """
    index_file = index_file or index_path(courses_dir)
    for entry in load_course_index(index_file).get("courses", []):
        course_id = entry.get("id")
        if not course_id or not entry.get("file"):
            continue
        course_info = read_course_file(courses_dir, entry)
        if course_info is None:
            continue
        yield course_id, course_info
