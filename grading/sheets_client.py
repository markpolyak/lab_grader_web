"""
Google Sheets client for lab grading.

This module provides helpers for interacting with Google Sheets
to find students, update grades, and manage lab data.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Cell protection rules: these values can be overwritten
OVERWRITABLE_VALUES = {"", "x", "?"}
OVERWRITABLE_PREFIXES = ("?",)  # Cells starting with "?" can be overwritten


@dataclass
class StudentLocation:
    """Location of a student in the spreadsheet."""
    row: int  # 1-based row index
    name: str
    github: str | None


@dataclass
class LabColumn:
    """Location of a lab column in the spreadsheet."""
    col: int  # 1-based column index
    short_name: str


def find_student_row(
    github_values: list[str],
    github_username: str,
    start_row: int = 3
) -> int | None:
    """
    Find the row number for a student by their GitHub username.

    Args:
        github_values: List of GitHub usernames from the column (0-indexed)
        github_username: Username to find
        start_row: First data row number (default 3 = after 2 header rows)

    Returns:
        1-based row number or None if not found

    Examples:
        >>> find_student_row(["user1", "user2", "user3"], "user2")
        4
        >>> find_student_row(["user1", "user2"], "unknown") is None
        True
    """
    # Handle case-insensitive comparison
    github_lower = github_username.lower()

    for idx, value in enumerate(github_values):
        if value and value.lower() == github_lower:
            return start_row + idx

    return None


def find_lab_column_by_name(
    worksheet,
    short_name: str
) -> int | None:
    """
    Find lab column by searching for short_name in headers.

    Uses gspread's find() method to locate the cell.

    Args:
        worksheet: gspread Worksheet object
        short_name: Lab short name to find (e.g., "ЛР1")

    Returns:
        1-based column number or None if not found
    """
    try:
        cell = worksheet.find(short_name)
        if cell:
            return cell.col
    except Exception:
        pass
    return None


def calculate_lab_column(
    lab_number: int,
    lab_column_offset: int
) -> int:
    """
    Calculate lab column using offset formula.

    Fallback when short_name is not found in headers.

    Args:
        lab_number: Lab number (1, 2, 3, etc.)
        lab_column_offset: Offset from config (0-based in config)

    Returns:
        1-based column number

    Examples:
        >>> calculate_lab_column(1, 3)  # offset 3 means column D (4)
        4
        >>> calculate_lab_column(2, 3)  # lab 2 at offset+2 = column E (5)
        5
    """
    # lab_column_offset is 0-based in config
    # lab_number starts at 1
    # Result is 1-based for gspread
    return lab_column_offset + lab_number


def can_overwrite_cell(current_value: str) -> bool:
    """
    Check if a cell value can be overwritten.

    Allowed to overwrite:
    - Empty cells
    - Cells with "x" (failed grade)
    - Cells starting with "?" (pending/questionable)

    Args:
        current_value: Current cell value

    Returns:
        True if cell can be overwritten

    Examples:
        >>> can_overwrite_cell("")
        True
        >>> can_overwrite_cell("x")
        True
        >>> can_overwrite_cell("?! Wrong TASKID")
        True
        >>> can_overwrite_cell("v")
        False
        >>> can_overwrite_cell("v-3")
        False
    """
    if not current_value:
        return True

    value_stripped = current_value.strip()

    if value_stripped in OVERWRITABLE_VALUES:
        return True

    for prefix in OVERWRITABLE_PREFIXES:
        if value_stripped.startswith(prefix):
            return True

    return False


def format_cell_protection_message(current_value: str) -> str:
    """
    Format message explaining why cell cannot be overwritten.

    Args:
        current_value: Current cell value

    Returns:
        User-friendly message in Russian
    """
    return (
        f"Ячейка уже содержит оценку '{current_value}'. "
        "Для изменения обратитесь к преподавателю."
    )


@dataclass
class GradeUpdate:
    """Result of a grade update operation."""
    success: bool
    message: str
    previous_value: str | None = None
    new_value: str | None = None


def prepare_grade_update(
    current_value: str,
    new_value: str
) -> GradeUpdate:
    """
    Prepare a grade update, checking if it's allowed.

    Args:
        current_value: Current cell value
        new_value: Value to write

    Returns:
        GradeUpdate with success status and message

    Examples:
        >>> result = prepare_grade_update("", "v")
        >>> result.success
        True
        >>> result = prepare_grade_update("v", "v-2")
        >>> result.success
        False
    """
    if can_overwrite_cell(current_value):
        return GradeUpdate(
            success=True,
            message="OK",
            previous_value=current_value,
            new_value=new_value
        )

    return GradeUpdate(
        success=False,
        message=format_cell_protection_message(current_value),
        previous_value=current_value,
        new_value=None
    )


def get_deadline_from_sheet(
    worksheet,
    lab_col: int,
    deadline_row: int = 1,
    timezone_str: str | None = None,
) -> datetime | None:
    """
    Get deadline datetime from spreadsheet.

    Deadline is typically stored in the row above the lab header.

    Args:
        worksheet: gspread Worksheet object
        lab_col: 1-based column number of the lab
        deadline_row: Row number containing deadline (default 1)
        timezone_str: Timezone string (e.g., "UTC+3", "UTC-5") to apply if date is naive

    Returns:
        datetime object with timezone or None if not found/parseable

    Supported formats:
        - "DD.MM.YYYY" (e.g., "15.03.2025")
        - "DD.MM.YYYY HH:MM" (e.g., "15.03.2025 23:59")
        - "YYYY-MM-DD" (e.g., "2025-03-15")
        - "YYYY-MM-DDTHH:MM:SS" (ISO format)
        - Dates with timezone info (e.g., "2025-03-15T23:59:59+03:00")
    """
    try:
        cell_value = worksheet.cell(deadline_row, lab_col).value
        return parse_deadline(cell_value, timezone_str)
    except Exception as e:
        logger.error(f"Error reading deadline: {e}")
        return None


def parse_deadline(
    cell_value: str | None,
    timezone_str: str | None = None,
) -> datetime | None:
    """
    Parse a deadline cell value into a timezone-aware datetime.

    Args:
        cell_value: Raw cell content (may be None or empty)
        timezone_str: Timezone string (e.g., "UTC+3", "UTC-5") to apply if date is naive

    Returns:
        datetime object with timezone or None if empty/unparseable

    Supported formats:
        - "DD.MM.YYYY" (e.g., "15.03.2025")
        - "DD.MM.YYYY HH:MM" (e.g., "15.03.2025 23:59")
        - "YYYY-MM-DD" (e.g., "2025-03-15")
        - "YYYY-MM-DDTHH:MM:SS" (ISO format)
        - Dates with timezone info (e.g., "2025-03-15T23:59:59+03:00")
    """
    from datetime import timezone, timedelta
    import re

    if not cell_value:
        return None

    cell_value = cell_value.strip()

    # Try different date formats
    formats = [
        "%d.%m.%Y %H:%M",      # 15.03.2025 23:59
        "%d.%m.%Y",             # 15.03.2025
        "%Y-%m-%d %H:%M:%S",    # 2025-03-15 23:59:59
        "%Y-%m-%d %H:%M",       # 2025-03-15 23:59
        "%Y-%m-%d",             # 2025-03-15
        "%Y-%m-%dT%H:%M:%S",    # ISO format
    ]

    parsed_dt = None
    for fmt in formats:
        try:
            parsed_dt = datetime.strptime(cell_value, fmt)
            break
        except ValueError:
            continue

    if parsed_dt is None:
        logger.warning(f"Could not parse deadline '{cell_value}'")
        return None

    # If date was parsed without time (midnight), set to end of day (23:59:59)
    # This ensures deadlines like "19.11.2025" mean "until the end of that day"
    if parsed_dt.hour == 0 and parsed_dt.minute == 0 and parsed_dt.second == 0:
        parsed_dt = parsed_dt.replace(hour=23, minute=59, second=59)
        logger.debug(f"Deadline parsed without time, set to end of day: {parsed_dt}")

    # If datetime already has timezone info, return as-is
    if parsed_dt.tzinfo is not None:
        logger.debug(f"Deadline already has timezone: {parsed_dt}")
        return parsed_dt

    # If no timezone and timezone_str provided, apply it
    if timezone_str:
        # Parse timezone string like "UTC+3" or "UTC-5"
        match = re.match(r'UTC([+-]\d+)', timezone_str)
        if match:
            offset_hours = int(match.group(1))
            tz = timezone(timedelta(hours=offset_hours))
            parsed_dt = parsed_dt.replace(tzinfo=tz)
            logger.debug(f"Applied timezone {timezone_str} to deadline: {parsed_dt}")
        else:
            logger.warning(f"Could not parse timezone string '{timezone_str}', using naive datetime")

    return parsed_dt


def get_student_order(
    worksheet,
    row: int,
    task_id_column: int
) -> int | None:
    """
    Get student's task ID / order number from spreadsheet.

    Args:
        worksheet: gspread Worksheet object
        row: Student's row number (1-based)
        task_id_column: Column number containing task IDs (0-based in config, converted to 1-based)

    Returns:
        Integer order number or None if not found

    Note:
        task_id_column from config is 0-based, but gspread uses 1-based indexing,
        so caller should add 1 before passing to this function.
    """
    try:
        cell_value = worksheet.cell(row, task_id_column).value
        if not cell_value:
            return None

        return int(cell_value.strip())
    except (ValueError, TypeError) as e:
        logger.warning(f"Could not parse task ID at row {row}, col {task_id_column}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading task ID: {e}")
        return None


def get_decimal_separator(spreadsheet) -> str:
    """
    Get decimal separator used by the spreadsheet based on its locale.

    Args:
        spreadsheet: gspread Spreadsheet object

    Returns:
        Decimal separator: '.' or ','

    Note:
        - Locales like en_US, en_GB use '.'
        - Locales like ru_RU, de_DE, fr_FR use ','
        - Defaults to '.' if locale cannot be determined
    """
    try:
        # Get spreadsheet metadata to access locale
        metadata = spreadsheet.fetch_sheet_metadata()
        locale = metadata.get('properties', {}).get('locale', 'en_US')

        logger.debug(f"Spreadsheet locale: {locale}")

        # Locales that use comma as decimal separator
        comma_locales = {
            'ru_RU', 'ru', 'de_DE', 'de', 'fr_FR', 'fr', 'es_ES', 'es',
            'it_IT', 'it', 'pt_BR', 'pt', 'nl_NL', 'nl', 'pl_PL', 'pl',
            'cs_CZ', 'cs', 'sv_SE', 'sv', 'da_DK', 'da', 'fi_FI', 'fi',
            'no_NO', 'no', 'tr_TR', 'tr', 'el_GR', 'el', 'hu_HU', 'hu',
        }

        separator = ',' if locale in comma_locales else '.'
        logger.info(f"Using decimal separator '{separator}' for locale {locale}")
        return separator

    except Exception as e:
        logger.warning(f"Could not determine spreadsheet locale: {e}. Using default separator '.'")
        return '.'


# ---------------------------------------------------------------------------
# Grid-based helpers
#
# Bulk grading reads the whole worksheet once via ``get_all_values()`` and then
# resolves everything in memory. The Sheets API allows 60 read requests per
# minute, while the per-cell helpers above spend ~6 requests per student.
#
# ``values`` is the raw grid returned by gspread: a list of rows, each a list
# of cell strings. Rows may be ragged - trailing empty cells are omitted.
# ---------------------------------------------------------------------------


def cell_from_grid(values: list[list[str]], row: int, col: int) -> str:
    """
    Read a single cell from the grid using 1-based coordinates.

    Args:
        values: Grid from worksheet.get_all_values()
        row: 1-based row number
        col: 1-based column number

    Returns:
        Cell value, or "" if the coordinates are outside the grid

    Examples:
        >>> cell_from_grid([["a", "b"], ["c"]], 1, 2)
        'b'
        >>> cell_from_grid([["a", "b"], ["c"]], 2, 2)
        ''
    """
    if row < 1 or col < 1 or row > len(values):
        return ""

    row_values = values[row - 1]
    if col > len(row_values):
        return ""

    return row_values[col - 1] or ""


def column_values_from_grid(
    values: list[list[str]],
    col: int,
    start_row: int = 1,
) -> list[str]:
    """
    Read a column from the grid using 1-based coordinates.

    Mirrors gspread's ``col_values()``: missing cells become empty strings.

    Args:
        values: Grid from worksheet.get_all_values()
        col: 1-based column number
        start_row: 1-based row to start from (default 1 = from the top)

    Returns:
        List of cell values from start_row to the last row of the grid

    Examples:
        >>> column_values_from_grid([["a", "b"], ["c", "d"], ["e"]], 2)
        ['b', 'd', '']
        >>> column_values_from_grid([["a", "b"], ["c", "d"]], 1, start_row=2)
        ['c']
    """
    return [
        cell_from_grid(values, row, col)
        for row in range(start_row, len(values) + 1)
    ]


def find_lab_column_in_grid(
    values: list[list[str]],
    short_name: str,
) -> int | None:
    """
    Find lab column by searching for short_name in the grid.

    Reproduces the scan order of gspread's ``worksheet.find()``: rows top to
    bottom, cells left to right, first exact match wins.

    Args:
        values: Grid from worksheet.get_all_values()
        short_name: Lab short name to find (e.g., "ЛР1")

    Returns:
        1-based column number or None if not found

    Examples:
        >>> find_lab_column_in_grid([["№", "ФИО", "GitHub", "ЛР1"]], "ЛР1")
        4
        >>> find_lab_column_in_grid([["№", "ФИО"]], "ЛР1") is None
        True
    """
    if not short_name:
        return None

    for row_values in values:
        for idx, value in enumerate(row_values):
            if value == short_name:
                return idx + 1

    return None


def get_deadline_from_grid(
    values: list[list[str]],
    lab_col: int,
    deadline_row: int = 1,
    timezone_str: str | None = None,
) -> datetime | None:
    """
    Get deadline datetime from the grid.

    Grid-based counterpart of get_deadline_from_sheet().

    Args:
        values: Grid from worksheet.get_all_values()
        lab_col: 1-based column number of the lab
        deadline_row: Row number containing deadline (default 1)
        timezone_str: Timezone string (e.g., "UTC+3") to apply if date is naive

    Returns:
        datetime object with timezone or None if not found/parseable
    """
    return parse_deadline(cell_from_grid(values, deadline_row, lab_col), timezone_str)


def get_student_order_from_grid(
    values: list[list[str]],
    row: int,
    task_id_column: int,
) -> int | None:
    """
    Get student's task ID / order number from the grid.

    Grid-based counterpart of get_student_order().

    Args:
        values: Grid from worksheet.get_all_values()
        row: Student's 1-based row number
        task_id_column: 1-based column number containing task IDs

    Returns:
        Integer order number or None if empty/unparseable
    """
    cell_value = cell_from_grid(values, row, task_id_column)
    if not cell_value:
        return None

    try:
        return int(cell_value.strip())
    except (ValueError, TypeError) as e:
        logger.warning(f"Could not parse task ID at row {row}, col {task_id_column}: {e}")
        return None
