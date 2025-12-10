"""
Unit tests for grading/sheets_client.py

Tests Google Sheets helper functions.
"""
import pytest
from unittest.mock import MagicMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.sheets_client import (
    find_student_row,
    find_lab_column_by_name,
    calculate_lab_column,
    can_overwrite_cell,
    format_cell_protection_message,
    prepare_grade_update,
    format_forbidden_files_note,
    StudentLocation,
    LabColumn,
    GradeUpdate,
)


class TestFindStudentRow:
    """Tests for find_student_row function."""

    def test_find_existing_student(self):
        """Find student in list."""
        github_values = ["user1", "user2", "user3"]
        row = find_student_row(github_values, "user2")
        assert row == 4  # start_row (3) + index (1)

    def test_first_student(self):
        """Find first student in list."""
        github_values = ["user1", "user2", "user3"]
        row = find_student_row(github_values, "user1")
        assert row == 3

    def test_student_not_found(self):
        """Student not in list returns None."""
        github_values = ["user1", "user2"]
        row = find_student_row(github_values, "unknown")
        assert row is None

    def test_case_insensitive(self):
        """Search is case-insensitive."""
        github_values = ["UserOne", "UserTwo"]
        row = find_student_row(github_values, "userone")
        assert row == 3

        row = find_student_row(github_values, "USERTWO")
        assert row == 4

    def test_empty_list(self):
        """Empty list returns None."""
        row = find_student_row([], "user1")
        assert row is None

    def test_custom_start_row(self):
        """Custom start row offset."""
        github_values = ["user1", "user2"]
        row = find_student_row(github_values, "user2", start_row=5)
        assert row == 6  # 5 + 1

    def test_skip_empty_values(self):
        """Skip empty values in list."""
        github_values = ["", "user1", "", "user2"]
        row = find_student_row(github_values, "user2")
        assert row == 6  # 3 + 3


class TestFindLabColumnByName:
    """Tests for find_lab_column_by_name function."""

    def test_find_existing_column(self):
        """Find column by short name."""
        mock_worksheet = MagicMock()
        mock_cell = MagicMock()
        mock_cell.col = 5
        mock_worksheet.find.return_value = mock_cell

        col = find_lab_column_by_name(mock_worksheet, "ЛР1")
        assert col == 5
        mock_worksheet.find.assert_called_once_with("ЛР1")

    def test_column_not_found(self):
        """Column not found returns None."""
        mock_worksheet = MagicMock()
        mock_worksheet.find.return_value = None

        col = find_lab_column_by_name(mock_worksheet, "ЛР99")
        assert col is None

    def test_find_raises_exception(self):
        """Exception during find returns None."""
        mock_worksheet = MagicMock()
        mock_worksheet.find.side_effect = Exception("Search error")

        col = find_lab_column_by_name(mock_worksheet, "ЛР1")
        assert col is None


class TestCalculateLabColumn:
    """Tests for calculate_lab_column function."""

    def test_first_lab(self):
        """Calculate column for first lab."""
        # offset 3 (0-based, meaning column D) + lab 1 = column 4
        col = calculate_lab_column(1, 3)
        assert col == 4

    def test_second_lab(self):
        """Calculate column for second lab."""
        col = calculate_lab_column(2, 3)
        assert col == 5

    def test_offset_zero(self):
        """Offset zero means labs start at column A."""
        col = calculate_lab_column(1, 0)
        assert col == 1

    def test_large_lab_number(self):
        """Large lab number."""
        col = calculate_lab_column(10, 5)
        assert col == 15


class TestCanOverwriteCell:
    """Tests for can_overwrite_cell function."""

    def test_empty_cell(self):
        """Empty cell can be overwritten."""
        assert can_overwrite_cell("") is True

    def test_whitespace_only(self):
        """Whitespace-only cell can be overwritten."""
        assert can_overwrite_cell("   ") is True

    def test_x_grade(self):
        """'x' (failed) can be overwritten."""
        assert can_overwrite_cell("x") is True

    def test_question_mark(self):
        """'?' can be overwritten."""
        assert can_overwrite_cell("?") is True

    def test_question_mark_prefix(self):
        """Cells starting with '?' can be overwritten."""
        assert can_overwrite_cell("?! Wrong TASKID") is True
        assert can_overwrite_cell("?pending") is True

    def test_v_grade_protected(self):
        """'v' (success) is protected."""
        assert can_overwrite_cell("v") is False

    def test_v_with_penalty_protected(self):
        """'v-N' (success with penalty) is protected."""
        assert can_overwrite_cell("v-3") is False
        assert can_overwrite_cell("v-10") is False

    def test_other_values_protected(self):
        """Other non-empty values are protected."""
        assert can_overwrite_cell("some text") is False
        assert can_overwrite_cell("123") is False


class TestFormatCellProtectionMessage:
    """Tests for format_cell_protection_message function."""

    def test_message_contains_value(self):
        """Message includes current value."""
        msg = format_cell_protection_message("v-3")
        assert "v-3" in msg
        assert "преподавателю" in msg

    def test_message_format(self):
        """Message has proper format."""
        msg = format_cell_protection_message("v")
        assert "оценку" in msg.lower() or "Ячейка" in msg


class TestPrepareGradeUpdate:
    """Tests for prepare_grade_update function."""

    def test_update_empty_cell(self):
        """Update empty cell succeeds."""
        result = prepare_grade_update("", "v")
        assert result.success is True
        assert result.new_value == "v"
        assert result.previous_value == ""

    def test_update_x_to_v(self):
        """Update from 'x' to 'v' succeeds."""
        result = prepare_grade_update("x", "v")
        assert result.success is True
        assert result.new_value == "v"

    def test_update_question_to_v(self):
        """Update from '?' to 'v' succeeds."""
        result = prepare_grade_update("?! Wrong TASKID", "v")
        assert result.success is True

    def test_update_v_blocked(self):
        """Update from 'v' is blocked."""
        result = prepare_grade_update("v", "v-2")
        assert result.success is False
        assert result.new_value is None
        assert "v" in result.message

    def test_update_v_penalty_blocked(self):
        """Update from 'v-N' is blocked."""
        result = prepare_grade_update("v-3", "v")
        assert result.success is False


class TestFormatForbiddenFilesNote:
    """Tests for format_forbidden_files_note function."""

    def test_single_file(self):
        """Format note with single file."""
        note = format_forbidden_files_note(["test_main.py"])
        assert "test_main.py" in note
        assert "⚠️" in note
        assert "запрещённые" in note.lower()

    def test_multiple_files(self):
        """Format note with multiple files."""
        note = format_forbidden_files_note(["test_main.py", "tests/test.py"])
        assert "test_main.py" in note
        assert "tests/test.py" in note

    def test_empty_list(self):
        """Format note with empty list."""
        note = format_forbidden_files_note([])
        assert "⚠️" in note

    def test_contains_bullet_points(self):
        """Note contains bullet points for each file."""
        note = format_forbidden_files_note(["file1.py", "file2.py"])
        assert "- file1.py" in note
        assert "- file2.py" in note


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
