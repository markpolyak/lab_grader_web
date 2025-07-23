import io
import os
import pytest
from fastapi.testclient import TestClient
from main import app
from unittest.mock import patch, MagicMock, mock_open

client = TestClient(app)


def test_get_courses():
    """
    Проверяет, что эндпоинт /courses возвращает список курсов и статус 200.
    """
    response = client.get("/courses")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_existing_course():
    """
    Проверяет, что при запросе существующего курса возвращаются основные поля: name, semester, email.
    """
    response = client.get("/courses/1")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "semester" in data
    assert "email" in data


def test_get_nonexistent_course():
    """
    Проверяет, что при запросе несуществующего курса возвращается статус 404.
    """
    response = client.get("/courses/999")
    assert response.status_code == 404

@pytest.fixture
def temp_yaml_file():
    filename = "example.yaml"
    path = os.path.join("courses", filename)

    if os.path.exists(path):
        os.remove(path)

    yield filename

    if os.path.exists(path):
        os.remove(path)


def test_upload_valid_yaml(temp_yaml_file):
    """
    Проверяет, что корректный YAML-файл успешно загружается через эндпоинт /courses/upload.
    """
    yaml_content = f"""
course:
  name: Example Course
  semester: Fall 2025
  email: example@example.com
  github:
    organization: example-org
  google:
    spreadsheet: dummy
    info-sheet: Info
  labs:
    ЛР1:
      short-name: ЛР1
      github-prefix: lab1
"""
    file = io.BytesIO(yaml_content.encode("utf-8"))
    response = client.post(
        "/courses/upload",
        files={"file": (temp_yaml_file, file, "application/x-yaml")}
    )
    assert response.status_code == 200
    assert "успешно" in response.json()["detail"].lower()
    assert os.path.exists(os.path.join("courses", temp_yaml_file))


def test_upload_invalid_yaml():
    """
    Проверяет, что при загрузке некорректного YAML-файла возвращается ошибка с кодом 400.
    """
    broken_yaml = """
course:
  name: Test
    semester: Broken indentation
"""
    file = io.BytesIO(broken_yaml.encode("utf-8"))
    response = client.post(
        "/courses/upload",
        files={"file": ("broken.yaml", file, "application/x-yaml")}
    )
    assert response.status_code == 400
    assert "yaml" in response.json()["detail"].lower()


def test_get_existing_course_detail():
    """
    Проверяет, что при запросе информации о курсе по ID возвращается корректная структура ответа.
    """
    response = client.get("/courses/1")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "semester" in data
    assert "email" in data
    assert "config" in data
    assert data["id"] == "1"


def test_get_nonexistent_course_detail():
    """
    Проверяет поведение при запросе информации о несуществующем курсе — ожидается статус 404.
    """
    response = client.get("/courses/999")
    assert response.status_code == 404
    assert "Course not found" in response.json()["detail"]


def test_get_edit_course():
    """
    Проверяет, что при запросе на редактирование курса возвращается содержимое файла.
    """
    response = client.get("/courses/1/edit")
    assert response.status_code == 200
    data = response.json()
    assert "filename" in data
    assert "content" in data
    assert "course:" in data["content"]


def test_put_edit_course_valid():
    """
    Проверяет успешное обновление содержимого YAML-файла курса.
    """
    get_resp = client.get("/courses/1/edit")
    assert get_resp.status_code == 200
    original_content = get_resp.json()["content"]

    modified_content = original_content.replace("Fall", "Spring") if "Fall" in original_content else original_content + "\n# test comment"

    try:
        put_resp = client.put("/courses/1/edit", json={"content": modified_content})
        assert put_resp.status_code == 200
        assert "успешно" in put_resp.json()["message"].lower()
    finally:
        client.put("/courses/1/edit", json={"content": original_content})


def test_put_edit_course_invalid():
    """
    Проверяет обработку ошибки при передаче некорректного YAML-контента на обновление.
    """
    broken_content = "course:\n  name: Broken\n    semester: Bad indent"
    response = client.put("/courses/1/edit", json={"content": broken_content})
    assert response.status_code == 400
    assert "yaml" in response.json()["detail"].lower()


