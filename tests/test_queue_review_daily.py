"""Review D1: persisted all-day enrollment when no timetable exists."""
from tests import test_defense_queue_operations as base
from importlib import import_module
import unittest
runtime = import_module('queue_ops_test.runtime')
student_view = import_module('queue_ops_test.student_view').student_view

class ReviewDailyTests(unittest.TestCase):
    setUp = base.OperationsTests.setUp
    tearDown = base.OperationsTests.tearDown

    def test_d1_daily_queue_without_schedule_survives_restart(self):
        self.store = type(self.store)(self.store.path.with_name('daily.db'))
        self.store.configure_group('c', 'g', title='Daily')
        sid = self.store.student('Student', 'daily', now=1)
        cid = self.store.qualify('c', 'g', sid, '1', 1, deadline_at=None, now=2)
        course = dict(self.course, queue={'enabled': True})
        view = runtime.queue_view(self.store, 'c', 'g', course, deadlines_available=True, now=100)
        assert view['session'] is not None
        assert view['attendance_open']
        self.store.attendance(view['session']['session_id'], cid, True, now=101)
        same = type(self.store)(self.store.path)
        restored = runtime.queue_view(same, 'c', 'g', course, deadlines_available=True, now=102)
        assert restored['session']['session_id'] == view['session']['session_id']
        assert any(r['will_attend'] for r in restored['entries'])
        tomorrow = runtime.queue_view(same, 'c', 'g', course, deadlines_available=True, now=86401)
        assert tomorrow['session']['session_id'] != view['session']['session_id']
        assert not any(r['will_attend'] for r in tomorrow['entries'])

    def test_day_queue_student_can_join_and_leave_after_midnight(self):
        self.store = type(self.store)(self.store.path.with_name('day-attendance.db'))
        self.store.configure_group('c', 'g', title='Daily')
        sid = self.store.student('Student', 'daily', now=1)
        cid = self.store.qualify('c', 'g', sid, '1', 1, deadline_at=None, now=2)
        course = dict(self.course, queue={'enabled': True})
        view = student_view(self.store,'c','g',course,deadlines_available=True,now=100)
        session = view['session']['session_id']
        self.assertTrue(view['attendance_open'])
        self.store.attendance(session,cid,True,now=101)
        self.assertEqual(base.ops.teacher_view(self.store,'c','g',course,deadlines_available=True,now=102,session_id=session)['stats']['waiting'],1)
        self.store.attendance(session,cid,False,now=103)
        self.assertEqual(base.ops.teacher_view(self.store,'c','g',course,deadlines_available=True,now=104,session_id=session)['stats']['waiting'],0)
