from fastapi import FastAPI, Request, Response, HTTPException
from fastapi import UploadFile, File, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any
import os
import yaml
import gspread
import requests
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv
from itsdangerous import TimestampSigner, BadSignature
import re
import pandas as pd
from io import BytesIO
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- Helper functions for uploading scores ---
def normalize_whitespace(s: str) -> str:
    return " ".join(str(s).strip().split())

def parse_scores(file: UploadFile) -> pd.DataFrame:
    filename = file.filename.lower()
    content = file.file.read()
    if filename.endswith(".csv"):
        df = pd.read_csv(BytesIO(content))
    elif filename.endswith((".xls", ".xlsx")):
        df = pd.read_excel(BytesIO(content))
    else:
        raise HTTPException(400, "Неподдерживаемый формат: .csv или .xlsx")

    for col in ("Фамилия", "Имя", "Отчество или второе имя"):
        if col not in df.columns:
            raise HTTPException(400, f"Отсутствует колонка '{col}'")

    df["Фамилия"] = df["Фамилия"].astype(str).apply(normalize_whitespace)
    df["Имя"] = df["Имя"].astype(str).apply(normalize_whitespace)
    df["Отчество или второе имя"] = (
        df["Отчество или второе имя"].fillna("").astype(str).apply(normalize_whitespace)
    )
    df["full_name"] = (
        df["Фамилия"]
        + " "
        + df["Имя"]
        + df["Отчество или второе имя"].apply(lambda m: f" {m}" if m else "")
    )

    score_col = next(
        (c for c in df.columns if c.strip().lower().startswith("оценка")), None
    )
    if not score_col:
        raise HTTPException(400, "Отсутствует столбец, начинающийся на 'Оценка'")

    raw = df[score_col].astype(str).str.replace(",", ".", regex=False)
    df["score"] = pd.to_numeric(raw, errors="coerce").fillna(0)
    return df[["full_name", "score"]]

load_dotenv()

# --- Google Sheets API ---
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
KEY_FILE = os.getenv("CREDENTIALS_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if not KEY_FILE:
    raise RuntimeError("CREDENTIALS_FILE or GOOGLE_APPLICATION_CREDENTIALS must be set")

_creds = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
_sheets_service = build("sheets", "v4", credentials=_creds).spreadsheets()

def get_sheet_titles(spreadsheet_id: str) -> List[str]:
    meta = _sheets_service.get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]

def get_sheet_values(spreadsheet_id: str, rng: str) -> List[List[Any]]:
    res = _sheets_service.values().get(spreadsheetId=spreadsheet_id, range=rng).execute()
    return res.get("values", [])

def batch_update(spreadsheet_id: str, data: List[Dict[str, Any]]) -> None:
    body = {"valueInputOption": "RAW", "data": data}
    _sheets_service.values().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()

app = FastAPI()
COURSES_DIR = "courses"
CREDENTIALS_FILE = "credentials.json"  # Файл с учетными данными Google API
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ADMIN_LOGIN = os.getenv("ADMIN_LOGIN")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешить запросы с любых источников
    allow_credentials=True,
    allow_methods=["*"],  # Разрешить все HTTP-методы
    allow_headers=["*"],  # Разрешить все заголовки
)
signer = TimestampSigner(SECRET_KEY)

class AuthRequest(BaseModel):
    login: str
    password: str


class StudentRegistration(BaseModel):
    name: str = Field(..., min_length=1)
    surname: str = Field(..., min_length=1)
    patronymic: str = ""
    github: str = Field(..., min_length=1)


@app.get("/")
async def read_index():
    return FileResponse("dist/index.html")

@app.post("/api/admin/login")
def admin_login(data: AuthRequest, response: Response):
    if data.login == ADMIN_LOGIN and data.password == ADMIN_PASSWORD:
        token = signer.sign(data.login.encode()).decode()
        response.set_cookie(
            key="admin_session",
            value=token,
            httponly=True,
            max_age=3600,
            path="/",
            secure=False
        )
        return {"authenticated": True}
    raise HTTPException(status_code=401, detail="Неверный логин или пароль")

@app.get("/api/admin/check-auth")
def check_auth(request: Request):
    cookie = request.cookies.get("admin_session")
    if not cookie:
        raise HTTPException(status_code=401, detail="Нет сессии")

    try:
        login = signer.unsign(cookie, max_age=3600).decode()
    except BadSignature:
        raise HTTPException(status_code=401, detail="Невалидная или просроченная сессия")

    if login != ADMIN_LOGIN:
        raise HTTPException(status_code=401, detail="Невалидная сессия")

    return {"authenticated": True}

