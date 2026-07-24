"""Тесты endpoint полного OAuth-сценария `/join`."""

from urllib.parse import parse_qs, urlsplit

import pytest
import responses
from fastapi.testclient import TestClient
from freezegun import freeze_time

import main
from grading.github_oauth import GitHubOAuthError
from grading.repository_provisioner import (
    GitHubRepositoryClient,
    ProvisionResult,
    RepositoryProvisionError,
)


@pytest.fixture
def join_course():
    return {
        "name": "Operating Systems",
        "github": {"organization": "test-org"},
        "labs": {
            "01": {
                "short-name": "ЛР0.1",
                "github-prefix": "os-task-I-1",
                "template-repo": "teacher-org/os-template",
            }
        },
        "_meta": {"filename": "test.yaml"},
    }


@pytest.fixture
def join_client(monkeypatch, join_course):
    monkeypatch.setattr(main, "GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setattr(main, "GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(
        main,
        "GITHUB_OAUTH_CALLBACK_URL",
        "https://grader.example/api/v1/join/callback",
    )
    monkeypatch.setattr(main, "FRONTEND_BASE_URL", "https://grader.example")

    def fake_get_course(course_id):
        if course_id != "os-2026":
            raise main.HTTPException(status_code=404, detail="Course not found")
        return join_course

    monkeypatch.setattr(main, "get_course_by_id", fake_get_course)
    return TestClient(main.app)


def signed_state(join_client, course_id="os-2026", lab_id="01", set_cookie=True):
    nonce = "test-browser-nonce-with-sufficient-entropy"
    if set_cookie:
        join_client.cookies.set(main.OAUTH_NONCE_COOKIE_NAME, nonce)
    return main.oauth_state_serializer.dumps(
        {"course_id": course_id, "lab_id": lab_id, "nonce": nonce}
    )


def assert_oauth_state_error(response, expected_code):
    """Проверить безопасный redirect общей ошибки без исходных OAuth-параметров."""

    location = urlsplit(response.headers["location"])
    assert response.status_code == 303
    assert location.path == "/join/error"
    assert parse_qs(location.query) == {
        "status": ["error"],
        "error": [expected_code],
    }
    assert "code=" not in response.headers["location"]
    assert "state=" not in response.headers["location"]
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_join_info_uses_exact_lab_key(join_client):
    response = join_client.get("/join/os-2026/01")

    assert response.status_code == 200
    assert response.json() == {
        "course_id": "os-2026",
        "course_name": "Operating Systems",
        "lab_id": "01",
        "lab_name": "ЛР0.1",
    }
    assert join_client.get("/join/os-2026/1").status_code == 404


def test_join_info_reports_missing_template(join_client, join_course):
    del join_course["labs"]["01"]["template-repo"]

    response = join_client.get("/join/os-2026/01")

    assert response.status_code == 409
    assert "репозиторий-шаблон" in response.json()["detail"]


def test_join_info_rejects_malformed_template(join_client, join_course):
    join_course["labs"]["01"]["template-repo"] = "teacher-org/template.git"

    response = join_client.get("/join/os-2026/01")

    assert response.status_code == 409
    assert "owner/repo" in response.json()["detail"]


def test_join_config_accepts_numeric_prefix_from_existing_courses(join_client, join_course):
    join_course["labs"]["01"]["github-prefix"] = 1

    config = main.get_join_lab_config("os-2026", "01")

    assert config.github_prefix == "1"


def test_start_redirect_contains_signed_state(join_client):
    response = join_client.get("/join/os-2026/01/start", follow_redirects=False)
    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)

    assert response.status_code == 302
    assert f"{location.scheme}://{location.netloc}{location.path}" == main.GitHubOAuthClient.AUTHORIZE_URL
    assert query["scope"] == ["read:user"]
    assert query["redirect_uri"] == ["https://grader.example/api/v1/join/callback"]
    state_payload = main.oauth_state_serializer.loads(query["state"][0], max_age=600)
    assert state_payload["course_id"] == "os-2026"
    assert state_payload["lab_id"] == "01"
    assert len(state_payload["nonce"]) >= 32
    assert response.cookies[main.OAUTH_NONCE_COOKIE_NAME] == state_payload["nonce"]
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


