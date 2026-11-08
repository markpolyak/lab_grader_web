import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import AsyncMock

import yaml
import pytest

import main
from queues.parser.parser import ScheduleParser


@pytest.fixture(autouse=True)
def disable_real_history(monkeypatch):
    monkeypatch.setenv("QUEUE_HISTORY_SPREADSHEET_ID", "")


def test_lifespan_loads_seed_and_reloads_after_parser_job(tmp_path, monkeypatch):
    directory = tmp_path / "suai" / "2026-autumn"
    directory.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "schedule/suai/2026-autumn/Поляк-Марк-Дмитриевич.yaml"
    document = yaml.safe_load(source.read_text())
    target = directory / "teacher.yaml"
    target.write_text(yaml.safe_dump(document))
    monkeypatch.setenv("SCHEDULE_DIR", str(tmp_path))
    monkeypatch.setenv("QUEUE_STATE_DIR", str(tmp_path / "queue_state"))
    monkeypatch.setenv("QUEUE_WEEK_START", "2026-11-09")
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter([
        ("os", {"name": "Операционные системы", "university": "ГУАП"}),
    ]))
    parser = Mock()
    monkeypatch.setattr(main, "ScheduleParser", Mock(return_value=parser))

    async def check():
        app = SimpleNamespace(state=SimpleNamespace())
        async with main.lifespan(app):
            service = app.state.queue_service
            lesson = service.schedules["os"].lessons[0]
            assert len(service.schedules["os"].lessons) == 3
            callback = parser.scheduler.add_listener.call_args.args[0]
            updated = deepcopy(document)
            updated["days"]["2026-11-09"][0]["room"] = "Новая аудитория"
            target.write_text(yaml.safe_dump(updated))
            callback(SimpleNamespace(job_id="schedule-refresh"))
            assert service.schedules["os"].lessons[0] is lesson
            assert lesson.room == "Новая аудитория"
            target.write_text("days: [broken")
            callback(SimpleNamespace(job_id="schedule-refresh"))
            assert service.schedules["os"].lessons[0] is lesson
        parser.shutdown.assert_called_once()

    asyncio.run(check())


def test_independent_sync_rolls_week_and_retries_bad_yaml(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    directory = tmp_path / "suai" / "2026-autumn"
    directory.mkdir(parents=True)
    target = directory / "teacher.yaml"
    item = {"subject": "ОС", "kind": "ЛР", "starts-at": "10:00",
            "ends-at": "11:00", "groups": ["4431"], "room": "1"}
    document = {"university": "suai", "days": {
        "2026-11-09": [item], "2026-11-16": [item],
    }}
    target.write_text(yaml.safe_dump(document))
    monkeypatch.setenv("SCHEDULE_DIR", str(tmp_path))
    monkeypatch.setenv("QUEUE_STATE_DIR", str(tmp_path / "queue_state"))
    monkeypatch.delenv("QUEUE_WEEK_START", raising=False)
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 11, 9, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr("queues.usecase.queue_service.datetime", clock)
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter([
        ("os", {"name": "ОС", "university": "ГУАП"}),
    ]))
    parser = Mock()
    monkeypatch.setattr(main, "ScheduleParser", Mock(return_value=parser))

    async def check():
        app = SimpleNamespace(state=SimpleNamespace())
        async with main.lifespan(app):
            service = app.state.queue_service
            old = service.schedules["os"].lessons[0]
            job = parser.scheduler.add_job.call_args
            assert job.kwargs["id"] == "queue-schedule-sync"
            assert job.kwargs["minute"] == "*"
            sync = job.args[0]
            # A timer callback advances the week without an HTTP request or parsing.
            clock.now.return_value = datetime(2026, 11, 16, tzinfo=ZoneInfo("Europe/Moscow"))
            sync()
            current = service.schedules["os"].lessons[0]
            assert service.schedules["os"].week_start.isoformat() == "2026-11-16"
            assert current.id != old.id
            assert service._lessons[old.id] is old
            updated = deepcopy(document)
            updated["days"]["2026-11-16"][0]["room"] = "2"
            target.write_text(yaml.safe_dump(updated))
            sync()
            assert current.room == "2"
            target.write_text("days: [broken")
            sync()
            assert service.schedules["os"].lessons[0] is current
            target.write_text(yaml.safe_dump(document))
            sync()
            assert current.room == "1"

    asyncio.run(check())


def test_empty_parser_config_still_starts_local_scheduler(tmp_path, monkeypatch):
    index = tmp_path / "index.yaml"
    index.write_text("schedules: []")
    monkeypatch.setenv("SCHEDULE_DIR", str(tmp_path))
    monkeypatch.setenv("QUEUE_STATE_DIR", str(tmp_path / "queue_state"))
    monkeypatch.setenv("SCHEDULE_CONFIG", str(index))
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter([]))
    parser = ScheduleParser(tmp_path)
    monkeypatch.setattr(main, "ScheduleParser", Mock(return_value=parser))

    async def check():
        app = SimpleNamespace(state=SimpleNamespace())
        async with main.lifespan(app):
            assert parser.scheduler.running
            assert parser.scheduler.get_job("queue-schedule-sync") is not None
            assert parser.scheduler.get_job("schedule-refresh") is None
        assert not parser.scheduler.running

    asyncio.run(check())


def test_history_jobs_are_wired_to_async_service(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from queues.domain.lesson import Lesson
    from queues.domain.schedule import Schedule

    monkeypatch.setenv("QUEUE_HISTORY_SPREADSHEET_ID", "test-history")
    monkeypatch.setenv("SCHEDULE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "iter_course_configs", lambda: iter([]))
    parser, history = Mock(), AsyncMock()
    monkeypatch.setattr(main, "ScheduleParser", Mock(return_value=parser))
    monkeypatch.setattr(main, "GoogleRepo", Mock(return_value=history))

    async def check():
        app = SimpleNamespace(state=SimpleNamespace())
        async with main.lifespan(app):
            jobs = {call.kwargs["id"]: call for call in parser.scheduler.add_job.call_args_list}
            daily = jobs["queue-history-daily"]
            assert daily.kwargs["hour"] == 23 and daily.kwargs["minute"] == 59
            assert jobs["queue-history-retry"].kwargs["kwargs"] == {"retry_only": True}
            end = datetime.now(timezone.utc) - timedelta(days=1)
            item = Lesson(starts_at=end - timedelta(hours=1), ends_at=end,
                          groups=("4431",), kind="ЛР")
            service = app.state.queue_service
            service._lessons[item.id] = item
            schedule = Schedule(course_id="os", course_name="ОС",
                                week_start=end.date(), lessons=[item])
            service._schedules[schedule.id] = schedule
            await asyncio.to_thread(daily.args[0])
            history.save.assert_awaited_once()
            assert item.id not in service._lessons

    asyncio.run(check())
