"""
Unit tests for grading/taskid.py

Tests TASKID extraction from logs and expected ID calculation.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.taskid import (
    extract_taskid_from_logs,
    calculate_expected_taskid,
    validate_taskid,
    TaskIdResult,
)


class TestExtractTaskIdFromLogs:
    """Tests for extract_taskid_from_logs function."""

    def test_simple_taskid(self):
        """Extract TASKID from simple log line."""
        logs = "2024-01-15T10:30:00.000Z TASKID is 15\n"
        result = extract_taskid_from_logs(logs)
        assert result.found == 15
        assert result.error is None

    def test_taskid_with_other_output(self):
        """Extract TASKID from logs with other content."""
        logs = """2024-01-15T10:30:00.000Z Starting tests...
2024-01-15T10:30:01.000Z TASKID is 7
2024-01-15T10:30:02.000Z Running test case 1...
2024-01-15T10:30:03.000Z Test passed"""
        result = extract_taskid_from_logs(logs)
        assert result.found == 7
        assert result.error is None

    def test_taskid_without_timestamp_not_matched(self):
        """TASKID without GitHub timestamp prefix is NOT matched."""
        logs = "TASKID is 42\nSome other output"
        result = extract_taskid_from_logs(logs)
        assert result.found is None
        assert "не найден" in result.error

    def test_taskid_case_insensitive(self):
        """TASKID matching is case insensitive."""
        logs = "2024-01-15T10:30:00.000Z taskid is 5\n"
        result = extract_taskid_from_logs(logs)
        assert result.found == 5

        logs = "2024-01-15T10:30:00.000Z TaskId Is 10\n"
        result = extract_taskid_from_logs(logs)
        assert result.found == 10

    def test_taskid_in_middle_of_line_ignored(self):
        """TASKID in middle of line should NOT be matched."""
        logs = """2024-01-15T10:30:00.000Z Some text TASKID is 99 more text
2024-01-15T10:30:01.000Z Another line with TASKID is 88 embedded"""
        result = extract_taskid_from_logs(logs)
        # Neither should match - both are in the middle of lines
        assert result.found is None
        assert "не найден" in result.error

    def test_taskid_at_start_matches_middle_ignored(self):
        """Only TASKID at start of line (after timestamp) matches."""
        logs = """2024-01-15T10:30:00.000Z Some text TASKID is 99 more text
2024-01-15T10:30:01.000Z TASKID is 5
2024-01-15T10:30:02.000Z Another TASKID is 88 embedded"""
        result = extract_taskid_from_logs(logs)
        # Only the second line matches (TASKID at start after timestamp)
        assert result.found == 5
        assert result.error is None

    def test_taskid_not_found(self):
        """Error when no TASKID in logs."""
        logs = "2024-01-15T10:30:00.000Z No taskid here\nJust some output"
        result = extract_taskid_from_logs(logs)
        assert result.found is None
        assert "не найден" in result.error

    def test_empty_logs(self):
        """Error when logs are empty."""
        result = extract_taskid_from_logs("")
        assert result.found is None
        assert "пуст" in result.error

    def test_multiple_same_taskid(self):
        """Multiple occurrences of same TASKID is OK."""
        logs = """2024-01-15T10:30:00.000Z TASKID is 5
2024-01-15T10:30:01.000Z TASKID is 5
2024-01-15T10:30:02.000Z TASKID is 5"""
        result = extract_taskid_from_logs(logs)
        assert result.found == 5
        assert result.error is None

    def test_multiple_different_taskids_error(self):
        """Error when different TASKIDs found."""
        logs = """2024-01-15T10:30:00.000Z TASKID is 5
2024-01-15T10:30:01.000Z TASKID is 7"""
        result = extract_taskid_from_logs(logs)
        assert result.found is None
        assert "несколько" in result.error

    def test_taskid_with_extra_whitespace(self):
        """TASKID with extra whitespace still matched."""
        logs = "2024-01-15T10:30:00.000Z TASKID  is   15\n"
        result = extract_taskid_from_logs(logs)
        assert result.found == 15


class TestCalculateExpectedTaskId:
    """Tests for calculate_expected_taskid function."""

    def test_simple_calculation(self):
        """Basic calculation without wrap-around."""
        # student 5, shift 0, max 20 -> 5
        assert calculate_expected_taskid(5, 0, 20) == 5

    def test_with_shift(self):
        """Calculation with shift value."""
        # student 5, shift 4, max 20 -> 9
        assert calculate_expected_taskid(5, 4, 20) == 9

    def test_modulo_wrap(self):
        """Wrap around when sum exceeds max."""
        # student 18, shift 4, max 20 -> (18+4)%20 = 2
        assert calculate_expected_taskid(18, 4, 20) == 2

    def test_modulo_zero_becomes_max(self):
        """Result 0 becomes max (not 0)."""
        # student 16, shift 4, max 20 -> (16+4)%20 = 0 -> 20
        assert calculate_expected_taskid(16, 4, 20) == 20

    def test_student_order_1(self):
        """First student in list."""
        # student 1, shift 0, max 20 -> 1
        assert calculate_expected_taskid(1, 0, 20) == 1

    def test_large_shift(self):
        """Shift larger than max."""
        # student 1, shift 25, max 20 -> (1+25)%20 = 6
        assert calculate_expected_taskid(1, 25, 20) == 6

    def test_negative_shift(self):
        """Negative shift value (allowed by modulo)."""
        # student 5, shift -2, max 20 -> (5-2)%20 = 3
        assert calculate_expected_taskid(5, -2, 20) == 3

    def test_invalid_taskid_max_zero(self):
        """Error when taskid_max is 0."""
        with pytest.raises(ValueError):
            calculate_expected_taskid(5, 0, 0)

    def test_invalid_taskid_max_negative(self):
        """Error when taskid_max is negative."""
        with pytest.raises(ValueError):
            calculate_expected_taskid(5, 0, -1)


class TestValidateTaskId:
    """Tests for validate_taskid function."""

    def test_matching_ids(self):
        """Valid when IDs match."""
        is_valid, error = validate_taskid(5, 5)
        assert is_valid is True
        assert error is None

    def test_mismatched_ids(self):
        """Invalid when IDs don't match."""
        is_valid, error = validate_taskid(5, 10)
        assert is_valid is False
        assert "5" in error
        assert "10" in error
        assert "вариант" in error.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
