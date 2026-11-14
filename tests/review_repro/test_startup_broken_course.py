"""Регрессия: один курс с пустой секцией `google:` роняет запуск всего backend."""
import textwrap

import pytest
from fastapi.testclient import TestClient

import main

GOOD = """\
    course:
      name: Рабочий курс
      university: ИТМО
      google:
        spreadsheet: sheet-id
      labs: {}
    """
DRAFT = "course:\n  name: Черновик\n  university: ИТМО\n  google:\n  labs: {}\n"


@pytest.fixture(params=["", " ''", " TODO", " []", " {}"])
def draft(request):
    return DRAFT.replace("google:\n", f"google:{request.param}\n")


@pytest.fixture
def courses_dir(tmp_path, monkeypatch):
    courses = tmp_path / "courses"
    courses.mkdir()
    (courses / "index.yaml").write_text(
        "courses:\n  - id: good-course\n    file: good.yaml\n    status: active\n",
        encoding="utf-8")
    (courses / "good.yaml").write_text(textwrap.dedent(GOOD), encoding="utf-8")
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.setattr(main, "COURSES_DIR", str(courses))
    monkeypatch.setattr(main, "INDEX_FILE", str(courses / "index.yaml"))
    monkeypatch.setenv("SCHEDULE_CONFIG", str(tmp_path / "no-schedule.yaml"))
    monkeypatch.setenv("SCHEDULE_DIR", str(tmp_path / "schedule"))
    monkeypatch.setenv("QUEUE_STATE_DIR", str(tmp_path / "queue_state"))
    monkeypatch.delenv("QUEUE_HISTORY_SPREADSHEET_ID", raising=False)
    return courses


def test_hidden_draft_course_does_not_block_startup(courses_dir, draft):
    (courses_dir / "index.yaml").write_text(
        "courses:\n"
        "  - id: good-course\n    file: good.yaml\n    status: active\n"
        "  - id: draft-course\n    file: draft.yaml\n    status: hidden\n",
        encoding="utf-8")
    (courses_dir / "draft.yaml").write_text(draft, encoding="utf-8")
    with TestClient(main.app) as client:   # настоящий lifespan, как у uvicorn
        response = client.get("/courses")
    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == ["good-course"]


def test_course_uploaded_via_admin_does_not_break_next_restart(courses_dir, draft):
    # 1. Сервис работает, преподаватель загружает заготовку курса через админку.
    with TestClient(main.app) as client:
        login = client.post("/admin/login",
                            json={"login": main.ADMIN_LOGIN, "password": main.ADMIN_PASSWORD})
        assert login.status_code == 200
        upload = client.post("/courses/upload",
                             files={"file": ("draft.yaml", draft.encode("utf-8"), "application/x-yaml")})
        assert upload.status_code == 200, upload.text
    # 2. Рестарт backend.
    with TestClient(main.app) as client:
        response = client.get("/courses")
    assert response.status_code == 200
    assert "good-course" in [c["id"] for c in response.json()]
