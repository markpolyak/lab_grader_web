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
from unittest.mock import MagicMock, patch

import pytest
import responses
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module
from grading.course_store import LocalCourseStore
from main import app
from grading.grader import CIEvaluation, GradeResult, GradeStatus
from grading.propagate import _jobs, _running_lab_keys
from grading.bulk import _jobs as _bulk_jobs, _running_keys as _bulk_running_keys
from grading.join_links import TOKEN_RE


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
    _bulk_jobs.clear()
    _bulk_running_keys.clear()
    yield
    _jobs.clear()
    _running_lab_keys.clear()
    _bulk_jobs.clear()
    _bulk_running_keys.clear()


@pytest.fixture
def client():
    return TestClient(app)


SUPERADMIN = main_module.AdminIdentity(
    login=main_module.ADMIN_LOGIN, role="superadmin", via="password"
)


def put_course(client, content, course_id="test-course"):
    """PUT .../edit with the version the editor would have loaded first."""
    version = client.get(f"/courses/{course_id}/edit").json()["version"]
    return client.put(f"/courses/{course_id}/edit", json={"content": content, "version": version})


def valid_cookie():
    """Emergency (password) sign-in session - superadmin rights."""
    return main_module._build_admin_session(SUPERADMIN)


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
    monkeypatch.setattr(main_module, "COURSE_STORE", LocalCourseStore(str(courses_dir)))
    return courses_dir


