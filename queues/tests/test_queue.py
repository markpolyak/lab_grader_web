import unittest
from datetime import datetime, timezone
from uuid import UUID

from queues.domain.queue import Queue
from queues.domain.student import Student


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = Queue(closed=False)
        self.ivan = Student("Иван", "Иванов", "Иванович", "ivanov")
        self.anna = Student("Анна", "Петрова", "Сергеевна", "petrova")
        self.oleg = Student("Олег", "Сидоров", "", "sidorov")

    def test_new_queue_is_closed_and_rejects_registration(self):
        queue = Queue()
        self.assertTrue(queue.closed)
        with self.assertRaisesRegex(ValueError, "Очередь закрыта для записи"):
            queue.add_entry(self.ivan, "1")
        self.assertEqual(queue.entries, [])

    def test_registration_can_be_opened_closed_and_reopened(self):
        queue = Queue()
        queue.closed = False
        first = queue.add_entry(self.ivan, "1")
        queue.closed = True
        with self.assertRaisesRegex(ValueError, "Очередь закрыта для записи"):
            queue.add_entry(self.anna, "2")
        self.assertEqual(queue.entries, [first])
        queue.closed = False
        second = queue.add_entry(self.anna, "2")
        self.assertEqual(queue.entries, [first, second])

    def test_closure_does_not_block_search_or_movement(self):
        first = self.queue.add_entry(self.ivan, "1")
        second = self.queue.add_entry(self.anna, "2")
        self.queue.closed = True
        self.assertEqual(self.queue.get_position(github="ivanov"), 1)
        self.assertTrue(self.queue.move_down(github="ivanov"))
        self.assertEqual(self.queue.entries, [second, first])

    def test_add_records_lab_time_and_unique_id_in_fifo_order(self):
        before = datetime.now(timezone.utc)
        first = self.queue.add_entry(self.ivan, "1", "ЛР1")
        second = self.queue.add_entry(self.anna, "2")
        after = datetime.now(timezone.utc)

        self.assertEqual(self.queue.entries, [first, second])
        self.assertIs(first.student, self.ivan)
        self.assertEqual((first.lab_id, first.lab_name), ("1", "ЛР1"))
        self.assertEqual(second.lab_id, "2")
        self.assertIsInstance(first.id, UUID)
        self.assertNotEqual(first.id, second.id)
        for entry in self.queue.entries:
            self.assertLessEqual(before, entry.enqueued_time)
            self.assertLessEqual(entry.enqueued_time, after)
            self.assertEqual(entry.enqueued_time.tzinfo, timezone.utc)

    def test_position_by_full_name_and_case_insensitive_github(self):
        self.queue.add_entry(self.ivan, "1")
        self.queue.add_entry(self.anna, "2")
        self.assertEqual(self.queue.get_position(full_name="  ПЕТРОВА  Анна Сергеевна "), 2)
        self.assertEqual(self.queue.get_position(github=" IVANOV "), 1)

    def test_full_name_without_patronymic_and_with_yo(self):
        self.queue.add_entry(self.oleg, "1")
        self.queue.add_entry(Student("Пётр", "Фёдоров", "", "fedorov"), "1")
        self.assertEqual(self.queue.get_position(full_name="Сидоров Олег"), 1)
        self.assertEqual(self.queue.get_position(full_name="Федоров Петр"), 2)

    def test_move_down_swaps_only_adjacent_entries_and_preserves_metadata(self):
        first = self.queue.add_entry(self.ivan, "1")
        second = self.queue.add_entry(self.anna, "2")
        third = self.queue.add_entry(self.oleg, "3")
        metadata = first.id, first.enqueued_time, first.lab_id

        self.assertTrue(self.queue.move_down(github="IVANOV"))
        self.assertEqual(self.queue.entries, [second, first, third])
        self.assertEqual(self.queue.get_position(github="ivanov"), 2)
        self.assertEqual((first.id, first.enqueued_time, first.lab_id), metadata)
        self.assertTrue(self.queue.move_down(full_name="Иванов Иван Иванович"))
        self.assertEqual(self.queue.entries, [second, third, first])

    def test_move_last_student_returns_false_without_changing_order(self):
        first = self.queue.add_entry(self.ivan, "1")
        second = self.queue.add_entry(self.anna, "2")
        self.assertFalse(self.queue.move_down(github="petrova"))
        self.assertEqual(self.queue.entries, [first, second])

    def test_position_follows_manual_list_reordering(self):
        self.queue.add_entry(self.ivan, "1")
        self.queue.add_entry(self.anna, "2")
        self.queue.entries.reverse()
        self.assertEqual(self.queue.get_position(github="petrova"), 1)
        self.assertEqual(self.queue.get_position(github="ivanov"), 2)

    def test_missing_student_does_not_modify_queue(self):
        for has_entries in (False, True):
            if has_entries:
                self.queue.add_entry(self.ivan, "1")
            before = list(self.queue.entries)
            for method in (self.queue.get_position, self.queue.move_down):
                with self.assertRaises(LookupError):
                    method(github="missing")
            self.assertEqual(self.queue.entries, before)

    def test_namesakes_require_github_for_search_and_movement(self):
        first = self.queue.add_entry(self.ivan, "1")
        second = self.queue.add_entry(Student("Иван", "Иванов", "Иванович", "other"), "1")
        for method in (self.queue.get_position, self.queue.move_down):
            with self.assertRaises(ValueError):
                method(full_name="Иванов Иван Иванович")
        self.assertEqual(self.queue.entries, [first, second])
        self.assertEqual(self.queue.get_position(github="other"), 2)

    def test_duplicate_github_is_rejected_without_adding_entry(self):
        first = self.queue.add_entry(self.ivan, "1")
        duplicate = Student("Другое", "Имя", "", " IVANOV ")
        with self.assertRaises(ValueError):
            self.queue.add_entry(duplicate, "2")
        self.assertEqual(self.queue.entries, [first])

    def test_invalid_identifiers_are_rejected(self):
        self.queue.add_entry(self.ivan, "1")
        for method in (self.queue.get_position, self.queue.move_down):
            for kwargs in ({}, {"github": " "}, {"full_name": " "},
                           {"github": "ivanov", "full_name": "Иванов Иван Иванович"}):
                with self.subTest(method=method.__name__, kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        method(**kwargs)

    def test_invalid_entry_is_not_added(self):
        with self.assertRaises(ValueError):
            self.queue.add_entry(self.ivan, " ")
        with self.assertRaises(ValueError):
            self.queue.add_entry(Student("Иван", "Иванов", "", " "), "1")
        self.assertEqual(self.queue.entries, [])

    def test_queues_have_independent_lists(self):
        self.queue.add_entry(self.ivan, "1")
        self.assertEqual(Queue().entries, [])


if __name__ == "__main__":
    unittest.main()
