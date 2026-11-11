"""
Tests for the defense queue endpoints in main.py (issue #62).

The queue logic itself is tested in test_defense_queue.py; here it is the
HTTP layer: who the student is (only the signed cookie), the GitHub sign-in
through the shared /join/callback, codes -> statuses, the teacher's routes,
rate limits and Origin. Requests go through TestClient against a real
courses/ directory and a real SQLite file under tmp_path; Google Sheets is a
read-only fake that counts how often it is opened, GitHub OAuth is mocked
with `responses`, time is set through main._queue_now.
"""
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import gspread
import pytest
import responses
import yaml
from fastapi import Response
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module
from grading.defense_queue import DefenseQueue
from grading.queue_roster import reset_roster_cache
from grading.schedule import reset_schedule_caches
from grading.schedule.manual import ManualSource
from main import app

MSK = timezone(timedelta(hours=3))
COURSE = "os-queue"
FRONT = "https://front.example.com"
# Понедельник, 15:20 по Москве: идёт пара 4431 (15:10-16:40), запись на
# пару 4432 (17:00-18:30) откроется в 16:45.
MONDAY_1520 = datetime(2026, 11, 9, 15, 20, tzinfo=MSK)
GUAP_PAGE = Path(__file__).parent / "fixtures" / "guap" / "prep_polyak_39614.html"
# До подмены фикстурой sheets: настоящая фабрика клиента Sheets для очереди.
QUEUE_SHEETS_CLIENT = main_module.queue_sheets_client


# ---------------------------------------------------------------------------
# Courses, spreadsheet, clock
# ---------------------------------------------------------------------------

LABS = {
    "1": {"github-prefix": "os-task1", "short-name": "ЛР1"},
    "2": {"github-prefix": "os-task2", "short-name": "ЛР2"},
    "3": {"github-prefix": "os-task3", "short-name": "ЛР3"},
    # Без short-name у работы нет столбца в таблице - в очередь она не попадает.
    "4": {"github-prefix": "os-task4"},
    # Ещё не опубликована: не видна ни в списке, ни для записи.
    "5": {"github-prefix": "os-task5", "short-name": "ЛР5", "join": {"opens-at": "2027-01-01 10:00"}},
}

MANUAL_SCHEDULE = {
    "source": "manual",
    "lessons": [
        {"groups": ["4431"], "weekday": "mon", "start": "15:10", "end": "16:40"},
        {"groups": ["4432"], "weekday": "mon", "start": "17:00", "end": "18:30"},
    ],
}


def course_yaml(queue=None, timezone_str="UTC+3"):
    course = {
        "name": "Операционные системы",
        "timezone": timezone_str,
        "github": {"organization": "suai-os"},
        "google": {"spreadsheet": "sheet-os", "info-sheet": "График", "student-name-column": 1},
        "labs": LABS,
    }
    if queue is not None:
        course["queue"] = queue
    return {"course": course}


COURSES = {
    "os-queue": ("active", course_yaml({"enabled": True, "open-before-minutes": 15, "schedule": MANUAL_SCHEDULE})),
    "os-allday": ("active", course_yaml({"enabled": True})),
    "os-off": ("active", course_yaml()),
    "os-archived": ("archived", course_yaml({"enabled": True})),
    "os-broken": ("active", course_yaml({"enabled": True, "open-before-minutes": "15"})),
    "os-guap": ("active", course_yaml({
        "enabled": True,
        "schedule": {"source": "guap", "teacher-id": 39614, "subject": "Операционные системы"},
    })),
}


def add_course(directory, course_id, data, status="active"):
    """One more course in the courses/ directory of the test."""
    (directory / f"{course_id}.yaml").write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    index_path = directory / "index.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    index["courses"].append({"id": course_id, "file": f"{course_id}.yaml", "status": status, "priority": 0})
    index_path.write_text(yaml.dump(index, allow_unicode=True), encoding="utf-8")