PROTECTED_ROUTES = [
    ("GET", "/admin/check-auth", {}),
    ("DELETE", "/courses/test-course", {}),
    ("GET", "/courses/test-course/edit", {}),
    ("PUT", "/courses/test-course/edit", {"json": {"content": "course: {}", "version": "x"}}),
    ("GET", "/admin/courses/test-course/labs", {}),
    (
        "POST",
        "/admin/courses/test-course/labs/1/propagate-template-update",
        {"json": {"dry_run": True}},
    ),
    ("GET", "/admin/propagate-jobs/does-not-exist", {}),
    (
        "POST",
        "/admin/courses/test-course/groups/P3300/labs/1/bulk-grade",
        {"json": {"dry_run": True}},
    ),
    ("GET", "/admin/bulk-grade-jobs/does-not-exist", {}),
    ("POST", "/admin/bulk-grade-jobs/does-not-exist/cancel", {}),
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
        assert response.json() == {
            "authenticated": True,
            "login": main_module.ADMIN_LOGIN,
            "role": "superadmin",
            "via": "password",
        }

    def test_edit_get_returns_200(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        response = client.get("/courses/test-course/edit")
        assert response.status_code == 200
        assert response.json()["filename"] == "test-course.yaml"

    def test_edit_put_returns_200(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        response = put_course(client, yaml.dump({"course": {"name": "Updated"}}, allow_unicode=True))
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

    def test_bulk_grade_job_status_returns_404_for_unknown_job_not_401(self, client):
        client.cookies.set("admin_session", valid_cookie())
        response = client.get("/admin/bulk-grade-jobs/does-not-exist")
        assert response.status_code == 404

    def test_bulk_grade_job_cancel_returns_404_for_unknown_job_not_401(self, client):
        client.cookies.set("admin_session", valid_cookie())
        response = client.post("/admin/bulk-grade-jobs/does-not-exist/cancel")
        assert response.status_code == 404

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


# Grabbed before the autouse fixture below replaces the module attribute, so
# TestBackgroundJobStart can exercise the real thing.
START_BACKGROUND_JOB = main_module._start_background_job


@pytest.fixture(autouse=True)
def captured_jobs():
    """
    Catch the background jobs the endpoints start.

    The endpoints hand long runs to main._start_background_job (a daemon
    thread, so a disconnected client can't lose the run). Here it only
    records them, and a test that wants the work done calls run_captured -
    same as draining FastAPI background tasks used to be, but without a real
    thread racing the test's mocks.
    """
    calls = []
    with patch("main._start_background_job", side_effect=lambda func, *args, name=None: calls.append((func, args))):
        yield calls


def run_captured(calls):
    for func, args in calls:
        func(*args)


class TestBackgroundJobStart:
    """
    Long runs must not depend on the response being delivered: with FastAPI's
    BackgroundTasks, a client that disconnected (closed tab, proxy dropping
    the connection) left the job registered as running while nothing ran it,
    and the group/lab stayed locked until the backend was restarted.
    """

    def test_runs_the_function_in_a_daemon_thread(self):
        import threading

        seen = {}
        finished = threading.Event()

        def work(value):
            seen["value"] = value
            seen["daemon"] = threading.current_thread().daemon
            finished.set()

        START_BACKGROUND_JOB(work, 42, name="test-job")

        assert finished.wait(5)
        assert seen == {"value": 42, "daemon": True}


class TestOptionalLabNumber:
    """A lab key doesn't have to be a number, and the number is only needed to
    place the grade column for a lab without short-name."""

    def test_number_is_taken_from_the_key(self):
        assert main_module.optional_lab_number("ЛР1") == 1
        assert main_module.optional_lab_number("3") == 3

    def test_key_without_digits_gives_none_instead_of_refusing(self):
        assert main_module.optional_lab_number("quiz") is None
        # ...unlike parse_lab_id, which is still used where a number is required
        with pytest.raises(HTTPException):
            main_module.parse_lab_id("quiz")


class TestSheetsClient:
    def test_sets_a_request_timeout(self):
        """gspread waits forever by default, and one hung socket stops a whole
        bulk run (the GitHub side of this was PR #42)."""
        client = MagicMock()
        with patch("main.ServiceAccountCredentials.from_json_keyfile_name"), \
             patch("main.gspread.authorize", return_value=client):
            assert main_module.sheets_client() is client

        client.set_timeout.assert_called_once_with(main_module.SHEETS_TIMEOUT_SECONDS)


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
        # The fork lacks one template commit, and no update PR is open yet.
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/test-task1-student1/compare/main..." + "a" * 40,
            json={"ahead_by": 1, "files": [{"filename": "lab1.cpp"}]}, status=200,
        )
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/test-task1-student1/pulls", json=[], status=200,
        )
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
                    mock_request, "test-course", "1",
                    body=main_module.PropagateRequest(dry_run=True), admin=SUPERADMIN,
                )
        assert exc_info.value.status_code == 400

    def test_rejects_lab_without_template_repo(self, mock_request, sample_course_config):
        sample_course_config["labs"]["1"]["repo-provisioning"] = "fork"
        with patch("main.get_course_by_id", return_value=sample_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.propagate_template_update(
                    mock_request, "test-course", "1",
                    body=main_module.PropagateRequest(dry_run=True), admin=SUPERADMIN,
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
        # The fork lacks one template commit, and no update PR is open yet.
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/test-task1-student1/compare/main..." + "a" * 40,
            json={"ahead_by": 1, "files": [{"filename": "lab1.cpp"}]}, status=200,
        )
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/test-task1-student1/pulls", json=[], status=200,
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
            mock_request, "test-course", "1",
            body=main_module.PropagateRequest(dry_run=True), admin=SUPERADMIN,
        )

        assert result["total"] == 1
        assert pr_call.call_count == 0

    @responses.activate
    @patch("grading.propagate.time.sleep")
    def test_dry_run_false_returns_202_and_runs_job(self, _sleep, mock_request, mock_get_course_by_id, captured_jobs):
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
        # The fork lacks one template commit, and no update PR is open yet.
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test-org/test-task1-student1/compare/main..." + "a" * 40,
            json={"ahead_by": 1, "files": [{"filename": "lab1.cpp"}]}, status=200,
        )
        responses.add(
            responses.GET, "https://api.github.com/repos/test-org/test-task1-student1/pulls", json=[], status=200,
        )
        responses.add(
            responses.POST, "https://api.github.com/repos/test-org/test-task1-student1/git/refs",
            json={}, status=201,
        )
        responses.add(
            responses.POST, "https://api.github.com/repos/test-org/test-task1-student1/pulls",
            json={"html_url": "https://github.com/test-org/test-task1-student1/pull/1"}, status=201,
        )

        response = main_module.propagate_template_update(
            mock_request, "test-course", "1",
            body=main_module.PropagateRequest(dry_run=False), admin=SUPERADMIN,
        )
        assert response.status_code == 202
        import json
        job_id = json.loads(response.body)["job_id"]

        run_captured(captured_jobs)

        job = main_module.get_propagate_job(job_id)
        assert job.status == "done"
        assert job.results[0].status == "pr_created"

    def test_second_run_while_first_running_is_409(self, mock_request, mock_get_course_by_id):
        from grading.propagate import try_start_propagate_job
        try_start_propagate_job("test-course", "1")

        with pytest.raises(HTTPException) as exc_info:
            main_module.propagate_template_update(
                mock_request, "test-course", "1",
                body=main_module.PropagateRequest(dry_run=False), admin=SUPERADMIN,
            )
        assert exc_info.value.status_code == 409

    def test_unknown_job_status_is_404(self, mock_request):
        with pytest.raises(HTTPException) as exc_info:
            main_module.get_propagate_job_status(mock_request, "does-not-exist", admin=SUPERADMIN)
        assert exc_info.value.status_code == 404


