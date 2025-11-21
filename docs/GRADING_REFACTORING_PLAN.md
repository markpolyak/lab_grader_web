# План рефакторинга и тестирования grade_lab

## Текущее состояние

Функция `grade_lab` в `main.py` (~200 строк) — монолит, выполняющий:
- Загрузку конфигурации курса/лабы
- 6+ HTTP запросов к GitHub API
- Работу с Google Sheets API
- Бизнес-логику проверки
- Вычисление результата

### Проблемы
- Сложно тестировать (много внешних зависимостей)
- Сложно поддерживать (одно изменение может сломать всё)
- Сложно расширять (добавление функционала увеличивает сложность)

---

## Стратегия рефакторинга

### Принцип: "Тесты → Рефакторинг → Тесты"

**Важно:** Рефакторинг без тестов опасен. Порядок действий:

1. Написать характеризационные тесты на текущее поведение
2. Выносить только чистые функции (без side effects)
3. Покрывать каждую вынесенную функцию тестами
4. Постепенно выносить остальное

### Фаза 1: Характеризационные тесты (safety net)

Тесты, фиксирующие текущее поведение endpoint'а с моками внешних сервисов:

```python
# tests/test_grade_lab_characterization.py
"""
Характеризационные тесты - фиксируют текущее поведение.
Не проверяют "правильность", а гарантируют неизменность при рефакторинге.
"""

@pytest.fixture
def mock_github_api():
    """Мок всех GitHub API вызовов"""
    with responses.RequestsMock() as rsps:
        yield rsps

@pytest.fixture
def mock_gspread(mocker):
    """Мок Google Sheets"""
    return mocker.patch('main.gspread')

def test_grade_lab_success_flow(mock_github_api, mock_gspread, client):
    """Полный успешный flow проверки"""
    # Setup mocks...
    response = client.post("/courses/os-2025/groups/4431/labs/ЛР2/grade",
                          json={"github": "student1"})
    assert response.status_code == 200
    assert response.json()["status"] == "updated"

def test_grade_lab_missing_files(mock_github_api, mock_gspread, client):
    """Проверка при отсутствии обязательных файлов"""

def test_grade_lab_ci_failed(mock_github_api, mock_gspread, client):
    """Проверка при провале CI"""

def test_grade_lab_cell_protected(mock_github_api, mock_gspread, client):
    """Проверка защиты ячейки от перезаписи"""
```

### Фаза 2: Выделение чистых функций

Чистые функции (без side effects) — безопасно выносить и тестировать:

```python
# grading/penalty.py
"""Расчет штрафных баллов"""
from datetime import datetime
from enum import Enum

class PenaltyStrategy(Enum):
    WEEKLY = "weekly"           # 1 балл за неделю
    IMMEDIATE_MAX = "immediate" # Сразу максимум
    NONE = "none"               # Без штрафа
    DAILY = "daily"             # 1 балл за день

def calculate_penalty(
    completed_at: datetime,
    deadline: datetime,
    penalty_max: int,
    strategy: PenaltyStrategy = PenaltyStrategy.WEEKLY
) -> int:
    """
    Рассчитывает штрафные баллы за просрочку.

    Args:
        completed_at: Дата успешного прохождения тестов
        deadline: Дедлайн сдачи
        penalty_max: Максимальный штраф
        strategy: Стратегия расчета штрафа

    Returns:
        Количество штрафных баллов (0 если в срок)
    """
    if completed_at <= deadline:
        return 0

    if strategy == PenaltyStrategy.NONE:
        return 0

    if strategy == PenaltyStrategy.IMMEDIATE_MAX:
        return penalty_max

    delta = completed_at - deadline

    if strategy == PenaltyStrategy.DAILY:
        days = delta.days + (1 if delta.seconds > 0 else 0)
        return min(days, penalty_max)

    # WEEKLY (default)
    weeks = delta.days // 7 + (1 if delta.days % 7 > 0 or delta.seconds > 0 else 0)
    return min(weeks, penalty_max)
```

