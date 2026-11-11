"""
Тесты ручного расписания (grading/schedule/manual.py).

Конфиг по возможности берётся из YAML-текста через yaml.safe_load, как его
прочитает сервер: даты без кавычек приходят как date, время без кавычек -
как число (15:10 -> 910).
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.schedule import (  # noqa: E402
    STATE_OK,
    ScheduleConfigError,
    SourceContext,
    build_schedule_source,
)
from grading.schedule import manual  # noqa: E402

MSK = timezone(timedelta(hours=3))
UTC5 = timezone(timedelta(hours=5))

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=MSK)
MONDAY = date(2026, 11, 9)      # неделя 11 от 2026-08-31, нечётная
TUESDAY = date(2026, 11, 10)

OS_SCHEDULE = """
source: manual
from: 2026-09-01
until: "2026-12-31"
week1-monday: 2026-08-31
lessons:
    - groups: [4431]
      weekday: mon
      start: "15:10"
      end: "16:40"
    - groups: ["4434"]
      weekday: mon
      start: "18:40"
      end: "20:10"
    - groups: ["4432"]
      weekday: tue
      start: "13:00"
      end: "14:30"
      weeks: odd
"""


def make_source(cfg, tz=MSK):
    if isinstance(cfg, str):
        cfg = yaml.safe_load(cfg)
    return build_schedule_source(cfg, SourceContext(snapshots=None, course_tz=tz))


def lesson_cfg(**overrides):
    lesson = {"groups": ["4431"], "weekday": "mon", "start": "15:10", "end": "16:40"}
    lesson.update(overrides)
    return {"source": "manual", "lessons": [lesson]}


def times(lessons):
    return [(l.starts_at.strftime("%H:%M"), l.ends_at.strftime("%H:%M"), l.groups) for l in lessons]


class TestLessons:
    def test_os_schedule(self):
        source = make_source(OS_SCHEDULE)
        assert source.name == "manual"
        assert times(source.lessons_on(MONDAY, NOW)) == [
            ("15:10", "16:40", ("4431",)),
            ("18:40", "20:10", ("4434",)),
        ]
        lesson = source.lessons_on(MONDAY, NOW)[0]
        assert lesson.key == "12:10-13:40|4431||"
        assert (lesson.lesson_type, lesson.subject) == ("", "")

    def test_time_in_course_timezone(self):
        lesson = make_source(OS_SCHEDULE, tz=UTC5).lessons_on(MONDAY, NOW)[0]
        assert lesson.starts_at == datetime(2026, 11, 9, 15, 10, tzinfo=UTC5)
        assert lesson.ends_at.utcoffset() == timedelta(hours=5)

    def test_other_weekday(self):
        assert make_source(OS_SCHEDULE).lessons_on(date(2026, 11, 11), NOW) == []

    def test_int_groups_become_strings(self):
        source = make_source(lesson_cfg(groups=[4431, " 4432 "]))
        assert source.lessons_on(MONDAY, NOW)[0].groups == ("4431", "4432")

    def test_single_group_without_list(self):
        assert make_source(lesson_cfg(groups=4431)).lessons_on(MONDAY, NOW)[0].groups == ("4431",)

    def test_weekday_case_insensitive(self):
        assert len(make_source(lesson_cfg(weekday="Mon")).lessons_on(MONDAY, NOW)) == 1

    def test_hour_without_leading_zero(self):
        assert times(make_source(lesson_cfg(start="9:30", end="11:00")).lessons_on(MONDAY, NOW)) == [
            ("09:30", "11:00", ("4431",))]

    def test_double_lesson_merged(self):
        cfg = {"source": "manual", "lessons": [
            {"groups": ["4431"], "weekday": "thu", "start": "09:30", "end": "11:00"},
            {"groups": ["4431"], "weekday": "thu", "start": "11:10", "end": "12:40"},
        ]}
        lessons = make_source(cfg).lessons_on(date(2026, 11, 5), NOW)
        assert times(lessons) == [("09:30", "12:40", ("4431",))]
        assert lessons[0].key == "06:30-09:40|4431||"

    def test_duplicate_lesson_and_next_pair_are_one_queue(self):
        # Занятие, по ошибке записанное в конфиг дважды, не даёт второй очереди.
        pair = {"groups": ["4431"], "weekday": "mon", "start": "09:30", "end": "11:00"}
        cfg = {"source": "manual", "lessons": [
            pair, dict(pair), {"groups": ["4431"], "weekday": "mon", "start": "11:10", "end": "12:40"},
        ]}
        assert times(make_source(cfg).lessons_on(MONDAY, NOW)) == [("09:30", "12:40", ("4431",))]

    def test_empty_lessons_allowed(self):
        source = make_source({"source": "manual", "lessons": []})
        assert source.lessons_on(MONDAY, NOW) == []

    def test_status_always_ok(self):
        status = make_source(OS_SCHEDULE).status(NOW)
        assert (status.source, status.state, status.fetched_at, status.error) == ("manual", STATE_OK, None, None)


class TestWeeks:
    @pytest.mark.parametrize("day, week", [
        (date(2026, 8, 31), 1),
        (date(2026, 9, 6), 1),
        (date(2026, 9, 7), 2),
        (date(2026, 11, 2), 10),
        (date(2026, 11, 9), 11),
        (date(2026, 8, 30), 0),
    ])
    def test_week_number(self, day, week):
        assert make_source(OS_SCHEDULE).source.week_number(day) == week

    def test_odd_weeks(self):
        source = make_source(OS_SCHEDULE)
        assert times(source.lessons_on(TUESDAY, NOW)) == [("13:00", "14:30", ("4432",))]
        assert source.lessons_on(date(2026, 11, 3), NOW) == []
        assert len(source.lessons_on(date(2026, 9, 1), NOW)) == 1

    def test_even_weeks(self):
        cfg = lesson_cfg(weeks="even")
        cfg["week1-monday"] = "2026-08-31"
        source = make_source(cfg)
        assert source.lessons_on(MONDAY, NOW) == []
        assert len(source.lessons_on(date(2026, 11, 2), NOW)) == 1

    def test_every_week_needs_no_anchor(self):
        source = make_source(lesson_cfg(weeks="every"))
        assert len(source.lessons_on(MONDAY, NOW)) == 1
        assert len(source.lessons_on(date(2026, 11, 2), NOW)) == 1


class TestDates:
    def test_from_until_inclusive(self):
        cfg = lesson_cfg()
        cfg.update({"from": "2026-09-07", "until": "2026-11-09"})
        source = make_source(cfg)
        assert source.lessons_on(date(2026, 8, 31), NOW) == []
        assert len(source.lessons_on(date(2026, 9, 7), NOW)) == 1
        assert len(source.lessons_on(date(2026, 11, 9), NOW)) == 1
        assert source.lessons_on(date(2026, 11, 16), NOW) == []

    def test_dates_as_date_objects(self):
        cfg = lesson_cfg(weeks="odd")
        cfg.update({"from": date(2026, 9, 1), "until": date(2026, 12, 31), "week1-monday": date(2026, 8, 31)})
        assert len(make_source(cfg).lessons_on(MONDAY, NOW)) == 1

    def test_unquoted_yaml_dates(self):
        source = make_source(OS_SCHEDULE).source
        assert (source.first_day, source.last_day, source.week1_monday) == (
            date(2026, 9, 1), date(2026, 12, 31), date(2026, 8, 31))


class TestErrors:
    def test_unquoted_time_hint(self):
        text = OS_SCHEDULE.replace('start: "15:10"', "start: 15:10")
        assert yaml.safe_load(text)["lessons"][0]["start"] == 910
        with pytest.raises(ScheduleConfigError, match="кавычки") as err:
            make_source(text)
        assert "15:10" in str(err.value)
        assert "lessons[0].start" in str(err.value)

    @pytest.mark.parametrize("overrides", [
        {"groups": []},
        {"groups": None},
        {"groups": [""]},
        {"groups": [True]},
        {"groups": [["4431"]]},
        {"weekday": "monday"},
        {"weekday": 1},
        {"start": "25:00"},
        {"start": "15.10"},
        {"start": "15:1"},
        {"start": 15.1},
        {"end": "15:10"},
        {"end": "14:00"},
        {"weeks": "weekly"},
        {"weeks": None},
        {"room": "23-09"},
    ])
    def test_lesson_errors(self, overrides):
        with pytest.raises(ScheduleConfigError):
            make_source(lesson_cfg(**overrides))

    @pytest.mark.parametrize("key", ["groups", "weekday", "start", "end"])
    def test_required_lesson_keys(self, key):
        cfg = lesson_cfg()
        del cfg["lessons"][0][key]
        with pytest.raises(ScheduleConfigError, match=key):
            make_source(cfg)

    @pytest.mark.parametrize("cfg", [
        {"source": "manual"},
        {"source": "manual", "lessons": "mon 15:10"},
        {"source": "manual", "lessons": ["mon 15:10"]},
        {"source": "manual", "lessons": [], "from": "1 сентября"},
        {"source": "manual", "lessons": [], "until": "2026-13-01"},
        {"source": "manual", "lessons": [], "from": datetime(2026, 9, 1, 10, 0)},
        {"source": "manual", "lessons": [], "from": "2026-12-31", "until": "2026-09-01"},
        {"source": "manual", "lessons": [], "teacher-id": 39614},
    ])
    def test_section_errors(self, cfg):
        with pytest.raises(ScheduleConfigError):
            make_source(cfg)

    def test_parity_needs_week1_monday(self):
        with pytest.raises(ScheduleConfigError, match="week1-monday"):
            make_source(lesson_cfg(weeks="odd"))

    def test_week1_monday_must_be_monday(self):
        cfg = lesson_cfg(weeks="odd")
        cfg["week1-monday"] = "2026-09-01"
        with pytest.raises(ScheduleConfigError, match="2026-08-31"):
            make_source(cfg)

    def test_error_names_the_lesson(self):
        cfg = {"source": "manual", "lessons": [
            {"groups": ["4431"], "weekday": "mon", "start": "15:10", "end": "16:40"},
            {"groups": ["4432"], "weekday": "tue", "start": "13:00", "end": "12:00"},
        ]}
        with pytest.raises(ScheduleConfigError, match=r"lessons\[1\]"):
            make_source(cfg)

    def test_source_name_constant(self):
        assert manual.SOURCE_NAME == "manual"