@pytest.fixture
def courses_dir(tmp_path, monkeypatch):
    directory = tmp_path / "courses"
    directory.mkdir()
    entries = []
    for course_id, (status, data) in COURSES.items():
        (directory / f"{course_id}.yaml").write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        entries.append({"id": course_id, "file": f"{course_id}.yaml", "status": status, "priority": 0})
    index = directory / "index.yaml"
    index.write_text(yaml.dump({"courses": entries}, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(main_module, "COURSES_DIR", str(directory))
    monkeypatch.setattr(main_module, "INDEX_FILE", str(index))
    return directory


def group_grid(*students):
    grid = [
        ["№", "ФИО", "GitHub", "01.10.2026", "15.10.2026", "01.11.2026"],
        ["", "", "", "ЛР1", "ЛР2", "ЛР3"],
    ]
    for num, (name, github, cells) in enumerate(students, start=1):
        grid.append([str(num), name, github, *cells])
    return grid


def course_sheets():
    return {
        "4431": group_grid(
            ("Алексеева Анна", "alice", ["v", "x", "vv"]),
            ("Борисов Борис", "bob", ["v-1", "?1", ""]),
            ("Васильева Вера", "carol", ["v@10", "", ""]),
            ("Егоров Егор", "erin", ["X", "н/я", "-"]),
        ),
        "4432": group_grid(("Дмитриев Дмитрий", "dave", ["v", "v", ""])),
        "График": [["Расписание защит"]],
    }


class FakeSheets:
    """
    Read-only stand-in for the gspread client.

    `created` counts client_factory() calls - every lookup that actually
    goes to Google Sheets. There are no write methods at all.
    """

    def __init__(self, sheets):
        self.sheets = sheets
        self.created = 0
        self.fail = False

    def factory(self):
        self.created += 1
        if self.fail:
            raise ConnectionError("Google Sheets is down")
        return self

    def open_by_key(self, key):
        assert key == "sheet-os"
        return self

    def worksheets(self):
        return [type("Worksheet", (), {"title": title})() for title in self.sheets]

    def values_batch_get(self, ranges, params=None):
        by_range = {gspread.utils.absolute_range_name(t): grid for t, grid in self.sheets.items()}
        # Копии: тест может «поменять ячейку» в таблице, не задев кэш.
        return {"valueRanges": [{"range": r, "values": [list(row) for row in by_range[r]]} for r in ranges]}


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def sheets(monkeypatch):
    fake = FakeSheets(course_sheets())
    monkeypatch.setattr(main_module, "queue_sheets_client", fake.factory)
    return fake


@pytest.fixture
def clock(monkeypatch):
    fake = Clock(MONDAY_1520)
    monkeypatch.setattr(main_module, "_queue_now", fake)
    return fake


@pytest.fixture(autouse=True)
def queue_env(request, monkeypatch, courses_dir, sheets, clock):
    """
    OAuth settings and the limiter switched off, as in test_admin_endpoints.py.

    The tests marked rate_limit keep the limiter, starting from an empty
    storage so that other test modules do not eat into the budget.
    """
    monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", "test_client_id")
    monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setattr(main_module, "FRONTEND_URL", FRONT)
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", None)
    monkeypatch.setattr(main_module, "GITHUB_OAUTH_CALLBACK_URL", "https://backend.example.com/join/callback")
    if request.node.get_closest_marker("rate_limit"):
        main_module.limiter.reset()
        yield
        main_module.limiter.reset()
    else:
        monkeypatch.setattr(main_module.limiter, "enabled", False)
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STUDENTS = {
    "alice": ("Алексеева Анна", "4431"),
    "bob": ("Борисов Борис", "4431"),
    "carol": ("Васильева Вера", "4431"),
    "erin": ("Егоров Егор", "4431"),
    "dave": ("Дмитриев Дмитрий", "4432"),
}


def session_cookie(github, course_id=COURSE, group=None, name=None, expires=None, **overrides):
    """A queue cookie exactly as the callback signs it."""
    default_name, default_group = STUDENTS.get(github, ("Кто-то", "4431"))
    end = expires if expires is not None else int(datetime(2026, 11, 10, tzinfo=MSK).timestamp())
    payload = {"u": github, "c": course_id, "g": group or default_group, "n": name or default_name, "e": end}
    payload.update(overrides)
    return main_module.queue_session_signer.sign(main_module._encode_payload(payload)).decode()


def student(github, course_id=COURSE, **cookie):
    client = TestClient(app)
    client.cookies.set(main_module._queue_cookie_name(course_id), session_cookie(github, course_id, **cookie))
    return client


def admin(**kwargs):
    client = TestClient(app, **kwargs)
    client.cookies.set("admin_session", main_module.signer.sign(main_module.ADMIN_LOGIN.encode()).decode())
    return client


def signed_at(moment, signer, value=None):
    """`value` (the admin login by default) signed by `signer` at the Unix time `moment`."""
    value = main_module.ADMIN_LOGIN if value is None else value
    with patch("itsdangerous.timed.time.time", return_value=moment):
        return signer.sign(value.encode()).decode()


def admin_signed_in(session_age, start_age=None):
    """A teacher whose admin_session is `session_age` s old, signed in by password `start_age` s ago."""
    now = time.time()
    client = TestClient(app)
    client.cookies.set("admin_session", signed_at(now - session_age, main_module.signer))
    if start_age is not None:
        client.cookies.set(
            main_module.ADMIN_SESSION_START_COOKIE, signed_at(now - start_age, main_module.admin_start_signer)
        )
    return client


def queue_url(tail="", course_id=COURSE):
    return f"/courses/{course_id}/queue{tail}"


def admin_url(tail="", course_id=COURSE):
    return f"/admin/courses/{course_id}/queue{tail}"


def session_for(state, group):
    return next(s for s in state["sessions"] if s["groups"] == [group])


def sign_up(client, session_id, lab="1", course_id=COURSE, **extra):
    return client.post(queue_url("/entries", course_id), json={"session_id": session_id, "lab_id": lab, **extra})


def current_session_id(group="4431"):
    return session_for(TestClient(app).get(queue_url()).json(), group)["id"]


def qs(location):
    return parse_qs(urlparse(location).query)


def set_cookies(response):
    return response.headers.get_list("set-cookie")


def cookie_values(response):
    """{name: value} of the cookies a response sets."""
    return dict(c.split(";", 1)[0].split("=", 1) for c in set_cookies(response))


def github_oauth(rsps, login):
    rsps.add(responses.POST, "https://github.com/login/oauth/access_token", json={"access_token": "gho_test"})
    rsps.add(responses.GET, "https://api.github.com/user", json={"login": login})


def oauth_login(client, github, lab=None, course_id=COURSE):
    """Sign in the way the browser does: /queue/login -> GitHub -> /join/callback."""
    start = client.get(queue_url("/login", course_id), params={"lab": lab} if lab else {}, follow_redirects=False)
    assert start.status_code == 307, start.text
    state = qs(start.headers["location"])["state"][0]
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        github_oauth(rsps, github)
        return client.get("/join/callback", params={"code": "code", "state": state}, follow_redirects=False)


# ---------------------------------------------------------------------------
# Student: who is asking
# ---------------------------------------------------------------------------

class TestStudentIdentity:
    def test_anonymous_sees_counters_without_names(self):
        alice = student("alice")
        sign_up(alice, current_session_id())

        state = TestClient(app).get(queue_url()).json()
        session = session_for(state, "4431")
        assert state["viewer"] is None
        assert state["my_entry"] is None
        assert session["waiting_count"] == 1
        assert session["entries"] is None

    def test_signed_in_student_sees_the_queue_of_their_group(self):
        alice = student("alice")
        state = alice.get(queue_url()).json()
        assert state["viewer"] == {"github": "alice", "name": "Алексеева Анна", "group": "4431"}
        assert session_for(state, "4431")["for_me"] is True
        assert session_for(state, "4432")["for_me"] is False
        assert session_for(state, "4432")["entries"] is None

    def test_login_in_the_body_is_ignored(self):
        """The body may name anybody - the entry is still the cookie's student."""
        alice = student("alice")
        response = sign_up(alice, current_session_id(), github="bob", username="bob", login="bob")
        assert response.status_code == 200, response.text
        assert response.json()["my_entry"]["lab"] == "ЛР1"

        bob_state = student("bob").get(queue_url()).json()
        assert bob_state["my_entry"] is None
        assert [e["name"] for e in session_for(bob_state, "4431")["entries"]] == ["Алексеева Анна"]

    def test_group_comes_from_the_spreadsheet_not_from_the_cookie(self):
        """dave is in 4432 in the sheet; a cookie claiming 4431 does not let him in."""
        dave = student("dave", group="4431")
        response = sign_up(dave, current_session_id("4431"))
        assert response.status_code == 403
        assert response.json() == {"detail": "NOT_YOUR_GROUP"}

    def test_without_cookie_actions_need_a_session(self):
        client = TestClient(app)
        assert client.post(queue_url("/leave")).json() == {"detail": "SESSION_REQUIRED"}
        assert client.post(queue_url("/leave")).status_code == 401
        assert client.get(queue_url("/labs")).status_code == 401
        assert sign_up(client, current_session_id()).status_code == 401

    def test_forged_cookie_is_anonymous(self):
        client = TestClient(app)
        client.cookies.set(main_module._queue_cookie_name(COURSE), session_cookie("alice") + "x")
        assert client.get(queue_url()).json()["viewer"] is None
        assert client.post(queue_url("/leave")).status_code == 401

    def test_cookie_signed_with_another_salt_is_rejected(self):
        """A /join state or the admin cookie signer cannot mint a queue session."""
        payload = main_module._encode_payload({"u": "alice", "c": COURSE, "g": "4431", "n": "A", "e": 2**40})
        client = TestClient(app)
        client.cookies.set(main_module._queue_cookie_name(COURSE), main_module.signer.sign(payload).decode())
        assert client.get(queue_url()).json()["viewer"] is None

    def test_cookie_of_another_course_does_not_work_here(self):
        client = TestClient(app)
        # Cookie с именем этого курса, но выданная для другого.
        client.cookies.set(main_module._queue_cookie_name(COURSE), session_cookie("alice", course_id="os-allday"))
        assert client.get(queue_url()).json()["viewer"] is None
        assert client.post(queue_url("/leave")).status_code == 401

    def test_cookie_expires_at_the_end_of_the_day(self, clock):
        alice = student("alice")
        assert alice.get(queue_url()).json()["viewer"] is not None
        clock.now = datetime(2026, 11, 10, 0, 0, tzinfo=MSK)
        assert alice.get(queue_url()).json()["viewer"] is None
        assert alice.post(queue_url("/leave")).status_code == 401

    def test_signature_older_than_a_day_is_rejected(self):
        with patch("itsdangerous.timed.time.time", return_value=time.time() - 25 * 3600):
            client = student("alice", expires=2**40)
        assert client.get(queue_url()).json()["viewer"] is None

    def test_two_courses_two_cookies(self):
        """A student of two courses stands in both queues independently."""
        client = TestClient(app)
        client.cookies.set(main_module._queue_cookie_name(COURSE), session_cookie("alice"))
        client.cookies.set(main_module._queue_cookie_name("os-allday"), session_cookie("alice", course_id="os-allday"))

        assert sign_up(client, current_session_id()).status_code == 200
        all_day = client.get(queue_url(course_id="os-allday")).json()
        assert sign_up(client, all_day["sessions"][0]["id"], course_id="os-allday").status_code == 200

        assert client.get(queue_url()).json()["my_entry"]["status"] == "waiting"
        assert client.get(queue_url(course_id="os-allday")).json()["my_entry"]["status"] == "waiting"

    @pytest.mark.parametrize("ids", [("ос-2026", "мп-2026"), ("os 2026", "os_2026"), ("os_2026", "os~2026")])
    def test_cookie_names_of_two_courses_differ(self, ids):
        first, second = (main_module._queue_cookie_name(course_id) for course_id in ids)
        assert first != second
        oauth = {main_module._queue_oauth_cookie_name(course_id) for course_id in ids}
        assert len(oauth) == 2 and not oauth & {first, second}

    def test_plain_course_id_keeps_its_cookie_name(self):
        assert main_module._queue_cookie_name("os-2026.v_1") == "queue_session_os-2026.v_1"
        assert main_module._queue_oauth_cookie_name("os-2026.v_1") == "queue_oauth_os-2026.v_1"

    def test_two_cyrillic_courses_keep_their_sign_ins(self, courses_dir):
        """«ос-2026» and «мп-2026» get different cookies: signing in to one keeps the other."""
        data = course_yaml({"enabled": True, "schedule": MANUAL_SCHEDULE})
        add_course(courses_dir, "ос-2026", data)
        add_course(courses_dir, "мп-2026", data)
        name = main_module._queue_cookie_name("ос-2026")
        assert name.startswith("queue_session~") and name.isascii()

        browser = TestClient(app)
        assert qs(oauth_login(browser, "alice", course_id="ос-2026").headers["location"])["status"] == ["ok"]
        assert qs(oauth_login(browser, "alice", course_id="мп-2026").headers["location"])["status"] == ["ok"]
        for course_id in ("ос-2026", "мп-2026"):
            assert browser.get(queue_url(course_id=course_id)).json()["viewer"]["github"] == "alice"

        assert browser.post(queue_url("/logout", "мп-2026")).status_code == 200
        assert browser.get(queue_url(course_id="мп-2026")).json()["viewer"] is None
        assert browser.get(queue_url(course_id="ос-2026")).json()["viewer"]["github"] == "alice"

    def test_logout_deletes_the_course_cookie(self):
        alice = TestClient(app)
        oauth_login(alice, "alice")
        assert alice.get(queue_url()).json()["viewer"] is not None
        response = alice.post(queue_url("/logout"))
        assert response.status_code == 200
        cookie = next(c for c in set_cookies(response) if c.startswith(f"{main_module._queue_cookie_name(COURSE)}="))
        assert "Path=/" in cookie and "Max-Age=0" in cookie
        assert alice.get(queue_url()).json()["viewer"] is None


# ---------------------------------------------------------------------------
# Student: signing up
# ---------------------------------------------------------------------------

class TestSignUp:
    @pytest.mark.parametrize("github,lab,code", [
        ("alice", "2", "LAB_NOT_PASSED"),        # x
        ("bob", "2", "LAB_NOT_PASSED"),          # ?1
        ("bob", "3", "LAB_NOT_PASSED"),          # пусто
        ("erin", "1", "LAB_NOT_PASSED"),         # X - ручная пометка, не проверка сервисом
        ("erin", "2", "LAB_NOT_PASSED"),         # н/я
        ("alice", "3", "LAB_ALREADY_DEFENDED"),  # vv
    ])
    def test_lab_must_have_passed_the_check(self, github, lab, code):
        response = sign_up(student(github), current_session_id(), lab=lab)
        assert response.status_code == 409
        assert response.json() == {"detail": code}

    @pytest.mark.parametrize("github,lab", [("alice", "1"), ("bob", "1"), ("carol", "1"), ("bob", "ЛР1")])
    def test_passed_cells_are_accepted(self, github, lab):
        """v, v-1, v@10; the lab may be named by its key or its short name."""
        response = sign_up(student(github), current_session_id(), lab=lab)
        assert response.status_code == 200, response.text
        entry = response.json()["my_entry"]
        assert entry["status"] == "waiting" and entry["place"] == 1 and entry["lab"] == "ЛР1"

    @pytest.mark.parametrize("lab", ["99", "4", "5"])
    def test_unknown_unnamed_or_unpublished_lab(self, lab):
        response = sign_up(student("alice"), current_session_id(), lab=lab)
        assert response.status_code == 404
        assert response.json() == {"detail": "LAB_NOT_FOUND"}

    def test_double_sign_up(self):
        alice = student("alice")
        assert sign_up(alice, current_session_id()).status_code == 200
        response = sign_up(alice, current_session_id())
        assert response.status_code == 409
        assert response.json() == {"detail": "ALREADY_IN_QUEUE"}

    def test_two_parallel_sign_ups_make_one_entry(self):
        sid = current_session_id()
        barrier = threading.Barrier(2)
        codes = []

        def run():
            client = student("alice")
            barrier.wait()
            codes.append(sign_up(client, sid).status_code)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes) == [200, 409]
        assert session_for(student("bob").get(queue_url()).json(), "4431")["waiting_count"] == 1

    def test_upcoming_session_does_not_accept_yet(self):
        response = sign_up(student("dave"), current_session_id("4432"))
        assert response.status_code == 409
        assert response.json() == {"detail": "SIGNUP_CLOSED"}

    def test_unknown_session(self):
        response = sign_up(student("alice"), 12345)
        assert response.status_code == 404
        assert response.json() == {"detail": "SESSION_NOT_FOUND"}

    def test_fresh_grade_is_seen_after_one_reread(self, sheets):
        """The cached copy says x, the sheet already has v: one fresh read lets the student in."""
        alice = student("alice")
        assert alice.get(queue_url("/labs")).status_code == 200      # копия таблицы в кэше
        sheets.sheets["4431"][2][4] = "v"                              # ЛР2 только что проверена
        with patch("grading.queue_roster.ROSTER_FRESH_AFTER_SECONDS", 0):
            response = sign_up(alice, current_session_id(), lab="2")
        assert response.status_code == 200, response.text
        assert sheets.created == 2

    def test_sheets_down_refuses_with_503(self, sheets):
        sheets.fail = True
        response = sign_up(student("alice"), current_session_id())
        assert response.status_code == 503
        assert response.json() == {"detail": "SHEETS_UNAVAILABLE"}

    def test_student_missing_from_the_sheet(self):
        response = sign_up(student("mallory"), current_session_id())
        assert response.status_code == 403
        assert response.json() == {"detail": "STUDENT_NOT_FOUND"}

    def test_labs_list(self, sheets):
        response = student("alice").get(queue_url("/labs"))
        assert response.status_code == 200
        data = response.json()
        assert data["labs"] == [
            {"id": "1", "short_name": "ЛР1", "status": "passed"},
            {"id": "2", "short_name": "ЛР2", "status": "not_passed"},
            {"id": "3", "short_name": "ЛР3", "status": "defended"},
        ]
        assert data["fetched_at"] is not None

    def test_lab_defended_through_the_queue_is_defended_in_the_list(self):
        alice = student("alice")
        sign_up(alice, current_session_id())
        teacher = admin()
        sid = current_session_id()
        entry = teacher.post(admin_url(f"/sessions/{sid}/call-next"), json={}).json()["entry"]
        teacher.post(admin_url(f"/entries/{entry['id']}/outcome"), json={"outcome": "defended"})

        labs = {lab["id"]: lab["status"] for lab in alice.get(queue_url("/labs")).json()["labs"]}
        assert labs["1"] == "defended"
        response = sign_up(alice, sid, lab="1")
        assert response.json() == {"detail": "LAB_ALREADY_DEFENDED"}

    def test_leave_and_skip_codes(self):
        alice, bob = student("alice"), student("bob")
        assert alice.post(queue_url("/leave")).json() == {"detail": "NOT_IN_QUEUE"}
        sign_up(alice, current_session_id())
        response = alice.post(queue_url("/skip"))
        assert response.status_code == 409
        assert response.json() == {"detail": "NOTHING_TO_SKIP"}
        sign_up(bob, current_session_id())
        assert alice.post(queue_url("/skip")).json()["my_entry"]["place"] == 2
        assert bob.get(queue_url()).json()["my_entry"]["place"] == 1


# ---------------------------------------------------------------------------
# Polling and course state
# ---------------------------------------------------------------------------

class TestPolling:
    def test_polling_never_opens_google_sheets(self, sheets):
        alice = student("alice")
        sign_up(alice, current_session_id())
        sheets.created = 0

        for _ in range(5):
            assert alice.get(queue_url()).status_code == 200
            assert TestClient(app).get(queue_url()).status_code == 200
            assert admin().get(admin_url()).status_code == 200
        assert sheets.created == 0

    def test_state_shape(self):
        state = student("alice").get(queue_url()).json()
        assert state["course_id"] == COURSE
        assert state["utc_offset_minutes"] == 180
        assert state["day"] == "2026-11-09"
        assert state["now"] == "2026-11-09T12:20:00+00:00"
        assert state["schedule"] == {"source": "manual", "state": "ok", "fetched_at": None, "error": None}
        session = session_for(state, "4431")
        assert session["signup"] == "open"
        assert session["signup_opens_at"] == "2026-11-09T11:55:00+00:00"   # 14:55 МСК
        assert session["signup_closes_at"] == "2026-11-09T13:40:00+00:00"  # 16:40 МСК
        assert session_for(state, "4432")["signup"] == "upcoming"

    def test_course_without_schedule_has_one_queue_for_the_day(self):
        state = student("dave", course_id="os-allday").get(queue_url(course_id="os-allday")).json()
        assert [s["kind"] for s in state["sessions"]] == ["all_day"]
        assert state["sessions"][0]["for_me"] is True
        assert state["schedule"]["source"] == "all_day"

    @pytest.mark.parametrize("course_id", ["os-off", "os-archived"])
    def test_queue_disabled(self, course_id):
        client = student("alice", course_id=course_id)
        for response in (client.get(queue_url(course_id=course_id)), client.post(queue_url("/leave", course_id))):
            assert response.status_code == 404
            assert response.json() == {"detail": "QUEUE_DISABLED"}

    def test_unknown_course(self):
        response = TestClient(app).get(queue_url(course_id="nope"))
        assert response.status_code == 404
        assert response.json() == {"detail": "COURSE_NOT_FOUND"}

    def test_broken_config_is_a_stable_code(self):
        response = TestClient(app).get(queue_url(course_id="os-broken"))
        assert response.status_code == 400
        assert response.json() == {"detail": "QUEUE_MISCONFIGURED"}

    def test_storage_unavailable_is_503(self, tmp_path, monkeypatch):
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("")
        monkeypatch.setattr(main_module, "QUEUE_DB_PATH", str(blocker / "queue.sqlite3"))
        monkeypatch.setattr(main_module, "_QUEUE_STORE", None)
        response = TestClient(app).get(queue_url())
        assert response.status_code == 503
        assert response.json() == {"detail": "STORAGE_UNAVAILABLE"}

    def test_queue_survives_a_restart(self):
        """A backend restart mid-lesson: the queue is in the file, not in memory."""
        alice = student("alice")
        sign_up(alice, current_session_id())
        main_module._QUEUE_STORE = None
        reset_roster_cache()
        reset_schedule_caches()
        assert alice.get(queue_url()).json()["my_entry"]["place"] == 1

    def test_repeated_poll_is_one_read_and_no_write(self, monkeypatch):
        """The day's sessions are created by the first poll; after that a poll only reads, once."""
        alice = student("alice")
        sign_up(alice, current_session_id())
        store = main_module._queue_store()
        calls = []
        for name in ("read", "transaction"):
            original = getattr(store, name)
            monkeypatch.setattr(store, name, lambda *a, _o=original, _n=name, **k: (calls.append(_n), _o(*a, **k))[1])

        for client, url in ((TestClient(app), queue_url()), (alice, queue_url()), (admin(), admin_url())):
            calls.clear()
            assert client.get(url).status_code == 200
            assert calls == ["read"], url

    def test_failing_schedule_source_leaves_the_sessions_as_they_are(self, monkeypatch, caplog):
        """Any error of the source, not only ScheduleUnavailable: nothing is created or closed."""
        sid = current_session_id()

        class Broken:
            name = "manual"

            def __init__(self, inner):
                self.inner = inner

            def lessons_on(self, day, now):
                raise RuntimeError("parser bug")

            def status(self, now):
                return self.inner.status(now)

        original = main_module._schedule_source
        monkeypatch.setattr(main_module, "_schedule_source", lambda settings: Broken(original(settings)))
        response = student("alice").get(queue_url())
        assert response.status_code == 200
        assert session_for(response.json(), "4431")["signup"] == "open"
        assert sign_up(student("alice"), sid).status_code == 200
        day = admin().get(admin_url())
        assert day.status_code == 200
        assert session_for(day.json(), "4431")["in_schedule"] is True
        assert "ошибка источника расписания" in caplog.text

        # У курса, где сессий дня ещё нет, они и не появляются.
        assert TestClient(app).get(queue_url(course_id="os-allday")).json()["sessions"] == []

    def test_course_lists_report_queue_enabled(self):
        listed = {c["id"]: c["queue_enabled"] for c in TestClient(app).get("/courses?status=all").json()}
        assert listed["os-queue"] is True
        assert listed["os-off"] is False
        assert listed["os-archived"] is True
        assert TestClient(app).get("/courses/os-queue").json()["queue_enabled"] is True
        assert TestClient(app).get("/courses/os-off").json()["queue_enabled"] is False

    @pytest.mark.parametrize("course_id,queue,configured", [
        ("os-queue", None, True),
        ("os-off", None, False),
        ("os-disabled", {"enabled": False, "schedule": MANUAL_SCHEDULE}, True),
        ("os-typo", {"enabled": "true"}, True),
        ("os-list", ["enabled"], False),
    ])
    def test_course_lists_report_queue_configured(self, courses_dir, course_id, queue, configured):
        """The teacher's «Очередь» button: the history stays reachable when the queue is off."""
        if queue is not None:
            add_course(courses_dir, course_id, course_yaml(queue))
        listed = {c["id"]: c for c in TestClient(app).get("/courses?status=all").json()}
        single = TestClient(app).get(f"/courses/{course_id}").json()
        for course in (listed[course_id], single):
            assert course["queue_configured"] is configured
        if queue is not None:
            assert listed[course_id]["queue_enabled"] is False and single["queue_enabled"] is False


class TestGuapSchedule:
    """The real teacher page of GUAP behind the queue (no network: `responses`)."""

    def test_sessions_from_the_site_and_from_the_saved_copy_after_restart(self, clock):
        html = GUAP_PAGE.read_bytes()
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, "https://guap.ru/rasp?pr=39614", body=html)
            state = student("alice", course_id="os-guap").get(queue_url(course_id="os-guap")).json()
        assert state["schedule"]["state"] == "ok"
        assert sorted(s["groups"][0] for s in state["sessions"]) == ["4431", "4434"]
        assert session_for(state, "4431")["signup"] == "open"

        # Рестарт и сайт недоступен: работаем по копии из базы.
        main_module._QUEUE_STORE = None
        reset_schedule_caches()
        clock.now = MONDAY_1520 + timedelta(minutes=40)
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, "https://guap.ru/rasp?pr=39614", status=503)
            state = TestClient(app).get(queue_url(course_id="os-guap")).json()
        assert state["schedule"]["state"] == "stale"
        assert state["schedule"]["error"] == "FETCH_FAILED"
        assert session_for(state, "4431")["signup"] == "open"

    def test_site_down_without_copy_leaves_manual_opening(self):
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, "https://guap.ru/rasp?pr=39614", status=503)
            state = TestClient(app).get(queue_url(course_id="os-guap")).json()
            assert state["sessions"] == []
            assert state["schedule"]["state"] == "unavailable"

            teacher = admin()
            response = teacher.post(admin_url("/sessions", "os-guap"), json={"groups": ["4431"], "closes_at": None})
        assert response.status_code == 200, response.text
        assert response.json()["session"]["kind"] == "manual"
        assert response.json()["schedule"]["state"] == "unavailable"


