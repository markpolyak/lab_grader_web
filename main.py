from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.staticfiles import StaticFiles
import os
import yaml
import gspread
import requests
from oauth2client.service_account import ServiceAccountCredentials
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import UploadFile, File
from dotenv import load_dotenv
from itsdangerous import TimestampSigner, BadSignature
import re
import logging
from datetime import datetime

from grading import (
    LabGrader,
    GitHubClient,
    GradeStatus,
    find_student_row,
    find_lab_column_by_name,
    calculate_lab_column,
    can_overwrite_cell,
    get_deadline_from_sheet,
    get_student_order,
    calculate_expected_taskid,
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
async def read_index():
    return FileResponse("dist/index.html")

@app.post("/admin/login")
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

@app.get("/admin/check-auth")
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

@app.post("/admin/logout")
def logout(response: Response):
    response.delete_cookie("admin_session", path="/")
    return {"message": "Logged out"}


@app.get("/courses")
def get_courses(status: str = "active"):
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

@app.get("/courses/{course_id}")
def get_course(course_id: str):
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
def delete_course(course_id: str):
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
def edit_course_get(course_id: str):
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
def edit_course_put(course_id: str, data: EditCourseRequest):
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
def get_course_groups(course_id: str):
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
def get_course_labs(course_id: str, group_id: str):
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
def register_student(course_id: str, group_id: str, student: StudentRegistration):
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
def grade_lab(course_id: str, group_id: str, lab_id: str, request: GradeRequest):
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
    logger.info(f"Grading attempt - Course: {course_id}, Group: {group_id}, Lab: {lab_id}, GitHub: {request.github}")

    try:
        # Load course and lab configuration
        course_info = get_course_by_id(course_id)
        org = course_info.get("github", {}).get("organization")
        spreadsheet_id = course_info.get("google", {}).get("spreadsheet")

        labs = course_info.get("labs", {})
        lab_number = parse_lab_id(lab_id)
        lab_config_dict = labs.get(str(lab_number), {})
        repo_prefix = lab_config_dict.get("github-prefix")

        logger.debug(f"Looking for lab config with key '{lab_number}', found: {bool(lab_config_dict)}")

        if not all([org, spreadsheet_id, repo_prefix]):
            logger.error(f"Missing course configuration for {course_id}: org={org}, spreadsheet={spreadsheet_id}, repo_prefix={repo_prefix}")
            raise HTTPException(status_code=400, detail="Missing course configuration")

        # Create grader and do GitHub checks FIRST (before Sheets connection)
        github_client = GitHubClient(GITHUB_TOKEN)
        grader = LabGrader(github_client)

        username = request.github
        repo_name = f"{repo_prefix}-{username}"
        logger.info(f"Checking repository: {org}/{repo_name}")

        # Step 1: Check repository (required files, workflows, commits)
        repo_error = grader.check_repository(org, repo_name, lab_config_dict)
        if repo_error:
            logger.warning(f"Repository check failed: {repo_error.message}")
            raise HTTPException(status_code=400, detail=repo_error.message)

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
            sheet = sheets_client.open_by_key(spreadsheet_id).worksheet(group_id)
            logger.info(f"Successfully opened worksheet '{group_id}'")
        except Exception as e:
            logger.error(f"Failed to open worksheet '{group_id}': {str(e)}")
            raise HTTPException(status_code=404, detail="Группа не найдена в Google Таблице")

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
            lab_col = calculate_lab_column(lab_number, lab_offset)
            logger.info(f"Calculated lab column using offset: {lab_offset} + {lab_number} = {lab_col}")

        # Get current cell value for protection check
        current_value = sheet.cell(row_idx, lab_col).value or ""
        logger.info(f"Current cell value at row {row_idx}, column {lab_col}: '{current_value}'")

        # Determine final grade
        final_result = ci_evaluation.grade_result.result  # "v" or "x"
        final_message = ci_evaluation.grade_result.message

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
            deadline = get_deadline_from_sheet(sheet, lab_col, deadline_row=1)
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
                    final_result = format_grade_with_penalty("v", penalty)
                    final_message = f"Результат CI: ✅ Все проверки пройдены (штраф: -{penalty})"
                    logger.info(f"Applied penalty {penalty} for late submission: {final_result}")

        # Check cell protection
        if not can_overwrite_cell(current_value):
            logger.warning(f"Update rejected: cell already contains '{current_value}'")
            return {
                "status": "rejected",
                "result": current_value,
                "message": "⚠️ Работа уже была проверена ранее. Обратитесь к преподавателю для пересдачи.",
                "passed": ci_evaluation.grade_result.passed,
                "checks": ci_evaluation.grade_result.checks,
                "current_grade": current_value
            }

        # Update Google Sheets with new grade
        logger.info(f"Updating cell at row {row_idx}, column {lab_col} with result '{final_result}'")
        sheet.update_cell(row_idx, lab_col, final_result)
        logger.info(f"Successfully updated grade for '{username}' in lab {lab_id}")

        return {
            "status": "updated",
            "result": final_result,
            "message": final_message,
            "passed": ci_evaluation.grade_result.passed,
            "checks": ci_evaluation.grade_result.checks
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error during grading: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")




@app.post("/courses/upload")
async def upload_course(file: UploadFile = File(...)):
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