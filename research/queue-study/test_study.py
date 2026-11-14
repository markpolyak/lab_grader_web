import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from study import Scenario, paired_interval, priority, simulate
sys.path.insert(0, str(Path(__file__).resolve().parent / "source"))
from common import SimState
from models import MODELS


def scalar_reference(config, policy, runs, seed):
    rng = np.random.default_rng(seed)
    shape = (runs, config.students)
    p = np.clip(rng.normal(config.probability_mean, .15, shape), .05, .95)
    current = np.zeros(shape, dtype=int)
    scores = np.zeros(shape, dtype=int)
    deadlines = np.arange(1, config.labs + 1) * config.deadline_spacing - 1
    for week in range(1, config.weeks + 1):
        present = rng.random(shape) < config.attendance
        ties = rng.random(shape)
        rolls = rng.random(shape)
        for run in range(runs):
            wanted = []
            for student in range(config.students):
                lab = current[run, student]
                if lab == config.labs or not present[run, student]:
                    continue
                if config.release_before_deadline is not None and week < max(1, deadlines[lab] - config.release_before_deadline):
                    continue
                delta = deadlines[lab] - week
                key = 0 if policy == "random" else max(0 if policy == "clamp0" else -1, delta)
                wanted.append((key, ties[run, student], student))
            cap = 0 if week == config.cancelled_week else config.capacity
            if week == config.extra_capacity_week:
                cap *= 2
            for _, _, student in sorted(wanted)[:cap]:
                lab = current[run, student]
                chance = p[run, student]
                if config.difficulty:
                    chance = np.clip(chance * (.8 + .4 * lab / max(1, config.labs - 1)), 0, 1)
                if rolls[run, student] < chance:
                    scores[run, student] += max(0, 5 - config.penalty * max(0, week - deadlines[lab]))
                    current[run, student] += 1
    return scores


class StudyTests(unittest.TestCase):
    def model_state(self):
        return SimState(current_lab=np.arange(30) % 10,
                        completion_week=np.full((30, 10), -1),
                        pass_chances=np.full(30, .5), deadlines=np.arange(1, 11) * 3 - 1,
                        week=10, n_labs=10, rng=np.random.default_rng(42))

    def test_registered_models_and_standalone_zero_class(self):
        self.assertEqual(set(MODELS), {"random", "priority", "priority_clamped", "priority_clamped_zero", "most_passed", "target_three", "surname_k", "odd_number"})
        self.assertIsNot(MODELS["priority_clamped"], MODELS["priority_clamped_zero"])
        for name, floor in (("priority_clamped", -1), ("priority_clamped_zero", 0)):
            state = self.model_state()
            order = MODELS[name]().order_queue(state)
            keys = np.maximum(floor, state.deadlines[state.current_lab] - state.week)
            self.assertTrue(np.all(np.diff(keys[order]) >= 0))

    def test_arbitrary_rules_and_valid_orders(self):
        for name, cls in MODELS.items():
            with self.subTest(name=name):
                state = self.model_state()
                order = cls().order_queue(state)
                self.assertEqual(len(order), len(np.unique(order)))
                self.assertTrue(np.isin(order, state.active()).all())
        order = MODELS["surname_k"]().order_queue(self.model_state())
        self.assertEqual(set(order[:3]), {0, 10, 20})
        order = MODELS["odd_number"]().order_queue(self.model_state())
        self.assertTrue(np.all((order[:15] + 1) % 2 == 1))

    def test_target_three_stops_at_threshold_and_prefers_closer(self):
        state = self.model_state()
        state.current_lab[:] = 0
        state.current_lab[0:2] = [5, 4]
        state.completion_week[0, :5] = 1
        state.completion_week[1, :4] = 1
        order = MODELS["target_three"]().order_queue(state)
        self.assertNotIn(0, order)
        self.assertEqual(order[0], 1)

    def test_floor_boundaries(self):
        delta = np.array([-18, -2, -1, 0, 1, 18])
        np.testing.assert_array_equal(priority(delta, "clamp_minus1"), [-1, -1, -1, 0, 1, 18])
        np.testing.assert_array_equal(priority(delta, "clamp0"), [0, 0, 0, 0, 1, 18])

    def test_vectorized_matches_independent_scalar(self):
        base = Scenario(students=7, labs=4, weeks=15, capacity=3)
        configs = [base, replace(base, attendance=.6), replace(base, cancelled_week=4, extra_capacity_week=3), replace(base, release_before_deadline=2), replace(base, penalty=2, difficulty=True)]
        for config in configs:
            for policy in ("random", "clamp0", "clamp_minus1"):
                with self.subTest(config=config, policy=policy):
                    scores, _ = simulate(config, policy, 8, 141)
                    np.testing.assert_array_equal(scores, scalar_reference(config, policy, 8, 141))

    def test_no_competition_gives_exact_same_results(self):
        config = Scenario(capacity=30)
        random, _ = simulate(config, "random", 100, 42)
        for policy in ("clamp0", "clamp_minus1"):
            scores, metrics = simulate(config, policy, 100, 42)
            np.testing.assert_array_equal(random, scores)
            self.assertEqual(int(metrics["queue_rejects"].sum()), 0)

    def test_reproducible_and_bounded(self):
        a, _ = simulate(Scenario(), "clamp_minus1", 40, 42)
        b, _ = simulate(Scenario(), "clamp_minus1", 40, 42)
        np.testing.assert_array_equal(a, b)
        self.assertTrue(np.all((0 <= a) & (a <= 50)))

    def test_absent_students_are_not_queue_rejects(self):
        scores, metrics = simulate(Scenario(attendance=0), "clamp_minus1", 2, 42)
        self.assertFalse(scores.any())
        self.assertFalse(metrics["wanted"].any())
        self.assertFalse(metrics["queue_rejects"].any())

    def test_paired_interval(self):
        self.assertEqual(paired_interval(np.ones(3), np.zeros(3)), (1., 1., 1.))


if __name__ == "__main__":
    unittest.main()