# ---------------------------------------------------------------------------
# Sign-in through GitHub (/queue/login -> /join/callback)
# ---------------------------------------------------------------------------

class TestQueueSignIn:
    def test_login_redirects_to_github_with_a_queue_state(self):
        response = TestClient(app).get(queue_url("/login"), params={"lab": "ЛР1"}, follow_redirects=False)
        assert response.status_code == 307
        location = urlparse(response.headers["location"])
        assert (location.netloc, location.path) == ("github.com", "/login/oauth/authorize")
        params = qs(response.headers["location"])
        assert params["redirect_uri"] == ["https://backend.example.com/join/callback"]
        assert params["scope"] == ["read:user"]
        oauth_cookie = next(c for c in set_cookies(response) if c.startswith("queue_oauth_os-queue="))
        assert "HttpOnly" in oauth_cookie and "Path=/" in oauth_cookie and "Max-Age=600" in oauth_cookie

    def test_full_sign_in(self):
        client = TestClient(app)
        response = oauth_login(client, "Alice", lab="ЛР1")

        assert response.status_code == 307
        assert response.headers["location"] == f"{FRONT}/course/{COURSE}/queue?status=ok&lab=%D0%9B%D0%A01"
        cookie = next(c for c in set_cookies(response) if c.startswith("queue_session_os-queue="))
        assert "Path=/" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
        assert any(c.startswith("queue_oauth_os-queue=") and "Max-Age=0" in c for c in set_cookies(response))

        # Логин из GitHub с другим регистром - тот же студент таблицы.
        viewer = client.get(queue_url()).json()["viewer"]
        assert viewer == {"github": "Alice", "name": "Алексеева Анна", "group": "4431"}

    def test_cookie_lives_until_the_end_of_the_course_day(self):
        response = oauth_login(TestClient(app), "alice")
        cookie = next(c for c in set_cookies(response) if c.startswith("queue_session_os-queue="))
        # 15:20 -> 24:00 по Москве
        assert f"Max-Age={8 * 3600 + 40 * 60}" in cookie

    def test_nonce_must_match_the_browser(self):
        start = TestClient(app).get(queue_url("/login"), follow_redirects=False)
        state = qs(start.headers["location"])["state"][0]
        other_browser = TestClient(app)
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            github_oauth(rsps, "alice")
            response = other_browser.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert qs(response.headers["location"])["reason"] == ["state_mismatch"]
        assert not any(c.startswith("queue_session_") for c in set_cookies(response))

    def test_sign_ins_to_two_courses_at_once(self):
        """Two tabs: both logins started before either callback; each nonce is of its course."""
        client = TestClient(app)
        states = {}
        for course_id in (COURSE, "os-allday"):
            start = client.get(queue_url("/login", course_id), follow_redirects=False)
            states[course_id] = qs(start.headers["location"])["state"][0]
        for course_id, state in states.items():
            with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
                github_oauth(rsps, "alice")
                response = client.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
            assert qs(response.headers["location"])["status"] == ["ok"], course_id
        for course_id in states:
            assert client.get(queue_url(course_id=course_id)).json()["viewer"]["github"] == "alice"

    def test_nonce_of_another_course_does_not_fit(self):
        client = TestClient(app)
        state = qs(client.get(queue_url("/login"), follow_redirects=False).headers["location"])["state"][0]
        nonce = client.cookies.get(main_module._queue_oauth_cookie_name(COURSE))
        other = TestClient(app)
        other.cookies.set(main_module._queue_oauth_cookie_name("os-allday"), nonce)
        with responses.RequestsMock():                       # GitHub не спрашивают
            response = other.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert qs(response.headers["location"])["reason"] == ["state_mismatch"]

    def test_expired_state_lands_on_the_queue_page(self):
        client = TestClient(app)
        with patch("itsdangerous.timed.time.time", return_value=time.time() - main_module.JOIN_STATE_MAX_AGE - 10):
            start = client.get(queue_url("/login"), follow_redirects=False)
        state = qs(start.headers["location"])["state"][0]
        response = client.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert response.headers["location"] == f"{FRONT}/course/{COURSE}/queue?status=error&reason=state_expired"

    @pytest.mark.parametrize("params,reason", [
        ({"error": "access_denied"}, "access_denied"),
        ({}, "missing_code"),
    ])
    def test_github_refusals(self, params, reason):
        client = TestClient(app)
        start = client.get(queue_url("/login"), follow_redirects=False)
        state = qs(start.headers["location"])["state"][0]
        response = client.get("/join/callback", params={"state": state, **params}, follow_redirects=False)
        assert qs(response.headers["location"])["reason"] == [reason]

    def test_failed_code_exchange(self):
        client = TestClient(app)
        start = client.get(queue_url("/login"), follow_redirects=False)
        state = qs(start.headers["location"])["state"][0]
        with responses.RequestsMock() as rsps:
            rsps.add(responses.POST, "https://github.com/login/oauth/access_token", status=500)
            response = client.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert qs(response.headers["location"])["reason"] == ["oauth_exchange_failed"]

    @pytest.mark.parametrize("github,reason", [("mallory", "STUDENT_NOT_FOUND")])
    def test_student_must_be_in_the_spreadsheet(self, github, reason):
        response = oauth_login(TestClient(app), github)
        assert qs(response.headers["location"])["reason"] == [reason]

    def test_ambiguous_student(self, sheets):
        sheets.sheets["4432"].append(["2", "Алексеева Анна", "alice", "v", "", ""])
        response = oauth_login(TestClient(app), "alice")
        assert qs(response.headers["location"])["reason"] == ["STUDENT_AMBIGUOUS"]

    def test_sheets_down_lets_in_a_student_already_in_the_queue(self, sheets):
        sign_up(student("alice"), current_session_id())
        reset_roster_cache()
        sheets.fail = True

        client = TestClient(app)
        assert qs(oauth_login(client, "alice").headers["location"])["status"] == ["ok"]
        state = client.get(queue_url()).json()
        assert state["viewer"]["name"] == "Алексеева Анна"
        assert state["my_entry"]["place"] == 1

        response = oauth_login(TestClient(app), "bob")
        assert qs(response.headers["location"])["reason"] == ["SHEETS_UNAVAILABLE"]

    @pytest.mark.parametrize("course_id,reason", [("os-off", "QUEUE_DISABLED"), ("os-broken", "QUEUE_MISCONFIGURED")])
    def test_login_to_a_course_without_a_working_queue(self, course_id, reason):
        response = TestClient(app).get(queue_url("/login", course_id), follow_redirects=False)
        assert response.headers["location"] == f"{FRONT}/course/{course_id}/queue?status=error&reason={reason}"

    def test_login_without_oauth_app(self, monkeypatch):
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", None)
        response = TestClient(app).get(queue_url("/login"), follow_redirects=False)
        assert qs(response.headers["location"])["reason"] == ["oauth_not_configured"]


