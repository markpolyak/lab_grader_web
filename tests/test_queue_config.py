"""
Тесты секции course.queue: разбор конфигурации очереди на защиту и правило
ячейки «работа прошла проверку» (grading/defense_queue.py).

Реестр источников расписания (grading/schedule) проверяется своими тестами;
здесь он подменён, чтобы ошибки конфигурации очереди проверялись отдельно от
разбора расписания. Сквозная проверка с настоящим ручным расписанием - в
TestWithRealRegistry.
"""
import logging
import os
import sys
from datetime import timedelta, timezone

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grading.schedule as schedule_registry  # noqa: E402
from grading.defense_queue import (  # noqa: E402
    DEFAULT_DEFENDED_PREFIX,
    LAB_DEFENDED,
    LAB_NOT_PASSED,
    LAB_PASSED,
    QueueConfigError,
    QueueSettings,
    lab_cell_status,
    parse_queue_config,
    parse_utc_offset,
    queue_flag,
    warn_about_queue_config,
)
from grading.schedule.base import ScheduleConfigError, SourceContext  # noqa: E402
from grading.sheets_client import can_overwrite_cell  # noqa: E402

MSK = timezone(timedelta(hours=3))


@pytest.fixture
def registry_calls(monkeypatch):
    """Подменённый build_schedule_source: запоминает вызовы, source="broken" отвергает."""
    calls = []

    def fake_build(cfg, ctx):
        calls.append((cfg, ctx))
        if cfg.get("source") == "broken":
            raise ScheduleConfigError("неизвестный источник расписания 'broken'")
        return object()

    monkeypatch.setattr(schedule_registry, "build_schedule_source", fake_build, raising=False)
    return calls


def course(queue=None, timezone_str="UTC+3", **extra) -> dict:
    """course_info курса с секцией queue (None - секции нет)."""
    info = {"name": "Операционные системы", "timezone": timezone_str, **extra}
    if queue is not None:
        info["queue"] = queue
    return info


class TestQueueFlag:
    """queue_flag - для списков курсов: без разбора и без исключений."""

    @pytest.mark.parametrize("info", [
        {},
        {"queue": None},
        {"queue": "yes"},
        {"queue": []},
        {"queue": {}},
        {"queue": {"enabled": False}},
        {"queue": {"enabled": "true"}},
        {"queue": {"enabled": 1}},
        None,
        "not a dict",
    ])
    def test_off(self, info):
        assert queue_flag(info) is False

    def test_on_even_with_broken_rest_of_section(self):
        assert queue_flag({"queue": {"enabled": True, "open-before-minutes": "много"}}) is True


class TestParseUtcOffset:
    @pytest.mark.parametrize("text, hours", [
        ("UTC+3", 3),
        ("UTC-5", -5),
        ("UTC+0", 0),
        ("UTC+12", 12),
        ("UTC-12", -12),
        (" UTC+3 ", 3),
    ])
    def test_valid(self, text, hours):
        assert parse_utc_offset(text) == timezone(timedelta(hours=hours))

    @pytest.mark.parametrize("text", [
        None, "", "UTC", "MSK", "Europe/Moscow", "UTC+3:30", "UTC+15", "UTC+100",
        "utc+3", "GMT+3", "UTC 3", "UTC+3x", 3,
    ])
    def test_invalid(self, text):
        with pytest.raises(QueueConfigError) as exc:
            parse_utc_offset(text)
        assert repr(text) in str(exc.value)


