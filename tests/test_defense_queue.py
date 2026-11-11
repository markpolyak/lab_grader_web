"""
Тесты очереди на защиту: домен (grading/defense_queue.py) и хранилище SQLite
(grading/queue_store.py).

Модули не знают ни о FastAPI, ни о Google Sheets: время передаётся
параметром `now`, база - временный файл в tmp_path, занятия строятся через
Lesson.build, как их отдают источники расписания. Два QueueStore на одном
файле - это два независимых набора соединений без общей блокировки процесса,
то есть та же ситуация, что у двух процессов backend.
"""
import inspect
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading import defense_queue, queue_store  # noqa: E402
from grading.defense_queue import (  # noqa: E402
    CALLED,
    DEFENDED,
    EXPIRED,
    HISTORY_FIRST_DAY,
    HISTORY_LAST_DAY,
    HISTORY_MAX_DAYS,
    KIND_ALL_DAY,
    KIND_MANUAL,
    KIND_SCHEDULE,
    LEFT,
    NO_SHOW,
    NOT_DEFENDED,
    OUTCOMES,
    SIGNUP_CLOSED,
    SIGNUP_OPEN,
    SIGNUP_UPCOMING,
    WAITING,
    DefenseQueue,
    QueueSettings,
    iso_utc,
    normalize_login,
    parse_closes_at,
)
from grading.queue_roster import StudentRecord  # noqa: E402
from grading.queue_store import SCHEMA_VERSION, QueueError, QueueStore  # noqa: E402
from grading.schedule.base import Lesson  # noqa: E402

MSK = timezone(timedelta(hours=3))
COURSE = "operating-systems-2026"
OTHER_COURSE = "machine-learning-2026"

MONDAY = date(2026, 11, 9)
TUESDAY = date(2026, 11, 10)

SETTINGS = QueueSettings(
    open_before=timedelta(minutes=15),
    defended_prefix="vv",
    tz=MSK,
    schedule_cfg={"source": "guap", "teacher-id": 39614},
)
ALL_DAY_SETTINGS = QueueSettings(
    open_before=timedelta(minutes=15),
    defended_prefix="vv",
    tz=MSK,
    schedule_cfg=None,
)

NAMES = {
    "alice": "Иванова Алиса",
    "bob": "Петров Борис",
    "carol": "Сидорова Вера",
    "dave": "Кузнецов Денис",
}


def at(hour, minute, second=0, day=MONDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=MSK)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def lesson(start=(15, 10), end=(16, 40), groups=("4431",), day=MONDAY) -> Lesson:
    return Lesson.build(
        at(*start, day=day), at(*end, day=day), groups,
        "Лабораторное занятие", "Операционные системы",
    )


# 4431, понедельник, 4-я пара.
PAIR4 = lesson()
NOW = at(15, 0)


def student(login, group="4431") -> StudentRecord:
    return StudentRecord(
        github=login.capitalize(),
        login=login.casefold(),
        name=NAMES.get(login.casefold(), f"Студент {login}"),
        group=group,
        labs={},
    )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "data" / "queue.sqlite3"


@pytest.fixture
def queue(db_path):
    return DefenseQueue(QueueStore(db_path))


@pytest.fixture
def session(queue):
    """Сессия 4-й пары 4431, запись открыта (сейчас 15:00)."""
    return queue.sync_day(COURSE, SETTINGS, [PAIR4], NOW)[0]


def sign(queue, session, who, lab="3", now=NOW, group="4431", course=COURSE) -> dict:
    return queue.sign_up(course, session["id"], student(who, group), lab, f"ЛР{lab}", now)


def code_of(action) -> str:
    with pytest.raises(QueueError) as exc:
        action()
    return exc.value.code


def session_view(queue, session, now=NOW, day=MONDAY, current_keys=None) -> dict:
    for item in queue.day_view(COURSE, day, now, current_keys):
        if item["id"] == session["id"]:
            return item
    raise AssertionError(f"session {session['id']} is not on the admin page")


def waiting(queue, session, now=NOW) -> list[str]:
    entries = session_view(queue, session, now)["entries"]
    in_line = sorted((e for e in entries if e["status"] == WAITING), key=lambda e: e["place"])
    return [e["github"].casefold() for e in in_line]


def statuses(queue, session, now=NOW, day=MONDAY) -> dict[str, str]:
    return {e["github"].casefold(): e["status"] for e in session_view(queue, session, now, day)["entries"]}


def history(queue, day_from, day_to, now, course=COURSE, **filters) -> list[dict]:
    entries, truncated = queue.history(course, day_from, day_to, MSK, now, **filters)
    assert truncated is False
    return entries


# ---------------------------------------------------------------------------
# Sessions and windows
# ---------------------------------------------------------------------------


class TestSyncDay:
    def test_creates_session_once(self, queue):
        first = queue.sync_day(COURSE, SETTINGS, [PAIR4], NOW)
        second = queue.sync_day(COURSE, SETTINGS, [PAIR4], NOW + timedelta(minutes=5))
        assert len(first) == 1
        assert [s["id"] for s in second] == [first[0]["id"]]
        assert first[0]["kind"] == KIND_SCHEDULE
        assert first[0]["lesson_key"] == PAIR4.key
        assert first[0]["groups"] == ["4431"]
        assert first[0]["lesson_type"] == "Лабораторное занятие"
        assert first[0]["subject"] == "Операционные системы"
        assert first[0]["closed_at"] is None

    def test_signup_window_of_a_lesson(self, session):
        # Пара 15:10-16:40, N=15 -> запись [14:55, 16:40), конец дня - полночь МСК.
        assert session["signup_opens_at"] == iso(at(14, 55))
        assert session["signup_closes_at"] == iso(at(16, 40))
        assert session["starts_at"] == iso(at(15, 10))
        assert session["ends_at"] == iso(at(16, 40))
        assert session["day_ends_at"] == iso(at(0, 0, day=TUESDAY))
        assert session["day"] == "2026-11-09"

    def test_window_does_not_open_on_the_previous_day(self, queue):
        early = lesson(start=(0, 5), end=(1, 35))
        created = queue.sync_day(COURSE, SETTINGS, [early], at(0, 1))[0]
        assert created["signup_opens_at"] == iso(at(0, 0))

    def test_all_day_queue_without_schedule(self, queue):
        all_day = Lesson(
            key="all-day", starts_at=at(0, 0), ends_at=at(0, 0, day=TUESDAY),
            groups=(), lesson_type="", subject="",
        )
        created = queue.sync_day(COURSE, ALL_DAY_SETTINGS, [all_day], at(9, 0))[0]
        assert created["kind"] == KIND_ALL_DAY
        assert created["groups"] == []
        assert created["signup_opens_at"] == iso(at(0, 0))
        assert created["signup_closes_at"] == iso(at(0, 0, day=TUESDAY))
        # Студент любой группы.
        entry = sign(queue, created, "alice", group="4334", now=at(23, 59, 59))
        assert entry["place"] == 1

    def test_unknown_schedule_creates_nothing(self, queue, session):
        assert [s["id"] for s in queue.sync_day(COURSE, SETTINGS, None, NOW)] == [session["id"]]
        assert queue.sync_day(OTHER_COURSE, SETTINGS, None, NOW) == []

    def test_returns_manual_sessions_too(self, queue, session):
        manual = queue.open_manual(COURSE, SETTINGS, ["4434"], None, NOW)
        ids = [s["id"] for s in queue.sync_day(COURSE, SETTINGS, [PAIR4], NOW)]
        assert sorted(ids) == sorted([session["id"], manual["id"]])

    def test_courses_are_separate(self, queue, session):
        other = queue.sync_day(OTHER_COURSE, SETTINGS, [PAIR4], NOW)
        assert other[0]["id"] != session["id"]
        assert code_of(lambda: queue.sign_up(
            OTHER_COURSE, session["id"], student("alice"), "3", "ЛР3", NOW,
        )) == "SESSION_NOT_FOUND"


class TestSignupBoundary:
    """Запись открыта на полуинтервале [opens, closes)."""

    @pytest.mark.parametrize("now, state", [
        (at(14, 54, 59), SIGNUP_UPCOMING),
        (at(14, 55), SIGNUP_OPEN),
        (at(16, 39, 59), SIGNUP_OPEN),
        (at(16, 40), SIGNUP_CLOSED),
        (at(23, 0), SIGNUP_CLOSED),
    ])
    def test_state(self, queue, now, state):
        created = queue.sync_day(COURSE, SETTINGS, [PAIR4], now)[0]
        assert DefenseQueue.signup_state(created, now) == state
        if state == SIGNUP_OPEN:
            assert sign(queue, created, "alice", now=now)["status"] == WAITING
        else:
            assert code_of(lambda: sign(queue, created, "alice", now=now)) == "SIGNUP_CLOSED"

    def test_waiting_students_are_called_after_the_lesson(self, queue, session):
        sign(queue, session, "alice")
        late = at(19, 0)
        assert queue.call_next(COURSE, session["id"], late)["status"] == CALLED


# ---------------------------------------------------------------------------
# Student
# ---------------------------------------------------------------------------