class TestQueueSignInWhileAtGitHub:
    """What changed between /queue/login and the callback."""

    @staticmethod
    def start(client):
        response = client.get(queue_url("/login"), follow_redirects=False)
        return qs(response.headers["location"])["state"][0]

    def test_queue_switched_off(self, courses_dir):
        client = TestClient(app)
        state = self.start(client)
        (courses_dir / "os-queue.yaml").write_text(
            yaml.dump(course_yaml({"enabled": False}), allow_unicode=True), encoding="utf-8"
        )
        with responses.RequestsMock():                       # GitHub не спрашивают
            response = client.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert qs(response.headers["location"])["reason"] == ["QUEUE_DISABLED"]
        assert not any(c.startswith("queue_session_") for c in set_cookies(response))

    def test_oauth_app_removed(self, monkeypatch):
        client = TestClient(app)
        state = self.start(client)
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", None)
        with responses.RequestsMock():
            response = client.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert qs(response.headers["location"])["reason"] == ["oauth_not_configured"]
        assert not any(c.startswith("queue_session_") for c in set_cookies(response))


class TestRegularJoinIsUntouched:
    def test_garbage_state_still_goes_to_join_error(self):
        response = TestClient(app).get("/join/callback", params={"code": "c", "state": "garbage"}, follow_redirects=False)
        assert response.headers["location"] == f"{FRONT}/join/error?status=error&reason=invalid_state"

    def test_join_state_still_goes_through_join(self):
        state = main_module._build_join_state("os-queue", "1")
        response = TestClient(app).get(
            "/join/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False
        )
        assert response.headers["location"] == f"{FRONT}/join/os-queue/1?status=error&reason=access_denied"

    def test_queue_payload_under_the_join_signer_is_not_a_queue_sign_in(self):
        payload = main_module._encode_payload({"purpose": "queue", "course_id": COURSE, "nonce": "n", "lab": None})
        state = main_module.signer.sign(payload).decode()
        response = TestClient(app).get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert response.headers["location"].startswith(f"{FRONT}/join/error?")


# ---------------------------------------------------------------------------
# Teacher
# ---------------------------------------------------------------------------

