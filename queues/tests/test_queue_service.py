from copy import deepcopy
import asyncio
from unittest.mock import AsyncMock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from queues.domain.lesson import Lesson
from queues.domain.queue import Queue
from queues.domain.schedule import Schedule
from queues.domain.student import Student
from queues.usecase.queue_service import QueueService
from queues.usecase.queue_history_service import QueueHistoryService


MONDAY = date(2026, 11, 9)


def courses():
    return {
        "os": {"name": "Операционные системы", "university": "ГУАП"},
        "networks": {"name": "Сети", "university": "ГУАП"},
        "ml": {"name": "Машинное обучение", "university": "ИТМО"},
        "old-os": {"name": "Операционные системы", "university": "ГУАП", "_meta": {"status": "archived"}},
    }


def item(**changes):
    return {
        "subject": "Операционные системы", "kind": "Лабораторное занятие",
        "groups": ["4431"], "room": "23-09", "starts-at": "15:10", "ends-at": "16:40",
    } | changes


def document(days=None):
    return {
        "university": "suai", "timezone": "Europe/Moscow",
        "days": days if days is not None else {"2026-11-09": [item()]},
        "unscheduled": [item()],
    }


def generate(service, doc):
    return service.generate_week(courses(), [doc], week_start=MONDAY)


def test_course_matching_and_week_boundaries():
    schedules = generate(QueueService(), document({
        "2026-11-08": [item()],
        "2026-11-09": [item(), item(subject="Сети"), item(subject="Машинное обучение")],
        "2026-11-15": [item()],
        "2026-11-16": [item()],
    }))
    assert set(schedules) == {"os", "networks"}
    schedule = schedules["os"]
    assert isinstance(schedule, Schedule)
    assert schedule.course_name == "Операционные системы"
    assert schedule.week_start == MONDAY
    assert [lesson.starts_at.date() for lesson in schedule.lessons] == [MONDAY, MONDAY + timedelta(days=6)]
    lesson = schedule.lessons[0]
    assert isinstance(lesson, Lesson)
    assert lesson.starts_at.isoformat() == "2026-11-09T15:10:00+03:00"
    assert lesson.ends_at.isoformat() == "2026-11-09T16:40:00+03:00"
    assert lesson.groups == ("4431",)
    assert len(schedules["networks"].lessons) == 1


def test_each_lesson_owns_one_independent_queue():
    schedule = generate(QueueService(), document({"2026-11-09": [item(), item(groups=["4434"])]}))["os"]
    first, second = schedule.lessons
    assert isinstance(first.queue, Queue)
    assert first.queue is not second.queue
    assert first.queue.closed and second.queue.closed
    first.queue.closed = False
    first.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1")
    assert len(first.queue.entries) == 1
    assert second.queue.entries == []


def test_refresh_preserves_queue_entries_and_manual_state():
    service = QueueService()
    doc = document()
    original = generate(service, doc)["os"].lessons[0]
    original.queue.closed = False
    entry = original.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1")
    original.queue.closed = True
    doc["days"]["2026-11-09"][0]["room"] = "Другая аудитория"
    refreshed = generate(service, doc)["os"].lessons[0]
    assert refreshed is original
    assert refreshed.queue.entries == [entry]
    assert refreshed.queue.closed
    assert refreshed.room == "Другая аудитория"
    generate(service, document({}))
    assert generate(service, doc)["os"].lessons[0] is original


def test_week_rollover_retains_old_queue_and_is_repeatable():
    service = QueueService()
    doc = document({"2026-11-09": [item()], "2026-11-16": [item()]})
    old = generate(service, doc)["os"].lessons[0]
    old.queue.closed = False
    entry = old.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1")
    next_monday = MONDAY + timedelta(days=7)
    new = service.generate_week(courses(), [doc], week_start=next_monday)["os"].lessons[0]
    assert new.id != old.id
    assert new.queue.closed and not new.queue.entries
    assert service._lessons[old.id] is old
    assert old.queue.entries == [entry]
    assert not old.queue.closed
    assert service.generate_week(courses(), [doc], week_start=next_monday)["os"].lessons[0] is new
    assert len(service._lessons) == 2
    # A cancelled lesson is removed from the current view, but retained in memory.
    assert service.generate_week(courses(), [document({})], week_start=next_monday)["os"].lessons == []
    assert service._lessons[new.id] is new
    assert service._lessons[old.id].queue.entries == [entry]


def test_restart_restores_lessons_with_empty_queues():
    doc = document()
    original = generate(QueueService(), doc)["os"].lessons[0]
    original.queue.closed = False
    original.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1")
    restored = generate(QueueService(), doc)["os"].lessons[0]
    assert restored.id == original.id
    assert restored.queue.closed and not restored.queue.entries


def test_parallel_refreshes_do_not_replace_queue_objects():
    from concurrent.futures import ThreadPoolExecutor

    service = QueueService()
    doc = document()
    original = generate(service, doc)["os"].lessons[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: generate(service, deepcopy(doc)), range(20)))
    assert all(result["os"].lessons[0] is original for result in results)
    assert len(service._lessons) == 1


def test_duplicates_merge_but_distinct_kind_and_time_have_their_own_queue():
    service = QueueService()
    doc = document({"2026-11-09": [
        item(), item(), item(kind="Практическое занятие"), item(**{"starts-at": "17:00", "ends-at": "18:30"}),
    ]})
    schedules = service.generate_week(courses(), [doc, deepcopy(doc)], week_start=MONDAY)
    lessons = schedules["os"].lessons
    assert len(lessons) == 3
    assert len({lesson.id for lesson in lessons}) == 3
    assert len({id(lesson.queue) for lesson in lessons}) == 3
    assert schedules["networks"].lessons == []


