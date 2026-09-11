"""
Tests for the /join/* endpoints (student repo creation flow).

Follows the same style as test_grade_lab_characterization.py: endpoint
functions are called directly (they're plain functions under the slowapi
decorator) with the shared `mock_request` fixture, and GitHub API calls are
mocked with `responses`.

See docs/REPO_GENERATION_PLAN.md §7, §10, §11 (stage 2/3/4 acceptance).
"""
import json
import sys
import os
import time
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

import pytest
import responses
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module


@pytest.fixture(autouse=True)
def oauth_env(mock_env_vars, monkeypatch):
    """Configure OAuth settings used by the /join endpoints for every test in this module."""
    monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", "test_client_id")
    monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setattr(main_module, "FRONTEND_URL", "https://front.example.com")
    # Avoid depending on request.base_url (mock_request's scope doesn't set scheme/server).
    monkeypatch.setattr(main_module, "GITHUB_OAUTH_CALLBACK_URL", "https://backend.example.com/join/callback")


@pytest.fixture
def join_course_config(sample_course_config):
    """sample_course_config, with a top-level name and lab '1' configured for /join."""
    config = sample_course_config
    config["name"] = "Test Course"
    config["labs"]["1"]["template-repo"] = "test-org/os-task1-template"
    return config


@pytest.fixture
def mock_get_course_by_id(join_course_config):
    with patch("main.get_course_by_id") as mock:
        mock.return_value = join_course_config
        yield mock


def qs(location: str) -> dict:
    return parse_qs(urlparse(location).query)


