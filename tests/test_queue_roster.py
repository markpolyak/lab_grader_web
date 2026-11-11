"""
Tests for reading the course spreadsheet for the defense queue (grading/queue_roster.py).

The fake client below has only the read methods the module is allowed to use;
touching anything else - update_cell, batch_update, values_update, ... -
fails the test. That is the check that the queue never writes to the
spreadsheet. The sheet layout follows the real ОС-2026 table: row 1 with the
"GitHub" header in column 34 (0-based), row 2 with the lab short names, data
from row 3. All names are made up.
"""
import ast
import contextlib
import copy
import logging
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

import gspread
import pytest
import requests
import responses
from google.auth.exceptions import TransportError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading import queue_roster
from grading.queue_roster import (
    ROSTER_CACHE_TTL_SECONDS,
    ROSTER_FRESH_AFTER_SECONDS,
    CourseRoster,
    RosterError,
    RosterUnavailable,
    StudentRecord,
    fetch_course_grids,
    find_student_in_grids,
    invalidate,
    login_in_grids,
    reset_roster_cache,
)

SPREADSHEET_ID = "os-2026-sheet"
GITHUB_COL = 34
SHORT_NAMES = ["ЛР0", "ЛР0.1", "ЛР1", "ЛР2", "ЛР3", "ЛР4", "ЛР5", "ЛР6", "ЛР7"]
DEADLINES = [
    "14.09.2026", "21.09.2026", "28.09.2026", "12.10.2026", "26.10.2026",
    "09.11.2026", "23.11.2026", "07.12.2026", "21.12.2026",
]


def header_rows(github_header="GitHub"):
    row1 = ["№ п/п (№ вар.)", "Срок сдачи ->", *DEADLINES]
    row1 += [""] * (GITHUB_COL - len(row1)) + [github_header]
    row2 = ["", "Ф.И.О.", *SHORT_NAMES]
    return [row1, row2]


def student_row(num, name, cells, github=None):
    """A data row; without GitHub it is ragged, as the API returns it."""
    row = [str(num), name, *cells]
    if github is not None:
        row += [""] * (GITHUB_COL - len(row)) + [github]
    while row and row[-1] == "":
        row.pop()
    return row


def group_grid(*students, github_header="GitHub"):
    return header_rows(github_header) + [
        student_row(idx + 1, *student) for idx, student in enumerate(students)
    ]


def os_2026_sheets():
    """Worksheets of the course, in the order of the real table."""
    return {
        "4431": group_grid(
            ("Смирнов Артём Олегович", ["v", "v", "v-1", "vv", "x", "", "?1"], "art-smirnov"),
            ("Кузнецова Дарья Игоревна", ["v", "v", "x"], "DaryaK"),
            ("Орлов Пётр Андреевич", ["v"]),
        ),
        "4432": group_grid(
            ("Волков Илья Сергеевич", ["vv", "vv-2", "v@10", "v"], "ivolkov"),
        ),
        "4434": group_grid(
            ("Морозова Анна Викторовна", ["v", "v"], " Anna-Moroz "),
        ),
        "4331з": group_grid(
            ("Лебедев Олег Николаевич", ["v"], "lebedev-o"),
        ),
        "4332Кз": group_grid(
            ("Федорова Ирина Павловна", ["v", "v", "v"], "fedorova-ip"),
        ),
        "График": [
            ["Неделя", "Дата", "Работа"],
            ["1", "07.09.2026", "ЛР0"],
        ],
    }


def course_info(**google):
    labs = {key: {"short-name": short} for key, short in zip(
        ["0", "01", "1", "2", "3", "4", "5", "6", "7"], SHORT_NAMES,
    )}
    labs["8"] = {"github-prefix": "os-task8"}   # без short-name: в таблице не ищется
    return {
        "name": "Операционные системы",
        "google": {
            "spreadsheet": SPREADSHEET_ID,
            "info-sheet": "График",
            "student-name-column": 1,
            **google,
        },
        "labs": labs,
    }


# ---------------------------------------------------------------------------
# Strict read-only fake of gspread
# ---------------------------------------------------------------------------


