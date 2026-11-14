"""Чужой курс с пустым `university:` отключает очереди всех курсов ГУАП."""
import shutil
import textwrap
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import main
from queues.usecase import queue_service

ROOT = Path(main.__file__).resolve().parent
# Заготовка другого курса: поле university пока не заполнено.
DRAFT = textwrap.dedent("""\
    course:
      name: Черновик другого курса
      university:
    """)


@pytest.fixture
def env(tmp_path, monkeypatch):
    courses = tmp_path / "courses"
    courses.mkdir()
    shutil.copy(ROOT / "courses" / "operating-systems-2026.yaml", courses / "os.yaml")
    (courses / "index.yaml").write_text(
        "courses:\n  - id: os-2026\n    file: os.yaml\n    status: active\n", encoding="utf-8")
    # Сохранённое в репозитории расписание Поляка, без сети.
    shutil.copytree(ROOT / "schedule" / "suai", tmp_path / "schedule" / "suai")
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "COURSES_DIR", str(courses))
    monkeypatch.setattr(main, "INDEX_FILE", str(courses / "index.yaml"))
    monkeypatch.setattr(queue_service, "_now",
                        lambda: datetime(2026, 11, 10, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")))
    monkeypatch.setenv("SCHEDULE_CONFIG", str(tmp_path / "no-network.yaml"))
    monkeypatch.setenv("SCHEDULE_DIR", str(tmp_path / "schedule"))
    monkeypatch.setenv("QUEUE_STATE_DIR", str(tmp_path / "queue_state"))
    monkeypatch.setenv("QUEUE_WEEK_START", "2026-11-09")
    monkeypatch.delenv("QUEUE_HISTORY_SPREADSHEET_ID", raising=False)
    return courses


def test_control_os_queue_alone(env):
    with TestClient(main.app) as client:
        response = client.get("/courses/os-2026/lessons")
    assert response.status_code == 200, response.text
    assert response.json()["lessons"]


def test_hidden_draft_course_does_not_disable_os_queue(env):
    (env / "draft.yaml").write_text(DRAFT, encoding="utf-8")
    with open(env / "index.yaml", "a", encoding="utf-8") as index:
        index.write("  - id: draft\n    file: draft.yaml\n    status: hidden\n")
    with TestClient(main.app) as client:
        response = client.get("/courses/os-2026/lessons")
    assert response.status_code == 200, response.text
    assert response.json()["lessons"]


def test_hidden_draft_course_does_not_disable_teacher_queues(env):
    (env / "draft.yaml").write_text(DRAFT, encoding="utf-8")
    with open(env / "index.yaml", "a", encoding="utf-8") as index:
        index.write("  - id: draft\n    file: draft.yaml\n    status: hidden\n")
    with TestClient(main.app) as client:
        assert client.post("/admin/login", json={"login": "test", "password": "test"}).status_code == 200
        response = client.get("/admin/courses/os-2026/queues")
    assert response.status_code == 200, response.text


def test_course_uploaded_by_admin_does_not_disable_os_queue_after_restart(env):
    with TestClient(main.app) as client:
        assert client.get("/courses/os-2026/lessons").status_code == 200
        assert client.post("/admin/login", json={"login": "test", "password": "test"}).status_code == 200
        upload = client.post("/courses/upload",
                             files={"file": ("draft.yaml", DRAFT.encode(), "application/x-yaml")})
        assert upload.status_code == 200, upload.text   # загрузка принята без замечаний
        assert client.get("/courses").status_code == 200  # список курсов работает

    with TestClient(main.app) as client:  # рестарт backend
        response = client.get("/courses/os-2026/lessons")
    assert response.status_code == 200, response.text
    assert response.json()["lessons"]
