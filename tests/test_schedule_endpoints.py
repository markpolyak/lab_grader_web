import yaml
import pytest
from fastapi.testclient import TestClient

import main as main_module
from grading.schedule import guap as guap_module


@pytest.fixture
def schedule_admin_client(tmp_path, monkeypatch):
    courses_dir = tmp_path / "courses"
    schedules_dir = tmp_path / "schedules"
    courses_dir.mkdir()
    schedules_dir.mkdir()
    course_path = courses_dir / "test-course.yaml"
    course_path.write_text(
        """# Preserve this comment.
course:
  name: Test Course
  google:
    spreadsheet: test-sheet
    info-sheet: Info
""",
        encoding="utf-8",
    )
    (schedules_dir / "test-course.yaml").write_text(
        """# Keep schedule note.
course-id: test-course
source: guap
timezone: UTC+3
manual-groups:
  - '4432'
groups:
  '4432':
    group_id: '4432'
    slots:
      - id: weekly-session
        recurrence:
          day: monday
          week: all
        start: '10:00'
        end: '11:00'
""",
        encoding="utf-8",
    )
    course_info = yaml.safe_load(course_path.read_text(encoding="utf-8"))["course"]
    course_info["_meta"] = {"filename": course_path.name}
    monkeypatch.setattr(main_module, "COURSES_DIR", str(courses_dir))
    monkeypatch.setattr(main_module, "SCHEDULES_DIR", str(schedules_dir))
    monkeypatch.setattr(main_module, "get_course_by_id", lambda _course_id: course_info)
    monkeypatch.setattr(main_module.limiter, "enabled", False)

    class FakeSheet:
        def __init__(self, title):
            self.title = title

    class FakeSpreadsheet:
        def worksheets(self):
            return [
                FakeSheet("4433"),
                FakeSheet("Example"),
                FakeSheet("Queue"),
                FakeSheet("History"),
                FakeSheet("Info"),
            ]

    class FakeSheetsClient:
        def open_by_key(self, spreadsheet_id):
            assert spreadsheet_id == "test-sheet"
            return FakeSpreadsheet()

    monkeypatch.setattr(main_module, "sheets_client", lambda: FakeSheetsClient())
    client = TestClient(main_module.app)
    client.cookies.set(
        "admin_session",
        main_module.signer.sign(main_module.ADMIN_LOGIN.encode()).decode(),
    )
    return client, course_path, schedules_dir / "test-course.yaml"


def test_schedule_groups_endpoint_merges_sheet_and_manual_groups(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.get("/admin/courses/test-course/schedule/groups")

    assert response.status_code == 200
    assert response.json() == {
        "course_id": "test-course",
        "groups": [
            {"id": "4432", "guap_group_id": "4432"},
            {"id": "4433", "guap_group_id": "4433"},
        ],
        "manual_groups": ["4432"],
        "warnings": [],
    }


def test_save_manual_groups_updates_schedule_yaml(schedule_admin_client):
    client, _, schedule_path = schedule_admin_client
    response = client.put(
        "/admin/courses/test-course/schedule/groups",
        json={"groups": [" 4434 ", "4435", "4434", ""]},
    )

    assert response.status_code == 200
    assert response.json()["manual_groups"] == ["4434", "4435"]
    saved = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert saved["manual-groups"] == ["4434", "4435"]


def test_save_manual_groups_rejects_non_numeric_ids(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.put(
        "/admin/courses/test-course/schedule/groups",
        json={"groups": ["Example"]},
    )

    assert response.status_code == 400
    assert "только числовые" in response.json()["detail"]


@pytest.mark.parametrize("has_schedule_file", [True, False])
def test_schedule_sessions_fetches_guap_group_and_course_on_demand(
    schedule_admin_client, monkeypatch, has_schedule_file
):
    client, _, schedule_path = schedule_admin_client
    if not has_schedule_file:
        schedule_path.unlink()
    captured = {}

    class FakeGuapScheduleSource:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def list_sessions(self, group_id, since, until):
            captured["query"] = (group_id, since, until)
            return []

    monkeypatch.setattr(main_module, "GuapScheduleSource", FakeGuapScheduleSource)
    response = client.get(
        "/admin/courses/test-course/groups/4433/schedule/sessions"
    )

    assert response.status_code == 200
    assert response.json()["course_id"] == "test-course"
    assert response.json()["group_id"] == "4433"
    assert "since" in response.json()
    assert captured["group_code"] == "4433"
    assert captured["course_name"] == "Test Course"
    assert captured["query"][0] == "4433"


def test_schedule_sessions_keeps_manual_yaml_source(schedule_admin_client, monkeypatch):
    client, _, schedule_path = schedule_admin_client
    schedule_path.write_text(
        schedule_path.read_text(encoding="utf-8").replace(
            "source: guap", "source: manual", 1
        ),
        encoding="utf-8",
    )

    def fail_if_guap_is_requested(**_kwargs):
        raise AssertionError("manual schedules must not call the GUAP parser")

    monkeypatch.setattr(
        main_module, "GuapScheduleSource", fail_if_guap_is_requested
    )
    response = client.get(
        "/admin/courses/test-course/groups/4432/schedule/sessions"
    )

    assert response.status_code == 200
    assert response.json()["queue_settings"] == {}


def test_refresh_all_returns_partial_results(schedule_admin_client, monkeypatch):
    client, _, schedule_path = schedule_admin_client
    captured = {}
    result = {
        "course_id": "test-course",
        "filename": "test-course.yaml",
        "refreshed": {"4432": 5},
        "errors": {"4433": "group missing from snapshot"},
    }

    def fake_refresh(**kwargs):
        captured.update(kwargs)
        return result

    monkeypatch.setattr(main_module, "refresh_all_groups", fake_refresh)
    response = client.post("/admin/courses/test-course/schedule/refresh-all")

    assert response.status_code == 207
    assert response.json()["refreshed"] == {"4432": 5}
    assert "отсутствует" in response.json()["errors"]["4433"]
    assert captured["groups"] == [
        {"id": "4432", "guap_group_id": "4432"},
        {"id": "4433", "guap_group_id": "4433"},
    ]
    assert captured["schedule_file_path"] == schedule_path


def test_refresh_all_creates_schedule_file_on_first_request(
    schedule_admin_client, monkeypatch
):
    client, _, schedule_path = schedule_admin_client
    schedule_path.unlink()

    class FakeGuapScheduleSource:
        def __init__(self, **_kwargs):
            pass

        def fetch_raw(self, _group_code):
            return ["lesson"]

        def to_yaml_sessions(self, _raw_lessons):
            return [
                {
                    "id": "first-slot",
                    "subject": "Test Course",
                    "recurrence": {"day": "monday", "week": "all"},
                    "start": "10:00",
                    "end": "11:00",
                }
            ]

    monkeypatch.setattr(guap_module, "GuapScheduleSource", FakeGuapScheduleSource)
    response = client.post("/admin/courses/test-course/schedule/refresh-all")

    assert response.status_code == 200
    assert response.json()["refreshed"] == {"4433": 1}
    saved = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert saved["course-id"] == "test-course"
    assert saved["groups"]["4433"]["group_id"] == "4433"
    assert saved["groups"]["4433"]["slots"][0]["id"] == "first-slot"


def test_schedule_groups_endpoint_requires_admin():
    response = TestClient(main_module.app).get(
        "/admin/courses/test-course/schedule/groups"
    )

    assert response.status_code == 401
