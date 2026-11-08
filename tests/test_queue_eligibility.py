import pytest

from grading.queue_eligibility import (
    LabNotFoundError,
    LabNotPassedError,
    SheetsUnavailableError,
    StudentNotFoundError,
    is_successful_cell,
    parse_full_name,
    resolve_eligible_lab,
    resolve_student,
)

COURSE = {
    "google": {"student-name-column": 1, "lab-column-offset": 2},
    "labs": {
        "1": {"short-name": "ЛР1"},
        "2": {"short-name": "ЛР2"},
    },
}


def grid(lab_value="v", *, groups=("4431",)):
    values = {
        "4431": [
            ["№", "ФИО", "GitHub", "ЛР1", "ЛР2"],
            ["", "", "", "", ""],
            ["1", "Иванов Иван Иванович", "student", lab_value, "x"],
        ]
    }
    return values


class Worksheet:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return [list(row) for row in self.values]


class Spreadsheet:
    def __init__(self, sheets):
        self.sheets = {key: Worksheet(value) for key, value in sheets.items()}

    def worksheet(self, group_id):
        if group_id not in self.sheets:
            raise KeyError(group_id)
        return self.sheets[group_id]


@pytest.mark.parametrize(
    "value",
    ["v", "v-3", "v@10", "v@10.5", "v@10,5", "v@10,5-3", " V "],
)
def test_successful_cell_formats(value):
    assert is_successful_cell(value)


@pytest.mark.parametrize(
    "value",
    ["", "x", "?", "v@", "v-", "abc", "v x", None],
)
def test_failed_cell_formats(value):
    assert not is_successful_cell(value)


def test_resolve_eligible_lab_returns_trusted_student_data():
    result = resolve_eligible_lab(
        COURSE, Spreadsheet(grid()), "Student", "ЛР1", ("4431",)
    )

    assert result.group_id == "4431"
    assert result.lab_id == "1"
    assert result.lab_name == "ЛР1"
    assert result.full_name == "Иванов Иван Иванович"
    assert result.student.github == "Student"
    assert result.student.surname == "Иванов"
    assert result.student.name == "Иван"
    assert result.student.patronymic == "Иванович"


def test_resolve_eligible_lab_searches_only_given_groups():
    sheets = grid()
    sheets["4432"] = [
        ["№", "ФИО", "GitHub", "ЛР1"],
        ["", ""],
        ["2", "Петров Пётр", "other", "v"],
    ]

    result = resolve_eligible_lab(
        COURSE, Spreadsheet(sheets), "student", "ЛР1", ("4431", "4432")
    )
    assert result.group_id == "4431"


def test_resolve_eligible_lab_rejects_failed_cell():
    with pytest.raises(LabNotPassedError):
        resolve_eligible_lab(
            COURSE, Spreadsheet(grid("x")), "student", "ЛР1", ("4431",)
        )


def test_resolve_eligible_lab_reports_unknown_student():
    with pytest.raises(StudentNotFoundError):
        resolve_eligible_lab(
            COURSE, Spreadsheet(grid()), "unknown", "ЛР1", ("4431",)
        )


def test_resolve_eligible_lab_reports_unknown_lab():
    with pytest.raises(LabNotFoundError):
        resolve_eligible_lab(
            COURSE, Spreadsheet(grid()), "student", "ЛР404", ("4431",)
        )


def test_missing_worksheet_is_reported_as_unavailable():
    with pytest.raises(SheetsUnavailableError):
        resolve_eligible_lab(
            COURSE, Spreadsheet(grid()), "student", "ЛР1", ("9999",)
        )


def test_resolve_student_checks_the_requested_group():
    info = resolve_student(COURSE, Spreadsheet(grid()), "4431", "student")
    assert info.group_id == "4431"
    assert info.full_name == "Иванов Иван Иванович"

    with pytest.raises(StudentNotFoundError):
        resolve_student(COURSE, Spreadsheet(grid()), "4431", "unknown")

    with pytest.raises(SheetsUnavailableError):
        resolve_student(COURSE, Spreadsheet(grid()), "9999", "student")


def test_parse_full_name_handles_missing_patronymic():
    student = parse_full_name("Иванов Иван", "student")
    assert student.surname == "Иванов"
    assert student.name == "Иван"
    assert student.patronymic == ""
