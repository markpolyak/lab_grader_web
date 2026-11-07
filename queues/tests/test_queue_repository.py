import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from queues.domain import DefenseResult, EntryStatus, QueueEntry, QueueEntryMetadata, QueueRecord, Student
from queues.domain.err import (
    QueueAlreadyExistsError,
    QueueNotFoundError,
    QueueVersionConflictError,
)
from queues.repositories.storage.inmemory import InMemoryQueueRepository


def make_queue(*, course_id="os", group_id="1234") -> QueueRecord:
    start = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    return QueueRecord(
        course_id=course_id,
        group_id=group_id,
        lesson_starts_at=start,
        lesson_ends_at=start + timedelta(minutes=90),
        registration_opens_at=start - timedelta(minutes=15),
        registration_closes_at=start + timedelta(minutes=90),
        service_until=start.replace(hour=23, minute=59),
    )


def make_entry(queue: QueueRecord) -> QueueEntry:
    return QueueEntry(
        student=Student(name="Иван", surname="Иванов", patronymic="", github="student"),
        lab_id="1",
        enqueued_time=queue.registration_opens_at,
    )


class QueueRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = InMemoryQueueRepository()

    async def test_new_queues_do_not_share_entries(self):
        first, second = make_queue(), make_queue()
        first.queue.entries.append(make_entry(first))
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.queue.entries, [])

    async def test_create_and_reads_do_not_expose_internal_state(self):
        original = make_queue()
        original.queue.entries.append(make_entry(original))
        original.entry_metadata[original.queue.entries[0].id] = QueueEntryMetadata(queue_id=original.id)
        created = await self.repo.create(original)
        original.queue.entries.clear()
        created.entry_metadata[created.queue.entries[0].id].status = EntryStatus.CALLED

        first_read = await self.repo.get(original.id)
        self.assertEqual(len(first_read.queue.entries), 1)
        self.assertEqual(first_read.entry_metadata[first_read.queue.entries[0].id].status, EntryStatus.WAITING)
        first_read.queue.entries.clear()
        self.assertEqual(len((await self.repo.get(original.id)).queue.entries), 1)

    async def test_duplicate_creation_does_not_overwrite_queue(self):
        queue = make_queue()
        await self.repo.create(queue)
        queue.queue.entries.append(make_entry(queue))
        with self.assertRaises(QueueAlreadyExistsError):
            await self.repo.create(queue)
        self.assertEqual((await self.repo.get(queue.id)).queue.entries, [])

    async def test_stale_snapshot_cannot_erase_another_update(self):
        original = await self.repo.create(make_queue())
        first = await self.repo.get(original.id)
        second = await self.repo.get(original.id)
        first.queue.entries.append(make_entry(first))

        results = await asyncio.gather(
            self.repo.save(first, expected_version=0),
            self.repo.save(second, expected_version=0),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, QueueRecord) for r in results), 1)
        self.assertEqual(sum(isinstance(r, QueueVersionConflictError) for r in results), 1)
        stored = await self.repo.get(original.id)
        self.assertEqual(stored.version, 1)
        self.assertEqual(len(stored.queue.entries), 1)

    async def test_saved_result_and_input_remain_independent(self):
        queue = await self.repo.create(make_queue())
        queue.queue.entries.append(make_entry(queue))
        entry_id = queue.queue.entries[0].id
        queue.entry_metadata[entry_id] = QueueEntryMetadata(
            queue_id=queue.id, status=EntryStatus.FINISHED,
            result=DefenseResult.DEFENDED, finished_at=queue.lesson_ends_at,
        )
        saved = await self.repo.save(queue, expected_version=0)
        self.assertEqual(queue.version, 0)
        self.assertEqual(saved.version, 1)
        saved.queue.entries.clear()
        queue.queue.entries.clear()

        restored = await self.repo.get(queue.id)
        self.assertEqual(restored.entry_metadata[entry_id].result, DefenseResult.DEFENDED)
        self.assertEqual(restored.entry_metadata[entry_id].finished_at, restored.lesson_ends_at)

    async def test_course_and_group_filters_return_independent_snapshots(self):
        await self.repo.create(make_queue())
        await self.repo.create(make_queue(group_id="5678"))
        await self.repo.create(make_queue(course_id="ml"))
        self.assertEqual(len(await self.repo.list_by_course("os")), 2)
        selected = await self.repo.list_by_course("os", group_id="1234")
        self.assertEqual(len(selected), 1)
        selected[0].queue.entries.append(make_entry(selected[0]))
        self.assertEqual((await self.repo.get(selected[0].id)).queue.entries, [])

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

    async def test_colleague_queue_behavior_survives_storage(self):
        record = make_queue()
        record.queue.closed = False
        student = Student("Иван", "Иванов", "", "student")
        entry = record.queue.add_entry(student, "1")
        await self.repo.create(record)
        saved = await self.repo.get(record.id)
        self.assertEqual(saved.queue.get_position(github="student"), 1)
        self.assertEqual(saved.queue.entries[0].id, entry.id)
        with self.assertRaises(ValueError):
            saved.queue.add_entry(student, "2")
        saved.queue.closed = True
        updated = await self.repo.save(saved, expected_version=0)
        self.assertTrue(updated.queue.closed)
        self.assertFalse(record.queue.closed)


if __name__ == "__main__":
    unittest.main()
