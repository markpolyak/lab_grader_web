"""
Tests for team (group) lab assignments (grading/teams.py).

GitHub API calls are mocked the same way tests/test_repo_provisioning.py does
it. See docs/TEAM_ASSIGNMENTS_PLAN.md §15 for the list this file covers.
"""
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.repo_provisioning import ProvisionResult, ProvisionStatus
from grading.teams import (
    DESCRIPTION_MAX_LENGTH,
    TEAMS_CACHE_TTL_SECONDS,
    TITLE_MAX_LENGTH,
    TeamActionStatus,
    TeamConfig,
    TeamConfigError,
    TeamInfo,
    TeamRegistry,
    TeamTitleError,
    clean_team_description,
    clean_team_title,
    compose_description,
    is_team_lab,
    parse_description,
    parse_team_config,
    reset_teams_state,
)


class TestIsTeamLab:
    """The `team` section is what makes a lab a team lab."""

    def test_lab_without_section(self):
        assert is_team_lab({"github-prefix": "os-task1"}) is False

    def test_empty_section_still_counts(self):
        assert is_team_lab({"team": {}}) is True

    def test_null_section_still_counts(self):
        """`team:` with nothing under it parses as None in YAML."""
        assert is_team_lab({"team": None}) is True

    def test_section_with_limits(self):
        assert is_team_lab({"team": {"size-max": 4}}) is True

    def test_not_a_dict(self):
        assert is_team_lab(None) is False


class TestParseTeamConfig:
    """Validation of the `team` section (§4 of the plan)."""

    def test_individual_lab_yields_none(self):
        assert parse_team_config({"github-prefix": "os-task1"}) is None

    def test_empty_section_has_no_limits(self):
        assert parse_team_config({"team": {}}) == TeamConfig(size_max=None, count_max=None)

    def test_null_section_has_no_limits(self):
        assert parse_team_config({"team": None}) == TeamConfig()

    def test_both_limits(self):
        assert parse_team_config({"team": {"size-max": 4, "count-max": 8}}) == TeamConfig(4, 8)

    def test_one_limit_only(self):
        assert parse_team_config({"team": {"size-max": 3}}) == TeamConfig(size_max=3)

    def test_zero_is_rejected(self):
        with pytest.raises(TeamConfigError) as exc:
            parse_team_config({"team": {"size-max": 0}})
        assert "size-max" in str(exc.value)

    def test_negative_is_rejected(self):
        with pytest.raises(TeamConfigError):
            parse_team_config({"team": {"count-max": -1}})

    def test_string_is_rejected(self):
        with pytest.raises(TeamConfigError) as exc:
            parse_team_config({"team": {"count-max": "восемь"}})
        assert "count-max" in str(exc.value)

    def test_bool_is_rejected(self):
        """`size-max: yes` is a bool in YAML, and bool is an int subclass."""
        with pytest.raises(TeamConfigError):
            parse_team_config({"team": {"size-max": True}})

    def test_float_is_rejected(self):
        with pytest.raises(TeamConfigError):
            parse_team_config({"team": {"size-max": 2.5}})

    def test_section_of_wrong_type_is_rejected(self):
        with pytest.raises(TeamConfigError):
            parse_team_config({"team": [1, 2]})


class TestParseDescription:
    """Title and description are stored in the repository's description field."""

    def test_title_and_description(self):
        assert parse_description("Пингвины — учим планировщик") == (
            "Пингвины", "учим планировщик"
        )

    def test_title_only(self):
        assert parse_description("Пингвины") == ("Пингвины", "")

    def test_empty(self):
        assert parse_description("") == ("", "")
        assert parse_description(None) == ("", "")

    def test_separator_inside_the_description_survives(self):
        """Only the first separator splits; the title never contains one."""
        assert parse_description("Пингвины — первый — второй") == (
            "Пингвины", "первый — второй"
        )

    def test_hand_edited_description_without_separator(self):
        """A teacher editing the field by hand must degrade, not error."""
        title, description = parse_description("что-то написанное вручную")
        assert title == "что-то написанное вручную"
        assert description == ""

    def test_plain_dash_is_not_the_separator(self):
        assert parse_description("Кто-то - что-то") == ("Кто-то - что-то", "")


