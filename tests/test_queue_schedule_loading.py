import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import yaml

import main


def test_lifespan_loads_seed_and_reloads_after_parser_job(tmp_path, monkeypatch):
    directory = tmp_path / "suai" / "2026-autumn"
    directory.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "schedule/suai/2026-autumn/Поляк-Марк-Дмитриевич.yaml"
    document = yaml.safe_load(source.read_text())
    target = directory / "teacher.yaml"
    target.write_text(yaml.safe_dump(document))
    monkeypatch.setenv("SCHEDULE_DIR", str(tmp_path))
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
            assert len(service.schedules["os"].lessons) == 4
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