class TestTeacher:
    def test_day_view(self):
        sign_up(student("alice"), current_session_id())
        response = admin().get(admin_url())
        assert response.status_code == 200
        data = response.json()
        assert data["queue_enabled"] is True and data["config_error"] is None
        assert data["schedule"]["source"] == "manual"
        session = session_for(data, "4431")
        assert session["waiting_count"] == 1
        entry = session["entries"][0]
        assert (entry["name"], entry["group"], entry["github"], entry["lab"], entry["place"]) == (
            "Алексеева Анна", "4431", "alice", "ЛР1", 1,
        )

    def test_session_is_renewed_while_the_page_is_open(self):
        client = admin_signed_in(session_age=11 * 60, start_age=11 * 60)
        response = client.get(admin_url())
        assert response.status_code == 200
        renewed = next(c for c in set_cookies(response) if c.startswith("admin_session="))
        assert "Max-Age=3600" in renewed and "Path=/" in renewed and "HttpOnly" in renewed
        assert client.get("/admin/check-auth").status_code == 200

    def test_fresh_session_is_not_reissued(self):
        response = admin().get(admin_url())
        assert not any(c.startswith("admin_session=") for c in set_cookies(response))

    def test_archived_course_history_and_day_still_work(self):
        teacher = admin()
        day = teacher.get(admin_url(course_id="os-archived"))
        assert day.status_code == 200
        assert day.json()["queue_enabled"] is False
        assert day.json()["schedule"] is None
        history = teacher.get(admin_url("/history", "os-archived"))
        assert history.status_code == 200
        assert history.json()["entries"] == []

    @pytest.mark.parametrize("status,queue,code", [
        ("archived", {"enabled": True, "open-before-minutes": 15, "schedule": MANUAL_SCHEDULE}, "QUEUE_DISABLED"),
        ("hidden", {"enabled": True, "open-before-minutes": 15, "schedule": MANUAL_SCHEDULE}, "QUEUE_DISABLED"),
        ("active", {"enabled": False, "schedule": MANUAL_SCHEDULE}, "QUEUE_DISABLED"),
        ("active", {"enabled": True, "open-before-minutes": "15", "schedule": MANUAL_SCHEDULE}, "QUEUE_MISCONFIGURED"),
    ])
    def test_signup_is_closed_while_the_queue_does_not_work(self, courses_dir, status, queue, code):
        """Sessions of the day stay on the page, but students cannot sign up: no «open» there."""
        teacher = admin()
        sid = current_session_id()
        assert sign_up(student("alice"), sid).status_code == 200
        assert session_for(teacher.get(admin_url()).json(), "4431")["signup"] == "open"

        (courses_dir / "os-queue.yaml").write_text(yaml.dump(course_yaml(queue), allow_unicode=True), encoding="utf-8")
        index_path = courses_dir / "index.yaml"
        index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
        next(c for c in index["courses"] if c["id"] == COURSE)["status"] = status
        index_path.write_text(yaml.dump(index, allow_unicode=True), encoding="utf-8")

        assert sign_up(student("bob"), sid).json() == {"detail": code}
        day = teacher.get(admin_url()).json()
        assert {s["signup"] for s in day["sessions"]} == {"closed"}
        assert session_for(day, "4431")["waiting_count"] == 1
        # Вызвать того, кто уже ждёт, по-прежнему можно.
        response = teacher.post(admin_url(f"/sessions/{sid}/call-next"), json={"expected_entry_id": None})
        assert response.status_code == 200, response.text
        assert {s["signup"] for s in response.json()["sessions"]} == {"closed"}

    def test_broken_config_is_shown_to_the_teacher(self):
        data = admin().get(admin_url(course_id="os-broken")).json()
        assert "open-before-minutes" in data["config_error"]
        assert data["queue_enabled"] is True

    def test_course_without_timezone(self, courses_dir):
        """The days are counted in UTC just to show the page, the history and the config error."""
        data = course_yaml({"enabled": True})
        del data["course"]["timezone"]
        add_course(courses_dir, "os-notz", data)
        teacher = admin()
        day = teacher.get(admin_url(course_id="os-notz"))
        assert day.status_code == 200
        assert "timezone" in day.json()["config_error"]
        assert (day.json()["utc_offset_minutes"], day.json()["day"]) == (0, "2026-11-09")
        history = teacher.get(admin_url("/history", "os-notz"))
        assert history.status_code == 200 and history.json()["to"] == "2026-11-09"
        assert TestClient(app).get(queue_url(course_id="os-notz")).json() == {"detail": "QUEUE_MISCONFIGURED"}

    def test_unimportable_schedule_module_does_not_take_the_page_down(self):
        """A deployment without a source's dependency: the teacher sees why, students get a code."""
        sign_up(student("alice"), current_session_id())
        with patch("grading.defense_queue._check_schedule", side_effect=ModuleNotFoundError("No module named 'bs4'")):
            day = admin().get(admin_url())
            poll = TestClient(app).get(queue_url())
        assert day.status_code == 200
        assert "bs4" in day.json()["config_error"]
        assert session_for(day.json(), "4431")["waiting_count"] == 1
        assert poll.status_code == 400 and poll.json() == {"detail": "QUEUE_MISCONFIGURED"}

    def test_manual_session_for_all_groups(self):
        teacher = admin()
        response = teacher.post(admin_url("/sessions"), json={"groups": [], "closes_at": "18:00"})
        assert response.status_code == 200, response.text
        session = response.json()["session"]
        assert session["kind"] == "manual" and session["groups"] == []
        assert session["signup_closes_at"] == "2026-11-09T15:00:00+00:00"

        # dave (4432) может встать в общую очередь, хотя его пара ещё не началась.
        response = sign_up(student("dave"), session["id"])
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("body,code", [
        ({"groups": "4431", "closes_at": None}, "INVALID_GROUPS"),
        ({"groups": [""], "closes_at": None}, "INVALID_GROUPS"),
        ({"closes_at": None}, "INVALID_GROUPS"),
        ({"groups": [], "closes_at": "25:00"}, "INVALID_TIME"),
        ({"groups": [], "closes_at": "15:00"}, "INVALID_TIME"),   # уже прошло
        ({"groups": [], "closes_at": 1800}, "INVALID_TIME"),
    ])
    def test_manual_session_validation(self, body, code):
        response = admin().post(admin_url("/sessions"), json=body)
        assert response.status_code == 400
        assert response.json() == {"detail": code}

    def test_manual_session_needs_a_working_queue(self):
        response = admin().post(admin_url("/sessions", "os-archived"), json={"groups": []})
        assert response.json() == {"detail": "QUEUE_DISABLED"}

    def test_call_next_codes(self):
        teacher = admin()
        sid = current_session_id()
        response = teacher.post(admin_url(f"/sessions/{sid}/call-next"), json={"expected_entry_id": None})
        assert response.status_code == 409 and response.json() == {"detail": "QUEUE_EMPTY"}

        sign_up(student("alice"), sid)
        sign_up(student("bob"), sid)
        head = session_for(teacher.get(admin_url()).json(), "4431")["entries"][0]
        student("alice").post(queue_url("/leave"))          # пока преподаватель смотрел на экран
        response = teacher.post(admin_url(f"/sessions/{sid}/call-next"), json={"expected_entry_id": head["id"]})
        assert response.status_code == 409 and response.json() == {"detail": "QUEUE_CHANGED"}

        response = teacher.post(admin_url("/sessions/999/call-next"), json={})
        assert response.status_code == 404 and response.json() == {"detail": "SESSION_NOT_FOUND"}

    def test_repeated_call_next_calls_one_student(self):
        sid = current_session_id()
        sign_up(student("alice"), sid)
        sign_up(student("bob"), sid)
        head = session_for(admin().get(admin_url()).json(), "4431")["entries"][0]
        barrier = threading.Barrier(2)
        results = []

        def run():
            client = admin()
            barrier.wait()
            results.append(client.post(admin_url(f"/sessions/{sid}/call-next"), json={"expected_entry_id": head["id"]}))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert [r.status_code for r in results] == [200, 200]
        assert {r.json()["entry"]["id"] for r in results} == {head["id"]}
        session = session_for(admin().get(admin_url()).json(), "4431")
        assert session["called_count"] == 1 and session["waiting_count"] == 1

    def test_outcome_codes(self):
        teacher = admin()
        response = teacher.post(admin_url("/entries/1/outcome"), json={"outcome": "passed"})
        assert response.status_code == 400 and response.json() == {"detail": "INVALID_OUTCOME"}
        response = teacher.post(admin_url("/entries/1/outcome"))
        assert response.status_code == 400 and response.json() == {"detail": "INVALID_OUTCOME"}
        response = teacher.post(admin_url("/entries/1/outcome"), json={"outcome": "defended"})
        assert response.status_code == 404 and response.json() == {"detail": "ENTRY_NOT_FOUND"}

        sign_up(student("alice"), current_session_id())
        entry_id = student("alice").get(queue_url()).json()["my_entry"]["id"]
        response = teacher.post(admin_url(f"/entries/{entry_id}/outcome"), json={"outcome": "defended"})
        assert response.status_code == 409 and response.json() == {"detail": "ENTRY_NOT_CALLED"}

    def test_no_show_returns_to_the_old_place(self):
        teacher = admin()
        sid = current_session_id()
        sign_up(student("alice"), sid)
        sign_up(student("bob"), sid)
        alice_entry = teacher.post(admin_url(f"/sessions/{sid}/call-next"), json={}).json()["entry"]
        teacher.post(admin_url(f"/entries/{alice_entry['id']}/outcome"), json={"outcome": "no_show"})
        assert student("alice").get(queue_url()).json()["my_last_result"]["status"] == "no_show"

        response = teacher.post(admin_url(f"/entries/{alice_entry['id']}/return"))
        assert response.status_code == 200
        assert response.json()["entry"]["place"] == 1
        assert student("bob").get(queue_url()).json()["my_entry"]["place"] == 2

    def test_close_reopen_finish(self):
        teacher = admin()
        sid = current_session_id()
        sign_up(student("alice"), sid)

        assert teacher.post(admin_url(f"/sessions/{sid}/close")).json()["session"]["closed_at"] is not None
        assert sign_up(student("bob"), sid).json() == {"detail": "SIGNUP_CLOSED"}
        assert teacher.post(admin_url(f"/sessions/{sid}/reopen")).json()["session"]["closed_at"] is None
        assert sign_up(student("bob"), sid).status_code == 200

        response = teacher.post(admin_url(f"/sessions/{sid}/finish"))
        assert response.status_code == 200
        assert response.json()["session"]["closed_at"] is not None
        assert student("alice").get(queue_url()).json()["my_last_result"]["status"] == "expired"

    def test_history_filters_and_bad_dates(self):
        teacher = admin()
        sid = current_session_id()
        sign_up(student("alice"), sid)
        sign_up(student("bob"), sid, lab="1")

        data = teacher.get(admin_url("/history")).json()
        assert (data["from"], data["to"]) == ("2026-10-11", "2026-11-09")
        assert [e["github"] for e in data["entries"]] == ["bob", "alice"]
        assert [e["github"] for e in teacher.get(admin_url("/history"), params={"q": "алекс"}).json()["entries"]] == ["alice"]
        assert teacher.get(admin_url("/history"), params={"group": "4432"}).json()["entries"] == []
        assert teacher.get(admin_url("/history"), params={"from": "2026-11-10"}).json() == {"detail": "INVALID_PERIOD"}
        assert teacher.get(admin_url("/history"), params={"to": "вчера"}).status_code == 400


# ---------------------------------------------------------------------------
# Origin, rate limits, logs
# ---------------------------------------------------------------------------

