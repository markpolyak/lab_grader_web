"""Тесты всех ветвей создания репозитория и обработки приглашений."""

import pytest
import requests
import responses

from grading.repository_provisioner import (
    GitHubRepositoryClient,
    RepositoryProvisionError,
    RepositoryProvisioner,
)


API = GitHubRepositoryClient.BASE_URL
TARGET = "test-org/os-task1-StudentName"
DIRECT_COLLABORATORS_URL = f"{API}/repos/{TARGET}/collaborators"


@pytest.fixture
def provisioner():
    return RepositoryProvisioner(GitHubRepositoryClient("server-token"))


def provision(provisioner):
    return provisioner.provision(
        organization="test-org",
        github_prefix="os-task1",
        template_owner="teacher-org",
        template_repository="lab-template",
        join_key="StudentName",
    )


@responses.activate
def test_creates_private_repository_and_first_invitation(provisioner):
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=404)
    responses.add(
        responses.POST,
        f"{API}/repos/teacher-org/lab-template/generate",
        json={"full_name": "test-org/os-task1-StudentName"},
        status=201,
    )
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API}/repos/test-org/os-task1-StudentName/invitations",
        json=[],
        status=200,
    )
    responses.add(
        responses.PUT,
        f"{API}/repos/test-org/os-task1-StudentName/collaborators/StudentName",
        json={"id": 1},
        status=201,
    )

    result = provision(provisioner)

    assert result.created is True
    assert result.access_action == "invited"
    assert result.repository_url == "https://github.com/test-org/os-task1-StudentName"
    create_body = responses.calls[1].request.body.decode()
    assert '"private": true' in create_body
    assert responses.calls[1].request.headers["Authorization"] == "Bearer server-token"


@responses.activate
def test_existing_repository_and_access_are_left_untouched(provisioner):
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=200)
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json=[{"login": "studentname"}],
        status=200,
    )

    result = provision(provisioner)

    assert result.created is False
    assert result.access_action == "already_has_access"
    assert [call.request.method for call in responses.calls] == ["GET", "GET"]
    assert "affiliation=direct" in responses.calls[1].request.url


@responses.activate
def test_direct_collaborator_lookup_follows_pagination(provisioner):
    responses.add(responses.GET, f"{API}/repos/{TARGET}", status=200)
    first_page = [{"login": f"other-{index}"} for index in range(100)]
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json=first_page,
        status=200,
    )
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json=[{"login": "studentname"}],
        status=200,
    )

    result = provision(provisioner)

    assert result.access_action == "already_has_access"
    collaborator_calls = [
        call
        for call in responses.calls
        if "/collaborators" in call.request.url and call.request.method == "GET"
    ]
    assert len(collaborator_calls) == 2
    assert "affiliation=direct" in collaborator_calls[0].request.url
    assert "page=2" in collaborator_calls[1].request.url


@responses.activate
def test_pending_invitation_is_deleted_and_sent_again(provisioner):
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=200)
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json=[],
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API}/repos/test-org/os-task1-StudentName/invitations",
        json=[{"id": 77, "invitee": {"login": "studentname"}}],
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{API}/repos/test-org/os-task1-StudentName/invitations/77",
        status=204,
    )
    responses.add(
        responses.PUT,
        f"{API}/repos/test-org/os-task1-StudentName/collaborators/StudentName",
        status=201,
    )

    result = provision(provisioner)

    assert result.access_action == "reinvited"
    assert [call.request.method for call in responses.calls][-2:] == ["DELETE", "PUT"]


@responses.activate
def test_invitation_lookup_follows_pagination(provisioner):
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=200)
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json=[],
        status=200,
    )
    first_page = [
        {"id": invitation_id, "invitee": {"login": f"other-{invitation_id}"}}
        for invitation_id in range(100)
    ]
    responses.add(
        responses.GET,
        f"{API}/repos/test-org/os-task1-StudentName/invitations",
        json=first_page,
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API}/repos/test-org/os-task1-StudentName/invitations",
        json=[{"id": 701, "invitee": {"login": "studentname"}}],
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{API}/repos/test-org/os-task1-StudentName/invitations/701",
        status=204,
    )
    responses.add(
        responses.PUT,
        f"{API}/repos/test-org/os-task1-StudentName/collaborators/StudentName",
        status=204,
    )

    result = provision(provisioner)

    assert result.access_action == "reinvited"
    invitation_calls = [
        call for call in responses.calls if "/invitations" in call.request.url
        and call.request.method == "GET"
    ]
    assert len(invitation_calls) == 2
    assert "page=2" in invitation_calls[1].request.url


@responses.activate
def test_creation_race_is_success_when_repository_appears(provisioner):
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=404)
    responses.add(
        responses.POST,
        f"{API}/repos/teacher-org/lab-template/generate",
        json={"message": "name already exists"},
        status=422,
    )
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=200)
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json=[{"login": "StudentName"}],
        status=200,
    )

    result = provision(provisioner)

    assert result.created is False
    assert result.access_action == "already_has_access"


@responses.activate
def test_creation_validation_error_is_not_mistaken_for_race(provisioner):
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=404)
    responses.add(
        responses.POST,
        f"{API}/repos/teacher-org/lab-template/generate",
        json={"message": "Validation Failed"},
        status=422,
    )
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=404)

    with pytest.raises(RepositoryProvisionError) as error:
        provision(provisioner)

    assert error.value.code == "repository_create_failed"


