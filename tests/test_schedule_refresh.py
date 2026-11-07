import yaml

from grading.schedule import ScheduleConfigError, load_course_groups, refresh_all_groups
from grading.schedule import guap as guap_module


def _write_course_config(path, groups):
    path.write_text(
        yaml.safe_dump({"course": {"groups": groups}}, allow_unicode=True),
        encoding="utf-8",
    )


def test_load_course_groups_normalizes_ids_and_defaults_guap_id(tmp_path):
    courses_dir = tmp_path / "courses"
    courses_dir.mkdir()
    _write_course_config(
        courses_dir / "test-course.yaml",
        [{"id": 4432}, {"id": "4433", "guap_group_id": 9001}],
    )

    assert load_course_groups("test-course", courses_dir) == [
        {"id": "4432", "guap_group_id": "4432"},
        {"id": "4433", "guap_group_id": "9001"},
    ]


def test_load_course_groups_rejects_malformed_group(tmp_path):
    courses_dir = tmp_path / "courses"
    courses_dir.mkdir()
    _write_course_config(courses_dir / "test-course.yaml", [{"guap_group_id": "4432"}])

    try:
        load_course_groups("test-course", courses_dir)
    except ScheduleConfigError as error:
        assert "course.groups[0].id" in str(error)
    else:
        raise AssertionError("Invalid group config should fail")


def test_refresh_all_groups_isolates_errors_and_preserves_teacher_edits(
    tmp_path, monkeypatch
):
    courses_dir = tmp_path / "courses"
    schedules_dir = tmp_path / "schedules"
    courses_dir.mkdir()
    schedules_dir.mkdir()
    _write_course_config(
        courses_dir / "test-course.yaml",
        [
            {"id": "4432", "guap_group_id": "ok"},
            {"id": "4433", "guap_group_id": "missing"},
        ],
    )
    schedule_path = schedules_dir / "test-course.yaml"
    schedule_path.write_text(
        yaml.safe_dump(
            {
                "course-id": "test-course",
                "groups": {
                    "4432": {
                        "source": "guap",
                        "timezone": "Europe/Moscow",
                        "auto_open_minutes_before": 22,
                        "auto_close_minutes_after": 33,
                        "corrections": [{"id": "teacher-correction"}],
                        "queue_settings": {"max_students_per_slot": 17},
                        "slots": [{"id": "old-4432"}],
                    },
                    "4433": {"slots": [{"id": "old-4433"}]},
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    class FakeGuapScheduleSource:
        def __init__(self, group_code, **_kwargs):
            self.group_code = group_code

        def fetch_raw(self, _group_code):
            if self.group_code == "missing":
                raise RuntimeError("group missing from snapshot")
            return ["raw lesson"]

        def to_yaml_sessions(self, raw_lessons):
            assert raw_lessons == ["raw lesson"]
            return [{"id": "fresh-slot"}]

    monkeypatch.setattr(guap_module, "GuapScheduleSource", FakeGuapScheduleSource)

    result = refresh_all_groups(
        "test-course",
        courses_dir=courses_dir,
        schedules_dir=schedules_dir,
        snapshot_path="unused.json",
    )

    assert result["refreshed"] == {"4432": 1}
    assert result["errors"] == {
        "4433": "RuntimeError: group missing from snapshot"
    }
    data = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert data["groups"]["4432"]["slots"] == [{"id": "fresh-slot"}]
    assert data["groups"]["4432"]["corrections"] == [{"id": "teacher-correction"}]
    assert data["groups"]["4432"]["queue_settings"] == {"max_students_per_slot": 17}
    assert data["groups"]["4432"]["timezone"] == "Europe/Moscow"
    assert data["groups"]["4432"]["auto_open_minutes_before"] == 22
    assert data["groups"]["4432"]["auto_close_minutes_after"] == 33
    assert data["groups"]["4433"]["slots"] == [{"id": "old-4433"}]
    assert list(schedules_dir.glob(".schedule-*.yaml.tmp")) == []
