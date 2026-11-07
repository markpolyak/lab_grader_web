from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock
from threading import Event

import pytest
import yaml
from freezegun import freeze_time

from queues.parser.parser import ScheduleConfig, ScheduleParser
from queues.parser.vendor.suai_rasp.model import Lesson, Ref


@pytest.fixture
def config():
    return ScheduleConfig(
        university="suai", teacher="Преподаватель И.О.", semester="2030-autumn",
        starts_on=date(2030, 9, 2), ends_on=date(2030, 9, 16),
        first_week_start=date(2030, 9, 2),
    )


def test_refresh_changes_and_interval(tmp_path, config):
    item = {"subject": "ОС", "kind": "ЛР", "groups": ["4431"], "room": "1"}
    source = Mock(return_value=({"2030-09-02": [item, item]}, []))
    parser = ScheduleParser(tmp_path, {"suai": source})
    assert parser.refresh(config)
    path = parser.path_for(config)
    first = yaml.safe_load(path.read_text())
    assert first["days"]["2030-09-02"] == [item]
    assert not parser.refresh(config)
    source.assert_called_once()
    assert not parser.refresh(config, force=True)
    second = yaml.safe_load(path.read_text())
    assert first["updated-at"] == second["updated-at"]
    source.return_value = ({"2030-09-02": [item | {"room": "2"}]}, [])
    assert parser.refresh(config, force=True)
    assert yaml.safe_load(path.read_text())["days"]["2030-09-02"][0]["room"] == "2"


def test_source_failure_preserves_file(tmp_path, config):
    source = Mock(return_value=({}, []))
    parser = ScheduleParser(tmp_path, {"suai": source})
    parser.refresh(config)
    before = parser.path_for(config).read_bytes()
    source.side_effect = ConnectionError("offline")
    with pytest.raises(ConnectionError):
        parser.refresh(config, force=True)
    assert parser.path_for(config).read_bytes() == before


