"""Две сверки, обслуживание до полуночи и восстановление без открытого браузера."""
from datetime import datetime
from zoneinfo import ZoneInfo
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest

package = types.ModuleType('queue_nightly')
package.__path__ = [str(Path(__file__).parents[1] / 'grading/defense_queue')]
sys.modules[package.__name__] = package
ops = importlib.import_module('queue_nightly.operations')
worker = importlib.import_module('queue_nightly.worker')
Store = importlib.import_module('queue_nightly.storage').QueueStore
service_ends_at = importlib.import_module('queue_nightly.timing').service_ends_at
QueueClosed = importlib.import_module('queue_nightly.storage').QueueClosed


class NightlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'queue.db')
        self.store.configure_group('c','g',title='Subject',timezone='UTC')
        self.course = {'timezone':'UTC','queue':{}}
        self.store.create_session('s','c','g','t',starts_at=100,ends_at=200,local_date='1970-01-01',now=1)
        for i in range(3):
            student = self.store.student(f'Student {i}', f'user{i}', now=1)
            candidate = self.store.qualify('c','g',student,'1',1,deadline_at=None,now=2)
            self.store.attendance('s',candidate,True,now=10)
        self.view()

    def view(self, now=110):
        return ops.teacher_view(self.store,'c','g',self.course,deadlines_available=True,now=now,session_id='s')

    def test_early_read_keeps_waiting_and_called_until_night(self):
        first = ops.call_next(self.store,'s',now=110,actor='teacher')
        self.assertTrue(worker.reconcile(self.store,'s',now=800,sync=lambda:None))
        early = self.view(801)
        self.assertEqual(early['stats']['called'],1)
        self.assertEqual(early['stats']['waiting'],2)
        self.assertTrue(early['service_open'])
        self.assertEqual(early['reconciliation']['due_at'],86400)
        second = ops.call_next(self.store,'s',now=1000,actor='teacher')
        ops.record_result(self.store,second,'defended',now=1001,actor='teacher')
        ops.record_result(self.store,first,'no_show',now=1002,actor='teacher')
        # Поздняя отметка преподавателя в Google Sheets попадёт только в ночное чтение.
        remaining = next(r['candidate_id'] for r in self.view(1003)['attempts'] if r['status']=='waiting')
        def late_sheet():
            with self.store.transaction() as con:
                con.execute("UPDATE queue_candidates SET status='defended' WHERE candidate_id=?",(remaining,))
        self.store = Store(self.store.path)
        self.assertTrue(worker.reconcile(self.store,'s',now=86400,sync=late_sheet))
        final = self.view(86401)
        self.assertEqual(final['stats']['defended'],2)
        self.assertEqual(final['stats']['no_show'],1)
        self.assertFalse(final['service_open'])
        self.assertFalse(worker.reconcile(self.store,'s',now=86402,sync=lambda:self.fail('Repeated final read')))

    def test_night_expires_leftovers_and_poll_does_not_resurrect(self):
        ops.call_next(self.store,'s',now=110,actor='teacher')
        worker.reconcile(self.store,'s',now=800,sync=lambda:None)
        worker.reconcile(self.store,'s',now=86400,sync=lambda:None)
        for now in (86401,86402):
            view = self.view(now)
            self.assertEqual(view['stats']['expired'],3)
            self.assertEqual(view['stats']['waiting'],0)
        with self.assertRaises(QueueClosed): ops.call_next(self.store,'s',now=86401,actor='teacher')

    def test_midnight_blocks_calls_even_if_worker_is_late(self):
        first = ops.call_next(self.store,'s',now=110,actor='teacher')
        self.assertTrue(self.view(86399)['service_open'])
        with self.assertRaises(QueueClosed): ops.call_next(self.store,'s',now=86400,actor='teacher')
        with self.assertRaises(QueueClosed): ops.record_result(self.store,first,'defended',now=86400,actor='teacher')

    def test_final_read_retries_after_restart(self):
        worker.reconcile(self.store,'s',now=800,sync=lambda:None)
        def fail(): raise OSError('offline')
        self.assertFalse(worker.reconcile(self.store,'s',now=86400,sync=fail))
        self.store = Store(self.store.path)
        self.assertFalse(worker.reconcile(self.store,'s',now=86459,sync=lambda:self.fail('Too soon')))
        self.assertTrue(worker.reconcile(self.store,'s',now=86460,sync=lambda:None))
        self.assertEqual(self.view(86461)['reconciliation']['done_at'],86460)

    def test_missing_early_run_still_finishes_at_night(self):
        self.assertTrue(worker.reconcile(self.store,'s',now=86400,sync=lambda:None))
        self.assertEqual(self.view(86401)['stats']['expired'],3)

    def test_late_session_does_not_delay_night_for_ten_minutes(self):
        with self.store.transaction() as con:
            con.execute('UPDATE sessions SET ends_at=86399,expires_at=86399 WHERE session_id=?',('s',))
            con.execute('DELETE FROM queue_reconciliations WHERE session_id=?',('s',))
        self.view(86399)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT due_at FROM queue_reconciliations').fetchone()[0],86400)
        self.assertTrue(worker.reconcile(self.store,'s',now=86400,sync=lambda:None))
        self.assertEqual(self.view(86401)['stats']['expired'],3)

    def test_midnight_uses_session_timezone_and_dst(self):
        for zone, end in [('Europe/Moscow','2026-11-07T16:40:00'),('Europe/Berlin','2026-03-29T16:40:00'),('UTC','2026-11-08T00:00:00')]:
            with self.subTest(zone=zone):
                dt = datetime.fromisoformat(end).replace(tzinfo=ZoneInfo(zone))
                stamp = service_ends_at({'timezone':zone,'ends_at':int(dt.timestamp())})
                local = datetime.fromtimestamp(stamp,ZoneInfo(zone))
                self.assertEqual((local.hour,local.minute,local.second),(0,0,0))
                self.assertEqual(local.day,8 if zone != 'Europe/Berlin' else 30)
