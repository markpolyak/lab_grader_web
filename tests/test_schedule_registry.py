"""
Тесты реестра источников расписания (grading/schedule/__init__.py) и
склейки сдвоенных занятий (merge_adjacent из grading/schedule/base.py).

Источник ищется по имени модуля: новый вуз подключается файлом
grading/schedule/<name>.py, без правок реестра. Здесь это проверяется
модулем-подделкой, подложенным в sys.modules.
"""
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.schedule import (  # noqa: E402
    ALL_DAY_SOURCE,
    STATE_OK,
    AllDaySource,
    Lesson,
    ScheduleConfigError,
    ScheduleStatus,
    SourceContext,
    build_schedule_source,
    merge_adjacent,
    reset_schedule_caches,
)
import grading.schedule as schedule_package  # noqa: E402
from grading.schedule.guap import GuapSource  # noqa: E402
from grading.schedule.manual import ManualSource  # noqa: E402

MSK = timezone(timedelta(hours=3))
UTC5 = timezone(timedelta(hours=5))
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=MSK)
DAY = date(2026, 11, 5)

LAB = "Лабораторное занятие"


def ctx(tz=MSK):
    return SourceContext(snapshots=None, course_tz=tz)


def at(hhmm: str, day: date = DAY, tz=MSK) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime(day.year, day.month, day.day, int(hours), int(minutes), tzinfo=tz)


def lesson(start, end, groups=("4431",), lesson_type=LAB, subject="ОС", day=DAY):
    return Lesson.build(at(start, day), at(end, day), groups, lesson_type, subject)


def spans(lessons):
    return [(l.starts_at.strftime("%H:%M"), l.ends_at.strftime("%H:%M"), l.groups) for l in lessons]


@pytest.fixture(autouse=True)
def clean_caches():
    reset_schedule_caches()
    yield
    reset_schedule_caches()


@pytest.fixture
def plugin(monkeypatch):
    """Подкладывает модуль grading.schedule.<name>, как будто это новый файл в пакете."""
    def install(name, source_name=None, build=None, **attrs):
        module = types.ModuleType(f"grading.schedule.{name}")
        if source_name is not None:
            module.SOURCE_NAME = source_name
        if build is not None:
            module.build = build
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return module
    return install


class FakeUniSource:
    """Источник «другого вуза»: две пары подряд у одной группы."""

    name = "otheruni"

    def __init__(self, cfg, ctx):
        self.cfg = cfg
        self.ctx = ctx

    def lessons_on(self, day, now):
        return [lesson("11:10", "12:40", day=day), lesson("09:30", "11:00", day=day)]

    def status(self, now):
        return ScheduleStatus(self.name)