class TestJoinInfo:
    def test_returns_public_info_for_configured_lab(self, mock_request, mock_get_course_by_id):
        data = main_module.join_lab_info(mock_request, "test-course", "1")
        assert data["course_name"] == "Test Course"
        assert data["lab_short_name"] == "ЛР1"

    def test_unknown_course_returns_404(self, mock_request):
        with pytest.raises(HTTPException) as exc_info:
            main_module.join_lab_info(mock_request, "does-not-exist", "1")
        assert exc_info.value.status_code == 404

    def test_lab_without_template_repo_returns_400(self, mock_request, join_course_config):
        del join_course_config["labs"]["1"]["template-repo"]
        with patch("main.get_course_by_id", return_value=join_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_info(mock_request, "test-course", "1")
        assert exc_info.value.status_code == 400
        assert "template-repo" in exc_info.value.detail

    def test_unknown_lab_returns_404(self, mock_request, mock_get_course_by_id):
        with pytest.raises(HTTPException) as exc_info:
            main_module.join_lab_info(mock_request, "test-course", "99")
        assert exc_info.value.status_code == 404

    def test_unknown_repo_provisioning_value_returns_400(self, mock_request, join_course_config):
        """A typo like 'forks' must not silently fall back to 'template' (issue #51)."""
        join_course_config["labs"]["1"]["repo-provisioning"] = "forks"
        with patch("main.get_course_by_id", return_value=join_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_info(mock_request, "test-course", "1")
        assert exc_info.value.status_code == 400
        assert "repo-provisioning" in exc_info.value.detail

    def test_fork_repo_provisioning_value_is_accepted(self, mock_request, join_course_config):
        join_course_config["labs"]["1"]["repo-provisioning"] = "fork"
        with patch("main.get_course_by_id", return_value=join_course_config):
            data = main_module.join_lab_info(mock_request, "test-course", "1")
        assert data["course_name"] == "Test Course"

    def test_individual_lab_reports_teams_disabled(self, mock_request, mock_get_course_by_id):
        data = main_module.join_lab_info(mock_request, "test-course", "1")
        assert data["team"]["enabled"] is False
        assert data["team"]["size_max"] is None
        assert data["team"]["count_max"] is None

    def test_team_lab_reports_its_limits(self, mock_request, join_course_config):
        join_course_config["labs"]["1"]["team"] = {"size-max": 4, "count-max": 8}
        with patch("main.get_course_by_id", return_value=join_course_config):
            data = main_module.join_lab_info(mock_request, "test-course", "1")
        assert data["team"]["enabled"] is True
        assert data["team"]["size_max"] == 4
        assert data["team"]["count_max"] == 8

    def test_invalid_team_limit_returns_400(self, mock_request, join_course_config):
        """A bad limit must be a clear config error, not a 500 (stage 1 checklist)."""
        join_course_config["labs"]["1"]["team"] = {"size-max": 0}
        with patch("main.get_course_by_id", return_value=join_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_info(mock_request, "test-course", "1")
        assert exc_info.value.status_code == 400
        assert "size-max" in exc_info.value.detail

    def test_team_limit_of_wrong_type_returns_400(self, mock_request, join_course_config):
        join_course_config["labs"]["1"]["team"] = {"count-max": "восемь"}
        with patch("main.get_course_by_id", return_value=join_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_info(mock_request, "test-course", "1")
        assert exc_info.value.status_code == 400


class TestJoinStart:
    def test_redirects_to_github_authorize_with_signed_state(self, mock_request, mock_get_course_by_id):
        resp = main_module.join_lab_start(mock_request, "test-course", "1")
        location = resp.headers["location"]
        parsed = urlparse(location)
        assert parsed.netloc == "github.com"
        assert parsed.path == "/login/oauth/authorize"

        params = qs(location)
        assert params["client_id"] == ["test_client_id"]
        assert params["scope"] == ["read:user"]
        assert params["redirect_uri"] == ["https://backend.example.com/join/callback"]
        assert "state" in params

    def test_missing_oauth_config_returns_503(self, mock_request, monkeypatch, mock_get_course_by_id):
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", None)
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", None)

        with pytest.raises(HTTPException) as exc_info:
            main_module.join_lab_start(mock_request, "test-course", "1")
        assert exc_info.value.status_code == 503

    def test_unconfigured_lab_errors_before_redirecting_to_github(self, mock_request, join_course_config):
        del join_course_config["labs"]["1"]["template-repo"]
        with patch("main.get_course_by_id", return_value=join_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_start(mock_request, "test-course", "1")
        assert exc_info.value.status_code == 400


def _get_state(mock_request):
    """Do a real /start call to obtain a validly signed state param."""
    resp = main_module.join_lab_start(mock_request, "test-course", "1")
    return qs(resp.headers["location"])["state"][0]


class TestJoinCallback:
    def test_invalid_state_redirects_to_join_error(self, mock_request):
        """course_id/lab_id are unknown for a garbled state, so the student lands
        on the course/lab-agnostic /join/error page instead of a raw 400 response."""
        resp = main_module.join_callback(mock_request, code="abc", state="garbage", error=None)

        location = resp.headers["location"]
        assert location.startswith("https://front.example.com/join/error?")
        params = qs(location)
        assert params["status"] == ["error"]
        assert params["reason"] == ["invalid_state"]

    def test_missing_state_redirects_to_join_error(self, mock_request):
        resp = main_module.join_callback(mock_request, code="abc", state=None, error=None)

        location = resp.headers["location"]
        assert location.startswith("https://front.example.com/join/error?")
        params = qs(location)
        assert params["status"] == ["error"]
        assert params["reason"] == ["invalid_state"]

    def test_expired_state_redirects_to_join_error(self, mock_request, mock_get_course_by_id):
        """A validly-signed state older than JOIN_STATE_MAX_AGE must be rejected too,
        not just a garbled/forged one (§10 of the plan)."""
        backdated = time.time() - (main_module.JOIN_STATE_MAX_AGE + 10)
        with patch("itsdangerous.timed.time.time", return_value=backdated):
            state = _get_state(mock_request)

        resp = main_module.join_callback(mock_request, code="abc", state=state, error=None)

        location = resp.headers["location"]
        assert location.startswith("https://front.example.com/join/error?")
        params = qs(location)
        assert params["status"] == ["error"]
        assert params["reason"] == ["invalid_state"]

    def test_access_denied_redirects_with_error_reason(self, mock_request, mock_get_course_by_id):
        state = _get_state(mock_request)
        resp = main_module.join_callback(mock_request, code=None, state=state, error="access_denied")

        location = resp.headers["location"]
        assert location.startswith("https://front.example.com/join/test-course/1?")
        params = qs(location)
        assert params["status"] == ["error"]
        assert params["reason"] == ["access_denied"]

    @responses.activate
    def test_oauth_exchange_failure_redirects_with_error(self, mock_request, mock_get_course_by_id):
        state = _get_state(mock_request)
        responses.add(
            responses.POST,
            "https://github.com/login/oauth/access_token",
            json={"error": "bad_verification_code"},
            status=400,
        )

        resp = main_module.join_callback(mock_request, code="abc", state=state, error=None)

        params = qs(resp.headers["location"])
        assert params["status"] == ["error"]
        assert params["reason"] == ["oauth_exchange_failed"]

    @responses.activate
    def test_successful_join_redirects_to_success_with_repo_url(self, mock_request, mock_get_course_by_id):
        state = _get_state(mock_request)

        responses.add(
            responses.POST,
            "https://github.com/login/oauth/access_token",
            json={"access_token": "gho_student_token"},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/user",
            json={"login": "student1"},
            status=200,
        )

        org = "test-org"
        repo_name = "test-task1-student1"
        responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}", status=404)
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{org}/os-task1-template/generate",
            json={},
            status=201,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/collaborators/student1",
            status=404,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/invitations",
            json=[],
            status=200,
        )
        responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{org}/{repo_name}/collaborators/student1",
            status=201,
        )

        resp = main_module.join_callback(mock_request, code="abc", state=state, error=None)

        params = qs(resp.headers["location"])
        assert params["status"] == ["success"]
        assert params["username"] == ["student1"]
        assert params["repo_url"] == [f"https://github.com/{org}/{repo_name}"]

    @responses.activate
    def test_successful_join_with_fork_provisioning(self, mock_request, join_course_config):
        """A lab configured with repo-provisioning: fork drives the fork-creation
        branch (fork_repo -> poll -> enable_actions -> clear is_template), not
        the template `generate` API (issue #51)."""
        join_course_config["labs"]["1"]["repo-provisioning"] = "fork"
        with patch("main.get_course_by_id", return_value=join_course_config):
            state = _get_state(mock_request)

            responses.add(
                responses.POST,
                "https://github.com/login/oauth/access_token",
                json={"access_token": "gho_student_token"},
                status=200,
            )
            responses.add(
                responses.GET,
                "https://api.github.com/user",
                json={"login": "student1"},
                status=200,
            )

            org = "test-org"
            repo_name = "test-task1-student1"
            responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}", status=404)
            responses.add(
                responses.GET,
                f"https://api.github.com/repos/{org}/os-task1-template",
                json={"private": True},
                status=200,
            )
            responses.add(
                responses.POST,
                f"https://api.github.com/repos/{org}/os-task1-template/forks",
                json={},
                status=202,
            )
            responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}", status=200)
            responses.add(
                responses.PUT,
                f"https://api.github.com/repos/{org}/{repo_name}/actions/permissions",
                status=204,
            )
            responses.add(
                responses.PATCH,
                f"https://api.github.com/repos/{org}/{repo_name}",
                status=200,
            )
            responses.add(
                responses.GET,
                f"https://api.github.com/repos/{org}/{repo_name}/collaborators/student1",
                status=404,
            )
            responses.add(
                responses.GET,
                f"https://api.github.com/repos/{org}/{repo_name}/invitations",
                json=[],
                status=200,
            )
            responses.add(
                responses.PUT,
                f"https://api.github.com/repos/{org}/{repo_name}/collaborators/student1",
                status=201,
            )

            resp = main_module.join_callback(mock_request, code="abc", state=state, error=None)

        params = qs(resp.headers["location"])
        assert params["status"] == ["success"]
        assert params["username"] == ["student1"]
        assert params["repo_url"] == [f"https://github.com/{org}/{repo_name}"]

    @responses.activate
    def test_repeat_visit_does_not_recreate_existing_repo(self, mock_request, mock_get_course_by_id):
        """Revisiting the join link for an already-provisioned repo must not call /generate again."""
        state = _get_state(mock_request)

        responses.add(
            responses.POST,
            "https://github.com/login/oauth/access_token",
            json={"access_token": "gho_student_token"},
            status=200,
        )
        responses.add(responses.GET, "https://api.github.com/user", json={"login": "student1"}, status=200)

        org = "test-org"
        repo_name = "test-task1-student1"
        responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}", status=200)
        generate_call = responses.add(
            responses.POST,
            f"https://api.github.com/repos/{org}/os-task1-template/generate",
            json={},
            status=201,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/collaborators/student1",
            status=204,
        )

        resp = main_module.join_callback(mock_request, code="abc", state=state, error=None)

        assert qs(resp.headers["location"])["status"] == ["success"]
        assert generate_call.call_count == 0

    @responses.activate
    def test_team_lab_sets_a_session_and_creates_nothing(self, mock_request, join_course_config):
        """A team lab needs a dialogue, so the callback only authenticates
        the student - the repository is created later, by the team endpoints
        (docs/TEAM_ASSIGNMENTS_PLAN.md §8.1)."""
        join_course_config["labs"]["1"]["team"] = {"size-max": 4}
        with patch("main.get_course_by_id", return_value=join_course_config):
            state = _get_state(mock_request)

            responses.add(
                responses.POST,
                "https://github.com/login/oauth/access_token",
                json={"access_token": "gho_student_token"},
                status=200,
            )
            responses.add(
                responses.GET, "https://api.github.com/user",
                json={"login": "student1"}, status=200,
            )
            generate_call = responses.add(
                responses.POST,
                "https://api.github.com/repos/test-org/os-task1-template/generate",
                json={}, status=201,
            )

            resp = main_module.join_callback(mock_request, code="abc", state=state, error=None)

        params = qs(resp.headers["location"])
        assert params["status"] == ["authenticated"]
        assert generate_call.call_count == 0

        cookie = resp.headers["set-cookie"]
        assert "join_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie.replace("samesite", "SameSite")
        assert "Path=/join" in cookie
        assert f"Max-Age={main_module.JOIN_SESSION_MAX_AGE}" in cookie
        assert main_module.JOIN_SESSION_MAX_AGE == 1800

    @responses.activate
    def test_student_access_token_is_never_exposed_in_redirect(self, mock_request, mock_get_course_by_id):
        """The student's one-shot OAuth access token must never leak into the final redirect."""
        state = _get_state(mock_request)

        responses.add(
            responses.POST,
            "https://github.com/login/oauth/access_token",
            json={"access_token": "gho_super_secret_token"},
            status=200,
        )
        responses.add(responses.GET, "https://api.github.com/user", json={"login": "student1"}, status=200)
        org = "test-org"
        repo_name = "test-task1-student1"
        responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}", status=200)
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/collaborators/student1",
            status=204,
        )

        resp = main_module.join_callback(mock_request, code="abc", state=state, error=None)

        assert "gho_super_secret_token" not in resp.headers["location"]


