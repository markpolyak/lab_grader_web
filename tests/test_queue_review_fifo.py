"""Regression scenarios for review D1–D3 and the FIFO enrollment window."""
from tests import test_defense_queue_operations as base
import unittest
ops = base.ops
from importlib import import_module

runtime = import_module('queue_ops_test.runtime')
student_view = import_module('queue_ops_test.student_view').student_view


class ReviewFifoTests(unittest.TestCase):
    setUp = base.OperationsTests.setUp
    tearDown = base.OperationsTests.tearDown
    view = base.OperationsTests.view
    def enable_fifo(self, minutes=15):
        ops.save_settings(self.store, 'c', random=False, fifo=True,
                          opens_before_minutes=minutes, deadlines={}, now=1)

    def test_d2_d3_fifo_uses_signup_time_and_window(self):
        self.enable_fifo(minutes=1)
        view = runtime.queue_view(self.store, 'c', 'g', self.course, deadlines_available=False, now=39)
        assert view['mode'] == 'fifo'
        assert not view['attendance_open']
        with self.assertRaises(ops.QueueClosed):
            self.store.attendance('s', self.ids[2], True, now=39)
        self.store.attendance('s', self.ids[1], False, now=40)
        self.store.attendance('s', self.ids[0], False, now=40)
        self.store.attendance('s', self.ids[1], True, now=41)
        self.store.attendance('s', self.ids[0], True, now=42)
        # Repeating yes must retain the first signup timestamp.
        self.store.attendance('s', self.ids[1], True, now=43)
        view = self.view(110)
        assert [r['attempt']['candidate_id'] for r in view['round_entries']] == [self.ids[1], self.ids[0]]

    def test_fifo_signup_during_class_and_until_end(self):
        self.enable_fifo()
        self.view(110)
        self.store.attendance('s', self.ids[2], True, now=120)
        view = student_view(self.store, 'c', 'g', self.course, deadlines_available=True, now=121)
        assert view['attendance_open']
        assert len(self.view(122)['round_entries']) == 3
        self.store.attendance('s', self.ids[2], False, now=123)
        assert self.view(124)['stats']['waiting'] == 2
        with self.assertRaises(ops.QueueClosed):
            self.store.attendance('s', self.ids[2], True, now=200)

    def test_settings_survive_restart_and_reject_incompatible_modes(self):
        self.enable_fifo(23)
        store = type(self.store)(self.store.path)
        queue = ops.effective_course(store, 'c', self.course)['queue']
        assert queue['fifo'] and queue['opens_before_minutes'] == 23
        with self.assertRaises(ValueError):
            ops.save_settings(store, 'c', random=True, fifo=True, deadlines={}, now=2)

    def test_fifo_first_view_after_start_and_same_second_rejoin(self):
        self.enable_fifo()
        self.store.create_session('late', 'c', 'g', 'teacher', starts_at=300, ends_at=500,
                                  local_date='2026-11-14', now=250)
        auto = runtime.queue_view(self.store, 'c', 'g', self.course, deadlines_available=True, now=310)
        assert auto['session']['session_id'] == 'late'
        assert auto['attendance_open']
        view = ops.teacher_view(self.store, 'c', 'g', self.course, deadlines_available=True, now=310, session_id='late')
        assert view['attendance_open']
        self.store.attendance('late', self.ids[2], True, now=311)
        view = ops.teacher_view(self.store, 'c', 'g', self.course, deadlines_available=True, now=311, session_id='late')
        assert view['stats']['waiting'] == 1
        self.store.attendance('late', self.ids[2], False, now=311)
        self.store.attendance('late', self.ids[2], True, now=311)
        view = ops.teacher_view(self.store, 'c', 'g', self.course, deadlines_available=True, now=311, session_id='late')
        assert view['stats']['waiting'] == 1
        public = student_view(self.store, 'c', 'g', self.course, deadlines_available=True, now=312, session_id='late')
        assert not {'history', 'student_catalog', 'available_candidates', 'attempts'} & public.keys()
