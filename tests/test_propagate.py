"""
Tests for grading/propagate.py (propagate template updates via fork PRs,
issue #52). Follows the mocking style of test_repo_provisioning.py:
GitHubClient against `responses`, time.sleep patched out.
"""
import sys
import os
from unittest.mock import patch

import pytest
import requests
import responses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading import GitHubClient
from grading.propagate import (
    TEMPLATE_UPDATE_BRANCH,
    PropagateJob,
    PropagateSetupError,
    dry_run_propagation,
    run_propagation,
    try_start_propagate_job,
    get_propagate_job,
    _jobs,
    _running_lab_keys,
)


ORG = "test-org"
GITHUB_PREFIX = "os-task1"
TEMPLATE_OWNER = "test-org"
TEMPLATE_NAME = "os-task1-template"
TEMPLATE_REPO = f"{TEMPLATE_OWNER}/{TEMPLATE_NAME}"


def make_client():
    return GitHubClient("test_token")


@pytest.fixture(autouse=True)
def no_sleep():
    """run_propagation pauses between PR creations - don't actually wait."""
    with patch("grading.propagate.time.sleep") as mock_sleep:
        yield mock_sleep


@pytest.fixture(autouse=True)
def clean_job_store():
    """Job store is module-level global state - reset it around every test."""
    _jobs.clear()
    _running_lab_keys.clear()
    yield
    _jobs.clear()
    _running_lab_keys.clear()


TEMPLATE_HEAD_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def add_branch_creation_mock(fork_name, status=201, json_body=None):
    """POST /git/refs - placing TEMPLATE_UPDATE_BRANCH into a student fork."""
    return responses.add(
        responses.POST,
        f"https://api.github.com/repos/{ORG}/{fork_name}/git/refs",
        json=json_body if json_body is not None else {"ref": f"refs/heads/{TEMPLATE_UPDATE_BRANCH}"},
        status=status,
    )


def add_branch_update_mock(fork_name, status=200):
    """PATCH /git/refs/heads/... - moving an existing service branch."""
    return responses.add(
        responses.PATCH,
        f"https://api.github.com/repos/{ORG}/{fork_name}/git/refs/heads/{TEMPLATE_UPDATE_BRANCH}",
        json={"ref": f"refs/heads/{TEMPLATE_UPDATE_BRANCH}"},
        status=status,
    )


def add_template_and_forks(forks_pages, org_repos=None, branch_mocks=True):
    responses.add(
        responses.GET,
        f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}",
        json={"default_branch": "main"},
        status=200,
    )
    # The tip commit of the template's default branch - it is what gets placed
    # into each fork as TEMPLATE_UPDATE_BRANCH.
    responses.add(
        responses.GET,
        f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}/git/ref/heads/main",
        json={"object": {"sha": TEMPLATE_HEAD_SHA}},
        status=200,
    )
    for page in forks_pages:
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}/forks",
            json=page,
            status=200,
        )
    responses.add(
        responses.GET,
        f"https://api.github.com/orgs/{ORG}/repos",
        json=org_repos or [],
        status=200,
    )
    if branch_mocks:
        seen = set()
        for page in forks_pages:
            for fork in page:
                name = fork.get("name")
                if name and name not in seen:
                    seen.add(name)
                    add_branch_creation_mock(name)