@patch("main.ServiceAccountCredentials.from_json_keyfile_name")
@patch("main.gspread.authorize")
def test_get_course_groups_mock(mock_authorize, mock_creds):
    """
    Проверяет работу получения списка групп курса с использованием мока Google Sheets API.
    """
    mock_creds.return_value = MagicMock()

    fake_sheet1 = MagicMock()
    fake_sheet1.title = "4231"
    fake_sheet2 = MagicMock()
    fake_sheet2.title = "Info"

    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheets.return_value = [fake_sheet1, fake_sheet2]

    mock_client = MagicMock()
    mock_client.open_by_key.return_value = mock_spreadsheet
    mock_authorize.return_value = mock_client

    response = client.get("/courses/1/groups")
    assert response.status_code == 200
    assert response.json() == ["4231", "Info"]


@patch("main.ServiceAccountCredentials.from_json_keyfile_name")
@patch("main.gspread.authorize")
def test_get_course_labs_mock(mock_authorize, mock_creds):
    """
    Проверяет корректность получения лабораторных работ по группе и курсу, используя моки.
    """
    mock_creds.return_value = MagicMock()

    mock_sheet = MagicMock()
    mock_sheet.row_values.return_value = ["", "", "ЛР1", "ЛР2"]

    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheet.return_value = mock_sheet

    mock_client = MagicMock()
    mock_client.open_by_key.return_value = mock_spreadsheet
    mock_authorize.return_value = mock_client

    response = client.get("/courses/1/groups/4231/labs")
    assert response.status_code == 200
    assert "ЛР1" in response.json()


