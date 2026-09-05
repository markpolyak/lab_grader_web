from fastapi import FastAPI, Request, Response, HTTPException, Depends, BackgroundTasks
from fastapi.staticfiles import StaticFiles
import os
import yaml
import gspread
import requests
from oauth2client.service_account import ServiceAccountCredentials
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import UploadFile, File
from dotenv import load_dotenv
from itsdangerous import TimestampSigner, BadSignature
import re
import json
import base64
import logging
from datetime import datetime
from urllib.parse import urlencode
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from grading import (
    LabGrader,
    GitHubClient,
    GradeStatus,
    RepoProvisioner,
    ProvisionStatus,
    find_student_row,
    find_lab_column_by_name,
    calculate_lab_column,
    can_overwrite_cell,
    get_deadline_from_sheet,
    get_student_order,
    calculate_expected_taskid,
    get_decimal_separator,
    format_grade_with_score,
    format_score,
    PropagateSetupError,
    dry_run_propagation,
    try_start_propagate_job,
    run_propagation,
    get_propagate_job,
)

# Configure logging to both file and console
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Set log level from environment (default: INFO)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, LOG_LEVEL, logging.INFO)

# Create formatters
log_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Root logger configuration
root_logger = logging.getLogger()
root_logger.setLevel(log_level)

# Console handler (for docker logs)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# File handler (persistent logs)
log_file = os.path.join(LOG_DIR, "labgrader.log")
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

# Configure uvicorn loggers to use the same format
uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.handlers = [console_handler, file_handler]

uvicorn_error = logging.getLogger("uvicorn.error")
uvicorn_error.handlers = [console_handler, file_handler]

uvicorn_main = logging.getLogger("uvicorn")
uvicorn_main.handlers = [console_handler, file_handler]

logger = logging.getLogger(__name__)
logger.info(f"Logging initialized. Log file: {log_file}")

load_dotenv()
app = FastAPI()
COURSES_DIR = "courses"
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")  # Файл с учетными данными Google API
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ADMIN_LOGIN = os.getenv("ADMIN_LOGIN")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key")

# GitHub OAuth App for the /join student repo creation flow (see docs/REPO_GENERATION_PLAN.md).
# Optional: existing deployments that don't use this feature can leave these unset;
# the /join endpoints then respond with a clear 503 instead of the app failing to start.
GITHUB_OAUTH_CLIENT_ID = os.getenv("GITHUB_OAUTH_CLIENT_ID")
GITHUB_OAUTH_CLIENT_SECRET = os.getenv("GITHUB_OAUTH_CLIENT_SECRET")
# Explicit override for the OAuth callback URL registered with the OAuth App.
# Falls back to request.base_url + "join/callback" when unset (see _oauth_redirect_uri).
GITHUB_OAUTH_CALLBACK_URL = os.getenv("GITHUB_OAUTH_CALLBACK_URL")
# Where to send the student's browser after the /join/callback finishes (the frontend's
# /join/:courseId/:labId route, which renders the "after" state from the query params).
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8080")
# Max age (seconds) for the signed OAuth `state` param - see docs/REPO_GENERATION_PLAN.md §3.3.
JOIN_STATE_MAX_AGE = 600

# Rate limiting configuration
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Проверка обязательных переменных окружения
if not ADMIN_LOGIN or not ADMIN_PASSWORD:
    raise RuntimeError(
        "ADMIN_LOGIN и ADMIN_PASSWORD должны быть установлены в переменных окружения. "
        "Приложение не может быть запущено без корректной конфигурации безопасности."
    )

if not GITHUB_TOKEN:
    raise RuntimeError(
        "GITHUB_TOKEN должен быть установлен в переменных окружения. "
        "Приложение требует доступ к GitHub API."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешить запросы с любых источников
    allow_credentials=True,
    allow_methods=["*"],  # Разрешить все HTTP-методы
    allow_headers=["*"],  # Разрешить все заголовки
)
signer = TimestampSigner(SECRET_KEY)

# Course index management
INDEX_FILE = os.path.join(COURSES_DIR, "index.yaml")

def load_course_index():
    """Load and validate course index file"""
    if not os.path.exists(INDEX_FILE):
        raise RuntimeError(f"Course index file not found: {INDEX_FILE}")

    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        index_data = yaml.safe_load(f)

    if not isinstance(index_data, dict) or "courses" not in index_data:
        raise RuntimeError("Invalid index.yaml structure: missing 'courses' key")

    return index_data

def validate_course_index():
    """Validate that index.yaml is synchronized with course files"""
    try:
        index_data = load_course_index()
    except Exception as e:
        print(f"❌ Failed to load course index: {e}")
        return False

    courses = index_data.get("courses", [])

    # Collect indexed files
    indexed_files = {entry["file"] for entry in courses if "file" in entry}

    # Collect actual files
    actual_files = {
        f for f in os.listdir(COURSES_DIR)
        if f.endswith(".yaml") and f != "index.yaml" and os.path.isfile(os.path.join(COURSES_DIR, f))
    }

    # Check for missing files
    missing_files = indexed_files - actual_files
    if missing_files:
        print(f"❌ ERROR: Files referenced in index but not found: {missing_files}")
        return False

    # Check for orphaned files
    orphaned_files = actual_files - indexed_files
    if orphaned_files:
        print(f"⚠️  WARNING: Course files not in index (will be ignored): {orphaned_files}")

    # Check for duplicate IDs
    ids = [entry.get("id") for entry in courses if "id" in entry]
    if len(ids) != len(set(ids)):
        duplicates = {x for x in ids if ids.count(x) > 1}
        print(f"❌ ERROR: Duplicate course IDs in index: {duplicates}")
        return False

    # Validate each indexed file can be loaded
    for entry in courses:
        file_path = os.path.join(COURSES_DIR, entry["file"])
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if not isinstance(data, dict) or "course" not in data:
                    print(f"❌ ERROR: Invalid course structure in {entry['file']}")
                    return False
        except Exception as e:
            print(f"❌ ERROR: Failed to load {entry['file']}: {e}")
            return False

    print(f"✅ Course index validated successfully ({len(courses)} courses)")
    return True

