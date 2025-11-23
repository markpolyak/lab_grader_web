"""
Unit tests for grading/penalty.py

Tests penalty calculation logic for late submissions.
"""
import pytest
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.penalty import (
    calculate_penalty,
    format_grade_with_penalty,
    PenaltyStrategy,
)


class TestCalculatePenalty:
    """Tests for calculate_penalty function."""

    def test_on_time_no_penalty(self):
        """Submission before deadline has no penalty."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 15, 20, 0)
        assert calculate_penalty(completed, deadline, 10) == 0

    def test_exactly_at_deadline_no_penalty(self):
        """Submission exactly at deadline has no penalty."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 15, 23, 59)
        assert calculate_penalty(completed, deadline, 10) == 0

    def test_one_second_late_one_week_penalty(self):
        """Even 1 second late counts as 1 week."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 15, 23, 59, 1)
        assert calculate_penalty(completed, deadline, 10) == 1

    def test_one_day_late_one_week_penalty(self):
        """1 day late = 1 week penalty."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 16, 10, 0)
        assert calculate_penalty(completed, deadline, 10) == 1

    def test_six_days_late_one_week_penalty(self):
        """6 days late = 1 week penalty."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 21, 10, 0)
        assert calculate_penalty(completed, deadline, 10) == 1

    def test_seven_days_late_one_week_penalty(self):
        """Exactly 7 days late = 1 week penalty."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 22, 23, 59)
        assert calculate_penalty(completed, deadline, 10) == 1

    def test_eight_days_late_two_weeks_penalty(self):
        """8 days late = 2 weeks penalty."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 23, 10, 0)
        assert calculate_penalty(completed, deadline, 10) == 2

    def test_penalty_capped_at_max(self):
        """Penalty should not exceed penalty_max."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 6, 15, 10, 0)  # ~3 months late
        assert calculate_penalty(completed, deadline, 5) == 5

    def test_penalty_max_zero(self):
        """If penalty_max is 0, always return 0."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 6, 15, 10, 0)
        assert calculate_penalty(completed, deadline, 0) == 0


class TestPenaltyStrategyImmediate:
    """Tests for IMMEDIATE_MAX strategy."""

    def test_immediate_max_one_second_late(self):
        """1 second late = max penalty with IMMEDIATE_MAX."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 15, 23, 59, 1)
        result = calculate_penalty(completed, deadline, 10, PenaltyStrategy.IMMEDIATE_MAX)
        assert result == 10

    def test_immediate_max_on_time(self):
        """On time = no penalty even with IMMEDIATE_MAX."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 15, 20, 0)
        result = calculate_penalty(completed, deadline, 10, PenaltyStrategy.IMMEDIATE_MAX)
        assert result == 0


class TestPenaltyStrategyNone:
    """Tests for NONE strategy."""

    def test_none_strategy_always_zero(self):
        """NONE strategy always returns 0 regardless of how late."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 6, 15, 10, 0)  # 3 months late
        result = calculate_penalty(completed, deadline, 10, PenaltyStrategy.NONE)
        assert result == 0


class TestPenaltyStrategyDaily:
    """Tests for DAILY strategy."""

    def test_daily_one_second_late(self):
        """1 second late = 1 day penalty with DAILY."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 15, 23, 59, 1)
        result = calculate_penalty(completed, deadline, 10, PenaltyStrategy.DAILY)
        assert result == 1

    def test_daily_one_day_late(self):
        """24 hours late = 1 day penalty."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 16, 23, 59)
        result = calculate_penalty(completed, deadline, 10, PenaltyStrategy.DAILY)
        assert result == 1

    def test_daily_25_hours_late(self):
        """25 hours late = 2 days penalty (rounds up)."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 17, 0, 59)
        result = calculate_penalty(completed, deadline, 10, PenaltyStrategy.DAILY)
        assert result == 2

    def test_daily_capped_at_max(self):
        """Daily penalty capped at max."""
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 30, 10, 0)  # 15 days late
        result = calculate_penalty(completed, deadline, 5, PenaltyStrategy.DAILY)
        assert result == 5


class TestFormatGradeWithPenalty:
    """Tests for format_grade_with_penalty function."""

    def test_no_penalty(self):
        """No penalty = just the grade."""
        assert format_grade_with_penalty("v", 0) == "v"

    def test_with_penalty(self):
        """With penalty = grade-N format."""
        assert format_grade_with_penalty("v", 3) == "v-3"

    def test_negative_penalty_treated_as_zero(self):
        """Negative penalty treated as no penalty."""
        assert format_grade_with_penalty("v", -1) == "v"

    def test_large_penalty(self):
        """Large penalty formatted correctly."""
        assert format_grade_with_penalty("v", 99) == "v-99"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