class TestParseQueueConfig:
    def test_no_section_means_no_queue(self, registry_calls):
        assert parse_queue_config(course()) is None
        assert parse_queue_config(course(queue=None)) is None
        assert registry_calls == []

    @pytest.mark.parametrize("queue", [{}, {"enabled": False}])
    def test_disabled_section_is_not_read(self, queue, registry_calls):
        # Выключенная очередь не требует timezone и не проверяет остальное.
        broken = {**queue, "open-before-minutes": -1, "schedule": {"source": "broken"}}
        assert parse_queue_config(course(queue=broken, timezone_str=None)) is None
        assert registry_calls == []

    def test_defaults(self, registry_calls):
        settings = parse_queue_config(course(queue={"enabled": True}))
        assert settings == QueueSettings(
            open_before=timedelta(minutes=15),
            defended_prefix=DEFAULT_DEFENDED_PREFIX,
            tz=MSK,
            schedule_cfg=None,
        )
        # Без расписания очередь на весь день, реестр не нужен.
        assert registry_calls == []

    def test_full_section(self, registry_calls):
        schedule = {"source": "guap", "teacher-id": 39614}
        settings = parse_queue_config(course(
            queue={"enabled": True, "open-before-minutes": 30, "defended-prefix": " зщ ", "schedule": schedule},
            timezone_str="UTC+5",
        ))
        assert settings.open_before == timedelta(minutes=30)
        assert settings.defended_prefix == "зщ"
        assert settings.tz == timezone(timedelta(hours=5))
        assert settings.schedule_cfg is schedule

    def test_schedule_is_checked_without_network_and_db(self, registry_calls):
        schedule = {"source": "guap", "teacher-id": 39614}
        parse_queue_config(course(queue={"enabled": True, "schedule": schedule}, timezone_str="UTC+5"))
        assert len(registry_calls) == 1
        cfg, ctx = registry_calls[0]
        assert cfg is schedule
        assert isinstance(ctx, SourceContext)
        assert ctx.snapshots is None
        assert ctx.course_tz == timezone(timedelta(hours=5))

    def test_schedule_error_becomes_queue_config_error(self, registry_calls):
        with pytest.raises(QueueConfigError, match="queue.schedule: неизвестный источник"):
            parse_queue_config(course(queue={"enabled": True, "schedule": {"source": "broken"}}))

    @pytest.mark.parametrize("schedule", ["guap", ["guap"], 1])
    def test_schedule_must_be_a_mapping(self, schedule, registry_calls):
        with pytest.raises(QueueConfigError, match="queue.schedule"):
            parse_queue_config(course(queue={"enabled": True, "schedule": schedule}))
        assert registry_calls == []

    @pytest.mark.parametrize("queue", ["yes", ["enabled"], True, 1])
    def test_section_must_be_a_mapping(self, queue):
        with pytest.raises(QueueConfigError, match="словарём"):
            parse_queue_config(course(queue=queue))

    @pytest.mark.parametrize("enabled", ["true", "yes", 1, 0, None])
    def test_enabled_must_be_a_bool(self, enabled):
        with pytest.raises(QueueConfigError) as exc:
            parse_queue_config(course(queue={"enabled": enabled}))
        assert repr(enabled) in str(exc.value)

    @pytest.mark.parametrize("minutes", [0, 15, 180])
    def test_open_before_bounds(self, minutes):
        settings = parse_queue_config(course(queue={"enabled": True, "open-before-minutes": minutes}))
        assert settings.open_before == timedelta(minutes=minutes)

    @pytest.mark.parametrize("minutes", [-1, 181, 15.0, "15", True, False, None, [15]])
    def test_open_before_invalid(self, minutes):
        with pytest.raises(QueueConfigError) as exc:
            parse_queue_config(course(queue={"enabled": True, "open-before-minutes": minutes}))
        assert "open-before-minutes" in str(exc.value)
        assert repr(minutes) in str(exc.value)

    @pytest.mark.parametrize("prefix", ["", "   ", None, 1, ["vv"]])
    def test_defended_prefix_invalid(self, prefix):
        with pytest.raises(QueueConfigError, match="defended-prefix"):
            parse_queue_config(course(queue={"enabled": True, "defended-prefix": prefix}))

    def test_unknown_key_is_an_error(self):
        # Опечатка, которая иначе молча оставила бы 15 минут по умолчанию.
        with pytest.raises(QueueConfigError, match="opens-before-minutes"):
            parse_queue_config(course(queue={"enabled": True, "opens-before-minutes": 30}))

    @pytest.mark.parametrize("timezone_str", [None, "MSK", "UTC+3:30"])
    def test_enabled_queue_needs_a_timezone(self, timezone_str):
        info = course(queue={"enabled": True}, timezone_str=timezone_str)
        if timezone_str is None:
            del info["timezone"]
        with pytest.raises(QueueConfigError, match="timezone"):
            parse_queue_config(info)


class TestWarnAboutQueueConfig:
    """Проверка при старте только пишет в лог и никогда не бросает."""

    def test_valid_config_is_silent(self, caplog):
        with caplog.at_level(logging.WARNING, logger="grading.defense_queue"):
            warn_about_queue_config("os.yaml", course(queue={"enabled": True}))
        assert caplog.records == []

    def test_config_error_is_logged_with_filename(self, caplog):
        with caplog.at_level(logging.WARNING, logger="grading.defense_queue"):
            warn_about_queue_config("os.yaml", course(queue={"enabled": True, "open-before-minutes": -5}))
        assert len(caplog.records) == 1
        assert "os.yaml" in caplog.text
        assert "open-before-minutes" in caplog.text

    def test_unexpected_exception_is_logged_not_raised(self, monkeypatch, caplog):
        def explode(cfg, ctx):
            raise RuntimeError("registry is broken")

        monkeypatch.setattr(schedule_registry, "build_schedule_source", explode, raising=False)
        with caplog.at_level(logging.WARNING, logger="grading.defense_queue"):
            warn_about_queue_config("os.yaml", course(queue={"enabled": True, "schedule": {"source": "guap"}}))
        assert "registry is broken" in caplog.text

    @pytest.mark.parametrize("info", [None, "text", {"queue": "yes"}, {"queue": {"enabled": True}}])
    def test_garbage_does_not_raise(self, info):
        # {"queue": {"enabled": True}} без timezone - тоже только warning.
        warn_about_queue_config("broken.yaml", info)