def get_course_by_id(course_id: str):
    """Get course configuration by ID from index"""
    index_data = load_course_index()

    # Find course entry in index
    course_entry = None
    for entry in index_data.get("courses", []):
        if entry.get("id") == course_id:
            course_entry = entry
            break

    if not course_entry:
        raise HTTPException(status_code=404, detail="Course not found")

    # Load course file
    file_path = os.path.join(COURSES_DIR, course_entry["file"])
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Course file not found")

    with open(file_path, "r", encoding="utf-8") as f:
        course_data = yaml.safe_load(f)

    # Merge index metadata with course data
    course_info = course_data.get("course", {})
    course_info["_meta"] = {
        "status": course_entry.get("status", "active"),
        "priority": course_entry.get("priority", 0),
        "featured": course_entry.get("featured", False),
        "filename": course_entry["file"]
    }

    return course_info

# Validate index on startup
print("Validating course index...")
if not validate_course_index():
    raise RuntimeError("Course index validation failed. Please fix index.yaml before starting.")

# Mount static files for course logos
LOGOS_DIR = os.path.join(COURSES_DIR, "logos")
if os.path.exists(LOGOS_DIR):
    app.mount("/courses/logos", StaticFiles(directory=LOGOS_DIR), name="course_logos")
    print(f"✅ Course logos available at /courses/logos")
else:
    print(f"⚠️  Warning: Logos directory not found at {LOGOS_DIR}")

class AuthRequest(BaseModel):
    login: str
    password: str


class StudentRegistration(BaseModel):
    name: str = Field(..., min_length=1)
    surname: str = Field(..., min_length=1)
    patronymic: str = ""
    github: str = Field(..., min_length=1)


@app.get("/")
@limiter.limit("100/minute")
async def read_index(request: Request):
    return FileResponse("dist/index.html")

@app.post("/admin/login")
@limiter.limit("5/minute")
def admin_login(request: Request, data: AuthRequest, response: Response):
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

def require_admin(request: Request) -> str:
    """
    Reusable auth dependency for admin-only endpoints (see issue #52).

    Same check /admin/check-auth used to do inline - factored out so it can
    be attached via Depends() to every admin route instead of relying on the
    frontend's ProtectedRoute alone.
    """
    cookie = request.cookies.get("admin_session")
    if not cookie:
        raise HTTPException(status_code=401, detail="Нет сессии")
    try:
        login = signer.unsign(cookie, max_age=3600).decode()
    except BadSignature:
        raise HTTPException(status_code=401, detail="Невалидная или просроченная сессия")
    if login != ADMIN_LOGIN:
        raise HTTPException(status_code=401, detail="Невалидная сессия")
    return login


@app.get("/admin/check-auth")
@limiter.limit("30/minute")
def check_auth(request: Request, admin: str = Depends(require_admin)):
    return {"authenticated": True}

@app.post("/admin/logout")
@limiter.limit("30/minute")
def logout(request: Request, response: Response):
    response.delete_cookie("admin_session", path="/")
    return {"message": "Logged out"}


@app.get("/courses")
@limiter.limit("100/minute")
def get_courses(request: Request, status: str = "active"):
    """
    Get courses filtered by status

    Args:
        status: Filter by status (active, archived, all). Default: active
    """
    index_data = load_course_index()
    courses = []

    for entry in index_data.get("courses", []):
        course_status = entry.get("status", "active")

        # Filter by status
        if status != "all" and course_status != status:
            continue

        # Load course file
        file_path = os.path.join(COURSES_DIR, entry["file"])
        if not os.path.exists(file_path):
            print(f"Warning: Course file {entry['file']} not found, skipping")
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Error parsing YAML in {entry['file']}: {e}")
            continue

        if not isinstance(data, dict) or "course" not in data:
            print(f"Skipping file {entry['file']}: invalid structure")
            continue

        course_info = data["course"]
        courses.append({
            "id": entry["id"],
            "name": course_info.get("name", "Unknown"),
            "university": course_info.get("university", ""),
            "semester": course_info.get("semester", "Unknown"),
            "logo": entry.get("logo", "/assets/default.png"),  # Logo from index, not course file
            "email": course_info.get("email", ""),
            "status": course_status,
            "priority": entry.get("priority", 0),
            "featured": entry.get("featured", False),
        })

    # Sort by priority (descending), then by name
    courses.sort(key=lambda x: (-x["priority"], x["name"]))

    return courses


def parse_lab_id(lab_id: str) -> int:
    match = re.search(r"\d+", lab_id)
    if not match:
        raise HTTPException(status_code=400, detail="Некорректный lab_id")
    return int(match.group(0))


def find_lab_config(labs: dict, lab_id: str) -> tuple[str, dict] | None:
    r"""
    Найти лабораторную по идентификатору, которым её назвал вызывающий.

    Одна и та же лаба адресуется тремя разными способами:
      - ключом из YAML ("01", "1") - URL /join и админки;
      - коротким именем ("ЛР0.1", "ЛР1") - интерфейс проверки, потому что
        GET /courses/{id}/groups/{gid}/labs отдаёт именно short-name;
      - произвольной строкой с числом ("lab3") - исторические вызовы.

    Сопоставление по одному лишь числу (прежнее `labs[str(parse_lab_id(...))]`)
    склеивает разные лабы: int("01") == 1 делает ключ "01" недостижимым, а
    re.search(r"\d+", "ЛР0.1") останавливается на "0", из-за чего ЛР0.1
    разрешалась в ЛР0 - чужой github-prefix и чужой столбец в таблице.

    Returns:
        (ключ в конфиге, конфиг лабы) или None, если лаба не найдена
    """
    if not isinstance(labs, dict):
        return None

    if lab_id in labs:
        return lab_id, labs[lab_id]

    for key, config in labs.items():
        if isinstance(config, dict) and config.get("short-name") == lab_id:
            return str(key), config

    # Запасной путь для строк, которые не являются ни ключом, ни коротким
    # именем ("lab3"). Неоднозначные случаи выше уже разобраны точным
    # совпадением, так что сюда доходят только однозначные.
    match = re.search(r"\d+", lab_id)
    if match:
        numeric_key = str(int(match.group(0)))
        if numeric_key in labs:
            return numeric_key, labs[numeric_key]

    return None

