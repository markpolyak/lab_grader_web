"""
Multiple teachers in the admin panel (docs/MULTI_TEACHER_PLAN.md §12):
GitHub sign-in through the shared /join/callback, the admin session, and
per-course access for every course route and background job.
"""
import base64
import json
import os
import sys
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module
from grading.course_store import LocalCourseStore
from main import app
from grading.admin_access import AdminIdentity, is_course_teacher, parse_login_list
from grading.propagate import _jobs, _running_lab_keys, try_start_propagate_job
from grading.bulk import (
    _jobs as _bulk_jobs,
    _running_keys as _bulk_running_keys,
    try_start_bulk_job,
)

ALICE = AdminIdentity(login="Alice", role="teacher", via="github")
BOB = AdminIdentity(login="bob", role="teacher", via="github")
ROOT = AdminIdentity(login="root-gh", role="superadmin", via="github")
EMERGENCY = AdminIdentity(login=main_module.ADMIN_LOGIN, role="superadmin", via="password")

NOT_FOUND = {"detail": "Курс не найден"}
JOB_NOT_FOUND = {"detail": "Работа не найдена"}


def course_yaml(name: str, teachers: list) -> dict:
    return {
        "course": {
            "name": name,
            "github": {"organization": "test-org", "teachers": teachers},
            "google": {"spreadsheet": "sheet-id"},
            "labs": {
                "1": {
                    "github-prefix": "task1",
                    "short-name": "ЛР1",
                    "template-repo": "test-org/task1-template",
                    "repo-provisioning": "fork",
                },
            },
        }
    }


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(main_module.limiter, "enabled", False)


@pytest.fixture(autouse=True)
def clean_jobs():
    for store in (_jobs, _running_lab_keys, _bulk_jobs, _bulk_running_keys):
        store.clear()
    yield
    for store in (_jobs, _running_lab_keys, _bulk_jobs, _bulk_running_keys):
        store.clear()


@pytest.fixture(autouse=True)
def no_background_jobs():
    with patch("main._start_background_job"):
        yield


@pytest.fixture(autouse=True)
def superadmins(monkeypatch):
    monkeypatch.setattr(main_module, "SUPERADMIN_GITHUB_LOGINS", frozenset({"root-gh"}))


