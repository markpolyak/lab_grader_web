import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location('queue_storage_test', Path(__file__).parents[1] / 'grading/defense_queue/storage.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
QueueStore = module.QueueStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'queue.sqlite3'
        self.store = QueueStore(self.path)
        self.store.configure_group('course', 'group', title='Предмет')
        self.student = self.store.student('Студент', 'login', now=1)
        self.candidate = self.store.qualify('course', 'group', self.student, '1', 1, deadline_at=None, now=2)
        self.store.create_session('lesson', 'course', 'group', 'teacher', starts_at=100, ends_at=200, local_date='2026-11-06', now=1)

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_and_no_duplicate_qualification(self):
        again = QueueStore(self.path)
        cid = again.qualify('course', 'group', self.student, '1', 1, deadline_at=20, now=50)
        self.assertEqual(cid, self.candidate)
        rows = again.roster('lesson')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['qualified_at'], 2)
        with again.connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM queue_events').fetchone()[0], 1)

    def test_default_and_explicit_no_are_distinct(self):
        self.assertEqual(self.store.roster('lesson')[0]['attendance_answered'], 0)
        self.store.attendance('lesson', self.candidate, False, now=10)
        row = self.store.roster('lesson')[0]
        self.assertEqual((row['will_attend'], row['attendance_answered']), (0, 1))

    def test_can_change_both_ways_without_reroll(self):
        self.store.attendance('lesson', self.candidate, True, now=10)
        key = self.store.roster('lesson')[0]['random_key']
        self.store.attendance('lesson', self.candidate, False, now=11)
        self.store.attendance('lesson', self.candidate, True, now=12)
        self.assertEqual(self.store.roster('lesson')[0]['random_key'], key)

    def test_start_boundary_rejects_late_choice_without_timer(self):
        with self.assertRaises(module.QueueClosed):
            self.store.attendance('lesson', self.candidate, True, now=100)
        self.assertEqual(self.store.roster('lesson')[0]['will_attend'], 0)

    def test_retries_do_not_duplicate_events(self):
        for _ in range(3):
            self.store.attendance('lesson', self.candidate, True, now=10)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM queue_events').fetchone()[0], 2)

    def test_other_group_rejected(self):
        self.store.configure_group('course', 'other', title='Предмет')
        self.store.create_session('other', 'course', 'other', 'teacher', starts_at=100, ends_at=200, local_date='2026-11-06', now=1)
        with self.assertRaises(ValueError):
            self.store.attendance('other', self.candidate, True, now=10)

    def test_unavailable_deadline_separate_from_no_deadline(self):
        self.assertEqual(self.store.roster('lesson')[0]['deadline_available'], 1)
        self.store.qualify('course', 'group', self.student, '1', 1, deadline_at=None, deadline_available=False, now=3)
        self.assertEqual(self.store.roster('lesson')[0]['deadline_available'], 0)

    def test_concurrent_qualification_is_idempotent(self):
        student = self.store.student('Другой', 'other', now=1)
        barrier = threading.Barrier(2)
        results = []
        def run():
            barrier.wait()
            results.append(self.store.qualify('course', 'group', student, '1', 1, deadline_at=None, now=5))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    def test_unknown_schema_version_is_not_overwritten(self):
        with self.store.connection() as con:
            con.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            QueueStore(self.path)

    def test_documented_schema_matches_migrations(self):
        root = Path(__file__).parents[1]
        import sqlite3
        snapshot = sqlite3.connect(':memory:')
        try:
            snapshot.executescript((root / 'docs/hackathon/suaios/queue/schema.sql').read_text())
            def objects(con):
                return {tuple(row) for row in con.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
            with self.store.connection() as con:
                self.assertEqual(objects(snapshot), objects(con))
        finally:
            snapshot.close()


if __name__ == '__main__':
    unittest.main()
