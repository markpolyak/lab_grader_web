"""
Тесты поправок к расписанию (grading/schedule/changes.py): отменённые и
дополнительные занятия из queue.schedule.changes.

Поправки разбирает и применяет реестр (grading/schedule/__init__.py), модули
guap и manual о них не знают. Конфиг берётся из YAML-текста, как его прочитает
сервер. Сеть не используется: страница ГУАП - фикстура через responses.
"""
import logging
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import responses
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.defense_queue import (  # noqa: E402
    SIGNUP_CLOSED,
    SIGNUP_OPEN,
    SIGNUP_UPCOMING,
    WAITING,
    DefenseQueue,
    QueueConfigError,
    QueueSettings,
    parse_queue_config,
)
from grading.queue_roster import StudentRecord  # noqa: E402
from grading.queue_store import QueueError, QueueStore  # noqa: E402
from grading.schedule import (  # noqa: E402
    STATE_STALE,
    STATE_UNAVAILABLE,
    ScheduleConfigError,
    ScheduleStatus,
    ScheduleUnavailable,
    SourceContext,
    build_schedule_source,
    reset_schedule_caches,
)
from grading.schedule.guap import SNAPSHOT_MAX_AGE  # noqa: E402

MSK = timezone(timedelta(hours=3))
UTC5 = timezone(timedelta(hours=5))
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=MSK)

MONDAY = date(2026, 11, 9)
TUESDAY = date(2026, 11, 10)
WEDNESDAY = date(2026, 11, 11)
THURSDAY = date(2026, 11, 12)
NEXT_MONDAY = date(2026, 11, 16)

OS = "Операционные системы"
LAB = "Лабораторное занятие"
GUAP_URL = "https://guap.ru/rasp?pr=39614"
POLYAK = (Path(__file__).parent / "fixtures" / "guap" / "prep_polyak_39614.html").read_text(encoding="utf-8")

MANUAL = """
source: manual
lessons:
    - groups: ["4431"]
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
    - groups: ["4431", "4432"]
      weekday: wed
      start: "09:30"
      end: "11:00"
"""

GUAP = f"""
source: guap
teacher-id: 39614
subject: {OS}
"""


def make_source(base, changes, tz=MSK, snapshots=None):
    cfg = yaml.safe_load(base)
    cfg["changes"] = yaml.safe_load(changes) if isinstance(changes, str) else changes
    return build_schedule_source(cfg, SourceContext(snapshots=snapshots, course_tz=tz))


def summary(lessons, tz=MSK):
    return [
        (l.starts_at.astimezone(tz).strftime("%H:%M"), l.ends_at.astimezone(tz).strftime("%H:%M"), l.groups)
        for l in lessons
    ]


def course(base, changes, snapshots=None):
    """Настройки очереди и источник курса с расписанием `base` и поправками `changes`."""
    schedule = {**yaml.safe_load(base), "changes": changes}
    settings = QueueSettings(open_before=timedelta(minutes=15), defended_prefix="vv", tz=MSK, schedule_cfg=schedule)
    return settings, build_schedule_source(schedule, SourceContext(snapshots=snapshots, course_tz=MSK))


def queue_today(settings, source, now):
    """Занятия дня и текущие ключи так, как их получает очередь, в том числе при упавшем источнике."""
    import main  # только здесь: остальные тесты обходятся без приложения

    _, lessons = main._today("os", settings, source, now)
    return lessons, main._lesson_keys(lessons)


def sign_up(queue, login, group, session, now, keys):
    return queue.sign_up("os", session["id"], StudentRecord(login, login, login, group), "1", "ЛР1", now, keys)


@pytest.fixture(autouse=True)
def clean_caches():
    reset_schedule_caches()
    yield
    reset_schedule_caches()


@pytest.fixture
def guap_site():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rs:
        rs.add(responses.GET, GUAP_URL, body=POLYAK, content_type="text/html; charset=utf-8")
        yield rs


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