@patch("main.requests.get")
@patch("main.ServiceAccountCredentials.from_json_keyfile_name")
@patch("main.gspread.authorize")
@patch("main.os.listdir", return_value=["test_course.yaml"])
@patch("builtins.open", new_callable=mock_open, read_data='''course:
  google:
    spreadsheet: "spreadsheet123"
    student-name-column: 2
''')
def test_register_student_mock(mock_open_file, mock_listdir, mock_authorize, mock_creds, mock_requests):
    """
    Проверяет успешную регистрацию студента через форму при отсутствии указанного GitHub-аккаунта.
    """
    mock_creds.return_value = MagicMock()

    mock_sheet = MagicMock()
    mock_sheet.col_values.return_value = ["", "", "Иванов Иван"]
    mock_sheet.row_values.return_value = ["ФИО", "GitHub"]
    mock_sheet.cell.return_value.value = ""
    mock_sheet.update_cell.return_value = None

    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheet.return_value = mock_sheet

    mock_client = MagicMock()
    mock_client.open_by_key.return_value = mock_spreadsheet
    mock_authorize.return_value = mock_client

    mock_requests.return_value.status_code = 200

    payload = {
        "name": "Иван",
        "surname": "Иванов",
        "patronymic": "",
        "github": "torvalds"
    }

    response = client.post("/courses/1/groups/4231/register", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "registered"


@patch("main.requests.get")
@patch("main.ServiceAccountCredentials.from_json_keyfile_name")
@patch("main.gspread.authorize")
@patch("main.os.listdir", return_value=["test_course.yaml"])
@patch("builtins.open", new_callable=mock_open, read_data='''\
course:
  google:
    spreadsheet: "spreadsheet123"
    student-name-column: 2
''')
def test_register_student_already_registered_mock(
    mock_open_file,
    mock_listdir,
    mock_authorize,
    mock_creds,
    mock_requests
):
    """
    Проверяет поведение при повторной регистрации студента с тем же GitHub-логином —
    ожидается статус already_registered.
    """
    mock_creds.return_value = MagicMock()

    mock_sheet = MagicMock()
    mock_sheet.col_values.return_value = ["", "", "Иванов Иван"]
    mock_sheet.row_values.return_value = ["ФИО", "GitHub"]
    mock_sheet.cell.return_value.value = "torvalds"
    mock_sheet.update_cell.return_value = None

    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheet.return_value = mock_sheet

    mock_client = MagicMock()
    mock_client.open_by_key.return_value = mock_spreadsheet
    mock_authorize.return_value = mock_client

    mock_requests.return_value.status_code = 200

    payload = {
        "name": "Иван",
        "surname": "Иванов",
        "patronymic": "",
        "github": "torvalds"
    }

    response = client.post("/courses/1/groups/4231/register", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "already_registered"
    assert "уже был указан ранее" in response.json()["message"]


@patch("main.requests.get")
@patch("main.ServiceAccountCredentials.from_json_keyfile_name")
@patch("main.gspread.authorize")
@patch("main.os.listdir", return_value=["test_course.yaml"])
@patch("builtins.open", new_callable=mock_open, read_data='''\
course:
  google:
    spreadsheet: "spreadsheet123"
    student-name-column: 2
''')
def test_register_student_conflict_mock(
    mock_open_file,
    mock_listdir,
    mock_authorize,
    mock_creds,
    mock_requests
):
    """
    Проверяет конфликтную ситуацию, когда у студента уже указан другой GitHub — ожидается ошибка 409.
    """
    mock_creds.return_value = MagicMock()

    mock_sheet = MagicMock()
    mock_sheet.col_values.return_value = ["", "", "Иванов Иван"]
    mock_sheet.row_values.return_value = ["ФИО", "GitHub"]
    mock_sheet.cell.return_value.value = "octocat"  # ← уже указан другой GitHub
    mock_sheet.update_cell.return_value = None

    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheet.return_value = mock_sheet

    mock_client = MagicMock()
    mock_client.open_by_key.return_value = mock_spreadsheet
    mock_authorize.return_value = mock_client

    mock_requests.return_value.status_code = 200

    payload = {
        "name": "Иван",
        "surname": "Иванов",
        "patronymic": "",
        "github": "torvalds"
    }

    response = client.post("/courses/1/groups/4231/register", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "conflict"
    assert "уже был указан ранее" in response.json()["detail"]["message"]


def test_get_course(monkeypatch):
    """
    Тест-заглушка для проверки работы эндпоинта получения курса с monkeypatch-авторизацией.
    """
    monkeypatch.setattr("main.check_auth", lambda req: True)
    response = client.get("/courses/1")
    assert response.status_code in (200, 404)


@patch("main.COURSES_DIR", "/fake/path")
@patch("main.os.listdir", return_value=["test_course.yaml"])
@patch("builtins.open", new_callable=mock_open, read_data='''\
course:
  name: "Test Course"
  semester: "Spring 2025"
  logo: "/assets/logo.png"
  email: "test@example.com"
''')
def test_get_course_by_id(mock_open_file, mock_listdir):
    """
    Проверяет, что при подмене содержимого courses возвращается корректный курс.
    """
    response = client.get("/courses/1")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert data["name"] == "Test Course"
    assert data["semester"] == "Spring 2025"
    assert data["email"] == "test@example.com"


def test_get_course_by_id_not_found():
    """
    Проверяет, что при несуществующем ID курса возвращается статус 404.
    """
    response = client.get("/courses/999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Course not found"


def test_register_student_missing_field():
    """
    Проверяет, что при отсутствии обязательного поля (github) возвращается ошибка валидации 422.
    """
    payload = {
        "name": "Иван",
        "surname": "Иванов"
        # нет поля github
    }
    response = client.post("/courses/1/groups/4231/register", json=payload)
    assert response.status_code == 422


@patch("main.COURSES_DIR", "/fake/path")
@patch("main.os.listdir", return_value=["broken.yaml"])
@patch("builtins.open", new_callable=mock_open, read_data='not: valid: yaml: [')
def test_get_courses_invalid_yaml(mock_open_file, mock_listdir):
    """
    Проверяет, что при наличии повреждённого YAML-файла в директории, он игнорируется,
    и возвращается пустой список.
    """
    response = client.get("/courses")
    assert response.status_code == 200
    assert response.json() == []

