import importlib
import sys
from pathlib import Path
import tempfile
import types
import unittest

PACKAGE = 'queue_ops_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).parents[1] / 'grading/defense_queue')]
sys.modules[PACKAGE] = package
ops = importlib.import_module(PACKAGE + '.operations')
Store = importlib.import_module(PACKAGE + '.storage').QueueStore


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'queue.db')
        self.store.configure_group('c', 'g', title='Предмет')
        self.ids = []
        for i in range(3):
            sid = self.store.student(f'Студент {i}', f'user{i}', now=1)
            cid = self.store.qualify('c', 'g', sid, '1', 1, deadline_at=None, now=2+i)
            self.ids.append(cid)
        self.store.create_session('s', 'c', 'g', 'teacher', starts_at=100, ends_at=200, local_date='2026-11-06', now=1)
        for cid in self.ids[:2]:
            self.store.attendance('s', cid, True, now=10)
        self.course = {'queue': {}, 'timezone': 'UTC'}

    def tearDown(self):
        self.temp.cleanup()

    def view(self, now=110):
        return ops.teacher_view(self.store, 'c', 'g', self.course, deadlines_available=True, now=now, session_id='s')

    def test_whole_roster_with_current_and_next(self):
        before = self.view()
        self.assertEqual(len(before['entries']), 3)
        self.assertEqual(before['stats']['waiting'], 2)
        self.assertEqual(before['entries'][0]['attempt']['joined_at'], 10)
        self.assertEqual(before['entries'][0]['attendance_at'], 10)
        eid = ops.call_next(self.store, 's', now=111, actor='admin')
        second = ops.call_next(self.store, 's', now=112, actor='admin')
        self.assertNotEqual(second, eid)
        view = self.view()
        self.assertEqual(view['current_entry_id'], eid)
        self.assertEqual(view['current_entry_ids'], [eid, second])
        self.assertIsNone(view['next_entry_id'])
        ops.record_result(self.store, second, 'defended', now=113, actor='admin')
        self.assertEqual(self.view()['current_entry_ids'], [eid])

    def test_upgrade_v4_keeps_called_and_allows_another_defense(self):
        self.view()
        first = ops.call_next(self.store, 's', now=111, actor='admin')
        with self.store.transaction() as con:
            con.execute('DROP TABLE queue_commands')
            con.execute('ALTER TABLE queue_reconciliations DROP COLUMN checked_at')
            con.execute("CREATE UNIQUE INDEX one_called_per_teacher ON queue_entries(teacher_key) WHERE status='called'")
            con.execute('DROP TABLE queue_undo')
            con.execute('ALTER TABLE queue_entries DROP COLUMN voided_at')
            con.execute('ALTER TABLE queue_entries DROP COLUMN rounds_managed')
            con.execute('ALTER TABLE queue_settings DROP COLUMN auto_repeat')
            con.execute('ALTER TABLE queue_settings DROP COLUMN undo_seconds')
            con.execute('DROP TABLE session_roster')
            con.execute('DROP TABLE queue_members')
            con.execute('DROP TRIGGER frozen_participation')
            con.execute('ALTER TABLE sessions DROP COLUMN enrollment_mode')
            con.execute("CREATE TRIGGER frozen_participation BEFORE UPDATE ON session_participation WHEN OLD.frozen_at IS NOT NULL AND NEW.will_attend != OLD.will_attend BEGIN SELECT RAISE(ABORT, 'attendance is frozen for this session'); END")
            con.execute('ALTER TABLE queue_settings DROP COLUMN fifo')
            con.execute('ALTER TABLE queue_settings DROP COLUMN opens_before_minutes')
            con.execute('PRAGMA user_version=4')
        self.store = Store(self.store.path)
        second = ops.call_next(self.store, 's', now=112, actor='admin', request_id='second')
        self.assertEqual(ops.call_next(self.store, 's', now=113, actor='admin', request_id='second'), second)
        self.assertEqual(self.view()['current_entry_ids'], [first, second])
        with self.store.connection() as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 10)

    def test_parallel_distinct_calls_never_call_same_student_twice(self):
        from concurrent.futures import ThreadPoolExecutor
        self.view()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: ops.call_next(self.store, 's', now=111, actor='admin', request_id=f'command-{i}'), range(8)))
        called = [entry_id for entry_id in results if entry_id is not None]
        self.assertEqual(len(called), 2)
        self.assertEqual(len(set(called)), 2)
        self.assertEqual(set(self.view()['current_entry_ids']), set(called))
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM queue_events WHERE kind='called'").fetchone()[0], 2)

    def test_failed_retry_tail_and_idempotency(self):
        self.view()
        eid = ops.call_next(self.store, 's', now=111, actor='admin')
        for _ in range(2):
            ops.record_result(self.store, eid, 'failed', now=112, actor='admin')
        view = self.view()
        self.assertEqual(view['stats']['failed'], 1)
        self.assertEqual(view['stats']['waiting'], 2)
        next_id = ops.call_next(self.store, 's', now=113, actor='admin')
        self.assertNotEqual(next_id, eid)
        called = next(row for row in self.view()['attempts'] if row['entry_id'] == next_id)
        self.assertEqual(called['candidate_id'], self.ids[1])

    def test_defense_updates_history_not_sheet(self):
        self.view()
        eid = ops.call_next(self.store, 's', now=111, actor='admin')
        ops.record_result(self.store, eid, 'defended', now=112, actor='admin')
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT status FROM queue_candidates WHERE candidate_id=?', (self.ids[0],)).fetchone()[0], 'defended')

    def test_manual_tail_and_withdrawal(self):
        self.store.attendance('s', self.ids[2], True, now=9)
        self.view()
        with self.store.connection() as con:
            initial = con.execute('SELECT entry_id FROM queue_entries WHERE candidate_id=?', (self.ids[2],)).fetchone()[0]
        ops.withdraw(self.store, initial, now=110, actor='admin')
        eid = ops.add_to_tail(self.store, 's', self.ids[2], now=111, actor='admin')
        view = self.view()
        self.assertEqual(view['entries'][-1]['candidate_id'], self.ids[2])
        ops.withdraw(self.store, eid, now=112, actor='admin')
        self.assertEqual(self.view()['stats']['withdrawn'], 2)

    def test_move_keeps_attendance_and_renews_snapshot(self):
        self.view()
        ops.move_session(self.store, 's', starts_at=300, ends_at=400, local_date='2026-11-07', now=120, actor='admin')
        self.store.attendance('s', self.ids[0], False, now=121)
        view = self.view(now=310)
        self.assertFalse(next(row for row in view['entries'] if row['candidate_id'] == self.ids[0])['will_attend'])
        self.assertEqual(view['stats']['waiting'], 1)

    def test_cancel_keeps_candidates(self):
        self.view()
        ops.cancel_session(self.store, 's', now=120, actor='admin')
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM queue_candidates WHERE status='eligible'").fetchone()[0], 3)
        self.assertEqual(self.view()['stats']['expired'], 2)

    def test_restore_cancelled_future_session_preserves_attendance(self):
        ops.cancel_session(self.store, 's', now=20, actor='admin')
        ops.restore_session(self.store, 's', now=21, actor='admin')
        self.assertEqual(self.view()['stats']['waiting'], 2)
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM queue_events WHERE kind='restored'").fetchone()[0], 1)
        ops.restore_session(self.store, 's', now=22, actor='admin')
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM queue_events WHERE kind='restored'").fetchone()[0], 1)

    def test_restore_current_session_keeps_called_and_waiting(self):
        self.view()
        first = ops.call_next(self.store, 's', now=111, actor='admin')
        ops.cancel_session(self.store, 's', now=112, actor='admin')
        ops.restore_session(self.store, 's', now=113, actor='admin')
        restored = self.view(114)
        self.assertEqual(restored['current_entry_ids'], [first])
        self.assertEqual(restored['stats']['waiting'], 1)
        self.assertEqual(len(restored['attempts']), 2)
        ops.record_result(self.store, first, 'defended', now=115, actor='admin')
        self.assertEqual(self.view(116)['stats']['defended'], 1)

    def test_restore_conflict_does_not_partially_reopen(self):
        self.view()
        ops.cancel_session(self.store, 's', now=112, actor='admin')
        self.store.create_session('other', 'c', 'g', 'other', starts_at=100, ends_at=200, local_date='1970-01-01', now=1)
        self.store.attendance('other', self.ids[1], True, now=10)
        ops.add_to_tail(self.store, 'other', self.ids[1], now=113, actor='admin')
        with self.assertRaises(ops.QueueConflict):
            ops.restore_session(self.store, 's', now=114, actor='admin')
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT enrollment_status FROM sessions WHERE session_id='s'").fetchone()[0], 'cancelled')
            self.assertEqual(con.execute("SELECT count(*) FROM queue_entries WHERE session_id='s' AND status IN ('waiting','called')").fetchone()[0], 0)

    def test_cancel_does_not_make_started_defense_movable(self):
        self.view()
        ops.call_next(self.store, 's', now=111, actor='admin')
        ops.cancel_session(self.store, 's', now=112, actor='admin')
        with self.assertRaises(ops.QueueConflict):
            ops.move_session(self.store, 's', starts_at=300, ends_at=400, local_date='1970-01-01', now=113, actor='admin')

    def test_restore_preserves_early_closed_enrollment(self):
        ops.set_enrollment(self.store, 's', closed=True, now=20, actor='admin')
        ops.cancel_session(self.store, 's', now=21, actor='admin')
        ops.restore_session(self.store, 's', now=22, actor='admin')
        with self.store.connection() as con:
            self.assertEqual(con.execute("SELECT enrollment_status FROM sessions WHERE session_id='s'").fetchone()[0], 'closed')

    def test_restore_cannot_reopen_a_past_day(self):
        ops.cancel_session(self.store, 's', now=112, actor='admin')
        with self.assertRaises(ops.QueueClosed):
            ops.restore_session(self.store, 's', now=86400, actor='admin')

    def test_cannot_cancel_finished_day(self):
        with self.assertRaises(ops.QueueClosed):
            ops.cancel_session(self.store, 's', now=86400, actor='admin')

    def test_settings_persist(self):
        ops.save_settings(self.store, 'c', random=True, deadlines={'1': None}, now=10)
        effective = ops.effective_course(Store(self.store.path), 'c', self.course)
        self.assertTrue(effective['queue']['random'])
        self.assertIsNone(effective['queue']['deadlines']['1'])

    def test_manual_tail_before_start_is_not_duplicated(self):
        ops.add_to_tail(self.store,'s',self.ids[0],now=10,actor='admin')
        view=self.view()
        self.assertEqual(len([r for r in view['attempts'] if r['candidate_id']==self.ids[0]]),1)
        self.assertTrue(next(r for r in view['attempts'] if r['candidate_id']==self.ids[0])['is_repeat'])

    def test_overlapping_class_does_not_duplicate_active_student(self):
        self.view()
        self.store.create_session('overlap','c','g','teacher2',starts_at=105,ends_at=205,local_date='1970-01-01',now=1)
        self.store.attendance('overlap',self.ids[0],True,now=90)
        view=ops.teacher_view(self.store,'c','g',self.course,deadlines_available=True,now=110,session_id='overlap')
        self.assertEqual(view['stats']['waiting'],0)

    def test_settings_can_remove_yaml_deadline_override(self):
        original={'queue':{'deadlines':{'1':'2026-01-01'},'random':True}}
        ops.save_schedule(self.store,'c','g',{'source':'manual'},now=5)
        self.assertTrue(ops.effective_course(self.store,'c',original)['queue']['random'])
        ops.save_settings(self.store,'c',random=False,deadlines={},now=6)
        self.assertEqual(ops.effective_course(self.store,'c',original)['queue']['deadlines'],{})
        self.assertFalse(ops.effective_course(self.store,'c',original)['queue']['random'])

    def test_early_close_reopen_and_freeze(self):
        ops.set_enrollment(self.store,'s',closed=True,now=20,actor='admin')
        with self.assertRaises(ValueError): self.store.attendance('s',self.ids[2],True,now=21)
        ops.set_enrollment(self.store,'s',closed=False,now=22,actor='admin')
        self.store.attendance('s',self.ids[2],True,now=23)
        self.assertEqual(self.view()['stats']['waiting'],3)
        with self.assertRaises(ValueError): ops.set_enrollment(self.store,'s',closed=False,now=100,actor='admin')

    def test_withdraw_and_manual_readd_in_same_second(self):
        self.store.attendance('s', self.ids[2], True, now=9)
        first=ops.add_to_tail(self.store,'s',self.ids[2],now=10,actor='admin')
        ops.withdraw(self.store,first,now=10,actor='admin')
        second=ops.add_to_tail(self.store,'s',self.ids[2],now=10,actor='admin')
        self.assertNotEqual(first,second)
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT status FROM queue_entries WHERE entry_id=?',(second,)).fetchone()[0],'waiting')