def test_start_reports_missing_oauth_configuration(join_client, monkeypatch):
    monkeypatch.setattr(main, "GITHUB_OAUTH_CLIENT_SECRET", None)

    response = join_client.get("/join/os-2026/01/start", follow_redirects=False)

    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [
        "oauth_not_configured"
    ]


def test_tampered_state_redirects_to_frontend_error(join_client):
    response = join_client.get(
        "/join/callback",
        params={
            "code": "sensitive-temporary-code",
            "state": f"{signed_state(join_client)}tampered",
        },
        follow_redirects=False,
    )

    assert_oauth_state_error(response, "oauth_state_invalid")


def test_missing_state_redirects_to_frontend_error(join_client):
    response = join_client.get(
        "/join/callback",
        params={"code": "sensitive-temporary-code"},
        follow_redirects=False,
    )

    assert_oauth_state_error(response, "oauth_state_missing")


def test_state_without_browser_nonce_is_rejected(join_client):
    state = signed_state(join_client, set_cookie=False)

    response = join_client.get(
        "/join/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert_oauth_state_error(response, "oauth_state_mismatch")


def test_signed_state_with_invalid_payload_is_rejected(join_client):
    nonce = "test-browser-nonce-with-sufficient-entropy"
    join_client.cookies.set(main.OAUTH_NONCE_COOKIE_NAME, nonce)
    state = main.oauth_state_serializer.dumps(
        {"course_id": "os-2026", "nonce": nonce}
    )

    response = join_client.get(
        "/join/callback",
        params={"code": "temporary-code", "state": state},
        follow_redirects=False,
    )

    assert_oauth_state_error(response, "oauth_state_invalid")


def test_missing_code_redirects_to_retryable_error(join_client):
    response = join_client.get(
        "/join/callback",
        params={"state": signed_state(join_client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [
        "oauth_failed"
    ]


def test_expired_state_redirects_to_frontend_error(join_client):
    with freeze_time("2026-01-01 12:00:00"):
        state = signed_state(join_client)
    with freeze_time("2026-01-01 12:11:00"):
        response = join_client.get(
            "/join/callback",
            params={"code": "code", "state": state},
            follow_redirects=False,
        )

    assert_oauth_state_error(response, "oauth_state_expired")


def test_replayed_callback_is_rejected_after_nonce_cookie_is_deleted(join_client):
    state = signed_state(join_client)
    first_response = join_client.get(
        "/join/callback",
        params={"state": state},
        follow_redirects=False,
    )
    assert first_response.status_code == 303
    assert "oauth_failed" in first_response.headers["location"]
    assert "max-age=0" in first_response.headers["set-cookie"].lower()

    # TestClient сохраняет вручную установленную cookie иначе, чем браузер.
    # Удаляем её явно после проверки Set-Cookie, воспроизводя повторный callback.
    join_client.cookies.delete(main.OAUTH_NONCE_COOKIE_NAME)
    replay_response = join_client.get(
        "/join/callback",
        params={"code": "temporary-code", "state": state},
        follow_redirects=False,
    )

    assert_oauth_state_error(replay_response, "oauth_state_mismatch")


def test_access_denied_redirects_to_retryable_frontend_error(join_client):
    response = join_client.get(
        "/join/callback",
        params={"error": "access_denied", "state": signed_state(join_client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://grader.example/join/os-2026/01?")
    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "status": ["error"],
        "error": ["oauth_denied"],
    }
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_successful_callback_redirects_to_repository(join_client, monkeypatch):
    monkeypatch.setattr(
        main.GitHubOAuthClient,
        "get_verified_username",
        lambda self, code: "VerifiedStudent",
    )
    provision_arguments = {}

    def successful_provision(self, **kwargs):
        provision_arguments.update(kwargs)
        return ProvisionResult(
            organization="test-org",
            repository="os-task-I-1-VerifiedStudent",
            repository_url="https://github.com/test-org/os-task-I-1-VerifiedStudent",
            created=True,
            access_action="invited",
        )

    monkeypatch.setattr(main.RepositoryProvisioner, "provision", successful_provision)

    response = join_client.get(
        "/join/callback",
        params={"code": "temporary-code", "state": signed_state(join_client)},
        follow_redirects=False,
    )
    query = parse_qs(urlsplit(response.headers["location"]).query)

    assert response.status_code == 303
    assert query["status"] == ["success"]
    assert query["repository"] == [
        "https://github.com/test-org/os-task-I-1-VerifiedStudent"
    ]
    assert provision_arguments == {
        "organization": "test-org",
        "github_prefix": "os-task-I-1",
        "template_owner": "teacher-org",
        "template_repository": "os-template",
        "join_key": "VerifiedStudent",
    }


@responses.activate
def test_complete_callback_uses_separate_student_and_server_tokens(join_client, monkeypatch):
    monkeypatch.setattr(main, "GITHUB_TOKEN", "server-organization-token")
    responses.add(
        responses.POST,
        main.GitHubOAuthClient.ACCESS_TOKEN_URL,
        json={"access_token": "student-identity-token"},
        status=200,
    )
    responses.add(
        responses.GET,
        main.GitHubOAuthClient.CURRENT_USER_URL,
        json={"login": "VerifiedStudent"},
        status=200,
    )
    repository_api = GitHubRepositoryClient.BASE_URL
    target = "test-org/os-task-I-1-VerifiedStudent"
    responses.add(responses.GET, f"{repository_api}/repos/{target}", status=404)
    responses.add(
        responses.POST,
        f"{repository_api}/repos/teacher-org/os-template/generate",
        status=201,
    )
    responses.add(
        responses.GET,
        f"{repository_api}/repos/{target}/collaborators",
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        f"{repository_api}/repos/{target}/invitations",
        json=[],
        status=200,
    )
    responses.add(
        responses.PUT,
        f"{repository_api}/repos/{target}/collaborators/VerifiedStudent",
        status=201,
    )

    response = join_client.get(
        "/join/callback",
        params={"code": "temporary-code", "state": signed_state(join_client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "status=success" in response.headers["location"]
    assert responses.calls[1].request.headers["Authorization"] == "Bearer student-identity-token"
    for call in responses.calls[2:]:
        assert call.request.headers["Authorization"] == "Bearer server-organization-token"


def test_oauth_failure_is_mapped_to_safe_code(join_client, monkeypatch):
    def fail_oauth(self, code):
        raise GitHubOAuthError("oauth_unavailable", "technical detail")

    monkeypatch.setattr(main.GitHubOAuthClient, "get_verified_username", fail_oauth)
    response = join_client.get(
        "/join/callback",
        params={"code": "temporary-code", "state": signed_state(join_client)},
        follow_redirects=False,
    )

    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [
        "oauth_unavailable"
    ]
    assert "technical detail" not in response.headers["location"]


def test_repository_failure_is_mapped_to_safe_code(join_client, monkeypatch):
    monkeypatch.setattr(
        main.GitHubOAuthClient,
        "get_verified_username",
        lambda self, code: "VerifiedStudent",
    )

    def fail_provision(self, **kwargs):
        raise RepositoryProvisionError(
            "github_rate_limit",
            "GitHub response with internal details",
            403,
        )

    monkeypatch.setattr(main.RepositoryProvisioner, "provision", fail_provision)
    response = join_client.get(
        "/join/callback",
        params={"code": "temporary-code", "state": signed_state(join_client)},
        follow_redirects=False,
    )

    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [
        "github_rate_limit"
    ]
    assert "internal" not in response.headers["location"]