def _repo(name, description=None):
    return {"name": name, "description": description}


def _collaborator(login, push=True, admin=False):
    return {"login": login, "permissions": {"push": push, "admin": admin}}


def _invitation(login, expired=False):
    return {"invitee": {"login": login}, "expired": expired}


class FakeGitHub:
    """Minimal stand-in for GitHubClient covering the calls TeamRegistry makes."""

    def __init__(self, repos=None, collaborators=None, invitations=None):
        self.repos = repos
        self.collaborators = collaborators or {}
        self.invitations = invitations or {}
        self.existing_repos = set()
        self.updated = []
        self.org_repo_calls = 0
        self.roster_calls = 0

    def list_org_repos(self, org):
        self.org_repo_calls += 1
        return self.repos

    def list_collaborators(self, org, repo, affiliation="direct"):
        self.roster_calls += 1
        return self.collaborators.get(repo, [])

    def list_invitations(self, org, repo):
        return self.invitations.get(repo, [])

    def repo_exists(self, org, repo):
        return repo in self.existing_repos

    def update_repo(self, org, repo, payload):
        self.updated.append((repo, payload))
        return SimpleNamespace(status_code=200, text="")


@pytest.fixture(autouse=True)
def clean_teams_state():
    """The cache and the lab locks are module-level, like the job stores."""
    reset_teams_state()
    yield
    reset_teams_state()


class TestListTeams:
    """Collecting the teams of a lab from the organization's repositories."""

    def test_matches_only_this_labs_team_repos(self):
        github = FakeGitHub(repos=[
            _repo("os-task5-team-1", "Пингвины"),
            _repo("os-task5-team-2", "Тюлени — вторая команда"),
            _repo("os-task5-student1"),
            _repo("os-task4-team-1"),
            _repo("unrelated"),
        ])
        teams = TeamRegistry(github).list_teams("test-org", "os-task5")

        assert [team.slug for team in teams] == ["team-1", "team-2"]
        assert teams[0].title == "Пингвины"
        assert teams[1].description == "вторая команда"
        assert teams[0].repo_url == "https://github.com/test-org/os-task5-team-1"

    def test_prefix_collision_with_longer_lab_number(self):
        """os-task1 must not swallow os-task10, as in filter_lab_repos."""
        github = FakeGitHub(repos=[_repo("os-task1-team-1"), _repo("os-task10-team-2")])

        assert [t.slug for t in TeamRegistry(github).list_teams("o", "os-task1")] == ["team-1"]
        assert [t.slug for t in TeamRegistry(github).list_teams("o", "os-task10")] == ["team-2"]

    def test_individual_repos_are_not_teams(self):
        github = FakeGitHub(repos=[_repo("os-task5-team"), _repo("os-task5-teamwork")])
        assert TeamRegistry(github).list_teams("o", "os-task5") == []

    def test_teams_are_ordered_by_number(self):
        github = FakeGitHub(repos=[
            _repo("os-task5-team-10"), _repo("os-task5-team-2"), _repo("os-task5-team-1"),
        ])
        teams = TeamRegistry(github).list_teams("o", "os-task5")
        assert [team.number for team in teams] == [1, 2, 10]

    def test_unavailable_org_repos_yield_none(self):
        assert TeamRegistry(FakeGitHub(repos=None)).list_teams("o", "os-task5") is None