class TestFormat:
    def test_both_kinds_parse(self):
        source = make_source(MANUAL, """
            - date: 2026-11-10          # без кавычек YAML даёт date
              cancel: true
              groups: [4432]            # число приводится к строке
            - date: "2026-11-12"
              cancel: false
              groups: "4431"
              start: "15:10"
              end: "16:40"
              lesson-type: "  Лабораторное   занятие "
              subject: Операционные системы
        """)
        assert source.lessons_on(TUESDAY, NOW) == []
        [extra] = source.lessons_on(THURSDAY, NOW)
        assert extra.key == "12:10-13:40|4431|Лабораторное занятие|Операционные системы"

    @pytest.mark.parametrize("changes", [None, []])
    def test_no_changes(self, changes):
        source = make_source(MANUAL, changes)
        assert summary(source.lessons_on(MONDAY, NOW)) == [("15:10", "16:40", ("4431",)), ("18:40", "20:10", ("4434",))]

    @pytest.mark.parametrize("changes,text", [
        ({"date": "2026-11-10", "cancel": True, "groups": ["4432"]}, "ожидается список поправок"),
        (["2026-11-10"], r"changes\[0\]: '2026-11-10' \(ожидается словарь"),
        ([{"date": "2026-11-10", "cancel": True, "groups": ["4432"], "reason": "праздник"}],
         r"Неизвестные параметры queue.schedule.changes\[0\]: reason"),
        ([{"date": "2026-11-10", "cancel": True, "groups": ["4432"], "end": "14:30"}],
         r"changes\[0\]: end \(допустимы для отмены"),
        ([{"date": "2026-11-12", "groups": ["4432"], "start": "13:00", "end": "14:30", "weekday": "thu"}],
         "допустимы для дополнительного занятия"),
        ([{"cancel": True, "groups": ["4432"]}], r"changes\[0\].date обязателен"),
        ([{"date": "2026-11-10", "cancel": True}], r"changes\[0\].groups обязателен"),
        ([{"date": "2026-11-10", "cancel": True, "groups": []}], "непустой список групп"),
        ([{"date": "2026-11-12", "groups": ["4432"], "end": "14:30"}], r"changes\[0\].start обязателен"),
        ([{"date": "2026-11-12", "groups": ["4432"], "start": "13:00"}], r"changes\[0\].end обязателен"),
        ([{"date": "2026-11-12", "groups": ["4432"], "start": "14:30", "end": "13:00"}], "должен быть позже"),
        ([{"date": "2026-11-12", "groups": ["4432"], "start": 780, "end": "14:30"}], "13:00 без кавычек"),
        ([{"date": "2026-11-10", "cancel": True, "groups": ["4432"], "start": "25:00"}], r"changes\[0\].start"),
        ([{"date": "10.11.2026", "cancel": True, "groups": ["4432"]}], "ГГГГ-ММ-ДД"),
        ([{"date": datetime(2026, 11, 10, 13, 0), "cancel": True, "groups": ["4432"]}], "ГГГГ-ММ-ДД"),
        ([{"date": "2026-11-10", "cancel": "yes", "groups": ["4432"]}], "ожидается true или false"),
        ([{"date": "2026-11-12", "groups": ["4432"], "start": "13:00", "end": "14:30", "subject": 5}],
         r"changes\[0\].subject"),
        ([{"date": "2026-11-12", "groups": ["4432"], "start": "13:00", "end": "14:30"}, "bad"], r"changes\[1\]"),
    ])
    def test_errors(self, changes, text):
        with pytest.raises(ScheduleConfigError, match=text):
            make_source(MANUAL, changes)

    def test_unquoted_time_from_yaml(self):
        with pytest.raises(ScheduleConfigError, match="возьмите время в кавычки"):
            make_source(MANUAL, "[{date: 2026-11-12, groups: ['4432'], start: 13:00, end: '14:30'}]")

    def test_config_check_rejects_broken_changes(self):
        course = {"timezone": "UTC+3", "queue": {"enabled": True, "schedule": {
            **yaml.safe_load(GUAP), "changes": [{"date": "2026-11-10", "cancel": True}],
        }}}
        with pytest.raises(QueueConfigError, match=r"queue.schedule: queue.schedule.changes\[0\].groups"):
            parse_queue_config(course)

    def test_source_module_does_not_see_changes(self):
        """guap и manual отвергают неизвестные ключи: changes вынимает реестр, конфиг курса не трогается."""
        changes = [{"date": "2026-11-10", "cancel": True, "groups": ["4432"]}]
        for base in (GUAP, MANUAL):
            cfg = {**yaml.safe_load(base), "changes": changes}
            build_schedule_source(cfg, SourceContext(snapshots=None, course_tz=MSK))
            assert cfg["changes"] is changes


