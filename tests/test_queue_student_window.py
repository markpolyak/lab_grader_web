from tests import test_defense_queue_operations as base
from importlib import import_module
import unittest
ops=base.ops
module=import_module('queue_ops_test.student_view')


class StudentWindowTests(unittest.TestCase):
    setUp=base.OperationsTests.setUp
    tearDown=base.OperationsTests.tearDown

    def view(self,now,**extra):
        return module.student_view(self.store,'c','g',self.course,deadlines_available=True,now=now,**extra)

    def test_before_class_and_exact_reopening_boundary(self):
        self.store.create_session('next','c','g','teacher',starts_at=1000,ends_at=2000,local_date='1970-01-01',now=1)
        self.assertTrue(self.view(99)['attendance_open'])
        for now in (100,199,200,799):
            view=self.view(now)
            self.assertTrue(view['read_only'])
            self.assertFalse(view['attendance_open'])
            self.assertEqual(view['session']['session_id'],'s')
            self.assertEqual(view['enrollment_opens_at'],800)
        after=self.view(800)
        self.assertFalse(after['read_only'])
        self.assertEqual(after['session']['session_id'],'next')
        self.assertTrue(after['attendance_open'])

    def test_public_queue_matches_teacher_but_has_no_private_action_data(self):
        teacher=ops.teacher_view(self.store,'c','g',self.course,deadlines_available=True,now=110,session_id='s')
        eid=teacher['round_entries'][0]['attempt']['entry_id']
        rounds=import_module('queue_ops_test.rounds')
        rounds.execute(self.store,'s','call',now=111,actor='admin',request_id='call',entry_id=eid)
        rounds.execute(self.store,'s','fail',now=112,actor='admin',request_id='fail',entry_id=eid)
        view=self.view(113)
        self.assertEqual([r['attempt']['status'] for r in view['round_entries']],['failed','waiting','waiting'])
        self.assertNotIn('student_catalog',view)
        self.assertNotIn('history',view)
        self.assertNotIn('entry_id',view['round_entries'][0]['attempt'])
        self.assertNotIn('student_id',view['round_entries'][0])

    def test_explicit_future_view_does_not_open_attendance_during_current_class(self):
        self.store.create_session('next','c','g','teacher',starts_at=1000,ends_at=2000,local_date='1970-01-01',now=1)
        self.assertFalse(self.view(110,session_id='next')['attendance_open'])
