import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from queues.domain import DefenseResult, EntryStatus, Queue, QueueEntry, Student
from queues.domain.err import (
    QueueAlreadyExistsError,
    QueueNotFoundError,
    QueueVersionConflictError,
)
from queues.repositories.storage.inmemory import InMemoryQueueRepository


def make_queue(*, course_id="os", group_id="1234") -> Queue:
    start = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    return Queue(
        course_id=course_id,
        group_id=group_id,
        lesson_starts_at=start,
        lesson_ends_at=start + timedelta(minutes=90),
        registration_opens_at=start - timedelta(minutes=15),
        registration_closes_at=start + timedelta(minutes=90),
        service_until=start.replace(hour=23, minute=59),
    )


def make_entry(queue: Queue) -> QueueEntry:
    return QueueEntry(
        queue_id=queue.id,
        student=Student(name="Иван", surname="Иванов", patronymic="", github="student", group_id=queue.group_id),
        lab_id="1",
        enqueued_time=queue.registration_opens_at,
        sequence=1,
    )


class QueueRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = InMemoryQueueRepository()

    async def test_new_queues_do_not_share_entries(self):
        first, second = make_queue(), make_queue()
        first.entries.append(make_entry(first))
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.entries, [])

    async def test_create_and_reads_do_not_expose_internal_state(self):
        original = make_queue()
        original.entries.append(make_entry(original))
        created = await self.repo.create(original)
        original.entries.clear()
        created.entries[0].status = EntryStatus.CALLED

        first_read = await self.repo.get(original.id)
        self.assertEqual(len(first_read.entries), 1)
        self.assertEqual(first_read.entries[0].status, EntryStatus.WAITING)
        first_read.entries.clear()
        self.assertEqual(len((await self.repo.get(original.id)).entries), 1)

    async def test_duplicate_creation_does_not_overwrite_queue(self):
        queue = make_queue()
        await self.repo.create(queue)
        queue.entries.append(make_entry(queue))
        with self.assertRaises(QueueAlreadyExistsError):
            await self.repo.create(queue)
        self.assertEqual((await self.repo.get(queue.id)).entries, [])

    async def test_stale_snapshot_cannot_erase_another_update(self):
        original = await self.repo.create(make_queue())
        first = await self.repo.get(original.id)
        second = await self.repo.get(original.id)
        first.entries.append(make_entry(first))

        results = await asyncio.gather(
            self.repo.save(first, expected_version=0),
            self.repo.save(second, expected_version=0),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, Queue) for r in results), 1)
        self.assertEqual(sum(isinstance(r, QueueVersionConflictError) for r in results), 1)
        stored = await self.repo.get(original.id)
        self.assertEqual(stored.version, 1)
        self.assertEqual(len(stored.entries), 1)

    async def test_saved_result_and_input_remain_independent(self):
        queue = await self.repo.create(make_queue())
        queue.entries.append(make_entry(queue))
        queue.entries[0].status = EntryStatus.FINISHED
        queue.entries[0].result = DefenseResult.DEFENDED
        queue.entries[0].finished_at = queue.lesson_ends_at
        saved = await self.repo.save(queue, expected_version=0)
        self.assertEqual(queue.version, 0)
        self.assertEqual(saved.version, 1)
        saved.entries.clear()
        queue.entries.clear()

        restored = await self.repo.get(queue.id)
        self.assertEqual(restored.entries[0].result, DefenseResult.DEFENDED)
        self.assertEqual(restored.entries[0].finished_at, restored.lesson_ends_at)

    async def test_course_and_group_filters_return_independent_snapshots(self):
        await self.repo.create(make_queue())
        await self.repo.create(make_queue(group_id="5678"))
        await self.repo.create(make_queue(course_id="ml"))
        self.assertEqual(len(await self.repo.list_by_course("os")), 2)
        selected = await self.repo.list_by_course("os", group_id="1234")
        self.assertEqual(len(selected), 1)
        selected[0].entries.append(make_entry(selected[0]))
        self.assertEqual((await self.repo.get(selected[0].id)).entries, [])

    async def test_missing_queue_is_not_created_by_save(self):
        self.assertIsNone(await self.repo.get(uuid4()))
        with self.assertRaises(QueueNotFoundError):
            await self.repo.save(make_queue(), expected_version=0)

    async def test_repository_owns_version_numbers(self):
        queue = make_queue()
        queue.version = 10
        with self.assertRaises(ValueError):
            await self.repo.create(queue)
        queue.version = 0
        await self.repo.create(queue)
        with self.assertRaises(ValueError):
            await self.repo.save(queue, expected_version=1)


if __name__ == "__main__":
    unittest.main()