# ---------------------------------------------------------------------------
# Cancelled and extra lessons, manual schedule
# ---------------------------------------------------------------------------

class TestManual:
    def test_cancel_every_lesson_of_the_group_that_day(self):
        source = make_source(MANUAL, [{"date": "2026-11-09", "cancel": True, "groups": ["4431"]}])
        assert summary(source.lessons_on(MONDAY, NOW)) == [("18:40", "20:10", ("4434",))]
        # Через неделю пара снова есть, другие дни не затронуты.
        assert summary(source.lessons_on(NEXT_MONDAY, NOW)) == [
            ("15:10", "16:40", ("4431",)), ("18:40", "20:10", ("4434",)),
        ]
        assert summary(source.lessons_on(TUESDAY, NOW)) == [("13:00", "14:30", ("4432",))]

    def test_cancel_several_groups(self):
        source = make_source(MANUAL, [{"date": "2026-11-09", "cancel": True, "groups": ["4431", "4434"]}])
        assert source.lessons_on(MONDAY, NOW) == []

    def test_cancel_by_start_takes_one_half_of_a_double_lesson(self):
        """Сдвоенная пара - два занятия источника: отмена со start снимает одно, вторая пара остаётся."""
        double = MANUAL + """
    - groups: ["4431"]
      weekday: mon
      start: "16:50"
      end: "18:20"
"""
        one = make_source(double, [{"date": "2026-11-09", "cancel": True, "groups": ["4431"], "start": "15:10"}])
        assert summary(one.lessons_on(MONDAY, NOW)) == [("16:50", "18:20", ("4431",)), ("18:40", "20:10", ("4434",))]
        both = make_source(double, """
            - {date: "2026-11-09", cancel: true, groups: ["4431"], start: "15:10"}
            - {date: "2026-11-09", cancel: true, groups: ["4431"], start: "16:50"}
        """)
        whole_day = make_source(double, [{"date": "2026-11-09", "cancel": True, "groups": ["4431"]}])
        for source in (both, whole_day):
            assert summary(source.lessons_on(MONDAY, NOW)) == [("18:40", "20:10", ("4434",))]

    def test_cancel_one_lesson_by_start(self):
        source = make_source(MANUAL, [
            {"date": "2026-11-09", "cancel": True, "groups": ["4431", "4434"], "start": "18:40"},
        ])
        assert summary(source.lessons_on(MONDAY, NOW)) == [("15:10", "16:40", ("4431",))]

    def test_start_that_matches_no_lesson_cancels_nothing(self):
        source = make_source(MANUAL, [{"date": "2026-11-09", "cancel": True, "groups": ["4431"], "start": "15:00"}])
        assert len(source.lessons_on(MONDAY, NOW)) == 2

    def test_partial_cancel_of_a_lesson_of_several_groups(self):
        source = make_source(MANUAL, [{"date": "2026-11-11", "cancel": True, "groups": ["4432"]}])
        [lesson] = source.lessons_on(WEDNESDAY, NOW)
        assert lesson.groups == ("4431",)
        # Ключ построен заново, как если бы у занятия была одна группа.
        assert lesson.key == "06:30-08:00|4431||"

    def test_extra_lesson_of_one_group(self):
        source = make_source(MANUAL, [{"date": "2026-11-12", "groups": ["4431"], "start": "15:10", "end": "16:40"}])
        [lesson] = source.lessons_on(THURSDAY, NOW)
        assert lesson.starts_at == datetime(2026, 11, 12, 15, 10, tzinfo=MSK)
        assert lesson.ends_at == datetime(2026, 11, 12, 16, 40, tzinfo=MSK)
        assert lesson.groups == ("4431",)
        assert source.lessons_on(date(2026, 11, 19), NOW) == []

    def test_extra_lesson_of_several_groups(self):
        source = make_source(MANUAL, [
            {"date": "2026-11-12", "groups": ["4434", "4431"], "start": "11:10", "end": "12:40"},
        ])
        assert summary(source.lessons_on(THURSDAY, NOW)) == [("11:10", "12:40", ("4431", "4434"))]

    def test_extra_lesson_next_to_a_regular_one(self):
        source = make_source(MANUAL, [{"date": "2026-11-09", "groups": ["4434"], "start": "11:10", "end": "12:40"}])
        assert summary(source.lessons_on(MONDAY, NOW)) == [
            ("11:10", "12:40", ("4434",)), ("15:10", "16:40", ("4431",)), ("18:40", "20:10", ("4434",)),
        ]

    def test_move_to_another_day(self):
        source = make_source(MANUAL, """
            - {date: "2026-11-10", cancel: true, groups: ["4432"]}
            - {date: "2026-11-12", groups: ["4432"], start: "13:00", end: "14:30"}
        """)
        assert source.lessons_on(TUESDAY, NOW) == []
        assert summary(source.lessons_on(THURSDAY, NOW)) == [("13:00", "14:30", ("4432",))]

    def test_move_within_the_day_cancels_first(self):
        """Отмена без start не гасит дополнительное занятие того же дня, где бы оно ни стояло в списке."""
        source = make_source(MANUAL, """
            - {date: "2026-11-09", groups: ["4431"], start: "17:00", end: "18:30"}
            - {date: "2026-11-09", cancel: true, groups: ["4431"]}
        """)
        assert summary(source.lessons_on(MONDAY, NOW)) == [("17:00", "18:30", ("4431",)), ("18:40", "20:10", ("4434",))]

    def test_extra_lesson_glued_to_the_adjacent_pair(self):
        """Доп. занятие сразу после пары той же группы - одна очередь, как у сдвоенной пары."""
        source = make_source(MANUAL, [{"date": "2026-11-09", "groups": ["4431"], "start": "16:50", "end": "18:20"}])
        lessons = source.lessons_on(MONDAY, NOW)
        assert summary(lessons) == [("15:10", "18:20", ("4431",)), ("18:40", "20:10", ("4434",))]
        assert lessons[0].key == "12:10-15:20|4431||"

    def test_times_are_in_the_course_timezone(self):
        source = make_source(MANUAL, [
            {"date": "2026-11-09", "cancel": True, "groups": ["4431"], "start": "15:10"},
            {"date": "2026-11-12", "groups": ["4431"], "start": "15:10", "end": "16:40"},
        ], tz=UTC5)
        assert summary(source.lessons_on(MONDAY, NOW), UTC5) == [("18:40", "20:10", ("4434",))]
        assert source.lessons_on(THURSDAY, NOW)[0].starts_at == datetime(2026, 11, 12, 15, 10, tzinfo=UTC5)

    def test_outside_from_until_extra_lessons_still_open(self):
        base = MANUAL.replace("source: manual", "source: manual\nuntil: \"2026-11-08\"")
        source = make_source(base, [{"date": "2026-11-12", "groups": ["4431"], "start": "15:10", "end": "16:40"}])
        assert source.lessons_on(MONDAY, NOW) == []
        assert len(source.lessons_on(THURSDAY, NOW)) == 1