@pytest.fixture
def courses_env(tmp_path, monkeypatch):
    """
    alice-course: Alice (mixed case in config) plus a name entry.
    bob-course: bob only, hidden - teachers see their hidden courses too.
    """
    courses_dir = tmp_path / "courses"
    courses_dir.mkdir()
    files = {
        "alice-course.yaml": course_yaml("Alice Course", ["Mark Polyak", "ALICE"]),
        "bob-course.yaml": course_yaml("Bob Course", ["bob"]),
    }
    for filename, data in files.items():
        (courses_dir / filename).write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    index_file = courses_dir / "index.yaml"
    index_file.write_text(
        yaml.dump(
            {
                "courses": [
                    {"id": "alice-course", "file": "alice-course.yaml", "status": "active", "priority": 0},
                    {"id": "bob-course", "file": "bob-course.yaml", "status": "hidden", "priority": 0},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main_module, "COURSE_STORE", LocalCourseStore(str(courses_dir)))
    return courses_dir


def client_as(identity: AdminIdentity | None) -> TestClient:
    client = TestClient(app)
    if identity is not None:
        client.cookies.set("admin_session", main_module._build_admin_session(identity))
    return client


@pytest.fixture
def oauth_configured(monkeypatch):
    monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(main_module, "FRONTEND_URL", "http://front")


def state_payload(state: str) -> dict:
    payload_b64 = main_module.signer.unsign(state).decode("ascii")
    return json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))


# ---------------------------------------------------------------------------
# OAuth state with a purpose (§4.1)
# ---------------------------------------------------------------------------


class TestOAuthState:
    def test_admin_purpose_round_trips(self):
        state = main_module._build_oauth_state("admin")
        assert main_module._parse_oauth_state(state) == ("admin", {})

    def test_join_state_round_trips(self):
        state = main_module._build_join_state("c", "1")
        assert main_module._parse_oauth_state(state) == ("join", {"course_id": "c", "lab_id": "1"})

    def test_state_without_purpose_is_join(self):
        """States issued before the deploy carry no purpose."""
        payload = base64.urlsafe_b64encode(json.dumps({"course_id": "c", "lab_id": "1"}).encode()).decode()
        state = main_module.signer.sign(payload.encode()).decode()
        assert main_module._parse_oauth_state(state) == ("join", {"course_id": "c", "lab_id": "1"})

    def test_forged_state_is_refused(self):
        state = main_module._build_oauth_state("admin")
        with pytest.raises(HTTPException) as exc:
            main_module._parse_oauth_state(state[:-2] + "xx")
        assert exc.value.status_code == 400

    def test_expired_state_is_refused(self, monkeypatch):
        state = main_module._build_oauth_state("admin")
        monkeypatch.setattr(main_module, "JOIN_STATE_MAX_AGE", -1)
        with pytest.raises(HTTPException) as exc:
            main_module._parse_oauth_state(state)
        assert exc.value.status_code == 400

    def test_unknown_purpose_is_refused(self):
        payload = base64.urlsafe_b64encode(json.dumps({"purpose": "quiz"}).encode()).decode()
        state = main_module.signer.sign(payload.encode()).decode()
        with pytest.raises(HTTPException):
            main_module._parse_oauth_state(state)

    def test_join_state_without_course_is_refused(self):
        payload = base64.urlsafe_b64encode(json.dumps({"lab_id": "1"}).encode()).decode()
        state = main_module.signer.sign(payload.encode()).decode()
        with pytest.raises(HTTPException):
            main_module._parse_oauth_state(state)


# ---------------------------------------------------------------------------
# GitHub sign-in (§4.2)
# ---------------------------------------------------------------------------


class TestAdminLoginGithubStart:
    def test_503_without_oauth_app(self, monkeypatch):
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", None)
        response = TestClient(app).get("/admin/login/github", follow_redirects=False)
        assert response.status_code == 503

    def test_redirects_to_github_with_admin_state(self, oauth_configured):
        response = TestClient(app).get("/admin/login/github", follow_redirects=False)
        assert response.status_code in (302, 307)
        location = urlparse(response.headers["location"])
        assert location.netloc == "github.com"
        params = parse_qs(location.query)
        assert params["scope"] == ["read:user"]
        assert params["redirect_uri"][0].endswith("/join/callback")
        assert state_payload(params["state"][0]) == {"purpose": "admin"}


class TestAdminOAuthCallback:
    def callback(self, code="the-code", **extra):
        state = main_module._build_oauth_state("admin")
        return TestClient(app).get(
            "/join/callback", params={"code": code, "state": state, **extra}, follow_redirects=False
        )

    def session_of(self, response) -> AdminIdentity:
        cookie = response.cookies.get("admin_session")
        assert cookie, "no admin_session cookie set"
        # Set-Cookie quotes a value with "=" in it; the server unquotes it on
        # the way back in, the raw response header does not.
        return main_module._parse_admin_session(cookie.strip('"'))

    def test_superadmin(self, courses_env, oauth_configured):
        with patch("main._exchange_code_for_username", return_value="Root-GH"):
            response = self.callback()
        assert response.headers["location"] == "http://front/admin/courses"
        identity = self.session_of(response)
        assert identity.role == "superadmin" and identity.via == "github"
        assert identity.login == "Root-GH"

    def test_teacher(self, courses_env, oauth_configured):
        with patch("main._exchange_code_for_username", return_value="alice"):
            response = self.callback()
        assert response.headers["location"] == "http://front/admin/courses"
        identity = self.session_of(response)
        assert (identity.login, identity.role, identity.via) == ("alice", "teacher", "github")

    def test_teacher_of_hidden_course_only(self, courses_env, oauth_configured):
        with patch("main._exchange_code_for_username", return_value="bob"):
            response = self.callback()
        assert self.session_of(response).role == "teacher"

    def test_stranger_is_refused(self, courses_env, oauth_configured):
        with patch("main._exchange_code_for_username", return_value="mallory"):
            response = self.callback()
        assert response.headers["location"] == "http://front/admin?error=not_a_teacher"
        assert "admin_session" not in response.cookies

    def test_name_entry_grants_nothing(self, courses_env, oauth_configured):
        # GitHub logins have no spaces, so "Mark Polyak" matches nobody
        with patch("main._exchange_code_for_username", return_value="markpolyak"):
            response = self.callback()
        assert response.headers["location"].endswith("error=not_a_teacher")

    def test_code_exchange_failure(self, courses_env, oauth_configured):
        with patch("main._exchange_code_for_username", return_value=None):
            response = self.callback()
        assert response.headers["location"] == "http://front/admin?error=oauth_failed"
        assert "admin_session" not in response.cookies

    def test_user_declined(self, courses_env, oauth_configured):
        with patch("main._exchange_code_for_username") as exchange:
            response = self.callback(code="", error="access_denied")
        exchange.assert_not_called()
        assert response.headers["location"].endswith("error=oauth_failed")

    def test_admin_state_never_provisions_a_repo(self, courses_env, oauth_configured):
        with patch("main._exchange_code_for_username", return_value="alice"), \
             patch("main.RepoProvisioner") as provisioner:
            self.callback()
        provisioner.assert_not_called()


# ---------------------------------------------------------------------------
# Session (§5, §6)
# ---------------------------------------------------------------------------


class TestRequireAdmin:
    def test_new_format_cookie(self, courses_env):
        response = client_as(ALICE).get("/admin/check-auth")
        assert response.status_code == 200
        assert response.json() == {"authenticated": True, "login": "Alice", "role": "teacher", "via": "github"}

    def test_old_format_cookie_is_refused(self):
        client = TestClient(app)
        client.cookies.set("admin_session", main_module.signer.sign(main_module.ADMIN_LOGIN.encode()).decode())
        assert client.get("/admin/check-auth").status_code == 401

    def test_expired_cookie_is_refused(self, monkeypatch):
        client = client_as(EMERGENCY)
        monkeypatch.setattr(main_module, "ADMIN_SESSION_MAX_AGE", -1)
        assert client.get("/admin/check-auth").status_code == 401

    def test_superadmin(self):
        assert client_as(ROOT).get("/admin/check-auth").json()["role"] == "superadmin"

    def test_superadmin_removed_after_sign_in_is_refused(self, monkeypatch):
        client = client_as(ROOT)
        monkeypatch.setattr(main_module, "SUPERADMIN_GITHUB_LOGINS", frozenset())
        assert client.get("/admin/check-auth").status_code == 401

    def test_password_session_after_admin_login_change_is_refused(self, monkeypatch):
        client = client_as(EMERGENCY)
        monkeypatch.setattr(main_module, "ADMIN_LOGIN", "someone-else")
        assert client.get("/admin/check-auth").status_code == 401

    def test_forged_role_is_refused(self):
        payload = base64.urlsafe_b64encode(
            json.dumps({"login": "x", "role": "god", "via": "github"}).encode()
        ).decode()
        client = TestClient(app)
        client.cookies.set("admin_session", main_module.signer.sign(payload.encode()).decode())
        assert client.get("/admin/check-auth").status_code == 401


class TestEmergencyLogin:
    def test_password_sign_in_gives_superadmin(self, courses_env):
        client = TestClient(app)
        response = client.post(
            "/admin/login", json={"login": main_module.ADMIN_LOGIN, "password": main_module.ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        assert client.get("/admin/check-auth").json()["role"] == "superadmin"
        assert {c["id"] for c in client.get("/admin/courses").json()} == {"alice-course", "bob-course"}

    def test_wrong_password(self):
        response = TestClient(app).post("/admin/login", json={"login": main_module.ADMIN_LOGIN, "password": "nope"})
        assert response.status_code == 401


class TestIsCourseTeacher:
    def test_case_insensitive(self):
        assert is_course_teacher({"github": {"teachers": ["AliCe"]}}, "aLIce")

    def test_name_entries_grant_nothing(self):
        info = {"github": {"teachers": ["Mark Polyak"]}}
        assert not is_course_teacher(info, "markpolyak")
        assert not is_course_teacher(info, "Mark")

    @pytest.mark.parametrize("info", [
        {},
        {"github": {}},
        {"github": {"teachers": []}},
        {"github": {"teachers": None}},
        {"github": None},
        None,
    ])
    def test_empty_or_missing_list(self, info):
        assert not is_course_teacher(info, "alice")

    def test_empty_login(self):
        assert not is_course_teacher({"github": {"teachers": ["alice"]}}, "")

    def test_superadmin_list_parsing(self):
        assert parse_login_list(" Root , other,,") == frozenset({"root", "other"})
        assert parse_login_list(None) == frozenset()


# ---------------------------------------------------------------------------
# Course routes (§7.2)
# ---------------------------------------------------------------------------


def course_routes(course_id: str):
    return [
        ("GET", f"/courses/{course_id}/edit", {}),
        ("PUT", f"/courses/{course_id}/edit", {"json": {"content": yaml.dump(course_yaml("X", ["alice", "bob"])), "version": "x"}}),
        ("DELETE", f"/courses/{course_id}", {}),
        ("GET", f"/admin/courses/{course_id}/labs", {}),
        ("POST", f"/admin/courses/{course_id}/labs/1/propagate-template-update", {"json": {"dry_run": True}}),
        ("POST", f"/admin/courses/{course_id}/groups/G1/labs/1/bulk-grade", {"json": {"dry_run": True}}),
    ]


ROUTE_IDS = ["edit-get", "edit-put", "delete", "labs", "propagate", "bulk-grade"]


def with_current_version(client, method, path, kwargs):
    """A save carries the version the editor loaded - fetch it like the editor does."""
    if method == "PUT" and path.endswith("/edit"):
        version = client.get(path).json()["version"]
        kwargs = {"json": {**kwargs["json"], "version": version}}
    return kwargs


def reached_handler(response) -> bool:
    """Not the access-check answer - the request got to the route itself."""
    return not (response.status_code == 404 and response.json() == NOT_FOUND)


@pytest.fixture
def stub_github_and_sheets():
    """Let propagate/bulk-grade get past the access check without the network."""
    with patch("main.dry_run_propagation", return_value={"repos": []}), \
         patch("main._open_group_worksheet", return_value=(None, None)):
        yield


class TestCourseRoutes:
    @pytest.mark.parametrize("index", range(6), ids=ROUTE_IDS)
    def test_own_course(self, courses_env, stub_github_and_sheets, index):
        method, path, kwargs = course_routes("alice-course")[index]
        client = client_as(ALICE)
        response = client.request(method, path, **with_current_version(client, method, path, kwargs))
        assert reached_handler(response), response.text
        assert response.status_code < 400, response.text

    @pytest.mark.parametrize("index", range(6), ids=ROUTE_IDS)
    def test_foreign_course_looks_missing(self, courses_env, stub_github_and_sheets, index):
        method, path, kwargs = course_routes("bob-course")[index]
        foreign = client_as(ALICE).request(method, path, **kwargs)

        method, path, kwargs = course_routes("no-such-course")[index]
        missing = client_as(ALICE).request(method, path, **kwargs)

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == NOT_FOUND

    @pytest.mark.parametrize("index", range(6), ids=ROUTE_IDS)
    def test_superadmin(self, courses_env, stub_github_and_sheets, index):
        method, path, kwargs = course_routes("bob-course")[index]
        client = client_as(ROOT)
        response = client.request(method, path, **with_current_version(client, method, path, kwargs))
        assert response.status_code < 400, response.text

    def test_foreign_course_is_left_untouched(self, courses_env):
        client_as(ALICE).put("/courses/bob-course/edit", json={"content": "course: {}", "version": "x"})
        client_as(ALICE).delete("/courses/bob-course")
        assert yaml.safe_load((courses_env / "bob-course.yaml").read_text(encoding="utf-8")) == course_yaml(
            "Bob Course", ["bob"]
        )
        index = yaml.safe_load((courses_env / "index.yaml").read_text(encoding="utf-8"))
        assert [c["status"] for c in index["courses"]] == ["active", "hidden"]

    def test_teachers_edit_takes_effect_without_sign_in(self, courses_env):
        client = client_as(ALICE)
        assert client.get("/courses/bob-course/edit").status_code == 404
        (courses_env / "bob-course.yaml").write_text(
            yaml.dump(course_yaml("Bob Course", ["bob", "alice"])), encoding="utf-8"
        )
        assert client.get("/courses/bob-course/edit").status_code == 200


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------


class TestJobs:
    def test_propagate_job_status(self, courses_env):
        job = try_start_propagate_job("bob-course", "1")
        foreign = client_as(ALICE).get(f"/admin/propagate-jobs/{job.job_id}")
        missing = client_as(ALICE).get("/admin/propagate-jobs/unknown")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == JOB_NOT_FOUND
        assert client_as(BOB).get(f"/admin/propagate-jobs/{job.job_id}").status_code == 200
        assert client_as(ROOT).get(f"/admin/propagate-jobs/{job.job_id}").status_code == 200

    def test_bulk_job_status(self, courses_env):
        job = try_start_bulk_job("bob-course", "G1", "1", "by_sheet", True, None)
        foreign = client_as(ALICE).get(f"/admin/bulk-grade-jobs/{job.job_id}")
        missing = client_as(ALICE).get("/admin/bulk-grade-jobs/unknown")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == JOB_NOT_FOUND
        assert client_as(BOB).get(f"/admin/bulk-grade-jobs/{job.job_id}").status_code == 200

    def test_foreign_bulk_job_cannot_be_cancelled(self, courses_env):
        job = try_start_bulk_job("bob-course", "G1", "1", "by_sheet", True, None)
        response = client_as(ALICE).post(f"/admin/bulk-grade-jobs/{job.job_id}/cancel")
        assert response.status_code == 404
        assert response.json() == JOB_NOT_FOUND
        assert job.cancel_requested is False

    def test_own_bulk_job_can_be_cancelled(self, courses_env):
        job = try_start_bulk_job("bob-course", "G1", "1", "by_sheet", True, None)
        assert client_as(BOB).post(f"/admin/bulk-grade-jobs/{job.job_id}/cancel").status_code == 200

    def test_job_of_a_removed_course(self, courses_env):
        """A teacher can't reach a job whose course is gone; a superadmin still can."""
        job = try_start_propagate_job("gone-course", "1")
        assert client_as(ALICE).get(f"/admin/propagate-jobs/{job.job_id}").status_code == 404
        assert client_as(ROOT).get(f"/admin/propagate-jobs/{job.job_id}").status_code == 200


# ---------------------------------------------------------------------------
# GET /admin/courses (§7.1)
# ---------------------------------------------------------------------------


class TestAdminCourseList:
    def ids(self, identity):
        return [c["id"] for c in client_as(identity).get("/admin/courses").json()]

    def test_teacher_sees_only_own(self, courses_env):
        assert self.ids(ALICE) == ["alice-course"]

    def test_teacher_sees_own_hidden_course(self, courses_env):
        assert self.ids(BOB) == ["bob-course"]

    def test_superadmin_sees_all(self, courses_env):
        assert sorted(self.ids(ROOT)) == ["alice-course", "bob-course"]

    def test_items_carry_status_and_priority(self, courses_env):
        item = client_as(BOB).get("/admin/courses").json()[0]
        assert item["status"] == "hidden"
        assert item["priority"] == 0

    def test_requires_session(self):
        assert TestClient(app).get("/admin/courses").status_code == 401

    def test_public_list_unchanged(self, courses_env):
        assert [c["id"] for c in TestClient(app).get("/courses").json()] == ["alice-course"]


# ---------------------------------------------------------------------------
# Upload and save: a teacher can't lock themselves out (§7.2)
# ---------------------------------------------------------------------------


class TestUploadAndSave:
    def upload(self, identity, teachers, filename="new-course.yaml"):
        content = yaml.dump(course_yaml("New", teachers)).encode()
        return client_as(identity).post(
            "/courses/upload", files={"file": (filename, content, "application/x-yaml")}
        )

    def test_teacher_uploads_course_with_self(self, courses_env):
        response = self.upload(ALICE, ["alice"])
        assert response.status_code == 200
        assert "new-course" in [c["id"] for c in client_as(ALICE).get("/admin/courses").json()]

    def test_teacher_uploads_course_without_self(self, courses_env):
        response = self.upload(ALICE, ["bob"])
        assert response.status_code == 400
        assert response.json()["detail"] == "Добавьте свой GitHub-логин в course.github.teachers"
        assert not (courses_env / "new-course.yaml").exists()

    def test_superadmin_uploads_course_without_self(self, courses_env):
        assert self.upload(ROOT, ["bob"]).status_code == 200

    def save(self, identity, teachers):
        client = client_as(identity)
        version = client.get("/courses/alice-course/edit").json()["version"]
        return client.put(
            "/courses/alice-course/edit",
            json={"content": yaml.dump(course_yaml("Alice Course", teachers)), "version": version},
        )

    def test_teacher_removing_self_is_refused(self, courses_env):
        response = self.save(ALICE, ["Mark Polyak", "bob"])
        assert response.status_code == 400
        assert response.json()["detail"] == "Нельзя удалить себя из преподавателей курса"
        saved = yaml.safe_load((courses_env / "alice-course.yaml").read_text(encoding="utf-8"))
        assert saved["course"]["github"]["teachers"] == ["Mark Polyak", "ALICE"]

    def test_teacher_keeping_self_saves(self, courses_env):
        assert self.save(ALICE, ["alice", "bob"]).status_code == 200

    def test_superadmin_may_remove_anyone(self, courses_env):
        assert self.save(ROOT, ["bob"]).status_code == 200
