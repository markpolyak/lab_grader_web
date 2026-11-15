"""Resolve a student's team without persisting GitHub identities in Sheets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grading.teams import TeamInfo, TeamRegistry, is_team_lab

from .errors import QueueError


@dataclass(frozen=True)
class QueueTeam:
    title: str
    repo_name: str
    members: tuple[str, ...]


def resolve_team_for_user(
    github_client,
    course_info: dict[str, Any],
    lab_config: dict[str, Any],
    username: str,
) -> QueueTeam | None:
    """Return the member's team, or `None` for an individual lab."""
    if not is_team_lab(lab_config):
        return None

    organization = course_info.get("github", {}).get("organization")
    github_prefix = lab_config.get("github-prefix")
    if not organization or not github_prefix:
        raise QueueError("TEAMS_UNAVAILABLE", "Не настроена GitHub-организация или префикс лабы")

    teachers = course_info.get("github", {}).get("teachers") or []
    registry = TeamRegistry(github_client)
    teams = registry.list_teams(organization, github_prefix, teachers)
    if teams is None:
        raise QueueError("TEAMS_UNAVAILABLE", "Не удалось получить список команд")

    team = registry.find_member_team(teams, username)
    if team is None:
        if any(candidate.members_unknown for candidate in teams):
            raise QueueError("TEAMS_UNAVAILABLE", "Не удалось проверить состав команд")
        raise QueueError("NO_TEAM", "Студент не состоит в команде этой лабораторной")
    if not team.title.strip():
        raise QueueError("TEAMS_UNAVAILABLE", "У команды не задано название")

    return QueueTeam(
        title=team.title,
        repo_name=team.repo_name,
        members=tuple((*team.members, *team.pending)),
    )