```python
# grading/taskid.py
"""Проверка номера варианта"""
import re
from dataclasses import dataclass

@dataclass
class TaskIdResult:
    found: int | None
    error: str | None = None

def extract_taskid_from_logs(logs: str) -> TaskIdResult:
    """
    Извлекает TASKID из логов GitHub Actions.

    Ищет строки формата "TASKID is <число>" в начале строки
    (после timestamp GitHub Actions).

    Args:
        logs: Текст логов job'а

    Returns:
        TaskIdResult с найденным ID или ошибкой
    """
    # Паттерн: начало строки (после timestamp) + "TASKID is " + число
    # GitHub Actions добавляет timestamp в начало каждой строки
    # Пример: "2024-01-15T10:30:00.000Z TASKID is 15"
    pattern = r'^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+TASKID is (\d+)'

    matches = re.findall(pattern, logs, re.MULTILINE)

    if len(matches) == 0:
        return TaskIdResult(found=None, error="TASKID не найден в логах")

    if len(matches) > 1:
        return TaskIdResult(
            found=None,
            error="Найдено несколько TASKID в логах. Обратитесь к преподавателю."
        )

    return TaskIdResult(found=int(matches[0]))


def calculate_expected_taskid(
    student_order: int,
    taskid_shift: int,
    taskid_max: int
) -> int:
    """
    Вычисляет ожидаемый номер варианта для студента.

    Formula: ((student_order + taskid_shift) % taskid_max) or taskid_max

    Args:
        student_order: Порядковый номер студента в таблице
        taskid_shift: Смещение из конфига лабы
        taskid_max: Максимальный номер варианта

    Returns:
        Ожидаемый номер варианта (1..taskid_max)
    """
    result = (student_order + taskid_shift) % taskid_max
    return result if result != 0 else taskid_max
```

```python
# grading/ci_checker.py
"""Проверка CI результатов"""
from dataclasses import dataclass
from datetime import datetime

DEFAULT_JOB_NAMES = ["run-autograding-tests", "test", "build", "Autograding"]

@dataclass
class CheckRun:
    name: str
    conclusion: str | None  # success, failure, None (pending)
    completed_at: datetime | None
    html_url: str

@dataclass
class CIResult:
    passed: bool
    passed_count: int
    total_count: int
    summary: list[str]
    latest_success_time: datetime | None


def filter_relevant_jobs(
    check_runs: list[dict],
    configured_jobs: list[str] | None
) -> list[dict]:
    """
    Фильтрует CI jobs по конфигурации.

    Args:
        check_runs: Список check_runs из GitHub API
        configured_jobs: Список имен jobs из конфига (None = использовать дефолты)

    Returns:
        Отфильтрованный список check_runs
    """
    if configured_jobs is None:
        # Ищем любой из дефолтных job'ов
        return [
            run for run in check_runs
            if run.get("name") in DEFAULT_JOB_NAMES
        ]

    # Фильтруем по явно указанным job'ам
    return [
        run for run in check_runs
        if run.get("name") in configured_jobs
    ]


def evaluate_ci_results(check_runs: list[dict]) -> CIResult:
    """
    Анализирует результаты CI проверок.

    Args:
        check_runs: Список check_runs (уже отфильтрованных)

    Returns:
        CIResult с агрегированными данными
    """
    summary = []
    passed_count = 0
    latest_success: datetime | None = None

    for run in check_runs:
        name = run.get("name", "Unnamed")
        conclusion = run.get("conclusion")
        html_url = run.get("html_url", "")

        if conclusion == "success":
            emoji = "✅"
            passed_count += 1
            completed_str = run.get("completed_at")
            if completed_str:
                completed = datetime.fromisoformat(completed_str.replace("Z", "+00:00"))
                if latest_success is None or completed > latest_success:
                    latest_success = completed
        elif conclusion == "failure":
            emoji = "❌"
        else:
            emoji = "⏳"

        summary.append(f"{emoji} {name} — {html_url}")

    return CIResult(
        passed=(passed_count == len(check_runs) and len(check_runs) > 0),
        passed_count=passed_count,
        total_count=len(check_runs),
        summary=summary,
        latest_success_time=latest_success
    )
```

### Фаза 3: Unit-тесты для чистых функций