class TestListTargetForksFiltering:
    """Filtering rules shared by dry_run_propagation and run_propagation."""

    @responses.activate
    def test_filters_by_owner_and_prefix(self):
        forks = [
            {"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"},
            # Wrong owner - forked outside the course org, must not be targeted.
            {"name": "os-task1-student2", "owner": {"login": "someone-else"}, "default_branch": "main"},
            # Right owner, wrong lab's prefix.
            {"name": "os-task2-student1", "owner": {"login": ORG}, "default_branch": "main"},
        ]
        add_template_and_forks([forks])

        summary = dry_run_propagation(make_client(), ORG, GITHUB_PREFIX, TEMPLATE_OWNER, TEMPLATE_NAME)

        assert summary["total"] == 1
        repos = {r["repo"] for r in summary["results"] if r["status"] == "will_process"}
        assert repos == {"os-task1-student1"}

    @responses.activate
    def test_template_itself_is_not_reported_as_not_a_fork(self):
        """The template usually sits in the same org and matches the lab prefix
        (github-prefix: os-task1 + os-task1-template) - listing it as a repo
        left out of the update is a false alarm."""
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        org_repos = [{"name": "os-task1-student1"}, {"name": TEMPLATE_NAME}]
        add_template_and_forks([forks], org_repos=org_repos)

        summary = dry_run_propagation(make_client(), ORG, GITHUB_PREFIX, TEMPLATE_OWNER, TEMPLATE_NAME)

        assert summary["not_a_fork_count"] == 0
        assert TEMPLATE_NAME not in {r["repo"] for r in summary["results"]}

    @responses.activate
    def test_owner_match_is_case_insensitive(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG.upper()}, "default_branch": "main"}]
        add_template_and_forks([forks])

        summary = dry_run_propagation(make_client(), ORG, GITHUB_PREFIX, TEMPLATE_OWNER, TEMPLATE_NAME)
        assert summary["total"] == 1

    @responses.activate
    def test_not_a_fork_repo_is_reported_separately(self):
        """A repo with the right prefix that isn't a fork of the template
        (created via `generate`, or from GitHub Classroom days) must show up
        as not_a_fork, not silently vanish."""
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        org_repos = [
            {"name": "os-task1-student1"},
            {"name": "os-task1-student2"},  # not in the fork list
            {"name": "os-task2-student3"},  # different lab's prefix, ignored entirely
        ]
        add_template_and_forks([forks], org_repos=org_repos)

        summary = dry_run_propagation(make_client(), ORG, GITHUB_PREFIX, TEMPLATE_OWNER, TEMPLATE_NAME)

        assert summary["total"] == 1
        assert summary["not_a_fork_count"] == 1
        not_a_fork = [r for r in summary["results"] if r["status"] == "not_a_fork"]
        assert [r["repo"] for r in not_a_fork] == ["os-task1-student2"]

    @responses.activate
    def test_pagination_collects_forks_across_pages(self):
        page1 = [
            {"name": f"os-task1-student{i}", "owner": {"login": ORG}, "default_branch": "main"}
            for i in range(100)
        ]
        page2 = [{"name": "os-task1-student100", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([page1, page2])

        summary = dry_run_propagation(make_client(), ORG, GITHUB_PREFIX, TEMPLATE_OWNER, TEMPLATE_NAME)
        assert summary["total"] == 101


class TestDryRun:
    @responses.activate
    def test_dry_run_never_calls_create_pull_request(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([forks])
        pr_call = responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"html_url": "should-not-be-called"},
            status=201,
        )

        dry_run_propagation(make_client(), ORG, GITHUB_PREFIX, TEMPLATE_OWNER, TEMPLATE_NAME)

        assert pr_call.call_count == 0

    @responses.activate
    def test_template_not_found_raises_setup_error(self):
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}",
            json={"message": "Not Found"},
            status=404,
        )
        with pytest.raises(PropagateSetupError):
            dry_run_propagation(make_client(), ORG, GITHUB_PREFIX, TEMPLATE_OWNER, TEMPLATE_NAME)

    @responses.activate
    def test_forks_list_failure_raises_setup_error(self):
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}",
            json={"default_branch": "main"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}/git/ref/heads/main",
            json={"object": {"sha": TEMPLATE_HEAD_SHA}},
            status=200,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}/forks",
            json={"message": "Not Found"},
            status=404,
        )
        with pytest.raises(PropagateSetupError):
            dry_run_propagation(make_client(), ORG, GITHUB_PREFIX, TEMPLATE_OWNER, TEMPLATE_NAME)


def make_job():
    return PropagateJob(job_id="job1", course_id="test-course", lab_id="1")