@app.get("/courses/{course_id}")
@limiter.limit("100/minute")
def get_course(request: Request, course_id: str):
    course_info = get_course_by_id(course_id)

    return {
        "id": course_id,
        "config": course_info["_meta"]["filename"],
        "name": course_info.get("name", "Unknown"),
        "university": course_info.get("university", ""),
        "semester": course_info.get("semester", "Unknown"),
        "email": course_info.get("email", "Unknown"),
        "github-organization": course_info.get("github", {}).get("organization", "Unknown"),
        "google-spreadsheet": course_info.get("google", {}).get("spreadsheet", "Unknown"),
        "status": course_info["_meta"]["status"],
        "priority": course_info["_meta"]["priority"],
    }

@app.delete("/courses/{course_id}")
@limiter.limit("20/minute")
def delete_course(request: Request, course_id: str, admin: str = Depends(require_admin)):
    """
    Mark course as hidden in index (soft delete)
    The course file is preserved in repository
    """
    index_data = load_course_index()

    # Find course in index
    course_found = False
    for entry in index_data.get("courses", []):
        if entry.get("id") == course_id:
            entry["status"] = "hidden"
            course_found = True
            break

    if not course_found:
        raise HTTPException(status_code=404, detail="Курс не найден")

    # Save updated index
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        yaml.dump(index_data, f, allow_unicode=True, sort_keys=False)

    return {"message": "Курс успешно скрыт (файл сохранен в репозитории)"}


class EditCourseRequest(BaseModel):
    content: str