class TestSignUp:
    def test_fifo_places(self, queue, session):
        places = [sign(queue, session, who, now=NOW + timedelta(seconds=i))["place"]
                  for i, who in enumerate(["alice", "bob", "carol"])]
        assert places == [1, 2, 3]
        assert waiting(queue, session) == ["alice", "bob", "carol"]

    def test_entry_fields(self, queue, session):
        entry = sign(queue, session, "alice", lab="2")
        assert entry["status"] == WAITING
        assert entry["name"] == "Иванова Алиса"
        assert entry["group"] == "4431"
        assert entry["github"] == "Alice"
        assert entry["lab"] == "ЛР2"
        assert entry["lab_key"] == "2"
        assert entry["created_at"] == iso(NOW)
        assert entry["called_at"] is None and entry["finished_at"] is None

    def test_place_counts_only_waiting(self, queue, session):
        for who in ["alice", "bob", "carol"]:
            sign(queue, session, who)
        queue.call_next(COURSE, session["id"], NOW)
        view = queue.student_view(COURSE, MONDAY, NOW, login="carol", group="4431")
        assert view["my_entry"]["place"] == 2

    @pytest.mark.parametrize("lab", ["3", "4"])
    def test_one_active_entry(self, queue, session, lab):
        sign(queue, session, "alice", lab="3")
        assert code_of(lambda: sign(queue, session, "alice", lab=lab)) == "ALREADY_IN_QUEUE"

    def test_one_active_entry_in_any_session_of_the_course(self, queue, session):
        manual = queue.open_manual(COURSE, SETTINGS, [], None, NOW)
        sign(queue, session, "alice")
        assert code_of(lambda: sign(queue, manual, "alice", lab="4")) == "ALREADY_IN_QUEUE"

    def test_login_is_case_insensitive(self, queue, session):
        sign(queue, session, "alice")
        upper = StudentRecord(github="ALICE", login="ALICE", name="Иванова Алиса", group="4431")
        assert code_of(lambda: queue.sign_up(COURSE, session["id"], upper, "4", "ЛР4", NOW)) == "ALREADY_IN_QUEUE"

    def test_other_course_is_independent(self, queue, session):
        other = queue.sync_day(OTHER_COURSE, SETTINGS, [PAIR4], NOW)[0]
        sign(queue, session, "alice")
        assert sign(queue, other, "alice", course=OTHER_COURSE)["place"] == 1

    def test_not_your_group(self, queue, session):
        assert code_of(lambda: sign(queue, session, "alice", group="4434")) == "NOT_YOUR_GROUP"

    def test_group_is_normalized(self, queue, session):
        assert sign(queue, session, "alice", group="\xa04431 ")["group"] == "4431"

    def test_unknown_session(self, queue, session):
        assert code_of(lambda: queue.sign_up(COURSE, 999, student("alice"), "3", "ЛР3", NOW)) == "SESSION_NOT_FOUND"

    @pytest.mark.parametrize("outcome", [DEFENDED, NOT_DEFENDED, NO_SHOW])
    def test_after_outcome_rejoins_at_the_end(self, queue, session, outcome):
        sign(queue, session, "alice", lab="3")
        sign(queue, session, "bob")
        called = queue.call_next(COURSE, session["id"], NOW)
        queue.set_outcome(COURSE, called["id"], outcome, NOW)
        lab = "4" if outcome == DEFENDED else "3"
        assert sign(queue, session, "alice", lab=lab)["place"] == 2
        assert waiting(queue, session) == ["bob", "alice"]

    def test_after_leaving_rejoins_at_the_end(self, queue, session):
        sign(queue, session, "alice")
        sign(queue, session, "bob")
        queue.leave(COURSE, "alice", NOW)
        sign(queue, session, "alice")
        assert waiting(queue, session) == ["bob", "alice"]

    def test_defended_lab_cannot_be_queued_again(self, queue, session):
        sign(queue, session, "alice", lab="3")
        entry = queue.call_next(COURSE, session["id"], NOW)
        queue.set_outcome(COURSE, entry["id"], DEFENDED, NOW)
        assert code_of(lambda: sign(queue, session, "alice", lab="3")) == "LAB_ALREADY_DEFENDED"
        assert queue.defended_labs(COURSE, "alice", NOW) == {"3"}

    def test_unique_index_is_the_last_line(self, queue, session, monkeypatch):
        # Проверку в коде «не заметили» (как если бы её обошёл другой процесс):
        # вторую активную запись отклоняет частичный UNIQUE-индекс.
        sign(queue, session, "alice")
        monkeypatch.setattr(defense_queue, "_active_row", lambda conn, course_id, login: None)
        assert code_of(lambda: sign(queue, session, "alice", lab="4")) == "ALREADY_IN_QUEUE"
        monkeypatch.undo()
        assert [e["lab_key"] for e in session_view(queue, session)["entries"]] == ["3"]

    def test_failed_signup_changes_nothing(self, queue, session):
        sign(queue, session, "alice")
        code_of(lambda: sign(queue, session, "alice"))
        assert len(session_view(queue, session)["entries"]) == 1


class TestLeave:
    def test_from_waiting(self, queue, session):
        sign(queue, session, "alice")
        left = queue.leave(COURSE, "Alice", NOW + timedelta(minutes=1))
        assert left["status"] == LEFT
        assert left["finished_at"] == iso(NOW + timedelta(minutes=1))
        assert queue.active_entry(COURSE, "alice", NOW) is None
        # Запись осталась в истории.
        assert [e["status"] for e in history(queue, MONDAY, MONDAY, now=NOW)] == [LEFT]

    def test_called_student_cannot_leave(self, queue, session):
        sign(queue, session, "alice")
        queue.call_next(COURSE, session["id"], NOW)
        assert code_of(lambda: queue.leave(COURSE, "alice", NOW)) == "ALREADY_CALLED"
        assert statuses(queue, session) == {"alice": CALLED}

    def test_not_in_queue(self, queue, session):
        assert code_of(lambda: queue.leave(COURSE, "alice", NOW)) == "NOT_IN_QUEUE"


class TestSkip:
    def test_swaps_with_the_next_one(self, queue, session):
        for who in ["alice", "bob", "carol"]:
            sign(queue, session, who)
        assert queue.skip(COURSE, "alice", NOW)["place"] == 2
        assert waiting(queue, session) == ["bob", "alice", "carol"]
        assert queue.skip(COURSE, "alice", NOW)["place"] == 3
        assert waiting(queue, session) == ["bob", "carol", "alice"]
        assert code_of(lambda: queue.skip(COURSE, "alice", NOW)) == "NOTHING_TO_SKIP"

    def test_can_skip_flag(self, queue, session):
        sign(queue, session, "alice")
        sign(queue, session, "bob")
        assert queue.student_view(COURSE, MONDAY, NOW, login="alice", group="4431")["my_entry"]["can_skip"] is True
        assert queue.student_view(COURSE, MONDAY, NOW, login="bob", group="4431")["my_entry"]["can_skip"] is False

    def test_skips_only_waiting_students(self, queue, session):
        for who in ["alice", "bob", "carol"]:
            sign(queue, session, who)
        bob = queue.call_next(COURSE, session["id"], NOW)
        assert bob["github"] == "Alice"
        # bob - первый ждущий, перед ним теперь carol.
        queue.skip(COURSE, "bob", NOW)
        assert waiting(queue, session) == ["carol", "bob"]

    def test_only_within_own_session(self, queue, session):
        manual = queue.open_manual(COURSE, SETTINGS, ["4434"], None, NOW)
        sign(queue, session, "alice")
        sign(queue, manual, "bob", group="4434")
        assert code_of(lambda: queue.skip(COURSE, "alice", NOW)) == "NOTHING_TO_SKIP"

    def test_called_or_absent(self, queue, session):
        assert code_of(lambda: queue.skip(COURSE, "alice", NOW)) == "NOT_IN_QUEUE"
        sign(queue, session, "alice")
        sign(queue, session, "bob")
        queue.call_next(COURSE, session["id"], NOW)
        assert code_of(lambda: queue.skip(COURSE, "alice", NOW)) == "ALREADY_CALLED"


# ---------------------------------------------------------------------------
# Teacher
# ---------------------------------------------------------------------------


class TestCallNext:
    def test_in_fifo_order(self, queue, session):
        for who in ["alice", "bob"]:
            sign(queue, session, who)
        first = queue.call_next(COURSE, session["id"], at(15, 20))
        assert first["github"] == "Alice"
        assert first["status"] == CALLED
        assert first["called_at"] == iso(at(15, 20))
        assert queue.call_next(COURSE, session["id"], at(15, 30))["github"] == "Bob"
        # Параллельная сдача: оба вызваны одновременно.
        assert statuses(queue, session) == {"alice": CALLED, "bob": CALLED}
        assert code_of(lambda: queue.call_next(COURSE, session["id"], NOW)) == "QUEUE_EMPTY"

    def test_unknown_session(self, queue, session):
        assert code_of(lambda: queue.call_next(COURSE, 999, NOW)) == "SESSION_NOT_FOUND"
        assert code_of(lambda: queue.call_next(OTHER_COURSE, session["id"], NOW)) == "SESSION_NOT_FOUND"

    def test_expected_head(self, queue, session):
        alice = sign(queue, session, "alice")
        sign(queue, session, "bob")
        assert queue.call_next(COURSE, session["id"], NOW, expected_entry_id=alice["id"])["id"] == alice["id"]

    def test_head_changed(self, queue, session):
        alice = sign(queue, session, "alice")
        bob = sign(queue, session, "bob")
        queue.leave(COURSE, "alice", NOW)
        assert code_of(lambda: queue.call_next(COURSE, session["id"], NOW, expected_entry_id=alice["id"])) == "QUEUE_CHANGED"
        assert statuses(queue, session)["bob"] == WAITING
        assert queue.call_next(COURSE, session["id"], NOW, expected_entry_id=bob["id"])["id"] == bob["id"]

    def test_repeated_request_calls_exactly_one(self, queue, session):
        alice = sign(queue, session, "alice")
        sign(queue, session, "bob")
        first = queue.call_next(COURSE, session["id"], at(15, 20), expected_entry_id=alice["id"])
        again = queue.call_next(COURSE, session["id"], at(15, 21), expected_entry_id=alice["id"])
        assert again["id"] == first["id"] == alice["id"]
        assert again["called_at"] == iso(at(15, 20))
        assert statuses(queue, session) == {"alice": CALLED, "bob": WAITING}

    def test_expected_head_on_empty_queue(self, queue, session):
        alice = sign(queue, session, "alice")
        queue.leave(COURSE, "alice", NOW)
        assert code_of(lambda: queue.call_next(COURSE, session["id"], NOW, expected_entry_id=alice["id"])) == "QUEUE_EMPTY"


class TestIdsOutOfRange:
    """id больше SQLite INTEGER - это «не найдено», а не OverflowError."""

    @pytest.mark.parametrize("bad_id", [2**63, -(2**63) - 1, 10**30])
    @pytest.mark.parametrize("action, code", [
        (lambda q, i: q.sign_up(COURSE, i, student("alice"), "3", "ЛР3", NOW), "SESSION_NOT_FOUND"),
        (lambda q, i: q.call_next(COURSE, i, NOW), "SESSION_NOT_FOUND"),
        (lambda q, i: q.close_signup(COURSE, i, NOW), "SESSION_NOT_FOUND"),
        (lambda q, i: q.reopen_signup(COURSE, i, NOW), "SESSION_NOT_FOUND"),
        (lambda q, i: q.finish_session(COURSE, i, NOW), "SESSION_NOT_FOUND"),
        (lambda q, i: q.set_outcome(COURSE, i, DEFENDED, NOW), "ENTRY_NOT_FOUND"),
        (lambda q, i: q.return_to_queue(COURSE, i, NOW), "ENTRY_NOT_FOUND"),
    ], ids=["sign_up", "call_next", "close", "reopen", "finish", "outcome", "return"])
    def test_not_found(self, queue, session, bad_id, action, code):
        assert code_of(lambda: action(queue, bad_id)) == code

    def test_expected_entry_id(self, queue, session):
        assert code_of(lambda: queue.call_next(COURSE, session["id"], NOW, expected_entry_id=2**63)) == "QUEUE_EMPTY"
        sign(queue, session, "alice")
        assert code_of(lambda: queue.call_next(COURSE, session["id"], NOW, expected_entry_id=2**63)) == "QUEUE_CHANGED"
        assert statuses(queue, session) == {"alice": WAITING}


