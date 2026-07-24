"""
Teacher notifications for plagiarism matches via Google Sheets cell notes.

Cell-anchored Drive discussion comments cannot be created via public APIs
(see docs/PLAGIARISM_DETECTION_PLAN.md §6). We use gspread notes on the grade
cell so teachers (and Readers) see the flag on hover. Grade value is never changed.
"""
from __future__ import annotations

import logging
from typing import Any

import gspread
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger(__name__)

DRIVE_SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def _credentials(credentials_file: str) -> ServiceAccountCredentials:
    return ServiceAccountCredentials.from_json_keyfile_name(
        credentials_file, DRIVE_SCOPES
    )


def a1_notation(row: int, col: int) -> str:
    """Convert 1-based row/col to A1 notation (supports cols beyond Z)."""
    n = col
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def format_plagiarism_comment(
    *,
    lab_short_name: str | None,
    student: str,
    matches: list[dict[str, Any]],
    cell_a1: str | None = None,
) -> str:
    """Build teacher-facing Russian note body."""
    lab = lab_short_name or "лабораторная"
    lines = [
        f"⚠️ Возможный плагиат ({lab}), студент GitHub: {student}",
    ]
    if cell_a1:
        lines.append(f"Ячейка: {cell_a1}")
    for m in matches:
        peers = {m["student_a"], m["student_b"]} - {student}
        peer = next(iter(peers), m["student_b"])
        pct = round(float(m["similarity"]) * 100)
        lines.append(f"• совпадение ~{pct}% с {peer}")
    lines.append("Проверьте пару и отметьте review в админ-панели.")
    return "\n".join(lines)


def set_cell_note(
    spreadsheet_id: str,
    worksheet_title: str,
    row: int,
    col: int,
    note: str,
    *,
    credentials_file: str,
) -> bool:
    """
    Set / replace a note on a specific cell (visible in Sheets UI on hover).

    Returns True on success.
    """
    try:
        creds = _credentials(credentials_file)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(spreadsheet_id).worksheet(worksheet_title)
        cell = a1_notation(row, col)
        if hasattr(sheet, "update_note"):
            sheet.update_note(cell, note)
        else:
            sheet.insert_note(cell, note)
        logger.info("Set note on %s!%s", worksheet_title, cell)
        return True
    except Exception:
        logger.exception(
            "Failed to set cell note on %s row=%s col=%s",
            spreadsheet_id, row, col,
        )
        return False


def notify_teacher_plagiarism(
    *,
    spreadsheet_id: str,
    worksheet_title: str,
    row: int,
    col: int,
    student: str,
    lab_short_name: str | None,
    matches: list[dict[str, Any]],
    credentials_file: str,
) -> dict[str, Any]:
    """
    Notify teacher about flagged plagiarism for one graded student.

    Writes a cell note only. Returns status dict.
    """
    if not matches:
        return {"note": False, "cell": None}

    cell = a1_notation(row, col)
    content = format_plagiarism_comment(
        lab_short_name=lab_short_name,
        student=student,
        matches=matches,
        cell_a1=cell,
    )

    note_ok = set_cell_note(
        spreadsheet_id,
        worksheet_title,
        row,
        col,
        content,
        credentials_file=credentials_file,
    )
    return {"note": note_ok, "cell": cell}