```python
# tests/test_penalty.py
import pytest
from datetime import datetime, timedelta
from grading.penalty import calculate_penalty, PenaltyStrategy

class TestCalculatePenalty:
    def test_on_time_no_penalty(self):
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 15, 20, 0)
        assert calculate_penalty(completed, deadline, 10) == 0

    def test_one_day_late_one_week_penalty(self):
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 16, 10, 0)
        assert calculate_penalty(completed, deadline, 10) == 1

    def test_eight_days_late_two_weeks_penalty(self):
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 23, 10, 0)  # 8 days
        assert calculate_penalty(completed, deadline, 10) == 2

    def test_penalty_capped_at_max(self):
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 6, 15, 10, 0)  # 3 months
        assert calculate_penalty(completed, deadline, 5) == 5

    def test_immediate_max_strategy(self):
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 3, 16, 0, 1)  # 1 minute late
        assert calculate_penalty(completed, deadline, 10, PenaltyStrategy.IMMEDIATE_MAX) == 10

    def test_no_penalty_strategy(self):
        deadline = datetime(2024, 3, 15, 23, 59)
        completed = datetime(2024, 6, 15, 10, 0)
        assert calculate_penalty(completed, deadline, 10, PenaltyStrategy.NONE) == 0


# tests/test_taskid.py
import pytest
from grading.taskid import extract_taskid_from_logs, calculate_expected_taskid

class TestExtractTaskId:
    def test_simple_taskid(self):
        logs = "2024-01-15T10:30:00.000Z TASKID is 15\n"
        result = extract_taskid_from_logs(logs)
        assert result.found == 15
        assert result.error is None

    def test_taskid_with_other_output(self):
        logs = """2024-01-15T10:30:00.000Z Starting tests...
2024-01-15T10:30:01.000Z TASKID is 7
2024-01-15T10:30:02.000Z Running test case 1...
2024-01-15T10:30:03.000Z Test passed"""
        result = extract_taskid_from_logs(logs)
        assert result.found == 7

    def test_taskid_not_found(self):
        logs = "2024-01-15T10:30:00.000Z No taskid here\n"
        result = extract_taskid_from_logs(logs)
        assert result.found is None
        assert "не найден" in result.error

    def test_multiple_taskids_error(self):
        logs = """2024-01-15T10:30:00.000Z TASKID is 5
2024-01-15T10:30:01.000Z TASKID is 7"""
        result = extract_taskid_from_logs(logs)
        assert result.found is None
        assert "несколько" in result.error

    def test_taskid_in_middle_of_line_ignored(self):
        """TASKID is X внутри строки не должен считаться"""
        logs = """2024-01-15T10:30:00.000Z Some text TASKID is 99 more text
2024-01-15T10:30:01.000Z TASKID is 5"""
        result = extract_taskid_from_logs(logs)
        assert result.found == 5


class TestCalculateExpectedTaskId:
    def test_simple_calculation(self):
        # student 5, shift 0, max 20 -> 5
        assert calculate_expected_taskid(5, 0, 20) == 5

    def test_with_shift(self):
        # student 5, shift 4, max 20 -> 9
        assert calculate_expected_taskid(5, 4, 20) == 9

    def test_modulo_wrap(self):
        # student 18, shift 4, max 20 -> 22 % 20 = 2
        assert calculate_expected_taskid(18, 4, 20) == 2

    def test_modulo_zero_becomes_max(self):
        # student 16, shift 4, max 20 -> 20 % 20 = 0 -> 20
        assert calculate_expected_taskid(16, 4, 20) == 20


# tests/test_ci_checker.py
from grading.ci_checker import filter_relevant_jobs, evaluate_ci_results, DEFAULT_JOB_NAMES

class TestFilterRelevantJobs:
    def test_filter_by_config(self):
        runs = [
            {"name": "run-autograding-tests"},
            {"name": "cpplint"},
            {"name": "other-job"}
        ]
        filtered = filter_relevant_jobs(runs, ["run-autograding-tests", "cpplint"])
        assert len(filtered) == 2
        assert all(r["name"] in ["run-autograding-tests", "cpplint"] for r in filtered)

    def test_use_defaults_when_none(self):
        runs = [
            {"name": "test"},
            {"name": "random-job"},
            {"name": "build"}
        ]
        filtered = filter_relevant_jobs(runs, None)
        assert len(filtered) == 2
        names = [r["name"] for r in filtered]
        assert "test" in names
        assert "build" in names


class TestEvaluateCIResults:
    def test_all_passed(self):
        runs = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
            {"name": "lint", "conclusion": "success", "html_url": "url2", "completed_at": "2024-01-15T10:01:00Z"}
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is True
        assert result.passed_count == 2
        assert result.total_count == 2

    def test_some_failed(self):
        runs = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
            {"name": "lint", "conclusion": "failure", "html_url": "url2", "completed_at": None}
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is False
        assert result.passed_count == 1
```

### Фаза 4: Выделение клиентов (с интерфейсами)

```python
# grading/github_client.py
"""Клиент для GitHub API"""
from abc import ABC, abstractmethod
import requests

class IGitHubClient(ABC):
    """Интерфейс для тестирования"""

    @abstractmethod
    def check_files_exist(self, org: str, repo: str, files: list[str]) -> list[str]:
        """Возвращает список отсутствующих файлов"""

    @abstractmethod
    def get_check_runs(self, org: str, repo: str, sha: str) -> list[dict]:
        """Возвращает check runs для коммита"""

    @abstractmethod
    def get_job_logs(self, org: str, repo: str, job_id: int) -> str:
        """Возвращает логи job'а"""


class GitHubClient(IGitHubClient):
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }

    def check_files_exist(self, org: str, repo: str, files: list[str]) -> list[str]:
        missing = []
        for file in files:
            url = f"https://api.github.com/repos/{org}/{repo}/contents/{file}"
            resp = requests.get(url, headers=self.headers)
            if resp.status_code != 200:
                missing.append(file)
        return missing

    # ... остальные методы


# grading/sheets_client.py
"""Клиент для Google Sheets API"""
from abc import ABC, abstractmethod
from datetime import datetime
import gspread

class ISheetsClient(ABC):
    @abstractmethod
    def find_student_row(self, sheet, github: str) -> int | None:
        pass

    @abstractmethod
    def find_lab_column(self, sheet, short_name: str) -> int | None:
        pass

    @abstractmethod
    def get_deadline(self, sheet, lab_col: int) -> datetime | None:
        pass

    @abstractmethod
    def get_cell_value(self, sheet, row: int, col: int) -> str:
        pass

    @abstractmethod
    def set_cell_value(self, sheet, row: int, col: int, value: str) -> None:
        pass
```

