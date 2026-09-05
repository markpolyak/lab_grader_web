"""
Tests for admin authorization (issue #52 `require_admin`) and the
propagate-template-update / propagate-jobs endpoints in main.py.

Two styles, matching the existing test suite:
- require_admin coverage uses fastapi.testclient.TestClient (real cookie
  handling, real Depends() resolution).
- Endpoint business logic uses direct function calls with a real Request
  object, like test_join_endpoints.py (Depends() isn't resolved when called
  this way, so `admin` is passed explicitly).
"""
import sys
import os
import yaml
from unittest.mock import patch

import pytest
import responses
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.background import BackgroundTasks

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module
from main import app
from grading.propagate import _jobs, _running_lab_keys


@pytest.fixture(autouse=True)
def disable_real_rate_limiting(monkeypatch):
    """conftest's disable_rate_limiting fixture only pre-seeds
    request.state.view_rate_limit for the mock_request fixture's Request
    object - a real TestClient request builds its own Request, which never
    gets that treatment. Flip the limiter off directly instead, for the
    routes hit here through TestClient."""
    monkeypatch.setattr(main_module.limiter, "enabled", False)


@pytest.fixture(autouse=True)
def clean_job_store():
    _jobs.clear()
    _running_lab_keys.clear()
    yield
    _jobs.clear()
    _running_lab_keys.clear()


@pytest.fixture
def client():
    return TestClient(app)


def valid_cookie():
    return main_module.signer.sign(main_module.ADMIN_LOGIN.encode()).decode()


