"""Invalid optional fields in one course must not affect its neighbours."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from queues.usecase.queue_service import QueueService


@pytest.mark.parametrize("university", [None, "", 42, [], {}, "ИТМО"])
@pytest.mark.parametrize("status", ["active", "hidden"])
def test_unconfigured_course_does_not_disable_supported_course(university, status):
    service = QueueService()
    service.generate_week({
        "good": {"name": "ОС", "university": "ГУАП"},
        "draft": {"name": "Черновик", "university": university, "_meta": {"status": status}},
    }, [], week_start=date(2026, 11, 9))
    assert set(service.schedules) == {"good"}


@pytest.mark.parametrize("university", ["ГУАП", " гуап ", "SUAI", " suai "])
def test_supported_university_names(university):
    service = QueueService()
    service.generate_week({"good": {"name": "ОС", "university": university},
                           "draft": {"name": "Черновик"}}, [], week_start=date(2026, 11, 9))
    assert set(service.schedules) == {"good"}


def test_invalid_google_preserves_valid_and_global_history(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("QUEUE_HISTORY_SPREADSHEET_ID", "global-history")
    monkeypatch.setenv("QUEUE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SCHEDULE_DIR", str(tmp_path / "schedule"))
    monkeypatch.setenv("QUEUE_WEEK_START", "2026-11-09")
    monkeypatch.setattr(main, "ScheduleParser", Mock())
    repository = Mock()
    monkeypatch.setattr(main, "GoogleRepo", repository)
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter([
        ("bad", {"google": None}),
        ("missing", {}),
        ("good", {"google": {"queue-history-spreadsheet": "course-history"}}),
    ]))

    async def check():
        async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
            pass

    asyncio.run(check())
    assert [call.args[1] for call in repository.call_args_list] == ["global-history", "course-history"]
    assert "bad" in caplog.text and "google" in caplog.text
