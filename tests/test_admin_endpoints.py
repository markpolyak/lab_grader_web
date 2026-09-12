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
from starlette.background import BackgroundTasks

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module
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
                    mock_request, "test-course", "P3300", "ЛР1", BackgroundTasks(),
                    body=main_module.BulkGradeRequest(), admin="admin",
                )
        assert exc_info.value.status_code == 400

    def test_unknown_lab_is_400(self, mock_request, bulk_course_config):
        with patch("main.get_course_by_id", return_value=bulk_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.start_bulk_grade(
                    mock_request, "test-course", "P3300", "ЛР42", BackgroundTasks(),
                    body=main_module.BulkGradeRequest(), admin="admin",
                )
        assert exc_info.value.status_code == 400

    def test_returns_202_and_runs_the_job(self, mock_request, bulk_course_config, mock_worksheet):
        import json

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            bg = BackgroundTasks()
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1", bg,
                body=main_module.BulkGradeRequest(dry_run=True), admin="admin",
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
                run_background_tasks(bg)

        assert job.status == "done"
        assert [r.github for r in job.results] == ["student1"]
        # dry_run: the report is built, the spreadsheet is left alone
        mock_worksheet.batch_update.assert_not_called()

    def test_name_file_selects_by_file_mode(self, mock_request, bulk_course_config, mock_worksheet):
        import json

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1", BackgroundTasks(),
                body=main_module.BulkGradeRequest(name_file="info.md"), admin="admin",
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
                    mock_request, "test-course", "P3300", "ЛР1", BackgroundTasks(),
                    body=main_module.BulkGradeRequest(name_file="info.md"), admin="admin",
                )

        assert exc_info.value.status_code == 400
        assert "ФИО" in exc_info.value.detail

    def test_team_lab_still_runs_in_by_sheet_mode(self, mock_request, bulk_course_config, mock_worksheet):
        import json

        bulk_course_config["labs"]["1"]["team"] = {"size-max": 4}
        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1", BackgroundTasks(),
                body=main_module.BulkGradeRequest(), admin="admin",
            )

        job = main_module.get_bulk_job(json.loads(response.body)["job_id"])
        assert job.mode == "by_sheet"

    def test_blank_name_file_falls_back_to_by_sheet_mode(self, mock_request, bulk_course_config, mock_worksheet):
        import json

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3300", "ЛР1", BackgroundTasks(),
                body=main_module.BulkGradeRequest(name_file="   "), admin="admin",
            )

        job = main_module.get_bulk_job(json.loads(response.body)["job_id"])
        assert job.mode == "by_sheet"
        assert job.name_file is None

    def test_second_run_for_same_group_and_lab_is_409(self, mock_request, bulk_course_config, mock_worksheet):
        from grading.bulk import try_start_bulk_job
        try_start_bulk_job("test-course", "P3300", "ЛР1", "by_sheet", False, None)

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            with pytest.raises(HTTPException) as exc_info:
                main_module.start_bulk_grade(
                    mock_request, "test-course", "P3300", "ЛР1", BackgroundTasks(),
                    body=main_module.BulkGradeRequest(), admin="admin",
                )
        assert exc_info.value.status_code == 409

    def test_other_group_may_start_while_one_runs(self, mock_request, bulk_course_config, mock_worksheet):
        from grading.bulk import try_start_bulk_job
        try_start_bulk_job("test-course", "P3300", "ЛР1", "by_sheet", False, None)

        with patch("main.get_course_by_id", return_value=bulk_course_config):
            response = main_module.start_bulk_grade(
                mock_request, "test-course", "P3301", "ЛР1", BackgroundTasks(),
                body=main_module.BulkGradeRequest(), admin="admin",
            )
        assert response.status_code == 202

    def test_cancel_marks_a_running_job(self, mock_request):
        from grading.bulk import try_start_bulk_job
        job = try_start_bulk_job("test-course", "P3300", "ЛР1", "by_sheet", False, None)

        result = main_module.cancel_bulk_grade_job(mock_request, job.job_id, admin="admin")

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
        response = client.put("/courses/test-course/edit", json={"content": updated})
        assert response.status_code == 200
        assert "revision: 4" in secret_lab_course.read_text(encoding="utf-8")

    def test_lowering_revision_is_rejected(self, client, secret_lab_course):
        """Откат ревизии воскресил бы уже отозванную ссылку (§13 плана)."""
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace("revision: 3", "revision: 2")
        response = client.put("/courses/test-course/edit", json={"content": updated})
        assert response.status_code == 400
        assert "revision" in response.json()["detail"]
        # Файл на диске не тронут
        assert "revision: 3" in secret_lab_course.read_text(encoding="utf-8")

    def test_broken_join_section_is_rejected_before_writing(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace('link: secret', 'link: sekret')
        response = client.put("/courses/test-course/edit", json={"content": updated})
        assert response.status_code == 400
        assert "join.link" in response.json()["detail"]
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML

    def test_unparseable_window_is_rejected(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace('opens-at: "2026-10-15 10:00"', 'opens-at: "когда-нибудь"')
        response = client.put("/courses/test-course/edit", json={"content": updated})
        assert response.status_code == 400
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML

    def test_team_lab_with_secret_link_is_rejected(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace(
            "      join:", "      team:\n        size-max: 4\n      join:"
        )
        response = client.put("/courses/test-course/edit", json={"content": updated})
        assert response.status_code == 400
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML

    def test_invalid_yaml_is_still_rejected(self, client, secret_lab_course):
        client.cookies.set("admin_session", valid_cookie())
        response = client.put("/courses/test-course/edit", json={"content": "course: [unclosed"})
        assert response.status_code == 400
        assert secret_lab_course.read_text(encoding="utf-8") == SECRET_LAB_YAML

    def test_course_without_join_sections_saves_as_before(self, client, admin_course_env):
        """Обратная совместимость: конфиг без join сохраняется как раньше."""
        client.cookies.set("admin_session", valid_cookie())
        content = "course:\n  name: Test Course\n  labs:\n    \"1\":\n      short-name: ЛР1\n"
        response = client.put("/courses/test-course/edit", json={"content": content})
        assert response.status_code == 200
        assert (admin_course_env / "test-course.yaml").read_text(encoding="utf-8") == content

    def test_write_is_atomic_and_leaves_no_temporary_files(self, client, secret_lab_course, admin_course_env):
        client.cookies.set("admin_session", valid_cookie())
        updated = SECRET_LAB_YAML.replace("revision: 3", "revision: 9")
        assert client.put("/courses/test-course/edit", json={"content": updated}).status_code == 200
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
            client.put("/courses/test-course/edit", json={"content": updated})
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
        labs = main_module.admin_list_course_labs(mock_request, "test-course", admin="admin")
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

    def test_ordinary_lab_has_no_link(self, mock_request, monkeypatch):
        labs = list_labs(mock_request, labs_course({"link": "public"}), monkeypatch)
        assert labs["1"]["join_link"] is None
        assert labs["1"]["join_secret"] is False
        assert labs["1"]["join_state"] == "open"

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