@app.post("/api/admin/logout")
def logout(response: Response):
    response.delete_cookie("admin_session", path="/")
    return {"message": "Logged out"}


@app.get("/courses")
def get_courses():
    courses = []
    for index, filename in enumerate(sorted(os.listdir(COURSES_DIR)), start=1):
        file_path = os.path.join(COURSES_DIR, filename)
        if filename.endswith(".yaml") and os.path.isfile(file_path):
            with open(file_path, "r", encoding="utf-8") as file:
                try:
                    data = yaml.safe_load(file)
                except yaml.YAMLError as e:
                    print(f"Ошибка при разборе YAML в {filename}: {e}")
                    continue

                if not isinstance(data, dict) or "course" not in data:
                    print(f"Пропускаем файл {filename}: неверная структура")
                    continue

                course_info = data["course"]
                courses.append({
                    "id": str(index),
                    "name": course_info.get("name", "Unknown"),
                    "semester": course_info.get("semester", "Unknown"),
                    "logo": course_info.get("logo", "/assets/default.png"),
                    "email": course_info.get("email", ""),
                })
    return courses


def parse_lab_id(lab_id: str) -> int:
    match = re.search(r"\d+", lab_id)
    if not match:
        raise HTTPException(status_code=400, detail="Некорректный lab_id")
    return int(match.group(0))

