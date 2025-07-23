import pytest
from fastapi.testclient import TestClient
from main import app
from unittest.mock import patch, MagicMock

client = TestClient(app)

# Фикстура для создания временного YAML-файла
@pytest.fixture
def setup_yaml(tmp_path):
    yaml_content = """
course:
  name: Machine Learning
  alt-names: ["ОСНОВЫ МАШИННОГО ОБУЧЕНИЯ"]
  github:
    organization: suai-diplom-2025
  google:
    spreadsheet: dummy-spreadsheet-id
    student-name-column: 2
    lab-column-offset: 1
  labs:
    ЛР1:
      github-prefix: ml-task1
      short-name: ЛР1
      alt-lab-names: ["РЕГРЕССИОННЫЙ АНАЛИЗ В РУТНОN"]
      report: ["Цель работы", "Индивидуальное задание"]
"""
    yaml_file = tmp_path / "course1.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")
    return str(tmp_path)

# Тест успешного выполнения
@patch("main.requests.get")
@patch("main.gspread.authorize")
@patch("main.check_pdf_content")
def test_grade_lab_success(mock_pdf, mock_gspread, mock_requests, setup_yaml):
    # Мокаем окружение
    with patch("main.COURSES_DIR", setup_yaml):
        # Мокаем GitHub API
        mock_requests.return_value.status_code = 200
        mock_requests.return_value.json.side_effect = [
            [{"sha": "a1b2c3d4"}],  # commits
            {"files": []},  # commit files
            {"check_runs": [{"name": "CI Check", "conclusion": "success", "html_url": "http://example.com"}]},
            [], [],  # workflows, test_main.py
        ]

        # Мокаем Google Sheets
        mock_sheet = MagicMock()
        mock_sheet.row_values.return_value = ["ФИО", "GitHub", "ЛР1"]
        mock_sheet.col_values.return_value = ["", "", "istoki0bespokoystva"]
        mock_sheet.cell.return_value.value = "А. В. Коновалова"
        mock_gspread.return_value.open_by_key.return_value.worksheet.return_value = mock_sheet

        # Мокаем check_pdf_content
        mock_pdf.return_value = {"first_page": True, "missing_sections": []}

        response = client.post(
            "/courses/1/groups/4232/labs/ЛР1/grade",
            json={"github": "istoki0bespokoystva"}
        )

    assert response.status_code == 200
    assert response.json()["result"] == "✓"
    assert response.json()["status"] == "updated"
    assert "Все проверки пройдены" in response.json()["message"]
    assert "PDF соответствует требованиям" in response.json()["message"]

# Тест ошибки: курс не найден
@patch("main.requests.get")
@patch("main.gspread.authorize")
def test_grade_lab_course_not_found(mock_gspread, mock_requests, tmp_path):
    with patch("main.COURSES_DIR", str(tmp_path)):  # Пустая директория
        response = client.post(
            "/courses/1/groups/4232/labs/ЛР1/grade",
            json={"github": "istoki0bespokoystva"}
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "Course not found"

# Тест ошибки: отсутствует test_main.py
@patch("main.requests.get")
@patch("main.gspread.authorize")
def test_grade_lab_no_test_main(mock_gspread, mock_requests, setup_yaml):
    with patch("main.COURSES_DIR", setup_yaml):
        mock_requests.side_effect = [
            MagicMock(status_code=404),  # test_main.py не найден
            MagicMock(status_code=200),  # workflows
            MagicMock(status_code=200, json=lambda: [{"sha": "a1b2c3d4"}]),  # commits
        ]
        response = client.post(
            "/courses/1/groups/4232/labs/ЛР1/grade",
            json={"github": "istoki0bespokoystva"}
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "⚠️ test_main.py не найден в репозитории"

# Тест ошибки: изменение test_main.py
@patch("main.requests.get")
@patch("main.gspread.authorize")
def test_grade_lab_test_main_modified(mock_gspread, mock_requests, setup_yaml):
    with patch("main.COURSES_DIR", setup_yaml):
        mock_requests.side_effect = [
            MagicMock(status_code=200),  # test_main.py
            MagicMock(status_code=200),  # workflows
            MagicMock(status_code=200, json=lambda: [{"sha": "a1b2c3d4"}]),  # commits
            MagicMock(status_code=200, json=lambda: {"files": [{"filename": "test_main.py", "status": "modified"}]})  # commit files
        ]
        response = client.post(
            "/courses/1/groups/4232/labs/ЛР1/grade",
            json={"github": "istoki0bespokoystva"}
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "🚨 Нельзя изменять test_main.py"

# Тест ошибки: GitHub-логин не найден
@patch("main.requests.get")
@patch("main.gspread.authorize")
def test_grade_lab_github_not_found(mock_gspread, mock_requests, setup_yaml):
    with patch("main.COURSES_DIR", setup_yaml):
        mock_requests.side_effect = [
            MagicMock(status_code=200),  # test_main.py
            MagicMock(status_code=200),  # workflows
            MagicMock(status_code=200, json=lambda: [{"sha": "a1b2c3d4"}]),  # commits
            MagicMock(status_code=200, json=lambda: {"files": []}),  # commit files
            MagicMock(status_code=200, json=lambda: {"check_runs": [{"name": "CI Check", "conclusion": "success", "html_url": "http://example.com"}]})
        ]
        mock_sheet = MagicMock()
        mock_sheet.row_values.return_value = ["ФИО", "GitHub", "ЛР1"]
        mock_sheet.col_values.return_value = ["", "", "otheruser"]  # Логин не совпадает
        mock_sheet.cell.return_value.value = "А. В. Коновалова"
        mock_gspread.return_value.open_by_key.return_value.worksheet.return_value = mock_sheet

        response = client.post(
            "/courses/1/groups/4232/labs/ЛР1/grade",
            json={"github": "istoki0bespokoystva"}
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "GitHub логин не найден в таблице. Зарегистрируйтесь."

# Тест ошибки: некорректный PDF
@patch("main.requests.get")
@patch("main.gspread.authorize")
@patch("main.check_pdf_content")
def test_grade_lab_invalid_pdf(mock_pdf, mock_gspread, mock_requests, setup_yaml):
    with patch("main.COURSES_DIR", setup_yaml):
        mock_requests.side_effect = [
            MagicMock(status_code=200),  # test_main.py
            MagicMock(status_code=200),  # workflows
            MagicMock(status_code=200, json=lambda: [{"sha": "a1b2c3d4"}]),  # commits
            MagicMock(status_code=200, json=lambda: {"files": []}),  # commit files
            MagicMock(status_code=200, json=lambda: {"check_runs": [{"name": "CI Check", "conclusion": "success", "html_url": "http://example.com"}]})
        ]
        mock_sheet = MagicMock()
        mock_sheet.row_values.return_value = ["ФИО", "GitHub", "ЛР1"]
        mock_sheet.col_values.return_value = ["", "", "istoki0bespokoystva"]
        mock_sheet.cell.return_value.value = "А. В. Коновалова"
        mock_gspread.return_value.open_by_key.return_value.worksheet.return_value = mock_sheet

        mock_pdf.return_value = {"first_page": False, "missing_sections": ["Цель работы"]}

        response = client.post(
            "/courses/1/groups/4232/labs/ЛР1/grade",
            json={"github": "istoki0bespokoystva"}
        )
    assert response.status_code == 200
    assert response.json()["result"] == "✗"
    assert "Ошибки в PDF" in response.json()["message"]
    assert response.json()["pdf_check"]["pdf_message"] == [
        "Не все данные присутствуют на первой странице PDF",
        "Отсутствуют разделы в PDF: Цель работы"
    ]
