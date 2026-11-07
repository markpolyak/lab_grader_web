import yaml

from grading.schedule import (
    ScheduleConfigError,
    parse_schedule_file,
    parse_course_schedule,
    refresh_all_groups,
)
from grading.schedule import guap as guap_module


def _write_schedule_config(path):
    path.write_text(
        """# Keep this teacher note.
course-id: test-course
source: guap
timezone: UTC+3
auto_open_minutes_before: 15
auto_close_minutes_after: 10
manual-groups: []
groups:
  '4432':
    group_id: '4432'
    guap_group_id: ok
    corrections:
      - id: teacher-correction
        date: '2026-11-09'
    queue_settings:
      max_students_per_slot: 17
    slots:
      - id: old-slot
        recurrence:
          day: monday
          week: all
        start: '10:00'
        end: '11:00'
  '4433':
    group_id: '4433'
    guap_group_id: missing
    slots:
      - id: old-4433
        recurrence:
          day: tuesday
        start: '10:00'
        end: '11:00'
""",
        encoding="utf-8",
    )


def test_refresh_all_groups_isolates_errors_and_preserves_teacher_edits(
    tmp_path, monkeypatch
):
    schedule_path = tmp_path / "test-course.yaml"
    _write_schedule_config(schedule_path)

    class FakeGuapScheduleSource:
        def __init__(self, group_code, **_kwargs):
            self.group_code = group_code

        def fetch_raw(self, _group_code):
            if self.group_code == "missing":
                raise RuntimeError("group missing from snapshot")
            return ["raw lesson"]

        def to_yaml_sessions(self, raw_lessons):
            assert raw_lessons == ["raw lesson"]
            return [
                {
                    "id": "fresh-slot",
                    "recurrence": {"day": "monday", "week": "all"},
                    "start": "12:00",
                    "end": "13:00",
                }
            ]

    monkeypatch.setattr(guap_module, "GuapScheduleSource", FakeGuapScheduleSource)
    groups = [
        {"id": "4432", "guap_group_id": "ok"},
        {"id": "4433", "guap_group_id": "missing"},
    ]

    result = refresh_all_groups(
        "test-course",
        schedule_file_path=schedule_path,
        groups=groups,
        snapshot_path="unused.json",
    )

    assert result["refreshed"] == {"4432": 1}
    assert result["errors"] == {
        "4433": "RuntimeError: group missing from snapshot"
    }
    data = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert data["groups"]["4432"]["slots"][0]["id"] == "fresh-slot"
    assert data["groups"]["4432"]["corrections"] == [
        {"id": "teacher-correction", "date": "2026-11-09"}
    ]
    assert data["groups"]["4432"]["queue_settings"] == {"max_students_per_slot": 17}
    assert data["groups"]["4432"]["group_id"] == "4432"
    assert data["groups"]["4432"]["guap_group_id"] == "ok"
    assert data["groups"]["4433"]["slots"][0]["id"] == "old-4433"
    assert not list(tmp_path.glob(".course-*.yaml.tmp"))
    assert parse_course_schedule(
        "test-course", tmp_path
    ).keys() == {"4432", "4433"}


def test_refresh_creates_initial_schedule_yaml(tmp_path, monkeypatch):
    schedule_dir = tmp_path / "schedules"
    schedule_path = schedule_dir / "test-course.yaml"

    class FakeGuapScheduleSource:
        def __init__(self, **_kwargs):
            pass

        def fetch_raw(self, _group_code):
            return ["lesson"]

        def to_yaml_sessions(self, _raw_lessons):
            return [
                {
                    "id": "new-slot",
                    "recurrence": {"day": "monday", "week": "all"},
                    "start": "10:00",
                    "end": "11:00",
                }
            ]

    monkeypatch.setattr(guap_module, "GuapScheduleSource", FakeGuapScheduleSource)
    result = refresh_all_groups(
        "test-course",
        schedule_path,
        [{"id": "4432", "guap_group_id": "4432"}],
    )
    assert result["refreshed"] == {"4432": 1}
    data = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert data["course-id"] == "test-course"
    assert data["groups"]["4432"]["group_id"] == "4432"
    assert data["groups"]["4432"]["slots"][0]["id"] == "new-slot"


def test_schedule_yaml_requires_course_and_group_identifiers(tmp_path):
    schedule_path = tmp_path / "test-course.yaml"
    schedule_path.write_text(
        "groups:\n  '4432':\n    slots: []\n",
        encoding="utf-8",
    )
    try:
        parse_schedule_file(schedule_path, "test-course")
    except ScheduleConfigError as error:
        assert "`course-id` is required" in str(error)
    else:
        raise AssertionError("Schedule without course-id should be rejected")

    schedule_path.write_text(
        "course-id: test-course\ngroups:\n  '4432':\n    slots: []\n",
        encoding="utf-8",
    )
    try:
        parse_schedule_file(schedule_path, "test-course")
    except ScheduleConfigError as error:
        assert "`group_id` is required" in str(error)
    else:
        raise AssertionError("Schedule group without group_id should be rejected")