class TestOutcome:
    @pytest.fixture
    def called(self, queue, session):
        sign(queue, session, "alice")
        return queue.call_next(COURSE, session["id"], at(15, 20))

    @pytest.mark.parametrize("outcome", sorted(OUTCOMES))
    def test_from_called(self, queue, called, outcome):
        result = queue.set_outcome(COURSE, called["id"], outcome, at(15, 30))
        assert result["status"] == outcome
        assert result["called_at"] == iso(at(15, 20))
        assert result["finished_at"] == iso(at(15, 30))
        assert queue.active_entry(COURSE, "alice", at(15, 30)) is None

    def test_waiting_entry_is_not_called(self, queue, session):
        entry = sign(queue, session, "alice")
        assert code_of(lambda: queue.set_outcome(COURSE, entry["id"], DEFENDED, NOW)) == "ENTRY_NOT_CALLED"

    def test_left_entry_is_not_called(self, queue, session):
        entry = sign(queue, session, "alice")
        queue.leave(COURSE, "alice", NOW)
        assert code_of(lambda: queue.set_outcome(COURSE, entry["id"], DEFENDED, NOW)) == "ENTRY_NOT_CALLED"

    def test_correction(self, queue, called):
        queue.set_outcome(COURSE, called["id"], DEFENDED, at(15, 30))
        fixed = queue.set_outcome(COURSE, called["id"], NOT_DEFENDED, at(15, 31))
        assert fixed["status"] == NOT_DEFENDED
        # Исправление не переносит время, когда защита закончилась.
        assert fixed["finished_at"] == iso(at(15, 30))
        # Ошибочное «защитил» исправлено - на эту работу снова можно записаться.
        assert queue.defended_labs(COURSE, "alice", at(15, 31)) == set()

    def test_same_outcome_twice_changes_nothing(self, queue, called):
        queue.set_outcome(COURSE, called["id"], DEFENDED, at(15, 30))
        again = queue.set_outcome(COURSE, called["id"], DEFENDED, at(15, 45))
        assert again["finished_at"] == iso(at(15, 30))

    @pytest.mark.parametrize("outcome", ["left", "expired", "waiting", "called", "", "DEFENDED", None, 1])
    def test_invalid_outcome(self, queue, called, outcome):
        assert code_of(lambda: queue.set_outcome(COURSE, called["id"], outcome, NOW)) == "INVALID_OUTCOME"

    def test_unknown_entry(self, queue, called):
        assert code_of(lambda: queue.set_outcome(COURSE, 999, DEFENDED, NOW)) == "ENTRY_NOT_FOUND"
        assert code_of(lambda: queue.set_outcome(OTHER_COURSE, called["id"], DEFENDED, NOW)) == "ENTRY_NOT_FOUND"

    def test_forgotten_called_after_end_of_day(self, queue, called):
        # Вызвал, не отметил, день кончился - отметить исход всё ещё можно.
        next_morning = at(9, 0, day=TUESDAY)
        result = queue.set_outcome(COURSE, called["id"], DEFENDED, next_morning)
        assert result["status"] == DEFENDED
        # Защита понедельника не «заканчивается» во вторник в момент клика.
        assert result["called_at"] == iso(at(15, 20))
        assert result["finished_at"] == iso(at(0, 0, day=TUESDAY))

    def test_fix_of_a_past_day_keeps_the_time(self, queue, called):
        queue.set_outcome(COURSE, called["id"], NOT_DEFENDED, at(15, 30))
        fixed = queue.set_outcome(COURSE, called["id"], DEFENDED, at(10, 0, day=TUESDAY))
        assert fixed["status"] == DEFENDED
        assert fixed["finished_at"] == iso(at(15, 30))
        [entry] = history(queue, MONDAY, TUESDAY, at(10, 1, day=TUESDAY))
        assert (entry["status"], entry["finished_at"]) == (DEFENDED, iso(at(15, 30)))

    def test_defended_while_queued_again_for_the_same_lab(self, queue, called):
        # «Не защитил», студент снова записался на ту же работу, а преподаватель
        # исправляет первую запись на «защитил»: вторая осталась бы в очереди
        # на защищённую работу.
        queue.set_outcome(COURSE, called["id"], NOT_DEFENDED, at(15, 30))
        again = sign(queue, {"id": called["session_id"]}, "alice", now=at(15, 31))
        fix = lambda: queue.set_outcome(COURSE, called["id"], DEFENDED, at(15, 32))
        assert code_of(fix) == "ALREADY_IN_QUEUE"
        assert queue.active_entry(COURSE, "alice", at(15, 32))["id"] == again["id"]
        assert queue.defended_labs(COURSE, "alice", at(15, 32)) == set()
        # Вызвана по второй записи - тоже нельзя, пока та активна.
        queue.call_next(COURSE, called["session_id"], at(15, 33))
        assert code_of(fix) == "ALREADY_IN_QUEUE"
        # Вторая запись закончилась - первую можно исправить.
        queue.set_outcome(COURSE, again["id"], NO_SHOW, at(15, 40))
        assert queue.set_outcome(COURSE, called["id"], DEFENDED, at(15, 41))["status"] == DEFENDED

    def test_defended_while_queued_for_another_lab(self, queue, called):
        queue.set_outcome(COURSE, called["id"], NOT_DEFENDED, at(15, 30))
        sign(queue, {"id": called["session_id"]}, "alice", lab="4", now=at(15, 31))
        assert queue.set_outcome(COURSE, called["id"], DEFENDED, at(15, 32))["status"] == DEFENDED
        assert queue.active_entry(COURSE, "alice", at(15, 32))["lab_key"] == "4"

    def test_other_outcomes_while_queued_again(self, queue, called):
        queue.set_outcome(COURSE, called["id"], NOT_DEFENDED, at(15, 30))
        sign(queue, {"id": called["session_id"]}, "alice", now=at(15, 31))
        assert queue.set_outcome(COURSE, called["id"], NO_SHOW, at(15, 32))["status"] == NO_SHOW
        assert queue.set_outcome(COURSE, called["id"], NOT_DEFENDED, at(15, 33))["status"] == NOT_DEFENDED

    def test_expired_without_call_has_no_outcome(self, queue, session):
        entry = sign(queue, session, "alice")
        next_morning = at(9, 0, day=TUESDAY)
        assert code_of(lambda: queue.set_outcome(COURSE, entry["id"], DEFENDED, next_morning)) == "ENTRY_NOT_CALLED"


class TestReturnToQueue:
    def test_called_back_to_old_place(self, queue, session):
        for who in ["alice", "bob", "carol"]:
            sign(queue, session, who)
        alice = queue.call_next(COURSE, session["id"], at(15, 20))
        back = queue.return_to_queue(COURSE, alice["id"], at(15, 25))
        assert back["status"] == WAITING
        assert back["place"] == 1
        assert back["called_at"] is None and back["finished_at"] is None
        assert waiting(queue, session) == ["alice", "bob", "carol"]

    def test_no_show_back(self, queue, session):
        sign(queue, session, "alice")
        sign(queue, session, "bob")
        alice = queue.call_next(COURSE, session["id"], NOW)
        queue.set_outcome(COURSE, alice["id"], NO_SHOW, NOW)
        assert queue.return_to_queue(COURSE, alice["id"], NOW)["place"] == 1

    @pytest.mark.parametrize("outcome", [DEFENDED, NOT_DEFENDED])
    def test_finished_defense_is_not_returned(self, queue, session, outcome):
        sign(queue, session, "alice")
        alice = queue.call_next(COURSE, session["id"], NOW)
        queue.set_outcome(COURSE, alice["id"], outcome, NOW)
        assert code_of(lambda: queue.return_to_queue(COURSE, alice["id"], NOW)) == "ENTRY_NOT_CALLED"

    def test_waiting_is_not_returned(self, queue, session):
        entry = sign(queue, session, "alice")
        assert code_of(lambda: queue.return_to_queue(COURSE, entry["id"], NOW)) == "ENTRY_NOT_CALLED"

    def test_day_over(self, queue, session):
        sign(queue, session, "alice")
        alice = queue.call_next(COURSE, session["id"], NOW)
        assert code_of(lambda: queue.return_to_queue(COURSE, alice["id"], at(0, 0, day=TUESDAY))) == "DAY_OVER"

    def test_student_already_signed_up_again(self, queue, session):
        sign(queue, session, "alice")
        alice = queue.call_next(COURSE, session["id"], NOW)
        queue.set_outcome(COURSE, alice["id"], NO_SHOW, NOW)
        sign(queue, session, "alice")
        assert code_of(lambda: queue.return_to_queue(COURSE, alice["id"], NOW)) == "ALREADY_IN_QUEUE"
        assert statuses(queue, session)["alice"] == WAITING

    def test_lab_defended_in_another_entry(self, queue, session):
        # «Не явился», записалась снова и защитила: старую запись не вернуть.
        sign(queue, session, "alice")
        first = queue.call_next(COURSE, session["id"], at(15, 20))
        queue.set_outcome(COURSE, first["id"], NO_SHOW, at(15, 21))
        sign(queue, session, "alice", now=at(15, 22))
        second = queue.call_next(COURSE, session["id"], at(15, 23))
        queue.set_outcome(COURSE, second["id"], DEFENDED, at(15, 30))
        assert code_of(lambda: queue.return_to_queue(COURSE, first["id"], at(15, 31))) == "LAB_ALREADY_DEFENDED"
        assert queue.active_entry(COURSE, "alice", at(15, 31)) is None
        assert statuses(queue, session, now=at(15, 31)) == {"alice": DEFENDED}
        entries = session_view(queue, session, now=at(15, 31))["entries"]
        assert [e["status"] for e in entries] == [NO_SHOW, DEFENDED]

    def test_other_lab_defended_does_not_block(self, queue, session):
        sign(queue, session, "alice", lab="4")
        first = queue.call_next(COURSE, session["id"], at(15, 20))
        queue.set_outcome(COURSE, first["id"], DEFENDED, at(15, 21))
        sign(queue, session, "alice", now=at(15, 22))
        second = queue.call_next(COURSE, session["id"], at(15, 23))
        queue.set_outcome(COURSE, second["id"], NO_SHOW, at(15, 24))
        assert queue.return_to_queue(COURSE, second["id"], at(15, 25))["status"] == WAITING

    def test_unknown_entry(self, queue, session):
        assert code_of(lambda: queue.return_to_queue(COURSE, 999, NOW)) == "ENTRY_NOT_FOUND"


