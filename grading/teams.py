"""
Team (group) lab assignments: one repository per team, shared by its members.

Continuation of the /join student repo creation flow (see
docs/REPO_GENERATION_PLAN.md); the full design lives in
docs/TEAM_ASSIGNMENTS_PLAN.md.

The source of truth about a team is the team's repository itself: its name
carries the team's slug (`{github-prefix}-team-{N}`), its `description` field
carries the human-readable title and description, and its direct
collaborators plus pending invitations are the roster. No new storage is
introduced - the project has no database, and everything a student sees or a
teacher edits is one GitHub entity.

Like grading/repo_provisioning.py, this module gets a GitHubClient configured
with the server's GITHUB_TOKEN, knows nothing about FastAPI and never touches
Google Sheets.
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lab configuration
# ---------------------------------------------------------------------------


class TeamConfigError(Exception):
    """A lab's `team` section is present but malformed."""


@dataclass(frozen=True)
class TeamConfig:
    """Parsed `team` section of a lab config."""
    size_max: int | None = None   # Max members per team, None = unlimited
    count_max: int | None = None  # Max teams per lab, None = unlimited


def is_team_lab(lab_config: dict | None) -> bool:
    """
    Whether a lab is a team lab.

    The mere presence of the `team` key switches the lab over, even when the
    section is empty (`team: {}`) - limits are optional.
    """
    return isinstance(lab_config, dict) and "team" in lab_config


def _positive_int(raw: dict, key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    # bool is an int subclass, and `size-max: yes` in YAML is a bool.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TeamConfigError(
            f"Некорректное значение team.{key}: {value!r} (ожидается целое число не меньше 1)"
        )
    return value


def parse_team_config(lab_config: dict) -> TeamConfig | None:
    """
    Parse and validate the `team` section of a lab config.

    Returns:
        TeamConfig for a team lab, or None for an individual one

    Raises:
        TeamConfigError: the section is not a mapping, or a limit is not a
        positive integer
    """
    if not is_team_lab(lab_config):
        return None

    raw = lab_config.get("team")
    if raw is None:
        # `team:` with nothing under it - a team lab without limits.
        raw = {}
    if not isinstance(raw, dict):
        raise TeamConfigError("Секция team лабораторной работы должна быть словарём")

    return TeamConfig(
        size_max=_positive_int(raw, "size-max"),
        count_max=_positive_int(raw, "count-max"),
    )
