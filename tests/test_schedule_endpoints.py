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


def test_schedule_sessions_uses_linked_schedule_config_and_utc_offset(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.get(
        "/admin/courses/test-course/groups/4432/schedule/sessions"
    )

    assert response.status_code == 200
    assert response.json()["course_id"] == "test-course"
    assert response.json()["group_id"] == "4432"
    assert "since" in response.json()


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


def test_edit_schedule_get_returns_yaml_content(schedule_admin_client):
    client, _, schedule_path = schedule_admin_client
    response = client.get("/admin/courses/test-course/schedule/edit")

    assert response.status_code == 200
    data = response.json()
    assert "filename" in data
    assert "content" in data
    assert "course-id: test-course" in data["content"]
    assert "weekly-session" in data["content"]


def test_edit_schedule_get_creates_default_when_missing(schedule_admin_client, tmp_path):
    client, _, schedule_path = schedule_admin_client
    schedule_path.unlink()
    response = client.get("/admin/courses/test-course/schedule/edit")

    assert response.status_code == 200
    data = response.json()
    assert "course-id: test-course" in data["content"]
    assert "source: guap" in data["content"]
    assert "groups: {}" in data["content"]
    assert "manual-groups: []" in data["content"]


def test_edit_schedule_put_saves_changes(schedule_admin_client, tmp_path):
    client, _, schedule_path = schedule_admin_client
    new_content = """course-id: test-course
source: guap
timezone: UTC+3
auto_open_minutes_before: 15
auto_close_minutes_after: 10
groups:
  '4432':
    group_id: '4432'
    slots:
      - id: new-slot
        recurrence:
          day: tuesday
          week: all
        start: '12:00'
        end: '13:00'
manual-groups: []
"""
    response = client.put(
        "/admin/courses/test-course/schedule/edit",
        json={"content": new_content},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Изменения успешно сохранены"
    saved = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert saved["groups"]["4432"]["slots"][0]["id"] == "new-slot"
    assert saved["groups"]["4432"]["slots"][0]["start"] == "12:00"


def test_edit_schedule_put_rejects_invalid_yaml(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.put(
        "/admin/courses/test-course/schedule/edit",
        json={"content": "invalid: yaml: ["},
    )

    assert response.status_code == 400
    assert "YAML" in response.json()["detail"]


def test_edit_schedule_put_rejects_mismatched_course_id(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.put(
        "/admin/courses/test-course/schedule/edit",
        json={"content": "course-id: other-course\ngroups: {}\nmanual-groups: []\n"},
    )

    assert response.status_code == 400
    assert "course-id" in response.json()["detail"]


def test_edit_schedule_put_rejects_non_numeric_group_ids(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.put(
        "/admin/courses/test-course/schedule/edit",
        json={
            "content": """course-id: test-course
groups:
  'abc':
    group_id: 'abc'
    slots: []
manual-groups: []
"""
        },
    )

    assert response.status_code == 400
    assert "цифр" in response.json()["detail"] or "digits" in response.json()["detail"]


def test_edit_schedule_put_rejects_mismatched_group_id(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.put(
        "/admin/courses/test-course/schedule/edit",
        json={
            "content": """course-id: test-course
groups:
  '4432':
    group_id: '4433'
    slots: []
manual-groups: []
"""
        },
    )

    assert response.status_code == 400
    assert "group_id" in response.json()["detail"]


def test_edit_schedule_put_rejects_invalid_groups_type(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.put(
        "/admin/courses/test-course/schedule/edit",
        json={
            "content": """course-id: test-course
groups: "not-a-dict"
manual-groups: []
"""
        },
    )

    assert response.status_code == 400
    assert "groups" in response.json()["detail"]


def test_edit_schedule_put_rejects_invalid_manual_groups_type(schedule_admin_client):
    client, _, _ = schedule_admin_client
    response = client.put(
        "/admin/courses/test-course/schedule/edit",
        json={
            "content": """course-id: test-course
groups: {}
manual-groups: "not-a-list"
"""
        },
    )

    assert response.status_code == 400
    assert "manual-groups" in response.json()["detail"]


def test_edit_schedule_put_requires_admin():
    response = TestClient(main_module.app).put(
        "/admin/courses/test-course/schedule/edit",
        json={"content": "course-id: test-course\ngroups: {}\nmanual-groups: []\n"},
    )

    assert response.status_code == 401
