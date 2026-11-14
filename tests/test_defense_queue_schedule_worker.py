import importlib
import sys
from pathlib import Path
import tempfile
import types
import unittest
from datetime import datetime, timezone

PACKAGE = 'queue_schedule_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).parents[1] / 'grading/defense_queue')]
sys.modules[PACKAGE] = package
schedule = importlib.import_module(PACKAGE + '.schedule')
worker = importlib.import_module(PACKAGE + '.worker')
ops = importlib.import_module(PACKAGE + '.operations')
Store = importlib.import_module(PACKAGE + '.storage').QueueStore


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'queue.db')
        self.course = {'name':'Предмет', 'timezone':'UTC', 'queue':{}}
    def tearDown(self): self.tmp.cleanup()

    def test_suai_html_and_parity(self):
        html = '''<div class="content"><h4>Понедельник</h4><div class="text-danger mt-3">1 пара</div>
        <div class="mb-3 py-2 d-flex gap-2"><div class="week1">▲</div><div class="fs-6">ЛР</div>
        <div class="lead">Предмет</div><a href="?gr=100">Группа</a><a href="?pr=7">Преподаватель</a></div></div>'''
        lessons = schedule.parse_suai(html)
        self.assertEqual(len(lessons),1)
        config = {'source':'suai','group_id':'100','subject':'Предмет','kind':'ЛР','week_anchor':'2026-09-07','teacher_id':'7'}
        now = int(datetime(2026,9,6,tzinfo=timezone.utc).timestamp())
        result = schedule.expand_suai(lessons,config,now=now,timezone='UTC')
        self.assertEqual(len(result),4)
        self.assertEqual(datetime.fromtimestamp(result[0]['starts_at'],timezone.utc).isoformat(),'2026-09-07T09:30:00+00:00')
        config['teacher_id']='8'
        self.assertEqual(schedule.expand_suai(lessons,config,now=now,timezone='UTC'),[])
        with self.assertRaises(ValueError): schedule.parse_suai('<html>Maintenance</html>')

    def test_import_idempotent_and_manual_changes_win(self):
        rows = [dict(key='a',starts_at=100,ends_at=200)]
        schedule.import_sessions(self.store,'c','g',self.course,rows,now=1)
        with self.store.connection() as con: sid = con.execute('SELECT session_id FROM sessions').fetchone()[0]
        student = self.store.student('Фамилия Имя','login',now=1)
        cid = self.store.qualify('c','g',student,'1',1,deadline_at=None,now=1)
        self.store.attendance(sid,cid,True,now=2)
        ops.move_session(self.store,sid,starts_at=300,ends_at=400,local_date='1970-01-01',now=3,actor='admin')
        schedule.import_sessions(self.store,'c','g',self.course,rows,now=4)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT starts_at FROM sessions').fetchone()[0],300)
            self.assertEqual(con.execute('SELECT count(*) FROM sessions').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT will_attend FROM session_participation').fetchone()[0],1)
        ops.cancel_session(self.store,sid,now=5,actor='admin')
        schedule.import_sessions(self.store,'c','g',self.course,rows,now=6)
        with self.store.connection() as con: self.assertEqual(con.execute('SELECT enrollment_status FROM sessions').fetchone()[0],'cancelled')

    def test_invalid_import_is_atomic(self):
        with self.assertRaises(ValueError):
            schedule.import_sessions(self.store,'c','g',self.course,[dict(key='a',starts_at=100,ends_at=200),dict(key='b',starts_at=300,ends_at=200)],now=1)
        with self.store.connection() as con: self.assertEqual(con.execute('SELECT count(*) FROM sessions').fetchone()[0],0)

    def test_settings_survive_restart(self):
        self.store.configure_group('c','g',title='Предмет')
        ops.save_schedule(self.store,'c','g',{'source':'manual','university':'Другой вуз'},now=1)
        reopened = Store(self.store.path)
        self.assertEqual(ops.effective_course(reopened,'c',self.course)['queue']['schedule']['g']['university'],'Другой вуз')

    def test_reconciliation_retry_restart_and_idempotency(self):
        self.store.configure_group('c','g',title='Предмет')
        self.store.create_session('s','c','g','t',starts_at=100,ends_at=200,local_date='1970-01-01',now=1)
        ids=[]
        for i in range(2):
            sid=self.store.student(f'Студент {i}',f'u{i}',now=1)
            cid=self.store.qualify('c','g',sid,'1',1,deadline_at=None,now=1);ids.append(cid)
            self.store.attendance('s',cid,True,now=2)
        ops.teacher_view(self.store,'c','g',self.course,deadlines_available=True,now=100,session_id='s')
        calls=[]
        def fail(): raise RuntimeError('offline')
        self.assertFalse(worker.reconcile(self.store,'s',now=799,sync=lambda:calls.append(1)))
        self.assertEqual(calls,[])
        self.assertFalse(worker.reconcile(self.store,'s',now=800,sync=fail))
        def success():
            calls.append(1)
            with self.store.transaction() as con: con.execute("UPDATE queue_candidates SET status='defended' WHERE candidate_id=?",(ids[0],))
        reopened=Store(self.store.path)
        self.assertTrue(worker.reconcile(reopened,'s',now=860,sync=success))
        self.assertFalse(worker.reconcile(reopened,'s',now=900,sync=success))
        self.assertEqual(calls,[1])
        with reopened.connection() as con:
            self.assertEqual([r[0] for r in con.execute('SELECT status FROM queue_entries ORDER BY entry_id')],['defended','waiting'])
            self.assertEqual(con.execute('SELECT attempts FROM queue_reconciliations').fetchone()[0],2)
            self.assertIsNone(con.execute('SELECT done_at FROM queue_reconciliations').fetchone()[0])
            self.assertEqual(con.execute('SELECT checked_at FROM queue_reconciliations').fetchone()[0],860)
        self.assertTrue(worker.reconcile(reopened,'s',now=86400,sync=success))
        with reopened.connection() as con:
            self.assertEqual([r[0] for r in con.execute('SELECT status FROM queue_entries ORDER BY entry_id')],['defended','expired'])
            self.assertEqual(con.execute('SELECT done_at FROM queue_reconciliations').fetchone()[0],86400)

    def test_worker_freezes_and_reconciles_without_open_browser(self):
        self.store.configure_group('c','g',title='Предмет')
        student=self.store.student('Студент','login',now=1)
        cid=self.store.qualify('c','g',student,'1',1,deadline_at=None,now=1)
        self.store.create_session('s','c','g','t',starts_at=100,ends_at=200,local_date='1970-01-01',now=1)
        self.store.attendance('s',cid,True,now=2)
        calls=[]
        def sync(*args,**kwargs):
            calls.append(kwargs)
            if kwargs.get('force'):
                with self.store.transaction() as con: con.execute("UPDATE queue_candidates SET status='defended' WHERE candidate_id=?",(cid,))
            return self.store, {'deadlines_available':True}
        course=dict(self.course,queue={'enabled':True})
        service=worker.QueueWorker(self.store,lambda:[('c',course)],sync,lambda url:None)
        service.tick(now=100)
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM queue_entries WHERE status='waiting'").fetchone()[0],1)
        worker.QueueWorker(Store(self.store.path),lambda:[('c',course)],sync,lambda url:None).tick(now=800)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT status FROM queue_entries').fetchone()[0],'defended')
            self.assertIsNone(con.execute('SELECT done_at FROM queue_reconciliations').fetchone()[0])
            self.assertEqual(con.execute('SELECT checked_at FROM queue_reconciliations').fetchone()[0],800)
        self.assertTrue(any(c.get('force') for c in calls))
        worker.QueueWorker(Store(self.store.path),lambda:[('c',course)],sync,lambda url:None).tick(now=86400)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT done_at FROM queue_reconciliations').fetchone()[0],86400)


class ScheduleExtensionTests(unittest.TestCase):
    setUp = ScheduleTests.setUp
    tearDown = ScheduleTests.tearDown
    def test_new_university_module_without_core_change(self):
        module = types.ModuleType(PACKAGE + '.schedule_sources.test_university')
        seen = []
        module.validate = lambda config: seen.append(config['campus'])
        module.load = lambda config, **kwargs: [dict(key='other:1', starts_at=100, ends_at=200)]
        sys.modules[module.__name__] = module
        try:
            self.course['queue']['schedule'] = {'g': {'source': 'test_university', 'campus': 'north'}}
            schedule.refresh_schedule(self.store, 'c', 'g', self.course, fetch=lambda url: self.fail('No network expected'), now=1)
            self.assertEqual(seen, ['north'])
            with self.store.connection() as con:
                self.assertEqual(con.execute('SELECT source_key FROM sessions').fetchone()[0], 'other:1')
            for source in ('os', '../os', 'a.b', '__init__'):
                with self.assertRaises(ValueError): schedule.validate_schedule({'source': source})
        finally:
            del sys.modules[module.__name__]

    def configured(self):
        self.course['queue'] = {'schedule': {group: {'source': 'manual', 'lessons': [
            dict(key='base', starts_at=100, ends_at=200)]} for group in ('g', 'g2')},
            'schedule_overrides': [
                dict(key='cancel-day', kind='cancel', groups=['g', 'g2'], date='1970-01-01'),
                dict(key='extra', kind='add', groups=['g', 'g2'], starts_at=300, ends_at=400)]}
        return self.course

    def test_overrides_multiple_groups_and_repeated_refresh(self):
        self.configured()
        for now in (1, 2, 3):
            for group in ('g', 'g2'):
                schedule.refresh_schedule(self.store, 'c', group, self.course, fetch=None, now=now)
        with self.store.connection() as con:
            rows = con.execute('SELECT group_id,source_key,enrollment_status FROM sessions ORDER BY group_id,source_key').fetchall()
            self.assertEqual([tuple(r) for r in rows], [('g','base','cancelled'),('g','override:extra','scheduled'),('g2','base','cancelled'),('g2','override:extra','scheduled')])
            self.assertEqual(con.execute("SELECT count(*) FROM queue_events WHERE kind='config_schedule_override'").fetchone()[0], 2)

    def test_overrides_work_when_university_is_down(self):
        self.configured()
        schedule.refresh_schedule(self.store,'c','g',self.course,fetch=None,now=1)
        self.course['queue']['schedule']['g'] = {'source':'suai','group_id':'100','subject':'Subject','week_anchor':'2026-09-07'}
        self.course['queue']['schedule_overrides'].append(dict(key='extra2',kind='add',groups=['g'],starts_at=500,ends_at=600))
        def down(url): raise OSError('offline')
        with self.assertRaises(OSError):
            schedule.refresh_schedule(self.store,'c','g',self.course,fetch=down,now=2)
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM sessions WHERE source_kind='extra'").fetchone()[0],2)

    def test_bad_overrides_do_not_partially_import(self):
        self.configured()
        self.course['queue']['schedule_overrides'].append(dict(key='bad',kind='add',groups=['g'],starts_at=300,ends_at=100))
        with self.assertRaises(ValueError):
            schedule.refresh_schedule(self.store,'c','g',self.course,fetch=None,now=1)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sessions').fetchone()[0],0)

    def test_config_move_keeps_identity_and_manual_move_wins(self):
        self.course['queue']['schedule'] = {'g':{'source':'manual','lessons':[dict(key='base',starts_at=100,ends_at=200)]}}
        schedule.refresh_schedule(self.store,'c','g',self.course,fetch=None,now=1)
        with self.store.connection() as con: sid=con.execute('SELECT session_id FROM sessions').fetchone()[0]
        student=self.store.student('Student','student',now=1)
        cid=self.store.qualify('c','g',student,'1',1,deadline_at=None,now=1)
        self.store.attendance(sid,cid,True,now=2)
        self.course['queue']['schedule_overrides']=[dict(key='move',kind='move',source_key='base',groups=['g'],starts_at=300,ends_at=400)]
        for now in (3,4): schedule.refresh_schedule(self.store,'c','g',self.course,fetch=None,now=now)
        with self.store.connection() as con:
            row=con.execute('SELECT * FROM sessions').fetchone()
            self.assertEqual((row['session_id'],row['starts_at'],row['schedule_revision']),(sid,300,2))
            self.assertEqual(con.execute('SELECT will_attend FROM session_participation').fetchone()[0],1)
        ops.move_session(self.store,sid,starts_at=500,ends_at=600,local_date='1970-01-01',now=5,actor='admin')
        self.course['queue']['schedule_overrides'][0].update(starts_at=700,ends_at=800)
        schedule.refresh_schedule(self.store,'c','g',self.course,fetch=None,now=6)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT starts_at FROM sessions').fetchone()[0],500)

if __name__ == '__main__': unittest.main()


def test_suai_card_without_gap_class_and_duplicate_subgroups():
    html='''<div class="content"><h4>Понедельник</h4><div class="text-danger mt-3">6 пара</div>
    <div class="mb-3 py-2 d-flex"><div class="fs-6">Лабораторное занятие</div>
    <div class="lead">Операционные системы</div><a href="?gr=7116">4434</a><a href="?pr=39614">Преподаватель</a></div></div>'''
    parsed=schedule.parse_suai(html)
    assert len(parsed)==1 and parsed[0]['slot']==6
    config={'source':'suai','group_id':'7116','subject':'Операционные системы','kind':'Лабораторное занятие','teacher_id':'39614','week_anchor':'2026-08-31'}
    now=int(datetime(2026,11,7,tzinfo=timezone.utc).timestamp())
    once=schedule.expand_suai(parsed,config,now=now,timezone='Europe/Moscow')
    twice=schedule.expand_suai(parsed*2,config,now=now,timezone='Europe/Moscow')
    assert twice==once and once
    assert datetime.fromtimestamp(once[0]['starts_at'],timezone.utc).hour==15  # 18:40 МСК