class TestOrigin:
    def test_foreign_origin_is_refused(self):
        evil = {"Origin": "https://evil.example.com"}
        alice = student("alice")
        response = alice.post(queue_url("/entries"), json={"session_id": current_session_id(), "lab_id": "1"}, headers=evil)
        assert response.status_code == 403 and response.json() == {"detail": "BAD_ORIGIN"}
        assert alice.post(queue_url("/leave"), headers=evil).status_code == 403
        response = admin().post(admin_url("/sessions"), json={"groups": []}, headers=evil)
        assert response.status_code == 403 and response.json() == {"detail": "BAD_ORIGIN"}
        assert alice.get(queue_url()).json()["my_entry"] is None

    @pytest.mark.parametrize("origin", [FRONT, "http://testserver"])
    def test_own_origin_passes(self, origin):
        response = sign_up(student("alice"), current_session_id(), lab="1")
        assert response.status_code == 200
        response = student("alice").post(queue_url("/leave"), headers={"Origin": origin})
        assert response.status_code == 200


    def test_foreign_origin_cannot_read_names_or_history(self):
        sign_up(student("alice"), current_session_id())
        evil = {"Origin": "https://evil.example.com"}
        for client, url in (
            (student("bob"), queue_url()),
            (student("bob"), queue_url("/labs")),
            (admin(), admin_url()),
            (admin(), admin_url("/history")),
        ):
            response = client.get(url, headers=evil)
            assert response.status_code == 403 and response.json() == {"detail": "BAD_ORIGIN"}, url
            assert "Алексеева" not in response.text
            # Свои страницы Origin в GET не шлют.
            assert client.get(url).status_code == 200, url

    @pytest.mark.parametrize("method,path,expected", [
        ("get", "", 200),
        ("post", "/leave", 404),            # NOT_IN_QUEUE: проверку Origin прошёл
    ])
    def test_origin_of_the_own_host_passes_behind_a_tls_proxy(self, monkeypatch, method, path, expected):
        """Behind Caddy request.base_url is http://host, the page is https://host, FRONTEND_URL not set."""
        monkeypatch.setattr(main_module, "FRONTEND_URL", "http://localhost:8080")
        client = TestClient(app, base_url="http://labgrader.example.ru")
        client.cookies.set(main_module._queue_cookie_name(COURSE), session_cookie("alice"))
        response = getattr(client, method)(queue_url(path), headers={"Origin": "https://labgrader.example.ru"})
        assert response.status_code == expected, response.text

    def test_refusal_log_suggests_frontend_url(self, caplog):
        caplog.set_level(logging.WARNING, logger="main")
        response = TestClient(app).get(queue_url(), headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 403
        assert "задайте FRONTEND_URL=http://localhost:5173, если это ваш фронтенд" in caplog.text

    @pytest.mark.parametrize("origin", ["null", "http://[", "http://evil\x01.example.com"])
    def test_no_hint_for_an_origin_that_is_not_an_address(self, caplog, origin):
        caplog.set_level(logging.WARNING, logger="main")
        TestClient(app).get(queue_url(), headers={"Origin": origin})
        assert "чужого Origin" in caplog.text and "FRONTEND_URL" not in caplog.text

    @pytest.mark.parametrize("origin", [
        "https://labgrader.example.ru:8443",
        "https://evil.labgrader.example.ru",
        "https://labgrader.example.ru.evil.com",
        "null",
        "",
        "http://[",
    ])
    def test_other_hosts_are_refused(self, monkeypatch, origin):
        monkeypatch.setattr(main_module, "FRONTEND_URL", "http://localhost:8080")
        client = TestClient(app, base_url="http://labgrader.example.ru")
        for response in (client.get(queue_url(), headers={"Origin": origin}),
                         client.post(queue_url("/leave"), headers={"Origin": origin})):
            assert response.status_code == 403 and response.json() == {"detail": "BAD_ORIGIN"}


@pytest.mark.rate_limit
class TestRateLimits:
    def test_limit_is_per_student_not_per_address(self):
        """25 students behind one NAT each act; only one student's own 21st request is limited."""
        clients = [student(f"student{i}") for i in range(25)]
        for client in clients:
            assert client.post(queue_url("/leave")).status_code == 404     # NOT_IN_QUEUE, но не 429

        codes = [clients[0].post(queue_url("/leave")).status_code for _ in range(20)]
        assert codes[:19] == [404] * 19
        assert codes[19] == 429
        assert clients[1].post(queue_url("/leave")).status_code == 404

    def test_anonymous_requests_share_the_address(self):
        anonymous = TestClient(app)
        codes = [anonymous.post(queue_url("/leave")).status_code for _ in range(21)]
        assert codes[:20] == [401] * 20 and codes[20] == 429
        # Вошедший студент с того же адреса считается отдельно.
        assert student("alice").post(queue_url("/leave")).status_code == 404

    def test_a_whole_group_opens_the_page_before_signing_in(self):
        anonymous = TestClient(app)
        codes = [anonymous.get(queue_url()).status_code for _ in range(121)]
        assert codes[:120] == [200] * 120 and codes[120] == 429

    def test_rate_key(self):
        request = type("R", (), {})()
        request.path_params = {"course_id": COURSE}
        request.cookies = {main_module._queue_cookie_name(COURSE): session_cookie("Alice")}
        request.client = type("C", (), {"host": "10.0.0.7"})()
        request.headers = {}
        assert main_module._queue_rate_key(request) == "qs:alice"
        request.cookies = {main_module._queue_cookie_name(COURSE): "forged"}
        assert main_module._queue_rate_key(request) == "ip:10.0.0.7"


class TestAccessLogFilter:
    @pytest.mark.parametrize("method,path,status,logged", [
        ("GET", "/courses/os-queue/queue", 200, False),
        ("GET", "/courses/os-queue/queue?x=1", 200, False),
        ("GET", "/admin/courses/os-queue/queue", 200, False),
        ("GET", "/courses/os-queue/queue", 429, True),
        ("GET", "/courses/os-queue/queue", 500, True),
        ("POST", "/courses/os-queue/queue/entries", 200, True),
        ("GET", "/admin/courses/os-queue/queue/history", 200, True),
        ("GET", "/courses", 200, True),
    ])
    def test_only_successful_polls_are_dropped(self, method, path, status, logged):
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '%s - "%s %s HTTP/%s" %d', ("10.0.0.1:5000", method, path, "1.1", status), None,
        )
        assert main_module.QueuePollAccessFilter().filter(record) is logged


# ---------------------------------------------------------------------------
# Integration with the rest of main.py
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_course_editor_rejects_a_broken_queue_section(self, courses_dir):
        path = courses_dir / "os-queue.yaml"
        before = path.read_text(encoding="utf-8")
        broken = yaml.dump(course_yaml({"enabled": True, "open-before-minutes": 500}), allow_unicode=True)
        response = admin().put("/courses/os-queue/edit", json={"content": broken})
        assert response.status_code == 400
        assert "open-before-minutes" in response.json()["detail"]
        assert path.read_text(encoding="utf-8") == before

        good = yaml.dump(course_yaml({"enabled": True, "open-before-minutes": 10}), allow_unicode=True)
        assert admin().put("/courses/os-queue/edit", json={"content": good}).status_code == 200

    def test_startup_validation_never_fails_on_the_queue_section(self, courses_dir):
        assert main_module.validate_course_index() is True

    def test_startup_storage_check_only_logs(self, tmp_path, monkeypatch, caplog):
        target = tmp_path / "volume" / "data" / "queue.sqlite3"
        monkeypatch.setattr(main_module, "QUEUE_DB_PATH", str(target))
        with patch("main.os.access", return_value=False):
            main_module.check_queue_storage()          # не бросает
        assert "QUEUE_DB_PATH" in caplog.text
        assert not target.parent.exists()              # и ничего не создаёт

        caplog.clear()
        main_module.check_queue_storage()
        assert "QUEUE_DB_PATH" not in caplog.text

    def test_visible_labs_behaviour_is_unchanged(self):
        course = main_module.get_course_by_id(COURSE)
        assert [key for key, _ in main_module.visible_lab_items(course, MONDAY_1520)] == ["1", "2", "3", "4"]
        assert [lab.get("short-name") for lab in main_module.visible_labs(course, MONDAY_1520)] == [
            "ЛР1", "ЛР2", "ЛР3", None,
        ]

    @responses.activate
    def test_register_student_resets_the_queue_cache(self, mock_gspread, mock_service_account_creds, mock_request):
        mock_gspread["worksheet"].col_values.return_value = ["", "", "Иванов Иван"]
        mock_gspread["worksheet"].row_values.return_value = ["№", "ФИО", "GitHub"]
        mock_gspread["worksheet"].cell.return_value.value = ""
        responses.add(responses.GET, "https://api.github.com/users/ivanov", json={"login": "ivanov"})
        with patch("main.queue_roster.invalidate") as invalidate:
            result = main_module.register_student(
                mock_request, COURSE, "4431",
                main_module.StudentRegistration(name="Иван", surname="Иванов", github="ivanov"),
            )
        assert result["status"] == "registered"
        invalidate.assert_called_once_with("sheet-os")

    @responses.activate
    def test_grade_lab_resets_the_queue_cache(
        self, sample_course_config, mock_gspread, mock_service_account_creds, mock_request
    ):
        repo = "https://api.github.com/repos/test-org/test-task1-testuser"
        responses.add(responses.GET, f"{repo}/contents/test_main.py", json={"name": "test_main.py"})
        responses.add(responses.GET, f"{repo}/contents/.github/workflows", json=[{"name": "test.yml"}])
        responses.add(responses.GET, f"{repo}/commits", json=[{"sha": "abc123"}])
        responses.add(responses.GET, f"{repo}/commits/abc123", json={"sha": "abc123", "files": []})
        responses.add(
            responses.GET, f"{repo}/commits/abc123/check-runs",
            json={"check_runs": [{"name": "test", "conclusion": "success", "html_url": "http://test"}]},
        )
        mock_gspread["worksheet"].row_values.return_value = ["№", "ФИО", "GitHub", "ЛР1"]
        mock_gspread["worksheet"].col_values.return_value = ["", "", "testuser"]

        with patch("main.get_course_by_id", return_value=sample_course_config), \
                patch("main.queue_roster.invalidate") as invalidate:
            result = main_module.grade_lab(
                mock_request, "test-course", "group1", "ЛР1", main_module.GradeRequest(github="testuser")
            )
        assert result["status"] == "updated"
        invalidate.assert_called_once_with("test_spreadsheet_id")


# ---------------------------------------------------------------------------
# Time of the sign-up, ids, history period
# ---------------------------------------------------------------------------

class TestSignUpTime:
    """The entry's time and the window check come after every network call of the request."""

    @staticmethod
    def slow_sheets(sheets, clock, seconds):
        read = sheets.values_batch_get

        def slow(ranges, params=None):
            clock.now += timedelta(seconds=seconds)
            return read(ranges, params)

        sheets.values_batch_get = slow

    def test_entry_time_is_after_the_spreadsheet_read(self, sheets, clock):
        sid = current_session_id()
        self.slow_sheets(sheets, clock, 3)
        response = sign_up(student("alice"), sid)
        assert response.status_code == 200
        assert response.json()["my_entry"]["created_at"] == "2026-11-09T12:20:03+00:00"
        assert response.json()["now"] == "2026-11-09T12:20:03+00:00"

    def test_request_that_reaches_the_queue_after_the_pair_is_late(self, sheets, clock):
        clock.now = datetime(2026, 11, 9, 16, 39, 58, tzinfo=MSK)
        sid = current_session_id()
        self.slow_sheets(sheets, clock, 3)
        assert sign_up(student("alice"), sid).json() == {"detail": "SIGNUP_CLOSED"}

    def test_slow_schedule_counts_too(self, monkeypatch, clock):
        clock.now = datetime(2026, 11, 9, 16, 39, 55, tzinfo=MSK)
        sid = current_session_id()
        original = main_module._schedule_source

        class Slow:
            def __init__(self, inner):
                self.inner, self.name = inner, inner.name

            def lessons_on(self, day, now):
                clock.now += timedelta(seconds=5)
                return self.inner.lessons_on(day, now)

            def status(self, now):
                return self.inner.status(now)

        monkeypatch.setattr(main_module, "_schedule_source", lambda settings: Slow(original(settings)))
        assert sign_up(student("alice"), sid).json() == {"detail": "SIGNUP_CLOSED"}