class TestTeamRoster:
    """Who counts as a member of a team (§7.1)."""

    def test_members_and_pending(self):
        github = FakeGitHub(
            repos=[_repo("os-task5-team-1", "Пингвины")],
            collaborators={"os-task5-team-1": [_collaborator("alice"), _collaborator("bob")]},
            invitations={"os-task5-team-1": [_invitation("carol")]},
        )
        team = TeamRegistry(github).list_teams("o", "os-task5")[0]

        assert team.members == ["alice", "bob"]
        assert team.pending == ["carol"]
        assert team.size == 3

    def test_organization_owner_is_excluded(self):
        """Owners appear as collaborators through their organization role."""
        github = FakeGitHub(
            repos=[_repo("os-task5-team-1")],
            collaborators={"os-task5-team-1": [
                _collaborator("owner", push=True, admin=True),
                _collaborator("alice"),
            ]},
        )
        team = TeamRegistry(github).list_teams("o", "os-task5")[0]
        assert team.members == ["alice"]

    def test_read_only_collaborator_is_excluded(self):
        github = FakeGitHub(
            repos=[_repo("os-task5-team-1")],
            collaborators={"os-task5-team-1": [_collaborator("viewer", push=False)]},
        )
        assert TeamRegistry(github).list_teams("o", "os-task5")[0].members == []

    def test_teachers_are_excluded_case_insensitively(self):
        github = FakeGitHub(
            repos=[_repo("os-task5-team-1")],
            collaborators={"os-task5-team-1": [_collaborator("MarkPolyak"), _collaborator("alice")]},
            invitations={"os-task5-team-1": [_invitation("markpolyak")]},
        )
        team = TeamRegistry(github).list_teams(
            "o", "os-task5", teachers=["Mark Polyak", "markpolyak"]
        )
        assert team[0].members == ["alice"]
        assert team[0].pending == []

    def test_unreadable_roster_marks_only_that_team(self):
        github = FakeGitHub(
            repos=[_repo("os-task5-team-1"), _repo("os-task5-team-2")],
            collaborators={"os-task5-team-2": [_collaborator("bob")]},
        )
        github.list_collaborators = lambda org, repo, affiliation="direct": (
            None if repo == "os-task5-team-1" else [_collaborator("bob")]
        )
        teams = TeamRegistry(github).list_teams("o", "os-task5")

        assert teams[0].members_unknown is True
        assert teams[0].members == [] and teams[0].size == 0
        assert teams[1].members_unknown is False
        assert teams[1].members == ["bob"]

    def test_has_member_is_case_insensitive(self):
        team = TeamInfo(
            slug="team-1", number=1, repo_name="r", repo_url="u",
            members=["Alice"], pending=["Carol"],
        )
        assert team.has_member("alice") is True
        assert team.has_member("carol") is True
        assert team.has_member("bob") is False


class TestMemberLookup:
    TEAMS = [
        TeamInfo(slug="team-1", number=1, repo_name="os-task5-team-1", repo_url="u1",
                 members=["alice"], pending=["carol"]),
        TeamInfo(slug="team-2", number=2, repo_name="os-task5-team-2", repo_url="u2",
                 members=["bob"]),
    ]

    def test_find_member_team(self):
        assert TeamRegistry.find_member_team(self.TEAMS, "BOB").slug == "team-2"

    def test_find_member_team_for_a_pending_invitee(self):
        assert TeamRegistry.find_member_team(self.TEAMS, "carol").slug == "team-1"

    def test_find_member_team_for_a_stranger(self):
        assert TeamRegistry.find_member_team(self.TEAMS, "dave") is None

    def test_member_index_covers_members_and_pending(self):
        index = TeamRegistry.member_index(self.TEAMS)
        assert index["alice"].repo_name == "os-task5-team-1"
        assert index["carol"].repo_name == "os-task5-team-1"
        assert index["bob"].repo_name == "os-task5-team-2"
        assert "dave" not in index


class TestNextTeamNumber:
    def test_first_team(self):
        assert TeamRegistry.next_team_number([]) == 1

    def test_consecutive_numbers(self):
        teams = [
            TeamInfo(slug="team-1", number=1, repo_name="r1", repo_url="u"),
            TeamInfo(slug="team-2", number=2, repo_name="r2", repo_url="u"),
        ]
        assert TeamRegistry.next_team_number(teams) == 3

    def test_gap_is_reused(self):
        """Deleting a team frees its number for the next one created."""
        teams = [
            TeamInfo(slug="team-2", number=2, repo_name="r2", repo_url="u"),
            TeamInfo(slug="team-3", number=3, repo_name="r3", repo_url="u"),
        ]
        assert TeamRegistry.next_team_number(teams) == 1


