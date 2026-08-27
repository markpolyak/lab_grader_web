from fastapi import FastAPI, Request, Response, status, HTTPException
import os
import yaml
import gspread
import requests
import hashlib
import base64
import httpx
import asyncio
from oauth2client.service_account import ServiceAccountCredentials
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import UploadFile, File
from dotenv import load_dotenv
from itsdangerous import TimestampSigner, BadSignature
import re

load_dotenv()
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

class StudentRegistration(BaseModel):
    name: str = Field(..., min_length=1)
    surname: str = Field(..., min_length=1)
    patronymic: str = ""
    github: str = Field(..., min_length=1)
    
class StudentIdentity(BaseModel):
    name: str = Field(..., min_length=1)
    surname: str = Field(..., min_length=1)
    patronymic: str = ""    



async def fetch_file_content(client, repo, path, ref, headers):
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    r = await client.get(url, headers=headers)
    if r.status_code == 200:
        content = r.json().get("content", "")
        return base64.b64decode(content)
    return None

def get_sha256(content):
    return hashlib.sha256(content).hexdigest()

async def list_all_files(client, repo, path, ref, headers):
    files = []
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    r = await client.get(url, headers=headers)
    if r.status_code != 200:
        return files
    for item in r.json():
        if item["type"] == "file":
            files.append(item["path"])
        elif item["type"] == "dir":
            nested = await list_all_files(client, repo, item["path"], ref, headers)
            files.extend(nested)
    return files



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



@app.post("/courses/{course_id}/groups/{group_id}/check-student", status_code=200)
def check_student_exists(course_id: str, group_id: str, student: StudentIdentity):
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Курс не найден")

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

    if full_name in student_list:
        return {"exists": True}
    else:
        raise HTTPException(status_code=404, detail="Студент не найден")



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



class GradeRequest(BaseModel):
    github: str = Field(..., min_length=1)



@app.post("/courses/{course_id}/groups/{group_id}/labs/{lab_id}/grade")
async def grade_lab(course_id: str, group_id: str, lab_id: str, request: GradeRequest):
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

    lab_config = course_info.get("labs", {}).get(lab_id, {})
    repo_prefix = lab_config.get("github-prefix")

    if not all([org, spreadsheet_id, repo_prefix]):
        raise HTTPException(status_code=400, detail="Missing course configuration")

    username = request.github
    repo_name = f"{repo_prefix}-{username}"
    repo = f"{org}/{repo_name}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        commits_url = f"https://api.github.com/repos/{repo}/commits?per_page=100"
        commits_resp = await client.get(commits_url, headers=headers)
        if commits_resp.status_code != 200:
            raise HTTPException(404, "Не удалось получить список коммитов")
        commits = commits_resp.json()
        if not commits:
            raise HTTPException(404, "Нет коммитов в репозитории")
        first_commit_sha = commits[-1]["sha"]

        orig_test = await fetch_file_content(client, repo, "test_main.py", first_commit_sha, headers)
        curr_test = await fetch_file_content(client, repo, "test_main.py", "HEAD", headers)
        if orig_test:
            if not curr_test or get_sha256(curr_test) != get_sha256(orig_test):
                raise HTTPException(403, "🚨 test_main.py был изменён или удалён")

        test_files = await list_all_files(client, repo, "tests", first_commit_sha, headers)
        tasks = []
        for path in test_files:
            tasks.append(fetch_file_content(client, repo, path, first_commit_sha, headers))
            tasks.append(fetch_file_content(client, repo, path, "HEAD", headers))
        file_contents = await asyncio.gather(*tasks)
        for i in range(0, len(file_contents), 2):
            orig, curr = file_contents[i], file_contents[i + 1]
            if not curr or get_sha256(curr) != get_sha256(orig):
                raise HTTPException(403, f"🚨 Изменён файл в папке tests/")

        wf_files = await list_all_files(client, repo, ".github/workflows", first_commit_sha, headers)
        tasks = []
        for path in wf_files:
            tasks.append(fetch_file_content(client, repo, path, first_commit_sha, headers))
            tasks.append(fetch_file_content(client, repo, path, "HEAD", headers))
        file_contents = await asyncio.gather(*tasks)
        for i in range(0, len(file_contents), 2):
            orig, curr = file_contents[i], file_contents[i + 1]
            if not curr or get_sha256(curr) != get_sha256(orig):
                raise HTTPException(403, f"🚨 Изменён файл в папке .github/workflows/")
    
    latest_sha = commits[0]["sha"]
    
    # Получение check-runs
    check_url = f"https://api.github.com/repos/{org}/{repo_name}/commits/{latest_sha}/check-runs"
    check_resp = requests.get(check_url, headers=headers)
    if check_resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Проверки CI не найдены")

    check_runs = check_resp.json().get("check_runs", [])
    if not check_runs:
        raise HTTPException(status_code=404, detail="Нет коммитов в репозитории")

    # Подсчёт успешных проверок
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

    # Обновление таблицы
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
    try:
        row_idx = github_values.index(username) + 3
    except ValueError:
        raise HTTPException(status_code=404, detail="GitHub логин не найден в таблице")

    lab_number = int("".join(filter(str.isdigit, lab_id)))
    lab_col = student_col + lab_number + lab_offset
    
    if final_result == "✓":
        sheet.update_cell(row_idx, lab_col, "✓")

    return {
        "status": "updated",
        "result": final_result,
        "message": f"Результат CI: {'✅ Все проверки пройдены' if final_result == '✓' else '❌ Обнаружены ошибки'}",
        "passed": result_string,
        "checks": summary
    }
    
@app.post("/courses/{course_id}/groups/{group_id}/github")
def get_github_from_sheet(course_id: str, group_id: str, student: StudentIdentity):
    files = sorted([f for f in os.listdir(COURSES_DIR) if f.endswith(".yaml")])
    try:
        filename = files[int(course_id) - 1]
    except (IndexError, ValueError):
        raise HTTPException(status_code=404, detail="Курс не найден")

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
        raise HTTPException(status_code=404, detail="Студент не найден в таблице")

    row_idx = student_list.index(full_name) + 3

    header_row = sheet.row_values(1)
    try:
        github_col_idx = header_row.index("GitHub") + 1
    except ValueError:
        raise HTTPException(status_code=400, detail="Столбец 'GitHub' не найден")

    github_value = sheet.cell(row_idx, github_col_idx).value
    if not github_value:
        raise HTTPException(status_code=404, detail="GitHub-ник не найден в таблице")

    return {"github": github_value}