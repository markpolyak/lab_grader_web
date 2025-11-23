"""
Penalty calculation for late lab submissions.

This module contains pure functions for calculating penalty points
based on submission time relative to deadline.
"""
from datetime import datetime
from enum import Enum


class PenaltyStrategy(Enum):
    """Strategy for calculating penalty points."""
    WEEKLY = "weekly"           # 1 point per week late
    IMMEDIATE_MAX = "immediate" # Maximum penalty immediately after deadline
    NONE = "none"               # No penalty regardless of time
    DAILY = "daily"             # 1 point per day late


def calculate_penalty(
    completed_at: datetime,
    deadline: datetime,
    penalty_max: int,
    strategy: PenaltyStrategy = PenaltyStrategy.WEEKLY
) -> int:
    """
    Calculate penalty points for late submission.

    Args:
        completed_at: DateTime when the lab was successfully completed (CI passed)
        deadline: DateTime of the submission deadline
        penalty_max: Maximum penalty points allowed
        strategy: Strategy for calculating penalty (default: WEEKLY)

    Returns:
        Number of penalty points (0 if submitted on time)

    Examples:
        >>> from datetime import datetime
        >>> deadline = datetime(2024, 3, 15, 23, 59)
        >>> on_time = datetime(2024, 3, 15, 20, 0)
        >>> calculate_penalty(on_time, deadline, 10)
        0
        >>> one_day_late = datetime(2024, 3, 16, 10, 0)
        >>> calculate_penalty(one_day_late, deadline, 10)
        1
    """
    if completed_at <= deadline:
        return 0

    if strategy == PenaltyStrategy.NONE:
        return 0

    if strategy == PenaltyStrategy.IMMEDIATE_MAX:
        return penalty_max

    delta = completed_at - deadline

    if strategy == PenaltyStrategy.DAILY:
        # Round up: any part of a day counts as a full day
        days = delta.days + (1 if delta.seconds > 0 else 0)
        return min(days, penalty_max)

    # WEEKLY (default): 1 point per week, rounded up
    weeks = delta.days // 7 + (1 if delta.days % 7 > 0 or delta.seconds > 0 else 0)
    return min(weeks, penalty_max)


def format_grade_with_penalty(base_grade: str, penalty: int) -> str:
    """
    Format grade string with penalty suffix.

    Args:
        base_grade: Base grade symbol (e.g., "v" for success)
        penalty: Number of penalty points

    Returns:
        Grade string, e.g., "v" or "v-3"

    Examples:
        >>> format_grade_with_penalty("v", 0)
        'v'
        >>> format_grade_with_penalty("v", 3)
        'v-3'
    """
    if penalty <= 0:
        return base_grade
    return f"{base_grade}-{penalty}"