# ---------------------------------------------------------------------------
# GUAP
# ---------------------------------------------------------------------------

class TestGuap:
    def test_cancel_and_other_days(self, guap_site):
        source = make_source(GUAP, [{"date": "2026-11-10", "cancel": True, "groups": ["4432"]}])
        assert source.lessons_on(TUESDAY, NOW) == []
        assert summary(source.lessons_on(MONDAY, NOW)) == [("15:10", "16:40", ("4431",)), ("18:40", "20:10", ("4434",))]

    def test_cancel_by_start_in_the_course_timezone(self, guap_site):
        """Пары ГУАП - по Москве, start поправки - по поясу курса: 15:10 МСК = 17:10 UTC+5."""
        def cancel_at(start):
            return make_source(GUAP, [{"date": "2026-11-09", "cancel": True, "groups": ["4431"], "start": start}], tz=UTC5)

        moscow, local = cancel_at("15:10"), cancel_at("17:10")
        assert len(moscow.lessons_on(MONDAY, NOW)) == 2
        assert summary(local.lessons_on(MONDAY, NOW)) == [("18:40", "20:10", ("4434",))]

    def test_partial_cancel_of_a_lecture_of_three_groups(self, guap_site):
        source = make_source(GUAP + "lesson-types: [Лабораторное занятие, Лекция]\n", [
            {"date": "2026-11-10", "cancel": True, "groups": ["4434"], "start": "09:30"},
        ])
        lecture, lab = source.lessons_on(TUESDAY, NOW)
        assert (lecture.groups, lecture.lesson_type) == (("4431", "4432"), "Лекция")
        assert lecture.key == f"06:30-08:00|4431,4432|Лекция|{OS}"
        assert lab.groups == ("4432",)

    def test_extra_lesson_glued_to_the_pair_of_the_site(self, guap_site):
        source = make_source(GUAP, [{
            "date": "2026-11-09", "groups": ["4431"], "start": "16:50", "end": "18:20",
            "lesson-type": LAB, "subject": OS,
        }])
        lessons = source.lessons_on(MONDAY, NOW)
        assert summary(lessons) == [("15:10", "18:20", ("4431",)), ("18:40", "20:10", ("4434",))]
        assert lessons[0].key == f"12:10-15:20|4431|{LAB}|{OS}"

    def test_extra_lesson_without_type_is_a_queue_of_its_own(self, guap_site):
        """Без lesson-type и subject доп. занятие не совпадает с парой сайта и не склеивается с ней."""
        source = make_source(GUAP, [{"date": "2026-11-09", "groups": ["4431"], "start": "16:50", "end": "18:20"}])
        assert summary(source.lessons_on(MONDAY, NOW)) == [
            ("15:10", "16:40", ("4431",)), ("16:50", "18:20", ("4431",)), ("18:40", "20:10", ("4434",)),
        ]

    def test_move(self, guap_site):
        source = make_source(GUAP, [
            {"date": "2026-11-10", "cancel": True, "groups": ["4432"], "start": "13:00"},
            {"date": "2026-11-12", "groups": ["4432"], "start": "13:00", "end": "14:30",
             "lesson-type": LAB, "subject": OS},
        ])
        assert source.lessons_on(TUESDAY, NOW) == []
        [moved] = source.lessons_on(THURSDAY, NOW)
        assert moved.key == f"10:00-11:30|4432|{LAB}|{OS}"

    def test_extra_lesson_at_the_clock_time_of_a_pair_in_another_timezone(self, guap_site, tmp_path):
        """
        Курс в UTC+0: пара сайта 15:10-16:40 МСК (12:10 UTC) и доп. занятие той же
        группы 15:10-16:40 по поясу курса - два разных занятия и две сессии.
        """
        utc = timezone.utc
        source = make_source(GUAP, [{
            "date": "2026-11-09", "groups": ["4431"], "start": "15:10", "end": "16:40",
            "lesson-type": LAB, "subject": OS,
        }], tz=utc)
        lessons = [l for l in source.lessons_on(MONDAY, NOW) if l.groups == ("4431",)]
        assert summary(lessons, utc) == [("12:10", "13:40", ("4431",)), ("15:10", "16:40", ("4431",))]
        assert lessons[0].key != lessons[1].key

        settings = QueueSettings(
            open_before=timedelta(minutes=15), defended_prefix="vv", tz=utc, schedule_cfg=yaml.safe_load(GUAP),
        )
        queue = DefenseQueue(QueueStore(tmp_path / "queue.sqlite3"))
        sessions = queue.sync_day("os", settings, lessons, datetime(2026, 11, 9, 12, 0, tzinfo=utc))
        assert [(s["starts_at"], s["ends_at"]) for s in sessions] == [
            ("2026-11-09T12:10:00+00:00", "2026-11-09T13:40:00+00:00"),
            ("2026-11-09T15:10:00+00:00", "2026-11-09T16:40:00+00:00"),
        ]

    def test_site_down_extra_lessons_still_open(self):
        """Ни страницы, ни копии: доп. занятие открывается по конфигу, день без него остаётся неизвестным."""
        source = make_source(GUAP, [
            {"date": "2026-11-12", "groups": ["4431"], "start": "15:10", "end": "16:40"},
            {"date": "2026-11-10", "cancel": True, "groups": ["4432"]},
        ])
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rs:
            rs.add(responses.GET, GUAP_URL, body=requests.ConnectionError("guap.ru is down"))
            assert summary(source.lessons_on(THURSDAY, NOW)) == [("15:10", "16:40", ("4431",))]
            with pytest.raises(ScheduleUnavailable):
                source.lessons_on(MONDAY, NOW)
            with pytest.raises(ScheduleUnavailable):
                source.lessons_on(TUESDAY, NOW)
            # Преподаватель по-прежнему видит, что сайт недоступен.
            assert source.status(NOW).state == STATE_UNAVAILABLE

    def test_copy_turns_too_old_midday(self, tmp_path):
        """
        Копия ГУАП стала старше 14 дней посреди дня с дополнительным занятием.

        День теперь известен только по конфигу: сессия обычной пары, созданная
        утром по копии, больше не принимает новые записи, уже записанные
        остаются и принимаются; дополнительное занятие открывается как обычно.
        """
        store = QueueStore(tmp_path / "queue.sqlite3")
        queue = DefenseQueue(store)
        settings = QueueSettings(
            open_before=timedelta(minutes=15), defended_prefix="vv", tz=MSK, schedule_cfg=yaml.safe_load(GUAP),
        )
        before, after = datetime(2026, 11, 9, 14, 59, tzinfo=MSK), datetime(2026, 11, 9, 15, 1, tzinfo=MSK)
        store.save_snapshot("guap:pr=39614", datetime(2026, 11, 9, 15, 0, tzinfo=MSK) - SNAPSHOT_MAX_AGE, POLYAK)
        source = make_source(GUAP, [{"date": "2026-11-09", "groups": ["4432"], "start": "18:40", "end": "20:10"}],
                             snapshots=store)

        def today(now):
            lessons = source.lessons_on(MONDAY, now)
            sessions = queue.sync_day("os", settings, lessons, now)
            return {l.key for l in lessons}, {tuple(s["groups"]): s for s in sessions}

        def sign(login, group, session, now, keys):
            return queue.sign_up(
                "os", session["id"], StudentRecord(login, login, login, group), "1", "ЛР1", now, keys,
            )

        with responses.RequestsMock(assert_all_requests_are_fired=False) as rs:
            rs.add(responses.GET, GUAP_URL, body=requests.ConnectionError("guap.ru is down"))

            keys, sessions = today(before)
            assert source.status(before).state == STATE_STALE
            assert sorted(sessions) == [("4431",), ("4432",), ("4434",)]
            regular = sessions[("4431",)]
            assert DefenseQueue.signup_state(regular, before, keys) == SIGNUP_OPEN
            sign("alice", "4431", regular, before, keys)

            keys, sessions = today(after)
            assert source.status(after).state == STATE_UNAVAILABLE
            [extra] = source.lessons_on(MONDAY, after)
            assert keys == {extra.key}
            assert DefenseQueue.signup_state(regular, after, keys) == SIGNUP_CLOSED
            with pytest.raises(QueueError) as refused:
                sign("bob", "4431", regular, after, keys)
            assert refused.value.code == "SIGNUP_CLOSED"
            assert DefenseQueue.signup_state(sessions[("4432",)], after, keys) == SIGNUP_UPCOMING

            [view] = [v for v in queue.day_view("os", MONDAY, after, keys) if v["id"] == regular["id"]]
            assert [(e["github"], e["status"]) for e in view["entries"]] == [("alice", WAITING)]
            assert queue.call_next("os", regular["id"], after)["github"] == "alice"

    def test_copy_turns_too_old_on_a_day_with_a_cancel(self, tmp_path):
        """
        Сессии дня созданы по копии ГУАП, потом в конфиг вписали отмену 4431,
        а к паре копии исполнилось 14 дней.

        Отмена от сайта не зависит: сессия 4431 больше не принимает новые
        записи, уже записанные остаются и принимаются; сессия 4434 работает
        как при неизвестном расписании.
        """
        store = QueueStore(tmp_path / "queue.sqlite3")
        queue = DefenseQueue(store)
        before, after = datetime(2026, 11, 9, 14, 59, tzinfo=MSK), datetime(2026, 11, 9, 15, 1, tzinfo=MSK)
        store.save_snapshot("guap:pr=39614", datetime(2026, 11, 9, 15, 0, tzinfo=MSK) - SNAPSHOT_MAX_AGE, POLYAK)

        def today(changes, now):
            settings, source = course(GUAP, changes, store)
            lessons, keys = queue_today(settings, source, now)
            return keys, {tuple(s["groups"]): s for s in queue.sync_day("os", settings, lessons, now)}

        with responses.RequestsMock(assert_all_requests_are_fired=False) as rs:
            rs.add(responses.GET, GUAP_URL, body=requests.ConnectionError("guap.ru is down"))

            keys, sessions = today([], before)
            assert sorted(sessions) == [("4431",), ("4434",)]
            sign_up(queue, "alice", "4431", sessions[("4431",)], before, keys)

            keys, sessions = today([{"date": "2026-11-09", "cancel": True, "groups": ["4431"]}], after)
            cancelled, other = sessions[("4431",)], sessions[("4434",)]
            assert DefenseQueue.signup_state(cancelled, after, keys) == SIGNUP_CLOSED
            with pytest.raises(QueueError) as refused:
                sign_up(queue, "bob", "4431", cancelled, after, keys)
            assert refused.value.code == "SIGNUP_CLOSED"

            [view] = [v for v in queue.day_view("os", MONDAY, after, keys) if v["id"] == cancelled["id"]]
            assert view["in_schedule"] is False
            assert [(e["github"], e["status"]) for e in view["entries"]] == [("alice", WAITING)]
            assert queue.call_next("os", cancelled["id"], after)["github"] == "alice"

            assert DefenseQueue.signup_state(other, after, keys) == SIGNUP_UPCOMING
            sign_up(queue, "dave", "4434", other, datetime(2026, 11, 9, 18, 30, tzinfo=MSK), keys)

    def test_cancel_set_in_advance_while_the_copy_turns_too_old(self, tmp_path):
        """Отмена 4431 вписана заранее: сессии 4431 нет ни по копии, ни когда копия устарела; 4434 работает."""
        store = QueueStore(tmp_path / "queue.sqlite3")
        queue = DefenseQueue(store)
        store.save_snapshot("guap:pr=39614", datetime(2026, 11, 9, 15, 0, tzinfo=MSK) - SNAPSHOT_MAX_AGE, POLYAK)
        settings, source = course(GUAP, [{"date": "2026-11-09", "cancel": True, "groups": ["4431"]}], store)

        with responses.RequestsMock(assert_all_requests_are_fired=False) as rs:
            rs.add(responses.GET, GUAP_URL, body=requests.ConnectionError("guap.ru is down"))
            for now in (datetime(2026, 11, 9, 0, 5, tzinfo=MSK), datetime(2026, 11, 9, 18, 30, tzinfo=MSK)):
                lessons, keys = queue_today(settings, source, now)
                [session] = queue.sync_day("os", settings, lessons, now)
                assert session["groups"] == ["4434"]
            assert source.status(now).state == STATE_UNAVAILABLE
            assert DefenseQueue.signup_state(session, now, keys) == SIGNUP_OPEN