class TestIds:
    """Any id that cannot be a row id is the stable "not found", not 422 or 500."""

    @pytest.mark.parametrize("session_id", [2 ** 63, 0, -1, "abc", None, 1.5])
    def test_student_sign_up(self, session_id):
        response = student("alice").post(queue_url("/entries"), json={"session_id": session_id, "lab_id": "1"})
        assert response.status_code == 404 and response.json() == {"detail": "SESSION_NOT_FOUND"}

    @pytest.mark.parametrize("path,body,code", [
        (f"/sessions/{2 ** 63}/close", None, "SESSION_NOT_FOUND"),
        ("/sessions/0/reopen", None, "SESSION_NOT_FOUND"),
        ("/sessions/-5/finish", None, "SESSION_NOT_FOUND"),
        (f"/sessions/{2 ** 63}/call-next", {}, "SESSION_NOT_FOUND"),
        ("/sessions/abc/call-next", {}, "SESSION_NOT_FOUND"),
        (f"/sessions/{2 ** 63 - 1}/close", None, "SESSION_NOT_FOUND"),   # наибольший допустимый - до базы
        (f"/entries/{2 ** 63}/outcome", {"outcome": "defended"}, "ENTRY_NOT_FOUND"),
        (f"/entries/{2 ** 63}/return", None, "ENTRY_NOT_FOUND"),
        ("/entries/1e3/return", None, "ENTRY_NOT_FOUND"),
    ])
    def test_teacher_actions(self, path, body, code):
        response = admin().post(admin_url(path), json=body)
        assert response.json() == {"detail": code}
        assert response.status_code == main_module._QUEUE_ERROR_STATUS[code]

    @pytest.mark.parametrize("expected", [2 ** 63, 0, "abc"])
    def test_impossible_expected_entry_calls_nobody(self, expected):
        sid = current_session_id()
        sign_up(student("alice"), sid)
        response = admin().post(admin_url(f"/sessions/{sid}/call-next"), json={"expected_entry_id": expected})
        assert response.status_code == 409 and response.json() == {"detail": "QUEUE_CHANGED"}
        assert session_for(admin().get(admin_url()).json(), "4431")["called_count"] == 0

    def test_other_validation_errors_stay_422(self):
        response = student("alice").post(queue_url("/entries"), json={"session_id": 1})
        assert response.status_code == 422
        assert [error["loc"] for error in response.json()["detail"]] == [["body", "lab_id"]]


class TestHistoryPeriod:
    @pytest.mark.parametrize("params", [
        {"from": "0001-01-01", "to": "2026-11-09"},
        {"from": "2026-11-01", "to": "9999-12-31"},
        {"to": "0001-01-05"},                        # без from: 30 дней назад - до начала календаря
        {"from": "1999-12-31", "to": "2000-01-05"},
        {"from": "2026-11-10", "to": "2026-11-09"},
        {"from": "2025-01-01", "to": "2026-11-09"},  # длиннее HISTORY_MAX_DAYS
        {"from": "09.11.2026"},
        {"to": "вчера"},
    ])
    def test_invalid_period(self, params):
        response = admin().get(admin_url("/history"), params=params)
        assert response.status_code == 400 and response.json() == {"detail": "INVALID_PERIOD"}

    def test_default_period_starts_no_earlier_than_the_first_allowed_day(self):
        data = admin().get(admin_url("/history"), params={"to": "2000-01-05"}).json()
        assert data == {"entries": [], "from": "2000-01-01", "to": "2000-01-05", "truncated": False}

    def test_long_history_is_truncated(self, monkeypatch):
        sid = current_session_id()
        sign_up(student("alice"), sid)
        sign_up(student("bob"), sid)
        history = DefenseQueue.history
        monkeypatch.setattr(DefenseQueue, "history", lambda self, *a, **k: history(self, *a, **k, limit=1))
        data = admin().get(admin_url("/history")).json()
        assert [e["github"] for e in data["entries"]] == ["bob"]
        assert data["truncated"] is True


# ---------------------------------------------------------------------------
# admin_session, OAuth error in the log, registration, config checks
# ---------------------------------------------------------------------------

class TestAdminSessionLifetime:
    """The queue page renews admin_session, but not further than 12 hours from the password sign-in."""

    @staticmethod
    def not_renewed(client):
        response = client.get(admin_url())
        assert response.status_code == 200
        assert not any(c.startswith("admin_session=") for c in set_cookies(response))

    def test_password_sign_in_sets_the_start(self):
        response = TestClient(app).post(
            "/admin/login", json={"login": main_module.ADMIN_LOGIN, "password": main_module.ADMIN_PASSWORD}
        )
        start = next(c for c in set_cookies(response) if c.startswith("admin_session_start="))
        assert "Max-Age=43200" in start and "HttpOnly" in start and "Path=/" in start

    def test_logout_deletes_the_start(self):
        response = admin().post("/admin/logout")
        assert any(c.startswith("admin_session_start=") and "Max-Age=0" in c for c in set_cookies(response))

    def test_renewal_is_logged(self, caplog):
        caplog.set_level(logging.INFO, logger="main")
        response = admin_signed_in(session_age=11 * 60, start_age=3 * 3600).get(admin_url())
        assert any(c.startswith("admin_session=") for c in set_cookies(response))
        assert "admin_session of 'test' renewed by /admin/courses/os-queue/queue" in caplog.text

    def test_no_start_no_renewal(self):
        self.not_renewed(admin_signed_in(session_age=11 * 60))

    def test_start_older_than_twelve_hours(self):
        self.not_renewed(admin_signed_in(session_age=11 * 60, start_age=12 * 3600 + 1))

    def test_last_hour_before_twelve_hours_is_not_renewed(self):
        """A renewal now would outlive the 12 hours from the sign-in."""
        self.not_renewed(admin_signed_in(session_age=11 * 60, start_age=11 * 3600 + 60))
        response = admin_signed_in(session_age=11 * 60, start_age=11 * 3600 - 60).get(admin_url())
        assert any(c.startswith("admin_session=") for c in set_cookies(response))

    @pytest.mark.parametrize("start", [
        "garbage",
        lambda: signed_at(time.time(), main_module.signer),                         # admin_session вместо start
        lambda: signed_at(time.time(), main_module.admin_start_signer, "someone"),  # чужой логин
    ])
    def test_forged_start(self, start):
        client = admin_signed_in(session_age=11 * 60)
        client.cookies.set(main_module.ADMIN_SESSION_START_COOKIE, start() if callable(start) else start)
        self.not_renewed(client)

    def test_stolen_cookies_die_twelve_hours_after_the_sign_in(self):
        """GET every 10 minutes with both cookies: renewed for 11 hours, 401 right after 12 hours."""
        start = time.time()
        with patch("itsdangerous.timed.time.time", return_value=start):
            response = TestClient(app).post(
                "/admin/login", json={"login": main_module.ADMIN_LOGIN, "password": main_module.ADMIN_PASSWORD}
            )
        cookies = cookie_values(response)
        polls = range(600, 12 * 3600 + 1, 600)
        statuses = {}
        for seconds in [*polls, 12 * 3600 + 1]:
            with patch("itsdangerous.timed.time.time", return_value=start + seconds):
                client = TestClient(app)
                for name, value in cookies.items():
                    client.cookies.set(name, value)
                response = client.get(admin_url())
            statuses[seconds] = response.status_code
            cookies.update(cookie_values(response))
        # Последнее продление - через 11 ч после входа; эта сессия живёт ровно до 12 ч.
        assert {statuses[s] for s in polls} == {200}
        assert statuses[12 * 3600 + 1] == 401

    @pytest.mark.parametrize("cookies", [{}, {"admin_session": "garbage"}])
    def test_without_a_valid_admin_session_nothing_is_renewed(self, cookies):
        cookies = {**cookies, main_module.ADMIN_SESSION_START_COOKIE: signed_at(time.time(), main_module.admin_start_signer)}
        request = type("Request", (), {"cookies": cookies})()
        response = Response()
        main_module._renew_admin_session(request, response)
        assert "set-cookie" not in response.headers


class TestOAuthErrorInTheLog:
    """The `error` of the GitHub callback is anyone's text: the queue sign-in must not let it start a new log line."""

    FORGED = "2026-11-09 15:21:00 - main - INFO - Successfully updated grade for 'alice' in lab 3"

    def assert_one_line(self, caplog):
        declined = [r.getMessage() for r in caplog.records if "declined GitHub OAuth" in r.getMessage()]
        assert len(declined) == 1
        assert "\n" not in declined[0] and "\\n" in declined[0]

    def test_queue_sign_in(self, caplog):
        caplog.set_level(logging.INFO, logger="main")
        client = TestClient(app)
        start = client.get(queue_url("/login"), follow_redirects=False)
        state = qs(start.headers["location"])["state"][0]
        response = client.get(
            "/join/callback", params={"state": state, "error": "access_denied\n" + self.FORGED}, follow_redirects=False
        )
        assert qs(response.headers["location"])["reason"] == ["access_denied"]
        self.assert_one_line(caplog)


class WritableSheet:
    """register_student's gspread view of FakeSheets: what it writes lands in the same grids."""

    def __init__(self, grids):
        self.grids = grids

    def open_by_key(self, key):
        return self

    def worksheet(self, title):
        grid = self.grids[title]
        sheet = MagicMock()
        sheet.col_values.side_effect = lambda col: [row[col - 1] if len(row) >= col else "" for row in grid]
        sheet.row_values.side_effect = lambda row: list(grid[row - 1])
        sheet.cell.side_effect = lambda row, col: MagicMock(value=grid[row - 1][col - 1])
        sheet.update_cell.side_effect = lambda row, col, value: grid[row - 1].__setitem__(col - 1, value)
        return sheet


class TestRegistrationKeepsLoginsUnique:
    """
    The queue finds a student by the GitHub column of every sheet and refuses
    a login found in two rows. The public registration form must not let
    anyone write a login that is already in the course into another row.
    """

    REFUSED = "Этот GitHub-аккаунт уже привязан к другой строке таблицы курса. Обратитесь к преподавателю"

    @pytest.fixture
    def register(self, sheets, monkeypatch):
        # Ещё не зарегистрированный Жуков в обеих группах.
        sheets.sheets["4431"].append(["5", "Жуков Жора", "", "", "", ""])
        sheets.sheets["4432"].append(["2", "Жуков Жора", "", "", "", ""])
        monkeypatch.setattr(main_module, "sheets_client", lambda: WritableSheet(sheets.sheets))

        def run(github, group="4432", course_id=COURSE):
            with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
                rsps.add(responses.GET, f"https://api.github.com/users/{github}", json={"login": github})
                return TestClient(app).post(
                    f"/courses/{course_id}/groups/{group}/register",
                    json={"surname": "Жуков", "name": "Жора", "patronymic": "", "github": github},
                )

        return run

    def test_classmate_cannot_lock_a_student_out_of_the_queue(self, register, sheets):
        response = register("Alice")
        assert response.status_code == 409 and response.json() == {"detail": self.REFUSED}
        assert sheets.sheets["4432"][-1][2] == ""
        assert qs(oauth_login(TestClient(app), "alice").headers["location"])["status"] == ["ok"]
        assert sign_up(student("alice"), current_session_id()).status_code == 200

    def test_new_login_is_registered_once(self, register, sheets):
        assert register("zhora").json()["status"] == "registered"
        assert sheets.sheets["4432"][-1][2] == "zhora"
        assert register("zhora").json()["status"] == "already_registered"
        # Только что записанный логин виден сразу: кэш таблицы сброшен.
        response = register("zhora", group="4431")
        assert response.status_code == 409 and sheets.sheets["4431"][-1][2] == ""

    def test_spreadsheet_unreadable_registers_as_before(self, register, sheets):
        sheets.fail = True
        assert register("alice").json()["status"] == "registered"

    def test_course_without_queue_is_not_checked(self, register):
        with patch.object(main_module._queue_roster, "login_exists") as login_exists:
            assert register("alice", course_id="os-off").json()["status"] == "registered"
        login_exists.assert_not_called()


