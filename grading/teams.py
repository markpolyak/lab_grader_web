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
from enum import Enum

from .github_client import GitHubClient
from .repo_provisioning import RepoProvisioner, ProvisionStatus

logger = logging.getLogger(__name__)

# Slug of a team, as it appears in URLs and after the lab's github-prefix.
# Never built from student input: the number comes from the repositories that
# already exist (see next_team_number).
TEAM_SLUG_RE = re.compile(r"^team-\d+$")

# How long a collected team list stays usable for read-only operations. A
# group of 30 students opening the picker page at once then costs one set of
# GitHub requests instead of thirty.
TEAMS_CACHE_TTL_SECONDS = 30

# Title/description limits (docs/TEAM_ASSIGNMENTS_PLAN.md §3.4). The composed
# string stays well below GitHub's 350-character limit on `description`.
TITLE_MIN_LENGTH = 3
TITLE_MAX_LENGTH = 60
DESCRIPTION_MAX_LENGTH = 200

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

class TeamTitleError(Exception):
    """Team title or description failed validation (§3.4 of the plan)."""


def _clean_text(value: str | None) -> str:
    """Strip, drop control characters and collapse whitespace runs."""
    if not value:
        return ""
    # Whitespace (a newline included) survives as a separator and is collapsed
    # below; every other non-printable character is dropped outright.
    without_controls = "".join(ch for ch in value if ch.isspace() or ch.isprintable())
    return " ".join(without_controls.split())


def clean_team_title(title: str | None) -> str:
    """
    Validate and normalize a team title.

    Raises:
        TeamTitleError: empty, too short, too long, or containing the
        title/description separator (which would break parsing back apart)
    """
    cleaned = _clean_text(title)
    if len(cleaned) < TITLE_MIN_LENGTH:
        raise TeamTitleError(
            f"Название команды должно содержать не меньше {TITLE_MIN_LENGTH} символов"
        )
    if len(cleaned) > TITLE_MAX_LENGTH:
        raise TeamTitleError(
            f"Название команды не должно быть длиннее {TITLE_MAX_LENGTH} символов"
        )
    if DESCRIPTION_SEPARATOR in cleaned:
        raise TeamTitleError("Название команды не должно содержать « — »")
    return cleaned


def clean_team_description(description: str | None) -> str:
    """
    Validate and normalize an optional team description.

    Raises:
        TeamTitleError: longer than DESCRIPTION_MAX_LENGTH after cleaning
    """
    cleaned = _clean_text(description)
    if len(cleaned) > DESCRIPTION_MAX_LENGTH:
        raise TeamTitleError(
            f"Описание команды не должно быть длиннее {DESCRIPTION_MAX_LENGTH} символов"
        )
    return cleaned


def compose_description(title: str, description: str) -> str:
    """Build the repository `description` field out of title and description."""
    if description:
        return f"{title}{DESCRIPTION_SEPARATOR}{description}"
    return title


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


class TeamActionStatus(Enum):
    OK = "ok"
    ERROR = "error"


@dataclass
class TeamActionResult:
    """Outcome of creating or joining a team."""
    status: TeamActionStatus
    team: TeamInfo | None = None
    repo_url: str | None = None
    message: str = ""
    error_code: str | None = None


def _error(code: str, message: str, team: TeamInfo | None = None) -> TeamActionResult:
    return TeamActionResult(
        status=TeamActionStatus.ERROR,
        error_code=code,
        message=message,
        team=team,
        repo_url=team.repo_url if team else None,
    )


# Module-level, exactly like the job stores in propagate.py / bulk.py: a
# TeamRegistry is built per request, so shared state cannot live on the
# instance. Correct only while the backend runs a single uvicorn worker -
# a constraint docs/PROJECT_DESCRIPTION.md already states.
_teams_cache: dict[tuple[str, str], tuple[float, list[TeamInfo]]] = {}
_cache_lock = threading.Lock()

_lab_locks: dict[tuple[str, str], threading.Lock] = {}
_lab_locks_mutex = threading.Lock()