class TestCreatePullRequestResponseTable:
    """The response-parsing table from issue #52, exercised via run_propagation."""

    @responses.activate
    def test_201_is_pr_created(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([forks])
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"html_url": "https://github.com/test-org/os-task1-student1/pull/1"},
            status=201,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        assert job.status == "done"
        assert job.processed == 1
        [result] = [r for r in job.results if r.repo == "os-task1-student1"]
        assert result.status == "pr_created"
        assert result.pr_url == "https://github.com/test-org/os-task1-student1/pull/1"

    @responses.activate
    def test_no_commits_between_is_up_to_date(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([forks])
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            # Real shape: the discriminating text is in errors[], not in `message`.
            json={
                "message": "Validation Failed",
                "errors": [{"message": "No commits between main and main"}],
            },
            status=422,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        [result] = job.results
        assert result.status == "up_to_date"

    @responses.activate
    def test_pr_already_exists_looks_up_url(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([forks])
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={
                "message": "Validation Failed",
                "errors": [
                    {"message": f"A pull request already exists for {ORG}:{TEMPLATE_UPDATE_BRANCH}."}
                ],
            },
            status=422,
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json=[{"html_url": "https://github.com/test-org/os-task1-student1/pull/7"}],
            status=200,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        [result] = job.results
        assert result.status == "pr_exists"
        assert result.pr_url == "https://github.com/test-org/os-task1-student1/pull/7"

    @responses.activate
    def test_rate_limit_is_retried_once_then_succeeds(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([forks])
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"message": "secondary rate limit"},
            status=403,
            headers={"Retry-After": "1"},
        )
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"html_url": "https://github.com/test-org/os-task1-student1/pull/1"},
            status=201,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        [result] = job.results
        assert result.status == "pr_created"

    @responses.activate
    def test_rate_limit_still_failing_after_retry_is_error(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([forks])
        for _ in range(2):
            responses.add(
                responses.POST,
                f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
                json={"message": "secondary rate limit"},
                status=403,
                headers={"Retry-After": "1"},
            )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        [result] = job.results
        assert result.status == "error"

    @responses.activate
    def test_other_error_is_reported_and_does_not_abort_job(self):
        """One repo failing must not stop the rest of the run (issue #52)."""
        forks = [
            {"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"},
            {"name": "os-task1-student2", "owner": {"login": ORG}, "default_branch": "main"},
        ]
        add_template_and_forks([forks])
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"message": "Validation Failed"},
            status=422,
        )
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student2/pulls",
            json={"html_url": "https://github.com/test-org/os-task1-student2/pull/2"},
            status=201,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        assert job.status == "done"
        assert job.processed == 2
        statuses = {r.repo: r.status for r in job.results}
        assert statuses["os-task1-student1"] == "error"
        assert statuses["os-task1-student2"] == "pr_created"

    @responses.activate
    def test_pr_is_opened_from_the_service_branch_inside_the_fork(self):
        """A cross-repo head ("owner:branch") silently resolves to the base repo
        when template and forks share an owner, so the template's commit is
        placed as a branch in the fork and the PR is a plain same-repo one
        (issue #52, verified against live GitHub)."""
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "dev"}]
        add_template_and_forks([forks], branch_mocks=False)
        branch_call = add_branch_creation_mock("os-task1-student1")
        pr_call = responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"html_url": "url"},
            status=201,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        import json as jsonlib
        branch_body = jsonlib.loads(branch_call.calls[0].request.body)
        assert branch_body["ref"] == f"refs/heads/{TEMPLATE_UPDATE_BRANCH}"
        assert branch_body["sha"] == TEMPLATE_HEAD_SHA

        pr_body = jsonlib.loads(pr_call.calls[0].request.body)
        assert pr_body["head"] == TEMPLATE_UPDATE_BRANCH  # bare branch, no owner prefix
        assert pr_body["base"] == "dev"

    @responses.activate
    def test_existing_service_branch_is_force_moved(self):
        """A re-run finds the branch already there; moving it updates the open
        PR instead of leaving it pointing at the previous template commit."""
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([forks], branch_mocks=False)
        add_branch_creation_mock(
            "os-task1-student1",
            status=422,
            json_body={"message": "Reference already exists"},
        )
        update_call = add_branch_update_mock("os-task1-student1")
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"html_url": "url"},
            status=201,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        import json as jsonlib
        body = jsonlib.loads(update_call.calls[0].request.body)
        assert body["sha"] == TEMPLATE_HEAD_SHA
        assert body["force"] is True
        assert [r.status for r in job.results] == ["pr_created"]

    @responses.activate
    def test_branch_placement_failure_is_a_per_repo_error(self):
        """A repo where the commit can't be placed (e.g. not really a fork, so
        the object is unknown) is an error for that repo only."""
        forks = [
            {"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"},
            {"name": "os-task1-student2", "owner": {"login": ORG}, "default_branch": "main"},
        ]
        add_template_and_forks([forks], branch_mocks=False)
        add_branch_creation_mock(
            "os-task1-student1",
            status=422,
            json_body={"message": "Object does not exist"},
        )
        add_branch_creation_mock("os-task1-student2")
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student2/pulls",
            json={"html_url": "url"},
            status=201,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        assert job.status == "done"
        statuses = {r.repo: r.status for r in job.results}
        assert statuses["os-task1-student1"] == "error"
        assert statuses["os-task1-student2"] == "pr_created"


