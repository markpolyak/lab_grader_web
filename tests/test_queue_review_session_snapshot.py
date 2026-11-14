"""D5: no worksheet reads while a class is running; local outcomes stay editable."""
from tests import test_defense_queue_operations as base
from importlib import import_module
from unittest.mock import Mock, patch
import unittest

runtime = import_module('queue_ops_test.runtime')
rounds = import_module('queue_ops_test.rounds')


class SessionSnapshotTests(unittest.TestCase):
    setUp = base.OperationsTests.setUp
    tearDown = base.OperationsTests.tearDown
    view = base.OperationsTests.view

    def queue_course(self):
        return dict(self.course, queue={'enabled': True, 'deadlines': {'1': None}},
                    google={'student-name-column': 1}, labs={'1': {'short-name': 'ЛР1'}})

    def test_active_pair_never_fetches_sheet_even_forced_after_restart(self):
        course = self.queue_course()
        grid = [['', '', 'GitHub', ''], ['№', 'ФИО', '', 'ЛР1'], ['1', 'Студент 0', 'user0', 'v']]
        with patch.object(runtime, 'queue_store', return_value=self.store), patch.object(runtime.time, 'time', return_value=50):
            runtime.sync_queue('c','g',course,fetch_grid=lambda:grid,parse_deadline=lambda _:None,force=True)
        runtime._CACHE.clear()
        fetch = Mock(return_value=[*grid[:2], ['1', 'Студент 0', 'user0', 'vv']])
        with patch.object(runtime, 'queue_store', return_value=type(self.store)(self.store.path)), patch.object(runtime.time, 'time', return_value=101):
            for force in (False, True):
                _, summary = runtime.sync_queue('c','g',course,fetch_grid=fetch,parse_deadline=lambda _:None,force=force)
        fetch.assert_not_called()
        self.assertTrue(summary['source_frozen'])
        self.assertFalse(summary.get('stale',False))
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT status FROM queue_candidates WHERE candidate_id=?',(self.ids[0],)).fetchone()[0], 'eligible')
        with patch.object(runtime, 'queue_store', return_value=self.store), patch.object(runtime.time, 'time', return_value=200):
            runtime.sync_queue('c','g',course,fetch_grid=fetch,parse_deadline=lambda _:None,force=True)
        fetch.assert_called_once()

    def test_old_sheet_confirmation_does_not_block_local_correction(self):
        self.view(110)
        entry = base.ops.call_next(self.store,'s',now=111,actor='admin')
        base.ops.record_result(self.store,entry,'defended',now=112,actor='admin')
        with self.store.transaction() as con:
            self.store.event(con,self.ids[0],'sheet_defended',113)
        base.ops.correct_result(self.store,entry,'failed',now=114,actor='admin')
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT status FROM queue_entries WHERE entry_id=?',(entry,)).fetchone()[0], 'failed')
            self.assertEqual(con.execute("SELECT count(*) FROM queue_events WHERE kind='sheet_defended'").fetchone()[0],1)

    def test_old_sheet_confirmation_does_not_block_local_undo(self):
        entry = self.view(110)['round_entries'][0]['attempt']['entry_id']
        rounds.execute(self.store,'s','call',entry_id=entry,now=111,actor='admin',request_id='call')
        command = rounds.execute(self.store,'s','pass',entry_id=entry,now=112,actor='admin',request_id='pass')
        with self.store.transaction() as con:
            self.store.event(con,self.ids[0],'sheet_defended',113)
        rounds.undo(self.store,'s',command['command_id'],now=113,actor='admin')
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT status FROM queue_entries WHERE entry_id=?',(entry,)).fetchone()[0], 'called')

    def test_day_session_keeps_refreshing_admissions(self):
        course = self.queue_course()
        with self.store.transaction() as con:
            con.execute("UPDATE sessions SET source_kind='day',enrollment_mode='daily' WHERE session_id='s'")
        grid = [['', '', 'GitHub', ''], ['№', 'ФИО', '', 'ЛР1'], ['1', 'Студент 0', 'user0', 'v']]
        fetch = Mock(return_value=grid)
        runtime._CACHE.clear()
        with patch.object(runtime,'queue_store',return_value=self.store), patch.object(runtime.time,'time',return_value=101):
            _, summary = runtime.sync_queue('c','g',course,fetch_grid=fetch,parse_deadline=lambda _:None,force=True)
        fetch.assert_called_once()
        self.assertFalse(summary.get('source_frozen',False))

    def test_worker_defers_reconciliation_when_another_pair_freezes_source(self):
        worker = import_module('queue_ops_test.worker')
        self.view(110)
        self.store.create_session('overlap','c','g','teacher',starts_at=700,ends_at=1000,local_date='2026-11-06',now=1)
        sync = Mock(return_value=(self.store, {'source_frozen': True, 'deadlines_available': True}))
        service = worker.QueueWorker(self.store,lambda:[('c',self.queue_course())],sync,lambda _:None)
        with patch.object(worker,'refresh_schedule'):
            service.tick(now=800)
        with self.store.connection() as con:
            task = con.execute("SELECT * FROM queue_reconciliations WHERE session_id='s'").fetchone()
            self.assertIsNone(task['checked_at'])
            self.assertIsNone(task['claimed_at'])
            self.assertEqual(task['due_at'],860)
            self.assertIsNotNone(task['error'])
