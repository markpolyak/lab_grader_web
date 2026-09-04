"""
Tests for the /join/* endpoints (student repo creation flow).

Follows the same style as test_grade_lab_characterization.py: endpoint
functions are called directly (they're plain functions under the slowapi
decorator) with the shared `mock_request` fixture, and GitHub API calls are
mocked with `responses`.

See docs/REPO_GENERATION_PLAN.md §7, §10, §11 (stage 2/3/4 acceptance).
"""
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
    def test_invalid_state_returns_400_not_500(self, mock_request):
        with pytest.raises(HTTPException) as exc_info:
            main_module.join_callback(mock_request, code="abc", state="garbage", error=None)
        assert exc_info.value.status_code == 400

    def test_missing_state_returns_400_not_500(self, mock_request):
        with pytest.raises(HTTPException) as exc_info:
            main_module.join_callback(mock_request, code="abc", state=None, error=None)
        assert exc_info.value.status_code == 400

    def test_expired_state_returns_400_not_500(self, mock_request, mock_get_course_by_id):
        """A validly-signed state older than JOIN_STATE_MAX_AGE must be rejected too,
        not just a garbled/forged one (§10 of the plan)."""
        backdated = time.time() - (main_module.JOIN_STATE_MAX_AGE + 10)
        with patch("itsdangerous.timed.time.time", return_value=backdated):
            state = _get_state(mock_request)

        with pytest.raises(HTTPException) as exc_info:
            main_module.join_callback(mock_request, code="abc", state=state, error=None)
        assert exc_info.value.status_code == 400

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