# ---------------------------------------------------------------------------
# End of the day
# ---------------------------------------------------------------------------


class TestEndOfDay:
    @pytest.fixture
    def left_overnight(self, queue, session):
        sign(queue, session, "alice")
        sign(queue, session, "bob")
        queue.call_next(COURSE, session["id"], NOW)
        return session

    def test_expired_without_sync_day(self, queue, left_overnight):
        # Утром никто не вызывал sync_day: закрывает первое же чтение.
        view = session_view(queue, left_overnight, now=at(8, 0, day=TUESDAY))
        assert {e["status"] for e in view["entries"]} == {EXPIRED}
        assert {e["finished_at"] for e in view["entries"]} == {iso(at(0, 0, day=TUESDAY))}
        assert view["signup"] == SIGNUP_CLOSED

    def test_last_second_of_the_day_is_still_today(self, queue, left_overnight):
        assert statuses(queue, left_overnight, now=at(23, 59, 59)) == {"alice": CALLED, "bob": WAITING}

    def test_midnight_closes_the_day(self, queue, left_overnight):
        assert statuses(queue, left_overnight, now=at(0, 0, day=TUESDAY)) == {"alice": EXPIRED, "bob": EXPIRED}

    @pytest.mark.parametrize("read", [
        lambda q, now: q.active_entry(COURSE, "bob", now),
        lambda q, now: q.student_view(COURSE, TUESDAY, now, login="bob", group="4431"),
        lambda q, now: q.defended_labs(COURSE, "bob", now),
        lambda q, now: q.history(COURSE, TUESDAY, TUESDAY, MSK, now=now),
    ], ids=["active_entry", "student_view", "defended_labs", "history"])
    def test_every_read_expires_first(self, db_path, queue, left_overnight, read):
        read(queue, at(9, 0, day=TUESDAY))
        with QueueStore(db_path).connect() as conn:
            rows = conn.execute("SELECT status FROM entries").fetchall()
        assert {row["status"] for row in rows} == {EXPIRED}

    def test_no_active_entry_the_next_morning(self, queue, left_overnight):
        tomorrow = at(9, 0, day=TUESDAY)
        assert queue.active_entry(COURSE, "bob", tomorrow) is None
        assert queue.student_view(COURSE, TUESDAY, tomorrow, login="bob", group="4431")["my_entry"] is None

    def test_next_day_signup_is_not_blocked(self, queue, left_overnight):
        tomorrow = at(15, 0, day=TUESDAY)
        tuesday = queue.sync_day(COURSE, SETTINGS, [lesson(day=TUESDAY)], tomorrow)
        assert [s["day"] for s in tuesday] == ["2026-11-10"]
        assert sign(queue, tuesday[0], "bob", now=tomorrow)["place"] == 1

    def test_expiry_is_idempotent(self, queue, left_overnight):
        statuses(queue, left_overnight, now=at(0, 0, day=TUESDAY))
        view = session_view(queue, left_overnight, now=at(12, 0, day=TUESDAY))
        assert {e["finished_at"] for e in view["entries"]} == {iso(at(0, 0, day=TUESDAY))}

    def test_only_active_entries_expire(self, queue, session):
        sign(queue, session, "alice")
        queue.leave(COURSE, "alice", at(15, 5))
        assert statuses(queue, session, now=at(1, 0, day=TUESDAY)) == {"alice": LEFT}


# ---------------------------------------------------------------------------
# Manual sessions, closing and finishing
# ---------------------------------------------------------------------------


class TestManual:
    def test_open(self, queue):
        now = at(12, 0)
        manual = queue.open_manual(COURSE, SETTINGS, ["4434", "4431"], at(13, 30), now)
        assert manual["kind"] == KIND_MANUAL
        assert manual["lesson_key"] is None
        assert manual["groups"] == ["4431", "4434"]
        assert manual["signup_opens_at"] == iso(now)
        assert manual["signup_closes_at"] == iso(at(13, 30))
        assert manual["day_ends_at"] == iso(at(0, 0, day=TUESDAY))
        assert DefenseQueue.signup_state(manual, now) == SIGNUP_OPEN
        assert DefenseQueue.signup_state(manual, at(13, 30)) == SIGNUP_CLOSED

    def test_until_end_of_day_by_default(self, queue):
        manual = queue.open_manual(COURSE, SETTINGS, ["4431"], None, at(12, 0))
        assert manual["signup_closes_at"] == iso(at(0, 0, day=TUESDAY))

    def test_idempotent(self, queue):
        first = queue.open_manual(COURSE, SETTINGS, ["4431", "4434"], None, at(12, 0))
        again = queue.open_manual(COURSE, SETTINGS, [" 4434", "4431", "4431"], None, at(12, 1))
        assert again == first
        other = queue.open_manual(COURSE, SETTINGS, ["4431"], None, at(12, 2))
        assert other["id"] != first["id"]

    def test_extended_before_the_end(self, queue):
        # Пара затянулась: «Открыть запись» ещё раз с более поздним концом.
        first = queue.open_manual(COURSE, SETTINGS, [], at(15, 40), at(15, 0))
        sign(queue, first, "bob", now=at(15, 1))
        longer = queue.open_manual(COURSE, SETTINGS, [], at(18, 0), at(15, 30))
        assert longer["id"] == first["id"]
        assert longer["signup_opens_at"] == first["signup_opens_at"]
        assert longer["signup_closes_at"] == longer["ends_at"] == iso(at(18, 0))
        assert DefenseQueue.signup_state(longer, at(16, 0)) == SIGNUP_OPEN
        assert sign(queue, longer, "carol", now=at(16, 0))["place"] == 2

    def test_reopened_after_the_window_is_over(self, queue):
        first = queue.open_manual(COURSE, SETTINGS, [], at(15, 40), at(15, 0))
        sign(queue, first, "bob", now=at(15, 1))
        assert code_of(lambda: queue.reopen_signup(COURSE, first["id"], at(15, 45))) == "SIGNUP_CLOSED"
        again = queue.open_manual(COURSE, SETTINGS, [], at(18, 0), at(15, 45))
        assert again["id"] == first["id"]
        assert again["signup_closes_at"] == iso(at(18, 0))
        # Одна очередь, места сохранены.
        assert sign(queue, again, "carol", now=at(15, 46))["place"] == 2
        manual = [s for s in queue.day_view(COURSE, MONDAY, at(15, 47)) if s["kind"] == KIND_MANUAL]
        assert [s["id"] for s in manual] == [first["id"]]
        assert waiting(queue, first, now=at(15, 47)) == ["bob", "carol"]

    def test_closed_by_the_teacher_is_reopened(self, queue):
        first = queue.open_manual(COURSE, SETTINGS, ["4431"], None, at(12, 0))
        queue.close_signup(COURSE, first["id"], at(12, 5))
        again = queue.open_manual(COURSE, SETTINGS, ["4431"], None, at(12, 6))
        assert again["id"] == first["id"]
        assert again["closed_at"] is None
        assert DefenseQueue.signup_state(again, at(12, 7)) == SIGNUP_OPEN

    def test_finished_reception_is_reopened(self, queue):
        first = queue.open_manual(COURSE, SETTINGS, ["4431"], None, at(12, 0))
        sign(queue, first, "alice", now=at(12, 1))
        queue.finish_session(COURSE, first["id"], at(12, 30))
        again = queue.open_manual(COURSE, SETTINGS, ["4431"], at(14, 0), at(13, 0))
        assert again["id"] == first["id"]
        # Записи, закрытые «Завершить приём», остаются в истории.
        assert statuses(queue, first, now=at(13, 1)) == {"alice": EXPIRED}
        assert sign(queue, again, "bob", now=at(13, 1))["place"] == 1

    def test_window_can_be_shortened(self, queue):
        first = queue.open_manual(COURSE, SETTINGS, ["4431"], None, at(12, 0))
        shorter = queue.open_manual(COURSE, SETTINGS, ["4431"], at(13, 0), at(12, 1))
        assert shorter["id"] == first["id"]
        assert shorter["signup_closes_at"] == iso(at(13, 0))

    def test_next_day_gets_its_own_session(self, queue):
        monday = queue.open_manual(COURSE, SETTINGS, ["4431"], None, at(12, 0))
        tuesday = queue.open_manual(COURSE, SETTINGS, ["4431"], None, at(12, 0, day=TUESDAY))
        assert tuesday["id"] != monday["id"]
        assert tuesday["day"] == "2026-11-10"

    def test_invalid_time_changes_nothing(self, queue):
        first = queue.open_manual(COURSE, SETTINGS, ["4431"], at(13, 0), at(12, 0))
        assert code_of(lambda: queue.open_manual(COURSE, SETTINGS, ["4431"], at(12, 30), at(12, 30))) == "INVALID_TIME"
        assert session_view(queue, first, now=at(12, 31))["signup_closes_at"] == iso(at(13, 0))

    def test_all_groups(self, queue):
        manual = queue.open_manual(COURSE, SETTINGS, [], None, at(12, 0))
        assert manual["groups"] == []
        sign(queue, manual, "alice", group="4431", now=at(12, 1))
        sign(queue, manual, "bob", group="4334", now=at(12, 2))
        assert waiting(queue, manual, now=at(12, 3)) == ["alice", "bob"]

    def test_group_restriction(self, queue):
        manual = queue.open_manual(COURSE, SETTINGS, ["4434"], None, at(12, 0))
        assert code_of(lambda: sign(queue, manual, "alice", group="4431", now=at(12, 1))) == "NOT_YOUR_GROUP"

    @pytest.mark.parametrize("closes_at", [at(12, 0), at(11, 0), at(0, 0, 1, day=TUESDAY)])
    def test_invalid_time(self, queue, closes_at):
        assert code_of(lambda: queue.open_manual(COURSE, SETTINGS, ["4431"], closes_at, at(12, 0))) == "INVALID_TIME"

    def test_end_of_day_exactly_is_valid(self, queue):
        manual = queue.open_manual(COURSE, SETTINGS, ["4431"], at(0, 0, day=TUESDAY), at(12, 0))
        assert manual["signup_closes_at"] == iso(at(0, 0, day=TUESDAY))

    @pytest.mark.parametrize("groups", [[""], ["  "], [4431], [None], "4431", None])
    def test_invalid_groups(self, queue, groups):
        assert code_of(lambda: queue.open_manual(COURSE, SETTINGS, groups, None, at(12, 0))) == "INVALID_GROUPS"


