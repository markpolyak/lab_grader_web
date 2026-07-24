"""
SQLite storage for plagiarism detection matches.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .plagiarism import PlagiarismMatch
from .plagiarism_cache import get_cache_root

logger = logging.getLogger(__name__)

DEFAULT_DB_NAME = "plagiarism.db"


def get_db_path(cache_root: str | Path | None = None) -> Path:
    """Return path to plagiarism.db (same volume as the source cache)."""
    env_path = os.environ.get("PLAGIARISM_DB_PATH")
    if env_path:
        return Path(env_path)
    return get_cache_root(cache_root) / DEFAULT_DB_NAME


@contextmanager
def connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open SQLite connection with row factory; ensures schema exists."""
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        init_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they do not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plagiarism_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            lab_id TEXT NOT NULL,
            student_a TEXT NOT NULL,
            student_b TEXT NOT NULL,
            similarity REAL NOT NULL,
            details TEXT,
            checked_at TEXT NOT NULL,
            reviewed_by_teacher INTEGER NOT NULL DEFAULT 0,
            UNIQUE (course_id, lab_id, student_a, student_b)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_plagiarism_course_lab
        ON plagiarism_matches (course_id, lab_id, similarity DESC)
        """
    )


@dataclass
class StoredMatch:
    id: int
    course_id: str
    lab_id: str
    student_a: str
    student_b: str
    similarity: float
    details: str | None
    checked_at: str
    reviewed_by_teacher: bool


def _ordered_pair(student_a: str, student_b: str) -> tuple[str, str]:
    return (student_a, student_b) if student_a <= student_b else (student_b, student_a)


def upsert_matches(
    course_id: str,
    lab_id: str,
    matches: list[PlagiarismMatch],
    *,
    db_path: str | Path | None = None,
) -> int:
    """
    Insert or update matches for a course/lab.

    Preserves reviewed_by_teacher=1 on conflict (does not reset teacher review).
    Returns number of rows written.
    """
    if not matches:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    with connect(db_path) as conn:
        for match in matches:
            a, b = _ordered_pair(match.student_a, match.student_b)
            conn.execute(
                """
                INSERT INTO plagiarism_matches (
                    course_id, lab_id, student_a, student_b,
                    similarity, details, checked_at, reviewed_by_teacher
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(course_id, lab_id, student_a, student_b) DO UPDATE SET
                    similarity = excluded.similarity,
                    details = excluded.details,
                    checked_at = excluded.checked_at
                WHERE plagiarism_matches.reviewed_by_teacher = 0
                """,
                (course_id, str(lab_id), a, b, match.similarity, match.details or None, now),
            )
            written += 1
    return written


def list_matches(
    course_id: str,
    lab_id: str,
    *,
    min_similarity: float | None = None,
    include_reviewed: bool = True,
    db_path: str | Path | None = None,
) -> list[StoredMatch]:
    """List stored matches for a lab, highest similarity first."""
    clauses = ["course_id = ?", "lab_id = ?"]
    params: list[object] = [course_id, str(lab_id)]

    if min_similarity is not None:
        clauses.append("similarity >= ?")
        params.append(min_similarity)
    if not include_reviewed:
        clauses.append("reviewed_by_teacher = 0")

    sql = f"""
        SELECT id, course_id, lab_id, student_a, student_b,
               similarity, details, checked_at, reviewed_by_teacher
        FROM plagiarism_matches
        WHERE {' AND '.join(clauses)}
        ORDER BY similarity DESC, student_a, student_b
    """
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        StoredMatch(
            id=row["id"],
            course_id=row["course_id"],
            lab_id=row["lab_id"],
            student_a=row["student_a"],
            student_b=row["student_b"],
            similarity=row["similarity"],
            details=row["details"],
            checked_at=row["checked_at"],
            reviewed_by_teacher=bool(row["reviewed_by_teacher"]),
        )
        for row in rows
    ]


def mark_reviewed(
    course_id: str,
    lab_id: str,
    student_a: str,
    student_b: str,
    reviewed: bool = True,
    *,
    db_path: str | Path | None = None,
) -> bool:
    """Set reviewed_by_teacher flag for a pair. Returns True if a row was updated."""
    a, b = _ordered_pair(student_a, student_b)
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE plagiarism_matches
            SET reviewed_by_teacher = ?
            WHERE course_id = ? AND lab_id = ? AND student_a = ? AND student_b = ?
            """,
            (1 if reviewed else 0, course_id, str(lab_id), a, b),
        )
        return cur.rowcount > 0
