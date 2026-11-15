"""Допуск студента к очереди: личность, группа и успешная проверка лабы.

Модуль только читает Google Sheets и ничего не пишет. Успешными считаются
форматы, которые выставляет автопроверка: ``v``, ``v-N``, ``v@score``,
``v@score-N`` (разделитель дробной части — точка или запятая). ``x``, ``?``,
пустая ячейка и произвольный текст допуском не являются.
"""
import re
from dataclasses import dataclass

from queues.domain.student import Student

from .sheets_client import (
    calculate_lab_column,
    cell_from_grid,
    find_lab_column_in_grid,
)

SUCCESS_PATTERN = re.compile(
    r"^v(?:@(\d+(?:[.,]\d+)?))?(?:-(\d+))?$",
    re.IGNORECASE,
)


class QueueEligibilityError(Exception):
    pass


class StudentNotFoundError(QueueEligibilityError):
    def __init__(self, github: str) -> None:
        self.github = github
        super().__init__(f"Студент с GitHub {github} не найден")


class LabNotFoundError(QueueEligibilityError):
    def __init__(self, lab_id: str) -> None:
        self.lab_id = lab_id
        super().__init__(f"Лабораторная {lab_id} не найдена в конфигурации")


class LabNotPassedError(QueueEligibilityError):
    def __init__(self, value: str = "") -> None:
        self.value = value
        super().__init__("Лабораторная работа не прошла проверку")


class SheetsUnavailableError(QueueEligibilityError):
    def __init__(self, message: str = "Google Sheets недоступен") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class StudentInfo:
    student: Student
    full_name: str
    group_id: str


@dataclass(frozen=True)
class EligibleStudentLab:
    student: Student
    full_name: str
    group_id: str
    lab_id: str
    lab_name: str


def is_successful_cell(value: str | None) -> bool:
    return bool(SUCCESS_PATTERN.fullmatch((value or "").strip()))


def parse_full_name(full_name: str, github: str) -> Student:
    parts = full_name.split()
    surname = parts[0] if parts else ""
    name = parts[1] if len(parts) > 1 else ""
    patronymic = " ".join(parts[2:]) if len(parts) > 2 else ""
    return Student(name=name, surname=surname, patronymic=patronymic, github=github)


def resolve_lab(course_info: dict, lab_id: str) -> tuple[str, str]:
    """Найти лабу по ключу или short-name; вернуть (ключ, short-name)."""
    labs = course_info.get("labs", {})
    if not isinstance(labs, dict):
        raise LabNotFoundError(lab_id)

    if lab_id in labs:
        key, config = lab_id, labs[lab_id]
    else:
        key, config = next(
            (
                (str(item_key), item_config)
                for item_key, item_config in labs.items()
                if isinstance(item_config, dict)
                and item_config.get("short-name") == lab_id
            ),
            (None, None),
        )
        if key is None or config is None:
            raise LabNotFoundError(lab_id)

    short_name = config.get("short-name") or key
    return key, short_name


def _worksheet_values(spreadsheet, group_id: str) -> list[list[str]]:
    try:
        return spreadsheet.worksheet(group_id).get_all_values()
    except Exception as exc:
        raise SheetsUnavailableError(
            f"Не удалось прочитать лист группы {group_id}"
        ) from exc


def _header_column(values: list[list[str]], title: str) -> int:
    if not values:
        raise SheetsUnavailableError("Таблица группы пуста")
    for index, value in enumerate(values[0]):
        if str(value).strip().casefold() == title.casefold():
            return index + 1
    raise SheetsUnavailableError(f"В таблице нет столбца {title}")


def _student_name_column(course_info: dict) -> int:
    config = course_info.get("google", {}).get("student-name-column", 1)
    return int(config) + 1


def _find_student_row(
    values: list[list[str]], course_info: dict, github: str
) -> tuple[int, str] | None:
    github_column = _header_column(values, "GitHub")
    name_column = _student_name_column(course_info)
    query = github.strip().casefold()
    for row_number, row in enumerate(values[2:], start=3):
        value = row[github_column - 1] if len(row) >= github_column else ""
        if value.strip().casefold() == query:
            return row_number, cell_from_grid(values, row_number, name_column)
    return None


def _lab_column(
    values: list[list[str]], course_info: dict, lab_key: str, short_name: str
) -> int:
    column = find_lab_column_in_grid(values, short_name)
    if column is None and str(lab_key).isdigit():
        offset = course_info.get("google", {}).get("lab-column-offset", 0)
        column = calculate_lab_column(int(lab_key), int(offset))
    if column is None:
        raise LabNotPassedError(short_name)
    return column


def resolve_eligible_lab(
    course_info: dict,
    spreadsheet,
    github: str,
    lab_id: str,
    group_ids,
) -> EligibleStudentLab:
    """Найти студента в одной из групп занятия и проверить его лабу."""
    lab_key, short_name = resolve_lab(course_info, lab_id)
    unavailable: SheetsUnavailableError | None = None

    for group_id in group_ids:
        try:
            values = _worksheet_values(spreadsheet, str(group_id))
        except SheetsUnavailableError as exc:
            unavailable = exc
            continue

        found = _find_student_row(values, course_info, github)
        if found is None:
            continue

        row, full_name = found
        column = _lab_column(values, course_info, lab_key, short_name)
        value = cell_from_grid(values, row, column)
        if not is_successful_cell(value):
            raise LabNotPassedError(value)

        return EligibleStudentLab(
            student=parse_full_name(full_name, github),
            full_name=full_name,
            group_id=str(group_id),
            lab_id=lab_key,
            lab_name=short_name,
        )

    if unavailable is not None:
        raise unavailable
    raise StudentNotFoundError(github)


def resolve_student(
    course_info: dict, spreadsheet, group_id: str, github: str
) -> StudentInfo:
    """Проверить, что студент есть в указанной группе."""
    values = _worksheet_values(spreadsheet, group_id)
    found = _find_student_row(values, course_info, github)
    if found is None:
        raise StudentNotFoundError(github)
    _, full_name = found
    return StudentInfo(
        student=parse_full_name(full_name, github),
        full_name=full_name,
        group_id=group_id,
    )