# ---------------------------------------------------------------------------
# A source of another university fails
# ---------------------------------------------------------------------------

class TestSourceError:
    @pytest.fixture
    def broken_source(self, monkeypatch):
        """Модуль grading.schedule.otheruni, у которого lessons_on падает с ошибкой, а не ScheduleUnavailable."""
        class Broken:
            name = "otheruni"

            def lessons_on(self, day, now):
                raise RuntimeError("parser bug")

            def status(self, now):
                return ScheduleStatus(self.name)

        module = types.ModuleType("grading.schedule.otheruni")
        module.SOURCE_NAME = "otheruni"
        module.build = lambda cfg, ctx: Broken()
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return "source: otheruni\n"

    def test_other_error_still_opens_extra_lessons(self, broken_source, caplog):
        """Любое исключение источника, не только ScheduleUnavailable: доп. занятие дня открывается по конфигу."""
        source = make_source(broken_source, [
            {"date": "2026-11-12", "groups": ["4431"], "start": "15:10", "end": "16:40"},
        ])
        with caplog.at_level(logging.WARNING, logger="grading.schedule.changes"):
            assert summary(source.lessons_on(THURSDAY, NOW)) == [("15:10", "16:40", ("4431",))]
        assert "otheruni" in caplog.text and "RuntimeError: parser bug" in caplog.text

    def test_other_error_without_extra_lessons_goes_on(self, broken_source):
        """День без доп. занятий получает исходное исключение: его логирует и обрабатывает main._today."""
        source = make_source(broken_source, [
            {"date": "2026-11-12", "groups": ["4431"], "start": "15:10", "end": "16:40"},
        ])
        with pytest.raises(RuntimeError, match="parser bug"):
            source.lessons_on(MONDAY, NOW)


