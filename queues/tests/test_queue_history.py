import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from queues.domain.lesson import Lesson
from queues.domain.schedule import Schedule
from queues.domain.student import Student
from queues.usecase.queue_service import QueueService
from queues.usecase.queue_history_service import QueueHistoryService


def lesson(*, future=False):
    start = datetime.now(timezone.utc) + timedelta(days=1 if future else -1)
    return Lesson(starts_at=start, ends_at=start + timedelta(hours=1),
                  groups=("4431",), kind="ЛР")


def attach(service, *lessons):
    day = lessons[0].starts_at.date()
    schedule = Schedule(course_id="os", course_name="ОС",
                        week_start=day - timedelta(days=day.weekday()), lessons=list(lessons))
    service._schedules[schedule.id] = schedule
    service._lessons.update({item.id: item for item in lessons})
    return schedule.id


def test_save_failure_retains_queue_and_success_removes_it():
    async def check():
        history = AsyncMock()
        history.save.side_effect = TimeoutError("offline")
        service = QueueService()
        history_service = QueueHistoryService(service, history)
        current = lesson()
        current.queue.closed = False
        entry = current.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1")
        schedule_id = attach(service, current)
        with pytest.raises(TimeoutError):
            await history_service.save_history(schedule_id, current.id)
        assert service._lessons[current.id] is current
        assert current.queue.entries == [entry]
        assert not current.queue._archiving
        assert not service._archived_lesson_ids
        history.save.side_effect = None
        history.save.return_value = False  # Already saved after a lost response.
        await history_service.save_history(schedule_id, current.id)
        assert current.id not in service._lessons
    asyncio.run(check())


def test_batch_archives_empty_and_nonempty_queues_and_retries_only_failures():
    async def check():
        history = AsyncMock()
        service = QueueService()
        history_service = QueueHistoryService(service, history)
        empty, occupied, future = lesson(), lesson(), lesson(future=True)
        occupied.queue.closed = False
        occupied.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1")
        attach(service, empty, occupied, future)
        async def save(snapshot, *, course_name):
            if snapshot.id == empty.id:
                raise ConnectionError("offline")
            return True
        history.save.side_effect = save
        errors = await history_service.archive_finished()
        assert set(errors) == {empty.id}
        assert set(service._lessons) == {empty.id, future.id}
        history.save.side_effect = None
        history.save.reset_mock()
        assert not await history_service.archive_finished(retry_only=True)
        history.save.assert_awaited_once()
        assert set(service._lessons) == {future.id}
    asyncio.run(check())


def test_concurrent_save_is_rejected_and_queue_is_frozen():
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        async def save(snapshot, *, course_name):
            entered.set()
            await release.wait()
            return True
        history = AsyncMock()
        history.save.side_effect = save
        service = QueueService()
        history_service = QueueHistoryService(service, history)
        current = lesson()
        current.queue.closed = False
        schedule_id = attach(service, current)
        task = asyncio.create_task(history_service.save_history(schedule_id, current.id))
        await entered.wait()
        try:
            with pytest.raises(RuntimeError, match="уже сохраняется"):
                await history_service.save_history(schedule_id, current.id)
            with pytest.raises(ValueError, match="сохраняется"):
                current.queue.add_entry(Student("Иван", "Иванов", "", "ivan"), "1")
        finally:
            release.set()
            await task
        history.save.assert_awaited_once()
        assert current.id not in service._lessons
    asyncio.run(check())


def test_missing_repository_never_removes_lesson():
    service = QueueService()
    history_service = QueueHistoryService(service)
    current = lesson()
    schedule_id = attach(service, current)
    with pytest.raises(RuntimeError, match="Не настроена"):
        asyncio.run(history_service.save_history(schedule_id, current.id))
    assert service._lessons[current.id] is current


def test_schedule_identity_and_old_week_archive():
    from datetime import date

    async def check():
        history = AsyncMock()
        service = QueueService()
        history_service = QueueHistoryService(service, history)
        courses = {"os": {"name": "ОС", "university": "ГУАП"}}
        item = {"subject": "ОС", "kind": "ЛР", "groups": ["4431"],
                "starts-at": "10:00", "ends-at": "11:00"}
        documents = [{"university": "suai", "days": {
            "2026-11-09": [item], "2026-11-16": [item],
        }}]
        old = service.generate_week(courses, documents, week_start=date(2026, 11, 9))["os"]
        current = old.lessons[0]
        refreshed = service.generate_week(courses, documents, week_start=date(2026, 11, 9))["os"]
        assert refreshed.id == old.id
        assert not hasattr(current, "course_name")
        new = service.generate_week(courses, documents, week_start=date(2026, 11, 16))["os"]
        assert new.id != old.id
        with pytest.raises(ValueError, match="не принадлежит"):
            await history_service.save_history(new.id, current.id)
        history.save.assert_not_awaited()
        await history_service.save_history(old.id, current.id)
        history.save.assert_awaited_once()
        assert history.save.call_args.kwargs == {"course_name": "ОС"}
        assert not service._schedules[old.id].lessons
        assert current.id not in service._lessons
        assert service._schedules[new.id].lessons

    asyncio.run(check())