class TestParseClosesAt:
    def test_today_in_course_timezone(self):
        assert parse_closes_at("17:30", at(12, 0), MSK) == at(17, 30)
        # 23:30 UTC 9 ноября - это уже 10 ноября по Москве.
        late = datetime(2026, 11, 9, 23, 30, tzinfo=timezone.utc)
        assert parse_closes_at("09:05", late, MSK) == at(9, 5, day=TUESDAY)

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty(self, value):
        assert parse_closes_at(value, at(12, 0), MSK) is None

    @pytest.mark.parametrize("value", ["24:00", "12:60", "1730", "17:3", "abc", 1730])
    def test_invalid(self, value):
        assert code_of(lambda: parse_closes_at(value, at(12, 0), MSK)) == "INVALID_TIME"


class TestHelpers:
    def test_iso_utc(self):
        assert iso_utc(at(15, 0)) == "2026-11-09T12:00:00+00:00"
        assert iso_utc(datetime(2026, 11, 9, 12, 0, 0, 999999, tzinfo=timezone.utc)) == "2026-11-09T12:00:00+00:00"
        with pytest.raises(ValueError):
            iso_utc(datetime(2026, 11, 9, 12, 0))

    def test_normalize_login(self):
        assert normalize_login("  Alice-B ") == "alice-b"
        assert normalize_login(None) == normalize_login("") == ""

    @pytest.mark.parametrize("method", ["history", "defended_labs", "active_entry"])
    def test_now_is_required(self, method):
        parameter = inspect.signature(getattr(DefenseQueue, method)).parameters["now"]
        assert parameter.default is inspect.Parameter.empty

    def test_modules_never_read_the_clock(self):
        # Время приходит только параметром now: так его подменяют тесты и
        # так все запросы одного HTTP-обращения видят один и тот же момент.
        for module in (defense_queue, queue_store):
            with open(module.__file__, encoding="utf-8") as source:
                assert "datetime.now" not in source.read(), module.__name__


class TestCloseReopenFinish:
    def test_close_keeps_waiting_students(self, queue, session):
        sign(queue, session, "alice")
        closed = queue.close_signup(COURSE, session["id"], at(15, 30))
        assert closed["closed_at"] == iso(at(15, 30))
        assert DefenseQueue.signup_state(closed, at(15, 31)) == SIGNUP_CLOSED
        assert code_of(lambda: sign(queue, session, "bob", now=at(15, 31))) == "SIGNUP_CLOSED"
        assert queue.call_next(COURSE, session["id"], at(15, 32))["github"] == "Alice"

    def test_close_twice_keeps_first_time(self, queue, session):
        queue.close_signup(COURSE, session["id"], at(15, 30))
        assert queue.close_signup(COURSE, session["id"], at(15, 40))["closed_at"] == iso(at(15, 30))

    def test_reopen(self, queue, session):
        queue.close_signup(COURSE, session["id"], at(15, 30))
        reopened = queue.reopen_signup(COURSE, session["id"], at(15, 35))
        assert reopened["closed_at"] is None
        assert sign(queue, session, "bob", now=at(15, 36))["place"] == 1

    def test_reopen_after_window(self, queue, session):
        queue.close_signup(COURSE, session["id"], at(15, 30))
        assert code_of(lambda: queue.reopen_signup(COURSE, session["id"], at(16, 40))) == "SIGNUP_CLOSED"

    def test_finish(self, queue, session):
        for who in ["alice", "bob", "carol"]:
            sign(queue, session, who)
        alice = queue.call_next(COURSE, session["id"], at(15, 20))
        finished = queue.finish_session(COURSE, session["id"], at(16, 0))
        assert finished["closed_at"] == iso(at(16, 0))
        assert statuses(queue, session, now=at(16, 1)) == {"alice": CALLED, "bob": EXPIRED, "carol": EXPIRED}
        view = session_view(queue, session, now=at(16, 1))
        assert {e["finished_at"] for e in view["entries"] if e["status"] == EXPIRED} == {iso(at(16, 0))}
        # Вызванному исход отмечается как обычно.
        assert queue.set_outcome(COURSE, alice["id"], DEFENDED, at(16, 5))["status"] == DEFENDED

    @pytest.mark.parametrize("action", ["close_signup", "reopen_signup", "finish_session"])
    def test_unknown_session(self, queue, session, action):
        assert code_of(lambda: getattr(queue, action)(COURSE, 999, NOW)) == "SESSION_NOT_FOUND"
        assert code_of(lambda: getattr(queue, action)(OTHER_COURSE, session["id"], NOW)) == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Schedule changes
# ---------------------------------------------------------------------------


class TestScheduleChange:
    """Расписание изменилось: записи не пропадают, новые - по новому расписанию."""

    MOVED = lesson(start=(17, 0), end=(18, 30))

    def test_entries_stay_new_signups_go_to_new_lesson(self, queue, session):
        sign(queue, session, "alice")
        now = at(15, 20)
        sessions = queue.sync_day(COURSE, SETTINGS, [self.MOVED], now)
        current = {self.MOVED.key}
        by_key = {s["lesson_key"]: s for s in sessions}
        assert set(by_key) == {PAIR4.key, self.MOVED.key}

        old = session_view(queue, session, now, current_keys=current)
        assert old["in_schedule"] is False
        assert old["signup"] == SIGNUP_CLOSED
        assert [e["github"] for e in old["entries"]] == ["Alice"]
        assert code_of(lambda: queue.sign_up(
            COURSE, session["id"], student("bob"), "3", "ЛР3", now, current,
        )) == "SIGNUP_CLOSED"
        # Тех, кто уже стоит, преподаватель принимает как обычно.
        assert queue.call_next(COURSE, session["id"], now)["github"] == "Alice"

        moved = by_key[self.MOVED.key]
        assert DefenseQueue.signup_state(moved, now, current) == SIGNUP_UPCOMING
        assert queue.sign_up(COURSE, moved["id"], student("bob"), "3", "ЛР3", at(16, 45), current)["place"] == 1

    def test_lesson_without_entries_disappears_from_pages(self, queue, session):
        now = at(15, 20)
        queue.sync_day(COURSE, SETTINGS, [self.MOVED], now)
        current = {self.MOVED.key}
        student_ids = [s["id"] for s in queue.student_view(COURSE, MONDAY, now, current_keys=current)["sessions"]]
        admin_ids = [s["id"] for s in queue.day_view(COURSE, MONDAY, now, current)]
        assert session["id"] not in student_ids
        assert session["id"] not in admin_ids
        assert len(student_ids) == len(admin_ids) == 1

    def test_unknown_schedule_does_not_close_signup(self, queue, session):
        # Источник недоступен (current_keys=None): ничего не закрываем.
        assert queue.sign_up(COURSE, session["id"], student("alice"), "3", "ЛР3", NOW, None)["place"] == 1
        assert session_view(queue, session)["in_schedule"] is True

    def test_all_day_queue_closes_when_the_course_gets_a_schedule(self, queue):
        # Утром расписания не было - общая очередь дня; днём его добавили.
        all_day = Lesson(key="all-day", starts_at=at(0, 0), ends_at=at(0, 0, day=TUESDAY), groups=())
        whole_day = queue.sync_day(COURSE, ALL_DAY_SETTINGS, [all_day], at(9, 0))[0]
        assert DefenseQueue.signup_state(whole_day, at(9, 0), {"all-day"}) == SIGNUP_OPEN
        sign(queue, whole_day, "alice", now=at(9, 0))

        now = at(12, 0)
        pair = lesson(start=(13, 0), end=(14, 30), groups=("4432",))
        current = {pair.key}
        queue.sync_day(COURSE, SETTINGS, [pair], now)
        old = session_view(queue, whole_day, now, current_keys=current)
        assert old["in_schedule"] is False
        assert old["signup"] == SIGNUP_CLOSED
        assert code_of(lambda: queue.sign_up(
            COURSE, whole_day["id"], student("bob"), "3", "ЛР3", now, current,
        )) == "SIGNUP_CLOSED"
        # Кто уже стоит, остаётся, и его принимают до конца дня.
        assert [e["github"] for e in old["entries"]] == ["Alice"]
        assert queue.call_next(COURSE, whole_day["id"], now)["github"] == "Alice"

    def test_empty_all_day_queue_disappears_with_a_schedule(self, queue):
        all_day = Lesson(key="all-day", starts_at=at(0, 0), ends_at=at(0, 0, day=TUESDAY), groups=())
        whole_day = queue.sync_day(COURSE, ALL_DAY_SETTINGS, [all_day], at(9, 0))[0]
        now = at(12, 0)
        queue.sync_day(COURSE, SETTINGS, [PAIR4], now)
        ids = [s["id"] for s in queue.student_view(COURSE, MONDAY, now, current_keys={PAIR4.key})["sessions"]]
        assert whole_day["id"] not in ids and len(ids) == 1

    def test_manual_session_ignores_schedule_keys(self, queue):
        manual = queue.open_manual(COURSE, SETTINGS, ["4431"], None, NOW)
        assert DefenseQueue.signup_state(manual, NOW, {self.MOVED.key}) == SIGNUP_OPEN


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class TestStudentView:
    @pytest.fixture
    def filled(self, queue, session):
        for who in ["alice", "bob", "carol"]:
            sign(queue, session, who)
        queue.call_next(COURSE, session["id"], at(15, 10))
        return session

    def test_anonymous_sees_counters_only(self, queue, filled):
        view = queue.student_view(COURSE, MONDAY, NOW)
        assert view["my_entry"] is None and view["my_last_result"] is None
        (item,) = view["sessions"]
        assert item["for_me"] is False
        assert item["entries"] is None
        assert item["waiting_count"] == 2
        assert item["called_count"] == 1
        assert item["signup"] == SIGNUP_OPEN
        assert set(item) == {
            "id", "kind", "lesson_type", "subject", "groups", "starts_at", "ends_at",
            "signup_opens_at", "signup_closes_at", "signup", "for_me",
            "waiting_count", "called_count", "entries",
        }

    def test_other_group_sees_no_names(self, queue, filled):
        (item,) = queue.student_view(COURSE, MONDAY, NOW, login="dave", group="4434")["sessions"]
        assert item["for_me"] is False
        assert item["entries"] is None

    def test_signed_in_student(self, queue, filled):
        view = queue.student_view(COURSE, MONDAY, NOW, login="Carol", group="4431")
        (item,) = view["sessions"]
        assert item["for_me"] is True
        assert item["entries"] == [
            {"place": None, "status": CALLED, "name": "Иванова Алиса", "group": "4431", "lab": "ЛР3", "is_me": False},
            {"place": 1, "status": WAITING, "name": "Петров Борис", "group": "4431", "lab": "ЛР3", "is_me": False},
            {"place": 2, "status": WAITING, "name": "Сидорова Вера", "group": "4431", "lab": "ЛР3", "is_me": True},
        ]
        mine = view["my_entry"]
        assert mine["status"] == WAITING
        assert mine["place"] == 2
        assert mine["session_id"] == filled["id"]
        assert mine["can_skip"] is False
        assert set(mine) == {"id", "session_id", "status", "place", "lab", "created_at", "called_at", "can_skip"}

    def test_called_student(self, queue, filled):
        mine = queue.student_view(COURSE, MONDAY, NOW, login="alice", group="4431")["my_entry"]
        assert mine["status"] == CALLED
        assert mine["place"] is None
        assert mine["called_at"] == iso(at(15, 10))
        assert mine["can_skip"] is False

    def test_last_result(self, queue, filled):
        alice = queue.active_entry(COURSE, "alice", NOW)
        queue.set_outcome(COURSE, alice["id"], NOT_DEFENDED, at(15, 30))
        sign(queue, filled, "alice", lab="3", now=at(15, 31))
        view = queue.student_view(COURSE, MONDAY, at(15, 32), login="alice", group="4431")
        assert view["my_last_result"] == {"status": NOT_DEFENDED, "lab": "ЛР3", "finished_at": iso(at(15, 30))}
        assert view["my_entry"]["place"] == 3

    def test_finished_entries_are_not_listed(self, queue, filled):
        queue.leave(COURSE, "bob", NOW)
        (item,) = queue.student_view(COURSE, MONDAY, NOW, login="carol", group="4431")["sessions"]
        assert [e["name"] for e in item["entries"]] == ["Иванова Алиса", "Сидорова Вера"]

    def test_all_groups_session_is_for_everyone(self, queue):
        queue.open_manual(COURSE, SETTINGS, [], None, NOW)
        (item,) = queue.student_view(COURSE, MONDAY, NOW, login="dave", group="4334")["sessions"]
        assert item["for_me"] is True
        assert item["entries"] == []

    def test_output_is_json_ready(self, queue, filled):
        json.dumps(queue.student_view(COURSE, MONDAY, NOW, login="alice", group="4431"))
        json.dumps(queue.day_view(COURSE, MONDAY, NOW))