class TestBulkGradeEndpoint:
    """The bulk grading endpoint itself: config checks, 202 + job, 409."""

    @pytest.fixture
    def bulk_course_config(self, sample_course_config):
        sample_course_config["google"]["spreadsheet"] = "sheet-id"
        return sample_course_config

    @pytest.fixture
    def mock_worksheet(self):
        """Patch the Sheets connection the endpoint opens before starting a job."""
        worksheet = MagicMock()
        worksheet.get_all_values.return_value = [
            ["№", "ФИО", "GitHub", ""],
            ["", "", "", "ЛР1"],
            ["1", "Иванов Иван", "student1", ""],
        ]
        spreadsheet = MagicMock()
        spreadsheet.fetch_sheet_metadata.return_value = {"properties": {"locale": "en_US"}}
        with patch.object(main_module, "_open_group_worksheet", return_value=(spreadsheet, worksheet)):
            yield worksheet

    def test_missing_spreadsheet_config_is_400(self, mock_request, sample_course_config):
        sample_course_config["google"].pop("spreadsheet", None)
        with patch("main.get_course_by_id", return_value=sample_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.start_bulk_grade(
                    mock_request, "test-course", "P3300", "ЛР1",
                    body=main_module.BulkGradeRequest(), admin=SUPERADMIN,
                )
        assert exc_info.value.status_code == 400

    def test_unknown_lab_is_400(self, mock_request, bulk_course_config):
        with patch("main.get_course_by_id", return_value=bulk_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.start_bulk_grade(
                    mock_request, "test-course", "P3300", "ЛР42",
                    body=main_module.BulkGradeRequest(), admin=SUPERADMIN,
                )
        assert exc_info.value.status_code == 400

    def test_returns_202_and_runs_the_job(self, mock_request, bulk_course_config, mock_worksheet, captured_jobs):
        import json

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1",
                body=main_module.BulkGradeRequest(dry_run=True), admin=SUPERADMIN,
            )
            assert response.status_code == 202
            job_id = json.loads(response.body)["job_id"]

            job = main_module.get_bulk_job(job_id)
            assert job.mode == "by_sheet"
            assert job.dry_run is True

            with patch.object(main_module.LabGrader, "check_repository", return_value=None), \
                 patch.object(main_module.LabGrader, "check_forbidden_files", return_value=None), \
                 patch.object(main_module.LabGrader, "_evaluate_ci_internal") as evaluate:
                evaluate.return_value = CIEvaluation(
                    grade_result=GradeResult(
                        status=GradeStatus.UPDATED, result="v",
                        message="Результат CI: ✅ Все проверки пройдены", passed="1/1",
                    ),
                    ci_passed=True,
                )
                run_captured(captured_jobs)

        assert job.status == "done"
        assert [r.github for r in job.results] == ["student1"]
        # dry_run: the report is built, the spreadsheet is left alone
        mock_worksheet.batch_update.assert_not_called()

    def test_name_file_selects_by_file_mode(self, mock_request, bulk_course_config, mock_worksheet):
        import json

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1",
                body=main_module.BulkGradeRequest(name_file="info.md"), admin=SUPERADMIN,
            )

        job = main_module.get_bulk_job(json.loads(response.body)["job_id"])
        assert job.mode == "by_file"
        assert job.name_file == "info.md"

    def test_by_file_mode_is_refused_for_a_team_lab(self, mock_request, bulk_course_config, mock_worksheet):
        """One name file per team cannot identify several students (§10.3)."""
        bulk_course_config["labs"]["1"]["team"] = {"size-max": 4}

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.start_bulk_grade(
                    mock_request, "test-course", "P3300", "ЛР1",
                    body=main_module.BulkGradeRequest(name_file="info.md"), admin=SUPERADMIN,
                )

        assert exc_info.value.status_code == 400
        assert "ФИО" in exc_info.value.detail

    def test_team_lab_still_runs_in_by_sheet_mode(self, mock_request, bulk_course_config, mock_worksheet):
        import json

        bulk_course_config["labs"]["1"]["team"] = {"size-max": 4}
        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1",
                body=main_module.BulkGradeRequest(), admin=SUPERADMIN,
            )

        job = main_module.get_bulk_job(json.loads(response.body)["job_id"])
        assert job.mode == "by_sheet"

    def test_blank_name_file_falls_back_to_by_sheet_mode(self, mock_request, bulk_course_config, mock_worksheet):
        import json

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1",
                body=main_module.BulkGradeRequest(name_file="   "), admin=SUPERADMIN,
            )

        job = main_module.get_bulk_job(json.loads(response.body)["job_id"])
        assert job.mode == "by_sheet"
        assert job.name_file is None

    def test_lab_whose_key_is_not_a_number_still_starts(
        self, mock_request, bulk_course_config, mock_worksheet, captured_jobs
    ):
        """The reported failure: a test lab keyed "quiz" answered 400
        "Некорректный lab_id" - after the job had been registered, so nothing
        ran it and the group stayed locked behind HTTP 409."""
        import json

        bulk_course_config["labs"]["quiz"] = {"github-prefix": "r", "short-name": "КР"}

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "quiz",
                body=main_module.BulkGradeRequest(dry_run=True), admin=SUPERADMIN,
            )

        assert response.status_code == 202
        job = main_module.get_bulk_job(json.loads(response.body)["job_id"])
        assert job.status == "running"
        # The lab number is passed as None, not refused
        [(func, args)] = captured_jobs
        assert func is main_module.run_bulk_grading
        assert args[-1] is None

    def test_a_job_that_cannot_be_started_releases_the_lab(
        self, mock_request, bulk_course_config, mock_worksheet
    ):
        """Registration takes the group and lab; if the start then fails, they
        have to be given back right away instead of answering 409 afterwards."""
        with patch("main.get_course_by_id", return_value=bulk_course_config), \
             patch("main._start_background_job", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                main_module.start_bulk_grade(
                    mock_request, "test-course", "P3300", "ЛР1",
                    body=main_module.BulkGradeRequest(), admin=SUPERADMIN,
                )

        assert main_module.get_running_bulk_job("test-course", "P3300", "ЛР1") is None
        with patch("main.get_course_by_id", return_value=bulk_course_config):
            retry = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1",
                body=main_module.BulkGradeRequest(), admin=SUPERADMIN,
            )
        assert retry.status_code == 202

    def test_run_is_handed_to_a_background_thread(
        self, mock_request, bulk_course_config, mock_worksheet, captured_jobs
    ):
        """Not to FastAPI's BackgroundTasks - see TestBackgroundJobStart."""
        with patch("main.get_course_by_id", return_value=bulk_course_config):
            main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1",
                body=main_module.BulkGradeRequest(), admin=SUPERADMIN,
            )

        assert [func for func, _args in captured_jobs] == [main_module.run_bulk_grading]

    def test_second_run_for_same_group_and_lab_is_409_with_the_running_job_id(
        self, mock_request, bulk_course_config, mock_worksheet
    ):
        """The 409 carries the run in progress, so the admin page can attach to
        it instead of leaving the teacher with an error and no way to look."""
        import json
        from grading.bulk import try_start_bulk_job
        running = try_start_bulk_job("test-course", "P3300", "ЛР1", "by_sheet", False, None)

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1",
                body=main_module.BulkGradeRequest(), admin=SUPERADMIN,
            )

        assert response.status_code == 409
        assert json.loads(response.body)["job_id"] == running.job_id

    def test_other_group_may_start_while_one_runs(self, mock_request, bulk_course_config, mock_worksheet):
        from grading.bulk import try_start_bulk_job
        try_start_bulk_job("test-course", "P3300", "ЛР1", "by_sheet", False, None)

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3301", "ЛР1",
                body=main_module.BulkGradeRequest(), admin=SUPERADMIN,
            )
        assert response.status_code == 202

    def test_cancel_marks_a_running_job(self, mock_request):
        from grading.bulk import try_start_bulk_job
        job = try_start_bulk_job("test-course", "P3300", "ЛР1", "by_sheet", False, None)

        result = main_module.cancel_bulk_grade_job(mock_request, job.job_id, admin=SUPERADMIN)

        assert result["status"] == "running"
        assert job.cancel_requested is True