@responses.activate
def test_disappearing_invitation_rechecks_access_before_reinviting(provisioner):
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=200)
    responses.add(responses.GET, DIRECT_COLLABORATORS_URL, json=[], status=200)
    responses.add(
        responses.GET,
        f"{API}/repos/test-org/os-task1-StudentName/invitations",
        json=[{"id": 91, "invitee": {"login": "StudentName"}}],
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{API}/repos/test-org/os-task1-StudentName/invitations/91",
        status=404,
    )
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json=[{"login": "StudentName"}],
        status=200,
    )

    result = provision(provisioner)

    assert result.access_action == "already_has_access"
    assert not any(call.request.method == "PUT" for call in responses.calls)


@responses.activate
def test_rate_limit_is_classified_for_frontend_retry_message(provisioner):
    responses.add(
        responses.GET,
        f"{API}/repos/test-org/os-task1-StudentName",
        json={"message": "API rate limit exceeded"},
        status=403,
        headers={"X-RateLimit-Remaining": "0"},
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provision(provisioner)

    assert error.value.code == "github_rate_limit"


@responses.activate
def test_inaccessible_template_has_specific_error_code(provisioner):
    responses.add(responses.GET, f"{API}/repos/test-org/os-task1-StudentName", status=404)
    responses.add(
        responses.POST,
        f"{API}/repos/teacher-org/lab-template/generate",
        json={"message": "Not Found"},
        status=404,
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provision(provisioner)

    assert error.value.code == "template_unavailable"


@pytest.mark.parametrize("status_code", [401, 403, 409, 422, 500, 502, 503])
@responses.activate
def test_repository_lookup_preserves_http_error_status(provisioner, status_code):
    responses.add(
        responses.GET,
        f"{API}/repos/{TARGET}",
        json={"message": "GitHub diagnostic message"},
        status=status_code,
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.repository_exists("test-org", "os-task1-StudentName")

    assert error.value.code == "repository_lookup_failed"
    assert error.value.status_code == status_code
    assert "server-token" not in error.value.log_message
    assert "Authorization" not in error.value.log_message


@responses.activate
def test_http_429_is_classified_as_github_rate_limit(provisioner):
    responses.add(
        responses.GET,
        f"{API}/repos/{TARGET}",
        json={"message": "Too many requests"},
        status=429,
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.repository_exists("test-org", "os-task1-StudentName")

    assert error.value.code == "github_rate_limit"
    assert error.value.status_code == 429


@pytest.mark.parametrize(
    "network_error",
    [
        requests.Timeout("timeout"),
        requests.ConnectionError("offline"),
    ],
)
@responses.activate
def test_repository_network_errors_are_retryable(provisioner, network_error):
    responses.add(
        responses.GET,
        f"{API}/repos/{TARGET}",
        body=network_error,
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.repository_exists("test-org", "os-task1-StudentName")

    assert error.value.code == "github_unavailable"


@responses.activate
def test_invalid_collaborator_json_is_not_treated_as_no_access(provisioner):
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        body="not-json",
        status=200,
        content_type="text/plain",
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.is_direct_collaborator(
            "test-org",
            "os-task1-StudentName",
            "StudentName",
        )

    assert error.value.code == "access_check_failed"


@responses.activate
def test_invalid_invitation_json_is_not_treated_as_missing_invitation(provisioner):
    responses.add(
        responses.GET,
        f"{API}/repos/{TARGET}/invitations",
        body="not-json",
        status=200,
        content_type="text/plain",
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.find_pending_invitation(
            "test-org",
            "os-task1-StudentName",
            "StudentName",
        )

    assert error.value.code == "invitation_lookup_failed"


@responses.activate
def test_collaborator_api_error_is_not_treated_as_no_access(provisioner):
    responses.add(
        responses.GET,
        DIRECT_COLLABORATORS_URL,
        json={"message": "Forbidden"},
        status=403,
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.is_direct_collaborator(
            "test-org",
            "os-task1-StudentName",
            "StudentName",
        )

    assert error.value.code == "access_check_failed"
    assert error.value.status_code == 403


@responses.activate
def test_invitation_api_error_is_not_treated_as_empty_list(provisioner):
    responses.add(
        responses.GET,
        f"{API}/repos/{TARGET}/invitations",
        json={"message": "Service unavailable"},
        status=503,
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.find_pending_invitation(
            "test-org",
            "os-task1-StudentName",
            "StudentName",
        )

    assert error.value.code == "invitation_lookup_failed"
    assert error.value.status_code == 503


@responses.activate
def test_invitation_delete_error_is_classified(provisioner):
    responses.add(
        responses.DELETE,
        f"{API}/repos/{TARGET}/invitations/77",
        json={"message": "Service unavailable"},
        status=502,
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.delete_invitation(
            "test-org",
            "os-task1-StudentName",
            77,
        )

    assert error.value.code == "invitation_delete_failed"
    assert error.value.status_code == 502


@responses.activate
def test_invitation_create_error_is_classified(provisioner):
    responses.add(
        responses.PUT,
        f"{API}/repos/{TARGET}/collaborators/StudentName",
        json={"message": "Validation Failed"},
        status=422,
    )

    with pytest.raises(RepositoryProvisionError) as error:
        provisioner.client.invite_collaborator(
            "test-org",
            "os-task1-StudentName",
            "StudentName",
        )

    assert error.value.code == "invitation_create_failed"
    assert error.value.status_code == 422