class TestJoinSession:
    """The signed cookie carrying the confirmed username (§6 of the team plan)."""

    def _request_with_cookie(self, cookie_value):
        from starlette.requests import Request

        headers = []
        if cookie_value is not None:
            headers.append((b"cookie", f"join_session={cookie_value}".encode()))
        scope = {
            "type": "http", "method": "GET", "path": "/join",
            "headers": headers, "client": ("127.0.0.1", 12345),
        }
        return Request(scope, lambda: None)

    def test_round_trip(self):
        cookie = main_module._build_join_session("student1", "test-course", "1")
        request = self._request_with_cookie(cookie)

        assert main_module.require_join_session(request, "test-course", "1") == "student1"

    def test_missing_cookie_is_401(self):
        with pytest.raises(HTTPException) as exc_info:
            main_module.require_join_session(self._request_with_cookie(None), "test-course", "1")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "SESSION_REQUIRED"

    def test_forged_cookie_is_401(self):
        request = self._request_with_cookie("not-a-signed-value")
        with pytest.raises(HTTPException) as exc_info:
            main_module.require_join_session(request, "test-course", "1")
        assert exc_info.value.status_code == 401

    def test_expired_cookie_is_401(self):
        backdated = time.time() - (main_module.JOIN_SESSION_MAX_AGE + 10)
        with patch("itsdangerous.timed.time.time", return_value=backdated):
            cookie = main_module._build_join_session("student1", "test-course", "1")

        with pytest.raises(HTTPException) as exc_info:
            main_module.require_join_session(self._request_with_cookie(cookie), "test-course", "1")
        assert exc_info.value.status_code == 401

    def test_cookie_of_another_lab_is_rejected(self):
        """A session obtained for one lab must not act on another."""
        cookie = main_module._build_join_session("student1", "test-course", "2")

        with pytest.raises(HTTPException) as exc_info:
            main_module.require_join_session(self._request_with_cookie(cookie), "test-course", "1")
        assert exc_info.value.status_code == 401

    def test_cookie_of_another_course_is_rejected(self):
        cookie = main_module._build_join_session("student1", "other-course", "1")

        with pytest.raises(HTTPException) as exc_info:
            main_module.require_join_session(self._request_with_cookie(cookie), "test-course", "1")
        assert exc_info.value.status_code == 401


