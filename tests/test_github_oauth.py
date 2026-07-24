"""Тесты изолированного серверного GitHub OAuth-клиента."""

from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import responses

from grading.github_oauth import (
    GitHubOAuthClient,
    GitHubOAuthConfig,
    GitHubOAuthError,
)


@pytest.fixture
def oauth_client():
    return GitHubOAuthClient(
        GitHubOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            callback_url="https://grader.example/api/v1/join/callback",
        )
    )


def test_authorization_url_contains_required_identity_parameters(oauth_client):
    url = urlsplit(oauth_client.build_authorization_url("signed-state"))
    query = parse_qs(url.query)

    assert f"{url.scheme}://{url.netloc}{url.path}" == GitHubOAuthClient.AUTHORIZE_URL
    assert query == {
        "client_id": ["client-id"],
        "redirect_uri": ["https://grader.example/api/v1/join/callback"],
        "scope": ["read:user"],
        "state": ["signed-state"],
    }


@responses.activate
def test_verified_username_uses_student_token_only_for_current_user(oauth_client):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        json={"access_token": "student-secret-token", "token_type": "bearer"},
        status=200,
    )
    responses.add(
        responses.GET,
        GitHubOAuthClient.CURRENT_USER_URL,
        json={"login": "VerifiedStudent"},
        status=200,
    )

    assert oauth_client.get_verified_username("temporary-code") == "VerifiedStudent"
    assert "student-secret-token" not in responses.calls[0].request.body
    assert responses.calls[1].request.headers["Authorization"] == "Bearer student-secret-token"


@responses.activate
def test_oauth_json_error_is_reported_without_exposing_secrets(oauth_client):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        json={"error": "bad_verification_code"},
        status=200,
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("invalid-code")

    assert error.value.code == "oauth_failed"
    assert "client-secret" not in error.value.log_message
    assert "invalid-code" not in error.value.log_message


@responses.activate
def test_missing_access_token_is_rejected(oauth_client):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        json={"token_type": "bearer"},
        status=200,
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("temporary-code")

    assert error.value.code == "oauth_failed"


@responses.activate
def test_non_json_token_response_is_retryable(oauth_client):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        body="temporarily unavailable",
        status=502,
        content_type="text/plain",
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("temporary-code")

    assert error.value.code == "oauth_unavailable"


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "oauth_failed"),
        (403, "oauth_failed"),
        (429, "oauth_unavailable"),
        (500, "oauth_unavailable"),
        (502, "oauth_unavailable"),
        (503, "oauth_unavailable"),
    ],
)
@responses.activate
def test_token_exchange_classifies_http_errors(
    oauth_client,
    status_code,
    expected_code,
):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        json={"error": "github-error"},
        status=status_code,
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("temporary-code")

    assert error.value.code == expected_code


@responses.activate
def test_missing_login_is_rejected(oauth_client):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        json={"access_token": "student-token"},
        status=200,
    )
    responses.add(
        responses.GET,
        GitHubOAuthClient.CURRENT_USER_URL,
        json={"id": 123},
        status=200,
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("temporary-code")

    assert error.value.code == "oauth_failed"


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "oauth_failed"),
        (403, "oauth_failed"),
        (429, "oauth_unavailable"),
        (500, "oauth_unavailable"),
        (502, "oauth_unavailable"),
        (503, "oauth_unavailable"),
    ],
)
@responses.activate
def test_current_user_classifies_http_errors(
    oauth_client,
    status_code,
    expected_code,
):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        json={"access_token": "student-token"},
        status=200,
    )
    responses.add(
        responses.GET,
        GitHubOAuthClient.CURRENT_USER_URL,
        json={"message": "github-error"},
        status=status_code,
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("temporary-code")

    assert error.value.code == expected_code


@responses.activate
def test_current_user_rejects_non_json_response(oauth_client):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        json={"access_token": "student-token"},
        status=200,
    )
    responses.add(
        responses.GET,
        GitHubOAuthClient.CURRENT_USER_URL,
        body="not-json",
        status=200,
        content_type="text/plain",
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("temporary-code")

    assert error.value.code == "oauth_failed"


@responses.activate
def test_unexpected_token_payload_cannot_escape_as_server_error(oauth_client):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        json=["unexpected", "payload"],
        status=200,
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("temporary-code")

    assert error.value.code == "oauth_unavailable"


@pytest.mark.parametrize(
    "network_error",
    [
        requests.Timeout("timeout"),
        requests.ConnectionError("offline"),
    ],
)
@responses.activate
def test_network_failure_has_retryable_public_code(oauth_client, network_error):
    responses.add(
        responses.POST,
        GitHubOAuthClient.ACCESS_TOKEN_URL,
        body=network_error,
    )

    with pytest.raises(GitHubOAuthError) as error:
        oauth_client.get_verified_username("temporary-code")

    assert error.value.code == "oauth_unavailable"