class TestTeamsCache:
    """One set of GitHub requests per lab per TTL (§7.2)."""

    def test_second_read_within_ttl_does_not_hit_github(self):
        github = FakeGitHub(repos=[_repo("os-task5-team-1")])
        registry = TeamRegistry(github)

        registry.list_teams("o", "os-task5")
        registry.list_teams("o", "os-task5")

        assert github.org_repo_calls == 1

    def test_a_second_registry_shares_the_cache(self):
        """A registry is built per request, so the cache lives in the module."""
        github = FakeGitHub(repos=[_repo("os-task5-team-1")])
        TeamRegistry(github).list_teams("o", "os-task5")
        TeamRegistry(github).list_teams("o", "os-task5")

        assert github.org_repo_calls == 1

    def test_fresh_bypasses_the_cache(self):
        github = FakeGitHub(repos=[_repo("os-task5-team-1")])
        registry = TeamRegistry(github)

        registry.list_teams("o", "os-task5")
        registry.list_teams("o", "os-task5", fresh=True)

        assert github.org_repo_calls == 2

    def test_expired_entry_is_refetched(self):
        github = FakeGitHub(repos=[_repo("os-task5-team-1")])
        registry = TeamRegistry(github)
        registry.list_teams("o", "os-task5")

        with patch("grading.teams.time.time", return_value=time.time() + TEAMS_CACHE_TTL_SECONDS + 1):
            registry.list_teams("o", "os-task5")

        assert github.org_repo_calls == 2

    def test_invalidate_drops_the_entry(self):
        github = FakeGitHub(repos=[_repo("os-task5-team-1")])
        registry = TeamRegistry(github)
        registry.list_teams("o", "os-task5")
        registry.invalidate("o", "os-task5")

        assert registry.cached_teams("o", "os-task5") is None
        registry.list_teams("o", "os-task5")
        assert github.org_repo_calls == 2

    def test_other_labs_are_cached_separately(self):
        github = FakeGitHub(repos=[_repo("os-task5-team-1")])
        registry = TeamRegistry(github)
        registry.list_teams("o", "os-task5")
        registry.list_teams("o", "os-task6")

        assert github.org_repo_calls == 2

    def test_cached_teams_is_empty_before_the_first_read(self):
        assert TeamRegistry(FakeGitHub()).cached_teams("o", "os-task5") is None


class TestCleanTeamTitle:
    """Title validation (§3.4). Manual, so the error code stays stable."""

    def test_plain_title(self):
        assert clean_team_title("Пингвины") == "Пингвины"

    def test_strips_and_collapses_whitespace(self):
        assert clean_team_title("  Весёлые   пингвины  ") == "Весёлые пингвины"

    def test_newline_becomes_a_space(self):
        assert clean_team_title("Пингвины\nи тюлени") == "Пингвины и тюлени"

    def test_empty_is_rejected(self):
        with pytest.raises(TeamTitleError):
            clean_team_title("")
        with pytest.raises(TeamTitleError):
            clean_team_title("   ")
        with pytest.raises(TeamTitleError):
            clean_team_title(None)

    def test_too_short_is_rejected(self):
        with pytest.raises(TeamTitleError):
            clean_team_title("ab")

    def test_too_long_is_rejected(self):
        with pytest.raises(TeamTitleError):
            clean_team_title("я" * (TITLE_MAX_LENGTH + 1))

    def test_maximum_length_is_accepted(self):
        assert len(clean_team_title("я" * TITLE_MAX_LENGTH)) == TITLE_MAX_LENGTH

    def test_separator_in_the_title_is_rejected(self):
        """It would make the stored description unparseable."""
        with pytest.raises(TeamTitleError):
            clean_team_title("Пингвины — лучшие")

    def test_control_characters_are_dropped(self):
        assert clean_team_title("Пинг\x00вины") == "Пингвины"


