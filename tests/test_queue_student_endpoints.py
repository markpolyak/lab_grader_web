from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main
from grading.queue_eligibility import (
    EligibleStudentLab,
    LabNotPassedError,
    SheetsUnavailableError,
    StudentInfo,
    StudentNotFoundError,
)
from queues.domain import Student
from queues.usecase.queue_service import QueueService

MONDAY = date(2026, 11, 9)
COURSES = {
    "os-2026": {
        "name": "Операционные системы",
        "university": "ГУАП",
        "google": {"spreadsheet": "sheet", "student-name-column": 1},
        "labs": {"1": {"short-name": "ЛР1"}},
    }
}
SCHEDULE_DIR = Path(__file__).resolve().parents[1] / "schedule"


@pytest.fixture
def endpoint(monkeypatch):
    service = QueueService()
    service.load_saved_week(COURSES, SCHEDULE_DIR, week_start=MONDAY)
    monkeypatch.setattr(main.app.state, "queue_service", service, raising=False)
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter(COURSES.items()))
    monkeypatch.setenv("QUEUE_WEEK_START", MONDAY.isoformat())
    monkeypatch.setattr(
        main,
        "get_course_by_id",
        lambda course_id: COURSES[course_id]
        if course_id in COURSES
        else (_ for _ in ()).throw(main.HTTPException(404, "Курс не найден")),
    )
    monkeypatch.setattr(main, "_open_spreadsheet", lambda course_info: object())
    monkeypatch.setattr(
        main,
        "resolve_student",
        lambda *args: StudentInfo(
            student=Student("Иван", "Иванов", "", "student"),
            full_name="Иванов Иван",
            group_id="4431",
        ),
    )
    monkeypatch.setattr(
        main,
        "resolve_eligible_lab",
        lambda *args: EligibleStudentLab(
            student=Student("Иван", "Иванов", "", "student"),
            full_name="Иванов Иван",
            group_id="4431",
            lab_id="1",
            lab_name="ЛР1",
        ),
    )
    return TestClient(main.app), service


def open_first_lesson(service):
    lesson = service.schedules["os-2026"].lessons[0]
    lesson.queue.closed = False
    return lesson


def test_state_requires_group_id(endpoint):
    client, _ = endpoint
    assert client.get("/courses/os-2026/queue?github=student").status_code == 422


def test_public_lesson_queue_has_fifo_names_without_private_admin_fields(endpoint):
    client, service = endpoint
    lesson = open_first_lesson(service)
    for name, github in [("Первый", "private-first"), ("Второй", "private-second")]:
        service.join(lesson.id, EligibleStudentLab(
            student=Student(name, "Студент", "", github),
            full_name=f"Студент {name}", group_id="4431", lab_id="1", lab_name="ЛР1",
        ))
    first = service.call_next(lesson.id)
    response = client.get(f"/courses/os-2026/lessons/{lesson.id}/queue")
    assert response.status_code == 200
    data = response.json()
    assert data["course_name"] == "Операционные системы"
    assert data["closed"] is False
    assert data["labs"] == [{"id": "1", "name": "ЛР1"}]
    assert data["waiting"][0]["full_name"] == "Студент Второй"
    assert data["waiting"][0]["position"] == 1
    assert data["called"][0]["id"] == str(first.id)
    assert "private-first" not in response.text and "private-second" not in response.text
    assert "finished" not in data
    assert "result" not in data["called"][0]
    service.finish(lesson.id, first.id, "defended")
    data = client.get(f"/courses/os-2026/lessons/{lesson.id}/queue").json()
    assert data["called"] == []
    assert len(data["waiting"]) == 1


def test_public_lesson_queue_rejects_unknown_lesson_and_course(endpoint):
    client, service = endpoint
    lesson = open_first_lesson(service)
    assert client.get(f"/courses/os-2026/lessons/{uuid4()}/queue").status_code == 404
    assert client.get(f"/courses/missing/lessons/{lesson.id}/queue").status_code == 404


def test_public_lesson_queue_omits_unpublished_labs(endpoint, monkeypatch):
    client, service = endpoint
    lesson = open_first_lesson(service)
    monkeypatch.setattr(main, "find_public_lab_config", lambda *args: None)
    data = client.get(f"/courses/os-2026/lessons/{lesson.id}/queue").json()
    assert data["labs"] == []


def test_join_checks_submitted_full_name_against_sheet(endpoint):
    client, service = endpoint
    lesson = open_first_lesson(service)
    payload = {"github": "student", "lesson_id": str(lesson.id), "lab_id": "1", "full_name": "Другой Студент"}
    response = client.post("/courses/os-2026/queue/join", json=payload)
    assert response.status_code == 403
    assert response.json()["detail"] == "STUDENT_NAME_MISMATCH"
    assert lesson.queue.entries == []
    payload["full_name"] = "  ИВАНОВ    Иван  "
    response = client.post("/courses/os-2026/queue/join", json=payload)
    assert response.status_code == 201
    assert response.json()["full_name"] == "Иванов Иван"
    assert len(lesson.queue.entries) == 1


