"""
Unit tests for grading/sheets_comments.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.sheets_comments import a1_notation, format_plagiarism_comment


class TestA1Notation:
    def test_basic(self):
        assert a1_notation(1, 1) == "A1"
        assert a1_notation(3, 2) == "B3"
        assert a1_notation(10, 26) == "Z10"
        assert a1_notation(5, 27) == "AA5"


class TestFormatComment:
    def test_includes_peers_and_percent(self):
        text = format_plagiarism_comment(
            lab_short_name="ЛР2",
            student="alice",
            matches=[
                {"student_a": "alice", "student_b": "bob", "similarity": 0.85},
                {"student_a": "carol", "student_b": "alice", "similarity": 0.7},
            ],
            cell_a1="D5",
        )
        assert "ЛР2" in text
        assert "alice" in text
        assert "bob" in text
        assert "carol" in text
        assert "85%" in text
        assert "D5" in text
        assert "админ-панели" in text
