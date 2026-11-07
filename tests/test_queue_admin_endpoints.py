"""Тесты админского чтения очередей (без OAuth и без Google Sheets)."""
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import main
from queues.domain.student import Student
from queues.usecase.queue_service import QueueService


MONDAY = date(2026, 11, 9)
COURSES = {
    "os-2026": {"name": "Операционные системы", "university": "ГУАП"},
    "ml": {"name": "Машинное обучение", "university": "ИТМО"},
}


@pytest.fixture
def endpoint(monkeypatch):
    service = QueueService()
    service.load_saved_week(
        COURSES, Path(__file__).resolve().parents[1] / "schedule", week_start=MONDAY
    )
    monkeypatch.setattr(main.app.state, "queue_service", service, raising=False)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter(COURSES.items()))
    monkeypatch.setenv("QUEUE_WEEK_START", MONDAY.isoformat())
    monkeypatch.setattr(
        main,
        "sheets_client",
        Mock(side_effect=AssertionError("Google Sheets must not be called")),
    )

    def course_by_id(course_id):
        if course_id not in COURSES:
            raise main.HTTPException(status_code=404, detail="Курс не найден")
        return COURSES[course_id]

    monkeypatch.setattr(main, "get_course_by_id", course_by_id)
    return TestClient(main.app), service


def authenticate(client):
    client.cookies.set(
        "admin_session", main.signer.sign(main.ADMIN_LOGIN.encode()).decode()
    )


def test_requires_admin(endpoint):
    client, _ = endpoint
    assert client.get("/admin/courses/os-2026/queues").status_code == 401


def test_lists_current_week_queues_without_private_data(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    lesson.queue.add_entry(Student("Иван", "Иванов", "", "private-login"), "1", "ЛР1")
    before = lesson.queue.entries[:]

    authenticate(client)
    response = client.get("/admin/courses/os-2026/queues")

    assert response.status_code == 200
    data = response.json()
    assert data["course_id"] == "os-2026"
    assert data["course_name"] == "Операционные системы"
    assert data["week_start"] == "2026-11-09"
    assert data["week_end"] == "2026-11-15"
    assert len(data["queues"]) == 4

    first = data["queues"][0]
    assert first["queue_id"] == str(lesson.id)
    assert first["lesson"] == {
        "id": str(lesson.id),
        "starts_at": "2026-11-09T15:10:00+03:00",
        "ends_at": "2026-11-09T16:40:00+03:00",
        "groups": ["4431"],
        "kind": "Лабораторное занятие",
        "room": "23-09 (Б. Морская 67)",
    }
    assert first["closed"] is False
    assert first["entries_count"] == 1
    assert data["queues"][1]["queue_id"] == str(
        service.schedules["os-2026"].lessons[1].id
    )
    assert data["queues"][1]["closed"] is True
    assert data["queues"][1]["entries_count"] == 0

    assert "private-login" not in response.text
    assert "Иванов" not in response.text
    assert lesson.queue.entries == before
    assert lesson.queue.closed is False


def test_filters_by_group_and_date(endpoint):
    client, _ = endpoint
    authenticate(client)

    assert (
        len(client.get("/admin/courses/os-2026/queues?group_id=4432").json()["queues"])
        == 2
    )
    assert (
        len(client.get("/admin/courses/os-2026/queues?date=2026-11-09").json()["queues"])
        == 2
    )
    assert (
        client.get("/admin/courses/os-2026/queues?group_id=9999").json()["queues"] == []
    )
    assert (
        client.get("/admin/courses/os-2026/queues?date=2026-11-16").json()["queues"] == []
    )
    assert (
        client.get("/admin/courses/os-2026/queues?date=not-a-date").status_code == 422
    )


def test_unknown_course_and_course_without_schedule_are_404(endpoint):
    client, _ = endpoint
    authenticate(client)

    assert client.get("/admin/courses/nope/queues").status_code == 404
    assert client.get("/admin/courses/ml/queues").status_code == 404


def test_uninitialized_service_is_503(endpoint, monkeypatch):
    client, _ = endpoint
    authenticate(client)
    monkeypatch.delattr(main.app.state, "queue_service")

    assert client.get("/admin/courses/os-2026/queues").status_code == 503