class TestCleanTeamDescription:
    def test_optional(self):
        assert clean_team_description(None) == ""
        assert clean_team_description("") == ""

    def test_collapses_whitespace(self):
        assert clean_team_description(" учим\n планировщик ") == "учим планировщик"

    def test_too_long_is_rejected(self):
        with pytest.raises(TeamTitleError):
            clean_team_description("я" * (DESCRIPTION_MAX_LENGTH + 1))

    def test_separator_is_allowed_in_the_description(self):
        assert clean_team_description("первый — второй") == "первый — второй"


class TestComposeDescription:
    def test_with_description(self):
        assert compose_description("Пингвины", "учим планировщик") == (
            "Пингвины — учим планировщик"
        )

    def test_without_description(self):
        assert compose_description("Пингвины", "") == "Пингвины"

    def test_round_trip(self):
        composed = compose_description("Пингвины", "учим планировщик")
        assert parse_description(composed) == ("Пингвины", "учим планировщик")


class FakeProvisioner:
    """Records provision() calls instead of talking to GitHub."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def provision(self, org, github_prefix, template_repo, repo_suffix,
                  mode="template", access_username=None, force_invite=False):
        self.calls.append({
            "org": org, "github_prefix": github_prefix, "template_repo": template_repo,
            "repo_suffix": repo_suffix, "mode": mode, "access_username": access_username,
            "force_invite": force_invite,
        })
        if self.result is not None:
            return self.result
        repo_name = f"{github_prefix}-{repo_suffix}"
        return ProvisionResult(
            status=ProvisionStatus.OK,
            repo_name=repo_name,
            repo_url=f"https://github.com/{org}/{repo_name}",
        )


def _registry(github, provisioner=None):
    return TeamRegistry(github, provisioner or FakeProvisioner())


LAB = dict(course_id="c", lab_key="5", org="test-org",
           github_prefix="os-task5", template_repo="test-org/os-task5-template")


class TestCreateTeam:
    """Creating a team (§7.3)."""

    def test_first_team_gets_team_1(self):
        github = FakeGitHub(repos=[])
        provisioner = FakeProvisioner()
        result = _registry(github, provisioner).create_team(
            **LAB, username="alice", title="Пингвины", description="учим планировщик",
        )

        assert result.status == TeamActionStatus.OK
        assert result.team.slug == "team-1"
        assert result.repo_url == "https://github.com/test-org/os-task5-team-1"
        assert provisioner.calls[0]["repo_suffix"] == "team-1"
        assert provisioner.calls[0]["access_username"] == "alice"

    def test_next_team_gets_the_following_number(self):
        github = FakeGitHub(repos=[_repo("os-task5-team-1", "Пингвины")])
        result = _registry(github).create_team(**LAB, username="dave", title="Тюлени")
        assert result.team.slug == "team-2"

    def test_title_and_description_are_written_to_the_repository(self):
        github = FakeGitHub(repos=[])
        _registry(github).create_team(
            **LAB, username="alice", title="Пингвины", description="учим планировщик",
        )
        assert github.updated == [
            ("os-task5-team-1", {"description": "Пингвины — учим планировщик"})
        ]

    def test_description_is_just_the_title_when_empty(self):
        github = FakeGitHub(repos=[])
        _registry(github).create_team(**LAB, username="alice", title="Пингвины")
        assert github.updated == [("os-task5-team-1", {"description": "Пингвины"})]

    def test_fork_mode_is_passed_through(self):
        provisioner = FakeProvisioner()
        _registry(FakeGitHub(repos=[]), provisioner).create_team(
            **LAB, username="alice", title="Пингвины", mode="fork",
        )
        assert provisioner.calls[0]["mode"] == "fork"

    def test_invalid_title_creates_nothing(self):
        provisioner = FakeProvisioner()
        result = _registry(FakeGitHub(repos=[]), provisioner).create_team(
            **LAB, username="alice", title="ab",
        )
        assert result.error_code == "INVALID_TITLE"
        assert provisioner.calls == []

    def test_duplicate_title_is_refused(self):
        github = FakeGitHub(repos=[_repo("os-task5-team-1", "Пингвины")])
        result = _registry(github).create_team(**LAB, username="dave", title="  пингвины ")
        assert result.error_code == "TITLE_TAKEN"

    def test_count_max_blocks_creation(self):
        github = FakeGitHub(repos=[
            _repo("os-task5-team-1", "Пингвины"), _repo("os-task5-team-2", "Тюлени"),
        ])
        result = _registry(github).create_team(
            **LAB, username="dave", title="Моржи", team_config=TeamConfig(count_max=2),
        )
        assert result.error_code == "TEAM_LIMIT_REACHED"

    def test_student_already_in_a_team_cannot_create_another(self):
        github = FakeGitHub(
            repos=[_repo("os-task5-team-1", "Пингвины")],
            collaborators={"os-task5-team-1": [_collaborator("alice")]},
        )
        result = _registry(github).create_team(**LAB, username="ALICE", title="Моржи")

        assert result.error_code == "ALREADY_IN_TEAM"
        assert result.team.slug == "team-1"
        assert result.repo_url == "https://github.com/test-org/os-task5-team-1"

    def test_pending_invitation_also_blocks_creating_another_team(self):
        github = FakeGitHub(
            repos=[_repo("os-task5-team-1", "Пингвины")],
            invitations={"os-task5-team-1": [_invitation("carol")]},
        )
        result = _registry(github).create_team(**LAB, username="carol", title="Моржи")
        assert result.error_code == "ALREADY_IN_TEAM"

    def test_existing_repository_name_is_a_race(self):
        """The organization listing lagged behind, or the name is taken."""
        github = FakeGitHub(repos=[])
        github.existing_repos.add("os-task5-team-1")
        provisioner = FakeProvisioner()

        result = _registry(github, provisioner).create_team(
            **LAB, username="alice", title="Пингвины",
        )
        assert result.error_code == "SLUG_RACE"
        assert provisioner.calls == []

    def test_unavailable_org_repos(self):
        result = _registry(FakeGitHub(repos=None)).create_team(
            **LAB, username="alice", title="Пингвины",
        )
        assert result.error_code == "TEAMS_UNAVAILABLE"

    def test_provisioning_failure_is_passed_through(self):
        provisioner = FakeProvisioner(result=ProvisionResult(
            status=ProvisionStatus.ERROR,
            message="Репозиторий-шаблон не найден",
            error_code="TEMPLATE_NOT_FOUND",
        ))
        result = _registry(FakeGitHub(repos=[]), provisioner).create_team(
            **LAB, username="alice", title="Пингвины",
        )
        assert result.error_code == "TEMPLATE_NOT_FOUND"

    def test_the_cache_is_dropped_after_creation(self):
        github = FakeGitHub(repos=[])
        registry = _registry(github)
        registry.list_teams("test-org", "os-task5")
        registry.create_team(**LAB, username="alice", title="Пингвины")

        assert registry.cached_teams("test-org", "os-task5") is None

    def test_reads_the_team_list_fresh_under_the_lock(self):
        """A stale cache must not decide the number or the title check."""
        github = FakeGitHub(repos=[])
        registry = _registry(github)
        registry.list_teams("test-org", "os-task5")
        calls_before = github.org_repo_calls

        registry.create_team(**LAB, username="alice", title="Пингвины")
        assert github.org_repo_calls == calls_before + 1


class TestJoinTeam:
    """Joining a team, or repairing access to one's own (§7.4)."""

    def _github(self):
        return FakeGitHub(
            repos=[_repo("os-task5-team-1", "Пингвины"), _repo("os-task5-team-2", "Тюлени")],
            collaborators={"os-task5-team-1": [_collaborator("alice")]},
            invitations={"os-task5-team-1": [_invitation("carol")]},
        )

    def test_joins_an_existing_team(self):
        provisioner = FakeProvisioner()
        result = _registry(self._github(), provisioner).join_team(
            **LAB, username="dave", slug="team-1",
        )

        assert result.status == TeamActionStatus.OK
        assert result.repo_url == "https://github.com/test-org/os-task5-team-1"
        assert provisioner.calls[0]["repo_suffix"] == "team-1"
        assert provisioner.calls[0]["access_username"] == "dave"

    def test_full_team_is_refused(self):
        result = _registry(self._github()).join_team(
            **LAB, username="dave", slug="team-1", team_config=TeamConfig(size_max=2),
        )
        assert result.error_code == "TEAM_FULL"

    def test_pending_invitation_occupies_a_place(self):
        """alice is a member and carol is invited - two of two places."""
        github = self._github()
        result = _registry(github).join_team(
            **LAB, username="dave", slug="team-1", team_config=TeamConfig(size_max=2),
        )
        assert result.error_code == "TEAM_FULL"

    def test_own_team_only_repairs_access_even_when_full(self):
        provisioner = FakeProvisioner()
        result = _registry(self._github(), provisioner).join_team(
            **LAB, username="ALICE", slug="team-1", team_config=TeamConfig(size_max=2),
        )

        assert result.status == TeamActionStatus.OK
        assert provisioner.calls[0]["access_username"] == "ALICE"

    def test_member_of_another_team_is_refused(self):
        result = _registry(self._github()).join_team(**LAB, username="alice", slug="team-2")

        assert result.error_code == "ALREADY_IN_TEAM"
        assert result.team.slug == "team-1"

    def test_unknown_slug(self):
        result = _registry(self._github()).join_team(**LAB, username="dave", slug="team-9")
        assert result.error_code == "TEAM_NOT_FOUND"

    def test_slug_not_matching_the_pattern_is_rejected_before_any_call(self):
        """The repository name is assembled by the server, never accepted."""
        github = self._github()
        provisioner = FakeProvisioner()
        for bad in ("../../secret", "team-1/../x", "student1", "TEAM-1", ""):
            result = _registry(github, provisioner).join_team(
                **LAB, username="dave", slug=bad,
            )
            assert result.error_code == "TEAM_NOT_FOUND", bad
        assert provisioner.calls == []
        assert github.org_repo_calls == 0

    def test_unreadable_roster_blocks_joining(self):
        github = self._github()
        github.list_collaborators = lambda org, repo, affiliation="direct": None
        result = _registry(github).join_team(
            **LAB, username="dave", slug="team-1", team_config=TeamConfig(size_max=4),
        )
        assert result.error_code == "TEAMS_UNAVAILABLE"

    def test_provisioning_failure_is_passed_through(self):
        provisioner = FakeProvisioner(result=ProvisionResult(
            status=ProvisionStatus.ERROR,
            message="Не удалось предоставить доступ",
            error_code="INVITE_FAILED",
        ))
        result = _registry(self._github(), provisioner).join_team(
            **LAB, username="dave", slug="team-1",
        )
        assert result.error_code == "INVITE_FAILED"

    def test_the_cache_is_dropped_after_joining(self):
        github = self._github()
        registry = _registry(github)
        registry.list_teams("test-org", "os-task5")
        registry.join_team(**LAB, username="dave", slug="team-1")

        assert registry.cached_teams("test-org", "os-task5") is None