def test_invalid_refresh_does_not_replace_existing_schedules():
    service = QueueService()
    original = generate(service, document())["os"]
    with pytest.raises(ValueError, match="Окончание"):
        generate(service, document({"2026-11-09": [item(**{"ends-at": "14:00"})]}))
    assert service.schedules["os"] is original
    with pytest.raises(ValueError, match="время"):
        generate(service, document({"2026-11-09": [item(**{"starts-at": None})]}))
    assert service.schedules["os"] is original


def test_other_university_exports_are_ignored():
    doc = document() | {"university": "itmo"}
    assert generate(QueueService(), doc)["os"].lessons == []


def test_default_week_uses_moscow_date_not_utc(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 8, 22, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("queues.usecase.queue_service.datetime", Clock)
    # In Moscow it is already Monday, November 9.
    assert QueueService().generate_week(courses(), [document()])["os"].week_start == MONDAY
    with pytest.raises(ValueError, match="понедельника"):
        QueueService().generate_week(courses(), [], week_start=date(2026, 11, 10))


def test_saved_real_export_seeds_only_three_labs_without_network():
    root = Path(__file__).resolve().parents[2]
    service = QueueService()
    schedule = service.load_saved_week(courses(), root / "schedule", week_start=MONDAY)["os"]
    assert len(schedule.lessons) == 3
    assert sum(lesson.kind == "Лабораторное занятие" for lesson in schedule.lessons) == 3
    assert all(lesson.queue.entries == [] for lesson in schedule.lessons)


def test_only_labs_and_practicals_generate_queues():
    service = QueueService()
    doc = document({"2026-11-09": [
        item(), item(kind="Практическое занятие"), item(kind="ЛР"),
        item(kind="Лекция", **{"starts-at": None}),
        item(kind="Консультация"), item(kind="Экзамен"),
    ]})
    lessons = generate(service, doc)["os"].lessons
    assert {lesson.kind for lesson in lessons} == {"Лабораторное занятие", "Практическое занятие", "ЛР"}
    assert len(service._lessons) == 3


def test_previously_saved_rescheduled_lecture_stays_out_of_public_and_admin_schedule():
    from queues.repositories.mappers import record_from_lesson

    service = QueueService()
    schedule = generate(service, document())["os"]
    lecture = deepcopy(schedule.lessons[0])
    from uuid import uuid4
    lecture.id = uuid4()
    lecture.kind = "Лекция"
    lecture.original_starts_at = lecture.starts_at
    record = record_from_lesson(schedule, lecture)
    service.repository.create(record)
    service._lessons[lecture.id] = lecture
    service._records[lecture.id] = record
    schedule.lessons.append(lecture)
    schedule = generate(service, document())["os"]
    assert all(lesson.kind != "Лекция" for lesson in schedule.lessons)
    assert all(lesson.kind != "Лекция" for lesson in service.admin_lessons("os", MONDAY))
    assert service.get_lesson("os", lecture.id) is None
    assert service.repository.get(lecture.id) is not None


def test_corrupt_yaml_preserves_loaded_queues(tmp_path):
    service = QueueService()
    original = generate(service, document())["os"]
    directory = tmp_path / "suai" / "2026-autumn"
    directory.mkdir(parents=True)
    (directory / "teacher.yaml").write_text("days: [broken")
    with pytest.raises(yaml.YAMLError):
        service.load_saved_week(courses(), tmp_path, week_start=MONDAY)
    assert service.schedules["os"] is original


def test_archived_lesson_is_not_recreated_from_yaml(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 9, 17, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("queues.usecase.queue_service.datetime", Clock)
    service = QueueService()
    history_service = QueueHistoryService(service, AsyncMock())
    doc = document()
    lesson = generate(service, doc)["os"].lessons[0]
    asyncio.run(history_service.save_history(service.schedules["os"].id, lesson.id))
    assert lesson.id not in service._lessons
    assert service.schedules["os"].lessons == []
    assert generate(service, doc)["os"].lessons == []
    assert lesson.id not in service._lessons
    asyncio.run(history_service.save_history(service.schedules["os"].id, lesson.id))
    # Changing the room must not bring back the archived queue either.
    doc["days"]["2026-11-09"][0]["room"] = "new room"
    assert generate(service, doc)["os"].lessons == []


def test_week_change_clears_markers_but_keeps_unarchived_queues():
    service = QueueService()
    history_service = QueueHistoryService(service, AsyncMock())
    doc = document({"2026-11-09": [item(), item(groups=["4432"])],
                    "2026-11-16": [item()]})
    first, retained = generate(service, doc)["os"].lessons
    old_schedule_id = service.schedules["os"].id
    asyncio.run(history_service.save_history(old_schedule_id, first.id))
    assert service._archived_lesson_ids == {first.id}
    next_week = MONDAY + timedelta(days=7)
    # An invalid new week must not clear existing protection.
    broken = document({"2026-11-16": [item(**{"ends-at": "14:00"})]})
    with pytest.raises(ValueError):
        service.generate_week(courses(), [broken], week_start=next_week)
    assert service._archived_lesson_ids == {first.id}
    current = service.generate_week(courses(), [doc], week_start=next_week)["os"].lessons[0]
    assert not service._archived_lesson_ids
    assert service._lessons[retained.id] is retained
    # Archiving an old queue does not accumulate old IDs in the new week's set.
    asyncio.run(history_service.save_history(old_schedule_id, retained.id))
    assert not service._archived_lesson_ids
    asyncio.run(history_service.save_history(service.schedules["os"].id, current.id))
    assert service._archived_lesson_ids == {current.id}
    assert service.generate_week(courses(), [doc], week_start=next_week)["os"].lessons == []