@pytest.fixture
def team_course_config(join_course_config):
    """join_course_config with lab '1' turned into a team lab."""
    join_course_config["labs"]["1"]["team"] = {"size-max": 3, "count-max": 2}
    join_course_config["github"]["teachers"] = ["Mark Polyak", "teacher1"]
    return join_course_config


@pytest.fixture(autouse=True)
def clean_teams_state():
    from grading.teams import reset_teams_state

    reset_teams_state()
    yield
    reset_teams_state()


def _session_request(username="student1", course_id="test-course", lab_id="1"):
    """A Request carrying a valid join_session cookie."""
    from starlette.requests import Request

    cookie = main_module._build_join_session(username, course_id, lab_id)
    scope = {
        "type": "http", "method": "GET", "path": "/join",
        "headers": [(b"cookie", f"join_session={cookie}".encode())],
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope, lambda: None)
    request.state.view_rate_limit = None
    return request


def _team_repo_responses(org="test-org", prefix="test-task1"):
    """Register org repos plus a roster for two teams."""
    responses.add(
        responses.GET,
        f"https://api.github.com/orgs/{org}/repos",
        json=[
            {"name": f"{prefix}-team-1", "description": "Пингвины — учим планировщик"},
            {"name": f"{prefix}-team-2", "description": "Тюлени"},
            {"name": f"{prefix}-student9", "description": "личный репозиторий"},
        ],
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://api.github.com/repos/{org}/{prefix}-team-1/collaborators",
        json=[
            {"login": "alice", "permissions": {"push": True, "admin": False}},
            {"login": "teacher1", "permissions": {"push": True, "admin": True}},
        ],
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://api.github.com/repos/{org}/{prefix}-team-1/invitations",
        json=[{"id": 1, "invitee": {"login": "carol"}}],
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://api.github.com/repos/{org}/{prefix}-team-2/collaborators",
        json=[{"login": "student1", "permissions": {"push": True, "admin": False}}],
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://api.github.com/repos/{org}/{prefix}-team-2/invitations",
        json=[],
        status=200,
    )