@app.get("/courses/{course_id}/edit")
@limiter.limit("30/minute")
def edit_course_get(request: Request, course_id: str, admin: str = Depends(require_admin)):
    """Получить YAML содержимое курса для редактирования"""
    course_info = get_course_by_id(course_id)
    filename = course_info["_meta"]["filename"]

    file_path = os.path.join(COURSES_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл курса не найден")

    with open(file_path, "r", encoding="utf-8") as file:
        content = file.read()

    return {"filename": filename, "content": content}


@app.put("/courses/{course_id}/edit")
@limiter.limit("20/minute")
def edit_course_put(request: Request, course_id: str, data: EditCourseRequest, admin: str = Depends(require_admin)):
    """Сохранить изменения в YAML файле курса"""
    course_info = get_course_by_id(course_id)
    filename = course_info["_meta"]["filename"]

    file_path = os.path.join(COURSES_DIR, filename)

    try:
        yaml.safe_load(data.content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Ошибка в YAML формате: {str(e)}")

    with open(file_path, "w", encoding="utf-8") as file:
        file.write(data.content)

    return {"message": "Изменения успешно сохранены"}


@app.get("/courses/{course_id}/groups")
@limiter.limit("10/minute")
def get_course_groups(request: Request, course_id: str):
    course_info = get_course_by_id(course_id)
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
@limiter.limit("10/minute")
def get_course_labs(request: Request, course_id: str, group_id: str):
    course_info = get_course_by_id(course_id)
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
@limiter.limit("10/minute")
def register_student(request: Request, course_id: str, group_id: str, student: StudentRegistration):
    # Build full name first for consistent logging
    full_name = f"{student.surname} {student.name} {student.patronymic}".strip()

    logger.info(f"Registration attempt - Course: {course_id}, Group: {group_id}, Full name: '{full_name}', GitHub: {student.github}")
    logger.debug(f"Input data - Surname: '{student.surname}', Name: '{student.name}', Patronymic: '{student.patronymic}'")

    try:
        course_info = get_course_by_id(course_id)
        spreadsheet_id = course_info.get("google", {}).get("spreadsheet")
        # Get column index (0-based in config, convert to 1-based for gspread)
        student_col_config = course_info.get("google", {}).get("student-name-column", 1)
        student_col = student_col_config + 1  # gspread uses 1-based indexing

        logger.debug(f"student-name-column from config: {student_col_config} (0-based) -> gspread column: {student_col} (1-based)")

        if not spreadsheet_id:
            logger.error(f"Spreadsheet ID not found for course {course_id}")
            raise HTTPException(status_code=400, detail="Spreadsheet ID not found in course config")

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        client = gspread.authorize(creds)

        try:
            spreadsheet = client.open_by_key(spreadsheet_id)
            sheet = spreadsheet.worksheet(group_id)
        except Exception as e:
            logger.error(f"Group '{group_id}' not found in spreadsheet for course {course_id}: {str(e)}")
            raise HTTPException(status_code=404, detail="Group not found in spreadsheet")

        logger.info(f"Searching for student '{full_name}' in column {student_col}")

        student_list = sheet.col_values(student_col)[2:]
        logger.info(f"Found {len(student_list)} students in spreadsheet")
        logger.debug(f"Student list: {student_list[:5]}..." if len(student_list) > 5 else f"Student list: {student_list}")

        # Check for exact match
        if full_name not in student_list:
            logger.warning(f"Student '{full_name}' not found in group {group_id}")
            # Log similarity for debugging
            similar = [s for s in student_list if student.surname in s]
            if similar:
                logger.info(f"Found {len(similar)} students with matching surname: {similar}")
            logger.debug(f"Search string length: {len(full_name)}, repr: {repr(full_name)}")
            if student_list:
                logger.debug(f"First student in list - length: {len(student_list[0])}, repr: {repr(student_list[0])}")
            raise HTTPException(status_code=404, detail="Студент не найден")

        row_idx = student_list.index(full_name) + 3
        logger.info(f"Student found at row {row_idx}")

        header_row = sheet.row_values(1)
        try:
            github_col_idx = header_row.index("GitHub") + 1
        except ValueError:
            logger.error(f"'GitHub' column not found in spreadsheet headers")
            raise HTTPException(status_code=400, detail="Столбец 'GitHub' не найден в таблице")

        try:
            github_response = requests.get(f"https://api.github.com/users/{student.github}")
            if github_response.status_code != 200:
                logger.warning(f"GitHub user '{student.github}' not found (status: {github_response.status_code})")
                raise HTTPException(status_code=404, detail="Пользователь GitHub не найден")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error checking GitHub user '{student.github}': {str(e)}")
            raise HTTPException(status_code=500, detail="Ошибка проверки GitHub пользователя")

        existing_github = sheet.cell(row_idx, github_col_idx).value

        if not existing_github:
            sheet.update_cell(row_idx, github_col_idx, student.github)
            logger.info(f"Successfully registered GitHub '{student.github}' for student '{full_name}'")
            return {"status": "registered", "message": "Аккаунт GitHub успешно задан"}

        if existing_github == student.github:
            logger.info(f"Student '{full_name}' already registered with GitHub '{student.github}'")
            return {
                "status": "already_registered",
                "message": "Этот аккаунт GitHub уже был указан ранее для этого же студента"
            }

        # Конфликт: студент пытается указать другой аккаунт
        logger.warning(f"GitHub conflict for '{full_name}': existing='{existing_github}', attempted='{student.github}'")
        raise HTTPException(
            status_code=409,
            detail="Аккаунт GitHub уже был указан ранее. Для изменения аккаунта обратитесь к преподавателю"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error during registration: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")


def normalize_lab_id(lab_id: str) -> str:
    """Возвращает нормализованную строку вида ЛР1, ЛР2 и т.д."""
    number = parse_lab_id(lab_id)
    return f"ЛР{number}"


class GradeRequest(BaseModel):
    github: str = Field(..., min_length=1)

@app.post("/courses/{course_id}/groups/{group_id}/labs/{lab_id}/grade")
@limiter.limit("10/minute")
def grade_lab(request: Request, course_id: str, group_id: str, lab_id: str, grade_request: GradeRequest):
    """
    Grade a lab submission by checking GitHub repository and CI status.

    Uses the LabGrader orchestrator for GitHub checks and CI evaluation,
    then updates the grade in Google Sheets.

    Flow (preserves original behavior):
    1. GitHub checks (files, workflows, commits, forbidden mods)
    2. CI evaluation
    3. Return early for errors/pending (no Sheets connection needed)
    4. Connect to Sheets only when we have a result to write
    """
    logger.info(f"Grading attempt - Course: {course_id}, Group: {group_id}, Lab: {lab_id}, GitHub: {grade_request.github}")

    try:
        # Load course and lab configuration
        course_info = get_course_by_id(course_id)
        org = course_info.get("github", {}).get("organization")
        spreadsheet_id = course_info.get("google", {}).get("spreadsheet")

        labs = course_info.get("labs", {})
        # lab_id приходит из интерфейса как short-name ("ЛР0.1"), а из прочих
        # вызовов - как ключ конфига. Разбор по числу выбирал не ту лабу,
        # см. find_lab_config.
        resolved = find_lab_config(labs, lab_id)
        lab_key, lab_config_dict = resolved if resolved else (None, {})
        repo_prefix = lab_config_dict.get("github-prefix")

        logger.debug(f"Looking for lab config by '{lab_id}', resolved key: {lab_key!r}, found: {bool(lab_config_dict)}")

        if not all([org, spreadsheet_id, repo_prefix]):
            logger.error(f"Missing course configuration for {course_id}: org={org}, spreadsheet={spreadsheet_id}, repo_prefix={repo_prefix}")
            raise HTTPException(status_code=400, detail="Missing course configuration")

        # Create grader and do GitHub checks FIRST (before Sheets connection)
        github_client = GitHubClient(GITHUB_TOKEN)
        grader = LabGrader(github_client)

        username = grade_request.github
        repo_name = f"{repo_prefix}-{username}"
        logger.info(f"Checking repository: {org}/{repo_name}")

        # Step 1: Check repository (required files, workflows, commits)
        repo_error = grader.check_repository(org, repo_name, lab_config_dict)
        if repo_error:
            logger.warning(f"Repository check failed: {repo_error.message}")
            # Use 404 for "no commits" to match original behavior
            status_code = 404 if repo_error.error_code == "NO_COMMITS" else 400
            raise HTTPException(status_code=status_code, detail=repo_error.message)

        # Step 2: Check forbidden file modifications
        forbidden_error = grader.check_forbidden_files(org, repo_name, lab_config_dict)
        if forbidden_error:
            logger.warning(f"Forbidden modification: {forbidden_error.message}")
            raise HTTPException(status_code=403, detail=forbidden_error.message)

        # Step 3: Evaluate CI results
        ci_evaluation = grader._evaluate_ci_internal(org, repo_name, lab_config_dict)

        # Return early for errors (no Sheets needed)
        if ci_evaluation.grade_result.status == GradeStatus.ERROR:
            logger.warning(f"CI error: {ci_evaluation.grade_result.message}")
            raise HTTPException(status_code=400, detail=ci_evaluation.grade_result.message)

        # Return early for pending (no Sheets needed)
        if ci_evaluation.grade_result.status == GradeStatus.PENDING:
            logger.info(f"CI pending: {ci_evaluation.grade_result.message}")
            return {
                "status": "pending",
                "message": ci_evaluation.grade_result.message,
                "passed": ci_evaluation.grade_result.passed,
                "checks": ci_evaluation.grade_result.checks
            }

        # CI evaluation complete - now connect to Sheets for writing result
        logger.info(f"Connecting to Google Sheets for group {group_id}")
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        sheets_client = gspread.authorize(creds)

        try:
            spreadsheet = sheets_client.open_by_key(spreadsheet_id)
            sheet = spreadsheet.worksheet(group_id)
            logger.info(f"Successfully opened worksheet '{group_id}'")
        except Exception as e:
            logger.error(f"Failed to open worksheet '{group_id}': {str(e)}")
            raise HTTPException(status_code=404, detail="Группа не найдена в Google Таблице")

        # Get decimal separator from spreadsheet locale
        decimal_separator = get_decimal_separator(spreadsheet)
        logger.info(f"Using decimal separator: '{decimal_separator}'")

        # Find GitHub column and student row
        header_row = sheet.row_values(1)
        try:
            github_col_idx = header_row.index("GitHub") + 1
        except ValueError:
            logger.error(f"'GitHub' column not found in spreadsheet headers")
            raise HTTPException(status_code=400, detail="Столбец 'GitHub' не найден")

        github_values = sheet.col_values(github_col_idx)[2:]
        row_idx = find_student_row(github_values, username)

        if row_idx is None:
            logger.warning(f"GitHub username '{username}' not found in spreadsheet for group {group_id}")
            raise HTTPException(status_code=404, detail="GitHub логин не найден в таблице. Зарегистрируйтесь.")

        # Find lab column
        lab_short_name = lab_config_dict.get("short-name")
        if lab_short_name:
            lab_col = find_lab_column_by_name(sheet, lab_short_name)
            if lab_col:
                logger.info(f"Found lab column '{lab_short_name}' at column {lab_col}")
            else:
                logger.error(f"Lab column '{lab_short_name}' not found in spreadsheet")
                raise HTTPException(status_code=400, detail=f"Столбец '{lab_short_name}' не найден в таблице")
        else:
            logger.warning(f"Lab config for '{lab_id}' is missing 'short-name', using offset calculation")
            lab_offset = course_info.get("google", {}).get("lab-column-offset", 1)
            # Номер берём из ключа конфига: строка клиента может быть short-name.
            lab_number = parse_lab_id(lab_key or lab_id)
            lab_col = calculate_lab_column(lab_number, lab_offset)
            logger.info(f"Calculated lab column using offset: {lab_offset} + {lab_number} = {lab_col}")

        # Get current cell value for protection check
        current_value = sheet.cell(row_idx, lab_col).value or ""
        logger.info(f"Current cell value at row {row_idx}, column {lab_col}: '{current_value}'")

        # Determine final grade
        final_result = ci_evaluation.grade_result.result  # "v" or "x"
        final_message = ci_evaluation.grade_result.message
        score_value = ci_evaluation.score  # Extracted score from logs (if any)

        # Additional checks only if CI passed
        if ci_evaluation.ci_passed:
            # Check TASKID if configured
            task_id_column_config = course_info.get("google", {}).get("task-id-column")
            taskid_max = lab_config_dict.get("taskid-max")
            ignore_taskid = lab_config_dict.get("ignore-task-id", False)

            if task_id_column_config is not None and taskid_max is not None and not ignore_taskid:
                task_id_column = task_id_column_config + 1
                student_order = get_student_order(sheet, row_idx, task_id_column)

                if student_order is not None:
                    taskid_shift = lab_config_dict.get("taskid-shift", 0)
                    expected_taskid = calculate_expected_taskid(student_order, taskid_shift, taskid_max)
                    logger.info(f"Expected TASKID: {expected_taskid} (order={student_order}, shift={taskid_shift}, max={taskid_max})")

                    taskid_error = grader.check_taskid(
                        org, repo_name,
                        ci_evaluation.successful_runs,
                        expected_taskid,
                    )
                    if taskid_error:
                        logger.warning(f"TASKID error: {taskid_error.message}")
                        raise HTTPException(status_code=400, detail=taskid_error.message)

            # Calculate penalty if deadline configured
            # Get timezone from course config to apply to deadline from sheet
            timezone_str = course_info.get("timezone")
            deadline = get_deadline_from_sheet(sheet, lab_col, deadline_row=1, timezone_str=timezone_str)
            penalty = 0
            if deadline and ci_evaluation.latest_success_time:
                from grading.penalty import calculate_penalty, format_grade_with_penalty, PenaltyStrategy
                penalty_max = lab_config_dict.get("penalty-max", 0)
                strategy_name = lab_config_dict.get("penalty-strategy", "weekly")
                try:
                    strategy = PenaltyStrategy(strategy_name)
                except ValueError:
                    strategy = PenaltyStrategy.WEEKLY

                penalty = calculate_penalty(
                    completed_at=ci_evaluation.latest_success_time,
                    deadline=deadline,
                    penalty_max=penalty_max,
                    strategy=strategy,
                )

                if penalty > 0:
                    logger.info(f"Calculated penalty: {penalty}")

            # Format final result with score and penalty
            if score_value is not None:
                # Format grade with score (and penalty if present)
                final_result = format_grade_with_score("v", score_value, penalty, decimal_separator)
                logger.info(f"Formatted grade with score: {final_result}")

                # Build message
                formatted_score = format_score(score_value, decimal_separator)
                if penalty > 0:
                    final_message = f"Результат CI: ✅ Все проверки пройдены (Баллы: {formatted_score}, штраф: -{penalty})"
                else:
                    final_message = f"Результат CI: ✅ Все проверки пройдены (Баллы: {formatted_score})"
            elif penalty > 0:
                # No score, but penalty exists
                from grading.penalty import format_grade_with_penalty
                final_result = format_grade_with_penalty("v", penalty)
                final_message = f"Результат CI: ✅ Все проверки пройдены (штраф: -{penalty})"
                logger.info(f"Applied penalty {penalty} for late submission: {final_result}")

        # Check cell protection
        if not can_overwrite_cell(current_value):
            logger.warning(f"Update rejected: cell already contains '{current_value}'")
            response = {
                "status": "rejected",
                "result": current_value,
                "message": "⚠️ Работа уже была проверена ранее. Обратитесь к преподавателю для пересдачи.",
                "passed": ci_evaluation.grade_result.passed,
                "checks": ci_evaluation.grade_result.checks,
                "current_grade": current_value
            }
            if score_value is not None:
                response["score"] = format_score(score_value, decimal_separator)
            return response

        # Update Google Sheets with new grade
        logger.info(f"Updating cell at row {row_idx}, column {lab_col} with result '{final_result}'")
        sheet.update_cell(row_idx, lab_col, final_result)
        logger.info(f"Successfully updated grade for '{username}' in lab {lab_id}")

        response = {
            "status": "updated",
            "result": final_result,
            "message": final_message,
            "passed": ci_evaluation.grade_result.passed,
            "checks": ci_evaluation.grade_result.checks
        }
        if score_value is not None:
            response["score"] = format_score(score_value, decimal_separator)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error during grading: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")


# ---------------------------------------------------------------------------
# /join: automatic student repo creation (replaces GitHub Classroom)
# See docs/REPO_GENERATION_PLAN.md for the full design.
# ---------------------------------------------------------------------------

# Valid values of a lab's `repo-provisioning` config field (see docs/COURSE_CONFIG.md).
# "template" is the default (backward-compatible) - GitHub's `generate` API.
# "fork" creates a real fork of template-repo instead (issue #51).
REPO_PROVISIONING_MODES = {"template", "fork"}


def _load_lab_for_join(course_id: str, lab_id: str) -> tuple[dict, dict, str]:
    """
    Load course/lab config needed by the /join flow.

    Returns:
        (course_info, lab_config, github_organization)

    Raises:
        HTTPException: 404 for unknown course/lab, 400 if the lab has no
        `template-repo` configured, has an unrecognized `repo-provisioning`
        value, or the course has no GitHub organization.
    """
    course_info = get_course_by_id(course_id)  # raises 404 if course unknown

    labs = course_info.get("labs", {})
    resolved = find_lab_config(labs, lab_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="Лабораторная работа не найдена")
    _lab_key, lab_config = resolved

    template_repo = lab_config.get("template-repo")
    if not template_repo:
        raise HTTPException(
            status_code=400,
            detail="Для этой лабораторной работы не настроено автоматическое создание репозитория (template-repo)",
        )

    repo_provisioning = lab_config.get("repo-provisioning", "template")
    if repo_provisioning not in REPO_PROVISIONING_MODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Некорректное значение repo-provisioning: '{repo_provisioning}' "
                f"(допустимо: {', '.join(sorted(REPO_PROVISIONING_MODES))})"
            ),
        )

    org = course_info.get("github", {}).get("organization")
    if not org:
        raise HTTPException(status_code=400, detail="Для курса не настроена GitHub организация")

    return course_info, lab_config, org


def _oauth_redirect_uri(request: Request) -> str:
    """Authorization callback URL - must exactly match the one registered with the OAuth App."""
    if GITHUB_OAUTH_CALLBACK_URL:
        return GITHUB_OAUTH_CALLBACK_URL
    return f"{str(request.base_url).rstrip('/')}/join/callback"


def _build_join_state(course_id: str, lab_id: str) -> str:
    """
    Build a signed `state` param carrying course_id/lab_id, using the same
    itsdangerous signer already used for admin_session (see §3.3 of the plan).
    """
    payload = json.dumps({"course_id": course_id, "lab_id": lab_id}).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii")
    return signer.sign(payload_b64.encode("ascii")).decode("ascii")


def _parse_join_state(state: str | None) -> dict:
    """
    Verify and decode a `state` param built by _build_join_state.

    Raises:
        HTTPException(400): if the state is missing, malformed, unsigned, or expired
    """
    try:
        payload_b64 = signer.unsign(state, max_age=JOIN_STATE_MAX_AGE).decode("ascii")
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
    except (BadSignature, ValueError, TypeError, KeyError):
        raise HTTPException(
            status_code=400,
            detail="Невалидная или просроченная ссылка. Запросите ссылку на подключение заново",
        )

    if not isinstance(payload, dict) or "course_id" not in payload or "lab_id" not in payload:
        raise HTTPException(status_code=400, detail="Невалидная ссылка")

    return payload


def _join_result_redirect(course_id: str, lab_id: str, status: str, **extra) -> str:
    """Build the frontend result URL (/join/:courseId/:labId) the student's browser lands on."""
    params = {"status": status, **{k: v for k, v in extra.items() if v is not None}}
    base = FRONTEND_URL.rstrip("/")
    return f"{base}/join/{course_id}/{lab_id}?{urlencode(params)}"


def _join_error_redirect(reason: str) -> str:
    """Результат для случая, когда course_id/lab_id ещё неизвестны (битый state)."""
    return f"{FRONTEND_URL.rstrip('/')}/join/error?{urlencode({'status': 'error', 'reason': reason})}"


def _exchange_code_for_username(code: str, redirect_uri: str) -> str | None:
    """
    Exchange an OAuth `code` for the confirmed GitHub username of the student.

    This is the ONLY source of truth for the student's identity (see §3.2 of
    the plan) - the student's access token obtained here is used for exactly
    one request (GET /user) and is never logged, stored, or returned.

    Returns:
        The confirmed GitHub login, or None if any step of the exchange failed
    """
    try:
        token_resp = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_OAUTH_CLIENT_ID,
                "client_secret": GITHUB_OAUTH_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
    except requests.RequestException as e:
        logger.error(f"OAuth token exchange request failed: {e}")
        return None

    if token_resp.status_code != 200:
        logger.error(f"OAuth token exchange failed with status {token_resp.status_code}")
        return None

    access_token = token_resp.json().get("access_token")
    if not access_token:
        logger.error("OAuth token exchange response had no access_token")
        return None

    try:
        user_resp = requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
    except requests.RequestException as e:
        logger.error(f"GitHub /user request failed: {e}")
        return None

    if user_resp.status_code != 200:
        logger.error(f"GitHub /user returned status {user_resp.status_code}")
        return None

    username = user_resp.json().get("login")
    if not username:
        logger.error("GitHub /user response had no 'login' field")
        return None

    return username


@app.get("/join/{course_id}/{lab_id}")
@limiter.limit("30/minute")
def join_lab_info(request: Request, course_id: str, lab_id: str):
    """Публичная информация для лендинга страницы присоединения к лабе (без аутентификации)."""
    course_info, lab_config, _org = _load_lab_for_join(course_id, lab_id)
    return {
        "course_id": course_id,
        "lab_id": lab_id,
        "course_name": course_info.get("name", "Unknown"),
        "lab_short_name": lab_config.get("short-name", lab_id),
    }


@app.get("/join/{course_id}/{lab_id}/start")
@limiter.limit("20/minute")
def join_lab_start(request: Request, course_id: str, lab_id: str):
    """Начинает GitHub OAuth Web Application Flow (см. §3 плана)."""
    _load_lab_for_join(course_id, lab_id)  # validate config before sending the student to GitHub

    if not GITHUB_OAUTH_CLIENT_ID or not GITHUB_OAUTH_CLIENT_SECRET:
        logger.error("GITHUB_OAUTH_CLIENT_ID/GITHUB_OAUTH_CLIENT_SECRET is not configured")
        raise HTTPException(status_code=503, detail="Вход через GitHub временно недоступен: OAuth App не настроен")

    state = _build_join_state(course_id, lab_id)
    params = {
        "client_id": GITHUB_OAUTH_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(request),
        "scope": "read:user",
        "state": state,
    }
    logger.info(f"Redirecting to GitHub OAuth for join {course_id}/{lab_id}")
    return RedirectResponse(url=f"https://github.com/login/oauth/authorize?{urlencode(params)}")


@app.get("/join/callback")
@limiter.limit("20/minute")
def join_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """
    Обрабатывает колбэк GitHub OAuth: проверяет state, получает подтверждённый
    username, создаёт репозиторий из шаблона и чинит доступ студента (§4 плана).
    """
    # course_id/lab_id are unknown until state is decoded, so a missing/invalid/
    # expired state can't redirect to the (unknown) frontend course/lab page -
    # it goes to the course/lab-agnostic /join/error page instead.
    try:
        payload = _parse_join_state(state)
    except HTTPException:
        return RedirectResponse(url=_join_error_redirect("invalid_state"))
    course_id = payload["course_id"]
    lab_id = payload["lab_id"]

    if error:
        logger.info(f"Student declined GitHub OAuth for join {course_id}/{lab_id}: {error}")
        return RedirectResponse(url=_join_result_redirect(course_id, lab_id, "error", reason="access_denied"))

    if not code:
        return RedirectResponse(url=_join_result_redirect(course_id, lab_id, "error", reason="missing_code"))

    try:
        course_info, lab_config, org = _load_lab_for_join(course_id, lab_id)
    except HTTPException:
        return RedirectResponse(url=_join_result_redirect(course_id, lab_id, "error", reason="config"))

    if not GITHUB_OAUTH_CLIENT_ID or not GITHUB_OAUTH_CLIENT_SECRET:
        logger.error("GITHUB_OAUTH_CLIENT_ID/GITHUB_OAUTH_CLIENT_SECRET is not configured")
        return RedirectResponse(url=_join_result_redirect(course_id, lab_id, "error", reason="oauth_not_configured"))

    username = _exchange_code_for_username(code, _oauth_redirect_uri(request))
    if username is None:
        return RedirectResponse(url=_join_result_redirect(course_id, lab_id, "error", reason="oauth_exchange_failed"))

    logger.info(f"Confirmed GitHub username '{username}' for join {course_id}/{lab_id}")

    github_prefix = lab_config.get("github-prefix")
    template_repo = lab_config.get("template-repo")
    repo_provisioning = lab_config.get("repo-provisioning", "template")

    try:
        # Server-side token, never the student's OAuth token (see §3.2/§6 of the plan).
        github_client = GitHubClient(GITHUB_TOKEN)
        provisioner = RepoProvisioner(github_client)
        result = provisioner.provision(org, github_prefix, template_repo, username, repo_provisioning)
    except Exception:
        logger.exception(f"Unexpected error provisioning repo for {username} in {course_id}/{lab_id}")
        return RedirectResponse(url=_join_result_redirect(course_id, lab_id, "error", reason="provision_failed"))

    if result.status != ProvisionStatus.OK:
        logger.warning(f"Provisioning failed for {username} in {course_id}/{lab_id}: {result.error_code}")
        return RedirectResponse(
            url=_join_result_redirect(course_id, lab_id, "error", reason=result.error_code or "provision_failed")
        )

    logger.info(f"Provisioned {result.repo_url} for {username} ({course_id}/{lab_id})")
    return RedirectResponse(
        url=_join_result_redirect(course_id, lab_id, "success", repo_url=result.repo_url, username=username)
    )


# ---------------------------------------------------------------------------
# Admin: propagate template repository updates to student repos via fork PRs
# (issue #52). Only meaningful for labs with repo-provisioning: fork - a real
# fork network is what lets GitHub build the cross-repo PR.
# ---------------------------------------------------------------------------


@app.get("/admin/courses/{course_id}/labs")
@limiter.limit("30/minute")
def admin_list_course_labs(request: Request, course_id: str, admin: str = Depends(require_admin)):
    """Labs of a course with the fields the admin lab list page needs."""
    course_info = get_course_by_id(course_id)
    labs = course_info.get("labs", {})

    result = []
    for lab_number, lab_config in labs.items():
        repo_provisioning = lab_config.get("repo-provisioning", "template")
        template_repo = lab_config.get("template-repo")
        result.append({
            "id": lab_number,
            "short_name": lab_config.get("short-name", lab_number),
            "github_prefix": lab_config.get("github-prefix"),
            "template_repo": template_repo,
            "repo_provisioning": repo_provisioning,
            "can_propagate": bool(template_repo) and repo_provisioning == "fork",
        })

    # Порядок как у преподавателя в таблице: ЛР0, ЛР0.1, ЛР1... Сортировка по
    # одному числу ставила бы "01" и "1" вровень, а ЛР0.1 - после ЛР1.
    def _lab_sort_key(lab):
        digits = re.findall(r"\d+", lab["short_name"] or lab["id"])
        # Лабы без чисел в названии (например "Тест / КР") - в конец списка.
        return (0 if digits else 1, [int(d) for d in digits], lab["id"])

    result.sort(key=_lab_sort_key)
    return result


def _load_lab_for_propagate(course_id: str, lab_id: str) -> tuple[str, str, str]:
    """
    Load the config needed to propagate a template update for a lab.

    Returns:
        (org, github_prefix, template_repo)

    Raises:
        HTTPException: 404 for unknown course/lab, 400 if the lab isn't
        `repo-provisioning: fork` with a `template-repo` set, or the course
        has no GitHub organization / the lab has no github-prefix
    """
    course_info = get_course_by_id(course_id)  # raises 404 if course unknown

    labs = course_info.get("labs", {})
    resolved = find_lab_config(labs, lab_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="Лабораторная работа не найдена")
    _lab_key, lab_config = resolved

    template_repo = lab_config.get("template-repo")
    repo_provisioning = lab_config.get("repo-provisioning", "template")
    if not template_repo or repo_provisioning != "fork":
        raise HTTPException(
            status_code=400,
            detail="Обновление шаблона доступно только для лаб с repo-provisioning: fork",
        )

    org = course_info.get("github", {}).get("organization")
    if not org:
        raise HTTPException(status_code=400, detail="Для курса не настроена GitHub организация")

    github_prefix = lab_config.get("github-prefix")
    if not github_prefix:
        raise HTTPException(status_code=400, detail="Для лабы не настроен github-prefix")

    return org, github_prefix, template_repo


class PropagateRequest(BaseModel):
    dry_run: bool = True


@app.post("/admin/courses/{course_id}/labs/{lab_id}/propagate-template-update")
@limiter.limit("10/minute")
def propagate_template_update(
    request: Request,
    course_id: str,
    lab_id: str,
    background_tasks: BackgroundTasks,
    body: PropagateRequest = PropagateRequest(),
    admin: str = Depends(require_admin),
):
    """
    Preview (dry_run=true, default) or start (dry_run=false) sending PR
    proposals with the template's latest code to every student fork.

    dry_run runs synchronously (read-only) and returns 200 with the summary.
    A real run returns 202 with a job_id to poll via GET
    /admin/propagate-jobs/{job_id}; only one run per (course_id, lab_id) at
    a time - a second POST while one is in flight gets HTTP 409.
    """
    org, github_prefix, template_repo = _load_lab_for_propagate(course_id, lab_id)

    try:
        template_owner, template_name = template_repo.split("/", 1)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Некорректно настроен template-repo лабы (ожидается формат 'owner/repo')",
        )

    github_client = GitHubClient(GITHUB_TOKEN)

    if body.dry_run:
        try:
            summary = dry_run_propagation(github_client, org, github_prefix, template_owner, template_name)
        except PropagateSetupError as e:
            logger.error(f"Dry-run propagate failed for {course_id}/{lab_id}: {e}")
            raise HTTPException(status_code=502, detail=str(e))
        return summary

    job = try_start_propagate_job(course_id, lab_id)
    if job is None:
        raise HTTPException(
            status_code=409,
            detail="Рассылка обновлений для этой лабораторной уже выполняется",
        )

    logger.info(f"Starting propagate job {job.job_id} for {course_id}/{lab_id} (admin={admin})")
    background_tasks.add_task(run_propagation, job, github_client, org, github_prefix, template_repo)
    return JSONResponse(status_code=202, content={"job_id": job.job_id})


