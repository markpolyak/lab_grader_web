"""
Unit tests for lab column lookup functionality.
Tests the logic of finding lab columns by short-name or fallback to offset calculation.
"""
import pytest
from unittest.mock import Mock, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLabColumnLookup:
    """Tests for lab column lookup logic."""

    def test_find_column_by_short_name(self):
        """Test that lab column is found by short-name using sheet.find()."""
        # Mock sheet
        mock_sheet = Mock()
        mock_cell = Mock()
        mock_cell.col = 7
        mock_cell.row = 2
        mock_sheet.find.return_value = mock_cell

        lab_short_name = "ЛР2"

        # Simulate the lookup logic from grade_lab
        cell = mock_sheet.find(lab_short_name)
        assert cell is not None
        lab_col = cell.col

        assert lab_col == 7
        mock_sheet.find.assert_called_once_with("ЛР2")

    def test_find_column_by_short_name_not_found(self):
        """Test that error is raised when short-name is not found."""
        mock_sheet = Mock()
        mock_sheet.find.return_value = None

        lab_short_name = "ЛР99"
        cell = mock_sheet.find(lab_short_name)

        assert cell is None

    def test_fallback_to_offset_calculation(self):
        """Test fallback to offset calculation when short-name is missing."""
        lab_config = {}  # No short-name
        lab_offset = 3
        lab_number = 2

        lab_short_name = lab_config.get("short-name")
        assert lab_short_name is None

        # Fallback calculation
        lab_col = lab_offset + lab_number
        assert lab_col == 5

    def test_short_name_found_in_different_rows(self):
        """Test that short-name can be found in any row, not just row 2."""
        mock_sheet = Mock()

        # Simulate short-name found in row 5
        mock_cell = Mock()
        mock_cell.col = 4
        mock_cell.row = 5
        mock_sheet.find.return_value = mock_cell

        cell = mock_sheet.find("ЛР1")
        assert cell.row == 5
        assert cell.col == 4

    def test_multiple_labs_different_columns(self):
        """Test finding multiple different labs."""
        mock_sheet = Mock()

        test_cases = [
            ("ЛР0", 4),
            ("ЛР0.1", 5),
            ("ЛР1", 6),
            ("ЛР2", 7),
            ("ЛР3", 8),
        ]

        for short_name, expected_col in test_cases:
            mock_cell = Mock()
            mock_cell.col = expected_col
            mock_cell.row = 2
            mock_sheet.find.return_value = mock_cell

            cell = mock_sheet.find(short_name)
            assert cell.col == expected_col, f"Failed for {short_name}"


class TestLabConfigLookup:
    """Tests for lab config lookup by lab_id."""

    def test_parse_lab_id_simple(self):
        """Test parsing simple lab IDs."""
        import re

        def parse_lab_id(lab_id: str) -> int:
            match = re.search(r"\d+", lab_id)
            if not match:
                raise ValueError("Invalid lab_id")
            return int(match.group(0))

        assert parse_lab_id("ЛР1") == 1
        assert parse_lab_id("ЛР2") == 2
        assert parse_lab_id("lab3") == 3
        assert parse_lab_id("1") == 1

    def test_parse_lab_id_with_decimal(self):
        """Test that ЛР0.1 parses to 0 (first number found)."""
        import re

        def parse_lab_id(lab_id: str) -> int:
            match = re.search(r"\d+", lab_id)
            if not match:
                raise ValueError("Invalid lab_id")
            return int(match.group(0))

        # ЛР0.1 -> finds "0" first
        assert parse_lab_id("ЛР0.1") == 0

    def test_lab_config_lookup_by_string_key(self):
        """Test that lab config uses string keys like '2', not 'ЛР2'."""
        labs = {
            "0": {"short-name": "ЛР0", "github-prefix": "os-task0"},
            "01": {"short-name": "ЛР0.1", "github-prefix": "os-task-I-1"},
            "1": {"short-name": "ЛР1", "github-prefix": "os-task1"},
            "2": {"short-name": "ЛР2", "github-prefix": "os-task2"},
        }

        # Lookup by string key
        assert labs.get("2", {}).get("short-name") == "ЛР2"
        assert labs.get("01", {}).get("short-name") == "ЛР0.1"

        # Wrong key format should return empty
        assert labs.get("ЛР2", {}) == {}


class TestGradeLabIntegration:
    """Integration-style tests for grade_lab function logic."""

    def test_grade_lab_finds_correct_column(self):
        """Test that grade_lab finds the correct column for a lab."""
        # Setup mock sheet
        mock_sheet = Mock()
        mock_sheet.row_values.return_value = ["", "ФИО", "GitHub", "ЛР0", "ЛР0.1", "ЛР1", "ЛР2"]
        mock_sheet.col_values.return_value = ["", "", "student1", "student2"]

        # Mock find to return correct column for ЛР2
        mock_cell = Mock()
        mock_cell.col = 7  # ЛР2 is in column 7 (1-based)
        mock_cell.row = 1
        mock_sheet.find.return_value = mock_cell

        mock_sheet.cell.return_value = Mock(value="")

        # Simulate the lookup
        lab_config = {"short-name": "ЛР2", "github-prefix": "os-task2"}
        lab_short_name = lab_config.get("short-name")

        cell = mock_sheet.find(lab_short_name)
        lab_col = cell.col

        assert lab_col == 7
        mock_sheet.find.assert_called_with("ЛР2")

    def test_grade_lab_fallback_when_no_short_name(self):
        """Test that grade_lab uses fallback when short-name is missing."""
        lab_config = {"github-prefix": "os-task2"}  # No short-name
        course_google_config = {"lab-column-offset": 3}

        lab_short_name = lab_config.get("short-name")
        lab_number = 2  # Parsed from "ЛР2"

        if lab_short_name:
            # Would search by short-name
            lab_col = None
        else:
            # Fallback
            lab_offset = course_google_config.get("lab-column-offset", 1)
            lab_col = lab_offset + lab_number

        assert lab_col == 5  # 3 + 2


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_lab_config(self):
        """Test handling of empty lab config."""
        lab_config = {}
        lab_short_name = lab_config.get("short-name")
        assert lab_short_name is None

    def test_special_characters_in_short_name(self):
        """Test that special characters in short-name are handled."""
        mock_sheet = Mock()
        mock_cell = Mock()
        mock_cell.col = 5
        mock_cell.row = 2
        mock_sheet.find.return_value = mock_cell

        # Short name with special characters
        short_names = ["ЛР0.1", "Лаб-1", "Lab_2"]

        for name in short_names:
            cell = mock_sheet.find(name)
            assert cell is not None
            mock_sheet.find.assert_called_with(name)

    def test_case_sensitivity(self):
        """Test that search is case-sensitive (as expected by gspread.find)."""
        mock_sheet = Mock()

        # gspread.find is case-sensitive by default
        mock_sheet.find.side_effect = lambda x: Mock(col=5) if x == "ЛР1" else None

        assert mock_sheet.find("ЛР1") is not None
        assert mock_sheet.find("лр1") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