def test_state_without_active_entry(endpoint):
    client, _ = endpoint
    response = client.get("/courses/os-2026/queue?github=student&group_id=4431")

    assert response.status_code == 200
    assert response.json() == {
        "github": "student",
        "group_id": "4431",
        "full_name": "Иванов Иван",
        "entry": None,
    }


def test_join_leave_and_state_flow(endpoint):
    client, service = endpoint
    lesson = open_first_lesson(service)

    joined = client.post(
        "/courses/os-2026/queue/join",
        json={"github": "student", "lesson_id": str(lesson.id), "lab_id": "1"},
    )
    assert joined.status_code == 201
    entry = joined.json()["entry"]
    assert entry["status"] == "waiting"
    assert entry["position"] == 1
    assert joined.json()["full_name"] == "Иванов Иван"

    state = client.get("/courses/os-2026/queue?github=student&group_id=4431")
    assert state.json()["entry"]["id"] == entry["id"]

    left = client.post(
        "/courses/os-2026/queue/leave",
        json={"github": "student", "entry_id": entry["id"]},
    )
    assert left.status_code == 200
    assert left.json() == {"left": True}
    assert client.get(
        "/courses/os-2026/queue?github=student&group_id=4431"
    ).json()["entry"] is None

    again = client.post(
        "/courses/os-2026/queue/join",
        json={"github": "student", "lesson_id": str(lesson.id), "lab_id": "1"},
    )
    assert again.status_code == 201

    service.call_next(lesson.id)
    called = client.get("/courses/os-2026/queue?github=student&group_id=4431")
    assert called.json()["entry"]["status"] == "called"
    assert called.json()["entry"]["position"] is None


def test_join_rejects_closed_queue(endpoint):
    client, service = endpoint
    lesson = service.schedules["os-2026"].lessons[0]

    response = client.post(
        "/courses/os-2026/queue/join",
        json={"github": "student", "lesson_id": str(lesson.id), "lab_id": "1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "QUEUE_CLOSED"


def test_join_rejects_duplicate_active_entry(endpoint):
    client, service = endpoint
    lesson = open_first_lesson(service)
    payload = {"github": "student", "lesson_id": str(lesson.id), "lab_id": "1"}

    assert client.post("/courses/os-2026/queue/join", json=payload).status_code == 201
    duplicate = client.post("/courses/os-2026/queue/join", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "ALREADY_QUEUED"


def test_join_reports_lab_not_passed(endpoint, monkeypatch):
    client, service = endpoint
    lesson = open_first_lesson(service)
    monkeypatch.setattr(
        main, "resolve_eligible_lab", lambda *args: (_ for _ in ()).throw(LabNotPassedError("x"))
    )

    response = client.post(
        "/courses/os-2026/queue/join",
        json={"github": "student", "lesson_id": str(lesson.id), "lab_id": "1"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "LAB_NOT_PASSED"


def test_join_reports_group_not_allowed(endpoint, monkeypatch):
    client, service = endpoint
    lesson = open_first_lesson(service)
    monkeypatch.setattr(
        main,
        "resolve_eligible_lab",
        lambda *args: EligibleStudentLab(
            student=Student("Иван", "Иванов", "", "student"),
            full_name="Иванов Иван",
            group_id="9999",
            lab_id="1",
            lab_name="ЛР1",
        ),
    )

    response = client.post(
        "/courses/os-2026/queue/join",
        json={"github": "student", "lesson_id": str(lesson.id), "lab_id": "1"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "GROUP_NOT_ALLOWED"


def test_join_unknown_lesson_is_404(endpoint):
    client, _ = endpoint
    response = client.post(
        "/courses/os-2026/queue/join",
        json={"github": "student", "lesson_id": str(uuid4()), "lab_id": "1"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "LESSON_NOT_FOUND"


def test_state_reports_missing_student_and_unavailable_sheets(endpoint, monkeypatch):
    client, _ = endpoint
    monkeypatch.setattr(
        main,
        "resolve_student",
        lambda *args: (_ for _ in ()).throw(StudentNotFoundError("student")),
    )
    assert client.get(
        "/courses/os-2026/queue?github=student&group_id=4431"
    ).json()["detail"] == "STUDENT_NOT_FOUND"

    monkeypatch.setattr(
        main,
        "resolve_student",
        lambda *args: (_ for _ in ()).throw(SheetsUnavailableError()),
    )
    assert client.get(
        "/courses/os-2026/queue?github=student&group_id=4431"
    ).status_code == 503


def test_leave_rejects_foreign_and_unknown_entry(endpoint):
    client, service = endpoint
    lesson = open_first_lesson(service)
    entry = client.post(
        "/courses/os-2026/queue/join",
        json={"github": "student", "lesson_id": str(lesson.id), "lab_id": "1"},
    ).json()["entry"]

    foreign = client.post(
        "/courses/os-2026/queue/leave",
        json={"github": "other", "entry_id": entry["id"]},
    )
    assert foreign.status_code == 403
    assert foreign.json()["detail"] == "FORBIDDEN"

    unknown = client.post(
        "/courses/os-2026/queue/leave",
        json={"github": "student", "entry_id": str(uuid4())},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "ENTRY_NOT_FOUND"