@app.get("/admin/propagate-jobs/{job_id}")
@limiter.limit("60/minute")
def get_propagate_job_status(request: Request, job_id: str, admin: str = Depends(require_admin)):
    job = get_propagate_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    return job.to_dict()


@app.post("/courses/upload")
@limiter.limit("10/minute")
async def upload_course(request: Request, file: UploadFile = File(...), admin: str = Depends(require_admin)):
    """
    Upload a new course file and add it to index

    The course will be added with status='active', priority=0 by default
    The ID will be generated from filename (e.g., 'os-2025.yaml' -> 'os-2025')
    """
    if not file.filename.endswith(".yaml") and not file.filename.endswith(".yml"):
        raise HTTPException(status_code=400, detail="Только YAML файлы разрешены")

    file_location = os.path.join(COURSES_DIR, file.filename)

    if os.path.exists(file_location):
        raise HTTPException(status_code=400, detail="Файл с таким именем уже существует")

    content = await file.read()
    try:
        course_data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail="Некорректный YAML файл")

    # Validate course structure
    if not isinstance(course_data, dict) or "course" not in course_data:
        raise HTTPException(status_code=400, detail="Некорректная структура курса: отсутствует ключ 'course'")

    # Save course file
    with open(file_location, "wb") as f:
        f.write(content)

    # Generate course ID from filename (e.g., 'operating-systems-2025.yaml' -> 'operating-systems-2025')
    course_id = file.filename.replace(".yaml", "").replace(".yml", "")

    # Update index
    index_data = load_course_index()

    # Check if ID already exists
    existing_ids = {entry.get("id") for entry in index_data.get("courses", [])}
    if course_id in existing_ids:
        # If ID exists, try appending a number
        counter = 2
        while f"{course_id}-{counter}" in existing_ids:
            counter += 1
        course_id = f"{course_id}-{counter}"

    # Add new course to index
    new_entry = {
        "id": course_id,
        "file": file.filename,
        "status": "active",
        "priority": 0
    }
    index_data["courses"].append(new_entry)

    # Save updated index
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        yaml.dump(index_data, f, allow_unicode=True, sort_keys=False)

    return {
        "detail": "Курс успешно загружен и добавлен в индекс",
        "course_id": course_id,
        "filename": file.filename
    }