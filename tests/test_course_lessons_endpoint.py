from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

import main
from queues.domain.student import Student
from queues.usecase.queue_service import QueueService


MONDAY = date(2026, 11, 9)
COURSES = {
    "os-2026": {"name": "Операционные системы", "university": "ГУАП"},
    "ml": {"name": "Машинное обучение", "university": "ИТМО"},
    "empty": {"name": "Без занятий", "university": "ГУАП"},
}


@pytest.fixture
def endpoint(monkeypatch):
    service = QueueService()
    service.load_saved_week(COURSES, Path(__file__).resolve().parents[1] / "schedule", week_start=MONDAY)
    monkeypatch.setattr(main.app.state, "queue_service", service, raising=False)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter(COURSES.items()))
    monkeypatch.setenv("QUEUE_WEEK_START", MONDAY.isoformat())
    sheets = Mock(side_effect=AssertionError("Google Sheets must not be called"))
    monkeypatch.setattr(main, "sheets_client", sheets)

    def course_by_id(course_id):
        if course_id not in COURSES:
            raise main.HTTPException(status_code=404, detail="Курс не найден")
        return COURSES[course_id]

    monkeypatch.setattr(main, "get_course_by_id", course_by_id)
    return TestClient(main.app), service


def test_returns_actual_lessons_and_public_queue_state(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    lesson.queue.add_entry(Student("Иван", "Иванов", "", "private-login"), "1")
    before = lesson.queue.entries[:]
    response = client.get("/courses/os-2026/lessons")
    assert response.status_code == 200
    data = response.json()
    assert data["course_id"] == "os-2026"
    assert data["course_name"] == "Операционные системы"
    assert data["week_start"] == "2026-11-09"
    assert data["week_end"] == "2026-11-15"
    assert len(data["lessons"]) == 4
    first = data["lessons"][0]
    assert first["id"] == str(lesson.id)
    assert first["starts_at"] == "2026-11-09T15:10:00+03:00"
    assert first["ends_at"] == "2026-11-09T16:40:00+03:00"
    assert first["groups"] == ["4431"]
    assert first["kind"] == "Лабораторное занятие"
    assert first["room"] == "23-09 (Б. Морская 67)"
    assert first["queue"] == {"closed": False, "entries_count": 1}
    assert data["lessons"][1]["queue"] == {"closed": True, "entries_count": 0}
    assert "private-login" not in response.text
    assert "Иванов" not in response.text
    assert lesson.queue.entries == before


def test_empty_week_and_missing_schedule_are_different(endpoint):
    client, _ = endpoint
    response = client.get("/courses/empty/lessons")
    assert response.status_code == 200
    assert response.json()["lessons"] == []
    assert client.get("/courses/ml/lessons").status_code == 404
    assert client.get("/courses/not-a-course/lessons").status_code == 404


def test_uninitialized_service_returns_503(endpoint, monkeypatch):
    client, _ = endpoint
    monkeypatch.delattr(main.app.state, "queue_service")
    assert client.get("/courses/os-2026/lessons").status_code == 503


def test_current_week_switches_at_moscow_monday(endpoint, monkeypatch):
    client, service = endpoint
    monkeypatch.delenv("QUEUE_WEEK_START")
    previous = service.load_saved_week(COURSES, week_start=date(2026, 11, 2))["os-2026"]
    assert previous.week_start == date(2026, 11, 2)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 8, 22, 5, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", Clock)
    response = client.get("/courses/os-2026/lessons")
    assert response.status_code == 200
    assert response.json()["week_start"] == "2026-11-09"
    assert service.schedules["os-2026"].week_start == MONDAY


def test_unreadable_new_week_is_not_reported_as_empty(endpoint, monkeypatch):
    client, service = endpoint
    monkeypatch.delenv("QUEUE_WEEK_START")
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime.fromisoformat("2026-11-16T10:00:00+03:00")
    monkeypatch.setattr(main, "datetime", clock)
    monkeypatch.setattr(service, "load_saved_week", Mock(side_effect=ValueError("bad YAML")))
    response = client.get("/courses/os-2026/lessons")
    assert response.status_code == 503
    assert service.schedules["os-2026"].week_start == MONDAY