class TestExpiredInvitation:
    """
    GitHub keeps an expired invitation listed with `expired: true`. It must
    keep holding the place: otherwise the seat silently goes to someone else
    and the invited student drops out of their own team (§7.1).
    """

    def _github(self):
        return FakeGitHub(
            repos=[_repo("os-task5-team-1", "Пингвины")],
            collaborators={"os-task5-team-1": [_collaborator("alice")]},
            invitations={"os-task5-team-1": [_invitation("carol", expired=True)]},
        )

    def test_expired_invitation_still_holds_a_place(self):
        team = TeamRegistry(self._github()).list_teams("o", "os-task5")[0]

        assert team.pending == ["carol"]
        assert team.expired == ["carol"]
        assert team.size == 2
        assert team.has_member("carol")

    def test_fresh_invitation_is_not_reported_as_expired(self):
        github = self._github()
        github.invitations = {"os-task5-team-1": [_invitation("carol")]}
        team = TeamRegistry(github).list_teams("o", "os-task5")[0]

        assert team.pending == ["carol"]
        assert team.expired == []

    def test_nobody_else_takes_the_place(self):
        provisioner = FakeProvisioner()
        result = _registry(self._github(), provisioner).join_team(
            **LAB, username="dave", slug="team-1", team_config=TeamConfig(size_max=2),
        )

        assert result.error_code == "TEAM_FULL"
        assert provisioner.calls == []

    def test_the_invited_student_repairs_access(self):
        provisioner = FakeProvisioner()
        result = _registry(self._github(), provisioner).join_team(
            **LAB, username="carol", slug="team-1", team_config=TeamConfig(size_max=2),
        )

        assert result.status == TeamActionStatus.OK
        assert provisioner.calls[0]["access_username"] == "carol"
        assert provisioner.calls[0]["force_invite"] is False

    def test_the_invited_student_is_graded_with_the_team(self):
        teams = TeamRegistry(self._github()).list_teams("o", "os-task5")
        assert TeamRegistry.member_index(teams)["carol"].slug == "team-1"