class TestWithRealRegistry:
    """Сквозная проверка: ошибки ручного расписания доходят до конфига очереди."""

    def manual(self, start='"15:10"') -> dict:
        text = f"""
        enabled: true
        schedule:
            source: manual
            lessons:
                - groups: ["4431"]
                  weekday: mon
                  start: {start}
                  end: "16:40"
        """
        return yaml.safe_load(text)

    def test_valid_manual_schedule(self):
        settings = parse_queue_config(course(queue=self.manual()))
        assert settings.schedule_cfg["source"] == "manual"

    def test_unquoted_time_from_yaml_is_an_error(self):
        # YAML 1.1 читает 15:10 без кавычек как число 910 (шестидесятеричная запись).
        queue = self.manual(start="15:10")
        assert queue["schedule"]["lessons"][0]["start"] == 910
        with pytest.raises(QueueConfigError, match="queue.schedule"):
            parse_queue_config(course(queue=queue))

    def test_unknown_source(self):
        with pytest.raises(QueueConfigError, match="queue.schedule"):
            parse_queue_config(course(queue={"enabled": True, "schedule": {"source": "itmo"}}))


class TestLabCellStatus:
    """В очередь пускает только отметка самого сервиса: ячейка, начинающаяся с «v»."""

    @pytest.mark.parametrize("value, expected", [
        ("v", LAB_PASSED),
        ("v-1", LAB_PASSED),
        ("v@10", LAB_PASSED),
        ("v@10,5-3", LAB_PASSED),
        ("v@10.5", LAB_PASSED),
        ("V", LAB_PASSED),
        (" v ", LAB_PASSED),
        ("\xa0v-2\xa0", LAB_PASSED),
        ("vv", LAB_DEFENDED),
        ("vv-2", LAB_DEFENDED),
        ("VV", LAB_DEFENDED),
        ("Vv@8", LAB_DEFENDED),
        (" vv ", LAB_DEFENDED),
        ("x", LAB_NOT_PASSED),
        ("X", LAB_NOT_PASSED),
        ("?", LAB_NOT_PASSED),
        ("?1", LAB_NOT_PASSED),
        ("?! Wrong TASKID", LAB_NOT_PASSED),
        ("", LAB_NOT_PASSED),
        ("   ", LAB_NOT_PASSED),
        (None, LAB_NOT_PASSED),
        ("н/я", LAB_NOT_PASSED),
        ("-", LAB_NOT_PASSED),
        ("0", LAB_NOT_PASSED),
        (0, LAB_NOT_PASSED),
        ("в", LAB_NOT_PASSED),   # кириллическая «в» - не отметка сервиса
    ])
    def test_table(self, value, expected):
        assert lab_cell_status(value) == expected

    def test_custom_defended_prefix(self):
        assert lab_cell_status("зщ", "зщ") == LAB_DEFENDED
        assert lab_cell_status("ЗЩ-1", "зщ") == LAB_DEFENDED
        # С другой отметкой защиты «vv» - просто прошедшая проверку работа.
        assert lab_cell_status("vv", "зщ") == LAB_PASSED

    def test_empty_prefix_does_not_mark_everything_defended(self):
        assert lab_cell_status("v", "") == LAB_PASSED
        assert lab_cell_status("x", "") == LAB_NOT_PASSED

    @pytest.mark.parametrize("value", ["v", "v-1", "v@10", "vv", "vv-2"])
    def test_passed_and_defended_cells_are_protected_from_overwrite(self, value):
        # Всё, что пускает в очередь, не перезаписывается проверкой.
        assert lab_cell_status(value) != LAB_NOT_PASSED
        assert not can_overwrite_cell(value)

    @pytest.mark.parametrize("value", ["X", "н/я", "-", "0"])
    def test_stricter_than_can_overwrite_cell(self, value):
        # Ручные пометки защищены от перезаписи, но в очередь не пускают.
        assert not can_overwrite_cell(value)
        assert lab_cell_status(value) == LAB_NOT_PASSED
