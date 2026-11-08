from datetime import datetime
from types import SimpleNamespace

import yaml

from grading.schedule import (
    ScheduleConfigError,
    parse_schedule_file,
    parse_course_schedule,
    refresh_all_groups,
)
from grading.schedule import guap as guap_module


def test_guap_fetch_raw_requests_only_the_selected_group_without_cache(monkeypatch):
    calls = []

    class FakeFetcher:
        def __init__(self, ttl):
            assert ttl == 0

        def index(self):
            calls.append(("index",))
            return "directory html"

        def by_group(self, group_id):
            calls.append(("group", group_id))
            return "group html"

    monkeypatch.setattr(guap_module, "Fetcher", FakeFetcher)
    monkeypatch.setattr(
        guap_module,
        "parse_directories",
        lambda _html: {"groups": {42: "М433"}},
    )
    monkeypatch.setattr(
        guap_module, "parse_lessons", lambda html: [f"parsed {html}"]
    )

    source = guap_module.GuapScheduleSource(course_name="Test Course")

    assert source.fetch_raw("М433") == ["parsed group html"]
    assert calls == [("index",), ("group", 42)]


def test_guap_sessions_filter_live_group_lessons_by_course():
    source = guap_module.GuapScheduleSource(
        group_code="М433",
        course_id="test-course",
        course_name="  TEST   Course ",
    )
    source.fetch_raw = lambda group_code: [
        SimpleNamespace(
            day="Понедельник",
            slot=1,
            kind="ЛР",
            subject="Test Course",
            room="Room 1",
            teachers=["Teacher"],
        ),
        SimpleNamespace(
            day="Понедельник",
            slot=2,
            kind="ЛР",
            subject="Another Course",
            room=None,
            teachers=[],
        ),
        SimpleNamespace(
            day="Понедельник",
            slot=3,
            kind="Лекция",
            subject="Test Course",
            room=None,
            teachers=[],
        ),
    ]

    sessions = list(
        source.list_sessions(
            "М433",
            datetime(2026, 11, 9),
            datetime(2026, 11, 10),
        )
    )

    assert len(sessions) == 1
    assert sessions[0].group_id == "М433"
    assert sessions[0].subject == "Test Course"
    assert sessions[0].room == "Room 1"
    assert sessions[0].teacher == "Teacher"


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
    captured = {}

    class FakeGuapScheduleSource:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def fetch_raw(self, group_code):
            if group_code == "missing":
                raise RuntimeError("group missing from GUAP")
            return ["raw lesson"]

        def to_yaml_sessions(self, raw_lessons):
            assert raw_lessons == ["raw lesson"]
            return [
                {
                    "id": "fresh-slot",
                    "recurrence": {"day": "monday", "week": "all"},
                    "start": "12:00",
                    "end": "13:00",
                    "subject": "Test Course",
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
        course_name="Test Course",
    )

    assert result["refreshed"] == {"4432": 1}
    assert result["errors"] == {
        "4433": "RuntimeError: group missing from GUAP"
    }
    assert captured["course_name"] == "Test Course"
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


def test_refresh_filters_directly_fetched_group_sessions_by_course_name(
    tmp_path, monkeypatch
):
    schedule_path = tmp_path / "test-course.yaml"

    class FakeGuapScheduleSource:
        def __init__(self, **_kwargs):
            self.fetches = []

        def fetch_raw(self, group_code):
            self.fetches.append(group_code)
            return [
                SimpleNamespace(subject="Операционные системы"),
                SimpleNamespace(subject="Проектирование баз данных"),
                SimpleNamespace(subject="  ОПЕРАЦИОННЫЕ   СИСТЕМЫ "),
            ]

        def to_yaml_sessions(self, raw_lessons):
            return [
                {
                    "id": f"slot-{index}",
                    "subject": lesson.subject,
                }
                for index, lesson in enumerate(raw_lessons)
            ]

    sources = []

    def make_source(**kwargs):
        source = FakeGuapScheduleSource(**kwargs)
        sources.append(source)
        return source

    monkeypatch.setattr(guap_module, "GuapScheduleSource", make_source)
    result = refresh_all_groups(
        "test-course",
        schedule_path,
        [{"id": "4432", "guap_group_id": "4432"}],
        course_name=" Операционные системы ",
    )

    assert len(sources) == 1
    assert sources[0].fetches == ["4432"]
    assert result["refreshed"] == {"4432": 2}
    data = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert [slot["subject"] for slot in data["groups"]["4432"]["slots"]] == [
        "Операционные системы",
        "  ОПЕРАЦИОННЫЕ   СИСТЕМЫ ",
    ]


def test_refresh_creates_initial_schedule_yaml(tmp_path, monkeypatch):
    schedule_dir = tmp_path / "schedules"
    schedule_path = schedule_dir / "test-course.yaml"

    class FakeGuapScheduleSource:
        def __init__(self, **kwargs):
            assert kwargs["course_name"] == "Test Course"

        def fetch_raw(self, _group_code):
            return ["lesson"]

        def to_yaml_sessions(self, _raw_lessons):
            return [
                {
                    "id": "new-slot",
                    "recurrence": {"day": "monday", "week": "all"},
                    "start": "10:00",
                    "end": "11:00",
                    "subject": "Test Course",
                }
            ]

    monkeypatch.setattr(guap_module, "GuapScheduleSource", FakeGuapScheduleSource)
    result = refresh_all_groups(
        "test-course",
        schedule_path,
        [{"id": "4432", "guap_group_id": "4432"}],
        course_name="Test Course",
    )
    assert result["refreshed"] == {"4432": 1}
    data = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert data["course-id"] == "test-course"
    assert data["groups"]["4432"]["group_id"] == "4432"
    assert data["groups"]["4432"]["slots"][0]["id"] == "new-slot"


def test_refresh_filters_slots_by_course_name_case_insensitively(tmp_path, monkeypatch):
    schedule_path = tmp_path / "test-course.yaml"

    class FakeGuapScheduleSource:
        def __init__(self, **_kwargs):
            pass

        def fetch_raw(self, _group_code):
            return ["matching", "other"]

        def to_yaml_sessions(self, raw_lessons):
            return [
                {"id": "operating-systems", "subject": "Операционные системы"},
                {"id": "other-course", "subject": "Проектирование баз данных"},
                {"id": "spacing-case", "subject": "  ОПЕРАЦИОННЫЕ   СИСТЕМЫ "},
            ]

    monkeypatch.setattr(guap_module, "GuapScheduleSource", FakeGuapScheduleSource)
    result = refresh_all_groups(
        "test-course",
        schedule_path,
        [{"id": "4432", "guap_group_id": "4432"}],
        course_name=" Операционные системы ",
    )

    assert result["refreshed"] == {"4432": 2}
    data = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert [slot["id"] for slot in data["groups"]["4432"]["slots"]] == [
        "operating-systems",
        "spacing-case",
    ]


def test_refresh_reports_groups_without_slots_but_not_manual_groups(tmp_path, monkeypatch):
    schedule_path = tmp_path / "test-course.yaml"
    schedule_path.write_text(
        """course-id: test-course
source: guap
groups:
  '4432':
    group_id: '4432'
    source: manual
    slots:
      - id: teacher-slot
  '4433':
    group_id: '4433'
    slots: []
""",
        encoding="utf-8",
    )

    class FakeGuapScheduleSource:
        def __init__(self, **_kwargs):
            pass

        def fetch_raw(self, _group_code):
            return []

        def to_yaml_sessions(self, _raw_lessons):
            return []

    monkeypatch.setattr(guap_module, "GuapScheduleSource", FakeGuapScheduleSource)
    result = refresh_all_groups(
        "test-course",
        schedule_path,
        [
            {"id": "4432", "guap_group_id": "4432"},
            {"id": "4433", "guap_group_id": "4433"},
        ],
    )

    assert result["refreshed"] == {"4432": 0, "4433": 0}
    assert result["not_found"] == ["4433"]
    assert result["skipped"] == ["4432"]
    data = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    assert data["groups"]["4432"]["slots"][0]["id"] == "teacher-slot"


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


def test_schedule_yaml_accepts_alphanumeric_group_ids(tmp_path):
    schedule_path = tmp_path / "test-course.yaml"
    schedule_path.write_text(
        """course-id: test-course
source: guap
groups:
  М433:
    group_id: М433
    slots: []
""",
        encoding="utf-8",
    )

    assert parse_schedule_file(schedule_path, "test-course").keys() == {"М433"}