# ---------------------------------------------------------------------------
# The source failed: of the day only the cancellations are known
# ---------------------------------------------------------------------------

class Failing:
    """Источник другого вуза, у которого lessons_on падает с ошибкой."""
    name = "otheruni"

    def lessons_on(self, day, now):
        raise RuntimeError("parser bug")


DOUBLE = """
source: manual
lessons:
    - groups: ["4431"]
      weekday: mon
      start: "15:10"
      end: "16:40"
    - groups: ["4431"]
      weekday: mon
      start: "16:50"
      end: "18:20"
"""


class TestUnknownDay:
    def test_what_a_cancellation_takes(self):
        """Группу общей сессии, сессию, которая начинается в start (по поясу курса), любую сессию группы без start."""
        changes = make_source(MANUAL, [
            {"date": "2026-11-09", "cancel": True, "groups": ["4431"], "start": "15:10"},
            {"date": "2026-11-09", "cancel": True, "groups": ["4434", "4435"]},
        ]).changes
        assert changes.unknown_day(TUESDAY) is None
        unknown = changes.unknown_day(MONDAY)
        assert list(unknown) == [] and not unknown

        pair = datetime(2026, 11, 9, 15, 10, tzinfo=MSK)
        assert unknown.cancelled(MONDAY, pair, ["4431", "4432"]) == {"4431"}
        assert unknown.cancelled(MONDAY, pair.astimezone(timezone.utc), ["4431"]) == {"4431"}
        assert unknown.cancelled(MONDAY, pair - timedelta(hours=2), ["4431"]) == set()
        assert unknown.cancelled(MONDAY, pair + timedelta(hours=3, minutes=30), ["4433", "4434"]) == {"4434"}
        assert unknown.cancelled(TUESDAY, pair, ["4431", "4434"]) == set()

        # 17:10 по UTC+5 - это 15:10 по Москве.
        local = make_source(MANUAL, [
            {"date": "2026-11-09", "cancel": True, "groups": ["4431"], "start": "17:10"},
        ], tz=UTC5)
        assert local.changes.unknown_day(MONDAY).cancelled(MONDAY, pair, ["4431"]) == {"4431"}

    def test_cancelled_pair_of_a_double_lesson(self, tmp_path):
        """
        Сдвоенная пара - одна сессия. Вторая пара, отменённая в сам день, пока
        источник падает, запись не закрывает: первая идёт. Заранее отменённая
        первая пара не закрывает сессию второй.
        """
        queue = DefenseQueue(QueueStore(tmp_path / "queue.sqlite3"))
        morning = datetime(2026, 11, 9, 9, 0, tzinfo=MSK)

        settings, source = course(DOUBLE, [])
        [double] = queue.sync_day("os", settings, source.lessons_on(MONDAY, morning), morning)
        assert (double["starts_at"], double["ends_at"]) == ("2026-11-09T12:10:00+00:00", "2026-11-09T15:20:00+00:00")
        settings, _ = course(DOUBLE, [{"date": "2026-11-09", "cancel": True, "groups": ["4431"], "start": "16:50"}])
        now = datetime(2026, 11, 9, 15, 30, tzinfo=MSK)
        _, keys = queue_today(settings, Failing(), now)
        sign_up(queue, "alice", "4431", double, now, keys)

        settings, source = course(DOUBLE, [{"date": "2026-11-09", "cancel": True, "groups": ["4431"], "start": "15:10"}])
        [second] = queue.sync_day("os-early", settings, source.lessons_on(MONDAY, morning), morning)
        assert second["starts_at"] == "2026-11-09T13:50:00+00:00"
        now = datetime(2026, 11, 9, 16, 40, tzinfo=MSK)
        _, keys = queue_today(settings, Failing(), now)
        queue.sign_up("os-early", second["id"], StudentRecord("bob", "bob", "bob", "4431"), "1", "ЛР1", now, keys)
