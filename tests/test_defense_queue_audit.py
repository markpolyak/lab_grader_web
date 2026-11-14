import importlib
import sys
from pathlib import Path
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

package=types.ModuleType('queue_audit')
package.__path__=[str(Path(__file__).parents[1]/'grading/defense_queue')]
sys.modules['queue_audit']=package
ops=importlib.import_module('queue_audit.operations')
worker=importlib.import_module('queue_audit.worker')
runtime=importlib.import_module('queue_audit.runtime')
sync_group=importlib.import_module('queue_audit.sync').sync_group
Store=importlib.import_module('queue_audit.storage').QueueStore

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'queue.db')
        self.store.configure_group('c','g',title='Предмет')
        self.course={'timezone':'UTC','queue':{}}
        self.sid=self.store.student('Студент','login',now=1)
        self.cid=self.store.qualify('c','g',self.sid,'1',1,deadline_at=None,now=1)
        self.store.create_session('s','c','g','t',starts_at=100,ends_at=200,local_date='1970-01-01',now=1)
        self.store.attendance('s',self.cid,True,now=10)
    def view(self,now=110):
        return ops.teacher_view(self.store,'c','g',self.course,deadlines_available=True,now=now,session_id='s')
    def test_retry_due_not_reset_by_teacher_poll(self):
        self.view()
        def fail(): raise OSError('offline')
        worker.reconcile(self.store,'s',now=800,sync=fail)
        self.view(810)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT due_at FROM queue_reconciliations').fetchone()[0],860)
    def test_history_never_materializes_previously_blocked_student(self):
        self.store.create_session('other','c','g','other',starts_at=90,ends_at=190,local_date='1970-01-01',now=1)
        self.store.attendance('other',self.cid,True,now=10)
        blocking=ops.add_to_tail(self.store,'other',self.cid,now=95,actor='admin')
        self.view()
        worker.reconcile(self.store,'s',now=800,sync=lambda:None)
        ops.withdraw(self.store,blocking,now=801,actor='admin')
        self.assertEqual(self.view(802)['stats']['waiting'],0)
    def test_move_during_sheet_read_keeps_new_lesson_open(self):
        self.view()
        def moved():
            ops.move_session(self.store,'s',starts_at=1200,ends_at=1300,local_date='1970-01-01',now=801,actor='admin')
        self.assertFalse(worker.reconcile(self.store,'s',now=800,sync=moved))
        with self.store.connection() as con:
            self.assertIsNone(con.execute('SELECT done_at FROM queue_reconciliations').fetchone()[0])
            self.assertEqual(con.execute('SELECT enrollment_status FROM sessions').fetchone()[0],'scheduled')
    def test_recovery_promotes_next_lab_exactly_once(self):
        self.view()
        eid=ops.call_next(self.store,'s',now=111,actor='admin')
        ops.record_result(self.store,eid,'defended',now=112,actor='admin')
        next_cid=self.store.qualify('c','g',self.sid,'2',2,deadline_at=None,now=113)
        for _ in range(2): ops.promote_next_labs(self.store,'s',now=114,actor='recovery')
        with self.store.connection() as con:
            rows=con.execute("SELECT candidate_id,is_repeat FROM queue_entries WHERE status='waiting'").fetchall()
        self.assertEqual([tuple(row) for row in rows],[(next_cid,1)])
    def test_partial_import_rolls_back_whole_group(self):
        course={'name':'Предмет','timezone':'UTC','google':{'student-name-column':1},'queue':{},'labs':{'1':{'short-name':'ЛР1'}}}
        grid=[['','','GitHub','2026-11-01'],['№','ФИО','','ЛР1'],['1','Новый','one','v'],['2','Другой','two','v']]
        qualify=self.store.qualify
        def injected(*args,**kwargs):
            if args[2] != self.sid: # reject the second new student only
                with self.store.connection() as con:
                    login=con.execute('SELECT github_login FROM students WHERE student_id=?',(args[2],)).fetchone()[0]
                if login=='two': raise RuntimeError('write failed')
            return qualify(*args,**kwargs)
        with patch.object(self.store,'qualify',side_effect=injected), self.assertRaises(RuntimeError):
            sync_group(self.store,'c','g',course,grid,parse_deadline=lambda v:datetime.fromisoformat(v).replace(tzinfo=timezone.utc),now=20)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM students').fetchone()[0],1)
            self.assertEqual(con.execute("SELECT count(*) FROM queue_candidates WHERE status='eligible'").fetchone()[0],1)

    def test_worker_uses_full_deadline_summary_when_freezing(self):
        with self.store.transaction() as con:
            con.execute("INSERT INTO queue_sync_state VALUES ('c','g',?,1)",('{"deadlines_available":false}',))
        course=dict(self.course,queue={'enabled':True})
        service=worker.QueueWorker(self.store,lambda:[('c',course)],lambda *a,**k:(self.store,{}),lambda _:None)
        service.tick(now=110)
        self.assertEqual(self.view()['mode'],'random')

    def test_frozen_attempt_preserves_name_deadline_and_rank(self):
        frozen=runtime.queue_view(self.store,'c','g',self.course,deadlines_available=True,now=100,session_id='s')
        with self.store.transaction() as con:
            con.execute("UPDATE students SET full_name='Новое имя'")
            con.execute('UPDATE queue_candidates SET deadline_at=9999999')
        view=self.view()
        attempt=view['attempts'][0]
        self.assertEqual(attempt['full_name_snapshot'],frozen['entries'][0]['full_name'])
        self.assertIsNone(attempt['deadline_at'])
        self.assertIsNone(attempt['priority'])
