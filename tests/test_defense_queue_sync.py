import importlib.util
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
PACKAGE = 'queue_sync_under_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'grading/defense_queue')]
sys.modules[PACKAGE] = package
from importlib import import_module
QueueStore = import_module(PACKAGE + '.storage').QueueStore
sync_group = import_module(PACKAGE + '.sync').sync_group


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = QueueStore(Path(self.temp.name) / 'queue.db')
        self.course = {'name': 'Предмет', 'timezone': 'UTC+3', 'google': {'student-name-column': 1},
                       'labs': {'1': {'short-name': 'ЛР1'}, '2': {'short-name': 'ЛР2'}}}
        self.grid = [['', '', 'GitHub', '2026-11-01', '2026-11-08'],
                     ['№', 'ФИО', '', 'ЛР1', 'ЛР2'], ['1', 'Студент', 'login', 'v', 'v']]

    def tearDown(self):
        self.temp.cleanup()

    def sync(self):
        def parse(value):
            try:
                return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return sync_group(self.store, 'course', 'group', self.course, self.grid, parse_deadline=parse, now=1)

    def rows(self):
        with self.store.connection() as con:
            return [dict(row) for row in con.execute("SELECT * FROM queue_candidates WHERE status='eligible'")]

    def test_import_first_only_and_idempotent(self):
        self.sync()
        self.sync()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['lab_key'], '1')
        self.assertEqual(self.grid[2][3], 'v')

    def test_pending_earlier_lab_does_not_block_later(self):
        self.grid[2][3] = 'x'
        self.sync()
        self.assertEqual(self.rows()[0]['lab_key'], '2')

    def test_defense_advances_and_is_not_duplicated(self):
        self.sync()
        self.grid[2][3] = 'vv-3'
        self.sync()
        self.sync()
        self.assertEqual(self.rows()[0]['lab_key'], '2')
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM queue_events WHERE kind='sheet_defended'").fetchone()[0], 1)

    def test_explicitly_no_deadline_differs_from_missing_cell(self):
        self.grid[0][3] = ''
        self.sync()
        self.assertEqual(self.rows()[0]['deadline_available'], 0)
        self.course['queue'] = {'deadlines': {'1': None}}
        self.sync()
        self.assertEqual(self.rows()[0]['deadline_available'], 1)
        self.assertIsNone(self.rows()[0]['deadline_at'])

    def test_duplicate_login_rejected_before_import(self):
        self.grid.append(['2', 'Другой', 'LOGIN', 'v', ''])
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(self.rows(), [])

    def test_initial_defended_marks_remain_in_history(self):
        self.grid[2][3]='vv'
        self.sync()
        self.grid[2][3]=''
        self.sync()
        self.assertEqual(self.rows()[0]['lab_key'],'2')

    def test_removed_student_loses_current_eligibility(self):
        self.sync()
        self.grid.pop()
        self.sync()
        self.assertEqual(self.rows(),[])


if __name__ == '__main__':
    unittest.main()