# ---------------------------------------------------------------------------
# Реестр
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_no_schedule_is_all_day(self):
        source = build_schedule_source(None, ctx(UTC5))
        assert isinstance(source, AllDaySource)
        assert source.name == ALL_DAY_SOURCE == "all_day"
        assert source.lessons_on(DAY, NOW) == [Lesson(
            key="all-day",
            starts_at=datetime(2026, 11, 5, 0, 0, tzinfo=UTC5),
            ends_at=datetime(2026, 11, 6, 0, 0, tzinfo=UTC5),
            groups=(),
            lesson_type="",
            subject="",
        )]
        assert source.status(NOW) == ScheduleStatus("all_day", STATE_OK, None, None)

    def test_guap_by_name(self):
        source = build_schedule_source({"source": "guap", "teacher-id": 39614}, ctx())
        assert source.name == "guap"
        assert isinstance(source.source, GuapSource)

    def test_manual_by_name(self):
        source = build_schedule_source({"source": "manual", "lessons": []}, ctx())
        assert source.name == "manual"
        assert isinstance(source.source, ManualSource)
        assert source.lessons_on(DAY, NOW) == []
        assert source.status(NOW).state == STATE_OK

    def test_source_config_errors_pass_through(self):
        with pytest.raises(ScheduleConfigError, match="teacher-id"):
            build_schedule_source({"source": "guap"}, ctx())

    @pytest.mark.parametrize("name", [
        "nosuch", "Guap", "guap ", " guap", "../guap", "guap.manual", "grading.schedule.guap",
        "", "_private", "__init__", "1guap", "os", "sys", None, 5, True, ["guap"],
    ])
    def test_unknown_or_invalid_name(self, name):
        with pytest.raises(ScheduleConfigError, match="Неизвестный источник"):
            build_schedule_source({"source": name}, ctx())

    def test_missing_source_key(self):
        with pytest.raises(ScheduleConfigError, match="Неизвестный источник"):
            build_schedule_source({"teacher-id": 39614}, ctx())

    @pytest.mark.parametrize("name", ["base", "suai_rasp"])
    def test_package_modules_that_are_not_sources(self, name):
        with pytest.raises(ScheduleConfigError, match="не является источником"):
            build_schedule_source({"source": name}, ctx())

    @pytest.mark.parametrize("cfg", ["guap", ["guap"], 39614, True])
    def test_section_must_be_a_dict(self, cfg):
        with pytest.raises(ScheduleConfigError, match="queue.schedule"):
            build_schedule_source(cfg, ctx())

    def test_new_university_is_one_module(self, plugin):
        plugin("otheruni", source_name="otheruni", build=FakeUniSource)
        cfg = {"source": "otheruni", "campus": "main"}
        source = build_schedule_source(cfg, ctx(UTC5))
        assert source.name == "otheruni"
        assert source.source.cfg is cfg
        assert source.source.ctx.course_tz is UTC5
        # Обёртка реестра склеивает пары подряд, сам источник об этом не знает.
        assert spans(source.lessons_on(DAY, NOW)) == [("09:30", "12:40", ("4431",))]
        assert source.status(NOW) == ScheduleStatus("otheruni")

    def test_module_name_must_match_source_name(self, plugin):
        plugin("otheruni", source_name="guap", build=FakeUniSource)
        with pytest.raises(ScheduleConfigError, match="не является источником"):
            build_schedule_source({"source": "otheruni"}, ctx())

    def test_module_without_build(self, plugin):
        plugin("otheruni", source_name="otheruni", build="not callable")
        with pytest.raises(ScheduleConfigError, match="не является источником"):
            build_schedule_source({"source": "otheruni"}, ctx())

    def test_broken_module_is_not_hidden(self, monkeypatch):
        # Модуль есть, но сам не импортируется: это ошибка развёртывания,
        # она не должна выглядеть как опечатка в конфиге курса.
        def broken_import(name, package=None):
            raise ModuleNotFoundError("No module named 'bs4'", name="bs4")

        monkeypatch.setattr(schedule_package, "importlib", types.SimpleNamespace(import_module=broken_import))
        with pytest.raises(ModuleNotFoundError, match="bs4"):
            build_schedule_source({"source": "guap", "teacher-id": 39614}, ctx())

    def test_reset_calls_reset_cache_of_loaded_sources(self, plugin):
        calls = []
        plugin("otheruni", source_name="otheruni", build=FakeUniSource, reset_cache=lambda: calls.append(1))
        reset_schedule_caches()
        assert calls == [1]


# ---------------------------------------------------------------------------
# Склейка сдвоенных занятий
# ---------------------------------------------------------------------------