class TestUnreadableRoster:
    """
    A roster GitHub refused to hand over is not evidence that the student is
    in no team - §7.3/§7.4. Regression: it used to read as "teamless" and
    handed the student a second team with a second repository.
    """

    def _github(self):
        return FakeGitHub(
            repos=[_repo("os-task5-team-1", "Пингвины"), _repo("os-task5-team-2", "Тюлени")],
            collaborators={
                "os-task5-team-1": [_collaborator("alice")],
                # The student may well be in this one - there is no way to tell.
                "os-task5-team-2": None,
            },
        )

    def test_creating_a_team_is_refused(self):
        provisioner = FakeProvisioner()
        result = _registry(self._github(), provisioner).create_team(
            **LAB, username="dave", title="Моржи",
        )

        assert result.error_code == "TEAMS_UNAVAILABLE"
        assert provisioner.calls == [], "репозиторий не должен быть создан"

    def test_joining_another_team_is_refused(self):
        provisioner = FakeProvisioner()
        result = _registry(self._github(), provisioner).join_team(
            **LAB, username="dave", slug="team-1",
        )

        assert result.error_code == "TEAMS_UNAVAILABLE"
        assert provisioner.calls == []

    def test_repairing_access_in_your_own_team_still_works(self):
        """alice is readable in team-1, so team-2 being unreadable changes nothing for her."""
        provisioner = FakeProvisioner()
        result = _registry(self._github(), provisioner).join_team(
            **LAB, username="alice", slug="team-1",
        )

        assert result.status == TeamActionStatus.OK
        assert provisioner.calls[0]["force_invite"] is False