def lab_lock(course_id: str, lab_key: str) -> threading.Lock:
    """
    The mutation lock of one lab, created on first use.

    `lab_key` must be the lab's canonical key in the course YAML, not the raw
    lab_id from the URL: find_lab_config resolves "5", "05", "ЛР5" and "lab5"
    to the same lab, so keying the lock by the raw path segment hands two
    students two different locks for one lab and lets them pass count-max,
    size-max, TITLE_TAKEN and ALREADY_IN_TEAM concurrently. Keying it
    canonically also bounds the size of _lab_locks, which the raw value -
    an unbounded set of spellings - does not.
    """
    key = (course_id, lab_key)
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

    # -- mutations --------------------------------------------------------

    def create_team(
        self,
        course_id: str,
        lab_key: str,
        org: str,
        github_prefix: str,
        template_repo: str,
        username: str,
        title: str | None,
        description: str | None = None,
        mode: str = "template",
        teachers: tuple[str, ...] | list[str] = (),
        team_config: TeamConfig | None = None,
    ) -> TeamActionResult:
        """
        Create a team repository and make its creator a collaborator (§7.3).

        The whole sequence runs under the lab's lock and re-reads the team
        list with fresh=True inside it, so two students cannot take the same
        number or the same title. `lab_key` must be the lab's canonical
        config key - see lab_lock.
        """
        config = team_config or TeamConfig()

        try:
            clean_title = clean_team_title(title)
            clean_description = clean_team_description(description)
        except TeamTitleError as e:
            return _error("INVALID_TITLE", str(e))

        with lab_lock(course_id, lab_key):
            teams = self.list_teams(org, github_prefix, teachers, fresh=True)
            if teams is None:
                return _error("TEAMS_UNAVAILABLE", "Не удалось получить список команд")

            if any(team.members_unknown for team in teams):
                # Without every roster there is no way to tell whether this
                # student is already in a team, and guessing "no" hands them a
                # second team with a second repository that grading then has
                # to pick between.
                return _error(
                    "TEAMS_UNAVAILABLE",
                    "Не удалось прочитать состав команд. Попробуйте ещё раз позже",
                )

            existing = self.find_member_team(teams, username)
            if existing is not None:
                return _error(
                    "ALREADY_IN_TEAM",
                    "Вы уже состоите в команде этой лабораторной работы",
                    team=existing,
                )

            if config.count_max is not None and len(teams) >= config.count_max:
                return _error(
                    "TEAM_LIMIT_REACHED",
                    "Достигнуто максимальное число команд для этой лабораторной работы",
                )

            if any(team.title.casefold() == clean_title.casefold() for team in teams):
                return _error("TITLE_TAKEN", "Команда с таким названием уже существует")

            slug = f"team-{self.next_team_number(teams)}"
            repo_name = f"{github_prefix}-{slug}"
            if self.github.repo_exists(org, repo_name):
                # The organization listing lagged behind reality, or the name
                # belongs to an unrelated repository. Either way, retrying
                # picks the next free number.
                logger.warning(f"{org}/{repo_name} already exists while creating a team")
                return _error("SLUG_RACE", "Не удалось занять имя репозитория, повторите попытку")

            logger.info(
                f"Student {username} creates team {slug} ({clean_title!r}) in {course_id}/{lab_key}"
            )
            provision = self.provisioner.provision(
                org, github_prefix, template_repo, slug,
                mode=mode, access_username=username,
            )
            if provision.status != ProvisionStatus.OK:
                self.invalidate(org, github_prefix)
                return _error(
                    provision.error_code or "PROVISION_FAILED",
                    provision.message or "Не удалось создать репозиторий команды",
                )

            # `generate` does not set a description, and a fork inherits the
            # template's - both need replacing with the team's name. A failure
            # here is logged but does not undo a working repository.
            composed = compose_description(clean_title, clean_description)
            resp = self.github.update_repo(org, repo_name, {"description": composed})
            if resp.status_code != 200:
                logger.error(
                    f"Could not set the description of {org}/{repo_name}: "
                    f"{resp.status_code} {resp.text[:500]}"
                )

            team = TeamInfo(
                slug=slug,
                number=int(slug.removeprefix("team-")),
                repo_name=repo_name,
                repo_url=provision.repo_url or f"https://github.com/{org}/{repo_name}",
                title=clean_title,
                description=clean_description,
                members=[],
                # The invitation has just been issued, so the creator is
                # pending in the common case. The cache is dropped right
                # below, so the next read reports the real roster anyway.
                pending=[username],
            )
            self.invalidate(org, github_prefix)
            return TeamActionResult(
                status=TeamActionStatus.OK,
                team=team,
                repo_url=team.repo_url,
                message="Команда создана",
            )

    def join_team(
        self,
        course_id: str,
        lab_key: str,
        org: str,
        github_prefix: str,
        template_repo: str,
        username: str,
        slug: str,
        mode: str = "template",
        teachers: tuple[str, ...] | list[str] = (),
        team_config: TeamConfig | None = None,
    ) -> TeamActionResult:
        """
        Add a student to an existing team, or repair their access to the team
        they are already in (§7.4).

        The repository name is always assembled by the server from the lab's
        prefix and a slug matching TEAM_SLUG_RE - a repository name is never
        accepted from the request. `lab_key` must be the lab's canonical
        config key - see lab_lock.
        """
        config = team_config or TeamConfig()

        if not TEAM_SLUG_RE.match(slug or ""):
            return _error("TEAM_NOT_FOUND", "Команда не найдена")

        with lab_lock(course_id, lab_key):
            teams = self.list_teams(org, github_prefix, teachers, fresh=True)
            if teams is None:
                return _error("TEAMS_UNAVAILABLE", "Не удалось получить список команд")

            team = next((candidate for candidate in teams if candidate.slug == slug), None)
            if team is None:
                return _error("TEAM_NOT_FOUND", "Команда не найдена")

            current = self.find_member_team(teams, username)
            if current is not None and current.slug != team.slug:
                return _error(
                    "ALREADY_IN_TEAM",
                    "Вы уже состоите в другой команде этой лабораторной работы",
                    team=current,
                )

            already_in_this_team = current is not None
            if not already_in_this_team:
                # `current is None` only means "not found in the rosters we
                # could read". Any unreadable roster may be the student's own,
                # and treating that as "in no team" lets them into a second
                # one; an unreadable target roster additionally hides how many
                # seats are taken.
                if any(candidate.members_unknown for candidate in teams):
                    return _error(
                        "TEAMS_UNAVAILABLE",
                        "Не удалось прочитать состав команд. Попробуйте ещё раз позже",
                        team=team,
                    )
                if config.size_max is not None and team.size >= config.size_max:
                    return _error("TEAM_FULL", "В команде нет свободных мест", team=team)

            logger.info(
                f"Student {username} joins team {slug} in {course_id}/{lab_key} "
                f"(access repair: {already_in_this_team})"
            )
            # The repository already exists, so this is effectively
            # _ensure_access (plus _repair_fork in fork mode).
            provision = self.provisioner.provision(
                org, github_prefix, template_repo, slug,
                mode=mode, access_username=username,
                # The roster read above is the definition of membership here,
                # so a student missing from it needs a direct push invitation
                # even when GitHub says they can already reach the repository
                # - see RepoProvisioner._ensure_access.
                force_invite=not already_in_this_team,
            )
            if provision.status != ProvisionStatus.OK:
                return _error(
                    provision.error_code or "PROVISION_FAILED",
                    provision.message or "Не удалось предоставить доступ к репозиторию команды",
                    team=team,
                )

            self.invalidate(org, github_prefix)
            return TeamActionResult(
                status=TeamActionStatus.OK,
                team=team,
                repo_url=team.repo_url,
                message="Доступ к репозиторию команды выдан",
            )