class TestMergeAdjacent:
    def test_double_lab_becomes_one(self):
        merged = merge_adjacent([lesson("09:30", "11:00"), lesson("11:10", "12:40")])
        assert spans(merged) == [("09:30", "12:40", ("4431",))]
        assert merged[0].key == "06:30-09:40|4431|Лабораторное занятие|ОС"
        assert (merged[0].lesson_type, merged[0].subject) == (LAB, "ОС")

    def test_three_in_a_row(self):
        merged = merge_adjacent([lesson("13:00", "14:30"), lesson("09:30", "11:00"), lesson("11:10", "12:40")])
        assert spans(merged) == [("09:30", "14:30", ("4431",))]

    def test_gap_of_30_minutes_merged(self):
        assert len(merge_adjacent([lesson("09:30", "11:00"), lesson("11:30", "13:00")])) == 1

    def test_lunch_break_merged(self):
        # 3-я и 4-я пары ГУАП: обед 40 минут, сдвоенная 3-4 пара - одна очередь.
        merged = merge_adjacent([lesson("13:00", "14:30"), lesson("15:10", "16:40")])
        assert spans(merged) == [("13:00", "16:40", ("4431",))]
        assert merged[0].key == "10:00-13:40|4431|Лабораторное занятие|ОС"

    def test_longer_gap_not_merged(self):
        merged = merge_adjacent([lesson("13:00", "14:30"), lesson("15:11", "16:40")])
        assert spans(merged) == [("13:00", "14:30", ("4431",)), ("15:11", "16:40", ("4431",))]

    def test_duplicate_lesson_is_one(self):
        merged = merge_adjacent([lesson("09:30", "11:00"), lesson("09:30", "11:00")])
        assert spans(merged) == [("09:30", "11:00", ("4431",))]
        assert merged[0].key == lesson("09:30", "11:00").key

    def test_duplicate_then_next_pair_is_one_queue(self):
        # Дубль сливается с оригиналом, а не склеивается со следующей парой
        # отдельно: иначе у группы были бы две открытые очереди сразу.
        merged = merge_adjacent([lesson("09:30", "11:00"), lesson("09:30", "11:00"), lesson("11:10", "12:40")])
        assert spans(merged) == [("09:30", "12:40", ("4431",))]
        assert merged[0].key == "06:30-09:40|4431|Лабораторное занятие|ОС"

    def test_lesson_inside_previous_absorbed(self):
        merged = merge_adjacent([lesson("09:30", "12:40"), lesson("11:10", "12:40"), lesson("09:30", "11:00")])
        assert spans(merged) == [("09:30", "12:40", ("4431",))]
        assert merged[0].key == "06:30-09:40|4431|Лабораторное занятие|ОС"

    def test_absorbed_lesson_does_not_cut_the_union(self):
        merged = merge_adjacent([lesson("09:30", "14:30"), lesson("10:00", "11:00"), lesson("15:10", "16:40")])
        assert spans(merged) == [("09:30", "16:40", ("4431",))]

    def test_overlap_merged(self):
        merged = merge_adjacent([lesson("10:30", "12:00"), lesson("09:30", "11:00")])
        assert spans(merged) == [("09:30", "12:00", ("4431",))]
        assert merged[0].key == "06:30-09:00|4431|Лабораторное занятие|ОС"

    @pytest.mark.parametrize("other", [
        {"groups": ("4432",)},
        {"groups": ("4431", "4432")},
        {"lesson_type": "Лекция"},
        {"subject": "Базы данных"},
    ])
    def test_different_lessons_not_merged(self, other):
        assert len(merge_adjacent([lesson("09:30", "11:00"), lesson("11:10", "12:40", **other)])) == 2
        assert len(merge_adjacent([lesson("09:30", "11:00"), lesson("09:30", "11:00", **other)])) == 2

    def test_different_days_not_merged(self):
        late = Lesson.build(at("22:00"), at("23:59"), ["4431"], LAB, "ОС")
        early = Lesson.build(at("00:10", DAY + timedelta(days=1)), at("01:00", DAY + timedelta(days=1)),
                             ["4431"], LAB, "ОС")
        assert len(merge_adjacent([late, early])) == 2

    def test_sorted_by_start(self):
        merged = merge_adjacent([
            lesson("18:40", "20:10", groups=("4434",)),
            lesson("15:10", "16:40", groups=("4431",)),
        ])
        assert spans(merged) == [("15:10", "16:40", ("4431",)), ("18:40", "20:10", ("4434",))]

    def test_empty(self):
        assert merge_adjacent([]) == []


# ---------------------------------------------------------------------------
# Ключ занятия
# ---------------------------------------------------------------------------


class TestLessonKey:
    def test_key_is_the_moment_not_the_clock_time(self):
        """Пары ГУАП - по Москве, поправки и manual - по поясу курса: 15:10 в двух поясах - два занятия."""
        utc = timezone.utc
        moscow = Lesson.build(at("15:10"), at("16:40"), ["4431"], LAB, "ОС")
        same_clock = Lesson.build(at("15:10", tz=utc), at("16:40", tz=utc), ["4431"], LAB, "ОС")
        same_moment = Lesson.build(at("12:10", tz=utc), at("13:40", tz=utc), ["4431"], LAB, "ОС")
        assert moscow.key != same_clock.key
        assert moscow.key == same_moment.key == "12:10-13:40|4431|Лабораторное занятие|ОС"
