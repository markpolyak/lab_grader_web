"""Ревью PR #66: «Завершить занятие» без таблицы истории Google замораживает очередь.

Конфигурация по умолчанию: QUEUE_HISTORY_SPREADSHEET_ID пуст (как в .env.example),
ни в одном courses/*.yaml нет google.queue-history-spreadsheet.
Реальный main.lifespan и courses/*.yaml, файловое хранилище в tmp_path.
Подменены только часы, сетевой парсер ГУАП и gspread (таблица оценок:
лист 4431, ЛР0 проверена у обоих студентов).
"""
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import main

NOW = datetime.fromisoformat("2026-11-09T15:20:00+03:00")  # пн, идёт ЛР 4431 15:10-16:40
REPO = Path(__file__).resolve().parents[2]
COURSE = "os-2026"


class _Sheet:
    def get_all_values(self):
        return [["", "", "GitHub", "ЛР0"],
                ["№", "ФИО", "", "ЛР0"],
                ["1", "Иванов Иван", "ivanov", "v"],
                ["2", "Петров Пётр", "petrov", "v-1"]]


class _Book:
    def worksheet(self, title):
        if title != "4431":
            raise KeyError(title)
        return _Sheet()


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("QUEUE_HISTORY_SPREADSHEET_ID", "")  # как в .env.example
    monkeypatch.setenv("QUEUE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SCHEDULE_DIR", str(REPO / "schedule"))
    monkeypatch.setenv("QUEUE_WEEK_START", "2026-11-09")
    monkeypatch.setattr("queues.usecase.queue_service._now", lambda: NOW)
    monkeypatch.setattr(main, "ScheduleParser", Mock(return_value=Mock()))  # без сети
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "sheets_client", lambda: Mock(open_by_key=lambda key: _Book()))
    return main.app


def _admin(client):
    client.cookies.set("admin_session", main.signer.sign(main.ADMIN_LOGIN.encode()).decode())


def _join(client, github, lesson_id):
    client.cookies.clear()
    return client.post(f"/courses/{COURSE}/queue/join",
                       json={"github": github, "lesson_id": str(lesson_id), "lab_id": "0"})


def test_complete_without_history_sheet_does_not_freeze_queue(app):
    with TestClient(app) as client:
        service = app.state.queue_service
        lesson = next(l for l in service.schedules[COURSE].lessons
                      if "4431" in l.groups and l.starts_at.date().isoformat() == "2026-11-09")
        base = f"/admin/courses/{COURSE}/queues/{lesson.id}"
        ids = {}
        for gh in ("ivanov", "petrov"):
            r = _join(client, gh, lesson.id)
            assert r.status_code == 201, r.text
            ids[gh] = r.json()["entry"]["id"]

        _admin(client)
        complete = client.post(base + "/complete")      # «Завершить занятие»
        detail = client.get(base)
        call_next = client.post(base + "/call-next")    # «Вызвать следующего»
        extra = client.post(f"/admin/courses/{COURSE}/queues", json={
            "group_ids": ["4431"], "closed": False,
            "lesson_starts_at": "2026-11-09T17:00:00+03:00",
            "lesson_ends_at": "2026-11-09T18:30:00+03:00",
        })
        assert extra.status_code == 201, extra.text
        client.cookies.clear()
        leave = client.post(f"/courses/{COURSE}/queue/leave",
                            json={"github": "petrov", "entry_id": ids["petrov"]})
        join_extra = _join(client, "petrov", extra.json()["queue_id"])
        jobs = [c.kwargs.get("id")
                for c in main.ScheduleParser.return_value.scheduler.add_job.call_args_list]

    with TestClient(app) as client:  # рестарт backend
        _admin(client)
        detail_restart = client.get(base)
        complete_again = client.post(base + "/complete")

    def body(r):
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

    report = {
        "complete": (complete.status_code, body(complete)),
        "archive_pending": detail.status_code == 200 and detail.json().get("archive_pending"),
        "call-next": (call_next.status_code, body(call_next)),
        "leave (petrov)": (leave.status_code, body(leave)),
        "join extra lesson (petrov)": (join_extra.status_code, body(join_extra)),
        "scheduled jobs": jobs,
        "archive_pending after restart":
            detail_restart.status_code == 200 and detail_restart.json().get("archive_pending"),
        "complete after restart": (complete_again.status_code, body(complete_again)),
    }
    for key, value in report.items():
        print(f"{key:30} -> {value}")

    # Допустимо любое исправление: либо занятие завершается локально (остаток -> expired,
    # допущение 4 PR), либо отказ проходит без побочных эффектов и очередь работает дальше.
    if complete.status_code != 200:
        assert detail.json()["archive_pending"] is False, \
            "отказ завершить занятие оставил очередь в archive_pending"
        assert call_next.status_code == 200, "преподаватель не может вызвать ожидающего"
        assert leave.status_code == 200, "студент не может выйти из очереди"
        assert detail_restart.json()["archive_pending"] is False, "после рестарта очередь заморожена"
    assert join_extra.status_code == 201, "студент не может записаться на доп. занятие того же дня"