class TestDayView:
    def test_all_entries_in_queue_order(self, queue, session):
        for who in ["alice", "bob", "carol"]:
            sign(queue, session, who)
        alice = queue.call_next(COURSE, session["id"], at(15, 10))
        queue.set_outcome(COURSE, alice["id"], DEFENDED, at(15, 20))
        queue.leave(COURSE, "bob", at(15, 21))
        (item,) = queue.day_view(COURSE, MONDAY, at(15, 22))
        assert [(e["github"], e["status"], e["place"]) for e in item["entries"]] == [
            ("Alice", DEFENDED, None), ("Bob", LEFT, None), ("Carol", WAITING, 1),
        ]
        assert item["waiting_count"] == 1 and item["called_count"] == 0
        assert set(item["entries"][0]) == {
            "id", "session_id", "place", "status", "name", "group", "github", "lab", "lab_key",
            "created_at", "called_at", "finished_at",
        }
        assert item["entries"][0]["finished_at"] == iso(at(15, 20))

    def test_day_as_string(self, queue, session):
        assert [s["id"] for s in queue.day_view(COURSE, "2026-11-09", NOW)] == [session["id"]]
        assert queue.day_view(COURSE, "2026-11-10", NOW) == []

    def test_day_is_not_a_datetime(self, queue, session):
        with pytest.raises(TypeError):
            queue.day_view(COURSE, NOW, NOW)


class TestHistory:
    @pytest.fixture
    def two_days(self, queue, session):
        sign(queue, session, "alice", lab="3")
        sign(queue, session, "bob", lab="4", now=at(15, 1))
        alice = queue.call_next(COURSE, session["id"], at(15, 10))
        queue.set_outcome(COURSE, alice["id"], DEFENDED, at(15, 20))
        # Вторник, 00:30 по Москве - это ещё понедельник по UTC.
        night = at(0, 30, day=TUESDAY)
        all_day = Lesson(key="all-day", starts_at=at(0, 0, day=TUESDAY),
                         ends_at=at(0, 0, day=date(2026, 11, 11)), groups=())
        tuesday = queue.sync_day(COURSE, ALL_DAY_SETTINGS, [all_day], night)[0]
        sign(queue, tuesday, "carol", lab="3", group="4434", now=night)
        return night

    def test_range_is_in_course_timezone(self, queue, two_days):
        monday = history(queue, MONDAY, MONDAY, now=two_days)
        tuesday = history(queue, TUESDAY, TUESDAY, now=two_days)
        assert [e["github"] for e in monday] == ["Bob", "Alice"]
        assert [e["github"] for e in tuesday] == ["Carol"]
        assert tuesday[0]["day"] == "2026-11-10"
        assert tuesday[0]["kind"] == KIND_ALL_DAY

    def test_every_status_is_kept(self, queue, two_days):
        items = history(queue, MONDAY, TUESDAY, now=two_days)
        assert [(e["github"], e["status"]) for e in items] == [
            ("Carol", WAITING), ("Bob", EXPIRED), ("Alice", DEFENDED),
        ]
        alice = items[-1]
        assert alice["called_at"] == iso(at(15, 10))
        assert alice["finished_at"] == iso(at(15, 20))
        assert alice["lesson_type"] == "Лабораторное занятие"
        assert alice["session_starts_at"] == iso(at(15, 10))

    @pytest.mark.parametrize("filters, expected", [
        ({"group": "4434"}, ["Carol"]),
        ({"group": " 4431 "}, ["Bob", "Alice"]),
        ({"query": "ИВАНОВА"}, ["Alice"]),
        ({"query": "вера"}, ["Carol"]),
        ({"query": "BOB"}, ["Bob"]),
        ({"query": "nobody"}, []),
        ({"lab_key": "3"}, ["Carol", "Alice"]),
        ({"lab_key": "3", "group": "4431"}, ["Alice"]),
    ])
    def test_filters(self, queue, two_days, filters, expected):
        items = history(queue, MONDAY, TUESDAY, now=two_days, **filters)
        assert [e["github"] for e in items] == expected

    def test_dates_as_strings_and_other_course(self, queue, two_days):
        assert len(history(queue, "2026-11-09", "2026-11-10", now=two_days)) == 3
        assert history(queue, MONDAY, TUESDAY, now=two_days, course=OTHER_COURSE) == []
        assert queue.history(COURSE, TUESDAY, MONDAY, MSK, two_days) == ([], False)

    @pytest.mark.parametrize("limit, expected, truncated", [
        (1, ["Carol"], True),
        (2, ["Carol", "Bob"], True),
        (3, ["Carol", "Bob", "Alice"], False),
    ])
    def test_limit(self, queue, two_days, limit, expected, truncated):
        entries, more = queue.history(COURSE, MONDAY, TUESDAY, MSK, two_days, limit=limit)
        assert [e["github"] for e in entries] == expected
        assert more is truncated

    def test_limit_counts_matching_entries_only(self, queue, two_days):
        assert history(queue, MONDAY, TUESDAY, now=two_days, query="иванова", limit=1)[0]["github"] == "Alice"
        assert history(queue, MONDAY, TUESDAY, now=two_days, group="4431", limit=2) == \
            history(queue, MONDAY, TUESDAY, now=two_days, group="4431")

    @pytest.mark.parametrize("day_from, day_to", [
        (date(1999, 12, 31), MONDAY),
        (MONDAY, date(2101, 1, 1)),
        ("0001-01-01", "2026-11-09"),
        ("2026-01-01", "9999-12-31"),
        ("2026-02-30", "2026-11-09"),
        ("вчера", "2026-11-09"),
        (None, MONDAY),
        (MONDAY - timedelta(days=HISTORY_MAX_DAYS), MONDAY),
    ], ids=["before-2000", "after-2100", "year-1", "year-9999", "no-such-day", "not-a-date", "none", "too-long"])
    def test_invalid_period(self, queue, day_from, day_to):
        assert code_of(lambda: queue.history(COURSE, day_from, day_to, MSK, NOW)) == "INVALID_PERIOD"

    def test_edges_of_the_allowed_period(self, queue):
        assert queue.history(COURSE, MONDAY - timedelta(days=HISTORY_MAX_DAYS - 1), MONDAY, MSK, NOW) == ([], False)
        # Крайние дни в крайних поясах: datetime не переполняется.
        east, west = timezone(timedelta(hours=14)), timezone(timedelta(hours=-14))
        assert queue.history(COURSE, HISTORY_FIRST_DAY, HISTORY_FIRST_DAY, east, NOW) == ([], False)
        assert queue.history(COURSE, HISTORY_LAST_DAY, HISTORY_LAST_DAY, west, NOW) == ([], False)


# ---------------------------------------------------------------------------
# Restart and storage
# ---------------------------------------------------------------------------


class TestRestart:
    """Рестарт backend посреди пары: новый объект на том же файле видит ту же очередь."""

    def test_queue_survives(self, db_path):
        before = DefenseQueue(QueueStore(db_path))
        session = before.sync_day(COURSE, SETTINGS, [PAIR4], NOW)[0]
        for who in ["alice", "bob", "carol"]:
            sign(before, session, who)
        before.call_next(COURSE, session["id"], at(15, 10))

        after = DefenseQueue(QueueStore(str(db_path)))
        assert [s["id"] for s in after.sync_day(COURSE, SETTINGS, [PAIR4], at(15, 20))] == [session["id"]]
        assert statuses(after, session, now=at(15, 20)) == {"alice": CALLED, "bob": WAITING, "carol": WAITING}
        assert sign(after, session, "dave", now=at(15, 21))["place"] == 3
        assert after.call_next(COURSE, session["id"], at(15, 22))["github"] == "Bob"


