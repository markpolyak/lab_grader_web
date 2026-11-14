import unittest
import importlib.util
import sys
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

spec = importlib.util.spec_from_file_location(
    "defense_queue_policy", Path(__file__).parents[1] / "grading/defense_queue/policy.py")
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)
CandidateOrder, QueueMode = policy.CandidateOrder, policy.QueueMode
order_candidates, priority_for, resolve_mode = policy.order_candidates, policy.priority_for, policy.resolve_mode


class DefenseQueuePolicyTests(unittest.TestCase):
    anchor = date(2026, 8, 31)
    start = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)

    def order(self, candidates, mode="priority"):
        return order_candidates(candidates, mode=QueueMode(mode, False),
                                session_start=self.start, week_anchor=self.anchor,
                                timezone="Europe/Moscow")

    def test_deadline_switch(self):
        self.assertEqual(resolve_mode(deadlines_available=False), QueueMode("random", True))
        self.assertEqual(resolve_mode(deadlines_available=True), QueueMode("priority", False))
        self.assertEqual(resolve_mode(deadlines_available=True, random_override=True), QueueMode("random", False))

    def test_floor_and_future(self):
        for weeks, expected in [(-10, -1), (-1, -1), (0, 0), (1, 1), (18, 18)]:
            self.assertEqual(priority_for(self.start + timedelta(weeks=weeks), self.start,
                                         week_anchor=self.anchor, timezone="Europe/Moscow"), expected)

    def test_local_week_boundary(self):
        sunday = datetime(2026, 11, 1, 20, 59, tzinfo=timezone.utc)
        monday = sunday + timedelta(minutes=1)
        self.assertEqual(priority_for(sunday, monday, week_anchor=self.anchor,
                                     timezone="Europe/Moscow"), -1)

    def test_repeat_is_last_even_with_overdue_lab(self):
        first = CandidateOrder(1, 10, self.start + timedelta(weeks=18), .8)
        repeated = CandidateOrder(2, 1, self.start - timedelta(weeks=10), .1, repeat_order=1)
        self.assertEqual(self.order([repeated, first]), [first, repeated])

    def test_priority_and_ties(self):
        future = CandidateOrder(1, 1, self.start + timedelta(weeks=2), .1)
        old = CandidateOrder(2, 3, self.start - timedelta(weeks=10), .2)
        recent = CandidateOrder(3, 2, self.start - timedelta(weeks=1), .3)
        self.assertEqual(self.order([future, old, recent]), [recent, old, future])

    def test_random_is_stable_across_reads(self):
        candidates = [CandidateOrder(1, 1, None, .8), CandidateOrder(2, 2, None, .2)]
        self.assertEqual(self.order(candidates, "random"), list(reversed(candidates)))
        self.assertEqual(self.order(candidates, "random"), self.order(candidates, "random"))
        self.assertEqual([c.candidate_id for c in candidates], [1, 2])

    def test_unavailable_deadline_requires_random_mode(self):
        with self.assertRaises(ValueError):
            self.order([CandidateOrder(1, 1, None, .1, deadline_available=False)])

    def test_explicitly_no_deadline_keeps_priority_mode(self):
        unlimited = CandidateOrder(1, 1, None, .1)
        dated = CandidateOrder(2, 2, self.start + timedelta(weeks=18), .8)
        self.assertEqual(self.order([unlimited, dated]), [dated, unlimited])

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            priority_for(datetime(2026, 11, 2), self.start,
                         week_anchor=self.anchor, timezone="Europe/Moscow")


if __name__ == "__main__":
    unittest.main()
