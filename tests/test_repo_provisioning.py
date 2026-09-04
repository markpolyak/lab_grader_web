"""
Tests for grading.repo_provisioning.RepoProvisioner (student repo creation,
see docs/REPO_GENERATION_PLAN.md §4 and §10).
"""
import sys
import os
from unittest.mock import patch

import pytest
import responses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading import GitHubClient, RepoProvisioner, ProvisionStatus
from grading.repo_provisioning import FORK_POLL_ATTEMPTS


ORG = "test-org"
GITHUB_PREFIX = "os-task1"
TEMPLATE_REPO = "test-org/os-task1-template"
USERNAME = "student1"
REPO_NAME = f"{GITHUB_PREFIX}-{USERNAME}"


def make_provisioner():
    return RepoProvisioner(GitHubClient("test_token"))


@pytest.fixture(autouse=True)
def no_sleep():
    """Fork-mode polling calls time.sleep between attempts - patch it out so
    these tests don't actually wait (see FORK_POLL_INTERVAL_SECONDS)."""
    with patch("grading.repo_provisioning.time.sleep") as mock_sleep:
        yield mock_sleep


class TestCreateFromTemplate:
    """Repo creation from a template repository."""

    @responses.activate
    def test_creates_repo_when_missing_and_grants_access(self):
        """New repo: created from template, then invited (§10 - успешное создание из шаблона)."""
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=404,
        )
        create_call = responses.add(
            responses.POST,
            f"https://api.github.com/repos/{TEMPLATE_REPO}/generate",
            json={"full_name": f"{ORG}/{REPO_NAME}"},
            status=201,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=404,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/invitations",
            json=[],
            status=200,
        )
        responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=201,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.OK
        assert result.repo_name == REPO_NAME
        assert result.repo_url == f"https://github.com/{ORG}/{REPO_NAME}"
        assert create_call.call_count == 1

    @responses.activate
    def test_existing_repo_is_not_recreated(self):
        """Repeat visit: repo already exists, /generate must not be called (§10)."""
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=200,
        )
        generate_call = responses.add(
            responses.POST,
            f"https://api.github.com/repos/{TEMPLATE_REPO}/generate",
            json={},
            status=201,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=204,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.OK
        assert generate_call.call_count == 0

    @responses.activate
    def test_race_condition_on_create_is_handled(self):
        """Concurrent double-create: /generate 422s, but repo exists on recheck -> no crash."""
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=404,
        )
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{TEMPLATE_REPO}/generate",
            json={"message": "Repository creation failed."},
            status=422,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=200,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=204,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.OK

    @responses.activate
    def test_template_not_found_is_reported_as_error_not_exception(self):
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=404,
        )
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{TEMPLATE_REPO}/generate",
            json={"message": "Not Found"},
            status=404,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "TEMPLATE_NOT_FOUND"

    def test_invalid_template_repo_format_is_reported_as_error(self):
        result = make_provisioner().provision(ORG, GITHUB_PREFIX, "not-a-valid-template", USERNAME)

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "INVALID_TEMPLATE_CONFIG"

    @responses.activate
    def test_secondary_rate_limit_is_reported_as_retryable_not_forbidden(self):
        """A 403 with a rate-limit signature must not be reported as CREATE_FORBIDDEN
        (§7 of the plan lists rate limiting as a retryable GitHub API error)."""
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=404,
        )
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{TEMPLATE_REPO}/generate",
            json={"message": "You have exceeded a secondary rate limit"},
            status=403,
            headers={"Retry-After": "30"},
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "RATE_LIMITED"

    @responses.activate
    def test_plain_403_without_rate_limit_signature_is_forbidden(self):
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=404,
        )
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{TEMPLATE_REPO}/generate",
            json={"message": "Must have admin rights to Repository."},
            status=403,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "CREATE_FORBIDDEN"


class TestAccessRepair:
    """Collaborator access checks/repairs (replaces github-reinvite, §10)."""

    @responses.activate
    def test_direct_collaborator_already_present_is_noop(self):
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=200,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=204,
        )
        put_call = responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=204,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.OK
        assert put_call.call_count == 0

    @responses.activate
    def test_pending_invitation_is_deleted_and_reissued(self):
        """Pending invitation exists: delete + re-invite, both calls made (§10 - починка pending)."""
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=200,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=404,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/invitations",
            json=[{"id": 42, "invitee": {"login": USERNAME}}],
            status=200,
        )
        delete_call = responses.add(
            responses.DELETE,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/invitations/42",
            status=204,
        )
        put_call = responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=201,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.OK
        assert delete_call.call_count == 1
        assert put_call.call_count == 1

    @responses.activate
    def test_no_invitation_creates_new_one(self):
        """No prior invitation: invited for the first time, no delete call (§10)."""
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=200,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=404,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/invitations",
            json=[],
            status=200,
        )
        put_call = responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=201,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.OK
        assert put_call.call_count == 1

    @responses.activate
    def test_invitations_unrelated_to_student_are_ignored(self):
        """Only the invitation matching this student's login should be touched."""
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=200,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=404,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/invitations",
            json=[{"id": 1, "invitee": {"login": "someone-else"}}],
            status=200,
        )
        responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=201,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.OK

    @responses.activate
    def test_invite_failure_is_reported_as_error(self):
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
            status=200,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=404,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/invitations",
            json=[],
            status=200,
        )
        responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=422,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "INVITE_FAILED"