# ---------------------------------------------------------------------------
# Сохранение конфига курса: семантическая проверка секции join, запрет
# уменьшать join.revision и атомарная запись
# (docs/SECRET_JOIN_LINKS_PLAN.md §7.2, §13, этап 1 чек-листа).
# ---------------------------------------------------------------------------


SECRET_LAB_YAML = """course:
  name: Test Course
  timezone: UTC+3
  labs:
    "7":
      github-prefix: kr1
      short-name: "Тест / КР"
      template-repo: org/kr1-template
      join:
        link: secret
        opens-at: "2026-10-15 10:00"
        revision: 3
"""


@pytest.fixture
def secret_lab_course(admin_course_env):
    """Курс с секретной лабой на диске - исходная версия для правок."""
    path = admin_course_env / "test-course.yaml"
    path.write_text(SECRET_LAB_YAML, encoding="utf-8")
    return path


class TestEditCourseJoinValidation:
    def test_saving_a_valid_change_works(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace("revision: 3", "revision: 4")
        response = put_course(client, updated)
        assert response.status_code == 200
        assert "revision: 4" in secret_lab_course.read_text(encoding="utf-8")

    def test_lowering_revision_is_rejected(self, client, secret_lab_course):
        """Откат ревизии воскресил бы уже отозванную ссылку (§13 плана)."""
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace("revision: 3", "revision: 2")
        response = put_course(client, updated)
        assert response.status_code == 400
        assert "revision" in response.json()["detail"]
        # Файл на диске не тронут
        assert "revision: 3" in secret_lab_course.read_text(encoding="utf-8")

    def test_broken_join_section_is_rejected_before_writing(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace('link: secret', 'link: sekret')
        response = put_course(client, updated)
        assert response.status_code == 400
        assert "join.link" in response.json()["detail"]
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML

    def test_unparseable_window_is_rejected(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace('opens-at: "2026-10-15 10:00"', 'opens-at: "когда-нибудь"')
        response = put_course(client, updated)
        assert response.status_code == 400
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML

    def test_team_lab_with_secret_link_is_rejected(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace(
            "      join:", "      team:\n        size-max: 4\n      join:"
        )
        response = put_course(client, updated)
        assert response.status_code == 400
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML

    def test_invalid_yaml_is_still_rejected(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        response = put_course(client, "course: [unclosed")
        assert response.status_code == 400
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML

    def test_course_without_join_sections_saves_as_before(self, client, admin_course_env):
        """Обратная совместимость: конфиг без join сохраняется как раньше."""
        client.cookies.set("admin_session", valid_cookie())
        content = "course:\n  name: Test Course\n  labs:\n    \"1\":\n      short-name: ЛР1\n"
        response = put_course(client, content)
        assert response.status_code == 200
        assert (admin_course_env / "test-course.yaml").read_text(encoding="utf-8") == content

    def test_write_is_atomic_and_leaves_no_temporary_files(self, client, secret_lab_course, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace("revision: 3", "revision: 9")
        assert put_course(client, updated).status_code == 200
        leftovers = [p.name for p in admin_course_env.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_failed_write_leaves_the_previous_file_intact(self, client, secret_lab_course, monkeypatch):
        """Прерванное сохранение не должно оставлять обрезанный файл."""
        client.cookies.set("admin_session", valid_cookie())

        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(main_module.os, "replace", failing_replace)
        updated = SECRET_LAB_YAML.replace("revision: 3", "revision: 5")
        with pytest.raises(OSError):
            put_course(client, updated)
        monkeypatch.setattr(main_module.os, "replace", real_replace)
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML


# ---------------------------------------------------------------------------
# Админский список лаб: готовая секретная ссылка и состояние окна
# (docs/SECRET_JOIN_LINKS_PLAN.md §7.2, §9, этап 5 чек-листа).
# ---------------------------------------------------------------------------

PAST = "2000-01-01 10:00"
FUTURE = "2099-01-01 10:00"


def labs_course(join_section, **lab_extra):
    lab = {
        "short-name": "Тест / КР",
        "github-prefix": "kr1",
        "template-repo": "org/kr1-template",
        "join": join_section,
    }
    lab.update(lab_extra)
    return {
        "name": "Test Course",
        "timezone": "UTC+3",
        "labs": {
            "1": {"short-name": "ЛР1", "github-prefix": "os-task1"},
            "7": lab,
        },
        "_meta": {"filename": "test-course.yaml", "id": "test-course"},
    }


def list_labs(mock_request, course, monkeypatch, base_url="https://labgrader.example.ru"):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", base_url)
    with patch("main.get_course_by_id", return_value=course):
        labs = main_module.admin_list_course_labs(mock_request, "test-course", admin=SUPERADMIN)
    return {lab["id"]: lab for lab in labs}


class TestAdminLabListJoinFields:
    def test_secret_lab_carries_a_ready_to_send_link(self, mock_request, monkeypatch):
        course = labs_course({"link": "secret", "opens-at": FUTURE})
        labs = list_labs(mock_request, course, monkeypatch)

        link = labs["7"]["join_link"]
        assert link.startswith("https://labgrader.example.ru/j/")
        token = link.rsplit("/", 1)[1]
        assert TOKEN_RE.match(token)
        assert labs["7"]["join_secret"] is True

    def test_link_is_available_long_before_opens_at(self, mock_request, monkeypatch):
        """Преподавателю нужно подготовить рассылку заранее (§9.1)."""
        course = labs_course({"link": "secret", "opens-at": FUTURE})
        labs = list_labs(mock_request, course, monkeypatch)
        assert labs["7"]["join_state"] == "not_open"
        assert labs["7"]["join_link"]
        assert labs["7"]["opens_at"].startswith("2099-01-01T10:00")

    @pytest.mark.parametrize("join_section,expected", [
        ({"link": "secret", "opens-at": FUTURE}, "not_open"),
        ({"link": "secret", "opens-at": PAST}, "open"),
        ({"link": "secret", "opens-at": PAST, "closes-at": PAST}, "closed"),
    ])
    def test_window_state_is_reported(self, mock_request, monkeypatch, join_section, expected):
        labs = list_labs(mock_request, labs_course(join_section), monkeypatch)
        assert labs["7"]["join_state"] == expected

    def test_lab_without_template_repo_has_no_link(self, mock_request, monkeypatch):
        """Лаба "1" в фикстуре без template-repo: /join для неё не работает."""
        labs = list_labs(mock_request, labs_course({"link": "public"}), monkeypatch)
        assert labs["1"]["join_link"] is None
        assert labs["1"]["join_secret"] is False
        assert labs["1"]["join_state"] == "open"

    def test_ordinary_lab_gets_the_public_link(self, mock_request, monkeypatch):
        """Столбец со ссылками общий: обычная лаба показывает /join/{курс}/{лаба}."""
        course = labs_course({"link": "public"})
        course["labs"]["1"]["template-repo"] = "org/os-task1-template"
        labs = list_labs(mock_request, course, monkeypatch)

        assert labs["1"]["join_link"] == "https://labgrader.example.ru/join/test-course/1"
        assert labs["1"]["join_secret"] is False

    def test_broken_join_section_does_not_break_the_list(self, mock_request, monkeypatch):
        labs = list_labs(mock_request, labs_course({"link": "secret", "revision": 0}), monkeypatch)
        assert labs["7"]["join_link"] is None
        assert "revision" in labs["7"]["join_error"]
        # остальные лабы курса на месте
        assert labs["1"]["short_name"] == "ЛР1"

    def test_frontend_url_is_used_when_public_base_url_is_unset(self, mock_request, monkeypatch):
        monkeypatch.setattr(main_module, "FRONTEND_URL", "https://front.example.com")
        labs = list_labs(mock_request, labs_course({"link": "secret"}), monkeypatch, base_url=None)
        assert labs["7"]["join_link"].startswith("https://front.example.com/j/")

    def test_link_matches_the_one_the_script_prints(self, mock_request, monkeypatch, tmp_path):
        """
        §9: админка и scripts/join-link.py показывают одну и ту же строку -
        обе считают её одними и теми же функциями grading/join_links.py.
        """
        import subprocess

        course = labs_course({"link": "secret", "opens-at": FUTURE})
        labs = list_labs(mock_request, course, monkeypatch)

        courses_dir = tmp_path / "courses"
        courses_dir.mkdir()
        (courses_dir / "index.yaml").write_text(
            yaml.dump({"courses": [{"id": "test-course", "file": "c.yaml"}]}), encoding="utf-8"
        )
        payload = {"course": {k: v for k, v in course.items() if k != "_meta"}}
        (courses_dir / "c.yaml").write_text(yaml.dump(payload, allow_unicode=True), encoding="utf-8")

        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "join-link.py")
        result = subprocess.run(
            [sys.executable, script, "--courses-dir", str(courses_dir),
             "--base-url", "https://labgrader.example.ru", "--course", "test-course", "--lab", "7"],
            capture_output=True, text=True,
            env={**os.environ, "SECRET_KEY": main_module.SECRET_KEY},
        )
        assert result.returncode == 0, result.stderr
        assert labs["7"]["join_link"] in result.stdout


class TestBulkGradeIgnoresJoinWindow:
    """
    Массовая проверка из админки окном не ограничена (§5, §7.2 плана):
    преподаватель проверяет работу и до открытия, и после закрытия приёма,
    иначе контрольную нельзя было бы проверить вовсе.
    """

    @pytest.fixture
    def worksheet(self):
        worksheet = MagicMock()
        worksheet.get_all_values.return_value = [
            ["№", "ФИО", "GitHub", ""],
            ["", "", "", "Тест / КР"],
            ["1", "Иванов Иван", "student1", ""],
        ]
        spreadsheet = MagicMock()
        spreadsheet.fetch_sheet_metadata.return_value = {"properties": {"locale": "en_US"}}
        with patch.object(main_module, "_open_group_worksheet", return_value=(spreadsheet, worksheet)):
            yield worksheet

    @pytest.mark.parametrize("join_section", [
        {"link": "secret", "opens-at": FUTURE},
        {"link": "secret", "opens-at": PAST, "closes-at": PAST},
    ])
    def test_secret_lab_is_graded_in_bulk_whatever_the_window(
        self, mock_request, worksheet, join_section
    ):
        course = labs_course(join_section)
        course["google"] = {"spreadsheet": "sheet-id"}
        course["github"] = {"organization": "test-org"}

        with patch("main.get_course_by_id", return_value=course):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "7",
                body=main_module.BulkGradeRequest(dry_run=True), admin=SUPERADMIN,
            )

        assert response.status_code == 202


# ---------------------------------------------------------------------------
# Хранилище конфигураций: версии, конфликты, история, логотипы
# (docs/COURSE_STORAGE_PLAN.md §7, §13).
# ---------------------------------------------------------------------------

from grading.course_store import (  # noqa: E402
    Revision,
    StoreUnavailableError,
    StoredFile,
    content_version,
)


class HistoryStore(LocalCourseStore):
    """LocalCourseStore with a hand-made history: {sha: {path: content}}."""

    supports_history = True

    def __init__(self, root, versions):
        super().__init__(root)
        self.versions = versions
        self.writes = []

    def write(self, changes, expected, message, author):
        self.writes.append((dict(changes), dict(expected), message, author))
        super().write(changes, expected, message, author)

    def history(self, path, limit=30):
        return [
            Revision(sha=sha, date=None, author="alice", message=f"{sha}: правка")
            for sha, files in self.versions.items() if path in files
        ]

    def read_at(self, path, sha):
        content = self.versions.get(sha, {}).get(path)
        if content is None:
            return None
        return StoredFile(path=path, content=content, version=content_version(content))


OLD_SHA = "a" * 40


@pytest.fixture
def history_store(admin_course_env, monkeypatch):
    store = HistoryStore(str(admin_course_env), {
        OLD_SHA: {"test-course.yaml": b"course:\n  name: Old Name\n"},
    })
    monkeypatch.setattr(main_module, "COURSE_STORE", store)
    return store


class TestEditVersion:
    def test_get_returns_version(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        data = client.get("/courses/test-course/edit").json()
        content = (admin_course_env / "test-course.yaml").read_bytes()
        assert data["version"] == content_version(content)

    def test_stale_version_is_409_and_file_untouched(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        stale = client.get("/courses/test-course/edit").json()["version"]
        assert put_course(client, "course:\n  name: First\n").status_code == 200

        response = client.put(
            "/courses/test-course/edit", json={"content": "course:\n  name: Second\n", "version": stale}
        )
        assert response.status_code == 409
        assert response.json()["detail"].startswith("Конфигурацию курса изменил другой пользователь")
        assert "First" in (admin_course_env / "test-course.yaml").read_text(encoding="utf-8")

    def test_store_unavailable_is_503(self, client, admin_course_env, monkeypatch):
        client.cookies.set("admin_session", valid_cookie())
        version = client.get("/courses/test-course/edit").json()["version"]

        def unavailable(*args, **kwargs):
            raise StoreUnavailableError("down")

        monkeypatch.setattr(main_module.COURSE_STORE, "write", unavailable)
        response = client.put("/courses/test-course/edit", json={"content": "course: {}", "version": version})
        assert response.status_code == 503
        assert response.json()["detail"] == "Хранилище конфигураций недоступно, изменения не сохранены"

    def test_course_key_is_required(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        assert put_course(client, "name: no course key\n").status_code == 400

    def test_broken_team_section_blocks_save(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        content = 'course:\n  labs:\n    "1":\n      team:\n        size-max: "много"\n'
        assert put_course(client, content).status_code == 400


class TestUploadSingleWrite:
    def test_file_and_index_in_one_write(self, client, history_store, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        response = client.post(
            "/courses/upload", files={"file": ("new-course.yaml", b"course: {name: New}\n", "application/x-yaml")}
        )
        assert response.status_code == 200
        assert len(history_store.writes) == 1
        changes, expected, message, author = history_store.writes[0]
        assert set(changes) == {"new-course.yaml", "index.yaml"}
        assert expected["new-course.yaml"] is None
        assert message == "new-course: новый курс"
        assert author == main_module.ADMIN_LOGIN

    @pytest.mark.parametrize("filename", ["index.yaml", "../evil.yaml", ".hidden.yaml", "sub/x.yaml"])
    def test_bad_file_names(self, client, admin_course_env, filename):
        client.cookies.set("admin_session", valid_cookie())
        response = client.post(
            "/courses/upload", files={"file": (filename, b"course: {}\n", "application/x-yaml")}
        )
        assert response.status_code == 400


class TestDeleteThroughStore:
    def test_hide_is_one_index_write(self, client, history_store, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        assert client.delete("/courses/test-course").status_code == 200
        changes, _expected, message, _author = history_store.writes[0]
        assert set(changes) == {"index.yaml"}
        assert message == "test-course: курс скрыт"
        assert "hidden" in (admin_course_env / "index.yaml").read_text(encoding="utf-8")


class TestHistoryAndRestore:
    def test_no_history_in_local_store_is_404(self, client, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        assert client.get("/admin/courses/test-course/history").status_code == 404

    def test_history(self, client, history_store):
        client.cookies.set("admin_session", valid_cookie())
        response = client.get("/admin/courses/test-course/history")
        assert response.status_code == 200
        assert response.json() == [
            {"sha": OLD_SHA, "date": None, "author": "alice", "message": f"{OLD_SHA}: правка"}
        ]

    def test_version_content(self, client, history_store):
        client.cookies.set("admin_session", valid_cookie())
        response = client.get(f"/admin/courses/test-course/history/{OLD_SHA}")
        assert response.json()["content"] == "course:\n  name: Old Name\n"
        assert client.get("/admin/courses/test-course/history/" + "b" * 40).status_code == 404
        assert client.get("/admin/courses/test-course/history/not-a-sha").status_code == 404

    def test_restore(self, client, history_store, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        version = client.get("/courses/test-course/edit").json()["version"]
        response = client.post(
            "/admin/courses/test-course/restore", json={"sha": OLD_SHA, "version": version}
        )
        assert response.status_code == 200
        assert "Old Name" in (admin_course_env / "test-course.yaml").read_text(encoding="utf-8")
        assert history_store.writes[-1][2] == f"test-course: восстановлена версия {OLD_SHA[:7]}"

    def test_restore_with_stale_version_is_409(self, client, history_store):
        client.cookies.set("admin_session", valid_cookie())
        response = client.post("/admin/courses/test-course/restore", json={"sha": OLD_SHA, "version": "stale"})
        assert response.status_code == 409

    def test_restoring_lower_join_revision_is_400(self, client, history_store, admin_course_env):
        """Восстановление старой версии не должно воскрешать отозванную ссылку."""
        (admin_course_env / "test-course.yaml").write_text(SECRET_LAB_YAML, encoding="utf-8")
        history_store.versions[OLD_SHA]["test-course.yaml"] = (
            SECRET_LAB_YAML.replace("revision: 3", "revision: 1").encode("utf-8")
        )
        client.cookies.set("admin_session", valid_cookie())
        version = client.get("/courses/test-course/edit").json()["version"]
        response = client.post("/admin/courses/test-course/restore", json={"sha": OLD_SHA, "version": version})
        assert response.status_code == 400
        assert "revision" in response.json()["detail"]
        assert (admin_course_env / "test-course.yaml").read_text(encoding="utf-8") == SECRET_LAB_YAML

    @pytest.mark.parametrize("method,path,kwargs", [
        ("GET", "/admin/courses/test-course/history", {}),
        ("GET", f"/admin/courses/test-course/history/{OLD_SHA}", {}),
        ("POST", "/admin/courses/test-course/restore", {"json": {"sha": OLD_SHA, "version": "x"}}),
    ])
    def test_foreign_course_is_404(self, client, history_store, method, path, kwargs):
        stranger = main_module.AdminIdentity(login="stranger", role="teacher", via="github")
        client.cookies.set("admin_session", main_module._build_admin_session(stranger))
        response = client.request(method, path, **kwargs)
        assert response.status_code == 404
        assert response.json() == {"detail": "Курс не найден"}
        assert history_store.writes == []


class TestLogoRoute:
    @pytest.fixture
    def logo(self, admin_course_env):
        (admin_course_env / "logos").mkdir()
        (admin_course_env / "logos" / "os.png").write_bytes(b"\x89PNG-data")
        return b"\x89PNG-data"

    def test_content_type_and_etag(self, client, logo):
        response = client.get("/courses/logos/os.png")
        assert response.status_code == 200
        assert response.content == logo
        assert response.headers["content-type"] == "image/png"
        assert response.headers["etag"] == f'"{content_version(logo)}"'
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_not_modified(self, client, logo):
        etag = client.get("/courses/logos/os.png").headers["etag"]
        response = client.get("/courses/logos/os.png", headers={"If-None-Match": etag})
        assert response.status_code == 304

    def test_missing_and_bad_names(self, client, logo):
        assert client.get("/courses/logos/none.png").status_code == 404
        assert client.get("/courses/logos/..%2Findex.yaml").status_code == 404
        assert client.get("/courses/logos/.hidden").status_code == 404


class TestCommitMessage:
    def save(self, client, message=None):
        version = client.get("/courses/test-course/edit").json()["version"]
        body = {"content": "course:\n  name: X\n", "version": version}
        if message is not None:
            body["message"] = message
        return client.put("/courses/test-course/edit", json=body)

    def test_default_message(self, client, history_store):
        client.cookies.set("admin_session", valid_cookie())
        assert self.save(client).status_code == 200
        assert history_store.writes[-1][2] == "test-course: правка через админку"

    def test_custom_message(self, client, history_store):
        client.cookies.set("admin_session", valid_cookie())
        assert self.save(client, "  Добавлен преподаватель\nivanov ").status_code == 200
        assert history_store.writes[-1][2] == "test-course: Добавлен преподаватель ivanov"

    def test_blank_message_is_default(self, client, history_store):
        client.cookies.set("admin_session", valid_cookie())
        self.save(client, "   ")
        assert history_store.writes[-1][2] == "test-course: правка через админку"

    def test_long_message_is_cut(self, client, history_store):
        client.cookies.set("admin_session", valid_cookie())
        self.save(client, "я" * 500)
        assert history_store.writes[-1][2] == "test-course: " + "я" * main_module.COMMIT_MESSAGE_MAX_LENGTH

    def test_restore_message(self, client, history_store):
        client.cookies.set("admin_session", valid_cookie())
        version = client.get("/courses/test-course/edit").json()["version"]
        client.post("/admin/courses/test-course/restore",
                    json={"sha": OLD_SHA, "version": version, "message": "откат ошибочной правки"})
        assert history_store.writes[-1][2] == "test-course: откат ошибочной правки"
