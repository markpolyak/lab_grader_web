"""
Tests for team (group) lab assignments (grading/teams.py).

GitHub API calls are mocked the same way tests/test_repo_provisioning.py does
it. See docs/TEAM_ASSIGNMENTS_PLAN.md §15 for the list this file covers.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.teams import (
    TeamConfig,
    TeamConfigError,
    is_team_lab,
    parse_team_config,
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
