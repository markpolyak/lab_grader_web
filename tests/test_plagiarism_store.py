"""
Unit tests for grading/plagiarism_store.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.plagiarism import PlagiarismMatch
from grading.plagiarism_store import list_matches, mark_reviewed, upsert_matches


class TestPlagiarismStore:
    def test_upsert_and_list(self, tmp_path):
        db = tmp_path / "plagiarism.db"
        matches = [
            PlagiarismMatch("bob", "alice", 0.9, details="/report"),
            PlagiarismMatch("carol", "alice", 0.5),
        ]
        assert upsert_matches("c1", "2", matches, db_path=db) == 2

        rows = list_matches("c1", "2", db_path=db)
        assert len(rows) == 2
        assert rows[0].student_a == "alice"  # ordered pair
        assert rows[0].student_b == "bob"
        assert rows[0].similarity == 0.9

        high = list_matches("c1", "2", min_similarity=0.8, db_path=db)
        assert len(high) == 1

    def test_preserves_reviewed_on_upsert(self, tmp_path):
        db = tmp_path / "plagiarism.db"
        upsert_matches(
            "c1", "1",
            [PlagiarismMatch("a", "b", 0.8)],
            db_path=db,
        )
        assert mark_reviewed("c1", "1", "b", "a", db_path=db) is True

        # Re-upsert should not clear reviewed flag / should skip update
        upsert_matches(
            "c1", "1",
            [PlagiarismMatch("a", "b", 0.95, details="new")],
            db_path=db,
        )
        rows = list_matches("c1", "1", db_path=db)
        assert len(rows) == 1
        assert rows[0].reviewed_by_teacher is True
        assert rows[0].similarity == 0.8  # unchanged

    def test_filter_unreviewed(self, tmp_path):
        db = tmp_path / "plagiarism.db"
        upsert_matches(
            "c1", "1",
            [
                PlagiarismMatch("a", "b", 0.9),
                PlagiarismMatch("a", "c", 0.7),
            ],
            db_path=db,
        )
        mark_reviewed("c1", "1", "a", "b", db_path=db)
        unreviewed = list_matches("c1", "1", include_reviewed=False, db_path=db)
        assert len(unreviewed) == 1
        assert unreviewed[0].student_b == "c"
