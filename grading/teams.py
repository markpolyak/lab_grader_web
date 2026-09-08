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
import re
import threading
import time
from dataclasses import dataclass, field

from .github_client import GitHubClient
from .repo_provisioning import RepoProvisioner

logger = logging.getLogger(__name__)

# Slug of a team, as it appears in URLs and after the lab's github-prefix.
# Never built from student input: the number comes from the repositories that
# already exist (see next_team_number).
TEAM_SLUG_RE = re.compile(r"^team-\d+$")

# How long a collected team list stays usable for read-only operations. A
# group of 30 students opening the picker page at once then costs one set of
# GitHub requests instead of thirty.
TEAMS_CACHE_TTL_SECONDS = 30

# Separates the title from the description inside the repository description.
# Space, em dash (U+2014), space.
DESCRIPTION_SEPARATOR = " — "


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


# ---------------------------------------------------------------------------
# Team title and description
# ---------------------------------------------------------------------------

def parse_description(raw: str | None) -> tuple[str, str]:
    """
    Split a repository description back into (title, description).

    A description edited by hand on GitHub may have no separator at all - the
    whole string is then shown as the title, degrading without an error.
    """
    text = (raw or "").strip()
    if not text:
        return "", ""
    title, separator, description = text.partition(DESCRIPTION_SEPARATOR)
    if not separator:
        return text, ""
    return title.strip(), description.strip()


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------



@dataclass
class TeamInfo:
    """One team of a lab, as read from its repository."""
    slug: str                 # "team-3"
    number: int               # 3
    repo_name: str            # "os-task5-team-3"
    repo_url: str
    title: str = ""
    description: str = ""
    members: list[str] = field(default_factory=list)   # accepted collaborators
    pending: list[str] = field(default_factory=list)   # invited, not accepted
    members_unknown: bool = False  # roster could not be read (see §7.1)

    @property
    def size(self) -> int:
        """Members occupying a place: a pending invitation holds one too."""
        return len(self.members) + len(self.pending)

    def has_member(self, username: str) -> bool:
        target = (username or "").casefold()
        return any(
            login.casefold() == target for login in (*self.members, *self.pending)
        )


# Module-level, exactly like the job stores in propagate.py / bulk.py: a
# TeamRegistry is built per request, so shared state cannot live on the
# instance. Correct only while the backend runs a single uvicorn worker -
# a constraint docs/PROJECT_DESCRIPTION.md already states.
_teams_cache: dict[tuple[str, str], tuple[float, list[TeamInfo]]] = {}
_cache_lock = threading.Lock()

_lab_locks: dict[tuple[str, str], threading.Lock] = {}
_lab_locks_mutex = threading.Lock()


def lab_lock(course_id: str, lab_id: str) -> threading.Lock:
    """The mutation lock of one lab, created on first use."""
    key = (course_id, lab_id)
    with _lab_locks_mutex:
        lock = _lab_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _lab_locks[key] = lock
        return lock


def reset_teams_state() -> None:
    """Drop every cached team list and lock (used by tests)."""
    with _cache_lock:
        _teams_cache.clear()
    with _lab_locks_mutex:
        _lab_locks.clear()


