"""Student access to queues whose lessons disappeared from the schedule export."""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

import main
from grading.queue_eligibility import EligibleStudentLab, StudentInfo
from queues.domain import DefenseResult, Student
from queues.repositories.storage.file import FileQueueRepository
from queues.usecase.queue_history_service import QueueHistoryService
from queues.usecase.queue_service import QueueService

MONDAY = date(2026, 11, 9)
NOW = datetime.fromisoformat("2026-11-09T10:05:00+03:00")
COURSES = {
    "os": {
        "name": "ОС", "university": "ГУАП",
        "google": {"spreadsheet": "sheet"},
        "labs": {"1": {"short-name": "ЛР1"}},
    },
    "networks": {"name": "Сети", "university": "ГУАП"},
}
IVAN = EligibleStudentLab(
    student=Student("Иван", "Иванов", "", "ivan"),
    full_name="Иванов Иван", group_id="4431", lab_id="1", lab_name="ЛР1",
)


def write_export(directory, *, moved=False):
    path = directory / "suai" / "2026-autumn" / "teacher.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "university": "suai", "timezone": "Europe/Moscow",
        "days": {MONDAY.isoformat(): [{
            "subject": "ОС", "kind": "Лабораторное занятие", "groups": ["4431"],
            "starts-at": "10:30" if moved else "10:00",
            "ends-at": "12:00" if moved else "11:30",
        }]},
    }
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")


@pytest.fixture
def displaced_queue(tmp_path, monkeypatch):
    monkeypatch.setattr("queues.usecase.queue_service._now", lambda: NOW)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setenv("QUEUE_WEEK_START", MONDAY.isoformat())
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter(COURSES.items()))
    monkeypatch.setattr(main, "get_course_by_id", lambda cid: COURSES[cid])
    monkeypatch.setattr(main, "_open_spreadsheet", lambda course: object())
    monkeypatch.setattr(main, "resolve_eligible_lab", lambda *args: IVAN)
    monkeypatch.setattr(main, "resolve_student", lambda *args: StudentInfo(
        student=IVAN.student, full_name=IVAN.full_name, group_id=IVAN.group_id,
    ))
    service = QueueService(FileQueueRepository(tmp_path / "queue_state"))
    monkeypatch.setattr(main.app.state, "queue_service", service, raising=False)
    directory = tmp_path / "schedule"
    monkeypatch.setenv("SCHEDULE_DIR", str(directory))
    write_export(directory)
    service.load_saved_week(COURSES, directory, week_start=MONDAY)
    client = TestClient(main.app)
    lesson_id = client.get("/courses/os/lessons").json()["lessons"][0]["id"]
    joined = client.post("/courses/os/queue/join", json={
        "github": "ivan", "lesson_id": lesson_id, "lab_id": "1",
    })
    assert joined.status_code == 201, joined.text
    assert client.get(f"/courses/os/lessons/{lesson_id}/queue").status_code == 200
    write_export(directory, moved=True)
    service.load_saved_week(COURSES, directory, week_start=MONDAY)
    return SimpleNamespace(
        client=client, service=service, directory=directory,
        lesson_id=lesson_id, entry_id=joined.json()["entry"]["id"],
    )


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("status", ["waiting", "called"])
def test_student_can_use_displaced_queue(displaced_queue, restart, status):
    env = displaced_queue
    service = env.service
    lesson_id, entry_id = UUID(env.lesson_id), UUID(env.entry_id)
    if status == "called":
        service.call_next(lesson_id)
    if restart:
        service = QueueService(service.repository)
        service.load_saved_week(COURSES, env.directory, week_start=MONDAY)
        main.app.state.queue_service = service

    state = env.client.get("/courses/os/queue", params={"github": "ivan", "group_id": "4431"})
    assert state.status_code == 200
    assert state.json()["entry"] is not None
    assert state.json()["entry"]["lesson_id"] == env.lesson_id
    assert state.json()["entry"]["status"] == status
    assert state.json()["entry"]["position"] == (1 if status == "waiting" else None)
    page = env.client.get(f"/courses/os/lessons/{env.lesson_id}/queue")
    assert page.status_code == 200, page.text
    assert page.json()["closed"] is True  # readable, but no new registrations
    assert page.json()[status][0]["id"] == env.entry_id
    assert page.json()[status][0]["status"] == status
    assert page.json()["lesson"]["starts_at"] == NOW.replace(minute=0).isoformat()

    # The old queue remains accessible by ID without becoming a registration target.
    lessons = env.client.get("/courses/os/lessons").json()["lessons"]
    assert len(lessons) == 1
    assert lessons[0]["id"] != env.lesson_id
    assert lessons[0]["starts_at"] == NOW.replace(minute=30).isoformat()
    rejected = env.client.post("/courses/os/queue/join", json={
        "github": "ivan", "lesson_id": env.lesson_id, "lab_id": "1",
    })
    assert rejected.status_code == 404
    assert rejected.json()["detail"] == "LESSON_NOT_FOUND"

    if status == "called":
        service.finish(lesson_id, entry_id, DefenseResult.DEFENDED)
    else:
        env.client.cookies.set("admin_session", main.signer.sign(main.ADMIN_LOGIN.encode()).decode())
        removed = env.client.delete(f"/admin/courses/os/queues/{env.lesson_id}/entries/{env.entry_id}")
        assert removed.status_code == 200
        assert removed.json() == {"removed": True}
    assert env.client.get("/courses/os/queue", params={
        "github": "ivan", "group_id": "4431",
    }).json()["entry"] is None


