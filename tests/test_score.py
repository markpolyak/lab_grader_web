"""
Tests for score extraction functionality.
"""
import pytest
from grading.score import (
    extract_score_from_logs,
    format_score,
    format_grade_with_score,
    scores_equal,
)


class TestExtractScoreFromLogs:
    """Tests for extract_score_from_logs function."""

    def test_extract_score_with_notice_pattern(self):
        """Test extracting score from GitHub notice format."""
        logs = "2024-01-15T10:30:00.000Z ##[notice]Points 10/10\n"
        patterns = [r'##\[notice\]Points\s+(\d+(?:[.,]\d+)?)/\d+']

        result = extract_score_from_logs(logs, patterns)

        assert result.found == "10"
        assert result.error is None

    def test_extract_score_with_score_is_pattern(self):
        """Test extracting score from 'Score is' format."""
        logs = "2024-01-15T10:30:00.000Z Score is 10.5\n"
        patterns = [r'Score\s+is\s+(\d+(?:[.,]\d+)?)']

        result = extract_score_from_logs(logs, patterns)

        assert result.found == "10.5"
        assert result.error is None

    def test_extract_score_with_comma_separator(self):
        """Test extracting score with comma decimal separator."""
        logs = "2024-01-15T10:30:00.000Z Total: 10,5\n"
        patterns = [r'Total:\s+(\d+(?:[.,]\d+)?)']

        result = extract_score_from_logs(logs, patterns)

        assert result.found == "10,5"
        assert result.error is None

    def test_extract_score_tries_multiple_patterns(self):
        """Test that multiple patterns are tried in order."""
        logs = "2024-01-15T10:30:00.000Z Score is 8.5\n"
        patterns = [
            r'Points\s+(\d+(?:[.,]\d+)?)',  # Won't match
            r'Score\s+is\s+(\d+(?:[.,]\d+)?)',  # Will match
        ]

        result = extract_score_from_logs(logs, patterns)

        assert result.found == "8.5"
        assert result.error is None

    def test_extract_score_multiple_occurrences_same_value(self):
        """Test multiple occurrences of same score value."""
        logs = """2024-01-15T10:30:00.000Z Score is 10.5
2024-01-15T10:30:01.000Z Score is 10.5
2024-01-15T10:30:02.000Z Score is 10.5"""
        patterns = [r'Score\s+is\s+(\d+(?:[.,]\d+)?)']

        result = extract_score_from_logs(logs, patterns)

        assert result.found == "10.5"
        assert result.error is None

    def test_extract_score_multiple_different_values_error(self):
        """Test error when multiple different scores found."""
        logs = """2024-01-15T10:30:00.000Z Score is 10.5
2024-01-15T10:30:01.000Z Score is 8.0"""
        patterns = [r'Score\s+is\s+(\d+(?:[.,]\d+)?)']

        result = extract_score_from_logs(logs, patterns)

        assert result.found is None
        assert "несколько разных" in result.error.lower()

    def test_extract_score_not_found(self):
        """Test error when score not found in logs."""
        logs = "2024-01-15T10:30:00.000Z No score here\n"
        patterns = [r'Score\s+is\s+(\d+(?:[.,]\d+)?)']

        result = extract_score_from_logs(logs, patterns)

        assert result.found is None
        assert "не найдены" in result.error.lower()

    def test_extract_score_empty_logs(self):
        """Test error with empty logs."""
        result = extract_score_from_logs("", [r'Score\s+is\s+(\d+)'])

        assert result.found is None
        assert "пусты" in result.error.lower()

    def test_extract_score_empty_patterns(self):
        """Test error with empty patterns list."""
        logs = "2024-01-15T10:30:00.000Z Score is 10\n"

        result = extract_score_from_logs(logs, [])

        assert result.found is None
        assert "не указаны" in result.error.lower()


class TestScoresEqual:
    """Tests for scores_equal function."""

    def test_scores_equal_same_format(self):
        """Test comparing scores with same format."""
        assert scores_equal("10.5", "10.5") is True
        assert scores_equal("10,5", "10,5") is True

    def test_scores_equal_different_separators(self):
        """Test comparing scores with different separators."""
        assert scores_equal("10.5", "10,5") is True
        assert scores_equal("10,5", "10.5") is True

    def test_scores_equal_with_trailing_zeros(self):
        """Test comparing scores with trailing zeros."""
        assert scores_equal("10", "10.0") is True
        assert scores_equal("10.0", "10") is True

    def test_scores_not_equal(self):
        """Test scores that are not equal."""
        assert scores_equal("10.5", "10.6") is False
        assert scores_equal("10", "11") is False


class TestFormatScore:
    """Tests for format_score function."""

    def test_format_score_to_dot(self):
        """Test formatting score with dot separator."""
        assert format_score("10.5", ".") == "10.5"
        assert format_score("10,5", ".") == "10.5"

    def test_format_score_to_comma(self):
        """Test formatting score with comma separator."""
        assert format_score("10.5", ",") == "10,5"
        assert format_score("10,5", ",") == "10,5"

    def test_format_score_integer(self):
        """Test formatting integer score."""
        assert format_score("10", ".") == "10"
        assert format_score("10", ",") == "10"

    def test_format_score_invalid_separator(self):
        """Test error with invalid separator."""
        with pytest.raises(ValueError):
            format_score("10.5", ";")


class TestFormatGradeWithScore:
    """Tests for format_grade_with_score function."""

    def test_format_grade_score_only(self):
        """Test formatting grade with score only."""
        result = format_grade_with_score("v", "10.5", 0, ".")
        assert result == "v@10.5"

    def test_format_grade_score_with_penalty(self):
        """Test formatting grade with score and penalty."""
        result = format_grade_with_score("v", "10.5", 3, ".")
        assert result == "v@10.5-3"

    def test_format_grade_integer_score(self):
        """Test formatting grade with integer score."""
        result = format_grade_with_score("v", "10", 0, ".")
        assert result == "v@10"

    def test_format_grade_with_comma_separator(self):
        """Test formatting grade with comma separator."""
        result = format_grade_with_score("v", "10.5", 0, ",")
        assert result == "v@10,5"

    def test_format_grade_score_and_penalty_comma(self):
        """Test formatting grade with score, penalty and comma."""
        result = format_grade_with_score("v", "10,5", 2, ",")
        assert result == "v@10,5-2"
