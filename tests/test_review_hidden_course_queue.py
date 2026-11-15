"""Ревью PR #66: скрытый курс с незавершённой записью роняет очереди других курсов в полночь.

Всё через публичный HTTP API (main.app): реальный index.yaml во временном каталоге,
реальная кнопка «удалить курс» (DELETE /courses/{id} -> status: hidden), реальное
файловое хранилище очередей. Подменены только часы очереди и чтение Google Sheets.
"""
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from grading.queue_eligibility import EligibleStudentLab
from queues.domain import Student
from queues.repositories.storage.file import FileQueueRepository
from queues.usecase.queue_service import QueueService

MONDAY = date(2026, 11, 9)
SCHEDULE_DIR = Path(__file__).resolve().parents[1] / "schedule"
COURSE_YAML = """course:
  name: {name}
  university: ГУАП
  google:
    spreadsheet: sheet
    student-name-column: 1
  labs:
    "1":
      short-name: ЛР1
"""


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path, monkeypatch):
    courses = tmp_path / "courses"
    courses.mkdir()
    (courses / "os.yaml").write_text(COURSE_YAML.format(name="Операционные системы"), encoding="utf-8")
    (courses / "net.yaml").write_text(COURSE_YAML.format(name="Сети ЭВМ"), encoding="utf-8")
    (courses / "index.yaml").write_text(
        'version: "1.0"\ncourses:\n'
        '  - {id: os-2026, file: os.yaml, status: active}\n'
        '  - {id: net-2026, file: net.yaml, status: active}\n', encoding="utf-8")
    monkeypatch.setattr(main, "COURSES_DIR", str(courses))
    monkeypatch.setattr(main, "INDEX_FILE", str(courses / "index.yaml"))
    monkeypatch.setattr(main.limiter, "enabled", False)  # лимиты запросов не относятся к дефекту
    monkeypatch.setenv("QUEUE_WEEK_START", MONDAY.isoformat())
    monkeypatch.setenv("SCHEDULE_DIR", str(SCHEDULE_DIR))
    clock = Clock(datetime.fromisoformat("2026-11-09T15:20:00+03:00"))
    monkeypatch.setattr("queues.usecase.queue_service._now", clock)
    # Только Google Sheets: студент petr из группы 4431, ЛР1 зачтена.
    monkeypatch.setattr(main, "_open_spreadsheet", lambda course_info: object())
    monkeypatch.setattr(main, "resolve_eligible_lab", lambda course, sheet, github, lab_id, groups:
                        EligibleStudentLab(Student("Петр", "Петров", "", github), "Петров Петр",
                                           "4431", lab_id, "ЛР1"))
    service = QueueService(FileQueueRepository(tmp_path / "queue_state"))
    monkeypatch.setattr(main.app.state, "queue_service", service, raising=False)

    def background_sync():
        # То же, что задача queue-schedule-sync (main.py:221-229), раз в минуту.
        try:
            service.load_saved_week(dict(main.iter_course_configs()), str(SCHEDULE_DIR), week_start=MONDAY)
        except Exception:
            pass
        try:
            service.reconcile()
        except Exception:
            pass

    background_sync()
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("admin_session", main.signer.sign(main.ADMIN_LOGIN.encode()).decode())
    return client, service, clock, background_sync


@pytest.mark.parametrize("hide_net", [False, True], ids=["control-net-visible", "net-hidden"])
def test_hidden_course_does_not_break_other_course_queues_after_midnight(env, hide_net):
    client, service, clock, background_sync = env
    os_lesson = next(l for l in service.schedules["os-2026"].lessons if l.groups == ("4431",))

    # 09.11 15:20: внештатное занятие курса net-2026, студент petr записался.
    created = client.post("/admin/courses/net-2026/queues", json={
        "group_ids": ["4431"], "lesson_starts_at": "2026-11-09T17:00:00+03:00",
        "lesson_ends_at": "2026-11-09T18:30:00+03:00", "closed": False})
    assert created.status_code == 201, created.text
    joined = client.post("/courses/net-2026/queue/join", json={
        "github": "petr", "lesson_id": created.json()["queue_id"], "lab_id": "1"})
    assert joined.status_code == 201, joined.text

    # Вечером преподаватель скрывает курс net-2026 кнопкой в админке.
    if hide_net:
        assert client.delete("/courses/net-2026").status_code == 200
    clock.now = datetime.fromisoformat("2026-11-09T21:00:00+03:00")
    background_sync()
    assert client.get("/courses/os-2026/lessons").status_code == 200  # пока всё работает

    # 10.11 00:00:30 и 00:05 — минутная фоновая задача отработала, дальше обычные запросы по os-2026.
    clock.now = datetime.fromisoformat("2026-11-10T00:00:30+03:00")
    background_sync()
    clock.now = datetime.fromisoformat("2026-11-10T00:05:00+03:00")
    background_sync()
    codes = {
        "GET /courses/os-2026/lessons": client.get("/courses/os-2026/lessons").status_code,
        "GET /courses/os-2026/lessons/{id}/queue":
            client.get(f"/courses/os-2026/lessons/{os_lesson.id}/queue").status_code,
        "GET /admin/courses/os-2026/queues": client.get("/admin/courses/os-2026/queues").status_code,
        "POST /admin/.../call-next":
            client.post(f"/admin/courses/os-2026/queues/{os_lesson.id}/call-next").status_code,
    }
    print(codes)
    try:
        service.reconcile()
    except Exception as exc:  # для наглядности причины в выводе pytest
        print("reconcile():", type(exc).__name__, exc)
    assert 500 not in codes.values(), codes

    # Expiry is durable, including when the hidden course is loaded again.
    from uuid import UUID
    from queues.domain.status import EntryStatus
    record = service.repository.get(UUID(created.json()["queue_id"]))
    assert record.entry_metadata[UUID(joined.json()["entry"]["id"])].status == EntryStatus.EXPIRED
    restored = QueueService(service.repository)
    restored.load_saved_week({"net-2026": {"name": "Сети ЭВМ", "university": "ГУАП"}},
                             str(SCHEDULE_DIR), week_start=MONDAY)
    restored.reconcile()
    assert restored.active_entry("net-2026", "petr") is None