@pytest.fixture
def admin_course_env(tmp_path, monkeypatch):
    """A real courses/ dir + index.yaml under tmp_path, so upload/edit/delete
    endpoints can run to completion without touching the repo's real courses/."""
    courses_dir = tmp_path / "courses"
    courses_dir.mkdir()
    (courses_dir / "test-course.yaml").write_text(
        yaml.dump({"course": {"name": "Test Course"}}, allow_unicode=True), encoding="utf-8"
    )
    index_file = courses_dir / "index.yaml"
    index_file.write_text(
        yaml.dump(
            {"courses": [{"id": "test-course", "file": "test-course.yaml", "status": "active", "priority": 0}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main_module, "COURSES_DIR", str(courses_dir))
    monkeypatch.setattr(main_module, "INDEX_FILE", str(index_file))
    return courses_dir


PROTECTED_ROUTES = [
    ("GET", "/admin/check-auth", {}),
    ("DELETE", "/courses/test-course", {}),
    ("GET", "/courses/test-course/edit", {}),
    ("PUT", "/courses/test-course/edit", {"json": {"content": "course: {}"}}),
    ("GET", "/admin/courses/test-course/labs", {}),
    (
        "POST",
        "/admin/courses/test-course/labs/1/propagate-template-update",
        {"json": {"dry_run": True}},
    ),
    ("GET", "/admin/propagate-jobs/does-not-exist", {}),
]


class TestRequireAdminBlocksUnauthenticated:
    """401 without a session cookie, on every route protected by require_admin."""

    @pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES)
    def test_no_cookie_is_401(self, client, method, path, kwargs):
        response = client.request(method, path, **kwargs)
        assert response.status_code == 401

    @pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES)
    def test_corrupted_cookie_is_401(self, client, method, path, kwargs):
        client.cookies.set("admin_session", "not-a-valid-signed-value")
        response = client.request(method, path, **kwargs)
        assert response.status_code == 401

    def test_upload_without_cookie_is_401(self, client):
        response = client.post(
            "/courses/upload",
            files={"file": ("new-course.yaml", b"course: {}", "application/x-yaml")},
        )
        assert response.status_code == 401


class TestRequireAdminAllowsValidSession:
    """A valid admin_session cookie must let requests reach the real handler."""

    def test_check_auth_returns_200(self, client):
        client.cookies.set("admin_session", valid_cookie())
        response = client.get("/admin/check-auth")
        assert response.status_code == 200
        assert response.json() == {"authenticated": True}

    def test_edit_get_returns_200(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        response = client.get("/courses/test-course/edit")
        assert response.status_code == 200
        assert response.json()["filename"] == "test-course.yaml"

    def test_edit_put_returns_200(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        response = client.put(
            "/courses/test-course/edit",
            json={"content": yaml.dump({"course": {"name": "Updated"}}, allow_unicode=True)},
        )
        assert response.status_code == 200

    def test_delete_returns_200(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        response = client.delete("/courses/test-course")
        assert response.status_code == 200

    def test_upload_returns_200(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        response = client.post(
            "/courses/upload",
            files={"file": ("new-course.yaml", b"course:\n  name: New Course\n", "application/x-yaml")},
        )
        assert response.status_code == 200

    def test_admin_labs_returns_200(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        response = client.get("/admin/courses/test-course/labs")
        assert response.status_code == 200
        assert response.json() == []

    def test_propagate_job_status_returns_404_for_unknown_job_not_401(self, client):
        client.cookies.set("admin_session", valid_cookie())
        response = client.get("/admin/propagate-jobs/does-not-exist")
        assert response.status_code == 404


@pytest.fixture
def propagate_course_config(sample_course_config):
    config = sample_course_config
    config["labs"]["1"]["template-repo"] = "test-org/os-task1-template"
    config["labs"]["1"]["repo-provisioning"] = "fork"
    return config


@pytest.fixture
def mock_get_course_by_id(propagate_course_config):
    with patch("main.get_course_by_id") as mock:
        mock.return_value = propagate_course_config
        yield mock


def run_background_tasks(bg: BackgroundTasks):
    for task in bg.tasks:
        task.func(*task.args, **task.kwargs)


class TestPropagateTemplateUpdateEndpointNoBody:
    """A POST with no JSON body at all must default to dry_run=true (issue
    #52: "значение по умолчанию - true, чтобы вызов без тела ничего не
    разослал"), exercised through a real ASGI request rather than a direct
    function call so FastAPI's own body-defaulting is what's under test."""

    @responses.activate
    def test_missing_body_defaults_to_dry_run(self, client, propagate_course_config):
        client.cookies.set("admin_session", valid_cookie())
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/os-task1-template",
            json={"default_branch": "main"}, status=200,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/os-task1-template/git/ref/heads/main",
            json={"object": {"sha": "a" * 40}}, status=200,
        )
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/os-task1-template/forks",
            json=[{"name": "test-task1-student1", "owner": {"login": "test-org"}, "default_branch": "main"}],
            status=200,
        )
        responses.add(responses.GET, "https://api.github.com/orgs/test-org/repos", json=[], status=200)
        responses.add(
            responses.POST, "https://api.github.com/repos/test-org/test-task1-student1/git/refs",
            json={}, status=201,
        )
        pr_call = responses.add(
            responses.POST, "https://api.github.com/repos/test-org/test-task1-student1/pulls",
            json={"html_url": "url"}, status=201,
        )

        with patch("main.get_course_by_id", return_value=propagate_course_config):
            response = client.post("/admin/courses/test-course/labs/1/propagate-template-update")

        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert pr_call.call_count == 0


class TestPropagateTemplateUpdateEndpoint:
    def test_rejects_lab_with_template_mode(self, mock_request, sample_course_config):
        sample_course_config["labs"]["1"]["template-repo"] = "test-org/os-task1-template"
        # repo-provisioning left at default ("template")
        with patch("main.get_course_by_id", return_value=sample_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.propagate_template_update(
                    mock_request, "test-course", "1", BackgroundTasks(),
                    body=main_module.PropagateRequest(dry_run=True), admin="admin",
                )
        assert exc_info.value.status_code == 400

    def test_rejects_lab_without_template_repo(self, mock_request, sample_course_config):
        sample_course_config["labs"]["1"]["repo-provisioning"] = "fork"
        with patch("main.get_course_by_id", return_value=sample_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.propagate_template_update(
                    mock_request, "test-course", "1", BackgroundTasks(),
                    body=main_module.PropagateRequest(dry_run=True), admin="admin",
                )
        assert exc_info.value.status_code == 400

    @responses.activate
    def test_dry_run_does_not_create_pull_requests(self, mock_request, mock_get_course_by_id):
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/os-task1-template",
            json={"default_branch": "main"}, status=200,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/os-task1-template/git/ref/heads/main",
            json={"object": {"sha": "a" * 40}}, status=200,
        )
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/os-task1-template/forks",
            json=[{"name": "test-task1-student1", "owner": {"login": "test-org"}, "default_branch": "main"}],
            status=200,
        )
        responses.add(
            responses.GET, "https://api.github.com/orgs/test-org/repos", json=[], status=200,
        )
        responses.add(
            responses.POST, "https://api.github.com/repos/test-org/test-task1-student1/git/refs",
            json={}, status=201,
        )
        pr_call = responses.add(
            responses.POST, "https://api.github.com/repos/test-org/test-task1-student1/pulls",
            json={"html_url": "url"}, status=201,
        )

        result = main_module.propagate_template_update(
            mock_request, "test-course", "1", BackgroundTasks(),
            body=main_module.PropagateRequest(dry_run=True), admin="admin",
        )

        assert result["total"] == 1
        assert pr_call.call_count == 0

    @responses.activate
    @patch("grading.propagate.time.sleep")
    def test_dry_run_false_returns_202_and_runs_job(self, _sleep, mock_request, mock_get_course_by_id):
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/os-task1-template",
            json={"default_branch": "main"}, status=200,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/os-task1-template/git/ref/heads/main",
            json={"object": {"sha": "a" * 40}}, status=200,
        )
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/os-task1-template/forks",
            json=[{"name": "test-task1-student1", "owner": {"login": "test-org"}, "default_branch": "main"}],
            status=200,
        )
        responses.add(
            responses.GET, "https://api.github.com/orgs/test-org/repos", json=[], status=200,
        )
        responses.add(
            responses.POST, "https://api.github.com/repos/test-org/test-task1-student1/git/refs",
            json={}, status=201,
        )
        responses.add(
            responses.POST, "https://api.github.com/repos/test-org/test-task1-student1/pulls",
            json={"html_url": "https://github.com/test-org/test-task1-student1/pull/1"}, status=201,
        )

        bg = BackgroundTasks()
        response = main_module.propagate_template_update(
            mock_request, "test-course", "1", bg,
            body=main_module.PropagateRequest(dry_run=False), admin="admin",
        )
        assert response.status_code == 202
        import json
        job_id = json.loads(response.body)["job_id"]

        run_background_tasks(bg)

        job = main_module.get_propagate_job(job_id)
        assert job.status == "done"
        assert job.results[0].status == "pr_created"

    def test_second_run_while_first_running_is_409(self, mock_request, mock_get_course_by_id):
        from grading.propagate import try_start_propagate_job
        try_start_propagate_job("test-course", "1")

        with pytest.raises(HTTPException) as exc_info:
            main_module.propagate_template_update(
                mock_request, "test-course", "1", BackgroundTasks(),
                body=main_module.PropagateRequest(dry_run=False), admin="admin",
            )
        assert exc_info.value.status_code == 409

    def test_unknown_job_status_is_404(self, mock_request):
        with pytest.raises(HTTPException) as exc_info:
            main_module.get_propagate_job_status(mock_request, "does-not-exist", admin="admin")
        assert exc_info.value.status_code == 404