class TestRunPropagationSetupFailure:
    @responses.activate
    def test_template_unreadable_fails_whole_job(self):
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{TEMPLATE_OWNER}/{TEMPLATE_NAME}",
            json={"message": "Not Found"},
            status=404,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        assert job.status == "failed"
        assert job.error

    def test_invalid_template_repo_format_fails_job(self):
        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, "not-a-valid-format")
        assert job.status == "failed"

    @responses.activate
    def test_not_a_fork_repos_are_included_in_results_without_being_processed(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        org_repos = [{"name": "os-task1-student1"}, {"name": "os-task1-student2"}]
        add_template_and_forks([forks], org_repos=org_repos)
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"html_url": "url"},
            status=201,
        )

        job = make_job()
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        assert job.total == 1  # not_a_fork doesn't count toward total/processed
        assert job.processed == 1
        statuses = {r.repo: r.status for r in job.results}
        assert statuses["os-task1-student2"] == "not_a_fork"
        assert statuses["os-task1-student1"] == "pr_created"


    def test_unexpected_exception_fails_job_and_unlocks_the_lab(self):
        """A bare requests timeout isn't a PropagateSetupError - if it escaped,
        the job would stay "running" forever and every later run for that lab
        would 409 until the backend restarts."""
        job = try_start_propagate_job("test-course", "1")
        assert job is not None

        with patch("grading.propagate._list_target_forks", side_effect=requests.ConnectionError("boom")):
            run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        assert job.status == "failed"
        assert job.error
        assert job.finished_at
        # The lab is free again: a retry must not be rejected with 409.
        assert try_start_propagate_job("test-course", "1") is not None


class TestJobStore:
    def test_unknown_job_id_returns_none(self):
        assert get_propagate_job("does-not-exist") is None

    def test_try_start_returns_job_and_registers_it(self):
        job = try_start_propagate_job("test-course", "1")
        assert job is not None
        assert get_propagate_job(job.job_id) is job

    def test_second_start_for_same_lab_while_running_returns_none(self):
        job = try_start_propagate_job("test-course", "1")
        assert job is not None
        assert try_start_propagate_job("test-course", "1") is None

    def test_different_lab_can_start_concurrently(self):
        assert try_start_propagate_job("test-course", "1") is not None
        assert try_start_propagate_job("test-course", "2") is not None

    @responses.activate
    def test_finishing_a_job_allows_restarting_same_lab(self):
        forks = [{"name": "os-task1-student1", "owner": {"login": ORG}, "default_branch": "main"}]
        add_template_and_forks([forks])
        responses.add(
            responses.POST,
            f"https://api.github.com/repos/{ORG}/os-task1-student1/pulls",
            json={"html_url": "url"},
            status=201,
        )

        job = try_start_propagate_job("test-course", "1")
        run_propagation(job, make_client(), ORG, GITHUB_PREFIX, TEMPLATE_REPO)

        assert try_start_propagate_job("test-course", "1") is not None

    def test_job_to_dict_matches_expected_shape(self):
        job = make_job()
        data = job.to_dict()
        assert set(data.keys()) == {
            "job_id", "course_id", "lab_id", "status", "started_at",
            "finished_at", "total", "processed", "results", "error",
        }
