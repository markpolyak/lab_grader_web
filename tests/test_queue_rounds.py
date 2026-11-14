from tests import test_defense_queue_operations as base
ops=base.ops
from importlib import import_module
import unittest
rounds=import_module('queue_ops_test.rounds')


class RoundTests(unittest.TestCase):
    setUp=base.OperationsTests.setUp
    tearDown=base.OperationsTests.tearDown
    view=base.OperationsTests.view

    def command(self,action,entry=None,student=None,now=112,key=None):
        return rounds.execute(self.store,'s',action,entry_id=entry,student_id=student,now=now,actor='admin',request_id=key or f'{action}-{now}')

    def first(self):
        view=self.view()
        return view['round_entries'][0]['attempt']['entry_id']

    def test_select_call_cancel_fail_undo_and_retained_row(self):
        first=self.first()
        self.command('call',first)
        self.assertEqual(self.view()['round_entries'][0]['attempt']['status'],'called')
        self.command('uncall',first,now=113)
        self.assertEqual(self.view()['round_entries'][0]['attempt']['status'],'waiting')
        self.command('call',first,now=114)
        undo=self.command('fail',first,now=115)
        rows=self.view(116)['round_entries']
        self.assertEqual([r['attempt']['status'] for r in rows],['failed','waiting','waiting'])
        self.assertTrue(rows[-1]['is_repeat'])
        rounds.undo(self.store,'s',undo['command_id'],now=116,actor='admin')
        self.assertEqual([r['attempt']['status'] for r in self.view(117)['round_entries']],['called','waiting'])

    def test_absent_without_call_arrived_and_undo(self):
        first=self.first()
        undo=self.command('absent',first)
        view=self.view(113)
        self.assertEqual(len(view['round_entries']),1)
        self.assertEqual(len(view['absent_entries']),2)
        rounds.undo(self.store,'s',undo['command_id'],now=113,actor='admin')
        self.assertEqual(len(self.view()['round_entries']),2)
        self.command('absent',first,now=114)
        sid=next(r['student_id'] for r in self.view()['absent_entries'] if r['github']=='user0')
        undo=self.command('arrived',student=sid,now=115)
        self.assertEqual(self.view()['round_entries'][-1]['github'],'user0')
        rounds.undo(self.store,'s',undo['command_id'],now=116,actor='admin')
        self.assertIn('user0',[r['github'] for r in self.view()['absent_entries']])

    def test_override_unconfirmed_and_duplicate_protection(self):
        view=self.view()
        student=view['absent_entries'][0]['student_id']
        self.command('add_student',student=student)
        with self.assertRaisesRegex(ops.QueueConflict,'Нельзя добавить'):
            self.command('add_student',student=student,now=113)
        self.assertEqual(len(self.view()['round_entries']),3)

    def test_preferences_disable_auto_retry_and_undo_expiration(self):
        rounds.save_preferences(self.store,'c',auto_repeat=False,undo_seconds=1,now=110)
        first=self.first()
        self.command('call',first)
        info=self.command('fail',first,now=113)
        self.assertEqual(len(self.view()['round_entries']),2)
        with self.assertRaisesRegex(ops.QueueConflict,'истекло'):
            rounds.undo(self.store,'s',info['command_id'],now=115,actor='admin')

    def test_undo_conflict_and_request_idempotence(self):
        first=self.first()
        info=self.command('absent',first,key='same')
        self.assertEqual(self.command('absent',first,key='same')['command_id'],info['command_id'])
        sid=next(r['student_id'] for r in self.view()['absent_entries'] if r['github']=='user0')
        self.command('arrived',student=sid,now=113)
        with self.assertRaisesRegex(ops.QueueConflict,'изменилось'):
            rounds.undo(self.store,'s',info['command_id'],now=114,actor='admin')

    def test_pass_undo_and_manual_next_lab(self):
        first=self.first()
        self.command('call',first)
        info=self.command('pass',first,now=113)
        with self.store.connection() as con:
            sid=con.execute('SELECT student_id FROM queue_entries WHERE entry_id=?',(first,)).fetchone()[0]
        self.store.qualify('c','g',sid,'2',2,deadline_at=None,now=114)
        ops.promote_next_labs(self.store,'s',now=114,actor='admin')
        self.assertEqual(len(self.view()['round_entries']),2)
        rounds.undo(self.store,'s',info['command_id'],now=114,actor='admin')
        self.assertEqual(self.view()['round_entries'][0]['attempt']['status'],'called')

    def test_manual_row_after_pass_can_repeat_completed_work(self):
        first=self.first()
        self.command('call',first)
        self.command('pass',first,now=113)
        sid=self.view()['round_entries'][0]['student_id']
        self.command('add_student',student=sid,now=114)
        rows=self.view()['round_entries']
        self.assertEqual(rows[0]['attempt']['status'],'defended')
        self.assertEqual(rows[-1]['student_id'],sid)
        self.assertTrue(rows[-1]['is_repeat'])

    def test_repeated_new_command_does_not_replace_meaningful_undo(self):
        first=self.first()
        self.command('call',first)
        original=self.command('fail',first,now=113)
        with self.assertRaisesRegex(ops.QueueConflict,'уже обработана'):
            self.command('fail',first,now=114)
        rounds.undo(self.store,'s',original['command_id'],now=114,actor='admin')
        self.assertEqual(self.view()['round_entries'][0]['attempt']['status'],'called')

    def test_undo_owned_by_teacher_and_denied_at_expiry(self):
        first=self.first()
        undo=self.command('absent',first,now=112)
        with self.assertRaisesRegex(ValueError,'не найдено'):
            rounds.undo(self.store,'s',undo['command_id'],now=113,actor='another-admin')
        with self.assertRaisesRegex(ops.QueueConflict,'истекло'):
            rounds.undo(self.store,'s',undo['command_id'],now=117,actor='admin')

    def test_undo_is_atomic_if_another_action_changed_entry(self):
        first=self.first()
        undo=self.command('call',first,now=112)
        ops.record_result(self.store,first,'defended',now=113,actor='external')
        with self.assertRaises(ops.QueueConflict):
            rounds.undo(self.store,'s',undo['command_id'],now=114,actor='admin')
        self.assertEqual(self.view()['round_entries'][0]['attempt']['status'],'defended')