@pytest.mark.parametrize("restart", [False, True])
def test_displaced_queue_still_checks_course_and_missing_id(displaced_queue, restart):
    env = displaced_queue
    if restart:
        service = QueueService(env.service.repository)
        service.load_saved_week(COURSES, env.directory, week_start=MONDAY)
        main.app.state.queue_service = service
    for course_id, lesson_id in [("networks", env.lesson_id), ("os", str(uuid4()))]:
        response = env.client.get(f"/courses/{course_id}/lessons/{lesson_id}/queue")
        assert response.status_code == 404
        assert response.json()["detail"] == "LESSON_NOT_FOUND"


def test_new_registration_uses_updated_lesson(displaced_queue, monkeypatch):
    env = displaced_queue
    monkeypatch.setattr("queues.usecase.queue_service._now", lambda: NOW.replace(minute=20))
    new_id = env.client.get("/courses/os/lessons").json()["lessons"][0]["id"]
    payload = {"github": "ivan", "lesson_id": new_id, "lab_id": "1"}
    duplicate = env.client.post("/courses/os/queue/join", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "ALREADY_QUEUED"
    env.client.cookies.set("admin_session", main.signer.sign(main.ADMIN_LOGIN.encode()).decode())
    assert env.client.delete(
        f"/admin/courses/os/queues/{env.lesson_id}/entries/{env.entry_id}"
    ).status_code == 200
    joined = env.client.post("/courses/os/queue/join", json=payload)
    assert joined.status_code == 201
    assert joined.json()["entry"]["lesson_id"] == new_id


@pytest.mark.parametrize("next_week", [False, True])
def test_displaced_queue_can_be_archived_after_restart(displaced_queue, monkeypatch, next_week):
    env = displaced_queue
    now = NOW.replace(hour=11, minute=31) + timedelta(days=7 if next_week else 0)
    monkeypatch.setattr("queues.usecase.queue_service._now", lambda: now)
    week_start = MONDAY + timedelta(days=7 if next_week else 0)
    service = QueueService(env.service.repository)
    service.load_saved_week(COURSES, env.directory, week_start=week_start)
    history = AsyncMock()
    errors = asyncio.run(QueueHistoryService(service, history).archive_finished())
    assert errors == {}
    history.save.assert_awaited_once()
    snapshot = history.save.call_args.args[0]
    assert str(snapshot.id) == env.lesson_id
    assert str(snapshot.queue.entries[0].id) == env.entry_id
    assert service.get_lesson("os", UUID(env.lesson_id)) is None

    restarted = QueueService(service.repository)
    restarted.load_saved_week(COURSES, env.directory, week_start=week_start)
    assert restarted.get_lesson("os", UUID(env.lesson_id)) is None
    assert restarted.active_entry("os", "ivan") is None


@pytest.mark.parametrize("restart", [False, True])
def test_queue_remains_readable_when_source_lesson_is_deleted(displaced_queue, restart):
    env = displaced_queue
    path = env.directory / "suai" / "2026-autumn" / "teacher.yaml"
    path.write_text(yaml.safe_dump({"university": "suai", "days": {}}), encoding="utf-8")
    service = QueueService(env.service.repository) if restart else env.service
    service.load_saved_week(COURSES, env.directory, week_start=MONDAY)
    main.app.state.queue_service = service
    response = env.client.get(f"/courses/os/lessons/{env.lesson_id}/queue")
    assert response.status_code == 200
    assert response.json()["closed"] is True
    assert response.json()["waiting"][0]["id"] == env.entry_id
    assert env.client.get("/courses/os/lessons").json()["lessons"] == []
    service.call_next(UUID(env.lesson_id))
    response = env.client.get(f"/courses/os/lessons/{env.lesson_id}/queue")
    assert response.json()["called"][0]["id"] == env.entry_id