class ReadOnly:
    """Any attribute that is not a whitelisted read method fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"the queue must only read the spreadsheet, it touched {name!r}")


class FakeWorksheet(ReadOnly):
    def __init__(self, title):
        self.title = title


class FakeSpreadsheet(ReadOnly):
    def __init__(self, client):
        self.client = client

    def worksheets(self):
        self.client.count("worksheets")
        return [FakeWorksheet(title) for title in self.client.sheets]

    def values_batch_get(self, ranges, params=None):
        self.client.count("values_batch_get")
        self.client.ranges.append(list(ranges))
        value_ranges = []
        for range_name in ranges:
            # Диапазон должен быть процитирован, как это делает absolute_range_name.
            assert range_name.startswith("'") and range_name.endswith("'"), range_name
            title = range_name[1:-1].replace("''", "'")
            grid = self.client.sheets[title]
            value_range = {"range": f"{range_name}!A1:AJ40", "majorDimension": "ROWS"}
            if grid:
                value_range["values"] = copy.deepcopy(grid)
            value_ranges.append(value_range)
        return {"spreadsheetId": SPREADSHEET_ID, "valueRanges": value_ranges}


class FakeSheetsClient(ReadOnly):
    def __init__(self, sheets=None):
        self.sheets = os_2026_sheets() if sheets is None else sheets
        self.reads = 0
        self.calls = []
        self.ranges = []

    def count(self, name):
        self.reads += 1
        self.calls.append(name)

    def open_by_key(self, key):
        self.count("open_by_key")
        assert key == SPREADSHEET_ID
        return FakeSpreadsheet(self)


class BlockingSheetsClient(FakeSheetsClient):
    """Holds open_by_key until the test releases it."""

    def __init__(self, sheets=None):
        super().__init__(sheets)
        self.entered = threading.Event()
        self.release = threading.Event()

    def open_by_key(self, key):
        self.entered.set()
        assert self.release.wait(5), "the test never released the read"
        return super().open_by_key(key)


class Factory:
    """client_factory that counts how often the roster asked for a client."""

    def __init__(self, client=None, error=None):
        self.client = client
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.client


def no_client():
    raise AssertionError("the spreadsheet must not be read here")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def clean_cache():
    reset_roster_cache()
    yield
    reset_roster_cache()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def roster(clock):
    return CourseRoster(clock=clock)


# ---------------------------------------------------------------------------
# The fake itself
# ---------------------------------------------------------------------------


class TestStrictFake:
    """The fake really refuses writes, so the tests below prove the module reads only."""

    @pytest.mark.parametrize("method", [
        "update", "update_cell", "update_cells", "batch_update", "append_row",
        "values_update", "values_append", "values_batch_update", "clear",
    ])
    def test_write_methods_fail(self, method):
        client = FakeSheetsClient()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.worksheets()[0]
        for target in (client, spreadsheet, worksheet):
            with pytest.raises(AssertionError):
                getattr(target, method)

    def test_module_has_no_write_calls(self):
        """No code path of the module, tested or not, names a write method."""
        source_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "grading", "queue_roster.py")
        with open(source_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        writes = {
            "update", "update_cell", "update_cells", "update_acell", "batch_update",
            "append_row", "append_rows", "insert_row", "insert_rows", "insert_cols",
            "values_update", "values_append", "values_batch_update", "values_clear",
            "batch_clear", "delete_rows", "delete_columns", "add_worksheet", "del_worksheet",
        }
        assert attrs & writes == set()


# ---------------------------------------------------------------------------
# find_student_in_grids
# ---------------------------------------------------------------------------


class TestFindStudentInGrids:
    def test_finds_student_with_lab_cells(self):
        record = find_student_in_grids(os_2026_sheets(), "art-smirnov", 1, SHORT_NAMES)
        assert record == StudentRecord(
            github="art-smirnov",
            login="art-smirnov",
            name="Смирнов Артём Олегович",
            group="4431",
            labs={
                "ЛР0": "v", "ЛР0.1": "v", "ЛР1": "v-1", "ЛР2": "vv", "ЛР3": "x",
                "ЛР4": "", "ЛР5": "?1", "ЛР6": "", "ЛР7": "",
            },
        )

    def test_login_ignores_case_and_spaces(self):
        """GitHub logins are case-insensitive; the sheet cell may carry stray spaces."""
        record = find_student_in_grids(os_2026_sheets(), "  ANNA-moroz", 1, SHORT_NAMES)
        assert record.login == "anna-moroz"
        assert record.github == "ANNA-moroz"
        assert record.group == "4434"

    def test_sheet_case_differs_from_github(self):
        record = find_student_in_grids(os_2026_sheets(), "daryak", 1, ["ЛР1"])
        assert record.name == "Кузнецова Дарья Игоревна"
        assert record.labs == {"ЛР1": "x"}

    def test_not_found(self):
        with pytest.raises(RosterError) as e:
            find_student_in_grids(os_2026_sheets(), "stranger", 1, SHORT_NAMES)
        assert e.value.code == "STUDENT_NOT_FOUND"

    def test_empty_login_never_matches_empty_cells(self):
        with pytest.raises(RosterError) as e:
            find_student_in_grids(os_2026_sheets(), "  ", 1, SHORT_NAMES)
        assert e.value.code == "STUDENT_NOT_FOUND"

    def test_duplicate_on_one_sheet(self):
        sheets = {"4431": group_grid(
            ("Смирнов Артём Олегович", ["v"], "art-smirnov"),
            ("Смирнова Алина Олеговна", ["x"], "Art-Smirnov"),
        )}
        with pytest.raises(RosterError) as e:
            find_student_in_grids(sheets, "art-smirnov", 1, SHORT_NAMES)
        assert e.value.code == "STUDENT_AMBIGUOUS"

    def test_duplicate_on_two_sheets(self):
        """A login on two group sheets is an error, not "the first one wins"."""
        sheets = os_2026_sheets()
        sheets["4432"].append(student_row(2, "Смирнов Артём Олегович", ["v"], "art-smirnov"))
        with pytest.raises(RosterError) as e:
            find_student_in_grids(sheets, "art-smirnov", 1, SHORT_NAMES)
        assert e.value.code == "STUDENT_AMBIGUOUS"

    def test_sheet_without_github_header_is_skipped(self):
        """A sheet without the header is not a group sheet even if the login is on it."""
        sheets = os_2026_sheets()
        sheets["Архив"] = group_grid(
            ("Смирнов Артём Олегович", ["v"], "art-smirnov"), github_header="Логин",
        )
        record = find_student_in_grids(sheets, "art-smirnov", 1, SHORT_NAMES)
        assert record.group == "4431"

    @pytest.mark.parametrize("header", ["github", "Github", "GitHub ", "GitHub логин"])
    def test_header_must_be_exact(self, header):
        """Same rule as grade_lab: header_row.index("GitHub")."""
        sheets = {"4431": group_grid(("Смирнов Артём Олегович", ["v"], "art-smirnov"), github_header=header)}
        with pytest.raises(RosterError) as e:
            find_student_in_grids(sheets, "art-smirnov", 1, SHORT_NAMES)
        assert e.value.code == "STUDENT_NOT_FOUND"

    def test_header_only_in_first_row(self):
        grid = group_grid(("Смирнов Артём Олегович", ["v"], "art-smirnov"), github_header="")
        grid[1] += [""] * (GITHUB_COL + 1 - len(grid[1]))
        grid[1][GITHUB_COL] = "GitHub"
        with pytest.raises(RosterError):
            find_student_in_grids({"4431": grid}, "art-smirnov", 1, SHORT_NAMES)

    def test_data_starts_at_third_row(self):
        grid = group_grid(("Смирнов Артём Олегович", ["v"], "art-smirnov"))
        grid[1] += [""] * (GITHUB_COL + 1 - len(grid[1]))
        grid[1][GITHUB_COL] = "short-row-login"
        with pytest.raises(RosterError):
            find_student_in_grids({"4431": grid}, "short-row-login", 1, SHORT_NAMES)

    def test_row_without_name_is_not_a_student(self):
        """Like bulk grading: a login below the group list with no name is a note, not a student."""
        sheets = os_2026_sheets()
        sheets["4431"].append(student_row("", "", [], "art-smirnov"))
        record = find_student_in_grids(sheets, "art-smirnov", 1, SHORT_NAMES)
        assert record.name == "Смирнов Артём Олегович"

        only_note = {"4431": header_rows() + [student_row("", "", [], "lonely")]}
        with pytest.raises(RosterError) as e:
            find_student_in_grids(only_note, "lonely", 1, SHORT_NAMES)
        assert e.value.code == "STUDENT_NOT_FOUND"

    def test_name_column_from_config(self):
        grid = header_rows()
        row = student_row(1, "ignored", ["v"], "art-smirnov")
        row[0] = "Смирнов  Артём\xa0Олегович"
        grid.append(row)
        record = find_student_in_grids({"4431": grid}, "art-smirnov", 0, SHORT_NAMES)
        assert record.name == "Смирнов Артём Олегович"

    def test_ragged_rows(self):
        """Trailing empty cells are missing in the API response."""
        grid = header_rows()
        grid.append(["1", "Смирнов Артём Олегович", "v", "v-1"] + [""] * (GITHUB_COL - 4) + ["art-smirnov"])
        grid.append(["2", "Кузнецова Дарья Игоревна"])
        grid.append([])
        record = find_student_in_grids({"4431": grid}, "art-smirnov", 1, SHORT_NAMES)
        assert record.labs["ЛР0.1"] == "v-1"
        assert record.labs["ЛР1"] == ""
        assert record.labs["ЛР7"] == ""

    def test_lab_column_missing_on_sheet(self):
        record = find_student_in_grids(os_2026_sheets(), "ivolkov", 1, ["ЛР1", "ЛР9"])
        assert record.labs == {"ЛР1": "v@10"}

    def test_empty_grid(self):
        sheets = {"Пустой лист": [], **os_2026_sheets()}
        assert find_student_in_grids(sheets, "ivolkov", 1, []).group == "4432"

    @pytest.mark.parametrize("title, group", [
        ("4331з", "4331з"),
        ("4332Кз", "4332Кз"),
        (" 4431 ", "4431"),
        ("4431\xa0доп", "4431 доп"),
        ("Группа  4434 (магистры)", "Группа 4434 (магистры)"),
        ("О'Нил", "О'Нил"),
    ])
    def test_group_is_normalized_title(self, title, group):
        sheets = {title: group_grid(("Смирнов Артём Олегович", ["v"], "art-smirnov"))}
        assert find_student_in_grids(sheets, "art-smirnov", 1, SHORT_NAMES).group == group


# Ячейки - как в таблице ОС-2026 и как их пишет грейдер; ожидаемый статус -
# правило допуска из docs/COURSE_CONFIG.md (`queue.defended-prefix`, таблица
# «Какую работу можно защищать»).
CELL_CASES = [
    ("v", "passed"),
    ("v-1", "passed"),
    ("v@10", "passed"),
    ("v@10,5-3", "passed"),
    (" v ", "passed"),
    ("vv", "defended"),
    ("vv-2", "defended"),
    ("VV", "defended"),
    ("x", "not_passed"),
    ("X", "not_passed"),
    ("?", "not_passed"),
    ("?1", "not_passed"),
    ("?! Wrong TASKID", "not_passed"),
    ("", "not_passed"),
    ("н/я", "not_passed"),
    ("-", "not_passed"),
]


class TestCellValues:
    @pytest.mark.parametrize("value, _status", CELL_CASES)
    def test_cell_reaches_record_unchanged(self, value, _status):
        """The roster passes the raw cell on; the rule lives in grading/defense_queue.py."""
        sheets = {"4431": group_grid(("Смирнов Артём Олегович", ["v", "v", value], "art-smirnov"))}
        record = find_student_in_grids(sheets, "art-smirnov", 1, ["ЛР1"])
        assert record.labs == {"ЛР1": value}

    @pytest.mark.parametrize("value, status", CELL_CASES)
    def test_cell_status(self, value, status):
        defense_queue = pytest.importorskip("grading.defense_queue")
        sheets = {"4431": group_grid(("Смирнов Артём Олегович", ["v", "v", value], "art-smirnov"))}
        record = find_student_in_grids(sheets, "art-smirnov", 1, ["ЛР1"])
        assert defense_queue.lab_cell_status(record.labs["ЛР1"], "vv") == status


# ---------------------------------------------------------------------------
# fetch_course_grids
# ---------------------------------------------------------------------------


class TestFetchCourseGrids:
    def test_three_reads_and_info_sheet_excluded(self):
        client = FakeSheetsClient()
        grids = fetch_course_grids(client, SPREADSHEET_ID, "График")
        assert client.reads == 3
        assert client.calls == ["open_by_key", "worksheets", "values_batch_get"]
        assert list(grids) == ["4431", "4432", "4434", "4331з", "4332Кз"]
        assert client.ranges == [["'4431'", "'4432'", "'4434'", "'4331з'", "'4332Кз'"]]
        assert grids["4432"][2][1] == "Волков Илья Сергеевич"

    def test_titles_with_spaces_and_apostrophe(self):
        sheets = {
            "Группа 4431": group_grid(("Смирнов Артём Олегович", ["v"], "art-smirnov")),
            "О'Нил и Ко": group_grid(("Кузнецова Дарья Игоревна", ["v"], "daryak")),
            "График": [["Неделя"]],
        }
        client = FakeSheetsClient(sheets)
        grids = fetch_course_grids(client, SPREADSHEET_ID, "График")
        assert client.ranges == [["'Группа 4431'", "'О''Нил и Ко'"]]
        assert find_student_in_grids(grids, "daryak", 1, []).group == "О'Нил и Ко"

    def test_empty_sheet_comes_without_values(self):
        sheets = {"4431": [], "4432": group_grid(("Волков Илья Сергеевич", ["v"], "ivolkov"))}
        grids = fetch_course_grids(FakeSheetsClient(sheets), SPREADSHEET_ID, None)
        assert grids["4431"] == []
        assert find_student_in_grids(grids, "ivolkov", 1, []).group == "4432"

    def test_without_info_sheet_reads_every_sheet(self):
        grids = fetch_course_grids(FakeSheetsClient(), SPREADSHEET_ID, None)
        assert "График" in grids

    def test_info_sheet_matched_after_normalization(self):
        sheets = {"График ": [["Неделя"]], "4431": header_rows()}
        assert list(fetch_course_grids(FakeSheetsClient(sheets), SPREADSHEET_ID, "График")) == ["4431"]

    def test_only_info_sheet(self):
        client = FakeSheetsClient({"График": [["Неделя"]]})
        assert fetch_course_grids(client, SPREADSHEET_ID, "График") == {}
        assert client.calls == ["open_by_key", "worksheets"]

    def test_response_with_missing_ranges_is_an_error(self):
        """zip() would silently drop sheets; a short response must not look like "student not found"."""

        class ShortResponse(FakeSpreadsheet):
            def values_batch_get(self, ranges, params=None):
                resp = super().values_batch_get(ranges, params)
                resp["valueRanges"] = resp["valueRanges"][:-1]
                return resp

        class ShortClient(FakeSheetsClient):
            def open_by_key(self, key):
                self.count("open_by_key")
                return ShortResponse(self)

        with pytest.raises(ValueError):
            fetch_course_grids(ShortClient(), SPREADSHEET_ID, "График")


# ---------------------------------------------------------------------------
# CourseRoster: cache and failures
# ---------------------------------------------------------------------------


class TestLookup:
    def test_first_lookup_reads_spreadsheet_once(self, roster):
        client = FakeSheetsClient()
        factory = Factory(client)
        record = roster.lookup(course_info(), "ivolkov", factory)
        assert record.group == "4432"
        assert record.labs["ЛР0"] == "vv"
        assert record.labs["ЛР1"] == "v@10"
        assert set(record.labs) == set(SHORT_NAMES)
        assert factory.calls == 1
        assert client.reads == 3

    def test_name_column_defaults_to_second(self, roster):
        info = course_info()
        del info["google"]["student-name-column"]
        record = roster.lookup(info, "lebedev-o", Factory(FakeSheetsClient()))
        assert record.name == "Лебедев Олег Николаевич"

    def test_invalid_name_column_falls_back(self, roster):
        record = roster.lookup(course_info(**{"student-name-column": "B"}), "lebedev-o",
                               Factory(FakeSheetsClient()))
        assert record.name == "Лебедев Олег Николаевич"

    def test_not_found_is_roster_error(self, roster):
        """An unknown student is an answer of the spreadsheet, not an outage."""
        with pytest.raises(RosterError) as e:
            roster.lookup(course_info(), "stranger", Factory(FakeSheetsClient()))
        assert e.value.code == "STUDENT_NOT_FOUND"

    def test_course_without_spreadsheet(self, roster):
        info = course_info()
        del info["google"]["spreadsheet"]
        with pytest.raises(RosterUnavailable):
            roster.lookup(info, "ivolkov", no_client)


class TestCache:
    def test_cache_shared_by_students_and_instances(self, roster, clock):
        client = FakeSheetsClient()
        factory = Factory(client)
        roster.lookup(course_info(), "ivolkov", factory)
        clock.now += ROSTER_CACHE_TTL_SECONDS - 1
        assert roster.lookup(course_info(), "art-smirnov", no_client).group == "4431"
        assert CourseRoster(clock=clock).lookup(course_info(), "daryak", no_client).group == "4431"
        assert client.reads == 3
        assert factory.calls == 1

    def test_ttl_expires(self, roster, clock):
        client = FakeSheetsClient()
        roster.lookup(course_info(), "ivolkov", Factory(client))
        clock.now += ROSTER_CACHE_TTL_SECONDS
        roster.lookup(course_info(), "ivolkov", Factory(client))
        assert client.reads == 6

    def test_fresh_rereads_only_older_cache(self, roster, clock):
        client = FakeSheetsClient()
        roster.lookup(course_info(), "art-smirnov", Factory(client))

        clock.now += ROSTER_FRESH_AFTER_SECONDS - 1
        roster.lookup(course_info(), "art-smirnov", no_client, fresh=True)
        assert client.reads == 3

        client.sheets["4431"][2][6] = "v"   # преподаватель поставил ЛР3
        clock.now += 2
        record = roster.lookup(course_info(), "art-smirnov", Factory(client), fresh=True)
        assert record.labs["ЛР3"] == "v"
        assert client.reads == 6

    def test_invalidate(self, roster, clock):
        client = FakeSheetsClient()
        roster.lookup(course_info(), "art-smirnov", Factory(client))
        invalidate(SPREADSHEET_ID)
        assert client.reads == 3   # сброс сам ничего не читает
        client.sheets["4431"][2][6] = "v-2"
        record = roster.lookup(course_info(), "art-smirnov", Factory(client))
        assert record.labs["ЛР3"] == "v-2"
        assert client.reads == 6

    def test_invalidate_of_unknown_spreadsheet(self):
        invalidate("never-read")

    def test_fetched_at(self, roster):
        assert roster.fetched_at(SPREADSHEET_ID) is None
        roster.lookup(course_info(), "ivolkov", Factory(FakeSheetsClient()))
        fetched_at = roster.fetched_at(SPREADSHEET_ID)
        assert fetched_at is not None and fetched_at.utcoffset().total_seconds() == 0
        invalidate(SPREADSHEET_ID)
        assert roster.fetched_at(SPREADSHEET_ID) is None

    def test_reset(self, roster):
        client = FakeSheetsClient()
        roster.lookup(course_info(), "ivolkov", Factory(client))
        reset_roster_cache()
        roster.lookup(course_info(), "ivolkov", Factory(client))
        assert client.reads == 6


class TestUnavailable:
    @pytest.mark.parametrize("error", [
        TransportError("Failed to retrieve http://metadata.google.internal"),
        FileNotFoundError(2, "No such file or directory", "credentials.json"),
    ])
    def test_factory_errors(self, roster, caplog, error):
        with caplog.at_level(logging.WARNING, logger="grading.queue_roster"):
            with pytest.raises(RosterUnavailable):
                roster.lookup(course_info(), "ivolkov", Factory(error=error))
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_read_error(self, roster):
        class BrokenClient(FakeSheetsClient):
            def open_by_key(self, key):
                raise requests.exceptions.ConnectionError("Read timed out")

        with pytest.raises(RosterUnavailable):
            roster.lookup(course_info(), "ivolkov", Factory(BrokenClient()))

    def test_failure_does_not_poison_cache(self, roster):
        with pytest.raises(RosterUnavailable):
            roster.lookup(course_info(), "ivolkov", Factory(error=TransportError("down")))
        assert roster.lookup(course_info(), "ivolkov", Factory(FakeSheetsClient())).group == "4432"

    def test_stale_cache_only_when_allowed(self, roster, clock, caplog):
        roster.lookup(course_info(), "ivolkov", Factory(FakeSheetsClient()))
        clock.now += 600
        down = Factory(error=TransportError("down"))

        with pytest.raises(RosterUnavailable):
            roster.lookup(course_info(), "ivolkov", down)

        with caplog.at_level(logging.WARNING, logger="grading.queue_roster"):
            record = roster.lookup(course_info(), "ivolkov", down, allow_stale_seconds=1800)
        assert record.group == "4432"
        assert any("could not read" in r.getMessage() for r in caplog.records)

        clock.now += 1800
        with pytest.raises(RosterUnavailable):
            roster.lookup(course_info(), "ivolkov", down, allow_stale_seconds=1800)
        assert down.calls == 3


class TestConcurrency:
    def test_single_flight(self, roster):
        """A group signing in at once costs one read of the spreadsheet."""
        client = BlockingSheetsClient()
        factory = Factory(client)
        logins = ["art-smirnov", "daryak", "ivolkov", "anna-moroz", "lebedev-o", "fedorova-ip"]
        results, errors = {}, []

        def sign_in(login):
            try:
                results[login] = roster.lookup(course_info(), login, factory)
            except Exception as e:
                errors.append(e)

        first = threading.Thread(target=sign_in, args=(logins[0],))
        first.start()
        assert client.entered.wait(5)
        others = [threading.Thread(target=sign_in, args=(login,)) for login in logins[1:]]
        for thread in others:
            thread.start()
        time.sleep(0.2)
        client.release.set()
        for thread in [first, *others]:
            thread.join(5)

        assert errors == []
        assert set(results) == set(logins)
        assert client.reads == 3
        assert factory.calls == 1

    def test_waiter_gets_cache_when_read_is_slow(self, roster, clock, monkeypatch):
        monkeypatch.setattr(queue_roster, "ROSTER_WAIT_SECONDS", 0.05)
        roster.lookup(course_info(), "art-smirnov", Factory(FakeSheetsClient()))
        clock.now += ROSTER_FRESH_AFTER_SECONDS + 5

        slow = BlockingSheetsClient()
        slow.sheets["4431"][2][6] = "v"
        holder_result = []
        holder = threading.Thread(target=lambda: holder_result.append(
            roster.lookup(course_info(), "art-smirnov", Factory(slow), fresh=True)))
        holder.start()
        assert slow.entered.wait(5)
        try:
            waiting = roster.lookup(course_info(), "art-smirnov", no_client, fresh=True)
            assert waiting.labs["ЛР3"] == "x"   # старая копия, без ожидания чужого чтения
        finally:
            slow.release.set()
            holder.join(5)
        assert holder_result[0].labs["ЛР3"] == "v"

    def test_waiter_without_cache_is_unavailable(self, roster, monkeypatch):
        monkeypatch.setattr(queue_roster, "ROSTER_WAIT_SECONDS", 0.05)
        slow = BlockingSheetsClient()
        holder = threading.Thread(target=lambda: roster.lookup(course_info(), "ivolkov", Factory(slow)))
        holder.start()
        assert slow.entered.wait(5)
        try:
            with pytest.raises(RosterUnavailable):
                roster.lookup(course_info(), "ivolkov", no_client)
        finally:
            slow.release.set()
            holder.join(5)

    def test_invalidate_during_read_wins(self, roster):
        """A read that started before a grade was written must not refill the cache."""
        slow = BlockingSheetsClient()
        holder = threading.Thread(target=lambda: roster.lookup(course_info(), "ivolkov", Factory(slow)))
        holder.start()
        assert slow.entered.wait(5)
        invalidate(SPREADSHEET_ID)
        slow.release.set()
        holder.join(5)

        client = FakeSheetsClient()
        roster.lookup(course_info(), "ivolkov", Factory(client))
        assert client.reads == 3


def register_github(sheets, title, row_idx, login):
    """What the registration form writes: a login into the GitHub column of a row."""
    row = sheets[title][row_idx]
    row += [""] * (GITHUB_COL - len(row)) + [login]


class HangingSheetsClient(BlockingSheetsClient):
    """Sheets hangs and then fails: the read never refreshes the cache."""

    def open_by_key(self, key):
        self.entered.set()
        assert self.release.wait(5), "the test never released the read"
        raise requests.exceptions.ConnectionError("Read timed out")


@contextlib.contextmanager
def read_in_progress(roster):
    """Another request holds the load lock: its read hangs until the block exits."""
    slow = HangingSheetsClient()
    holder = threading.Thread(target=lambda: _quietly(
        lambda: roster.lookup(course_info(), "ivolkov", Factory(slow), fresh=True)))
    holder.start()
    assert slow.entered.wait(5)
    try:
        yield slow
    finally:
        slow.release.set()
        holder.join(5)


def _quietly(fn):
    try:
        fn()
    except Exception:
        pass


class TestWaiterCopyAge:
    """A request that did not get the load lock in time never takes a copy of any age."""

    @pytest.fixture(autouse=True)
    def short_wait(self, monkeypatch):
        monkeypatch.setattr(queue_roster, "ROSTER_WAIT_SECONDS", 0.05)

    def test_hours_old_copy_is_refused(self, roster, clock):
        """Sign-up: 'v' cached at 09:00, the teacher put 'x' since, Sheets hangs at 15:00."""
        roster.lookup(course_info(), "art-smirnov", Factory(FakeSheetsClient()))
        clock.now += 6 * 3600
        with read_in_progress(roster):
            with pytest.raises(RosterUnavailable):
                roster.lookup(course_info(), "art-smirnov", no_client)
            with pytest.raises(RosterUnavailable):
                roster.lookup(course_info(), "art-smirnov", no_client, fresh=True)

    def test_copy_younger_than_ttl_is_used(self, roster, clock):
        roster.lookup(course_info(), "art-smirnov", Factory(FakeSheetsClient()))
        clock.now += ROSTER_CACHE_TTL_SECONDS - 1
        with read_in_progress(roster):
            assert roster.lookup(course_info(), "art-smirnov", no_client, fresh=True).group == "4431"
        clock.now += 1
        with read_in_progress(roster):
            with pytest.raises(RosterUnavailable):
                roster.lookup(course_info(), "art-smirnov", no_client, fresh=True)

    def test_older_copy_only_within_allow_stale(self, roster, clock):
        roster.lookup(course_info(), "art-smirnov", Factory(FakeSheetsClient()))
        clock.now += 1800
        with read_in_progress(roster):
            record = roster.lookup(course_info(), "art-smirnov", no_client, allow_stale_seconds=1800)
            assert record.group == "4431"
        clock.now += 1
        with read_in_progress(roster):
            with pytest.raises(RosterUnavailable):
                roster.lookup(course_info(), "art-smirnov", no_client, allow_stale_seconds=1800)


class TestLoginExists:
    def test_login_on_any_sheet(self, roster):
        client = FakeSheetsClient()
        factory = Factory(client)
        assert roster.login_exists(course_info(), "ivolkov", factory) is True
        assert roster.login_exists(course_info(), "fedorova-ip", factory) is True
        assert roster.login_exists(course_info(), "stranger", factory) is False
        assert client.reads == 3

    @pytest.mark.parametrize("login", ["DaryaK", "daryak", "  DARYAK ", "anna-moroz", "Anna-Moroz"])
    def test_case_and_spaces_ignored(self, roster, login):
        assert roster.login_exists(course_info(), login, Factory(FakeSheetsClient())) is True

    @pytest.mark.parametrize("login", ["", "   ", None])
    def test_empty_login_is_not_taken(self, roster, login):
        assert roster.login_exists(course_info(), login, no_client) is False

    def test_row_without_name_counts(self, roster):
        """lookup() skips such a row, but the login written there is still taken."""
        sheets = os_2026_sheets()
        sheets["4432"].append(student_row(9, "", ["v"], "ghost"))
        assert roster.login_exists(course_info(), "ghost", Factory(FakeSheetsClient(sheets))) is True

    @pytest.mark.parametrize("login", ["GitHub", "ЛР0", "Ф.И.О."])
    def test_headers_and_other_sheets_are_not_logins(self, roster, login):
        sheets = os_2026_sheets()
        sheets["Архив"] = [["ЛР0"], ["x", "Ф.И.О."], ["", "GitHub"]]   # не лист группы
        assert roster.login_exists(course_info(), login, Factory(FakeSheetsClient(sheets))) is False

    def test_rereads_cache_older_than_fresh_window(self, roster, clock):
        client = FakeSheetsClient()
        roster.lookup(course_info(), "ivolkov", Factory(client))
        clock.now += ROSTER_FRESH_AFTER_SECONDS - 1
        assert roster.login_exists(course_info(), "newbie", no_client) is False

        register_github(client.sheets, "4431", 4, "newbie")
        clock.now += 2
        assert roster.login_exists(course_info(), "newbie", Factory(client)) is True
        assert client.reads == 6

    def test_sees_login_right_after_invalidate(self, roster):
        """Two registrations in a row: the second one sees the login the first one wrote."""
        client = FakeSheetsClient()
        assert roster.login_exists(course_info(), "newbie", Factory(client)) is False
        register_github(client.sheets, "4431", 4, "newbie")
        invalidate(SPREADSHEET_ID)
        assert roster.login_exists(course_info(), "newbie", Factory(client)) is True

    @pytest.mark.parametrize("error", [TransportError("down"), requests.exceptions.ConnectionError("timeout")])
    def test_unreadable_is_none(self, roster, error):
        assert roster.login_exists(course_info(), "ivolkov", Factory(error=error)) is None

    def test_old_copy_is_not_used_when_sheets_fails(self, roster, clock):
        roster.lookup(course_info(), "ivolkov", Factory(FakeSheetsClient()))
        clock.now += ROSTER_FRESH_AFTER_SECONDS
        assert roster.login_exists(course_info(), "ivolkov", Factory(error=TransportError("down"))) is None

    def test_course_without_spreadsheet_is_none(self, roster):
        info = course_info()
        del info["google"]["spreadsheet"]
        assert roster.login_exists(info, "ivolkov", no_client) is None

    def test_another_read_in_progress_without_copy_is_none(self, roster, monkeypatch):
        monkeypatch.setattr(queue_roster, "ROSTER_WAIT_SECONDS", 0.05)
        with read_in_progress(roster):
            assert roster.login_exists(course_info(), "ivolkov", no_client) is None

    def test_pure_check(self):
        grids = os_2026_sheets()
        assert login_in_grids(grids, " Art-Smirnov ") is True
        assert login_in_grids(grids, "") is False
        assert login_in_grids({}, "ivolkov") is False


class TestRealGspread:
    """The same three reads through gspread 6.2.1 itself, with the Sheets API mocked over HTTP."""

    @responses.activate
    def test_three_get_requests(self, roster):
        sheets = os_2026_sheets()
        base = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
        metadata = {
            "spreadsheetId": SPREADSHEET_ID,
            "properties": {"title": "ОС-2026", "locale": "ru_RU"},
            "sheets": [
                {"properties": {"sheetId": idx, "title": title, "index": idx, "sheetType": "GRID",
                                "gridProperties": {"rowCount": 40, "columnCount": 36}}}
                for idx, title in enumerate(sheets)
            ],
        }
        group_titles = [title for title in sheets if title != "График"]
        responses.add(responses.GET, base, json=metadata)
        responses.add(responses.GET, f"{base}/values:batchGet", json={
            "spreadsheetId": SPREADSHEET_ID,
            "valueRanges": [
                {"range": f"'{title}'!A1:AJ40", "majorDimension": "ROWS", "values": sheets[title]}
                for title in group_titles
            ],
        })

        client = gspread.Client(auth=None, session=requests.Session())
        record = roster.lookup(course_info(), "fedorova-ip", Factory(client))

        assert record.group == "4332Кз"
        assert record.labs["ЛР1"] == "v"
        assert [call.request.method for call in responses.calls] == ["GET", "GET", "GET"]
        batch_url = responses.calls[2].request.url
        assert parse_qs(urlparse(batch_url).query)["ranges"] == [f"'{t}'" for t in group_titles]