class TestStore:
    def test_schema_and_wal(self, db_path, queue, session):
        assert db_path.parent.is_dir()
        with QueueStore(db_path).connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"sessions", "entries", "schedule_snapshots"} <= tables
        assert {
            "entries_one_active", "entries_by_session", "entries_by_course_time",
            "entries_by_login", "sessions_by_course_day",
        } <= indexes

    def test_unique_index_holds_without_the_code(self, db_path, queue, session):
        # Даже если проверку в sign_up обойти, вторую активную запись база не примет.
        sign(queue, session, "alice")
        with pytest.raises(sqlite3.IntegrityError):
            with QueueStore(db_path).transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO entries (session_id, course_id, login, github, student_name, group_name,
                                         lab_key, lab_name, seq, status, created_at)
                    VALUES (?, ?, 'alice', 'Alice', 'x', '4431', '4', 'ЛР4', 99, 'called', 'now')
                    """,
                    (session["id"], COURSE),
                )

    def test_locked_database_is_storage_unavailable(self, db_path, queue, session, caplog):
        blocker = sqlite3.connect(str(db_path), isolation_level=None)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            impatient = DefenseQueue(QueueStore(db_path, busy_timeout=0.2))
            with caplog.at_level(logging.ERROR, logger="grading.queue_store"):
                assert code_of(lambda: sign(impatient, session, "alice")) == "STORAGE_UNAVAILABLE"
            assert "недоступно" in caplog.text
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        # Блокировку отпустили - работает снова, отказ ничего не записал.
        assert sign(queue, session, "alice")["place"] == 1

    def test_directory_instead_of_file(self, tmp_path):
        broken = DefenseQueue(QueueStore(tmp_path))
        assert code_of(lambda: broken.sync_day(COURSE, SETTINGS, [PAIR4], NOW)) == "STORAGE_UNAVAILABLE"

    def test_damaged_file(self, tmp_path):
        path = tmp_path / "queue.sqlite3"
        path.write_bytes(b"this is not an sqlite database" * 100)
        broken = DefenseQueue(QueueStore(path))
        assert code_of(lambda: broken.active_entry(COURSE, "alice", NOW)) == "STORAGE_UNAVAILABLE"

    def test_newer_schema_is_refused(self, tmp_path):
        path = tmp_path / "queue.sqlite3"
        conn = sqlite3.connect(str(path))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.close()
        assert code_of(lambda: DefenseQueue(QueueStore(path)).active_entry(COURSE, "alice", NOW)) == "STORAGE_UNAVAILABLE"

    def test_naive_time_is_rejected(self, queue, session):
        with pytest.raises(ValueError):
            queue.call_next(COURSE, session["id"], datetime(2026, 11, 9, 15, 0))


def patch_wal_pragma(monkeypatch, answer):
    """PRAGMA journal_mode = WAL отвечает answer(conn): так ведёт себя файловая система без WAL."""
    real_connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode = WAL":
                return answer(self)
            return super().execute(sql, *args)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Connection, **k))
    monkeypatch.setattr(queue_store, "WAL_RETRY_PAUSE_SECONDS", 0)


def journal_mode(path) -> str:
    with QueueStore(path).connect() as conn:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]


def queue_still_works(path):
    queue = DefenseQueue(QueueStore(path))
    session = queue.sync_day(COURSE, SETTINGS, [PAIR4], NOW)[0]
    sign(queue, session, "alice")
    assert queue.student_view(COURSE, MONDAY, NOW, login="alice", group="4431")["my_entry"]["place"] == 1


class TestWalFallback:
    """Без WAL очередь работает, только чтения и записи ждут друг друга; об этом warning."""

    def test_journal_stays_delete(self, db_path, monkeypatch, caplog):
        patch_wal_pragma(monkeypatch, lambda conn: sqlite3.Connection.execute(conn, "PRAGMA journal_mode"))
        with caplog.at_level(logging.WARNING, logger="grading.queue_store"):
            queue_still_works(db_path)
        assert "а не WAL" in caplog.text
        assert journal_mode(db_path) == "delete"

    def test_pragma_keeps_failing(self, db_path, monkeypatch, caplog):
        attempts = []

        def locked(conn):
            attempts.append(1)
            raise sqlite3.OperationalError("database is locked")

        patch_wal_pragma(monkeypatch, locked)
        with caplog.at_level(logging.WARNING, logger="grading.queue_store"):
            queue_still_works(db_path)
        assert len(attempts) == queue_store.WAL_ATTEMPTS
        assert "не удалось включить WAL" in caplog.text
        assert journal_mode(db_path) == "delete"

    def test_second_attempt_succeeds(self, db_path, monkeypatch, caplog):
        attempts = []

        def busy_once(conn):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            return sqlite3.Connection.execute(conn, "PRAGMA journal_mode = WAL")

        patch_wal_pragma(monkeypatch, busy_once)
        with caplog.at_level(logging.WARNING, logger="grading.queue_store"):
            queue_still_works(db_path)
        assert len(attempts) == 2
        assert "WAL" not in caplog.text
        assert journal_mode(db_path) == "wal"


class CountingStore(QueueStore):
    """QueueStore, который считает свои читающие и пишущие транзакции."""

    def __init__(self, path, **kwargs):
        super().__init__(path, **kwargs)
        self.calls = []

    def read(self):
        self.calls.append("read")
        return super().read()

    def transaction(self):
        self.calls.append("write")
        return super().transaction()


READS = {
    "student_view": lambda q, now: q.student_view(COURSE, MONDAY, now, login="alice", group="4431"),
    "day_view": lambda q, now: q.day_view(COURSE, MONDAY, now),
    "history": lambda q, now: q.history(COURSE, MONDAY, MONDAY, MSK, now),
    "defended_labs": lambda q, now: q.defended_labs(COURSE, "alice", now),
    "active_entry": lambda q, now: q.active_entry(COURSE, "alice", now),
    "sync_day": lambda q, now: q.sync_day(COURSE, SETTINGS, [PAIR4], now),
}


class TestReadsTakeNoWriteLock:
    """Опрос очереди и история не ждут писателей и не задерживают их."""

    @pytest.fixture
    def filled(self, queue, session):
        sign(queue, session, "alice")
        sign(queue, session, "bob")
        return session

    @pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
    def test_read_while_another_process_writes(self, db_path, filled, read):
        writer = sqlite3.connect(str(db_path), isolation_level=None)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE entries SET status = 'left' WHERE login = 'alice'")
            impatient = DefenseQueue(QueueStore(db_path, busy_timeout=0.2))
            read(impatient, at(15, 5))
            # Незакоммиченное чтение не видит.
            assert impatient.active_entry(COURSE, "alice", at(15, 5))["status"] == WAITING
            # А запись ждёт писателя, как и раньше.
            assert code_of(lambda: sign(impatient, filled, "carol", now=at(15, 5))) == "STORAGE_UNAVAILABLE"
        finally:
            writer.execute("ROLLBACK")
            writer.close()

    @pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
    def test_read_is_one_read_transaction(self, db_path, filled, read):
        store = CountingStore(db_path)
        read(DefenseQueue(store), at(15, 5))
        assert store.calls == ["read"]

    @pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
    def test_closing_a_past_day_is_a_short_write(self, db_path, filled, read):
        store = CountingStore(db_path)
        read(DefenseQueue(store), at(9, 0, day=TUESDAY))
        assert store.calls == ["read", "write", "read"]
        with QueueStore(db_path).connect() as conn:
            assert {row[0] for row in conn.execute("SELECT status FROM entries")} == {EXPIRED}
        store.calls.clear()
        read(DefenseQueue(store), at(9, 1, day=TUESDAY))
        assert store.calls == ["read"]

    def test_missing_session_is_created_in_a_write(self, db_path):
        store = CountingStore(db_path)
        queue = DefenseQueue(store)
        assert len(queue.sync_day(COURSE, SETTINGS, [PAIR4, PAIR4], NOW)) == 1
        assert store.calls == ["read", "write", "read"]
        store.calls.clear()
        queue.sync_day(COURSE, SETTINGS, [PAIR4], NOW)
        queue.sync_day(COURSE, SETTINGS, None, NOW)
        assert store.calls == ["read", "read"]

    def test_change_is_one_write_transaction(self, db_path, filled):
        store = CountingStore(db_path)
        DefenseQueue(store).call_next(COURSE, filled["id"], at(15, 10))
        assert store.calls == ["write"]


class TestViewWithLessons:
    """Вид с settings и lessons делает то же, что sync_day, в том же чтении: одно обращение на опрос."""

    def test_student_view_creates_sessions(self, db_path):
        store = CountingStore(db_path)
        queue = DefenseQueue(store)
        view = queue.student_view(COURSE, MONDAY, NOW, settings=SETTINGS, lessons=[PAIR4])
        assert [(s["signup"], s["groups"]) for s in view["sessions"]] == [(SIGNUP_OPEN, ["4431"])]
        assert queue.sync_day(COURSE, SETTINGS, [PAIR4], NOW)[0]["id"] == view["sessions"][0]["id"]
        store.calls.clear()
        queue.student_view(COURSE, MONDAY, NOW, settings=SETTINGS, lessons=[PAIR4])
        assert store.calls == ["read"]

    def test_day_view_creates_sessions(self, db_path):
        store = CountingStore(db_path)
        queue = DefenseQueue(store)
        sessions = queue.day_view(COURSE, MONDAY, NOW, {PAIR4.key}, settings=SETTINGS, lessons=[PAIR4])
        assert [s["lesson_key"] for s in sessions] == [PAIR4.key]
        store.calls.clear()
        queue.day_view(COURSE, MONDAY, NOW, {PAIR4.key}, settings=SETTINGS, lessons=[PAIR4])
        assert store.calls == ["read"]

    def test_unknown_schedule_creates_nothing(self, queue):
        assert queue.day_view(COURSE, MONDAY, NOW, settings=SETTINGS, lessons=None) == []


class TestIndexes:
    def test_per_student_queries_use_the_login_index(self, db_path, queue, session):
        # Запросы по одному студенту не должны перебирать всю историю курса.
        statements = []
        real_open = QueueStore._open

        def traced_open(store):
            conn = real_open(store)
            conn.set_trace_callback(statements.append)
            return conn

        sign(queue, session, "alice")
        queue.set_outcome(COURSE, queue.call_next(COURSE, session["id"], NOW)["id"], DEFENDED, at(15, 20))
        sign(queue, session, "bob")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(QueueStore, "_open", traced_open)
            sign(queue, session, "alice", lab="4", now=at(15, 21))
            queue.student_view(COURSE, MONDAY, at(15, 22), login="alice", group="4431")
            queue.defended_labs(COURSE, "alice", at(15, 22))
            queue.active_entry(COURSE, "alice", at(15, 22))

        per_student = {sql for sql in statements if sql.lstrip().startswith("SELECT") and "login = " in sql}
        assert len(per_student) >= 4
        with QueueStore(db_path).connect() as conn:
            for sql in per_student:
                params = [None] * sql.count("?")
                plan = " | ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
                assert "login=?" in plan, (sql, plan)


class TestSnapshots:
    """QueueStore - это SnapshotStore источников расписания."""

    def test_save_and_load(self, db_path):
        store = QueueStore(db_path)
        assert store.load_snapshot("guap:pr=39614") is None
        assert store.snapshot_fetched_at("guap:pr=39614") is None
        fetched = datetime(2026, 11, 9, 12, 0, 0, 123456, tzinfo=MSK)
        store.save_snapshot("guap:pr=39614", fetched, "<html>Пн</html>")
        assert store.load_snapshot("guap:pr=39614") == (fetched, "<html>Пн</html>")
        assert store.snapshot_fetched_at("guap:pr=39614") == fetched
        assert store.snapshot_fetched_at("guap:pr=39614").tzinfo is not None

    def test_newer_copy_wins(self, db_path):
        store = QueueStore(db_path)
        store.save_snapshot("k", at(12, 0), "new")
        # Второй процесс закончил загрузку позже, но начал её раньше.
        QueueStore(db_path).save_snapshot("k", at(11, 0), "old")
        assert store.load_snapshot("k") == (at(12, 0), "new")
        store.save_snapshot("k", at(12, 30), "newest")
        assert store.load_snapshot("k")[1] == "newest"

    def test_survives_restart(self, db_path):
        QueueStore(db_path).save_snapshot("k", at(12, 0), "payload")
        assert QueueStore(db_path).load_snapshot("k") == (at(12, 0), "payload")

    def test_naive_time_is_rejected(self, db_path):
        with pytest.raises(ValueError):
            QueueStore(db_path).save_snapshot("k", datetime(2026, 11, 9, 12, 0), "payload")

    def test_does_not_wait_for_a_writer_long(self, db_path):
        # Сохраняет опрос, скачавший страницу: он не ждёт писателя 10 с.
        store = QueueStore(db_path)
        store.save_snapshot("k", at(12, 0), "old")
        assert store.busy_timeout == queue_store.DEFAULT_BUSY_TIMEOUT_SECONDS
        blocker = sqlite3.connect(str(db_path), isolation_level=None)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            assert code_of(lambda: store.save_snapshot("k", at(12, 30), "new")) == "STORAGE_UNAVAILABLE"
            assert time.monotonic() - started < 3
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        assert store.load_snapshot("k") == (at(12, 0), "old")
        store.save_snapshot("k", at(12, 31), "new")
        assert store.load_snapshot("k") == (at(12, 31), "new")


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def run_together(*actions):
    """
    Start every action at the same moment, each in its own thread.

    Returns a list of ("ok", result) / ("error", QueueError code) in the order
    of the actions; any other exception fails the test.
    """
    barrier = threading.Barrier(len(actions))
    results = [None] * len(actions)

    def worker(index, action):
        barrier.wait(timeout=10)
        try:
            results[index] = ("ok", action())
        except QueueError as e:
            results[index] = ("error", e.code)
        except Exception as e:  # pragma: no cover - shows up in the assert below
            results[index] = ("crash", repr(e))

    threads = [threading.Thread(target=worker, args=(i, a)) for i, a in enumerate(actions)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert all(result is not None and result[0] != "crash" for result in results), results
    return results


class TestConcurrency:
    """Два одновременных запроса к одной очереди - из двух «процессов» (два QueueStore)."""

    ROUNDS = 10

    @pytest.fixture
    def two(self, db_path, session):
        return DefenseQueue(QueueStore(db_path)), DefenseQueue(QueueStore(db_path))

    def test_double_signup_creates_one_entry(self, queue, session, two):
        first, second = two
        for i in range(self.ROUNDS):
            who = f"student{i}"
            results = run_together(
                lambda: sign(first, session, who),
                lambda: sign(second, session, who, lab="4"),
            )
            assert sorted(kind for kind, _ in results) == ["error", "ok"]
            assert [value for kind, value in results if kind == "error"] == ["ALREADY_IN_QUEUE"]
        entries = session_view(queue, session)["entries"]
        assert len(entries) == self.ROUNDS
        assert [e["place"] for e in entries] == list(range(1, self.ROUNDS + 1))

    def test_double_call_next_with_expected_head(self, queue, session, two):
        first, second = two
        alice = sign(queue, session, "alice")
        sign(queue, session, "bob")
        results = run_together(
            lambda: first.call_next(COURSE, session["id"], NOW, expected_entry_id=alice["id"]),
            lambda: second.call_next(COURSE, session["id"], NOW, expected_entry_id=alice["id"]),
        )
        assert [kind for kind, _ in results] == ["ok", "ok"]
        assert {value["id"] for _, value in results} == {alice["id"]}
        assert statuses(queue, session) == {"alice": CALLED, "bob": WAITING}

    def test_double_call_next_without_expected_calls_two(self, queue, session, two):
        first, second = two
        for who in ["alice", "bob", "carol"]:
            sign(queue, session, who)
        results = run_together(
            lambda: first.call_next(COURSE, session["id"], NOW),
            lambda: second.call_next(COURSE, session["id"], NOW),
        )
        assert sorted(value["github"] for _, value in results) == ["Alice", "Bob"]
        assert statuses(queue, session) == {"alice": CALLED, "bob": CALLED, "carol": WAITING}

    def test_skip_and_call_next(self, queue, session, two):
        first, second = two
        alice = sign(queue, session, "alice")
        sign(queue, session, "bob")
        sign(queue, session, "carol")
        skip, call = run_together(
            lambda: first.skip(COURSE, "alice", NOW),
            lambda: second.call_next(COURSE, session["id"], NOW, expected_entry_id=alice["id"]),
        )
        if skip[0] == "ok":
            # Пропуск успел первым: голова сменилась, вызова не было.
            assert call == ("error", "QUEUE_CHANGED")
            assert statuses(queue, session) == {"alice": WAITING, "bob": WAITING, "carol": WAITING}
            assert waiting(queue, session) == ["bob", "alice", "carol"]
        else:
            # Вызов успел первым: вызванный уже не может пропустить вперёд.
            assert skip == ("error", "ALREADY_CALLED")
            assert call[0] == "ok" and call[1]["id"] == alice["id"]
            assert waiting(queue, session) == ["bob", "carol"]

    def test_leave_and_outcome(self, queue, session, two):
        first, second = two
        sign(queue, session, "alice")
        alice = queue.call_next(COURSE, session["id"], NOW)
        leave, outcome = run_together(
            lambda: first.leave(COURSE, "alice", NOW),
            lambda: second.set_outcome(COURSE, alice["id"], DEFENDED, NOW),
        )
        # Выйти вызванный не может ни до отметки исхода, ни после неё.
        assert leave in (("error", "ALREADY_CALLED"), ("error", "NOT_IN_QUEUE"))
        assert outcome[0] == "ok"
        assert statuses(queue, session) == {"alice": DEFENDED}

    def test_leave_and_call_next(self, queue, session, two):
        first, second = two
        alice = sign(queue, session, "alice")
        leave, call = run_together(
            lambda: first.leave(COURSE, "alice", NOW),
            lambda: second.call_next(COURSE, session["id"], NOW, expected_entry_id=alice["id"]),
        )
        if leave[0] == "ok":
            assert call == ("error", "QUEUE_EMPTY")
            assert statuses(queue, session) == {"alice": LEFT}
        else:
            assert leave == ("error", "ALREADY_CALLED")
            assert statuses(queue, session) == {"alice": CALLED}

    def test_sync_day_from_two_processes(self, db_path):
        first, second = DefenseQueue(QueueStore(db_path)), DefenseQueue(QueueStore(db_path))
        other = lesson(start=(18, 40), end=(20, 10), groups=("4434",))
        results = run_together(
            lambda: first.sync_day(COURSE, SETTINGS, [PAIR4, other], NOW),
            lambda: second.sync_day(COURSE, SETTINGS, [PAIR4, other], NOW),
        )
        assert [sorted(s["id"] for s in value) for _, value in results][0] == \
            [sorted(s["id"] for s in value) for _, value in results][1]
        assert len(results[0][1]) == 2

    def test_fresh_file_initialized_in_parallel(self, tmp_path):
        # _initialize() напрямую, мимо блокировки процесса: так стартуют
        # несколько процессов на новом томе.
        path = tmp_path / "fresh" / "queue.sqlite3"
        stores = [QueueStore(path) for _ in range(4)]
        results = run_together(*[store._initialize for store in stores])
        assert [kind for kind, _ in results] == ["ok"] * 4
        with QueueStore(path).connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        created = DefenseQueue(QueueStore(path)).sync_day(COURSE, SETTINGS, [PAIR4], NOW)
        assert len(created) == 1


def test_student_page_reads_only_active_entries(queue, session):
    """The polled student page fetches waiting/called only, not the finished entries of the day."""
    for who in ("alice", "bob", "carol"):
        sign(queue, session, who)
    called = queue.call_next(COURSE, session["id"], NOW, None)
    queue.set_outcome(COURSE, called["id"], DEFENDED, NOW)

    with queue._store.read() as conn:
        every = defense_queue._day_entries(conn, COURSE, MONDAY.isoformat())
        active = defense_queue._day_entries(conn, COURSE, MONDAY.isoformat(), active_only=True)
    assert [r["status"] for r in every[session["id"]]] == [DEFENDED, WAITING, WAITING]
    assert [r["status"] for r in active[session["id"]]] == [WAITING, WAITING]

    view = queue.student_view(COURSE, MONDAY, NOW, login="bob", group="4431")
    [shown] = [s for s in view["sessions"] if s["id"] == session["id"]]
    assert (shown["waiting_count"], shown["called_count"]) == (2, 0)
    assert [e["name"] for e in shown["entries"]] == [NAMES["bob"], NAMES["carol"]]