TEMPLATE_OWNER, TEMPLATE_NAME = TEMPLATE_REPO.split("/", 1)


def make_fork_urls():
    return {
        "repo": f"https://api.github.com/repos/{ORG}/{REPO_NAME}",
        "template": f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}",
        "forks": f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}/forks",
        "actions": f"https://api.github.com/repos/{ORG}/{REPO_NAME}/actions/permissions",
        "collaborators": f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
        "invitations": f"https://api.github.com/repos/{ORG}/{REPO_NAME}/invitations",
    }


def add_access_mocks(urls, existing=False):
    """Register the collaborator-access mocks that run after repo creation."""
    if existing:
        responses.add(responses.GET, urls["collaborators"], status=204)
        return
    responses.add(responses.GET, urls["collaborators"], status=404)
    responses.add(responses.GET, urls["invitations"], json=[], status=200)
    responses.add(responses.PUT, urls["collaborators"], status=201)


class TestDefaultModeIsTemplate:
    """`provision()` called without a `mode` argument must behave exactly as before."""

    @responses.activate
    def test_default_mode_does_not_touch_fork_endpoints(self):
        """No fork-only endpoint is mocked - if the code called one, `responses`
        would raise ConnectionError and this test would fail, proving
        enable_actions/update_repo/fork_repo are never invoked in this mode."""
        responses.add(responses.GET, f"https://api.github.com/repos/{ORG}/{REPO_NAME}", status=404)
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{TEMPLATE_REPO}/generate",
            json={},
            status=201,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=404,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/invitations",
            json=[],
            status=200,
        )
        responses.add(
            responses.PUT,
            f"https://api.github.com/repos/{ORG}/{REPO_NAME}/collaborators/{USERNAME}",
            status=201,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME)

        assert result.status == ProvisionStatus.OK