def test_due_refresh_and_university_isolation(tmp_path, config):
    source = Mock(return_value=({}, []))
    parser = ScheduleParser(tmp_path, {"suai": source, "other": source})
    parser.refresh(config)
    path = parser.path_for(config)
    document = yaml.safe_load(path.read_text())
    document["last-checked-at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    path.write_text(yaml.safe_dump(document))
    parser.refresh(config)
    assert source.call_count == 2
    other = replace(config, university="other")
    parser.refresh(other)
    assert parser.path_for(other) != path
    assert path.exists() and parser.path_for(other).exists()
    with pytest.raises(ValueError, match="Unsupported university"):
        parser.refresh(replace(config, university="unknown"))


def test_suai_dates_and_parity(tmp_path, config, monkeypatch):
    from queues.parser.vendor.suai_rasp import fetch, parse

    client = Mock()
    monkeypatch.setattr(fetch, "Fetcher", Mock(return_value=client))
    monkeypatch.setattr(parse, "parse_directories", lambda _: {"teachers": {1: config.teacher}})
    def lesson(week, subject, day="Понедельник", slot=1):
        return Lesson(day, slot, week, "ЛР", subject, Ref(1, "1"), None,
                      groups=[Ref(1, "4431")])
    monkeypatch.setattr(parse, "parse_lessons", lambda _: [
        lesson(1, "odd"), lesson(2, "even"), lesson(0, "all"),
        lesson(0, "unscheduled", None, None),
    ])
    parser = ScheduleParser(tmp_path)
    parser.refresh(config)
    document = yaml.safe_load(parser.path_for(config).read_text())
    assert {x["subject"] for x in document["days"]["2030-09-02"]} == {"odd", "all"}
    assert {x["subject"] for x in document["days"]["2030-09-09"]} == {"even", "all"}
    assert {x["subject"] for x in document["days"]["2030-09-16"]} == {"odd", "all"}
    assert document["unscheduled"][0]["subject"] == "unscheduled"
    assert document["days"]["2030-09-02"][0]["starts-at"] == "09:30"
    client.by_teacher.assert_called_once_with(1)
    client.session.close.assert_called_once()
    fetch.Fetcher.assert_called_once_with(cache_dir=tmp_path / ".cache" / "suai", ttl=0)


def test_scheduler_retries_each_source_and_stops(tmp_path, config):
    parser = ScheduleParser(tmp_path)
    parser.scheduler = Mock(running=True)
    parser.start([config])
    kwargs = parser.scheduler.add_job.call_args.kwargs
    assert kwargs["max_instances"] == 1
    assert kwargs["hours"] == 1
    parser.scheduler.start.assert_called_once()
    parser.refresh = Mock(side_effect=[ConnectionError("offline"), True])
    parser._refresh_due([config, replace(config, teacher="Другой")])
    assert parser.refresh.call_count == 2
    parser.shutdown()
    parser.scheduler.shutdown.assert_called_once_with(wait=True)


def test_invalid_interval_and_first_week(config):
    assert config.refresh_interval_days == 7
    with pytest.raises(ValueError):
        replace(config, refresh_interval_days=0)
    with pytest.raises(ValueError):
        replace(config, first_week_start=date(2030, 9, 3))


def test_config_and_real_scheduler(tmp_path):
    index = tmp_path / "index.yaml"
    index.write_text("""schedules:
  - university: suai
    teacher: Test Teacher
    semester:
      id: 2030-autumn
      starts-on: 2030-09-02
      ends-on: 2030-09-16
      first-week-start: 2030-09-02
""")
    configs = ScheduleParser.load_configs(index)
    assert configs[0].refresh_interval_days == 7
    fetched = Event()
    def provider(config):
        fetched.set()
        return {}, []
    parser = ScheduleParser(tmp_path, {"suai": provider})
    try:
        parser.start(configs)
        assert fetched.wait(timeout=5), "APScheduler did not run the initial refresh"
    finally:
        parser.shutdown()
    assert parser.path_for(configs[0]).exists()


@freeze_time("2026-11-07 12:00:00")
def test_automatic_semester(tmp_path, monkeypatch):
    from queues.parser.vendor.suai_rasp import fetch, parse
    client = Mock()
    monkeypatch.setattr(fetch, "Fetcher", Mock(return_value=client))
    monkeypatch.setattr(parse, "parse_meta", lambda _: {
        "semester": "Осенний", "academic_year": "2026/27",
        "today": "суббота, 7 ноября 2026", "week_number": 10,
    })
    parser = ScheduleParser(tmp_path)
    config = ScheduleConfig("suai", "Поляк Марк Дмитриевич")
    resolved = parser._resolve_semester(config)
    assert resolved.semester == "2026-autumn"
    assert resolved.starts_on == date(2026, 9, 1)
    assert resolved.first_week_start == date(2026, 8, 31)
    assert resolved.ends_on == date(2027, 1, 3)
    assert resolved.estimated_end
    assert parser._resolve_semester(config) == resolved
    fetch.Fetcher.assert_called_once()


def test_full_teacher_name(tmp_path, config, monkeypatch):
    from queues.parser.vendor.suai_rasp import fetch, parse
    client = Mock()
    monkeypatch.setattr(fetch, "Fetcher", Mock(return_value=client))
    monkeypatch.setattr(parse, "parse_directories", lambda _: {"teachers": {
        39614: "Поляк М.Д., старший преподаватель", 1: "Полякова Т.Г.",
    }})
    monkeypatch.setattr(parse, "parse_lessons", lambda _: [
        Lesson("Понедельник", 1, 0, "ЛР", "ОС", None, None),
    ])
    ScheduleParser(tmp_path)._fetch_suai(replace(config, teacher="Поляк Марк Дмитриевич"))
    client.by_teacher.assert_called_once_with(39614)
