"""Тесты админского чтения очередей (без OAuth и без Google Sheets)."""
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main
from queues.domain import DefenseResult, EntryStatus, QueueEntryMetadata
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


def test_detail_requires_admin(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]

    assert client.get(f"/admin/courses/os-2026/queues/{lesson.id}").status_code == 401


def test_detail_splits_waiting_called_and_finished(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    first = lesson.queue.add_entry(
        Student("Иван", "Иванов", "Иванович", "ivan"), "1", "ЛР1"
    )
    second = lesson.queue.add_entry(
        Student("Пётр", "Петров", "", "petr"), "2", "ЛР2"
    )
    third = lesson.queue.add_entry(
        Student("Анна", "Сидорова", "", "anna"), "3", "ЛР3"
    )
    record = service.get_record(lesson.id)
    record.entry_metadata[second.id] = QueueEntryMetadata(
        queue_id=record.id,
        sequence=2,
        status=EntryStatus.CALLED,
        called_at=datetime(2026, 11, 9, 14, 55, tzinfo=timezone.utc),
    )
    record.entry_metadata[third.id] = QueueEntryMetadata(
        queue_id=record.id,
        sequence=3,
        status=EntryStatus.FINISHED,
        result=DefenseResult.DEFENDED,
        finished_at=datetime(2026, 11, 9, 15, 20, tzinfo=timezone.utc),
    )
    before = lesson.queue.entries[:]

    authenticate(client)
    response = client.get(f"/admin/courses/os-2026/queues/{lesson.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["queue_id"] == str(lesson.id)
    assert data["course_id"] == "os-2026"
    assert data["closed"] is False
    assert data["version"] == 0
    assert data["lesson"]["groups"] == ["4431"]

    assert [entry["id"] for entry in data["waiting"]] == [str(first.id)]
    assert data["waiting"][0]["position"] == 1
    assert data["waiting"][0]["status"] == "waiting"
    assert data["waiting"][0]["github"] == "ivan"
    assert data["waiting"][0]["full_name"] == "Иванов Иван Иванович"
    assert data["waiting"][0]["lab_id"] == "1"

    assert [entry["id"] for entry in data["called"]] == [str(second.id)]
    assert data["called"][0]["position"] is None
    assert data["called"][0]["status"] == "called"
    assert data["called"][0]["called_at"] is not None

    assert [entry["id"] for entry in data["finished"]] == [str(third.id)]
    assert data["finished"][0]["status"] == "finished"
    assert data["finished"][0]["result"] == "defended"
    assert data["finished"][0]["finished_at"] is not None

    assert lesson.queue.entries == before


def test_detail_defaults_an_entry_without_metadata_to_waiting(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    entry = lesson.queue.add_entry(
        Student("Иван", "Иванов", "", "ivan"), "1", "ЛР1"
    )

    authenticate(client)
    data = client.get(f"/admin/courses/os-2026/queues/{lesson.id}").json()

    assert len(data["waiting"]) == 1
    item = data["waiting"][0]
    assert item["id"] == str(entry.id)
    assert item["position"] == 1
    assert item["github"] == "ivan"
    assert item["full_name"] == "Иванов Иван"
    assert item["lab_id"] == "1"
    assert item["lab_name"] == "ЛР1"
    assert item["status"] == "waiting"
    assert item["joined_at"]
    assert item["called_at"] is None
    assert item["finished_at"] is None
    assert item["result"] is None
    assert data["called"] == []
    assert data["finished"] == []


def test_detail_unknown_queue_and_course_are_404(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    authenticate(client)

    assert client.get(
        "/admin/courses/os-2026/queues/00000000-0000-0000-0000-000000000001"
    ).status_code == 404
    assert client.get(
        f"/admin/courses/nope/queues/{lesson.id}"
    ).status_code == 404


def test_registration_requires_admin(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]

    response = client.put(
        f"/admin/courses/os-2026/queues/{lesson.id}/registration",
        json={"closed": False},
    )
    assert response.status_code == 401


def test_registration_opens_and_closes_with_version(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    authenticate(client)

    opened = client.put(
        f"/admin/courses/os-2026/queues/{lesson.id}/registration",
        json={"closed": False},
    )
    assert opened.status_code == 200
    assert opened.json() == {
        "queue_id": str(lesson.id),
        "closed": False,
        "version": 0,
    }
    assert lesson.queue.closed is False

    detail = client.get(f"/admin/courses/os-2026/queues/{lesson.id}").json()
    assert detail["closed"] is False
    assert detail["version"] == 0

    closed = client.put(
        f"/admin/courses/os-2026/queues/{lesson.id}/registration",
        json={"closed": True},
    )
    assert closed.status_code == 200
    assert closed.json() == {
        "queue_id": str(lesson.id),
        "closed": True,
        "version": 1,
    }
    assert service.get_record(lesson.id).version == 1


def test_registration_is_idempotent(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    authenticate(client)

    first = client.put(
        f"/admin/courses/os-2026/queues/{lesson.id}/registration",
        json={"closed": False},
    )
    second = client.put(
        f"/admin/courses/os-2026/queues/{lesson.id}/registration",
        json={"closed": False},
    )

    assert first.json()["version"] == 0
    assert second.json()["version"] == 0


def test_registration_rejects_missing_closed(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    authenticate(client)

    response = client.put(
        f"/admin/courses/os-2026/queues/{lesson.id}/registration", json={}
    )
    assert response.status_code == 422


def test_registration_unknown_queue_and_course_are_404(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    authenticate(client)

    assert client.put(
        "/admin/courses/os-2026/queues/00000000-0000-0000-0000-000000000001/registration",
        json={"closed": False},
    ).status_code == 404
    assert client.put(
        f"/admin/courses/nope/queues/{lesson.id}/registration",
        json={"closed": False},
    ).status_code == 404


def test_call_next_and_finish_move_entry_through_statuses(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    first = lesson.queue.add_entry(
        Student("Иван", "Иванов", "", "first"), "1", "ЛР1"
    )
    second = lesson.queue.add_entry(
        Student("Пётр", "Петров", "", "second"), "2", "ЛР2"
    )
    authenticate(client)

    called = client.post(f"/admin/courses/os-2026/queues/{lesson.id}/call-next")
    assert called.status_code == 200
    assert called.json()["entry"]["id"] == str(first.id)
    assert called.json()["entry"]["status"] == "called"
    assert called.json()["entry"]["called_at"]
    assert called.json()["version"] == 0

    finished = client.post(
        f"/admin/courses/os-2026/queues/{lesson.id}/entries/{first.id}/finish",
        json={"outcome": "defended"},
    )
    assert finished.status_code == 200
    assert finished.json()["entry"]["status"] == "finished"
    assert finished.json()["entry"]["result"] == "defended"
    assert finished.json()["version"] == 1

    assert client.post(
        f"/admin/courses/os-2026/queues/{lesson.id}/call-next"
    ).json()["entry"]["id"] == str(second.id)
    assert client.post(
        f"/admin/courses/os-2026/queues/{lesson.id}/call-next"
    ).status_code == 409


def test_call_next_empty_queue_is_409(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    authenticate(client)

    response = client.post(f"/admin/courses/os-2026/queues/{lesson.id}/call-next")
    assert response.status_code == 409
    assert response.json()["detail"] == "QUEUE_EMPTY"


def test_finish_requires_called_state_and_known_entry(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    entry = lesson.queue.add_entry(
        Student("Иван", "Иванов", "", "first"), "1", "ЛР1"
    )
    authenticate(client)

    waiting = client.post(
        f"/admin/courses/os-2026/queues/{lesson.id}/entries/{entry.id}/finish",
        json={"outcome": "defended"},
    )
    assert waiting.status_code == 409
    assert waiting.json()["detail"] == "INVALID_STATE"

    unknown = client.post(
        f"/admin/courses/os-2026/queues/{lesson.id}/entries/{uuid4()}/finish",
        json={"outcome": "defended"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "ENTRY_NOT_FOUND"


def test_call_and_finish_require_admin(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    entry = lesson.queue.add_entry(
        Student("Иван", "Иванов", "", "first"), "1", "ЛР1"
    )

    assert client.post(
        f"/admin/courses/os-2026/queues/{lesson.id}/call-next"
    ).status_code == 401
    assert client.post(
        f"/admin/courses/os-2026/queues/{lesson.id}/entries/{entry.id}/finish",
        json={"outcome": "defended"},
    ).status_code == 401


def test_queue_history_returns_terminal_entries(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    finished = lesson.queue.add_entry(
        Student("Иван", "Иванов", "", "finished"), "1", "ЛР1"
    )
    withdrawn = lesson.queue.add_entry(
        Student("Пётр", "Петров", "", "withdrawn"), "2", "ЛР2"
    )
    record = service.get_record(lesson.id)
    record.entry_metadata[finished.id] = QueueEntryMetadata(
        queue_id=record.id,
        status=EntryStatus.FINISHED,
        result=DefenseResult.DEFENDED,
        finished_at=datetime(2026, 11, 9, 15, 20, tzinfo=timezone.utc),
    )
    record.entry_metadata[withdrawn.id] = QueueEntryMetadata(
        queue_id=record.id,
        status=EntryStatus.WITHDRAWN,
        finished_at=datetime(2026, 11, 9, 15, 30, tzinfo=timezone.utc),
    )
    authenticate(client)

    response = client.get("/admin/courses/os-2026/queue-history")
    assert response.status_code == 200
    data = response.json()
    assert [item["github"] for item in data["entries"]] == ["withdrawn", "finished"]
    assert data["entries"][0]["status"] == "withdrawn"
    assert data["entries"][1]["result"] == "defended"
    assert data["next_cursor"] is None

    filtered = client.get("/admin/courses/os-2026/queue-history?group_id=9999")
    assert filtered.json()["entries"] == []
    page = client.get("/admin/courses/os-2026/queue-history?limit=1")
    assert len(page.json()["entries"]) == 1
    assert page.json()["next_cursor"] == "1"
    assert client.get(
        "/admin/courses/os-2026/queue-history?cursor=bad"
    ).status_code == 422


def test_queue_history_requires_admin(endpoint):
    client, _ = endpoint
    assert client.get("/admin/courses/os-2026/queue-history").status_code == 401


def test_create_manual_queue_and_see_it_in_list(endpoint):
    client, _ = endpoint
    authenticate(client)

    response = client.post(
        "/admin/courses/os-2026/queues",
        json={
            "group_ids": ["4431"],
            "lesson_starts_at": "2026-11-09T20:00:00+03:00",
            "lesson_ends_at": "2026-11-09T21:30:00+03:00",
            "registration_opens_at": "2026-11-09T19:45:00+03:00",
            "registration_closes_at": "2026-11-09T21:30:00+03:00",
            "service_until": "2026-11-09T23:59:00+03:00",
            "closed": False,
        },
    )

    assert response.status_code == 201
    queue_id = response.json()["queue_id"]
    assert response.json()["version"] == 0

    detail = client.get(f"/admin/courses/os-2026/queues/{queue_id}")
    assert detail.status_code == 200
    assert detail.json()["closed"] is False
    assert detail.json()["lesson"]["kind"] == "Внештатное занятие"
    assert detail.json()["lesson"]["groups"] == ["4431"]

    listed = client.get("/admin/courses/os-2026/queues?date=2026-11-09")
    assert queue_id in [item["queue_id"] for item in listed.json()["queues"]]


def test_create_manual_queue_validates_times_and_requires_admin(endpoint):
    client, _ = endpoint
    payload = {
        "group_ids": ["4431"],
        "lesson_starts_at": "2026-11-09T20:00:00+03:00",
        "lesson_ends_at": "2026-11-09T19:30:00+03:00",
    }

    assert client.post("/admin/courses/os-2026/queues", json=payload).status_code == 401

    authenticate(client)
    assert client.post("/admin/courses/os-2026/queues", json=payload).status_code == 422
    assert client.post(
        "/admin/courses/os-2026/queues",
        json={**payload, "group_ids": []},
    ).status_code == 422


def test_update_queue_rejects_scheduled_times_and_out_of_week_creation(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    authenticate(client)

    invalid = client.patch(
        f"/admin/courses/os-2026/queues/{lesson.id}",
        json={"lesson_starts_at": "2026-11-09T16:00:00+03:00"},
    )
    assert invalid.status_code == 422

    out_of_week = client.post(
        "/admin/courses/os-2026/queues",
        json={
            "group_ids": ["4431"],
            "lesson_starts_at": "2026-11-16T20:00:00+03:00",
            "lesson_ends_at": "2026-11-16T21:30:00+03:00",
        },
    )
    assert out_of_week.status_code == 422


def test_update_queue_times_and_window(endpoint):
    client, _ = endpoint
    authenticate(client)
    created = client.post(
        "/admin/courses/os-2026/queues",
        json={
            "group_ids": ["4431"],
            "lesson_starts_at": "2026-11-09T20:00:00+03:00",
            "lesson_ends_at": "2026-11-09T21:30:00+03:00",
        },
    ).json()
    queue_id = created["queue_id"]

    response = client.patch(
        f"/admin/courses/os-2026/queues/{queue_id}",
        json={
            "lesson_starts_at": "2026-11-09T20:30:00+03:00",
            "lesson_ends_at": "2026-11-09T22:00:00+03:00",
            "registration_closes_at": "2026-11-09T22:00:00+03:00",
            "service_until": "2026-11-09T23:59:00+03:00",
        },
    )

    assert response.status_code == 200
    assert response.json()["version"] == 1
    detail = client.get(f"/admin/courses/os-2026/queues/{queue_id}").json()
    assert detail["lesson"]["starts_at"] == "2026-11-09T20:30:00+03:00"
    assert detail["lesson"]["ends_at"] == "2026-11-09T22:00:00+03:00"
    assert detail["version"] == 1

    assert client.patch(
        f"/admin/courses/os-2026/queues/{queue_id}", json={}
    ).status_code == 422
    assert client.patch(
        "/admin/courses/os-2026/queues/00000000-0000-0000-0000-000000000001",
        json={"lesson_ends_at": "2026-11-09T22:00:00+03:00"},
    ).status_code == 404


def test_create_update_delete_require_admin(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]

    assert client.post(
        "/admin/courses/os-2026/queues",
        json={
            "group_ids": ["4431"],
            "lesson_starts_at": "2026-11-09T20:00:00+03:00",
            "lesson_ends_at": "2026-11-09T21:30:00+03:00",
        },
    ).status_code == 401
    assert client.patch(
        f"/admin/courses/os-2026/queues/{lesson.id}",
        json={"lesson_ends_at": "2026-11-09T18:00:00+03:00"},
    ).status_code == 401
    assert client.delete(
        f"/admin/courses/os-2026/queues/{lesson.id}"
    ).status_code == 401


def test_delete_manual_queue_and_reject_others(endpoint):
    client, service = endpoint
    authenticate(client)
    created = client.post(
        "/admin/courses/os-2026/queues",
        json={
            "group_ids": ["4431"],
            "lesson_starts_at": "2026-11-09T20:00:00+03:00",
            "lesson_ends_at": "2026-11-09T21:30:00+03:00",
        },
    ).json()
    queue_id = created["queue_id"]

    assert client.delete(
        f"/admin/courses/os-2026/queues/{queue_id}"
    ).status_code == 200
    assert client.get(
        f"/admin/courses/os-2026/queues/{queue_id}"
    ).status_code == 404

    scheduled = service.schedules["os-2026"].lessons[0]
    response = client.delete(f"/admin/courses/os-2026/queues/{scheduled.id}")
    assert response.status_code == 409
    assert response.json()["detail"] == "QUEUE_NOT_DELETABLE"

    manual = client.post(
        "/admin/courses/os-2026/queues",
        json={
            "group_ids": ["4431"],
            "lesson_starts_at": "2026-11-09T20:00:00+03:00",
            "lesson_ends_at": "2026-11-09T21:30:00+03:00",
        },
    ).json()["queue_id"]
    lesson = next(item for item in service.schedules["os-2026"].lessons if str(item.id) == manual)
    lesson.queue.closed = False
    lesson.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1", "ЛР1")
    service.save_lesson(lesson.id)

    non_empty = client.delete(f"/admin/courses/os-2026/queues/{manual}")
    assert non_empty.status_code == 409
    assert non_empty.json()["detail"] == "QUEUE_NOT_EMPTY"


def test_update_result_and_remove_entry(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    entry = lesson.queue.add_entry(
        Student("Иван", "Иванов", "", "ivan"), "1", "ЛР1"
    )
    service.save_lesson(lesson.id)
    authenticate(client)

    service.call_next(lesson.id)
    service.finish(lesson.id, entry.id, DefenseResult.DEFENDED)
    corrected = client.patch(
        f"/admin/courses/os-2026/queues/{lesson.id}/entries/{entry.id}/result",
        json={"outcome": "not_defended"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["entry"]["result"] == "not_defended"

    waiting = lesson.queue.add_entry(
        Student("Пётр", "Петров", "", "petr"), "2", "ЛР2"
    )
    service.save_lesson(lesson.id)
    assert client.patch(
        f"/admin/courses/os-2026/queues/{lesson.id}/entries/{waiting.id}/result",
        json={"outcome": "defended"},
    ).status_code == 409

    removed = client.delete(
        f"/admin/courses/os-2026/queues/{lesson.id}/entries/{waiting.id}"
    )
    assert removed.status_code == 200
    assert removed.json() == {"removed": True}
    detail = client.get(f"/admin/courses/os-2026/queues/{lesson.id}").json()
    assert any(item["id"] == str(waiting.id) for item in detail["finished"])

    assert client.delete(
        f"/admin/courses/os-2026/queues/{lesson.id}/entries/{waiting.id}"
    ).status_code == 409