class TeamRegistry:
    """
    Reads and mutates the teams of one lab, on top of their repositories.

    Mirrors RepoProvisioner: takes a GitHubClient built with the server's
    GITHUB_TOKEN, never the student's OAuth token.
    """

    def __init__(self, github_client: GitHubClient, provisioner: RepoProvisioner | None = None):
        self.github = github_client
        self.provisioner = provisioner or RepoProvisioner(github_client)

    # -- reading ----------------------------------------------------------

    def cached_teams(self, org: str, github_prefix: str) -> list[TeamInfo] | None:
        """Teams collected less than TEAMS_CACHE_TTL_SECONDS ago, if any."""
        with _cache_lock:
            entry = _teams_cache.get((org, github_prefix))
        if entry is None:
            return None
        collected_at, teams = entry
        if time.time() - collected_at >= TEAMS_CACHE_TTL_SECONDS:
            return None
        return teams

    def invalidate(self, org: str, github_prefix: str) -> None:
        """Forget the cached team list after a successful mutation."""
        with _cache_lock:
            _teams_cache.pop((org, github_prefix), None)

    def list_teams(
        self,
        org: str,
        github_prefix: str,
        teachers: tuple[str, ...] | list[str] = (),
        fresh: bool = False,
    ) -> list[TeamInfo] | None:
        """
        Collect the teams of one lab from the organization's repositories.

        Costs 1 (paginated) request for the organization plus 2 per team.

        Args:
            org: GitHub organization owning student repositories
            github_prefix: Lab's github-prefix
            teachers: `course.github.teachers` - a mixed list of names and
                logins, used only to keep a teacher out of a team roster
            fresh: Bypass the cache (mandatory inside a mutation, §7.2)

        Returns:
            Teams ordered by number, or None if the organization's repository
            list is unavailable
        """
        if not fresh:
            cached = self.cached_teams(org, github_prefix)
            if cached is not None:
                return cached

        repos = self.github.list_org_repos(org)
        if repos is None:
            logger.error(f"Could not list repositories of {org} to collect teams")
            return None

        pattern = re.compile(rf"^{re.escape(github_prefix)}-(team-(\d+))$")
        excluded = {str(name).casefold() for name in (teachers or ())}

        teams: list[TeamInfo] = []
        for repo in repos:
            name = repo.get("name", "")
            match = pattern.match(name)
            if not match:
                continue

            title, description = parse_description(repo.get("description"))
            members, pending, unknown = self._read_roster(org, name, excluded)
            teams.append(TeamInfo(
                slug=match.group(1),
                number=int(match.group(2)),
                repo_name=name,
                repo_url=f"https://github.com/{org}/{name}",
                title=title,
                description=description,
                members=members,
                pending=pending,
                members_unknown=unknown,
            ))

        teams.sort(key=lambda team: (team.number, team.slug))

        with _cache_lock:
            _teams_cache[(org, github_prefix)] = (time.time(), teams)
        return teams

    def _read_roster(
        self,
        org: str,
        repo_name: str,
        excluded: set[str],
    ) -> tuple[list[str], list[str], bool]:
        """
        Read one team's roster.

        A member is a direct collaborator with push but not admin permission:
        organization owners show up in the collaborator list through their
        organization role, and that filter is what keeps them out. Logins from
        `course.github.teachers` are excluded on top of it - that list mixes
        names and logins, so it is a helper, not the main criterion.

        Returns:
            (members, pending, members_unknown). A GitHub failure for one team
            yields empty lists and members_unknown=True, so that a single
            unreadable team does not break the whole page.
        """
        collaborators = self.github.list_collaborators(org, repo_name, affiliation="direct")
        invitations = self.github.list_invitations(org, repo_name)
        if collaborators is None or invitations is None:
            logger.warning(f"Could not read the roster of {org}/{repo_name}")
            return [], [], True

        members: list[str] = []
        for collaborator in collaborators:
            login = collaborator.get("login") or ""
            if not login or login.casefold() in excluded:
                continue
            permissions = collaborator.get("permissions") or {}
            if not permissions.get("push") or permissions.get("admin"):
                continue
            members.append(login)

        pending: list[str] = []
        for invitation in invitations:
            login = (invitation.get("invitee") or {}).get("login") or ""
            if not login or login.casefold() in excluded:
                continue
            pending.append(login)

        return members, pending, False

    @staticmethod
    def find_member_team(teams: list[TeamInfo], username: str) -> TeamInfo | None:
        """The team `username` belongs to, or None."""
        for team in teams:
            if team.has_member(username):
                return team
        return None

    @staticmethod
    def member_index(teams: list[TeamInfo]) -> dict[str, TeamInfo]:
        """
        Map every member's casefolded login to their team.

        Pending invitees are included: the repository is the team's, and the
        grade belongs in the row of everyone assigned to it.
        """
        index: dict[str, TeamInfo] = {}
        for team in teams:
            for login in (*team.members, *team.pending):
                index.setdefault(login.casefold(), team)
        return index

    @staticmethod
    def next_team_number(teams: list[TeamInfo]) -> int:
        """
        The smallest free positive number.

        Deleting a team frees its number for the next one, which is why no
        separate counter is stored.
        """
        used = {team.number for team in teams}
        number = 1
        while number in used:
            number += 1
        return number