class TestMembershipDefinition:
    """
    The roster is the single definition of membership: a student missing from
    it gets a direct push invitation even if GitHub says they can already
    reach the repository (read-only access, or write inherited from the
    organization's base permission).
    """

    def _github(self):
        return FakeGitHub(
            repos=[_repo("os-task5-team-1", "Пингвины")],
            collaborators={"os-task5-team-1": [_collaborator("alice")]},
            invitations={"os-task5-team-1": [_invitation("carol")]},
        )

    def test_a_student_outside_the_roster_is_force_invited(self):
        provisioner = FakeProvisioner()
        _registry(self._github(), provisioner).join_team(
            **LAB, username="dave", slug="team-1",
        )

        assert provisioner.calls[0]["force_invite"] is True
        assert provisioner.calls[0]["access_username"] == "dave"

    def test_an_existing_member_is_not_force_invited(self):
        provisioner = FakeProvisioner()
        _registry(self._github(), provisioner).join_team(
            **LAB, username="alice", slug="team-1",
        )

        assert provisioner.calls[0]["force_invite"] is False

    def test_a_pending_invitee_repairs_access_without_forcing(self):
        """carol was invited and has not accepted - the re-invite path handles her."""
        provisioner = FakeProvisioner()
        result = _registry(self._github(), provisioner).join_team(
            **LAB, username="carol", slug="team-1",
        )

        assert result.status == TeamActionStatus.OK
        assert provisioner.calls[0]["force_invite"] is False