class TestCourseConfigChecks:
    @staticmethod
    def upload(data):
        content = yaml.dump(data, allow_unicode=True)
        return admin().post("/courses/upload", files={"file": ("new-course.yaml", content, "application/x-yaml")})

    @pytest.mark.parametrize("queue,text", [
        ({"enabled": True, "open-before-minutes": "15"}, "open-before-minutes"),
        ({"enabled": "true"}, "queue.enabled"),
    ])
    def test_upload_checks_queue_before_writing(self, courses_dir, queue, text):
        data = course_yaml(queue)
        index_before = (courses_dir / "index.yaml").read_text(encoding="utf-8")
        response = self.upload(data)
        assert response.status_code == 400 and text in response.json()["detail"]
        assert not (courses_dir / "new-course.yaml").exists()
        assert (courses_dir / "index.yaml").read_text(encoding="utf-8") == index_before

    def test_upload_of_a_good_course(self, courses_dir):
        response = self.upload(course_yaml({"enabled": True}))
        assert response.status_code == 200 and response.json()["course_id"] == "new-course"
        assert TestClient(app).get(queue_url(course_id="new-course")).status_code == 200

    def test_enabled_that_is_not_a_bool_is_a_config_error(self, courses_dir):
        add_course(courses_dir, "os-strflag", course_yaml({"enabled": "true"}))
        response = TestClient(app).get(queue_url(course_id="os-strflag"))
        assert response.status_code == 400 and response.json() == {"detail": "QUEUE_MISCONFIGURED"}
        login = TestClient(app).get(queue_url("/login", "os-strflag"), follow_redirects=False)
        assert qs(login.headers["location"])["reason"] == ["QUEUE_MISCONFIGURED"]
        day = admin().get(admin_url(course_id="os-strflag")).json()
        assert "queue.enabled" in day["config_error"] and day["queue_enabled"] is False

    def test_queue_reads_the_sheet_with_a_short_timeout(self):
        client = MagicMock()
        with patch("main.ServiceAccountCredentials.from_json_keyfile_name"), \
                patch("main.gspread.authorize", return_value=client):
            assert QUEUE_SHEETS_CLIENT() is client
        client.set_timeout.assert_called_once_with(main_module.QUEUE_SHEETS_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# The whole lesson
# ---------------------------------------------------------------------------

def test_whole_lesson(clock):
    """
    Manual schedule; three students sign in through GitHub and queue up;
    the teacher calls the first one and marks "defended"; the second lets
    the third go first and then leaves; the day ends with the third still
    waiting. The history keeps every outcome.
    """
    alice, bob, carol = TestClient(app), TestClient(app), TestClient(app)
    for client, github in ((alice, "alice"), (bob, "bob"), (carol, "carol")):
        assert qs(oauth_login(client, github, lab="ЛР1").headers["location"])["status"] == ["ok"]

    state = alice.get(queue_url()).json()
    assert state["viewer"]["name"] == "Алексеева Анна"
    session = session_for(state, "4431")
    assert session["signup"] == "open" and session["for_me"] is True

    labs = alice.get(queue_url("/labs")).json()["labs"]
    assert [lab["id"] for lab in labs if lab["status"] == "passed"] == ["1"]

    for minutes, client in ((1, alice), (2, bob), (3, carol)):
        clock.now = MONDAY_1520 + timedelta(minutes=minutes)
        response = sign_up(client, session["id"], lab="1")
        assert response.status_code == 200, response.text
    assert [e["name"] for e in session_for(carol.get(queue_url()).json(), "4431")["entries"]] == [
        "Алексеева Анна", "Борисов Борис", "Васильева Вера",
    ]

    teacher = admin()
    clock.now = MONDAY_1520 + timedelta(minutes=10)
    head = session_for(teacher.get(admin_url()).json(), "4431")["entries"][0]
    called = teacher.post(admin_url(f"/sessions/{session['id']}/call-next"), json={"expected_entry_id": head["id"]})
    assert called.json()["entry"]["github"] == "alice"
    assert alice.get(queue_url()).json()["my_entry"]["status"] == "called"
    assert alice.post(queue_url("/leave")).json() == {"detail": "ALREADY_CALLED"}

    clock.now = MONDAY_1520 + timedelta(minutes=25)
    teacher.post(admin_url(f"/entries/{head['id']}/outcome"), json={"outcome": "defended"})
    assert alice.get(queue_url()).json()["my_last_result"] == {
        "status": "defended", "lab": "ЛР1", "finished_at": "2026-11-09T12:45:00+00:00",
    }

    # bob пропускает carol вперёд, потом уходит.
    assert bob.get(queue_url()).json()["my_entry"]["can_skip"] is True
    assert bob.post(queue_url("/skip")).json()["my_entry"]["place"] == 2
    assert carol.get(queue_url()).json()["my_entry"]["place"] == 1
    assert bob.post(queue_url("/leave")).json()["my_last_result"]["status"] == "left"

    # После пары новые записи не принимаются, но ждущих можно принять до конца дня.
    clock.now = datetime(2026, 11, 9, 17, 0, tzinfo=MSK)
    assert sign_up(alice, session["id"], lab="1").json() == {"detail": "SIGNUP_CLOSED"}
    assert session_for(teacher.get(admin_url()).json(), "4431")["waiting_count"] == 1

    clock.now = datetime(2026, 11, 10, 0, 5, tzinfo=MSK)
    assert carol.get(queue_url()).json()["viewer"] is None      # вход действовал до конца дня
    history = teacher.get(admin_url("/history"), params={"from": "2026-11-09", "to": "2026-11-09"}).json()
    outcomes = {e["github"]: (e["status"], e["finished_at"]) for e in history["entries"]}
    assert outcomes == {
        "alice": ("defended", "2026-11-09T12:45:00+00:00"),
        "bob": ("left", "2026-11-09T12:45:00+00:00"),
        "carol": ("expired", "2026-11-09T21:00:00+00:00"),
    }


# ---------------------------------------------------------------------------
# Schedule changes (queue.schedule.changes)
# ---------------------------------------------------------------------------

def test_cancelled_and_extra_lessons_from_the_config(courses_dir, clock):
    """
    Manual schedule with changes: on Monday the pair of 4431 is cancelled
    (no session, nothing to sign up for), on Tuesday 4431 has an extra lesson
    10:00-11:30 that opens like a regular one and takes a sign-up.
    """
    schedule = {**MANUAL_SCHEDULE, "changes": [
        {"date": "2026-11-09", "cancel": True, "groups": ["4431"]},
        {"date": "2026-11-10", "groups": ["4431"], "start": "10:00", "end": "11:30"},
    ]}
    add_course(courses_dir, "os-changes", course_yaml({"enabled": True, "schedule": schedule}))
    tuesday_end = int(datetime(2026, 11, 11, tzinfo=MSK).timestamp())
    alice = student("alice", course_id="os-changes", expires=tuesday_end)

    state = alice.get(queue_url(course_id="os-changes")).json()
    assert [s["groups"] for s in state["sessions"]] == [["4432"]]
    assert [s["groups"] for s in admin().get(admin_url(course_id="os-changes")).json()["sessions"]] == [["4432"]]

    clock.now = datetime(2026, 11, 10, 9, 40, tzinfo=MSK)
    session = session_for(alice.get(queue_url(course_id="os-changes")).json(), "4431")
    assert session["signup"] == "upcoming"
    assert sign_up(alice, session["id"], course_id="os-changes").json() == {"detail": "SIGNUP_CLOSED"}

    clock.now = datetime(2026, 11, 10, 9, 45, tzinfo=MSK)
    response = sign_up(alice, session["id"], course_id="os-changes")
    assert response.status_code == 200, response.text
    assert response.json()["my_entry"]["place"] == 1
    assert session_for(response.json(), "4431")["signup"] == "open"


def test_cancel_of_one_group_holds_while_the_source_fails(courses_dir, monkeypatch):
    """
    Сессию общей пары 4431 и 4432 уже создали, потом в конфиг вписали отмену
    4431, а модуль источника падает с ошибкой. Студенту 4431 запись закрыта,
    4432 видит пустую сессию и записывается как обычно.
    """
    schedule = {"source": "manual", "lessons": [
        {"groups": ["4431", "4432"], "weekday": "mon", "start": "15:10", "end": "16:40"},
    ]}
    add_course(courses_dir, "os-shared", course_yaml({"enabled": True, "schedule": schedule}))
    [session] = TestClient(app).get(queue_url(course_id="os-shared")).json()["sessions"]

    schedule["changes"] = [{"date": "2026-11-09", "cancel": True, "groups": ["4431"]}]
    (courses_dir / "os-shared.yaml").write_text(
        yaml.dump(course_yaml({"enabled": True, "schedule": schedule}), allow_unicode=True), encoding="utf-8",
    )

    def parser_bug(self, day, now):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(ManualSource, "lessons_on", parser_bug)

    bob, dave = student("bob", course_id="os-shared"), student("dave", course_id="os-shared")
    assert [s["signup"] for s in bob.get(queue_url(course_id="os-shared")).json()["sessions"]] == ["closed"]
    assert sign_up(bob, session["id"], course_id="os-shared").json() == {"detail": "SIGNUP_CLOSED"}
    assert [s["signup"] for s in dave.get(queue_url(course_id="os-shared")).json()["sessions"]] == ["open"]
    response = sign_up(dave, session["id"], course_id="os-shared")
    assert response.status_code == 200, response.text

    [day] = admin().get(admin_url(course_id="os-shared")).json()["sessions"]
    assert (day["in_schedule"], day["signup"]) == (True, "open")
    assert [e["github"] for e in day["entries"]] == ["dave"]