class TestJoinLabTeams:
    """GET /join/{course}/{lab}/teams (§8.2 of the team plan)."""

    @responses.activate
    def test_lists_teams_with_rosters(self, team_course_config):
        _team_repo_responses()
        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.join_lab_teams(_session_request(), "test-course", "1")

        assert data["username"] == "student1"
        assert data["size_max"] == 3 and data["count_max"] == 2
        assert [team["slug"] for team in data["teams"]] == ["team-1", "team-2"]

        first = data["teams"][0]
        assert first["title"] == "Пингвины"
        assert first["description"] == "учим планировщик"
        # The organization owner (admin) and the teacher stay out of the roster
        assert first["members"] == ["alice"]
        assert first["pending"] == ["carol"]
        assert first["expired"] == []
        assert first["size"] == 2

    @responses.activate
    def test_repo_url_only_for_my_own_team(self, team_course_config):
        _team_repo_responses()
        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.join_lab_teams(_session_request(), "test-course", "1")

        assert data["my_team"] == "team-2"
        assert data["teams"][0]["repo_url"] is None
        assert data["teams"][0]["is_mine"] is False
        assert data["teams"][1]["repo_url"] == "https://github.com/test-org/test-task1-team-2"
        assert data["teams"][1]["is_mine"] is True

    @responses.activate
    def test_member_of_a_team_cannot_create_another(self, team_course_config):
        _team_repo_responses()
        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.join_lab_teams(_session_request(), "test-course", "1")
        assert data["can_create"] is False

    @responses.activate
    def test_count_max_closes_creation(self, team_course_config):
        _team_repo_responses()
        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.join_lab_teams(_session_request("dave"), "test-course", "1")

        assert data["my_team"] is None
        # count-max is 2 and two teams already exist
        assert data["can_create"] is False

    @responses.activate
    def test_stranger_can_create_while_below_count_max(self, team_course_config):
        team_course_config["labs"]["1"]["team"]["count-max"] = 5
        _team_repo_responses()
        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.join_lab_teams(_session_request("dave"), "test-course", "1")
        assert data["can_create"] is True

    @responses.activate
    def test_is_full_reflects_size_max(self, team_course_config):
        team_course_config["labs"]["1"]["team"]["size-max"] = 2
        _team_repo_responses()
        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.join_lab_teams(_session_request("dave"), "test-course", "1")

        assert data["teams"][0]["is_full"] is True   # alice + pending carol
        assert data["teams"][1]["is_full"] is False

    @responses.activate
    def test_repeat_request_within_ttl_does_not_hit_github_again(self, team_course_config):
        _team_repo_responses()
        with patch("main.get_course_by_id", return_value=team_course_config):
            main_module.join_lab_teams(_session_request(), "test-course", "1")
            calls_after_first = len(responses.calls)
            main_module.join_lab_teams(_session_request(), "test-course", "1")

        assert len(responses.calls) == calls_after_first

    @responses.activate
    def test_unavailable_org_repos_return_502(self, team_course_config):
        responses.add(responses.GET, "https://api.github.com/orgs/test-org/repos", status=500)
        with patch("main.get_course_by_id", return_value=team_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_teams(_session_request(), "test-course", "1")

        assert exc_info.value.status_code == 502
        assert exc_info.value.detail == "TEAMS_UNAVAILABLE"

    def test_without_a_session_returns_401(self, team_course_config, mock_request):
        with patch("main.get_course_by_id", return_value=team_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_teams(mock_request, "test-course", "1")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "SESSION_REQUIRED"

    def test_session_of_another_lab_returns_401(self, team_course_config):
        request = _session_request(lab_id="2")
        with patch("main.get_course_by_id", return_value=team_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_teams(request, "test-course", "1")

        assert exc_info.value.status_code == 401

    def test_individual_lab_returns_not_a_team_lab(self, join_course_config):
        with patch("main.get_course_by_id", return_value=join_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_lab_teams(_session_request(), "test-course", "1")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "NOT_A_TEAM_LAB"

    @responses.activate
    def test_teams_count_appears_in_the_public_info_after_a_read(
        self, team_course_config, mock_request
    ):
        _team_repo_responses()
        with patch("main.get_course_by_id", return_value=team_course_config):
            before = main_module.join_lab_info(mock_request, "test-course", "1")
            assert before["team"]["teams_count"] is None

            main_module.join_lab_teams(_session_request(), "test-course", "1")
            after = main_module.join_lab_info(mock_request, "test-course", "1")

        assert after["team"]["teams_count"] == 2


def _created_team_responses(username="dave", org="test-org", prefix="test-task1", slug="team-3"):
    """GitHub calls made while creating a team from a template."""
    repo = f"{prefix}-{slug}"
    responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo}", status=404)
    responses.add(
        responses.POST,
        f"https://api.github.com/repos/{org}/os-task1-template/generate",
        json={}, status=201,
    )
    responses.add(
        responses.GET, f"https://api.github.com/repos/{org}/{repo}/collaborators/{username}",
        status=404,
    )
    responses.add(
        responses.GET, f"https://api.github.com/repos/{org}/{repo}/invitations",
        json=[], status=200,
    )
    responses.add(
        responses.PUT, f"https://api.github.com/repos/{org}/{repo}/collaborators/{username}",
        status=201,
    )
    responses.add(responses.PATCH, f"https://api.github.com/repos/{org}/{repo}", status=200)


class TestCreateJoinTeam:
    """POST /join/{course}/{lab}/teams."""

    @responses.activate
    def test_creates_a_team_and_returns_its_repository(self, team_course_config):
        team_course_config["labs"]["1"]["team"]["count-max"] = 5
        _team_repo_responses()
        _created_team_responses()
        body = main_module.CreateTeamRequest(title="Моржи", description="третья команда")

        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.create_join_team(
                _session_request("dave"), "test-course", "1", body,
            )

        assert data["status"] == "ok"
        assert data["slug"] == "team-3"
        assert data["repo_url"] == "https://github.com/test-org/test-task1-team-3"

        patches = [
            call for call in responses.calls
            if call.request.method == "PATCH"
            and call.request.url.endswith("/test-task1-team-3")
        ]
        assert json.loads(patches[0].request.body)["description"] == "Моржи — третья команда"

    @responses.activate
    def test_invalid_title_returns_400_with_a_stable_code(self, team_course_config):
        team_course_config["labs"]["1"]["team"]["count-max"] = 5
        _team_repo_responses()
        body = main_module.CreateTeamRequest(title="ab")

        with patch("main.get_course_by_id", return_value=team_course_config):
            response = main_module.create_join_team(
                _session_request("dave"), "test-course", "1", body,
            )

        assert response.status_code == 400
        assert json.loads(response.body)["detail"] == "INVALID_TITLE"

    @responses.activate
    def test_count_max_returns_403(self, team_course_config):
        _team_repo_responses()  # two teams already exist, count-max is 2
        body = main_module.CreateTeamRequest(title="Моржи")

        with patch("main.get_course_by_id", return_value=team_course_config):
            response = main_module.create_join_team(
                _session_request("dave"), "test-course", "1", body,
            )

        assert response.status_code == 403
        assert json.loads(response.body)["detail"] == "TEAM_LIMIT_REACHED"

    @responses.activate
    def test_member_of_a_team_gets_409_with_their_team(self, team_course_config):
        team_course_config["labs"]["1"]["team"]["count-max"] = 5
        _team_repo_responses()
        body = main_module.CreateTeamRequest(title="Моржи")

        with patch("main.get_course_by_id", return_value=team_course_config):
            response = main_module.create_join_team(
                _session_request("student1"), "test-course", "1", body,
            )

        assert response.status_code == 409
        payload = json.loads(response.body)
        assert payload["detail"] == "ALREADY_IN_TEAM"
        assert payload["my_team"] == "team-2"

    def test_without_a_session_returns_401(self, team_course_config, mock_request):
        body = main_module.CreateTeamRequest(title="Моржи")
        with patch("main.get_course_by_id", return_value=team_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.create_join_team(mock_request, "test-course", "1", body)
        assert exc_info.value.status_code == 401

    def test_the_request_body_has_no_username_field(self):
        """The identity comes from the cookie only, so there is nothing to forge."""
        assert "username" not in main_module.CreateTeamRequest.model_fields
        body = main_module.CreateTeamRequest(title="Моржи", username="victim")
        assert not hasattr(body, "username")

    @responses.activate
    def test_access_is_granted_to_the_session_user_only(self, team_course_config):
        """Whatever the body says, the invitation goes to the cookie's user."""
        team_course_config["labs"]["1"]["team"]["count-max"] = 5
        _team_repo_responses()
        _created_team_responses(username="dave")
        body = main_module.CreateTeamRequest(title="Моржи")

        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.create_join_team(
                _session_request("dave"), "test-course", "1", body,
            )

        assert data["status"] == "ok"
        invited = [
            call for call in responses.calls
            if call.request.method == "PUT" and "/collaborators/" in call.request.url
        ]
        assert invited and invited[0].request.url.endswith("/collaborators/dave")


class TestJoinJoinTeam:
    """POST /join/{course}/{lab}/teams/{slug}/join."""

    def _access_responses(self, org="test-org", repo="test-task1-team-1", username="dave"):
        responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo}", status=200)
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo}/collaborators/{username}",
            status=404,
        )
        responses.add(
            responses.GET, f"https://api.github.com/repos/{org}/{repo}/invitations",
            json=[], status=200,
        )
        responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{org}/{repo}/collaborators/{username}",
            status=201,
        )

    @responses.activate
    def test_joins_a_team(self, team_course_config):
        _team_repo_responses()
        self._access_responses()

        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.join_join_team(
                _session_request("dave"), "test-course", "1", "team-1",
            )

        assert data["status"] == "ok"
        assert data["repo_url"] == "https://github.com/test-org/test-task1-team-1"

    @responses.activate
    def test_full_team_returns_409(self, team_course_config):
        team_course_config["labs"]["1"]["team"]["size-max"] = 2
        _team_repo_responses()

        with patch("main.get_course_by_id", return_value=team_course_config):
            response = main_module.join_join_team(
                _session_request("dave"), "test-course", "1", "team-1",
            )

        assert response.status_code == 409
        assert json.loads(response.body)["detail"] == "TEAM_FULL"

    @responses.activate
    def test_unknown_slug_returns_404(self, team_course_config):
        _team_repo_responses()

        with patch("main.get_course_by_id", return_value=team_course_config):
            response = main_module.join_join_team(
                _session_request("dave"), "test-course", "1", "team-9",
            )

        assert response.status_code == 404
        assert json.loads(response.body)["detail"] == "TEAM_NOT_FOUND"

    def test_slug_outside_the_pattern_never_reaches_github(self, team_course_config):
        with patch("main.get_course_by_id", return_value=team_course_config):
            response = main_module.join_join_team(
                _session_request("dave"), "test-course", "1", "../os-task1-student9",
            )

        assert response.status_code == 404
        assert json.loads(response.body)["detail"] == "TEAM_NOT_FOUND"

    @responses.activate
    def test_own_team_repairs_access(self, team_course_config):
        """The "restore access" button re-issues a stale invitation."""
        _team_repo_responses()
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/test-task1-team-2", status=200,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/test-task1-team-2/collaborators/student1",
            status=404,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/test-task1-team-2/invitations",
            json=[{"id": 7, "invitee": {"login": "student1"}}], status=200,
        )
        responses.add(
            responses.DELETE,
            "https://api.github.com/repos/test-org/test-task1-team-2/invitations/7",
            status=204,
        )
        responses.add(
            responses.PUT,
            "https://api.github.com/repos/test-org/test-task1-team-2/collaborators/student1",
            status=201,
        )

        with patch("main.get_course_by_id", return_value=team_course_config):
            data = main_module.join_join_team(
                _session_request("student1"), "test-course", "1", "team-2",
            )

        assert data["status"] == "ok"
        assert any(call.request.method == "DELETE" for call in responses.calls)

    @responses.activate
    def test_member_of_another_team_returns_409(self, team_course_config):
        _team_repo_responses()

        with patch("main.get_course_by_id", return_value=team_course_config):
            response = main_module.join_join_team(
                _session_request("alice"), "test-course", "1", "team-2",
            )

        assert response.status_code == 409
        payload = json.loads(response.body)
        assert payload["detail"] == "ALREADY_IN_TEAM"
        assert payload["my_team"] == "team-1"

    def test_without_a_session_returns_401(self, team_course_config, mock_request):
        with patch("main.get_course_by_id", return_value=team_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.join_join_team(mock_request, "test-course", "1", "team-1")
        assert exc_info.value.status_code == 401


class TestLabKeyCanonicalization:
    """
    One lab is reachable through several spellings of lab_id, and the mutation
    lock must not depend on which one the student's URL used.
    """

    def test_load_team_lab_returns_the_canonical_key(self, team_course_config):
        with patch("main.get_course_by_id", return_value=team_course_config):
            _course, by_key, _config, _org, _team = main_module._load_team_lab("test-course", "1")
            _course, by_name, _config, _org, _team = main_module._load_team_lab("test-course", "ЛР1")

        assert by_key == "1"
        assert by_name == "1"

    @responses.activate
    def test_both_spellings_take_the_same_lock(self, team_course_config):
        """
        Regression: keying the lock by the raw path segment handed "1" and
        "ЛР1" two different locks, so two students could pass count-max,
        size-max and ALREADY_IN_TEAM at the same time.
        """
        import grading.teams as teams_module

        _team_repo_responses()
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/test-task1-team-1",
            json={"name": "test-task1-team-1"},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/test-task1-team-1/collaborators/dave",
            status=404,
        )
        responses.add(
            responses.PUT,
            "https://api.github.com/repos/test-org/test-task1-team-1/collaborators/dave",
            status=201,
        )
        responses.add(
            responses.DELETE,
            "https://api.github.com/repos/test-org/test-task1-team-1/invitations/1",
            status=204,
        )

        keys = []
        real_lab_lock = teams_module.lab_lock

        def recording_lab_lock(course_id, lab_key):
            keys.append((course_id, lab_key))
            return real_lab_lock(course_id, lab_key)

        with patch("grading.teams.lab_lock", recording_lab_lock), \
             patch("main.get_course_by_id", return_value=team_course_config):
            for lab_id in ("1", "ЛР1"):
                main_module.join_join_team(
                    _session_request("dave", lab_id=lab_id), "test-course", lab_id, "team-1",
                )

        assert len(keys) == 2
        assert keys[0] == keys[1] == ("test-course", "1")
