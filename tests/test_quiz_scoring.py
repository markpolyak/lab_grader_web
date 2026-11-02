"""Quiz scoring (docs/QUIZ_PLAN.md §4)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.quiz import Question, format_points, question_points, student_score
from grading.score import format_score


def q(weight):
    return Question(label="Q", subtitle=None, weight=weight, variants=())


class TestQuestionPoints:
    def test_weights(self):
        assert question_points([q(25), q(75)], 2) == [0.5, 1.5]

    def test_equal_when_some_weights_missing(self):
        assert question_points([q(25), q(None), q(50)], 3) == [1.0, 1.0, 1.0]

    def test_equal_when_no_weights(self):
        assert question_points([q(None)] * 4, 2) == [0.5] * 4


class TestStudentScore:
    def test_sum_of_correct(self):
        assert student_score([0.5, 1.5], [1]) == 1.5
        assert student_score([0.5, 1.5], []) == 0

    def test_finished_early_unasked_questions_give_zero(self):
        points = question_points([q(None)] * 4, 2)
        # Only the first two were asked and answered correctly; max stays 2
        assert student_score(points, [0, 1]) == 1.0

    @pytest.mark.parametrize("value,text", [
        (1.5, "1.5"), (2.0, "2"), (0, "0"), (2 / 3, "0.67"), (1 / 3 + 1 / 3 + 1 / 3, "1"), (0.125, "0.13"),
    ])
    def test_rounding(self, value, text):
        assert format_points(value) == text

    def test_decimal_comma(self):
        assert format_score(format_points(1.5), ",") == "1,5"
        assert format_score(format_points(2), ",") == "2"