if __name__ == '__main__':
    unittest.main()


class QueueRegressionTests(unittest.TestCase):
    setUp = OperationsTests.setUp
    tearDown = OperationsTests.tearDown
    view = OperationsTests.view
    def test_tail_requires_confirmation(self):
        view = self.view()
        row = next(r for r in view['entries'] if r['candidate_id'] == self.ids[2])
        self.assertFalse(row['can_add_tail'])
        with self.assertRaisesRegex(ops.QueueConflict, 'не подтвердил'):
            ops.add_to_tail(self.store,'s',self.ids[2],now=112,actor='admin')

    def test_next_lab_keeps_one_student_row_and_consent(self):
        self.view()
        eid = ops.call_next(self.store,'s',now=111,actor='admin')
        ops.record_result(self.store,eid,'defended',now=112,actor='admin')
        with self.store.connection() as con:
            student = con.execute('SELECT student_id FROM queue_candidates WHERE candidate_id=?',(self.ids[0],)).fetchone()[0]
        cid = self.store.qualify('c','g',student,'2',2,deadline_at=None,now=113)
        ops.promote_next_labs(self.store,'s',now=114,actor='admin')
        for _ in range(3):
            view = self.view(115)
            person = [r for r in view['entries'] if r['github']=='user0']
            self.assertEqual(len(person),1)
            self.assertEqual(person[0]['lab_key'],'2')
            self.assertTrue(person[0]['will_attend'])
            self.assertTrue(person[0]['attempt']['is_repeat'])
            self.assertEqual(person[0]['attempt']['candidate_id'],cid)
            self.assertEqual(person[0]['last_result']['entry_id'],eid)

    def test_correct_failure_to_pass_retracts_retry(self):
        self.view()
        eid = ops.call_next(self.store,'s',now=111,actor='admin')
        ops.record_result(self.store,eid,'failed',now=112,actor='admin')
        ops.correct_result(self.store,eid,'defended',now=113,actor='admin')
        ops.correct_result(self.store,eid,'defended',now=114,actor='admin')
        view=self.view(115)
        self.assertEqual(view['stats']['failed'],0)
        self.assertEqual(view['stats']['defended'],1)
        self.assertEqual(view['stats']['waiting'],1)
        self.assertEqual(len([e for e in view['history'] if e['kind']=='result_corrected']),1)
        self.assertEqual({e['attempt_number'] for e in view['history'] if e['entry_id']==eid},{1})

    def test_correct_pass_to_failure_retracts_next_lab(self):
        self.test_next_lab_keeps_one_student_row_and_consent()
        with self.store.connection() as con:
            eid=con.execute("SELECT entry_id FROM queue_entries WHERE status='defended'").fetchone()[0]
        ops.correct_result(self.store,eid,'failed',now=116,actor='admin')
        view=self.view(117)
        row=next(r for r in view['entries'] if r['github']=='user0')
        self.assertEqual(row['lab_key'],'1')
        self.assertEqual(row['attempt']['status'],'waiting')
        self.assertEqual(view['stats']['defended'],0)

    def test_no_show_and_pass_can_be_corrected_independently_of_sheet(self):
        self.view()
        eid=ops.call_next(self.store,'s',now=111,actor='admin')
        ops.record_result(self.store,eid,'no_show',now=112,actor='admin')
        ops.correct_result(self.store,eid,'defended',now=113,actor='admin')
        with self.store.transaction() as con:
            self.store.event(con,self.ids[0],'sheet_defended',114)
        ops.correct_result(self.store,eid,'failed',now=115,actor='admin')
        with self.store.connection() as con:
            self.assertEqual(con.execute('SELECT status FROM queue_entries WHERE entry_id=?',(eid,)).fetchone()[0],'failed')
            self.assertEqual(con.execute("SELECT count(*) FROM queue_events WHERE kind='sheet_defended'").fetchone()[0],1)

    def test_move_cannot_use_past_or_present(self):
        for start in (99,100):
            with self.assertRaisesRegex(ValueError,'будущее'):
                ops.move_session(self.store,'s',starts_at=start,ends_at=300,local_date='1970-01-01',now=100,actor='admin')

    def test_preview_priority_uses_current_week_then_freezes_at_class(self):
        from datetime import datetime, timezone
        ts=lambda value:int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
        now,start,deadline=map(ts,['2026-11-07T12:00:00','2026-11-09T12:00:00','2026-11-15T23:59:59'])
        self.store.create_session('november','c','g','teacher',starts_at=start,ends_at=start+5400,local_date='2026-11-09',now=now)
        with self.store.transaction() as con:
            con.execute('UPDATE queue_candidates SET deadline_at=?,deadline_available=1',(deadline,))
        view=ops.teacher_view(self.store,'c','g',self.course,deadlines_available=True,now=now,session_id='november')
        self.assertEqual({r['priority'] for r in view['entries']},{1})
        frozen=ops.teacher_view(self.store,'c','g',self.course,deadlines_available=True,now=start,session_id='november')
        self.assertEqual({r['priority'] for r in frozen['entries']},{0})

    def test_correction_cannot_rewrite_a_begun_followup(self):
        self.view()
        eid=ops.call_next(self.store,'s',now=111,actor='admin')
        ops.record_result(self.store,eid,'failed',now=112,actor='admin')
        ops.call_next(self.store,'s',now=113,actor='admin')
        ops.call_next(self.store,'s',now=114,actor='admin')
        with self.assertRaisesRegex(ops.QueueConflict,'следующая'):
            ops.correct_result(self.store,eid,'defended',now=115,actor='admin')

    def test_correction_to_no_show_displays_result_not_cancelled_retry(self):
        self.view()
        eid=ops.call_next(self.store,'s',now=111,actor='admin')
        ops.record_result(self.store,eid,'failed',now=112,actor='admin')
        ops.correct_result(self.store,eid,'no_show',now=113,actor='admin')
        row=next(r for r in self.view(114)['entries'] if r['github']=='user0')
        self.assertEqual(row['attempt']['status'],'no_show')
        self.assertTrue(row['can_add_tail'])

    def test_pre_class_consent_survives_change_of_first_admitted_lab(self):
        with self.store.transaction() as con:
            student=con.execute('SELECT student_id FROM queue_candidates WHERE candidate_id=?',(self.ids[0],)).fetchone()[0]
            con.execute("UPDATE queue_candidates SET status='defended' WHERE candidate_id=?",(self.ids[0],))
        cid=self.store.qualify('c','g',student,'2',2,deadline_at=None,now=30)
        row=next(r for r in self.view(40)['entries'] if r['github']=='user0')
        self.assertEqual(row['candidate_id'],cid)
        self.assertTrue(row['will_attend'])
        self.assertEqual(row['attendance_at'],10)
