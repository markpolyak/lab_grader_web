"""
Tests for grading.repo_provisioning.RepoProvisioner (student repo creation,
see docs/REPO_GENERATION_PLAN.md §4 and §10).
"""
import sys
import os

import responses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading import GitHubClient, RepoProvisioner, ProvisionStatus


ORG = "test-org"
GITHUB_PREFIX = "os-task1"
TEMPLATE_REPO = "test-org/os-task1-template"
USERNAME = "student1"
REPO_NAME = f"{GITHUB_PREFIX}-{USERNAME}"


def make_provisioner():
    return RepoProvisioner(GitHubClient("test_token"))


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
