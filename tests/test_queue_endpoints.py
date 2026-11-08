"""Endpoint tests for the queue API.

Endpoint functions are called directly (they're plain functions under the
slowapi decorator) with mocked Request objects that have view_rate_limit set,
bypassing the slowapi middleware entirely.
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

os.environ["ADMIN_LOGIN"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["GITHUB_TOKEN"] = "dummy"
os.environ["GITHUB_OAUTH_CLIENT_ID"] = "test"
os.environ["GITHUB_OAUTH_CLIENT_SECRET"] = "test"

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import main as main_module
from grading.queue.session import build_student_session
from grading.queue.store import (
    QUEUE_HEADERS,
    HISTORY_HEADERS,
    STATUS_WAITING,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_ABSENT,
    STATUS_WITHDRAWN,
    STATUS_NOT_REACHED,
)

SIGNER = main_module.signer
TODAY = date(2026, 11, 9)
TODAY_STR = TODAY.isoformat()


class FakeWorksheet:
    def __init__(self, title="Очередь-" + TODAY_STR, rows=None):
        self.title = title
        self.id = 1
        self.values = [list(r) for r in (rows or [QUEUE_HEADERS])]

    def row_values(self, row):
        return self.values[row - 1] if len(self.values) >= row else []

    def get_all_values(self):
        return [list(r) for r in self.values]

    def update_cell(self, row, col, value):
        while len(self.values) < row:
            self.values.append([])
        while len(self.values[row - 1]) < col:
            self.values[row - 1].append("")
        self.values[row - 1][col - 1] = str(value)

    def append_row(self, values, **_kw):
        self.values.append([str(v) for v in values])

    def batch_update(self, updates, **_kw):
        for u in updates:
            rng = u["range"]
            col = ord(rng[0].upper()) - ord("A") + 1
            row = int(rng[1:])
            self.update_cell(row, col, u["values"][0][0])


class FakeSpreadsheet:
    def __init__(self):
        self.id = "spreadsheet-id"
        self._sheets = {}
        self._next_id = 100
        self.metadata_requests = []
        today_ws = FakeWorksheet()
        today_ws.id = 1
        self._sheets["Очередь-" + TODAY_STR] = today_ws
        history_ws = FakeWorksheet("История_очереди", [HISTORY_HEADERS])
        history_ws.id = 2
        self._sheets["История_очереди"] = history_ws

    def worksheet(self, title):
        from gspread.exceptions import WorksheetNotFound
        if title not in self._sheets:
            raise WorksheetNotFound(title)
        return self._sheets[title]

    def worksheets(self):
        return list(self._sheets.values())

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(title)
        ws.id = self._next_id
        self._next_id += 1
        self._sheets[title] = ws
        return ws

    def batch_update(self, body):
        self.metadata_requests.extend(body["requests"])
        for req in body["requests"]:
            if "deleteSheet" in req:
                for k, v in list(self._sheets.items()):
                    if v.id == req["deleteSheet"]["sheetId"]:
                        del self._sheets[k]
            if "appendCells" in req:
                target = next(s for s in self._sheets.values() if s.id == req["appendCells"]["sheetId"])
                for row in req["appendCells"]["rows"]:
                    vals = [c["userEnteredValue"]["stringValue"] for c in row["values"]]
                    target.append_row(vals)


def _fake_course_info():
    return {
        "name": "Test Course",
        "timezone": "UTC+3",
        "github": {"organization": "test-org", "teachers": []},
        "google": {"spreadsheet": "fake-id", "info-sheet": "График",
                    "student-name-column": 1, "lab-column-offset": 1},
        "labs": {
            "1": {
                "short-name": "ЛР1",
                "github-prefix": "lab1",
                "template-repo": "test-org/lab1-template",
                "repo-provisioning": "generate",
                "files": ["main.py"],
                "ci": ["workflows"],
            },
        },
        "_meta": {"filename": "test.yaml", "status": "active",
                   "priority": 0, "logo": None, "featured": False},
    }


def _fake_session():
    return SimpleNamespace(
        slot_id="guap-mon-2",
        subject="Test",
        starts_at=datetime(2026, 11, 9, 10, 40),
        ends_at=datetime(2026, 11, 9, 12, 10),
        auto_open_at=datetime(2026, 11, 9, 10, 25),
        auto_close_at=datetime(2026, 11, 9, 12, 20),
        room="301",
        teacher=None,
        note=None,
        allowed_lab_ids=None,
        source="guap",
        course_id="dev-course",
        group_id="4431",
    )


def _make_student_cookie(username="alice", course_id="dev-course",
                         group_id="4431", fio="Alice Student"):
    return build_student_session(SIGNER, username, fio, course_id, group_id)


def _make_admin_cookie():
    return SIGNER.sign(b"admin").decode()


def _mock_request(cookies=None):
    headers = []
    if cookies:
        for name, value in cookies.items():
            headers.append((b"cookie", f"{name}={value}".encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope, lambda: None)
    request.state.view_rate_limit = None
    return request


def _post_request(json_body=None, cookies=None):
    headers = [("content-type", b"application/json")]
    body = b""
    if json_body is not None:
        body = json.dumps(json_body).encode()
    if cookies:
        for name, value in cookies.items():
            headers.append((b"cookie", f"{name}={value}".encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope, lambda: body)
    request.state.view_rate_limit = None
    return request


@pytest.fixture
def deps(monkeypatch):
    fake_ss = FakeSpreadsheet()

    monkeypatch.setattr(main_module, "get_course_by_id", lambda cid: _fake_course_info())

    fake_gspread_client = SimpleNamespace(
        open_by_key=lambda sid: fake_ss,
        set_timeout=lambda s: None,
    )
    monkeypatch.setattr(main_module, "sheets_client", lambda: fake_gspread_client)

    monkeypatch.setattr(main_module, "_queue_active_session", lambda *a, **kw: _fake_session())
    monkeypatch.setattr(main_module, "_queue_next_session", lambda *a, **kw: None)
    monkeypatch.setattr(main_module, "_queue_today_str", lambda ci: TODAY_STR)

    monkeypatch.setattr(main_module, "ensure_ci_passed",
                        lambda **kw: (SimpleNamespace(fio="Alice"), SimpleNamespace(passed=True)))

    monkeypatch.setattr(main_module, "_resolve_queue_team", lambda *a, **kw: None)

    fake_ctx = SimpleNamespace(
        spreadsheet=fake_ss,
        worksheet=fake_ss.worksheet("Очередь-" + TODAY_STR),
        grade_context=SimpleNamespace(
            current_cell_value="v",
            student_order=1,
            deadline=None,
            decimal_separator=".",
        ),
        row_index=3,
        lab_column=3,
        fio="Alice Student",
        current_cell_value="v",
    )
    monkeypatch.setattr(main_module, "_load_queue_sheet_context", lambda *a, **kw: fake_ctx)

    monkeypatch.setattr(main_module, "GitHubClient", lambda token: SimpleNamespace())
    monkeypatch.setattr(main_module, "_schedule_group_ids", lambda cid: ["4431", "4432"])

    return fake_ss


def _body(resp):
    return json.loads(resp.body)


class TestQueueLogin:
    def test_login_redirects_to_github(self, deps, monkeypatch):
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", "test")
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", "test")
        resp = main_module.queue_login(
            _mock_request(), "dev-course", "4431", "1"
        )
        assert resp.status_code == 307
        assert "github.com/login/oauth/authorize" in resp.headers["location"]


class TestQueueStatus:
    def test_without_cookie_returns_session_required(self, deps):
        data = main_module.queue_status(_mock_request(), "dev-course", "4431", "1")
        assert data["session_required"] is True
        assert data["in_queue"] is False
        assert data["queue_open"] is True

    def test_with_valid_cookie_returns_queue_info(self, deps):
        cookie = _make_student_cookie()
        data = main_module.queue_status(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data.get("session_required") is not True
        assert data["in_queue"] is False
        assert data["queue_open"] is True
        assert data["can_register"] is True

    def test_with_wrong_group_cookie_returns_session_required(self, deps):
        cookie = _make_student_cookie(group_id="4432")
        data = main_module.queue_status(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data["session_required"] is True

    def test_when_already_in_queue(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice Student", "ЛР1", "", STATUS_WAITING])

        cookie = _make_student_cookie()
        data = main_module.queue_status(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data["in_queue"] is True
        assert data["position"] == 1
        assert data["queue_size"] == 1

    def test_when_already_passed_in_history(self, deps):
        history = deps.worksheet("История_очереди")
        history.append_row(["2026-11-08", "1", "4431", "Alice Student", "ЛР1", "", STATUS_PASSED])

        cookie = _make_student_cookie()
        data = main_module.queue_status(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data["already_passed"] is True
        assert data["can_register"] is False

    def test_when_queue_closed(self, deps, monkeypatch):
        monkeypatch.setattr(main_module, "_queue_active_session", lambda *a, **kw: None)
        monkeypatch.setattr(main_module, "_queue_next_session", lambda *a, **kw: _fake_session())

        cookie = _make_student_cookie()
        data = main_module.queue_status(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data["queue_open"] is False
        assert data["can_register"] is False
        assert data["next_session"] is not None

    def test_team_lab_can_attempt_registration_with_server_side_ci_check(
        self, deps, monkeypatch
    ):
        course_info = _fake_course_info()
        lab_config = {**course_info["labs"]["1"], "team": {}}
        monkeypatch.setattr(
            main_module, "_resolve_queue_lab",
            lambda *args: (course_info, "1", lab_config),
        )
        monkeypatch.setattr(
            main_module, "_resolve_queue_team",
            lambda *args: SimpleNamespace(
                title="Team One", repo_name="lab1-team-1", members=["alice"]
            ),
        )

        data = main_module.queue_status(
            _mock_request({"student_session": _make_student_cookie()}),
            "dev-course", "4431", "1",
        )

        assert data["is_team_lab"] is True
        assert data["ci_status"] == "unknown"
        assert data["can_register"] is True


class TestQueueRegister:
    def test_register_success(self, deps):
        cookie = _make_student_cookie()
        data = main_module.queue_register(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data["position"] == 1
        assert data["queue_size"] == 1

    def test_without_cookie_returns_401(self, deps):
        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(_mock_request(), "dev-course", "4431", "1")
        assert exc.value.status_code == 401
        assert exc.value.detail == "SESSION_REQUIRED"

    def test_with_wrong_group_returns_401(self, deps):
        cookie = _make_student_cookie(group_id="4432")
        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
            )
        assert exc.value.status_code == 401

    def test_when_already_in_queue_returns_409(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice Student", "ЛР1", "", STATUS_WAITING])

        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "ALREADY_IN_QUEUE"

    def test_when_already_passed_returns_400(self, deps):
        history = deps.worksheet("История_очереди")
        history.append_row(["2026-11-08", "1", "4431", "Alice Student", "ЛР1", "", STATUS_PASSED])

        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "ALREADY_PASSED"

    def test_when_queue_closed_returns_403(self, deps, monkeypatch):
        monkeypatch.setattr(main_module, "_queue_active_session", lambda *a, **kw: None)

        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
            )
        assert exc.value.status_code == 403
        assert exc.value.detail == "QUEUE_CLOSED"

    def test_when_ci_fails_returns_400(self, deps, monkeypatch):
        from grading.queue.errors import QueueError

        def fail_ci(**kw):
            raise QueueError("CI_NOT_PASSED", "tests failed")

        monkeypatch.setattr(main_module, "ensure_ci_passed", fail_ci)

        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "CI_NOT_PASSED"

    def test_when_ci_pending_returns_409(self, deps, monkeypatch):
        from grading.queue.errors import QueueError

        def pending_ci(**kw):
            raise QueueError("CI_PENDING", "not ready")

        monkeypatch.setattr(main_module, "ensure_ci_passed", pending_ci)

        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "CI_PENDING"

    def test_team_registration_checks_ci_for_team_repository(self, deps, monkeypatch):
        course_info = _fake_course_info()
        lab_config = {**course_info["labs"]["1"], "team": {}}
        monkeypatch.setattr(
            main_module, "_resolve_queue_lab",
            lambda *args: (course_info, "1", lab_config),
        )
        monkeypatch.setattr(
            main_module, "_resolve_queue_team",
            lambda *args: SimpleNamespace(
                title="Team One", repo_name="lab1-team-1", members=["alice"]
            ),
        )
        monkeypatch.setattr(main_module, "LabGrader", lambda *_args: object())
        checked = {}

        def pass_ci(**kwargs):
            checked.update(kwargs)

        monkeypatch.setattr(main_module, "ensure_ci_passed", pass_ci)

        data = main_module.queue_register(
            _mock_request({"student_session": _make_student_cookie()}),
            "dev-course", "4431", "1",
        )

        assert checked["repo_name"] == "lab1-team-1"
        assert data["position"] == 1

    def test_team_registration_rejects_failed_ci(self, deps, monkeypatch):
        from grading.queue.errors import QueueError

        course_info = _fake_course_info()
        lab_config = {**course_info["labs"]["1"], "team": {}}
        monkeypatch.setattr(
            main_module, "_resolve_queue_lab",
            lambda *args: (course_info, "1", lab_config),
        )
        monkeypatch.setattr(
            main_module, "_resolve_queue_team",
            lambda *args: SimpleNamespace(
                title="Team One", repo_name="lab1-team-1", members=["alice"]
            ),
        )
        monkeypatch.setattr(main_module, "LabGrader", lambda *_args: object())
        monkeypatch.setattr(
            main_module, "ensure_ci_passed",
            lambda **_kwargs: (_ for _ in ()).throw(
                QueueError("CI_NOT_PASSED", "tests failed")
            ),
        )

        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(
                _mock_request({"student_session": _make_student_cookie()}),
                "dev-course", "4431", "1",
            )

        assert exc.value.status_code == 400
        assert exc.value.detail == "CI_NOT_PASSED"


class TestQueueWithdraw:
    def test_withdraw_success(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice Student", "ЛР1", "", STATUS_WAITING])

        cookie = _make_student_cookie()
        data = main_module.queue_withdraw(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data["status"] == "withdrawn"

    def test_without_cookie_returns_401(self, deps):
        with pytest.raises(HTTPException) as exc:
            main_module.queue_withdraw(_mock_request(), "dev-course", "4431", "1")
        assert exc.value.status_code == 401

    def test_when_not_in_queue_returns_404(self, deps):
        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_withdraw(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
            )
        assert exc.value.status_code == 404
        assert exc.value.detail == "NOT_IN_QUEUE"

    def test_when_already_withdrawn_returns_409(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice Student", "ЛР1", "", STATUS_WITHDRAWN])

        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_withdraw(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
            )
        assert exc.value.status_code == 409


class TestQueueShift:
    def test_shift_success(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice Student", "ЛР1", "", STATUS_WAITING])
        ws.append_row([2, "4431", "Bob Student", "ЛР1", "", STATUS_WAITING])

        cookie = _make_student_cookie()
        data = main_module.queue_shift(
            _post_request(json_body={"delta": 1},
                          cookies={"student_session": cookie}),
            "dev-course", "4431", "1",
            main_module.QueueShiftRequest(delta=1),
        )
        assert data["old_position"] == 1
        assert data["new_position"] == 2

    def test_without_cookie_returns_401(self, deps):
        with pytest.raises(HTTPException) as exc:
            main_module.queue_shift(
                _post_request(json_body={"delta": 1}),
                "dev-course", "4431", "1",
                main_module.QueueShiftRequest(delta=1),
            )
        assert exc.value.status_code == 401

    def test_when_not_in_queue_returns_404(self, deps):
        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_shift(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
                main_module.QueueShiftRequest(delta=1),
            )
        assert exc.value.status_code == 404

    def test_at_end_returns_400(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice Student", "ЛР1", "", STATUS_WAITING])

        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_shift(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "1",
                main_module.QueueShiftRequest(delta=1),
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "INVALID_SHIFT"


class TestQueueList:
    def test_list_returns_all_rows(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice", "ЛР1", "", STATUS_WAITING])
        ws.append_row([2, "4431", "Bob", "ЛР1", "", STATUS_WAITING])
        ws.append_row([1, "4431", "Alice", "ЛР1", "", STATUS_PASSED])

        cookie = _make_student_cookie()
        data = main_module.queue_list(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data["size"] == 3
        assert len(data["queue"]) == 3

    def test_without_any_cookie_returns_queue(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice", "ЛР1", "", STATUS_WAITING])
        data = main_module.queue_list(_mock_request(), "dev-course", "4431", "1")
        assert data["size"] == 1
        assert len(data["queue"]) == 1

    def test_with_admin_cookie_works(self, deps):
        admin_cookie = _make_admin_cookie()
        data = main_module.queue_list(
            _mock_request({"admin_session": admin_cookie}),
            "dev-course", "4431", "1",
        )
        assert data["size"] == 0

    def test_empty_queue(self, deps):
        cookie = _make_student_cookie()
        data = main_module.queue_list(
            _mock_request({"student_session": cookie}),
            "dev-course", "4431", "1",
        )
        assert data["size"] == 0
        assert data["queue"] == []


class TestQueueSetStatus:
    def test_set_status_success(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice Student", "ЛР1", "", STATUS_WAITING])

        admin_cookie = _make_admin_cookie()
        data = main_module.queue_set_status(
            _mock_request({"admin_session": admin_cookie}),
            "dev-course", "4431", "1",
            main_module.QueueSetStatusRequest(identifier="Alice Student", status="сдал"),
            admin="admin",
        )
        assert data["new_status"] == "сдал"

    def test_without_admin_returns_401(self, deps):
        with pytest.raises(HTTPException) as exc:
            main_module.queue_set_status(
                _mock_request(),
                "dev-course", "4431", "1",
                main_module.QueueSetStatusRequest(identifier="Alice", status="сдал"),
            )
        assert exc.value.status_code == 401

    def test_on_non_waiting_returns_409(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice", "ЛР1", "", STATUS_PASSED])

        admin_cookie = _make_admin_cookie()
        resp = main_module.queue_set_status(
            _mock_request({"admin_session": admin_cookie}),
            "dev-course", "4431", "1",
            main_module.QueueSetStatusRequest(identifier="Alice", status="не_сдал"),
            admin="admin",
        )
        assert resp.status_code == 409
        assert _body(resp)["detail"] == "NOT_WAITING"

    def test_non_existing_student_returns_404(self, deps):
        admin_cookie = _make_admin_cookie()
        resp = main_module.queue_set_status(
            _mock_request({"admin_session": admin_cookie}),
            "dev-course", "4431", "1",
            main_module.QueueSetStatusRequest(identifier="Ghost", status="сдал"),
            admin="admin",
        )
        assert resp.status_code == 404
        assert _body(resp)["detail"] == "NOT_IN_QUEUE"

    def test_invalid_status_returns_400(self, deps):
        ws = deps.worksheet("Очередь-" + TODAY_STR)
        ws.append_row([1, "4431", "Alice", "ЛР1", "", STATUS_WAITING])

        admin_cookie = _make_admin_cookie()
        resp = main_module.queue_set_status(
            _mock_request({"admin_session": admin_cookie}),
            "dev-course", "4431", "1",
            main_module.QueueSetStatusRequest(identifier="Alice", status="invalid"),
            admin="admin",
        )
        assert resp.status_code == 400
        assert _body(resp)["detail"] == "INVALID_STATUS"


class TestQueueSnapshot:
    def test_with_no_past_sheets_returns_zero(self, deps):
        admin_cookie = _make_admin_cookie()
        data = main_module.queue_snapshot(
            _mock_request({"admin_session": admin_cookie}),
            "dev-course",
            admin="admin",
        )
        assert data["moved_rows"] == 0

    def test_with_past_sheet_moves_rows(self, deps):
        past_ws = FakeWorksheet("Очередь-2026-11-08", [QUEUE_HEADERS])
        past_ws.append_row([1, "4431", "Past Student", "ЛР1", "", STATUS_WAITING])
        past_ws.id = 200
        deps._sheets["Очередь-2026-11-08"] = past_ws

        admin_cookie = _make_admin_cookie()
        data = main_module.queue_snapshot(
            _mock_request({"admin_session": admin_cookie}),
            "dev-course",
            admin="admin",
        )
        assert data["moved_rows"] == 1
        assert "Очередь-2026-11-08" not in deps._sheets
        history = deps.worksheet("История_очереди")
        rows = history.get_all_values()
        assert len(rows) == 2
        assert rows[1][3] == "Past Student"
        assert rows[1][6] == STATUS_NOT_REACHED


class TestQueueErrors:
    def test_unknown_lab_returns_404(self, deps, monkeypatch):
        monkeypatch.setattr(main_module, "get_course_by_id", lambda cid: {
            "labs": {}, "google": {}, "github": {}, "timezone": "UTC+3",
            "_meta": {"filename": "t.yaml", "status": "active", "priority": 0,
                       "logo": None, "featured": False},
        })
        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_status(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "999",
            )
        assert exc.value.status_code == 404

    def test_unknown_lab_on_register(self, deps, monkeypatch):
        monkeypatch.setattr(main_module, "get_course_by_id", lambda cid: {
            "labs": {}, "google": {}, "github": {}, "timezone": "UTC+3",
            "_meta": {"filename": "t.yaml", "status": "active", "priority": 0,
                       "logo": None, "featured": False},
        })
        cookie = _make_student_cookie()
        with pytest.raises(HTTPException) as exc:
            main_module.queue_register(
                _mock_request({"student_session": cookie}),
                "dev-course", "4431", "999",
            )
        assert exc.value.status_code == 404