@app.get("/courses/{course_id}")
def get_course(course_id: str):
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Course not found")

    file_path = os.path.join(COURSES_DIR, filename)
    with open(file_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
        course_info = data.get("course", {})
        return {
            "id": course_id,
            "config": filename,
            "name": course_info.get("name", "Unknown"),
            "semester": course_info.get("semester", "Unknown"),
            "email": course_info.get("email", "Unknown"),
            "github-organization": course_info.get("github", {}).get("organization", "Unknown"),
            "google-spreadsheet": course_info.get("google", {}).get("spreadsheet", "Unknown"),
        }

@app.delete("/courses/{course_id}")
def delete_course(course_id: str):
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Курс не найден")

    file_path = os.path.join(COURSES_DIR, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        return {"message": "Курс успешно удален"}
    else:
        raise HTTPException(status_code=404, detail="Файл курса не найден")


class EditCourseRequest(BaseModel):
    content: str


@app.get("/courses/{course_id}/edit")
def edit_course_get(course_id: str):
    """Получить YAML содержимое курса для редактирования"""
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Курс не найден")

    file_path = os.path.join(COURSES_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл курса не найден")

    with open(file_path, "r", encoding="utf-8") as file:
        content = file.read()

    return {"filename": filename, "content": content}


@app.put("/courses/{course_id}/edit")
def edit_course_put(course_id: str, data: EditCourseRequest):
    """Сохранить изменения в YAML файле курса"""
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Курс не найден")

    file_path = os.path.join(COURSES_DIR, filename)


    try:
        yaml.safe_load(data.content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Ошибка в YAML формате: {str(e)}")

    with open(file_path, "w", encoding="utf-8") as file:
        file.write(data.content)

    return {"message": "Изменения успешно сохранены"}


@app.get("/courses/{course_id}/groups")
def get_course_groups(course_id: str):
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Course not found")

    file_path = os.path.join(COURSES_DIR, filename)
    with open(file_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
        course_info = data.get("course", {})
        spreadsheet_id = course_info.get("google", {}).get("spreadsheet")
        info_sheet = course_info.get("google", {}).get("info-sheet")

    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="Spreadsheet ID not found in course config")


    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
        sheet_names = [sheet.title for sheet in spreadsheet.worksheets() if sheet.title != info_sheet]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch sheets: {str(e)}")

    return sheet_names


@app.get("/courses/{course_id}/groups/{group_id}/labs")
def get_course_labs(course_id: str, group_id: str):
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Course not found")

    file_path = os.path.join(COURSES_DIR, filename)
    with open(file_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
        course_info = data.get("course", {})
        spreadsheet_id = course_info.get("google", {}).get("spreadsheet")
        labs = [lab["short-name"] for lab in course_info.get("labs", {}).values() if "short-name" in lab]

    if not spreadsheet_id or not labs:
        raise HTTPException(status_code=400, detail="Missing spreadsheet ID or labs in config")


    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
        sheet = spreadsheet.worksheet(group_id)


        headers = sheet.row_values(2)[2:]
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Group not found in spreadsheet: {str(e)}")

    available_labs = [lab for lab in labs if lab in headers]
    return available_labs


@app.post("/courses/{course_id}/groups/{group_id}/register")
def register_student(course_id: str, group_id: str, student: StudentRegistration):
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Course not found")

    file_path = os.path.join(COURSES_DIR, filename)
    with open(file_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
        course_info = data.get("course", {})
        spreadsheet_id = course_info.get("google", {}).get("spreadsheet")
        student_col = course_info.get("google", {}).get("student-name-column", 2)

    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="Spreadsheet ID not found in course config")


    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
        sheet = spreadsheet.worksheet(group_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Group not found in spreadsheet")

    full_name = f"{student.surname} {student.name} {student.patronymic}".strip()


    student_list = sheet.col_values(student_col)[2:]

    if full_name not in student_list:
        raise HTTPException(status_code=404, detail={"message": "Студент не найден"})

    row_idx = student_list.index(full_name) + 3


    header_row = sheet.row_values(1)
    try:
        github_col_idx = header_row.index("GitHub") + 1
    except ValueError:
        raise HTTPException(status_code=400, detail="Столбец 'GitHub' не найден в таблице")


    try:
        github_response = requests.get(f"https://api.github.com/users/{student.github}")
        if github_response.status_code != 200:
            raise HTTPException(status_code=404, detail={"message": "Пользователь GitHub не найден"})
    except Exception:
        raise HTTPException(status_code=500, detail="Ошибка проверки GitHub пользователя")

    existing_github = sheet.cell(row_idx, github_col_idx).value

    if not existing_github:
        sheet.update_cell(row_idx, github_col_idx, student.github)
        return {"status": "registered", "message": "Аккаунт GitHub успешно задан"}

    if existing_github == student.github:
        return {
            "status": "already_registered",
            "message": "Этот аккаунт GitHub уже был указан ранее для этого же студента"
        }

    raise HTTPException(status_code=409, detail={
        "status": "conflict",
        "message": "Аккаунт GitHub уже был указан ранее. Для изменения аккаунта обратитесь к преподавателю"
    })


def normalize_lab_id(lab_id: str) -> str:
    """Возвращает нормализованную строку вида ЛР1, ЛР2 и т.д."""
    number = parse_lab_id(lab_id)
    return f"ЛР{number}"


class GradeRequest(BaseModel):
    github: str = Field(..., min_length=1)

@app.post("/courses/{course_id}/groups/{group_id}/labs/{lab_id}/grade")
def grade_lab(course_id: str, group_id: str, lab_id: str, request: GradeRequest):
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Course not found")

    file_path = os.path.join(COURSES_DIR, filename)
    with open(file_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
        course_info = data.get("course", {})
        org = course_info.get("github", {}).get("organization")
        spreadsheet_id = course_info.get("google", {}).get("spreadsheet")
        student_col = course_info.get("google", {}).get("student-name-column", 2)
        lab_offset = course_info.get("google", {}).get("lab-column-offset", 1)

    labs = course_info.get("labs", {})
    normalized_lab_id = normalize_lab_id(lab_id)
    lab_config = labs.get(normalized_lab_id, {})
    repo_prefix = lab_config.get("github-prefix")

    if not all([org, spreadsheet_id, repo_prefix]):
        raise HTTPException(status_code=400, detail="Missing course configuration")

    username = request.github
    repo_name = f"{repo_prefix}-{username}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    test_file_url = f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py"
    if requests.get(test_file_url, headers=headers).status_code != 200:
        raise HTTPException(status_code=400, detail="⚠️ test_main.py не найден в репозитории")

    workflows_url = f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows"
    if requests.get(workflows_url, headers=headers).status_code != 200:
        raise HTTPException(status_code=400, detail="⚠️ Папка .github/workflows не найдена. CI не настроен")

    commits_url = f"https://api.github.com/repos/{org}/{repo_name}/commits"
    commits_resp = requests.get(commits_url, headers=headers)
    if commits_resp.status_code != 200 or not commits_resp.json():
        raise HTTPException(status_code=404, detail="Нет коммитов в репозитории")

    latest_sha = commits_resp.json()[0]["sha"]

    commit_url = f"https://api.github.com/repos/{org}/{repo_name}/commits/{latest_sha}"
    commit_files = requests.get(commit_url, headers=headers).json().get("files", [])
    for f in commit_files:
        if f["filename"] == "test_main.py" and f["status"] in ("removed", "modified"):
            raise HTTPException(status_code=403, detail="🚨 Нельзя изменять test_main.py")
        if f["filename"].startswith("tests/") and f["status"] in ("removed", "modified"):
            raise HTTPException(status_code=403, detail="🚨 Нельзя изменять папку tests/")

    check_url = f"https://api.github.com/repos/{org}/{repo_name}/commits/{latest_sha}/check-runs"
    check_resp = requests.get(check_url, headers=headers)
    if check_resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Проверки CI не найдены")

    check_runs = check_resp.json().get("check_runs", [])
    if not check_runs:
        return {"status": "pending", "message": "Нет активных CI-проверок ⏳"}

    summary = []
    passed_count = 0

    for check in check_runs:
        name = check.get("name", "Unnamed check")
        conclusion = check.get("conclusion")
        html_url = check.get("html_url")
        if conclusion == "success":
            emoji = "✅"
            passed_count += 1
        elif conclusion == "failure":
            emoji = "❌"
        else:
            emoji = "⏳"
        summary.append(f"{emoji} {name} — {html_url}")

    total_checks = len(check_runs)
    result_string = f"{passed_count}/{total_checks} тестов пройдено"

    final_result = "✓" if passed_count == total_checks else "✗"

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)

    try:
        sheet = client.open_by_key(spreadsheet_id).worksheet(group_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Группа не найдена в Google Таблице")

    header_row = sheet.row_values(1)
    try:
        github_col_idx = header_row.index("GitHub") + 1
    except ValueError:
        raise HTTPException(status_code=400, detail="Столбец 'GitHub' не найден")

    github_values = sheet.col_values(github_col_idx)[2:]
    if username not in github_values:
        raise HTTPException(status_code=404, detail="GitHub логин не найден в таблице. Зарегистрируйтесь.")

    lab_number = parse_lab_id(lab_id)
    row_idx = github_values.index(username) + 3
    lab_col = student_col + lab_number + lab_offset
    sheet.update_cell(row_idx, lab_col, final_result)

    return {
        "status": "updated",
        "result": final_result,
        "message": f"Результат CI: {'✅ Все проверки пройдены' if final_result == '✓' else '❌ Обнаружены ошибки'}",
        "passed": result_string,
        "checks": summary
    }




@app.post("/courses/upload")
async def upload_course(file: UploadFile = File(...)):
    if not file.filename.endswith(".yaml") and not file.filename.endswith(".yml"):
        raise HTTPException(status_code=400, detail="Только YAML файлы разрешены")
    file_location = os.path.join(COURSES_DIR, file.filename)

    if os.path.exists(file_location):
        raise HTTPException(status_code=400, detail="Файл с таким именем уже существует")

    content = await file.read()
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail="Некорректный YAML файл")

    with open(file_location, "wb") as f:
        f.write(content)

    return {"detail": "Курс успешно загружен"}


# --- Endpoint: Upload scores to Google Sheets ---
class MissingResponse(BaseModel):
    missing_students: List[str]

@app.post(
    "/subjects/{subject_id}/upload_scores",
    response_model=MissingResponse,
    summary="Загрузить оценки из файла в Google Sheets"
)
async def upload_scores(
    subject_id: str,
    column: str = Query(..., description="Название столбца для оценок"),
    file: UploadFile = File(...),
):
    df = parse_scores(file)
    sheets = get_sheet_titles(subject_id)
    if not sheets:
        raise HTTPException(404, "В таблице нет листов")
    sheet_contexts = []
    for sheet in sheets:
        headers = get_sheet_values(subject_id, f"{sheet}!1:1")
        if not headers or not headers[0]:
            continue
        hdr = headers[0]
        col_letter = None
        if column in hdr:
            idx = hdr.index(column)
            col_letter = chr(ord("A") + idx)
        names = get_sheet_values(subject_id, f"{sheet}!B2:B1000")
        name_map = {
            name.strip(): i + 2
            for i, row in enumerate(names)
            if (name := (row[0] if row else "")).strip()
        }
        sheet_contexts.append((sheet, col_letter, name_map))
    updates, missing = [], []
    for full_name, score in zip(df["full_name"], df["score"]):
        placed = False
        for sheet, col_letter, name_map in sheet_contexts:
            if col_letter and full_name in name_map:
                updates.append({
                    "range": f"{sheet}!{col_letter}{name_map[full_name]}",
                    "values": [[score]]
                })
                placed = True
                break
        if not placed:
            missing.append(full_name)
    if updates:
        batch_update(subject_id, updates)
    return {"missing_students": missing}