### Фаза 5: Оркестратор

```python
# grading/grader.py
"""Главный класс для проверки лабораторных"""
from dataclasses import dataclass

@dataclass
class GradeResult:
    status: str  # "updated", "rejected", "pending", "error"
    result: str | None
    message: str
    passed: str | None
    checks: list[str]
    current_grade: str | None = None

class LabGrader:
    def __init__(
        self,
        github_client: IGitHubClient,
        sheets_client: ISheetsClient
    ):
        self.github = github_client
        self.sheets = sheets_client

    def grade(
        self,
        course_config: CourseConfig,
        group_id: str,
        lab_id: str,
        github_username: str
    ) -> GradeResult:
        """
        Главный метод проверки.
        Оркестрирует все проверки и возвращает результат.
        """
        lab_config = course_config.get_lab(lab_id)
        repo_name = f"{lab_config.github_prefix}-{github_username}"

        # 1. Проверка файлов
        missing = self.github.check_files_exist(
            course_config.org,
            repo_name,
            lab_config.required_files
        )
        if missing:
            return GradeResult(
                status="error",
                result=None,
                message=f"⚠️ Файлы не найдены: {', '.join(missing)}",
                passed=None,
                checks=[]
            )

        # 2. Получение CI результатов
        # ... и т.д.
```

---

## Целевая структура проекта

```
lab_grader_web/
├── main.py                    # FastAPI endpoints (тонкий слой)
├── grading/                   # Бизнес-логика проверки
│   ├── __init__.py
│   ├── config.py              # Парсинг конфигов курсов
│   ├── penalty.py             # Расчет штрафов
│   ├── taskid.py              # Проверка вариантов
│   ├── ci_checker.py          # Анализ CI результатов
│   ├── github_client.py       # GitHub API клиент
│   ├── sheets_client.py       # Google Sheets клиент
│   └── grader.py              # Оркестратор
├── tests/
│   ├── test_penalty.py
│   ├── test_taskid.py
│   ├── test_ci_checker.py
│   ├── test_config.py
│   ├── test_github_client.py  # С моками HTTP
│   ├── test_sheets_client.py  # С моками gspread
│   ├── test_grader.py         # Интеграционные
│   └── test_grade_lab_e2e.py  # E2E тесты endpoint'а
└── ...
```

---

## Порядок выполнения

| # | Задача | Риск | Тесты |
|---|--------|------|-------|
| 1 | Характеризационные тесты endpoint'а | Низкий | Да |
| 2 | Выделить `penalty.py` | Низкий | Unit |
| 3 | Выделить `taskid.py` | Низкий | Unit |
| 4 | Выделить `ci_checker.py` | Низкий | Unit |
| 5 | Выделить `config.py` | Низкий | Unit |
| 6 | Выделить `github_client.py` | Средний | Integration |
| 7 | Выделить `sheets_client.py` | Средний | Integration |
| 8 | Создать `grader.py` оркестратор | Высокий | E2E |
| 9 | Рефакторинг `grade_lab` → вызов `grader` | Высокий | E2E |

---

## Новый функционал (после рефакторинга)

### 1. Фильтрация CI jobs
- Поддержка `ci.jobs` (вместо `ci.workflows`)
- Дефолтные job'ы: `["run-autograding-tests", "test", "build", "Autograding"]`
- Обратная совместимость с `workflows`

### 2. Расчет штрафов по дедлайнам
- Чтение дедлайна из ячейки над названием лабы
- Стратегии: weekly, immediate_max, none, daily
- Результат: `v-{n}` где n = штрафные баллы

### 3. Проверка TASKID
- Парсинг логов job'а
- Расчет ожидаемого варианта
- Запись `?! Wrong TASKID!` при несовпадении

---

## Зависимости для тестирования

```
# requirements-dev.txt
pytest>=7.0
pytest-cov
pytest-mock
responses          # Мок HTTP запросов
freezegun          # Мок datetime для тестов дедлайнов
pytest-asyncio     # Если будут async тесты
```
