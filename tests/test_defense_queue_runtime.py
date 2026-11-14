import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PACKAGE = 'queue_runtime_under_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).parents[1] / 'grading/defense_queue')]
sys.modules[PACKAGE] = package
runtime = importlib.import_module(PACKAGE + '.runtime')
QueueStore = importlib.import_module(PACKAGE + '.storage').QueueStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = QueueStore(Path(self.temp.name) / 'queue.db')
        self.store.configure_group('c', 'g', title='Предмет')
        self.student = self.store.student('Студент', 'login', now=1)
        self.candidate = self.store.qualify('c', 'g', self.student, '1', 1, deadline_at=None, now=2)
        self.store.create_session('one', 'c', 'g', 'teacher', starts_at=100, ends_at=200, local_date='2026-11-06', now=1)
        self.course = {'timezone': 'UTC+3', 'queue': {}}

    def tearDown(self):
        self.temp.cleanup()

    def view(self, now, session_id=None, available=True):
        return runtime.queue_view(self.store, 'c', 'g', self.course, deadlines_available=available,
                                  now=now, session_id=session_id)

    def test_list_remains_visible_without_scheduled_session(self):
        with self.store.transaction() as con:
            con.execute('DELETE FROM sessions')
        result = self.view(50)
        self.assertIsNone(result['session'])
        self.assertEqual(len(result['entries']), 1)
        self.assertFalse(result['attendance_open'])

    def test_no_answer_is_visible_but_not_attending(self):
        result = self.view(50)
        self.assertEqual(len(result['entries']), 1)
        self.assertEqual(result['entries'][0]['state'], 'unconfirmed')
        self.assertIsNone(result['entries'][0]['position'])
        self.assertTrue(result['attendance_open'])

    def test_yes_no_changes_keep_random_key(self):
        self.view(50)
        before = self.store.roster('one')[0]['random_key']
        self.store.attendance('one', self.candidate, True, now=51)
        self.assertEqual(self.view(52)['entries'][0]['position'], 1)
        self.store.attendance('one', self.candidate, False, now=53)
        self.assertEqual(self.view(54)['entries'][0]['state'], 'declined')
        self.assertEqual(self.store.roster('one')[0]['random_key'], before)

    def test_frozen_snapshot_survives_later_qualification(self):
        self.store.attendance('one', self.candidate, True, now=50)
        frozen = self.view(100, 'one')
        new_student = self.store.student('Другой', 'other', now=101)
        self.store.qualify('c', 'g', new_student, '1', 1, deadline_at=None, now=101)
        self.assertEqual(self.view(102, 'one')['entries'], frozen['entries'])
        self.assertFalse(frozen['attendance_open'])
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM queue_events WHERE kind='roster_frozen'").fetchone()[0], 1)

    def test_next_session_opens_as_current_starts(self):
        self.store.create_session('two', 'c', 'g', 'teacher', starts_at=300, ends_at=400, local_date='2026-11-13', now=1)
        self.assertEqual(self.view(100)['session']['session_id'], 'two')
        self.assertEqual(self.view(100)['entries'][0]['state'], 'unconfirmed')

    def test_unavailable_deadlines_lock_random_mode(self):
        result = self.view(50, available=False)
        self.assertEqual((result['mode'], result['mode_locked']), ('random', True))

    def test_explicit_no_deadline_preserves_priority_mode(self):
        result = self.view(50)
        self.assertEqual((result['mode'], result['mode_locked']), ('priority', False))

    def test_sync_cache_avoids_repeated_sheet_reads(self):
        course = dict(self.course, labs={'1': {'short-name': 'ЛР1'}}, queue={'enabled': True, 'deadlines': {'1': None}})
        grid = [['', '', 'GitHub', ''], ['№', 'ФИО', '', 'ЛР1'], ['1', 'Студент', 'login', 'v']]
        from unittest.mock import Mock
        fetch = Mock(return_value=grid)
        runtime._CACHE.clear()
        with patch.object(runtime, 'queue_store', return_value=self.store), patch.object(runtime.time, 'time', return_value=50):
            for _ in range(3):
                runtime.sync_queue('c', 'g', course, fetch_grid=fetch, parse_deadline=lambda x: None)
        self.assertEqual(fetch.call_count, 1)

    def test_snapshot_saved_before_new_sheet_defense(self):
        course = dict(self.course, labs={'1': {'short-name': 'ЛР1'}}, queue={'enabled': True, 'deadlines': {'1': None}})
        grid = [['', '', 'GitHub', ''], ['№', 'ФИО', '', 'ЛР1'], ['1', 'Студент', 'login', 'vv']]
        self.store.attendance('one', self.candidate, True, now=50)
        runtime._CACHE.clear()
        with patch.object(runtime, 'queue_store', return_value=self.store), patch.object(runtime.time, 'time', return_value=101):
            runtime.sync_queue('c', 'g', course, fetch_grid=lambda: grid, parse_deadline=lambda x: None)
        self.assertEqual(len(self.view(102, 'one')['entries']), 1)

    def test_offline_snapshot_survives_process_cache_clear(self):
        from unittest.mock import patch
        course={'name':'Предмет','timezone':'UTC','google':{'student-name-column':1},'queue':{'enabled':True},'labs':{'1':{'short-name':'ЛР1'}}}
        grid=[['','','GitHub','2026-11-01'],['№','ФИО','','ЛР1'],['1','Студент','login','v']]
        parse=lambda value: __import__('datetime').datetime.fromisoformat(value).replace(tzinfo=__import__('datetime').timezone.utc)
        with patch.object(runtime,'queue_store',return_value=self.store), patch.object(runtime.time,'time',return_value=50):
            runtime.sync_queue('c','g',course,fetch_grid=lambda:grid,parse_deadline=parse,force=True)
            runtime._CACHE.clear()
            def fail(): raise OSError('offline')
            _,summary=runtime.sync_queue('c','g',course,fetch_grid=fail,parse_deadline=parse,force=True)
        self.assertTrue(summary['stale'])
        self.assertEqual(summary['eligible'],1)


if __name__ == '__main__':
    unittest.main()
