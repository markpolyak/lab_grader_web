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

from grading.teams import (
    TEAMS_CACHE_TTL_SECONDS,
    TeamConfig,
    TeamConfigError,
    TeamInfo,
    TeamRegistry,
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


def _invitation(login):
    return {"invitee": {"login": login}}


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