class TestCreateFromFork:
    """Repo creation via mode="fork" (issue #51)."""

    @responses.activate
    def test_creates_fork_when_missing_and_grants_access(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)  # not created yet
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={}, status=202)
        responses.add(responses.GET, urls["repo"], status=200)  # appears on first poll
        responses.add(responses.PUT, urls["actions"], status=204)
        responses.add(responses.PATCH, urls["repo"], status=200)
        add_access_mocks(urls)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.OK
        assert result.repo_name == REPO_NAME
        assert result.repo_url == f"https://github.com/{ORG}/{REPO_NAME}"

    @responses.activate
    def test_existing_fork_with_matching_parent_is_not_recreated(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=200)  # already exists
        responses.add(
            responses.GET, urls["repo"], json={"parent": {"full_name": TEMPLATE_REPO}}, status=200
        )
        actions_call = responses.add(responses.PUT, urls["actions"], status=204)
        responses.add(responses.PATCH, urls["repo"], status=200)
        add_access_mocks(urls, existing=True)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.OK
        # The fork is not re-created, but the repairs still run: an existing fork
        # may come from an attempt that died right after fork_repo, or be one a
        # teacher made by hand, and Actions would stay blocked either way.
        assert actions_call.call_count == 1

    @responses.activate
    def test_existing_fork_gets_actions_enabled_after_earlier_failure(self):
        """A student retrying /join after ACTIONS_ENABLE_FAILED must not get a
        'success' with Actions still blocked - the repo would look fine and
        never run CI (see issue #51 review)."""
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=200)  # created by the failed attempt
        responses.add(
            responses.GET, urls["repo"], json={"parent": {"full_name": TEMPLATE_REPO}}, status=200
        )
        responses.add(responses.PUT, urls["actions"], status=500)  # fails again
        add_access_mocks(urls, existing=True)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "ACTIONS_ENABLE_FAILED"

    @responses.activate
    def test_existing_repo_unreadable_is_not_reported_as_name_taken(self):
        """A GitHub outage while checking the parent is a retryable failure, not
        proof that the name belongs to an unrelated repo."""
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=200)  # exists
        responses.add(responses.GET, urls["repo"], status=502)  # but can't be read

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "FORK_CHECK_FAILED"

    @responses.activate
    def test_fork_call_returning_200_is_treated_as_success(self):
        """GitHub documents 202 for /forks, but any 2xx means the fork was
        accepted - it must not fall through to CREATE_FAILED."""
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={}, status=200)
        responses.add(responses.GET, urls["repo"], status=200)
        responses.add(responses.PUT, urls["actions"], status=204)
        responses.add(responses.PATCH, urls["repo"], status=200)
        add_access_mocks(urls)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.OK

    @responses.activate
    def test_existing_repo_with_foreign_parent_is_reported_as_name_taken(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=200)  # already exists
        responses.add(
            responses.GET, urls["repo"], json={"parent": {"full_name": "someone-else/unrelated"}}, status=200
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "NAME_TAKEN_BY_FOREIGN_REPO"

    @responses.activate
    def test_race_condition_with_matching_parent_continues(self):
        """Concurrent double-fork: /forks 422s, but the repo really is our fork -> success."""
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)  # not created yet
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={"message": "name already exists"}, status=422)
        responses.add(
            responses.GET, urls["repo"], json={"parent": {"full_name": TEMPLATE_REPO}}, status=200
        )
        responses.add(responses.GET, urls["repo"], status=200)  # poll after collision check
        responses.add(responses.PUT, urls["actions"], status=204)
        responses.add(responses.PATCH, urls["repo"], status=200)
        add_access_mocks(urls)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.OK

    @responses.activate
    def test_race_condition_with_foreign_repo_is_reported_as_name_taken(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)  # not created yet
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={"message": "name already exists"}, status=422)
        responses.add(
            responses.GET, urls["repo"], json={"parent": {"full_name": "someone-else/unrelated"}}, status=200
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "NAME_TAKEN_BY_FOREIGN_REPO"

    @responses.activate
    def test_public_template_is_rejected(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"private": False}, status=200)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "TEMPLATE_MUST_BE_PRIVATE"

    @responses.activate
    def test_template_not_found_is_reported_as_error(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"message": "Not Found"}, status=404)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "TEMPLATE_NOT_FOUND"

    @responses.activate
    def test_fork_call_404_is_reported_as_template_not_found(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={"message": "Not Found"}, status=404)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "TEMPLATE_NOT_FOUND"

    @responses.activate
    def test_secondary_rate_limit_is_reported_as_retryable_not_forbidden(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(
            responses.POST,
            urls["forks"],
            json={"message": "You have exceeded a secondary rate limit"},
            status=403,
            headers={"Retry-After": "30"},
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "RATE_LIMITED"

    @responses.activate
    def test_plain_403_without_rate_limit_signature_is_forbidden(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(
            responses.POST,
            urls["forks"],
            json={"message": "Must have admin rights to Repository."},
            status=403,
        )

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "CREATE_FORBIDDEN"

    @responses.activate
    def test_unexpected_status_on_fork_is_reported_as_create_failed(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={"message": "Internal error"}, status=500)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "CREATE_FAILED"

    @responses.activate
    def test_fork_timeout_is_reported_as_error(self, no_sleep):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)  # not created yet
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={}, status=202)
        responses.add(responses.GET, urls["repo"], status=404)  # never appears, repeats for every poll

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "FORK_TIMEOUT"
        # No sleep after the last attempt - the student waits out the poll
        # interval only between checks, not once more before the error.
        assert no_sleep.call_count == FORK_POLL_ATTEMPTS - 1

    @responses.activate
    def test_actions_enable_failure_is_fatal(self):
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={}, status=202)
        responses.add(responses.GET, urls["repo"], status=200)  # appears on first poll
        responses.add(responses.PUT, urls["actions"], json={"message": "error"}, status=500)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.ERROR
        assert result.error_code == "ACTIONS_ENABLE_FAILED"

    @responses.activate
    def test_is_template_clear_failure_is_not_fatal(self):
        """Clearing the inherited template flag is cosmetic - failure must not
        fail an otherwise successfully provisioned repo."""
        urls = make_fork_urls()
        responses.add(responses.GET, urls["repo"], status=404)
        responses.add(responses.GET, urls["template"], json={"private": True}, status=200)
        responses.add(responses.POST, urls["forks"], json={}, status=202)
        responses.add(responses.GET, urls["repo"], status=200)  # appears on first poll
        responses.add(responses.PUT, urls["actions"], status=204)
        responses.add(responses.PATCH, urls["repo"], json={"message": "error"}, status=500)
        add_access_mocks(urls)

        result = make_provisioner().provision(ORG, GITHUB_PREFIX, TEMPLATE_REPO, USERNAME, mode="fork")

        assert result.status == ProvisionStatus.OK
