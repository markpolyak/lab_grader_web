"""
Task ID validation for lab submissions.

This module contains functions for extracting and validating student
task IDs (variant numbers) from GitHub Actions logs.
"""
import re
from dataclasses import dataclass


@dataclass
class TaskIdResult:
    """Result of TASKID extraction from logs."""
    found: int | None
    error: str | None = None


def extract_taskid_from_logs(logs: str) -> TaskIdResult:
    """
    Extract TASKID from GitHub Actions job logs.

    Looks for lines containing "TASKID is <number>" pattern.
    GitHub Actions logs have timestamps at the beginning of each line.

    Args:
        logs: Full text of the job logs

    Returns:
        TaskIdResult with found ID or error message

    Examples:
        >>> result = extract_taskid_from_logs("2024-01-15T10:30:00.000Z TASKID is 15\\n")
        >>> result.found
        15
        >>> result.error is None
        True
    """
    if not logs:
        return TaskIdResult(found=None, error="Логи пусты")

    # Pattern: "TASKID is <number>" anywhere in line
    # GitHub Actions adds timestamp prefix like "2024-01-15T10:30:00.000Z "
    # But TASKID can also appear without timestamp in some cases
    pattern = r'TASKID\s+is\s+(\d+)'

    matches = re.findall(pattern, logs, re.IGNORECASE)

    if len(matches) == 0:
        return TaskIdResult(found=None, error="TASKID не найден в логах")

    # Check all matches are the same (multiple outputs of same TASKID is OK)
    unique_ids = set(int(m) for m in matches)
    if len(unique_ids) > 1:
        return TaskIdResult(
            found=None,
            error=f"Найдено несколько разных TASKID в логах: {sorted(unique_ids)}. Обратитесь к преподавателю."
        )

    return TaskIdResult(found=int(matches[0]))


def calculate_expected_taskid(
    student_order: int,
    taskid_shift: int,
    taskid_max: int
) -> int:
    """
    Calculate expected task ID for a student based on their position.

    Formula: ((student_order + taskid_shift) % taskid_max) or taskid_max
    This ensures result is in range [1, taskid_max].

    Args:
        student_order: Student's row number in spreadsheet (1-indexed position)
        taskid_shift: Shift value from lab config (allows different variants per lab)
        taskid_max: Maximum task ID number (number of variants)

    Returns:
        Expected task ID in range [1, taskid_max]

    Examples:
        >>> calculate_expected_taskid(5, 0, 20)
        5
        >>> calculate_expected_taskid(5, 4, 20)
        9
        >>> calculate_expected_taskid(16, 4, 20)  # (16+4)%20=0 -> 20
        20
    """
    if taskid_max <= 0:
        raise ValueError(f"taskid_max must be positive, got {taskid_max}")

    result = (student_order + taskid_shift) % taskid_max
    return result if result != 0 else taskid_max


def validate_taskid(
    found_taskid: int,
    expected_taskid: int
) -> tuple[bool, str | None]:
    """
    Validate that found TASKID matches expected.

    Args:
        found_taskid: TASKID extracted from logs
        expected_taskid: Calculated expected TASKID

    Returns:
        Tuple of (is_valid, error_message)

    Examples:
        >>> validate_taskid(5, 5)
        (True, None)
        >>> validate_taskid(5, 10)
        (False, 'Неверный вариант: найден 5, ожидается 10')
    """
    if found_taskid == expected_taskid:
        return True, None

    return False, f"Неверный вариант: найден {found_taskid}, ожидается {expected_taskid}"
