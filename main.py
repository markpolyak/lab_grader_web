from fastapi import FastAPI, Request, Response, HTTPException, Depends, Path, Query
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
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
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
import re
import json
import base64
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any
from urllib.parse import quote, urlencode, urlsplit
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
    SheetContext,
    evaluate_student,
    taskid_column,
    try_start_bulk_job,
    get_bulk_job,
    get_running_bulk_job,
    fail_bulk_job,
    bulk_worker_thread_name,
    request_bulk_job_cancel,
    run_bulk_grading,
    TeamActionStatus,
    TeamConfig,
    TeamConfigError,
    TeamInfo,
    TeamRegistry,
    is_team_lab,
    parse_team_config,
    JoinConfigError,
    JoinWindow,
    STATE_CLOSED,
    STATE_NOT_OPEN,
    STATE_OPEN,
    check_token_collisions,
    is_secret_lab,
    lab_token,
    parse_join_config,
    parse_window,
    resolve_token,
)
from grading.course_index import (
    course_meta,
    iter_course_configs as _iter_course_configs,
    load_course_index as _load_course_index,
)
from grading import queue_roster
from grading.defense_queue import (
    HISTORY_FIRST_DAY,
    LAB_DEFENDED,
    LAB_NOT_PASSED,
    SIGNUP_CLOSED,
    DefenseQueue,
    QueueConfigError,
    QueueError,
    QueueSettings,
    course_day,
    day_start,
    iso_utc,
    lab_cell_status,
    normalize_login,
    parse_closes_at,
    parse_history_day,
    parse_queue_config,
    parse_utc_offset,
    queue_flag,
    warn_about_queue_config,
)
from grading.queue_store import QueueStore
from grading.schedule import ScheduleUnavailable, SourceContext, build_schedule_source
from grading.schedule.changes import UnknownDay, parse_changes
import tempfile

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


class QueuePollAccessFilter(logging.Filter):
    """
    Keep successful polls of the defense queue out of the access log.

    The student page polls every 10 s and the teacher's every 5 s: one lesson
    of thirty students is tens of thousands of identical lines, and the log
    file has no rotation (docs/LOGGING.md). Errors and every other request
    are still logged.
    """

    POLL_PATH_RE = re.compile(r"(/admin)?/courses/[^/]+/queue")

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn: '%s - "%s %s HTTP/%s" %d' % (client, method, path, version, status)
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        _client, method, full_path, _version, status = args
        if method != "GET" or not isinstance(status, int) or not 200 <= status < 400:
            return True
        path = str(full_path).split("?", 1)[0]
        return self.POLL_PATH_RE.fullmatch(path) is None


uvicorn_access.addFilter(QueuePollAccessFilter())

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

SHEETS_SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Сколько ждать ответа Google Sheets. Без таймаута gspread ждёт вечно, и одно
# повисшее соединение останавливает всю фоновую работу целиком - ту же ошибку
# для GitHub исправлял PR #42.
SHEETS_TIMEOUT_SECONDS = 30


def sheets_client(timeout: float = SHEETS_TIMEOUT_SECONDS) -> gspread.Client:
    """Authorized gspread client with a request timeout set."""
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SHEETS_SCOPE)
    client = gspread.authorize(creds)
    client.set_timeout(timeout)
    return client


# Очередь читает таблицу, пока студент ждёт ответа на вход или запись: лучше
# быстро ответить «повторите», чем держать запрос полминуты.
QUEUE_SHEETS_TIMEOUT_SECONDS = 8


def queue_sheets_client() -> gspread.Client:
    """sheets_client() with the shorter timeout of the defense queue."""
    return sheets_client(QUEUE_SHEETS_TIMEOUT_SECONDS)


def _start_background_job(func, *args, name: str) -> None:
    """
    Run a long background job (bulk grading, template propagation) in a
    daemon thread.

    Not FastAPI's BackgroundTasks: those run only after the response has been
    delivered, so a client that disconnects (a closed tab, a proxy dropping
    the connection) leaves the job registered as running while nothing ever
    runs it - and the course/group/lab stays locked until the backend is
    restarted. A thread starts the work immediately, independently of the
    response. Still single-process state, so the single-worker requirement
    from docs/PROJECT_DESCRIPTION.md is unchanged.
    """
    threading.Thread(target=func, args=args, name=name, daemon=True).start()
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
# Public address of the service, used to build the secret /j/{token} links the
# admin page shows. Behind a reverse proxy request.base_url can hold the
# container's internal address, so production should set this explicitly
# (docs/SECRET_JOIN_LINKS_PLAN.md §9.1).
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
# Max age (seconds) for the signed OAuth `state` param - see docs/REPO_GENERATION_PLAN.md §3.3.
JOIN_STATE_MAX_AGE = 600

# Таймаут прямых обращений к GitHub из main.py. Без него requests ждёт ответа
# бесконечно: зависшее соединение держало бы воркер, а студент - открытую
# вкладку. Обращения через GitHubClient пользуются его собственными таймаутами.
GITHUB_REQUEST_TIMEOUT = 10
# Student session issued by /join/callback for team labs, so that the team
# endpoints can take the confirmed username from a signed cookie and never
# from the request - see docs/TEAM_ASSIGNMENTS_PLAN.md §6.
JOIN_SESSION_COOKIE = "join_session"
JOIN_SESSION_MAX_AGE = 1800

# SQLite-файл очереди на защиту (docs/DEPLOYMENT.md): в контейнере - на томе
# queue-data, при локальном запуске - в ./data/. Само соединение открывается
# лениво, при первом обращении к очереди (_queue_store).
QUEUE_DB_PATH = os.getenv("QUEUE_DB_PATH", "data/queue.sqlite3")

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
ADMIN_SESSION_MAX_AGE = 3600
# Когда преподаватель вошёл по паролю. Страница очереди продлевает
# admin_session (_renew_admin_session), но так, чтобы она кончилась не
# позже этого срока от входа: украденную cookie нельзя держать живой
# сколько угодно.
ADMIN_SESSION_START_COOKIE = "admin_session_start"
ADMIN_SESSION_MAX_LIFETIME = 12 * 3600
admin_start_signer = TimestampSigner(SECRET_KEY, salt="admin-session-start")

# Course index management
INDEX_FILE = os.path.join(COURSES_DIR, "index.yaml")

def load_course_index():
    """Load and validate course index file"""
    return _load_course_index(INDEX_FILE)

def iter_course_configs():
    """
    Перечислить конфигурации всех курсов индекса - единственная точка
    перечисления на весь проект (docs/SECRET_JOIN_LINKS_PLAN.md §6, §13).

    Резолв секретной ссылки и список курсов читают конфиги через неё, поэтому
    перенос конфигов в отдельное хранилище (БД, приватный git) потребует
    правки одного места, а не десятка чтений с диска. Там же потом появится
    индекс `token -> (курс, лаба)`.

    Само чтение файлов живёт в grading/course_index.py: тем же кодом
    пользуется scripts/join-link.py, которому сервер целиком не нужен.

    Yields:
        (course_id, course_info) - `course_info` с заполненным `_meta`
    """
    yield from _iter_course_configs(COURSES_DIR, INDEX_FILE)


def warn_about_join_links(filename: str, course_info: dict) -> None:
    """
    Log problems in labs' `join` sections (see warn_about_team_labs).

    A malformed section is answered per request by the endpoint that names
    the lab; here it makes it into the startup log, where the teacher sees it
    before a student does. Public paths hide such a lab rather than guess
    what was meant - see find_public_lab_config.
    """
    labs = course_info.get("labs", {})
    if not isinstance(labs, dict):
        return

    for lab_key, lab_config in labs.items():
        try:
            parse_join_config(lab_config, course_info.get("timezone"))
        except JoinConfigError as e:
            logger.warning(f"{filename}: лаба '{lab_key}' - {e}")


def warn_about_team_labs(filename: str, course_info: dict) -> None:
    """
    Log config problems of team labs that are not fatal on their own.

    A malformed `team` section is reported by /join for that lab (see
    _load_lab_for_join); here it only makes it into the startup log, together
    with `taskid-max`, which a team lab ignores - the variant number is
    derived from the student's position in the sheet and a team has none
    (docs/TEAM_ASSIGNMENTS_PLAN.md §4, §10.3).
    """
    labs = course_info.get("labs", {})
    if not isinstance(labs, dict):
        return

    for lab_key, lab_config in labs.items():
        if not is_team_lab(lab_config):
            continue

        try:
            parse_team_config(lab_config)
        except TeamConfigError as e:
            logger.warning(f"{filename}: лаба '{lab_key}' - {e}")

        if lab_config.get("taskid-max") is not None:
            logger.warning(
                f"{filename}: лаба '{lab_key}' - командная, поэтому taskid-max игнорируется "
                "(проверка варианта для командных лаб не выполняется)"
            )


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
                warn_about_team_labs(entry["file"], data["course"])
                warn_about_join_links(entry["file"], data["course"])
                warn_about_queue_config(entry["file"], data["course"])
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
    course_info["_meta"] = course_meta(course_entry)

    return course_info

# Validate index on startup
print("Validating course index...")
if not validate_course_index():
    raise RuntimeError("Course index validation failed. Please fix index.yaml before starting.")

# Two secret links resolving to the same lab would be silent; three lines turn
# that into a startup log line a teacher can fix by raising join.revision
# (docs/SECRET_JOIN_LINKS_PLAN.md §3.1).
for _collision in check_token_collisions(SECRET_KEY, iter_course_configs()):
    logger.error(f"Конфликт секретных ссылок: {_collision}")


def check_queue_storage() -> None:
    """
    Log an error when the queue database cannot be written, if any course uses the queue.

    Only a log line, nothing is created: the rest of the service works
    without the queue, and a missing volume is better seen in the startup
    log than as STORAGE_UNAVAILABLE in the middle of a lesson.
    """
    try:
        if not any(queue_flag(info) for _course_id, info in iter_course_configs()):
            return
        path = os.path.abspath(QUEUE_DB_PATH)
        # В режиме WAL рядом с базой появляются файлы -wal и -shm, поэтому
        # писать нужно и в сам файл, и в его каталог. Каталога может ещё не
        # быть - тогда проверяем ближайший существующий родитель.
        targets = [path] if os.path.exists(path) else []
        directory = os.path.dirname(path)
        while not os.path.exists(directory) and os.path.dirname(directory) != directory:
            directory = os.path.dirname(directory)
        targets.append(directory)
        for target in targets:
            if not os.access(target, os.W_OK):
                logger.error(
                    f"Очередь на защиту: нет прав на запись в {target} (QUEUE_DB_PATH={QUEUE_DB_PATH}). "
                    "Очередь будет отвечать STORAGE_UNAVAILABLE - см. docs/DEPLOYMENT.md"
                )
                return
    except Exception as e:
        logger.error(f"Очередь на защиту: не удалось проверить QUEUE_DB_PATH={QUEUE_DB_PATH}: {e}")


check_queue_storage()

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
            max_age=ADMIN_SESSION_MAX_AGE,
            path="/",
            secure=False
        )
        response.set_cookie(
            key=ADMIN_SESSION_START_COOKIE,
            value=admin_start_signer.sign(data.login.encode()).decode(),
            httponly=True,
            max_age=ADMIN_SESSION_MAX_LIFETIME,
            path="/",
            secure=False,
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
        login = signer.unsign(cookie, max_age=ADMIN_SESSION_MAX_AGE).decode()
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
    response.delete_cookie(ADMIN_SESSION_START_COOKIE, path="/")
    return {"message": "Logged out"}


@app.get("/courses")
@limiter.limit("100/minute")
def get_courses(request: Request, status: str = "active"):
    """
    Get courses filtered by status

    Args:
        status: Filter by status (active, archived, all). Default: active
    """
    courses = []

    for course_id, course_info in iter_course_configs():
        meta = course_info["_meta"]
        course_status = meta["status"]

        # Filter by status
        if status != "all" and course_status != status:
            continue

        courses.append({
            "id": course_id,
            "name": course_info.get("name", "Unknown"),
            "university": course_info.get("university", ""),
            "semester": course_info.get("semester", "Unknown"),
            "logo": meta["logo"],  # Logo from index, not course file
            "email": course_info.get("email", ""),
            "status": course_status,
            "priority": meta["priority"],
            "featured": meta["featured"],
            "queue_enabled": queue_flag(course_info),
            # The teacher's queue page and history work whatever `enabled` says.
            "queue_configured": isinstance(course_info.get("queue"), dict),
        })

    # Sort by priority (descending), then by name
    courses.sort(key=lambda x: (-x["priority"], x["name"]))

    return courses


def parse_lab_id(lab_id: str) -> int:
    match = re.search(r"\d+", lab_id)
    if not match:
        raise HTTPException(status_code=400, detail="Некорректный lab_id")
    return int(match.group(0))


def optional_lab_number(lab_id: str) -> int | None:
    """
    The lab's number, or None when its identifier has none.

    A lab key doesn't have to be a number - "quiz" is a perfectly good key -
    and the number is only a fallback for placing the grade column when the
    lab has no `short-name`. Refusing the whole request over it (what
    parse_lab_id does) broke bulk grading of a test: the 400 was raised after
    the job had been registered, so nothing ran it and the group stayed
    locked behind HTTP 409.
    """
    match = re.search(r"\d+", lab_id)
    return int(match.group(0)) if match else None


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

def find_visible_lab_config(
    course_info: dict,
    lab_id: str,
    now: datetime | None = None,
) -> tuple[str, dict] | None:
    """
    Найти лабораторную, которая уже открыта для студентов.

    Обёртка над find_lab_config, которая пропускает:
      - лабы, не достигшие `join.opens-at` (у секретной лабы отсутствие
        `opens-at` тоже означает «ещё не открыта», см. JoinWindow);
      - лабы с неразбираемой секцией `join` - конфигурация сломана, и
        безопаснее спрятать работу, чем гадать, что имелось в виду (ошибка
        при этом попадает в лог при старте, см. warn_about_join_links).

    После `closes-at` лаба остаётся видимой: работы сдают как раз после
    окончания контрольной, закрывается только выдача репозиториев.

    Вызывающий обязан ответить на None ровно так же, как на несуществующую
    лабу: одинаковый код и одинаковое тело ответа. Иначе публичный эндпоинт
    становится оракулом, подтверждающим существование контрольной, ради
    устранения которого секретные ссылки и сделаны
    (docs/SECRET_JOIN_LINKS_PLAN.md §5, §7.2).

    Админские пути, /j/{token} и массовая проверка продолжают пользоваться
    прямым find_lab_config: окно на них не распространяется.

    Returns:
        (ключ в конфиге, конфиг лабы) или None
    """
    resolved = find_lab_config(course_info.get("labs", {}), lab_id)
    if not resolved:
        return None

    lab_key, lab_config = resolved
    try:
        window = parse_window(lab_config, course_info.get("timezone"))
    except JoinConfigError as e:
        logger.warning(f"Лаба '{lab_key}' скрыта из публичных путей: {e}")
        return None

    if not window.is_visible(now):
        return None

    return lab_key, lab_config


def find_public_lab_config(
    course_info: dict,
    lab_id: str,
    now: datetime | None = None,
) -> tuple[str, dict] | None:
    """
    То же, что find_visible_lab_config, но дополнительно прячет лабы с
    `join.link: secret`.

    Разница нужна ровно в одном месте: адрес `/join/{курс}/{лаба}` для
    секретной лабы не работает НИКОГДА, а список работ и самостоятельная
    проверка после открытия работают - иначе студент не сдал бы контрольную
    (§5 плана).
    """
    resolved = find_visible_lab_config(course_info, lab_id, now)
    if resolved is None or is_secret_lab(resolved[1]):
        return None
    return resolved


def visible_lab_items(course_info: dict, now: datetime | None = None) -> list[tuple[str, dict]]:
    """
    Лабы курса, открытые студенту сейчас, вместе с ключами конфига
    (см. find_visible_lab_config).

    Ключ нужен очереди на защиту: запись хранит канонический ключ лабы, а не
    short-name, который преподаватель может переименовать.
    """
    labs = course_info.get("labs", {})
    if not isinstance(labs, dict):
        return []

    visible = []
    for lab_key in labs:
        resolved = find_visible_lab_config(course_info, str(lab_key), now)
        if resolved is not None:
            visible.append(resolved)
    return visible


def visible_labs(course_info: dict, now: datetime | None = None) -> list[dict]:
    """Конфиги лаб курса, открытых студенту сейчас (см. find_visible_lab_config)."""
    return [lab_config for _lab_key, lab_config in visible_lab_items(course_info, now)]


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
        "queue_enabled": queue_flag(course_info),
        "queue_configured": isinstance(course_info.get("queue"), dict),
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


def _labs_of(course_data) -> dict:
    """Секция labs разобранного YAML курса, или пустой словарь."""
    if not isinstance(course_data, dict):
        return {}
    course = course_data.get("course")
    if not isinstance(course, dict):
        return {}
    labs = course.get("labs")
    return labs if isinstance(labs, dict) else {}


def _validate_join_sections(new_data, old_data) -> None:
    """
    Проверить секции `join` сохраняемого конфига (§7.2 плана).

    Файл читается на каждый запрос и не кэшируется, поэтому сохранение с
    испорченной секцией `join` ломает /j/{token} немедленно и до следующей
    правки - проверять надо до записи, а не при старте.

    Отдельно запрещается уменьшать `join.revision`: это воскресило бы уже
    отозванную ссылку. Когда конфиги переедут в версионируемое хранилище,
    та же проверка понадобится и восстановлению версии (§13).

    Raises:
        HTTPException(400): секция не разбирается или ревизия уменьшена
    """
    new_course = new_data.get("course") if isinstance(new_data, dict) else None
    timezone_str = new_course.get("timezone") if isinstance(new_course, dict) else None

    old_labs = _labs_of(old_data)

    for lab_key, lab_config in _labs_of(new_data).items():
        try:
            settings = parse_join_config(lab_config, timezone_str)
        except JoinConfigError as e:
            raise HTTPException(status_code=400, detail=f"Лаба '{lab_key}': {e}")

        try:
            old_settings = parse_join_config(old_labs.get(str(lab_key)) or old_labs.get(lab_key))
        except JoinConfigError:
            # Прежний конфиг сам был испорчен - сравнивать не с чем,
            # сохранение исправленной версии блокировать нельзя.
            continue

        if settings.revision < old_settings.revision:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Лаба '{lab_key}': join.revision нельзя уменьшать "
                    f"({old_settings.revision} -> {settings.revision}) - это вернуло бы к жизни "
                    "уже отозванную ссылку"
                ),
            )


def _validate_queue_section(new_data) -> None:
    """
    Проверить секцию `queue` сохраняемого конфига, как и секции `join`.

    Сломанная секция не роняет сервис (студенты получат QUEUE_MISCONFIGURED),
    но во время пары это уже поздно: преподаватель должен увидеть ошибку в
    редакторе. Расписание проверяется без сети - только разбор настроек.

    Raises:
        HTTPException(400): секция не разбирается
    """
    new_course = new_data.get("course") if isinstance(new_data, dict) else None
    if not isinstance(new_course, dict):
        return
    try:
        parse_queue_config(new_course)
    except QueueConfigError as e:
        raise HTTPException(status_code=400, detail=f"Очередь на защиту: {e}")


def _write_file_atomically(file_path: str, content: str) -> None:
    """
    Записать файл целиком или не записать вовсе.

    Прежняя запись усечением оставляла обрезанный YAML, если процесс умирал
    посреди неё; во время идущей контрольной это ломает ссылку сразу.
    Временный файл создаётся рядом с целевым, чтобы os.replace был
    атомарным переименованием в пределах одной файловой системы.
    """
    directory = os.path.dirname(os.path.abspath(file_path))
    tmp_fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".course-", suffix=".yaml.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


@app.put("/courses/{course_id}/edit")
@limiter.limit("20/minute")
def edit_course_put(request: Request, course_id: str, data: EditCourseRequest, admin: str = Depends(require_admin)):
    """Сохранить изменения в YAML файле курса"""
    course_info = get_course_by_id(course_id)
    filename = course_info["_meta"]["filename"]

    file_path = os.path.join(COURSES_DIR, filename)

    try:
        new_data = yaml.safe_load(data.content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Ошибка в YAML формате: {str(e)}")

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            old_data = yaml.safe_load(file.read())
    except (OSError, yaml.YAMLError):
        old_data = None

    _validate_join_sections(new_data, old_data)
    _validate_queue_section(new_data)

    _write_file_atomically(file_path, data.content)

    return {"message": "Изменения успешно сохранены"}


@app.get("/courses/{course_id}/groups")
@limiter.limit("10/minute")
def get_course_groups(request: Request, course_id: str):
    course_info = get_course_by_id(course_id)
    spreadsheet_id = course_info.get("google", {}).get("spreadsheet")
    info_sheet = course_info.get("google", {}).get("info-sheet")

    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="Spreadsheet ID not found in course config")


    client = sheets_client()

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
    # Работа, не достигшая join.opens-at, в список не попадает: до публикации
    # её не должно быть видно даже по названию столбца (§5, §7.2 плана).
    labs = [lab["short-name"] for lab in visible_labs(course_info) if "short-name" in lab]

    if not spreadsheet_id or not labs:
        raise HTTPException(status_code=400, detail="Missing spreadsheet ID or labs in config")


    client = sheets_client()

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

        client = sheets_client()

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
            github_response = requests.get(
                f"https://api.github.com/users/{student.github}", timeout=GITHUB_REQUEST_TIMEOUT
            )
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
            # Очередь на защиту узнаёт студента по столбцу GitHub на всех
            # листах курса и при двух строках с одним логином отказывает во
            # входе: чужой логин, вписанный в пустую строку, закрыл бы очередь
            # его владельцу. Если таблицу прочитать не удалось, регистрация
            # идёт как раньше.
            if queue_flag(course_info) and _queue_roster.login_exists(
                course_info, student.github, queue_sheets_client
            ):
                logger.warning(
                    f"GitHub '{student.github}' is already in another row of course {course_id}, "
                    f"refused for '{full_name}'"
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Этот GitHub-аккаунт уже привязан к другой строке таблицы курса. "
                        "Обратитесь к преподавателю"
                    ),
                )
            sheet.update_cell(row_idx, github_col_idx, student.github)
            # Очередь на защиту ищет студента по столбцу GitHub в своём кэше
            # таблицы: без сброса только что зарегистрированный студент ещё
            # минуту не смог бы войти в очередь.
            queue_roster.invalidate(spreadsheet_id)
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


class GradeRequest(BaseModel):
    github: str = Field(..., min_length=1)

@app.post("/courses/{course_id}/groups/{group_id}/labs/{lab_id}/grade")
@limiter.limit("10/minute")
def grade_lab(request: Request, course_id: str, group_id: str, lab_id: str, grade_request: GradeRequest):
    """
    Grade a lab submission by checking GitHub repository and CI status.

    The grading decision itself lives in grading.bulk.evaluate_student, shared
    with the bulk admin run; this endpoint supplies the spreadsheet context and
    translates the outcome into an HTTP response.

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

        # lab_id приходит из интерфейса как short-name ("ЛР0.1"), а из прочих
        # вызовов - как ключ конфига. Разбор по числу выбирал не ту лабу,
        # см. find_lab_config.
        #
        # Лаба, не достигшая join.opens-at, отвечает здесь тем же, чем и
        # несуществующая: публичная проверка не должна подтверждать, что
        # контрольная существует (§5 плана). Массовая проверка из админки
        # окном не ограничена и резолвит лабу напрямую.
        resolved = find_visible_lab_config(course_info, lab_id)
        if not resolved:
            logger.info(f"Lab '{lab_id}' is not available in course {course_id}")
            raise HTTPException(status_code=404, detail="Лабораторная работа не найдена")
        lab_key, lab_config_dict = resolved
        repo_prefix = lab_config_dict.get("github-prefix")

        logger.debug(f"Looking for lab config by '{lab_id}', resolved key: {lab_key!r}, found: {bool(lab_config_dict)}")

        if not all([org, spreadsheet_id, repo_prefix]):
            logger.error(f"Missing course configuration for {course_id}: org={org}, spreadsheet={spreadsheet_id}, repo_prefix={repo_prefix}")
            raise HTTPException(status_code=400, detail="Missing course configuration")

        # Create grader and do GitHub checks FIRST (before Sheets connection)
        github_client = GitHubClient(GITHUB_TOKEN)
        grader = LabGrader(github_client)

        username = grade_request.github

        # Where the grade goes once evaluate_student produces one. Filled in by
        # load_sheet_context(), which only runs if we get that far.
        target: dict = {}

        def load_sheet_context() -> SheetContext:
            """Open the group's sheet and read everything the grading needs from it."""
            logger.info(f"Connecting to Google Sheets for group {group_id}")
            client = sheets_client()

            try:
                spreadsheet = client.open_by_key(spreadsheet_id)
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

            # Student order is only read when the TASKID check applies to this lab
            student_order = None
            task_id_column = taskid_column(course_info, lab_config_dict)
            if task_id_column is not None:
                student_order = get_student_order(sheet, row_idx, task_id_column)

            # Deadline for penalty calculation, in the course timezone
            timezone_str = course_info.get("timezone")
            deadline = get_deadline_from_sheet(sheet, lab_col, deadline_row=1, timezone_str=timezone_str)

            target["sheet"] = sheet
            target["row"] = row_idx
            target["col"] = lab_col

            return SheetContext(
                current_cell_value=current_value,
                student_order=student_order,
                deadline=deadline,
                decimal_separator=decimal_separator,
            )

        # A team lab has one repository per team, not per student: find the
        # student's team and grade that repository, writing the result only
        # into this student's own row (docs/TEAM_ASSIGNMENTS_PLAN.md §10.2).
        team_repo_name = None
        if is_team_lab(lab_config_dict):
            registry = TeamRegistry(github_client)
            teams = registry.list_teams(
                org, repo_prefix, _course_teachers(course_info)
            )
            if teams is None:
                raise HTTPException(
                    status_code=502,
                    detail="Не удалось получить список команд с GitHub. Попробуйте ещё раз позже",
                )
            team = registry.find_member_team(teams, username)
            if team is None:
                raise HTTPException(
                    status_code=404,
                    detail="Вы ещё не состоите в команде для этой лабораторной работы",
                )
            team_repo_name = team.repo_name
            logger.info(f"Grading team repository {org}/{team_repo_name} for '{username}'")

        outcome = evaluate_student(
            grader, org, username, lab_config_dict, course_info, load_sheet_context,
            repo_name=team_repo_name,
        )

        if outcome.status == "error":
            # Use 404 for "no commits" and 403 for forbidden edits, as before
            if outcome.error_code == "NO_COMMITS":
                status_code = 404
            elif outcome.error_code == "FORBIDDEN_MODIFICATION":
                status_code = 403
            else:
                status_code = 400
            raise HTTPException(status_code=status_code, detail=outcome.message)

        if outcome.status == "pending":
            return {
                "status": "pending",
                "message": outcome.message,
                "passed": outcome.passed,
                "checks": outcome.checks
            }

        if outcome.status == "rejected":
            response = {
                "status": "rejected",
                "result": outcome.current_grade,
                "message": outcome.message,
                "passed": outcome.passed,
                "checks": outcome.checks,
                "current_grade": outcome.current_grade
            }
            if outcome.score is not None:
                response["score"] = outcome.score
            return response

        # Update Google Sheets with new grade
        logger.info(f"Updating cell at row {target['row']}, column {target['col']} with result '{outcome.cell_value}'")
        target["sheet"].update_cell(target["row"], target["col"], outcome.cell_value)
        # Свежая отметка должна сразу пускать в очередь на защиту - сбрасываем
        # её кэш таблицы. Это память процесса, к Sheets не обращается.
        queue_roster.invalidate(spreadsheet_id)
        logger.info(f"Successfully updated grade for '{username}' in lab {lab_id}")

        response = {
            "status": "updated",
            "result": outcome.cell_value,
            "message": outcome.message,
            "passed": outcome.passed,
            "checks": outcome.checks
        }
        if outcome.score is not None:
            response["score"] = outcome.score
        return response
    except HTTPException:
        raise
    except requests.RequestException as e:
        # Таймаут или обрыв связи с GitHub - не внутренняя ошибка сервиса:
        # студенту нужно предложить повторить, а не "Внутреннюю ошибку".
        logger.error(
            f"GitHub API request failed while grading {grade_request.github} "
            f"in {course_id}/{lab_id}: {e}"
        )
        raise HTTPException(
            status_code=502,
            detail="GitHub не ответил вовремя. Попробуйте запустить проверку ещё раз",
        )
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


def _load_lab_for_join(
    course_id: str,
    lab_id: str,
    public: bool = True,
) -> tuple[dict, str, dict, str, TeamConfig | None]:
    """
    Load course/lab config needed by the /join flow.

    Args:
        public: resolve the lab the way a student without a secret link sees
            it - a lab with `join.link: secret` and a lab that has not
            reached `join.opens-at` are then indistinguishable from a lab
            that does not exist (same 404, same body). Callers that already
            hold the secret (the /j/{token} endpoints and the OAuth callback,
            which reads the lab out of a signed `state`) pass False and check
            the window themselves.

    Returns:
        (course_info, lab_key, lab_config, github_organization, team_config).
        `lab_key` is the lab's canonical key in the course YAML: one lab is
        reachable through several spellings of lab_id ("5", "05", "ЛР5"), and
        anything that keys shared state by lab must use this value rather than
        the raw path segment. team_config is None for an individual lab and a
        TeamConfig for a team one (docs/TEAM_ASSIGNMENTS_PLAN.md §4).

    Raises:
        HTTPException: 404 for unknown course/lab, 400 if the lab has no
        `template-repo` configured, has an unrecognized `repo-provisioning`
        value, has a malformed `team` section, or the course has no GitHub
        organization.
    """
    course_info = get_course_by_id(course_id)  # raises 404 if course unknown

    labs = course_info.get("labs", {})
    resolved = (
        find_public_lab_config(course_info, lab_id)
        if public
        else find_lab_config(labs, lab_id)
    )
    if not resolved:
        raise HTTPException(status_code=404, detail="Лабораторная работа не найдена")
    lab_key, lab_config = resolved

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

    try:
        team_config = parse_team_config(lab_config)
    except TeamConfigError as e:
        # Same treatment as an unknown repo-provisioning value: a config
        # mistake answers with a clear 400, never a 500.
        raise HTTPException(status_code=400, detail=f"Некорректная настройка команд: {e}")

    org = course_info.get("github", {}).get("organization")
    if not org:
        raise HTTPException(status_code=400, detail="Для курса не настроена GitHub организация")

    return course_info, lab_key, lab_config, org, team_config


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


def _build_join_session(username: str, course_id: str, lab_id: str) -> str:
    """
    Build the signed value of the `join_session` cookie.

    An individual lab finishes inside the OAuth callback, but a team lab needs
    a dialogue (show the teams, wait for the choice), so the confirmed
    username is carried in a short-lived session instead
    (docs/TEAM_ASSIGNMENTS_PLAN.md §6). Same signer and encoding as
    _build_join_state.
    """
    payload = json.dumps({
        "username": username,
        "course_id": course_id,
        "lab_id": lab_id,
    }).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii")
    return signer.sign(payload_b64.encode("ascii")).decode("ascii")


def _parse_join_session(cookie: str | None) -> dict | None:
    """
    Verify and decode a `join_session` cookie.

    Returns:
        The payload, or None if the cookie is missing, forged or expired
    """
    if not cookie:
        return None
    try:
        payload_b64 = signer.unsign(cookie, max_age=JOIN_SESSION_MAX_AGE).decode("ascii")
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
    except (BadSignature, ValueError, TypeError, KeyError):
        return None

    if not isinstance(payload, dict) or not payload.get("username"):
        return None
    return payload


def _set_join_session_cookie(response: Response, username: str, course_id: str, lab_id: str) -> None:
    """Attach the `join_session` cookie to a response (§6 of the plan)."""
    response.set_cookie(
        key=JOIN_SESSION_COOKIE,
        value=_build_join_session(username, course_id, lab_id),
        httponly=True,
        samesite="lax",
        max_age=JOIN_SESSION_MAX_AGE,
        path="/join",
        secure=False,
    )


def require_join_session(request: Request, course_id: str, lab_id: str) -> str:
    """
    The confirmed GitHub username of the student behind a team request.

    The username comes from this cookie and from nowhere else - never from the
    request body, a query parameter or the path. That is the same requirement
    as §3.2 of docs/REPO_GENERATION_PLAN.md: an identity is only ever
    established by the server-side `code -> access_token -> GET /user`
    exchange. Accepting a username from the request would hand out access to a
    private repository under someone else's login.

    The course and lab in the cookie must match the ones in the path, so a
    session obtained for one lab cannot act on another.

    Raises:
        HTTPException(401): with the stable code SESSION_REQUIRED
    """
    payload = _parse_join_session(request.cookies.get(JOIN_SESSION_COOKIE))
    if payload is None:
        raise HTTPException(status_code=401, detail="SESSION_REQUIRED")

    if payload.get("course_id") != course_id or payload.get("lab_id") != lab_id:
        logger.warning(
            "join_session for %s/%s presented for %s/%s",
            payload.get("course_id"), payload.get("lab_id"), course_id, lab_id,
        )
        raise HTTPException(status_code=401, detail="SESSION_REQUIRED")

    return payload["username"]


def _join_result_redirect(course_id: str, lab_id: str, status: str, **extra) -> str:
    """Build the frontend result URL (/join/:courseId/:labId) the student's browser lands on."""
    params = {"status": status, **{k: v for k, v in extra.items() if v is not None}}
    base = FRONTEND_URL.rstrip("/")
    return f"{base}/join/{course_id}/{lab_id}?{urlencode(params)}"


def _secret_join_result_redirect(token: str, status: str, **extra) -> str:
    """
    Same, for a lab reached through a secret link: the student must land back
    on /j/{token} and never on /join/{course}/{lab}, which for such a lab is
    indistinguishable from a lab that does not exist.
    """
    params = {"status": status, **{k: v for k, v in extra.items() if v is not None}}
    return f"{FRONTEND_URL.rstrip('/')}/j/{token}?{urlencode(params)}"


def _result_redirect_for(
    course_id: str,
    lab_id: str,
    lab_key: str,
    lab_config: dict,
    status: str,
    **extra,
) -> str:
    """Result URL for either kind of lab - the token is recomputed, never carried in `state`."""
    if is_secret_lab(lab_config):
        token = lab_token(SECRET_KEY, course_id, lab_key, lab_config)
        return _secret_join_result_redirect(token, status, **extra)
    return _join_result_redirect(course_id, lab_id, status, **extra)


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


# ---------------------------------------------------------------------------
# /j/{token}: the same flow behind a secret, unguessable link.
# See docs/SECRET_JOIN_LINKS_PLAN.md. The prefix is deliberately not /join/:
# /join/{token} would collide with /join/{course_id}/{lab_id} and
# /join/callback.
# ---------------------------------------------------------------------------

# Никакой ответ по секретной ссылке не должен утащить токен в заголовке
# Referer при переходе на github.com (§10 плана). Заголовок ставится и на
# ответы backend, и на саму страницу фронтенда (index.html).
NO_REFERRER_HEADERS = {"Referrer-Policy": "no-referrer"}


def _join_error(status_code: int, code: str, **extra) -> JSONResponse:
    """
    Стабильный код ошибки секретной ссылки (§7.3 плана) - фронтенд переводит
    его сам, как и коды провижининга.
    """
    # `None` остаётся в теле намеренно: у секретной лабы без opens-at времени
    # открытия нет, и фронтенду нужно отличать «времени нет» от «поля нет».
    return JSONResponse(
        status_code=status_code,
        content={"detail": code, **extra},
        headers=NO_REFERRER_HEADERS,
    )


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _load_lab_by_token(token: str) -> tuple[str, dict, str, dict]:
    """
    Найти лабораторную по токену секретной ссылки.

    Returns:
        (course_id, course_info, lab_key, lab_config)

    Raises:
        HTTPException(404): токен не совпал ни с одной лабой - испорчен,
        отозван увеличением revision или просто ничей. Все три случая
        отвечают одинаково: различать их снаружи нельзя.
    """
    resolved = resolve_token(SECRET_KEY, token, iter_course_configs())
    if resolved is None:
        raise HTTPException(status_code=404, detail="LINK_NOT_FOUND")

    course_id, lab_key = resolved
    course_info = get_course_by_id(course_id)
    labs = course_info.get("labs", {})
    lab_config = labs.get(lab_key)
    if not isinstance(lab_config, dict):
        # iter_secret_labs приводит ключ к строке, а незакавыченный ключ в
        # YAML разбирается в число - сверяем по строковому представлению.
        lab_config = next(
            (cfg for key, cfg in labs.items() if str(key) == lab_key and isinstance(cfg, dict)),
            None,
        )
    if not isinstance(lab_config, dict):
        # Конфиг изменился между перечислением и чтением курса.
        raise HTTPException(status_code=404, detail="LINK_NOT_FOUND")

    # Токен в лог не пишется - только курс и ключ лабы.
    logger.info(f"Secret join link resolved to {course_id}/{lab_key}")
    return course_id, course_info, lab_key, lab_config


def _lab_window(course_info: dict, lab_config: dict) -> JoinWindow:
    """
    Окно доступности лабы.

    Raises:
        HTTPException(400): секция join не разбирается - это ошибка
        конфигурации, а не 500
    """
    try:
        return parse_window(lab_config, course_info.get("timezone"))
    except JoinConfigError as e:
        logger.error(f"Некорректная секция join: {e}")
        raise HTTPException(status_code=400, detail="LAB_MISCONFIGURED")


@app.get("/j/{token}")
@limiter.limit("120/minute")
def secret_join_info(request: Request, token: str):
    """
    Публичная информация для лендинга секретной ссылки.

    Лимит намеренно высокий: в момент открытия контрольной вся группа
    заходит сюда одновременно, а за обратным прокси без --proxy-headers все
    30 студентов делят одну корзину (§11 плана). Перебор токена
    ограничивается не этим лимитом, а его длиной - 50 бит.
    """
    try:
        course_id, course_info, lab_key, lab_config = _load_lab_by_token(token)
    except HTTPException as e:
        return _join_error(e.status_code, str(e.detail))

    try:
        window = _lab_window(course_info, lab_config)
    except HTTPException as e:
        return _join_error(e.status_code, str(e.detail))

    state = window.state()
    if state == STATE_NOT_OPEN:
        # До публикации ссылка не раскрывает даже названия работы.
        return _join_error(403, "JOIN_NOT_OPEN", opens_at=_iso(window.opens_at))

    return JSONResponse(
        content={
            "token": token,
            "course_name": course_info.get("name", "Unknown"),
            "lab_short_name": lab_config.get("short-name", lab_key),
            "join_state": state,
            "opens_at": _iso(window.opens_at),
            "closes_at": _iso(window.closes_at),
        },
        headers=NO_REFERRER_HEADERS,
    )


@app.get("/j/{token}/start")
@limiter.limit("20/minute")
def secret_join_start(request: Request, token: str):
    """
    Начинает GitHub OAuth для секретной ссылки.

    После closes-at вход остаётся открытым: новый репозиторий не создастся,
    но студент с уже созданным чинит по этой же ссылке доступ (§8 плана).
    """
    try:
        course_id, course_info, lab_key, lab_config = _load_lab_by_token(token)
        window = _lab_window(course_info, lab_config)
    except HTTPException as e:
        return _join_error(e.status_code, str(e.detail))

    if window.state() == STATE_NOT_OPEN:
        return _join_error(403, "JOIN_NOT_OPEN", opens_at=_iso(window.opens_at))

    if not GITHUB_OAUTH_CLIENT_ID or not GITHUB_OAUTH_CLIENT_SECRET:
        logger.error("GITHUB_OAUTH_CLIENT_ID/GITHUB_OAUTH_CLIENT_SECRET is not configured")
        return _join_error(503, "OAUTH_NOT_CONFIGURED")

    # В state кладётся канонический ключ лабы, а не токен: колбэк вычислит
    # токен заново из конфига (§7.1 плана).
    params = {
        "client_id": GITHUB_OAUTH_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(request),
        "scope": "read:user",
        "state": _build_join_state(course_id, lab_key),
    }
    logger.info(f"Redirecting to GitHub OAuth for secret join {course_id}/{lab_key}")
    return RedirectResponse(
        url=f"https://github.com/login/oauth/authorize?{urlencode(params)}",
        headers=NO_REFERRER_HEADERS,
    )


@app.get("/join/{course_id}/{lab_id}")
@limiter.limit("30/minute")
def join_lab_info(request: Request, course_id: str, lab_id: str):
    """Публичная информация для лендинга страницы присоединения к лабе (без аутентификации)."""
    course_info, _lab_key, lab_config, org, team_config = _load_lab_for_join(course_id, lab_id)

    teams_count = None
    if team_config is not None:
        # Read from the cache only, never fetching: this endpoint is public
        # and unauthenticated, and the count is decorative - the authenticated
        # /teams endpoint below is what actually collects the teams (§8.1).
        cached = _team_registry().cached_teams(org, lab_config.get("github-prefix", ""))
        teams_count = len(cached) if cached is not None else None

    return {
        "course_id": course_id,
        "lab_id": lab_id,
        "course_name": course_info.get("name", "Unknown"),
        "lab_short_name": lab_config.get("short-name", lab_id),
        # Rosters are deliberately absent - this endpoint is public. Only the
        # fact that the lab is a team one, and its limits.
        "team": {
            "enabled": team_config is not None,
            "size_max": team_config.size_max if team_config else None,
            "count_max": team_config.count_max if team_config else None,
            "teams_count": teams_count,
        },
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


# Лимит поднят с 20 до 60 в минуту: через этот же колбэк в очередь на защиту
# входит вся группа разом в начале пары, а за прокси без --proxy-headers все
# студенты делят одну корзину лимита (ср. /j/{token}).
@app.get("/join/callback")
@limiter.limit("60/minute")
def join_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """
    Обрабатывает колбэк GitHub OAuth: проверяет state, получает подтверждённый
    username, создаёт репозиторий из шаблона и чинит доступ студента (§4 плана).

    У OAuth App один адрес колбэка, поэтому сюда же возвращается вход в
    очередь на защиту. State очереди подписан с другой солью: для /join это
    чужая подпись, а state /join не примет очередь.
    """
    queue_state = _parse_queue_state(state)
    if queue_state is not None:
        return _queue_oauth_callback(request, queue_state, code, error)

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
        # public=False: for a secret lab the state is the proof that the
        # student came through the secret link, and the window is checked
        # right below instead.
        course_info, lab_key, lab_config, org, team_config = _load_lab_for_join(
            course_id, lab_id, public=False
        )
    except HTTPException:
        return RedirectResponse(url=_join_result_redirect(course_id, lab_id, "error", reason="config"))

    def result_url(status: str, **extra) -> str:
        return _result_redirect_for(course_id, lab_id, lab_key, lab_config, status, **extra)

    try:
        window = _lab_window(course_info, lab_config)
    except HTTPException:
        return RedirectResponse(url=result_url("error", reason="LAB_MISCONFIGURED"))

    if window.state() == STATE_NOT_OPEN:
        # Ссылка ещё не опубликована (или окно сдвинули, пока студент был на
        # github.com) - репозиторий не создаётся.
        logger.info(f"Join attempt before opens-at for {course_id}/{lab_key}")
        return RedirectResponse(url=result_url("error", reason="JOIN_NOT_OPEN"))

    if not GITHUB_OAUTH_CLIENT_ID or not GITHUB_OAUTH_CLIENT_SECRET:
        logger.error("GITHUB_OAUTH_CLIENT_ID/GITHUB_OAUTH_CLIENT_SECRET is not configured")
        return RedirectResponse(url=result_url("error", reason="oauth_not_configured"))

    username = _exchange_code_for_username(code, _oauth_redirect_uri(request))
    if username is None:
        return RedirectResponse(url=result_url("error", reason="oauth_exchange_failed"))

    logger.info(f"Confirmed GitHub username '{username}' for join {course_id}/{lab_id}")

    if team_config is not None:
        # A team lab creates nothing here: the student still has to pick or
        # create a team. The confirmed username is carried onward in the
        # signed join_session cookie (§8.1 of the team plan).
        response = RedirectResponse(
            url=_join_result_redirect(course_id, lab_id, "authenticated", username=username)
        )
        _set_join_session_cookie(response, username, course_id, lab_id)
        return response

    github_prefix = lab_config.get("github-prefix")
    template_repo = lab_config.get("template-repo")
    repo_provisioning = lab_config.get("repo-provisioning", "template")

    # После closes-at новый репозиторий не создаётся, но студент с уже
    # созданным чинит доступ по той же ссылке (§8 плана).
    create = window.accepts_new_repos()

    try:
        # Server-side token, never the student's OAuth token (see §3.2/§6 of the plan).
        github_client = GitHubClient(GITHUB_TOKEN)
        provisioner = RepoProvisioner(github_client)
        result = provisioner.provision(
            org, github_prefix, template_repo, username, repo_provisioning, create=create
        )
    except Exception:
        logger.exception(f"Unexpected error provisioning repo for {username} in {course_id}/{lab_id}")
        return RedirectResponse(url=result_url("error", reason="provision_failed"))

    if result.status != ProvisionStatus.OK:
        logger.warning(f"Provisioning failed for {username} in {course_id}/{lab_id}: {result.error_code}")
        return RedirectResponse(
            url=result_url("error", reason=result.error_code or "provision_failed")
        )

    logger.info(f"Provisioned {result.repo_url} for {username} ({course_id}/{lab_id})")
    return RedirectResponse(
        url=result_url("success", repo_url=result.repo_url, username=username)
    )


# ---------------------------------------------------------------------------
# /join: team (group) lab assignments - one repository per team
# See docs/TEAM_ASSIGNMENTS_PLAN.md for the full design.
# ---------------------------------------------------------------------------


def _team_registry() -> TeamRegistry:
    """TeamRegistry on the server's token - never the student's OAuth token."""
    return TeamRegistry(GitHubClient(GITHUB_TOKEN))


def _course_teachers(course_info: dict) -> list[str]:
    """`course.github.teachers` - a mixed list of names and GitHub logins."""
    teachers = course_info.get("github", {}).get("teachers") or []
    return [str(entry) for entry in teachers if entry]


def _load_team_lab(course_id: str, lab_id: str) -> tuple[dict, str, dict, str, TeamConfig]:
    """
    Like _load_lab_for_join, but only for a lab that really is a team lab.

    Returns:
        (course_info, lab_key, lab_config, github_organization, team_config).
        `lab_key` is what the team mutations must lock on - see
        _load_lab_for_join.

    Raises:
        HTTPException(400): NOT_A_TEAM_LAB for an individual lab, or
        LAB_NOT_CONFIGURED when the lab has no github-prefix to build team
        repository names from
    """
    course_info, lab_key, lab_config, org, team_config = _load_lab_for_join(course_id, lab_id)
    if team_config is None:
        raise HTTPException(status_code=400, detail="NOT_A_TEAM_LAB")
    if not lab_config.get("github-prefix"):
        raise HTTPException(status_code=400, detail="LAB_NOT_CONFIGURED")
    return course_info, lab_key, lab_config, org, team_config


# Provisioning failures that a student can retry (GitHub-side or transient)
# answer 502; the rest are configuration mistakes and answer 400.
_TEAM_GATEWAY_ERROR_CODES = {
    "TEAMS_UNAVAILABLE",
    "RATE_LIMITED",
    "CREATE_FAILED",
    "FORK_TIMEOUT",
    "FORK_CHECK_FAILED",
    "ACTIONS_ENABLE_FAILED",
    "INVITATIONS_FETCH_FAILED",
    "REINVITE_DELETE_FAILED",
    "INVITE_FAILED",
    "PROVISION_FAILED",
}

# Codes with an HTTP status of their own (§8.3 of the plan).
_TEAM_ERROR_STATUS = {
    "NOT_A_TEAM_LAB": 400,
    "INVALID_TITLE": 400,
    "LAB_NOT_CONFIGURED": 400,
    "TEAM_LIMIT_REACHED": 403,
    "TEAM_NOT_FOUND": 404,
    "ALREADY_IN_TEAM": 409,
    "TEAM_FULL": 409,
    "TITLE_TAKEN": 409,
    "SLUG_RACE": 409,
}


def _team_error_status(error_code: str | None) -> int:
    if error_code in _TEAM_ERROR_STATUS:
        return _TEAM_ERROR_STATUS[error_code]
    return 502 if error_code in _TEAM_GATEWAY_ERROR_CODES else 400


def _team_payload(team: TeamInfo, is_mine: bool, size_max: int | None) -> dict:
    """
    One team as the student's picker sees it.

    `repo_url` is only filled in for the student's own team: a link to a
    private repository they have no access to is useless and misleading.
    Member logins are shown - they are public GitHub identifiers, and they are
    how a student recognizes their groupmates' team. Full names are not: the
    /join flow does not know the student's group and never opens the
    spreadsheet.
    """
    return {
        "slug": team.slug,
        "title": team.title,
        "description": team.description,
        "members": list(team.members),
        "pending": list(team.pending),
        # Subset of `pending`: the invitation expired, yet the place stays held
        # (see TeamRegistry._read_roster).
        "expired": list(team.expired),
        "size": team.size,
        "is_full": size_max is not None and team.size >= size_max,
        "is_mine": is_mine,
        "members_unknown": team.members_unknown,
        "repo_url": team.repo_url if is_mine else None,
    }


@app.get("/join/{course_id}/{lab_id}/teams")
@limiter.limit("30/minute")
def join_lab_teams(request: Request, course_id: str, lab_id: str):
    """Список команд лабы, команда студента и лимиты (см. §8.2 плана)."""
    course_info, _lab_key, lab_config, org, team_config = _load_team_lab(course_id, lab_id)
    username = require_join_session(request, course_id, lab_id)

    registry = _team_registry()
    teams = registry.list_teams(
        org, lab_config["github-prefix"], _course_teachers(course_info)
    )
    if teams is None:
        raise HTTPException(status_code=502, detail="TEAMS_UNAVAILABLE")

    my_team = registry.find_member_team(teams, username)

    return {
        "course_id": course_id,
        "lab_id": lab_id,
        "course_name": course_info.get("name", "Unknown"),
        "lab_short_name": lab_config.get("short-name", lab_id),
        "username": username,
        "size_max": team_config.size_max,
        "count_max": team_config.count_max,
        "can_create": (
            my_team is None
            and (team_config.count_max is None or len(teams) < team_config.count_max)
        ),
        "my_team": my_team.slug if my_team else None,
        "teams": [
            _team_payload(
                team,
                is_mine=my_team is not None and team.slug == my_team.slug,
                size_max=team_config.size_max,
            )
            for team in teams
        ],
    }


class CreateTeamRequest(BaseModel):
    """
    Body of POST /join/{course_id}/{lab_id}/teams.

    There is deliberately no `username` field: the student's identity comes
    from the signed join_session cookie and nowhere else (§6 of the plan).
    Title validation is done by hand in grading/teams.py rather than by a
    pydantic validator - FastAPI would answer its own 422 with a list of
    errors instead of the stable INVALID_TITLE code the frontend translates.
    """
    title: str | None = None
    description: str | None = None


def _team_action_response(result, status_code: int = 200) -> JSONResponse | dict:
    """Turn a TeamActionResult into an HTTP response (§8.3)."""
    if result.status == TeamActionStatus.OK:
        return {
            "status": "ok",
            "slug": result.team.slug if result.team else None,
            "repo_url": result.repo_url,
            "message": result.message,
        }

    payload = {"detail": result.error_code or "PROVISION_FAILED"}
    if result.team is not None:
        # ALREADY_IN_TEAM is actionable only if the student is told which team
        # is theirs, so the slug and the link travel next to the stable code.
        payload["my_team"] = result.team.slug
        payload["repo_url"] = result.repo_url
    return JSONResponse(status_code=_team_error_status(result.error_code), content=payload)


@app.post("/join/{course_id}/{lab_id}/teams")
@limiter.limit("10/minute")
def create_join_team(request: Request, course_id: str, lab_id: str, body: CreateTeamRequest):
    """Создаёт команду и выдаёт доступ к её репозиторию создателю (§7.3 плана)."""
    course_info, lab_key, lab_config, org, team_config = _load_team_lab(course_id, lab_id)
    username = require_join_session(request, course_id, lab_id)

    result = _team_registry().create_team(
        course_id=course_id,
        lab_key=lab_key,
        org=org,
        github_prefix=lab_config["github-prefix"],
        template_repo=lab_config["template-repo"],
        username=username,
        title=body.title,
        description=body.description,
        mode=lab_config.get("repo-provisioning", "template"),
        teachers=_course_teachers(course_info),
        team_config=team_config,
    )
    return _team_action_response(result)


@app.post("/join/{course_id}/{lab_id}/teams/{slug}/join")
@limiter.limit("10/minute")
def join_join_team(request: Request, course_id: str, lab_id: str, slug: str):
    """
    Присоединяет студента к команде либо чинит его доступ, если он уже в ней.

    Имя репозитория собирается сервером из префикса лабы и slug'а, прошедшего
    TEAM_SLUG_RE; из запроса имя репозитория не принимается никогда (§7.4).
    """
    course_info, lab_key, lab_config, org, team_config = _load_team_lab(course_id, lab_id)
    username = require_join_session(request, course_id, lab_id)

    result = _team_registry().join_team(
        course_id=course_id,
        lab_key=lab_key,
        org=org,
        github_prefix=lab_config["github-prefix"],
        template_repo=lab_config["template-repo"],
        username=username,
        slug=slug,
        mode=lab_config.get("repo-provisioning", "template"),
        teachers=_course_teachers(course_info),
        team_config=team_config,
    )
    return _team_action_response(result)


# ---------------------------------------------------------------------------
# Admin: propagate template repository updates to student repos via fork PRs
# (issue #52). Only meaningful for labs with repo-provisioning: fork - a real
# fork network is what lets GitHub build the cross-repo PR.
# ---------------------------------------------------------------------------


def _public_base_url(request: Request) -> str:
    """
    Адрес сервиса, из которого собирается секретная ссылка.

    Порядок важен: за обратным прокси `request.base_url` содержит внутренний
    адрес контейнера, поэтому в продакшене задаётся PUBLIC_BASE_URL, а
    FRONTEND_URL - разумный запасной вариант (§9.1 плана).
    """
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    if FRONTEND_URL:
        return FRONTEND_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


def _join_fields(
    request: Request,
    course_id: str,
    lab_key: str,
    lab_config: dict,
    timezone_str: str | None,
) -> dict:
    """
    Поля секции join для админского списка лаб: готовая ссылка целиком и
    состояние окна (§7.2, §9.1 плана).

    Ссылка отдаётся для любой лабы, у которой настроен `template-repo`:
    секретной - `/j/{token}`, обычной - `/join/{course_id}/{lab_key}`.
    Секретную собрать руками нельзя, поэтому админка вообще единственный
    способ её получить; обычную собрать можно, но раздавать ссылки удобнее
    из одного места, не помня формат.

    Ссылка доступна с момента появления лабы в конфиге, задолго до
    `opens-at`: преподавателю нужно подготовить рассылку заранее.

    Испорченная секция не роняет весь список: лаба приходит с текстом ошибки
    в `join_error`, чтобы преподаватель увидел её там же, где правит конфиг.
    """
    try:
        settings = parse_join_config(lab_config, timezone_str)
    except JoinConfigError as e:
        return {
            "join_link": None,
            "join_state": None,
            "join_secret": is_secret_lab(lab_config),
            "opens_at": None,
            "closes_at": None,
            "join_error": str(e),
        }

    window = JoinWindow(settings.opens_at, settings.closes_at, settings.secret)

    link = None
    if settings.secret:
        link = f"{_public_base_url(request)}/j/{lab_token(SECRET_KEY, course_id, lab_key, lab_config)}"
    elif lab_config.get("template-repo"):
        # Без template-repo ссылка /join/... отдаёт ошибку конфигурации,
        # показывать её в админке незачем.
        link = (
            f"{_public_base_url(request)}/join/"
            f"{quote(course_id, safe='')}/{quote(lab_key, safe='')}"
        )

    return {
        "join_link": link,
        "join_state": window.state(),
        "join_secret": settings.secret,
        "opens_at": _iso(settings.opens_at),
        "closes_at": _iso(settings.closes_at),
        "join_error": None,
    }


@app.get("/admin/courses/{course_id}/labs")
@limiter.limit("30/minute")
def admin_list_course_labs(request: Request, course_id: str, admin: str = Depends(require_admin)):
    """Labs of a course with the fields the admin lab list page needs."""
    course_info = get_course_by_id(course_id)
    labs = course_info.get("labs", {})
    timezone_str = course_info.get("timezone")

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
            # Bulk grading: candidates for the file holding the student's full
            # name, and the one preselected via `student-name-file`.
            "files": lab_config.get("files", []),
            "name_file": lab_config.get("student-name-file"),
            **_join_fields(request, course_id, str(lab_number), lab_config, timezone_str),
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
    # Кому именно рассылать: имена репозиториев из сводки dry-run. None -
    # всем найденным форкам лабы (прежнее поведение). Позволяет обновить
    # только часть репозиториев, например созданные после перехода на fork.
    repos: list[str] | None = None


@app.post("/admin/courses/{course_id}/labs/{lab_id}/propagate-template-update")
@limiter.limit("10/minute")
def propagate_template_update(
    request: Request,
    course_id: str,
    lab_id: str,
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
    _start_background_job(
        run_propagation,
        job, github_client, org, github_prefix, template_repo, body.repos,
        name=f"propagate-{job.job_id}",
    )
    return JSONResponse(status_code=202, content={"job_id": job.job_id})


@app.get("/admin/propagate-jobs/{job_id}")
@limiter.limit("60/minute")
def get_propagate_job_status(request: Request, job_id: str, admin: str = Depends(require_admin)):
    job = get_propagate_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    return job.to_dict()


# ---------------------------------------------------------------------------
# Admin: bulk grading of a whole group's submissions for one lab.
# Same job machinery as propagate above - a group takes minutes, so the run is
# backgrounded and polled. See docs/PROJECT_DESCRIPTION.md.
# ---------------------------------------------------------------------------


class BulkGradeRequest(BaseModel):
    # Файл, из первой строки которого берётся ФИО студента. Задан - обходятся
    # все репозитории лабы в организации и логины проставляются в таблицу;
    # пуст - проверяются только студенты с уже указанным логином.
    name_file: str | None = None
    # Прогнать все проверки и собрать отчёт, ничего не записывая в таблицу.
    dry_run: bool = False


def _open_group_worksheet(spreadsheet_id: str, group_id: str):
    """
    Open a group's worksheet, returning (spreadsheet, worksheet).

    Opened by the endpoint rather than inside the job, so that a wrong group
    fails the request with 404 instead of a job that dies immediately.
    """
    client = sheets_client()

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(group_id)
    except Exception as e:
        logger.error(f"Failed to open worksheet '{group_id}': {str(e)}")
        raise HTTPException(status_code=404, detail="Группа не найдена в Google Таблице")

    return spreadsheet, worksheet


@app.post("/admin/courses/{course_id}/groups/{group_id}/labs/{lab_id}/bulk-grade")
@limiter.limit("10/minute")
def start_bulk_grade(
    request: Request,
    course_id: str,
    group_id: str,
    lab_id: str,
    body: BulkGradeRequest = BulkGradeRequest(),
    admin: str = Depends(require_admin),
):
    """
    Start grading a whole group for one lab in the background.

    Every student goes through the same checks as a self-submitted work
    (grading.bulk.evaluate_student). Unlike propagate's read-only dry run,
    dry_run here still runs the full CI checks, so it is a background job too;
    it just writes nothing to the spreadsheet.

    Returns 202 with a job_id to poll via GET /admin/bulk-grade-jobs/{job_id};
    only one run per (course_id, group_id, lab_id) at a time - a second POST
    while one is in flight gets HTTP 409 with that run's `job_id`, so the
    admin page can attach to it instead of leaving the teacher with nothing
    but an error.
    """
    name_file = (body.name_file or "").strip() or None
    mode = "by_file" if name_file else "by_sheet"

    course_info = get_course_by_id(course_id)
    org = course_info.get("github", {}).get("organization")
    spreadsheet_id = course_info.get("google", {}).get("spreadsheet")

    resolved = find_lab_config(course_info.get("labs", {}), lab_id)
    lab_key, lab_config_dict = resolved if resolved else (None, {})
    repo_prefix = lab_config_dict.get("github-prefix")

    if not all([org, spreadsheet_id, repo_prefix]):
        logger.error(
            f"Missing course configuration for {course_id}: org={org}, "
            f"spreadsheet={spreadsheet_id}, repo_prefix={repo_prefix}"
        )
        raise HTTPException(status_code=400, detail="Missing course configuration")

    if mode == "by_file" and is_team_lab(lab_config_dict):
        # One repository holds one name file for several students, so a name
        # cannot resolve a row for the whole team (§10.3 of the team plan).
        raise HTTPException(
            status_code=400,
            detail=(
                "Для командной лабораторной работы сопоставление по файлу с ФИО неприменимо: "
                "проверяются студенты с указанным в таблице логином GitHub"
            ),
        )

    spreadsheet, worksheet = _open_group_worksheet(spreadsheet_id, group_id)

    job = try_start_bulk_job(course_id, group_id, lab_id, mode, body.dry_run, name_file)
    if job is None:
        running = get_running_bulk_job(course_id, group_id, lab_id)
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Проверка этой лабораторной для этой группы уже выполняется",
                "job_id": running.job_id if running else None,
            },
        )

    logger.info(
        f"Starting bulk grading job {job.job_id} for {course_id}/{group_id}/{lab_id} "
        f"(mode={mode}, dry_run={body.dry_run}, admin={admin})"
    )
    try:
        github_client = GitHubClient(GITHUB_TOKEN)
        _start_background_job(
            run_bulk_grading,
            job,
            LabGrader(github_client),
            github_client,
            worksheet,
            spreadsheet,
            course_info,
            lab_config_dict,
            # Номер берём из ключа конфига, и его отсутствие не повод
            # отказывать: он нужен только лабе без short-name.
            optional_lab_number(lab_key or lab_id),
            name=bulk_worker_thread_name(job.job_id),
        )
    except BaseException:
        # Работа уже зарегистрирована и держит группу с лабораторной занятыми:
        # если запустить её не удалось, отпускаем сразу.
        logger.exception(f"Bulk grading job {job.job_id} could not be started")
        fail_bulk_job(job, "Не удалось запустить проверку. Загляните в логи бэкенда")
        raise
    return JSONResponse(status_code=202, content={"job_id": job.job_id})


@app.get("/admin/bulk-grade-jobs/{job_id}")
@limiter.limit("120/minute")
def get_bulk_grade_job_status(request: Request, job_id: str, admin: str = Depends(require_admin)):
    job = get_bulk_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    return job.to_dict()


@app.post("/admin/bulk-grade-jobs/{job_id}/cancel")
@limiter.limit("30/minute")
def cancel_bulk_grade_job(request: Request, job_id: str, admin: str = Depends(require_admin)):
    """
    Ask a running bulk grading job to stop.

    It finishes the student it is on, flushes the grades buffered so far and
    ends with status "cancelled".
    """
    job = request_bulk_job_cancel(job_id)
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
    # Секция queue проверяется так же, как в редакторе: файл подхватывается сразу.
    _validate_queue_section(course_data)

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

# ---------------------------------------------------------------------------
# Defense queue (issue #62): students queue up to defend a lab, the teacher
# calls them one by one. The logic lives in grading/defense_queue.py (SQLite
# in grading/queue_store.py, schedule sources in grading/schedule/, read-only
# spreadsheet lookups in grading/queue_roster.py); here are only HTTP,
# cookies and rate limits. The queue never writes to Google Sheets.
# ---------------------------------------------------------------------------

_QUEUE_STORE: QueueStore | None = None
_queue_roster = queue_roster.CourseRoster()

# Ошибки очереди - стабильные коды в `detail`, фронтенд переводит их сам.
_QUEUE_ERROR_STATUS = {
    code: status_code
    for status_code, codes in {
        400: ("INVALID_OUTCOME", "INVALID_GROUPS", "INVALID_TIME", "INVALID_PERIOD", "QUEUE_MISCONFIGURED"),
        401: ("SESSION_REQUIRED",),
        403: ("NOT_YOUR_GROUP", "STUDENT_NOT_FOUND", "BAD_ORIGIN"),
        404: (
            "QUEUE_DISABLED", "SESSION_NOT_FOUND", "LAB_NOT_FOUND", "ENTRY_NOT_FOUND",
            "NOT_IN_QUEUE", "COURSE_NOT_FOUND",
        ),
        409: (
            "SIGNUP_CLOSED", "LAB_NOT_PASSED", "LAB_ALREADY_DEFENDED", "ALREADY_IN_QUEUE",
            "ALREADY_CALLED", "NOTHING_TO_SKIP", "QUEUE_EMPTY", "QUEUE_CHANGED",
            "ENTRY_NOT_CALLED", "DAY_OVER", "STUDENT_AMBIGUOUS",
        ),
        503: ("SHEETS_UNAVAILABLE", "STORAGE_UNAVAILABLE"),
    }.items()
    for code in codes
}

QUEUE_OAUTH_COOKIE = "queue_oauth"
QUEUE_OAUTH_MAX_AGE = 600
# Подпись cookie очереди старше суток недействительна в любом случае; сам
# вход живёт до конца дня по поясу курса (поле "e").
QUEUE_SESSION_MAX_AGE = 24 * 3600
QUEUE_SESSION_MIN_SECONDS = 60
# Если Sheets не ответил, для входа и списка работ годится копия таблицы не
# старше получаса. При записи в очередь копия старше минуты не
# подставляется: нужна актуальная ячейка.
QUEUE_ROSTER_STALE_SECONDS = 1800
QUEUE_LAB_PARAM_MAX_LENGTH = 64
QUEUE_HISTORY_DEFAULT_DAYS = 30
# Страница очереди преподавателя продлевает admin_session, пока открыта
# (сессия кончается не позже ADMIN_SESSION_MAX_LIFETIME от входа).
ADMIN_SESSION_RENEW_AFTER_SECONDS = 600
# Больше SQLite INTEGER не хранит: такой id заведомо ничему не соответствует.
QUEUE_ID_MAX = 2 ** 63 - 1

# Отдельные соли: state и cookie очереди нельзя выдать за state /join или
# admin_session (и наоборот), хотя ключ у всех один - SECRET_KEY.
queue_state_signer = TimestampSigner(SECRET_KEY, salt="defense-queue-state")
queue_session_signer = TimestampSigner(SECRET_KEY, salt="defense-queue-session")


@app.exception_handler(QueueError)
async def queue_error_handler(request: Request, exc: QueueError) -> JSONResponse:
    """A refused queue operation: {"detail": CODE} with the status of the code."""
    return JSONResponse(
        status_code=_QUEUE_ERROR_STATUS.get(exc.code, 400),
        content={"detail": exc.code},
    )


# id в пути и теле запросов очереди - целое из 1..QUEUE_ID_MAX. Любой другой
# id означает то же, что ответила бы база: такой сессии или записи нет.
QueueIdPath = Annotated[int, Path(ge=1, le=QUEUE_ID_MAX)]
QueueIdField = Annotated[int, Field(ge=1, le=QUEUE_ID_MAX)]
_QUEUE_ID_CODES = {
    "session_id": "SESSION_NOT_FOUND",
    "entry_id": "ENTRY_NOT_FOUND",
    # Такой записи нет - значит, первой в очереди стоит не она.
    "expected_entry_id": "QUEUE_CHANGED",
}


@app.exception_handler(RequestValidationError)
async def queue_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A bad id in a queue request is a stable code, like the rest of the queue; anything else stays 422."""
    route_path = getattr(request.scope.get("route"), "path", "")
    if "/queue" in route_path:
        for error in exc.errors():
            loc = error.get("loc") or ()
            code = _QUEUE_ID_CODES.get(loc[-1]) if loc else None
            if code is not None:
                return JSONResponse(status_code=_QUEUE_ERROR_STATUS[code], content={"detail": code})
    return await request_validation_exception_handler(request, exc)


def _queue_store() -> QueueStore:
    """The queue database at QUEUE_DB_PATH, opened on first use."""
    global _QUEUE_STORE
    # Без блокировки: QueueStore не держит соединений, и два объекта на
    # одном файле, созданные одновременно, друг другу не мешают.
    if _QUEUE_STORE is None:
        _QUEUE_STORE = QueueStore(QUEUE_DB_PATH)
    return _QUEUE_STORE


def _defense_queue() -> DefenseQueue:
    return DefenseQueue(_queue_store())


def _queue_now() -> datetime:
    """Current time of the queue endpoints (tests replace it)."""
    return datetime.now(timezone.utc)


def _utc_offset_minutes(tz) -> int:
    return int(tz.utcoffset(None).total_seconds() // 60)


@dataclass(frozen=True)
class QueueViewer:
    """The signed-in student, as the course's queue cookie says."""
    github: str
    login: str      # casefolded, the queue's identity key
    group: str
    name: str


# --- Course and schedule ---------------------------------------------------


def _queue_course(course_id: str) -> dict:
    try:
        return get_course_by_id(course_id)
    except HTTPException as e:
        if e.status_code == 404:
            raise QueueError("COURSE_NOT_FOUND")
        raise


def _schedule_source(settings: QueueSettings):
    return build_schedule_source(
        settings.schedule_cfg,
        SourceContext(snapshots=_queue_store(), course_tz=settings.tz),
    )


def _queue_settings(course_id: str, course_info: dict) -> tuple[QueueSettings | None, str | None]:
    """(settings or None if the queue is off, text of the config error or None)."""
    try:
        return parse_queue_config(course_info), None
    except QueueConfigError as e:
        return None, str(e)
    except Exception as e:
        # Например, модуль источника расписания не импортируется (в образе
        # нет зависимости). Это ошибка развёртывания, но страница
        # преподавателя всё равно должна открыться и показать её.
        logger.exception(f"Очередь курса {course_id}: не удалось разобрать секцию queue")
        return None, f"{type(e).__name__}: {e}"


def _course_status(course_info: dict) -> str:
    return (course_info.get("_meta") or {}).get("status", "active")


def _queue_enabled(course_info: dict) -> bool:
    """The course is active and its `queue` section says `enabled: true`."""
    return queue_flag(course_info) and _course_status(course_info) == "active"


def _queue_student_context(course_id: str) -> tuple[dict, QueueSettings, Any]:
    """
    Course, queue settings and schedule source for the student endpoints
    and the teacher's manual opening.

    Raises:
        QueueError: COURSE_NOT_FOUND; QUEUE_DISABLED when the course is not
        active or the queue is off; QUEUE_MISCONFIGURED for a broken `queue`
        section (`enabled: "true"` included - that is not "off")
    """
    course_info = _queue_course(course_id)
    if _course_status(course_info) != "active":
        raise QueueError("QUEUE_DISABLED")
    settings, config_error = _queue_settings(course_id, course_info)
    if config_error is not None:
        logger.warning(f"Очередь курса {course_id} настроена с ошибкой: {config_error}")
        raise QueueError("QUEUE_MISCONFIGURED")
    if settings is None:
        raise QueueError("QUEUE_DISABLED")
    return course_info, settings, _schedule_source(settings)


def _queue_admin_context(course_id: str) -> tuple[dict, QueueSettings | None, str | None]:
    """
    Course and queue settings for the teacher: only the course has to exist.

    The day, the history and the actions on existing entries work from the
    database whatever the course status, and even with a broken config -
    the error is returned to be shown on the page.

    Returns:
        (course_info, settings or None, text of the config error or None)
    """
    course_info = _queue_course(course_id)
    return (course_info, *_queue_settings(course_id, course_info))


def _queue_tz(course_info: dict, settings: QueueSettings | None) -> timezone:
    if settings is not None:
        return settings.tz
    try:
        return parse_utc_offset(course_info.get("timezone"))
    except QueueConfigError:
        # Без пояса дни очереди не посчитать; UTC - только чтобы показать
        # преподавателю историю и ошибку конфигурации.
        return timezone.utc


def _today(course_id: str, settings: QueueSettings, source, now: datetime) -> tuple[date, list | UnknownDay | None]:
    """
    The course's day and today's lessons from the schedule source.

    The sessions themselves are created by the view that gets the lessons
    (student_view / day_view), in the same read.

    Returns:
        (the course's day, today's lessons - None when the schedule is
        unknown right now: no session is created then and nothing about
        missing lessons is concluded; UnknownDay when it is unknown, but
        the config cancels lessons that day)
    """
    day = course_day(now, settings.tz)
    try:
        return day, source.lessons_on(day, now)
    except ScheduleUnavailable:
        # Ни свежего расписания, ни сохранённой копии: новые сессии не
        # создаются, уже созданные и ручные работают как работали. Источник
        # сам пишет причину в лог, преподаватель видит её в `schedule`.
        pass
    except Exception:
        logger.exception(f"Очередь курса {course_id}: ошибка источника расписания {source.name}")
    # Отмены из конфига от источника не зависят: в сессию, созданную раньше,
    # отменённая группа не записывается и без расписания.
    changes = parse_changes((settings.schedule_cfg or {}).get("changes"), SourceContext(None, settings.tz))
    return day, changes.unknown_day(day)


def _lesson_keys(lessons) -> set[str] | UnknownDay | None:
    if lessons is None or isinstance(lessons, UnknownDay):
        return lessons
    return {lesson.key for lesson in lessons}


def _schedule_json(source, now: datetime) -> dict:
    status = source.status(now)
    return {
        "source": status.source,
        "state": status.state,
        "fetched_at": None if status.fetched_at is None else iso_utc(status.fetched_at),
        "error": status.error,
    }


def _student_state(
    course_id: str,
    course_info: dict,
    settings: QueueSettings,
    source,
    viewer: QueueViewer | None,
    now: datetime,
    today: tuple[date, list | None] | None = None,
) -> dict:
    """Today's queue as the student page shows it (docs/PROJECT_DESCRIPTION.md, «Очередь на защиту»)."""
    if today is None or today[0] != course_day(now, settings.tz):
        today = _today(course_id, settings, source, now)
    day, lessons = today
    view = _defense_queue().student_view(
        course_id,
        day,
        now,
        login=viewer.login if viewer else None,
        group=viewer.group if viewer else None,
        current_keys=_lesson_keys(lessons),
        settings=settings,
        lessons=lessons,
    )
    return {
        "course_id": course_id,
        "course_name": course_info.get("name", "Unknown"),
        "utc_offset_minutes": _utc_offset_minutes(settings.tz),
        "now": iso_utc(now),
        "day": day.isoformat(),
        "schedule": _schedule_json(source, now),
        "viewer": None if viewer is None else {
            "github": viewer.github,
            "name": viewer.name,
            "group": viewer.group,
        },
        **view,
    }


def _admin_queue_state(
    course_id: str,
    course_info: dict,
    settings: QueueSettings | None,
    config_error: str | None,
    now: datetime,
) -> dict:
    """
    Today's sessions with every entry, for the teacher's page.

    Sessions are materialized from the schedule only for a course whose
    queue works (valid config, enabled, active course); otherwise the page
    shows what the database already has, with signup closed: students get
    QUEUE_DISABLED or QUEUE_MISCONFIGURED there.
    """
    enabled = _queue_enabled(course_info)
    tz = _queue_tz(course_info, settings)
    working = settings if enabled else None
    day, lessons, schedule = course_day(now, tz), None, None
    if working is not None:
        source = _schedule_source(working)
        day, lessons = _today(course_id, working, source, now)
        schedule = _schedule_json(source, now)
    sessions = _defense_queue().day_view(
        course_id, day, now, _lesson_keys(lessons), settings=working, lessons=lessons
    )
    if working is None:
        for session in sessions:
            session["signup"] = SIGNUP_CLOSED

    return {
        "course_id": course_id,
        "course_name": course_info.get("name", "Unknown"),
        "course_status": _course_status(course_info),
        "utc_offset_minutes": _utc_offset_minutes(tz),
        "now": iso_utc(now),
        "day": day.isoformat(),
        "queue_enabled": enabled,
        "config_error": config_error,
        "schedule": schedule,
        "sessions": sessions,
    }


# --- Student sign-in: GitHub OAuth through the shared /join/callback --------


def _encode_payload(payload: dict) -> bytes:
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))


def _decode_payload(value):
    if isinstance(value, str):
        value = value.encode("ascii")
    return json.loads(base64.urlsafe_b64decode(value))


def _course_cookie_name(prefix: str, course_id: str) -> str:
    """
    Name of a per-course cookie, different for different courses.

    An id of [A-Za-z0-9_.-] goes as is (prefix_id); any other id (Cyrillic,
    spaces) is base64url without padding after "~", which no plain id has.
    """
    if re.fullmatch(r"[A-Za-z0-9_.-]+", course_id):
        return f"{prefix}_{course_id}"
    encoded = base64.urlsafe_b64encode(course_id.encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"{prefix}~{encoded}"


def _queue_cookie_name(course_id: str) -> str:
    # Своя cookie на каждый курс: в очередях двух курсов студент сидит независимо.
    return _course_cookie_name("queue_session", course_id)


def _queue_oauth_cookie_name(course_id: str) -> str:
    # Тоже по курсу: два входа в двух вкладках не затирают nonce друг друга.
    return _course_cookie_name(QUEUE_OAUTH_COOKIE, course_id)


def _parse_queue_session(cookie: str | None, course_id: str, now: datetime) -> QueueViewer | None:
    """The student of a queue cookie, or None if it is missing, forged, expired or of another course."""
    if not cookie:
        return None
    try:
        payload = _decode_payload(queue_session_signer.unsign(cookie, max_age=QUEUE_SESSION_MAX_AGE))
    except (BadSignature, ValueError, TypeError):
        return None

    if not isinstance(payload, dict) or payload.get("c") != course_id:
        return None
    github, expires = payload.get("u"), payload.get("e")
    if not isinstance(github, str) or not github.strip():
        return None
    if isinstance(expires, bool) or not isinstance(expires, (int, float)) or expires <= now.timestamp():
        return None
    return QueueViewer(
        github=github,
        login=normalize_login(github),
        group=str(payload.get("g") or ""),
        name=str(payload.get("n") or ""),
    )


def _optional_queue_viewer(request: Request, course_id: str, now: datetime | None = None) -> QueueViewer | None:
    """The signed-in student of this course, or None for an anonymous visitor."""
    return _parse_queue_session(
        request.cookies.get(_queue_cookie_name(course_id)), course_id, now or _queue_now()
    )


def require_queue_session(request: Request, course_id: str, now: datetime | None = None) -> QueueViewer:
    """
    The confirmed student behind a queue request.

    Same rule as require_join_session: the login comes from the signed
    cookie and from nowhere else - never from the body, a query parameter or
    the path. The cookie must be of this course and of today.

    Raises:
        QueueError: SESSION_REQUIRED
    """
    viewer = _optional_queue_viewer(request, course_id, now)
    if viewer is None:
        raise QueueError("SESSION_REQUIRED")
    return viewer


def _set_queue_session_cookie(
    response: Response,
    viewer: QueueViewer,
    course_id: str,
    tz: timezone,
    now: datetime,
) -> None:
    """Sign the student in until the end of the course's day."""
    day_end = day_start(course_day(now, tz), tz) + timedelta(days=1)
    payload = {
        "u": viewer.github,
        "c": course_id,
        "g": viewer.group,
        "n": viewer.name,
        "e": int(day_end.timestamp()),
    }
    response.set_cookie(
        key=_queue_cookie_name(course_id),
        value=queue_session_signer.sign(_encode_payload(payload)).decode("ascii"),
        httponly=True,
        samesite="lax",
        max_age=max(int((day_end - now).total_seconds()), QUEUE_SESSION_MIN_SECONDS),
        # Path=/: за обратным прокси backend отвечает с префиксом /api/v1,
        # и cookie с путём /courses/... браузер туда бы не отправил.
        path="/",
        secure=False,
    )


def _queue_rate_key(request: Request) -> str:
    """
    Rate-limit key of the student queue endpoints: the student, not the address.

    A whole group often sits behind one NAT (or a proxy without
    --proxy-headers), and per-address limits would make thirty students
    share one bucket. Only a valid signed cookie of this course counts;
    anything else falls back to the address.
    """
    course_id = request.path_params.get("course_id")
    viewer = _optional_queue_viewer(request, course_id) if isinstance(course_id, str) else None
    if viewer is not None:
        return f"qs:{viewer.login}"
    return f"ip:{get_remote_address(request) or 'unknown'}"


def _origin(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        # Например, «http://[»: такой Origin ничему не равен.
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def _check_queue_origin(request: Request) -> None:
    """
    Accept queue requests only from the service's own pages.

    CORS here allows any origin with credentials, so another site (or a
    neighbouring subdomain, where SameSite=Lax cookies still go) could sign
    a student up, call the next one or read the names and the history. GETs
    are checked too: a browser always sends Origin cross-origin. No Origin
    (curl, tests) passes, and so does the request's own Host with any scheme:
    behind a TLS proxy request.base_url is http while the page is https.

    Raises:
        QueueError: BAD_ORIGIN
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = {_origin(FRONTEND_URL), _origin(PUBLIC_BASE_URL), _origin(str(request.base_url))}
    allowed.discard(None)
    normalized = _origin(origin)
    host = request.headers.get("host", "").strip().lower()
    if normalized in allowed or (normalized and host and normalized.split("://", 1)[1] == host):
        return
    # Чаще всего это фронтенд разработчика (npm run dev на :5173) при
    # FRONTEND_URL по умолчанию: подсказка, что поменять.
    hint = ""
    if normalized and normalized.isprintable():
        hint = f"; задайте FRONTEND_URL={normalized}, если это ваш фронтенд"
    logger.warning(f"Запрос к очереди {request.url.path} с чужого Origin {origin!r} отклонён{hint}")
    raise QueueError("BAD_ORIGIN")


def _queue_result_redirect(course_id: str, status: str, **extra) -> RedirectResponse:
    """Back to the frontend queue page (/course/:courseId/queue) after sign-in."""
    params = {"status": status, **{k: v for k, v in extra.items() if v}}
    base = FRONTEND_URL.rstrip("/")
    return RedirectResponse(url=f"{base}/course/{quote(course_id, safe='')}/queue?{urlencode(params)}")


def _parse_queue_state(state: str | None):
    """
    Decode the OAuth `state` of a queue sign-in, if it is one.

    Returns:
        the payload dict; ("expired", course_id) for a genuine queue state
        older than JOIN_STATE_MAX_AGE; None for anything else (a /join
        state, a forgery, garbage), which the regular /join flow handles
    """
    if not state:
        return None
    try:
        payload = _decode_payload(queue_state_signer.unsign(state, max_age=JOIN_STATE_MAX_AGE))
    except SignatureExpired as e:
        # Подпись настоящая, но устарела: курс известен, и студента можно
        # вернуть на страницу его очереди, а не на общую /join/error.
        try:
            payload = _decode_payload(e.payload)
        except (ValueError, TypeError):
            return None
        if isinstance(payload, dict) and payload.get("purpose") == "queue" and isinstance(payload.get("course_id"), str):
            return ("expired", payload["course_id"])
        return None
    except (BadSignature, ValueError, TypeError):
        return None

    if (
        not isinstance(payload, dict)
        or payload.get("purpose") != "queue"
        or not isinstance(payload.get("course_id"), str)
        or not isinstance(payload.get("nonce"), str)
    ):
        return None
    return payload


def _queue_student_record(
    course_info: dict,
    github: str,
    *,
    fresh: bool = False,
    allow_stale_seconds: float = 0,
) -> queue_roster.StudentRecord:
    """
    The student's row in the course spreadsheet (read-only, cached).

    Raises:
        QueueError: STUDENT_NOT_FOUND, STUDENT_AMBIGUOUS, SHEETS_UNAVAILABLE
    """
    try:
        return _queue_roster.lookup(
            course_info,
            github,
            queue_sheets_client,
            fresh=fresh,
            allow_stale_seconds=allow_stale_seconds,
        )
    except queue_roster.RosterError as e:
        raise QueueError(e.code)
    except queue_roster.RosterUnavailable:
        raise QueueError("SHEETS_UNAVAILABLE")


def _queue_viewer_for_login(course_id: str, course_info: dict, github: str, now: datetime) -> QueueViewer:
    """
    Who the student who has just signed in through GitHub is.

    A student added to the spreadsheet a minute ago gets one fresh re-read.
    If Sheets is down, a student who already stands in the queue still gets
    in - with the name and group of their entry - so that a restart or a
    lost cookie in the middle of a lesson does not lock them out.

    Raises:
        QueueError: STUDENT_NOT_FOUND, STUDENT_AMBIGUOUS, SHEETS_UNAVAILABLE
    """
    try:
        try:
            student = _queue_student_record(
                course_info, github, allow_stale_seconds=QUEUE_ROSTER_STALE_SECONDS
            )
        except QueueError as e:
            if e.code != "STUDENT_NOT_FOUND":
                raise
            student = _queue_student_record(
                course_info, github, fresh=True, allow_stale_seconds=QUEUE_ROSTER_STALE_SECONDS
            )
    except QueueError as e:
        if e.code != "SHEETS_UNAVAILABLE":
            raise
        entry = _defense_queue().active_entry(course_id, github, now)
        if entry is None:
            raise
        return QueueViewer(github=github, login=normalize_login(github), group=entry["group"], name=entry["name"])
    return QueueViewer(github=student.github, login=student.login, group=student.group, name=student.name)


def _queue_oauth_callback(request: Request, queue_state, code: str | None, error: str | None) -> RedirectResponse:
    """
    Second half of the queue sign-in started by queue_login.

    The login comes only from the code -> token -> GET /user exchange, the
    name and group from the course spreadsheet. Every failure lands on the
    queue page with status=error&reason=<code>.
    """
    if isinstance(queue_state, tuple):
        _expired, course_id = queue_state
        return _queue_result_redirect(course_id, "error", reason="state_expired")

    course_id = queue_state["course_id"]
    lab = queue_state.get("lab") if isinstance(queue_state.get("lab"), str) else None

    # Nonce из state должен совпасть с cookie браузера, начавшего вход:
    # иначе чужой code, подсунутый ссылкой, вошёл бы под чужим логином.
    oauth_cookie = _queue_oauth_cookie_name(course_id)
    cookie_nonce = request.cookies.get(oauth_cookie) or ""
    if not secrets.compare_digest(cookie_nonce.encode("utf-8"), queue_state["nonce"].encode("utf-8")):
        logger.warning(f"Queue sign-in for {course_id}: state does not match the queue_oauth cookie")
        return _queue_result_redirect(course_id, "error", reason="state_mismatch", lab=lab)

    def finish(status: str, **extra) -> RedirectResponse:
        response = _queue_result_redirect(course_id, status, lab=lab, **extra)
        response.delete_cookie(oauth_cookie, path="/")
        return response

    if error:
        logger.info(f"Student declined GitHub OAuth for the defense queue of {course_id}: {error!r}")
        return finish("error", reason="access_denied")
    if not code:
        return finish("error", reason="missing_code")

    try:
        course_info, settings, _source = _queue_student_context(course_id)
    except QueueError as e:
        return finish("error", reason=e.code)

    if not GITHUB_OAUTH_CLIENT_ID or not GITHUB_OAUTH_CLIENT_SECRET:
        logger.error("GITHUB_OAUTH_CLIENT_ID/GITHUB_OAUTH_CLIENT_SECRET is not configured")
        return finish("error", reason="oauth_not_configured")

    username = _exchange_code_for_username(code, _oauth_redirect_uri(request))
    if username is None:
        return finish("error", reason="oauth_exchange_failed")

    now = _queue_now()
    try:
        viewer = _queue_viewer_for_login(course_id, course_info, username, now)
    except QueueError as e:
        logger.info(f"GitHub user '{username}' cannot enter the defense queue of {course_id}: {e.code}")
        return finish("error", reason=e.code)

    logger.info(f"Confirmed GitHub username '{username}' for the defense queue of {course_id}")
    response = finish("ok")
    _set_queue_session_cookie(response, viewer, course_id, settings.tz, now)
    return response


@app.get("/courses/{course_id}/queue/login")
@limiter.limit("60/minute")
def queue_login(request: Request, course_id: str, lab: str | None = None):
    """
    Start the student's GitHub sign-in to the course's queue.

    A browser navigation, not a fetch: problems are sent back to the queue
    page as status=error&reason=<code>, the way the callback reports them,
    instead of a bare JSON error in the student's tab. `lab` is only carried
    through to preselect the lab on return.
    """
    try:
        _queue_student_context(course_id)
    except QueueError as e:
        return _queue_result_redirect(course_id, "error", reason=e.code)

    if not GITHUB_OAUTH_CLIENT_ID or not GITHUB_OAUTH_CLIENT_SECRET:
        logger.error("GITHUB_OAUTH_CLIENT_ID/GITHUB_OAUTH_CLIENT_SECRET is not configured")
        return _queue_result_redirect(course_id, "error", reason="oauth_not_configured")

    nonce = secrets.token_urlsafe(16)
    if lab is not None and len(lab) > QUEUE_LAB_PARAM_MAX_LENGTH:
        lab = None
    state = queue_state_signer.sign(_encode_payload({
        "purpose": "queue",
        "course_id": course_id,
        "nonce": nonce,
        "lab": lab,
    })).decode("ascii")
    params = {
        "client_id": GITHUB_OAUTH_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(request),
        "scope": "read:user",
        "state": state,
    }
    logger.info(f"Redirecting to GitHub OAuth for the defense queue of {course_id}")
    response = RedirectResponse(url=f"https://github.com/login/oauth/authorize?{urlencode(params)}")
    response.set_cookie(
        key=_queue_oauth_cookie_name(course_id),
        value=nonce,
        httponly=True,
        samesite="lax",
        max_age=QUEUE_OAUTH_MAX_AGE,
        path="/",
        secure=False,
    )
    return response


@app.post("/courses/{course_id}/queue/logout")
@limiter.limit("20/minute", key_func=_queue_rate_key)
def queue_logout(request: Request, course_id: str):
    """Sign the student out of the course's queue (drops the queue cookie)."""
    _check_queue_origin(request)
    response = JSONResponse(content={"status": "logged_out"})
    response.delete_cookie(_queue_cookie_name(course_id), path="/")
    return response


# --- Student ---------------------------------------------------------------


class QueueSignupRequest(BaseModel):
    """
    Body of POST /courses/{course_id}/queue/entries.

    There is deliberately no login or group here: the student is the one in
    the signed queue cookie, and the group comes from the course spreadsheet.
    """
    session_id: QueueIdField
    lab_id: str


# Вошедший студент считается отдельно, а до входа - по адресу: вся группа
# за одним NAT открывает страницу разом и опрашивает её раз в минуту.
@app.get("/courses/{course_id}/queue")
@limiter.limit("120/minute", key_func=_queue_rate_key)
def queue_state(request: Request, course_id: str):
    """
    Today's queue of the course, polled by the student page.

    Never reads Google Sheets or GitHub: the student's name and group come
    from the queue cookie, everything else from the queue database.
    """
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, source = _queue_student_context(course_id)
    viewer = _optional_queue_viewer(request, course_id, now)
    return _student_state(course_id, course_info, settings, source, viewer, now)


@app.get("/courses/{course_id}/queue/labs")
@limiter.limit("20/minute", key_func=_queue_rate_key)
def queue_labs(request: Request, course_id: str):
    """
    The labs the student can pick when signing up, with their status.

    Statuses come from the cached spreadsheet copy (`fetched_at` tells how
    old it is) and from the queue history; the sign-up itself checks the
    cell again.
    """
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, _source = _queue_student_context(course_id)
    viewer = require_queue_session(request, course_id, now)
    student = _queue_student_record(
        course_info, viewer.github, allow_stale_seconds=QUEUE_ROSTER_STALE_SECONDS
    )
    defended = _defense_queue().defended_labs(course_id, viewer.login, now)

    labs = []
    for lab_key, lab_config in visible_lab_items(course_info, now):
        short_name = lab_config.get("short-name")
        if not short_name:
            # Без short-name у работы нет столбца в таблице.
            continue
        status = lab_cell_status(student.labs.get(str(short_name), ""), settings.defended_prefix)
        if lab_key in defended:
            status = LAB_DEFENDED
        labs.append({"id": lab_key, "short_name": str(short_name), "status": status})

    spreadsheet_id = (course_info.get("google") or {}).get("spreadsheet")
    fetched_at = _queue_roster.fetched_at(spreadsheet_id)
    return {"fetched_at": None if fetched_at is None else iso_utc(fetched_at), "labs": labs}


@app.post("/courses/{course_id}/queue/entries")
@limiter.limit("20/minute", key_func=_queue_rate_key)
def queue_sign_up(request: Request, course_id: str, body: QueueSignupRequest):
    """
    Put the signed-in student at the end of a session's queue.

    Only a lab that passed the service's check can be defended (cell `v...`,
    see lab_cell_status). The spreadsheet is read before the queue
    transaction, so a slow Sheets request never holds the database lock.
    """
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, source = _queue_student_context(course_id)
    viewer = require_queue_session(request, course_id, now)

    resolved = find_visible_lab_config(course_info, body.lab_id, now)
    if resolved is None or not resolved[1].get("short-name"):
        raise QueueError("LAB_NOT_FOUND")
    lab_key, short_name = resolved[0], str(resolved[1]["short-name"])

    student = _queue_student_record(course_info, viewer.github)
    status = lab_cell_status(student.labs.get(short_name, ""), settings.defended_prefix)
    if status == LAB_NOT_PASSED:
        # Отметка могла появиться только что (в другом процессе или руками
        # преподавателя), а копия таблицы - старше: перечитываем один раз.
        student = _queue_student_record(course_info, viewer.github, fresh=True)
        status = lab_cell_status(student.labs.get(short_name, ""), settings.defended_prefix)
    if status == LAB_DEFENDED:
        raise QueueError("LAB_ALREADY_DEFENDED")
    if status == LAB_NOT_PASSED:
        raise QueueError("LAB_NOT_PASSED")

    # Время записи и проверка окна - после всех обращений к сети (таблица,
    # у guap ещё и расписание): место в очереди даёт момент транзакции, и
    # время записи должно с ним совпадать, а запрос, начатый до конца пары и
    # дошедший до базы после него, уже опоздал.
    today = _today(course_id, settings, source, _queue_now())
    now = _queue_now()
    _defense_queue().sign_up(
        course_id, body.session_id, student, lab_key, short_name, now, current_keys=_lesson_keys(today[1])
    )
    logger.info(f"Defense queue {course_id}: '{student.github}' signed up for {lab_key} (session {body.session_id})")
    return _student_state(course_id, course_info, settings, source, viewer, now, today)


@app.post("/courses/{course_id}/queue/leave")
@limiter.limit("20/minute", key_func=_queue_rate_key)
def queue_leave(request: Request, course_id: str):
    """Leave the queue while still waiting; a called student is marked by the teacher."""
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, source = _queue_student_context(course_id)
    viewer = require_queue_session(request, course_id, now)
    _defense_queue().leave(course_id, viewer.login, now)
    return _student_state(course_id, course_info, settings, source, viewer, now)


@app.post("/courses/{course_id}/queue/skip")
@limiter.limit("20/minute", key_func=_queue_rate_key)
def queue_skip(request: Request, course_id: str):
    """Let the student right behind go first (swap places); moving forward is impossible."""
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, source = _queue_student_context(course_id)
    viewer = require_queue_session(request, course_id, now)
    _defense_queue().skip(course_id, viewer.login, now)
    return _student_state(course_id, course_info, settings, source, viewer, now)


# --- Teacher ---------------------------------------------------------------


class QueueManualSessionRequest(BaseModel):
    """
    Body of POST /admin/courses/{course_id}/queue/sessions.

    Types are checked by hand: FastAPI would answer its own 422, while the
    page translates the stable INVALID_GROUPS / INVALID_TIME.
    """
    groups: Any = None          # ["4431"], [] = every group of the course
    closes_at: Any = None       # "HH:MM" in the course timezone, None = end of the day


class QueueCallNextRequest(BaseModel):
    """Body of call-next: the head the teacher sees, so a repeat or a second tab calls no one extra."""
    expected_entry_id: QueueIdField | None = None


class QueueOutcomeRequest(BaseModel):
    """Body of .../outcome; checked by DefenseQueue.set_outcome to answer INVALID_OUTCOME, not 422."""
    outcome: Any = None


def _renew_admin_session(request: Request, response: Response) -> None:
    """
    Re-issue admin_session while the teacher keeps the queue page open.

    The session lasts an hour, a lesson with defenses is longer. A stolen
    cookie must not live forever, so the session ends at most
    ADMIN_SESSION_MAX_LIFETIME after the password sign-in (the signed
    admin_session_start cookie): the last renewal is one ADMIN_SESSION_MAX_AGE
    before that, and without admin_session_start nothing is renewed.
    Same cookie parameters as admin_login.
    """
    cookie = request.cookies.get("admin_session")
    start = request.cookies.get(ADMIN_SESSION_START_COOKIE)
    if not cookie or not start:
        return
    try:
        login, signed_at = signer.unsign(cookie, max_age=ADMIN_SESSION_MAX_AGE, return_timestamp=True)
        started_by = admin_start_signer.unsign(
            start, max_age=ADMIN_SESSION_MAX_LIFETIME - ADMIN_SESSION_MAX_AGE
        )
    except BadSignature:
        return
    if started_by != login:
        return
    if time.time() - signed_at.timestamp() < ADMIN_SESSION_RENEW_AFTER_SECONDS:
        return
    response.set_cookie(
        key="admin_session",
        value=signer.sign(login).decode(),
        httponly=True,
        max_age=ADMIN_SESSION_MAX_AGE,
        path="/",
        secure=False,
    )
    # Сам опрос в журнал доступа не попадает (QueuePollAccessFilter), продление - да.
    logger.info(f"admin_session of '{login.decode()}' renewed by {request.url.path}")


def _admin_queue_action(request: Request, course_id: str, action) -> tuple[dict, dict]:
    """
    Run one teacher action and answer with the fresh day state.

    Works from the database whatever the course status and config, so
    entries already in the queue can be finished even after the course was
    archived or its config broken.

    Returns:
        (day state as GET /admin/courses/{id}/queue, result of the action)
    """
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, config_error = _queue_admin_context(course_id)
    result = action(_defense_queue(), now)
    return _admin_queue_state(course_id, course_info, settings, config_error, now), result


@app.get("/admin/courses/{course_id}/queue")
@limiter.limit("120/minute")
def admin_queue_day(request: Request, response: Response, course_id: str, admin: str = Depends(require_admin)):
    """Today's sessions of the course with every entry (polled by the teacher's page)."""
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, config_error = _queue_admin_context(course_id)
    state = _admin_queue_state(course_id, course_info, settings, config_error, now)
    _renew_admin_session(request, response)
    return state


@app.post("/admin/courses/{course_id}/queue/sessions")
@limiter.limit("60/minute")
def admin_queue_open_manual(
    request: Request,
    course_id: str,
    body: QueueManualSessionRequest,
    admin: str = Depends(require_admin),
):
    """
    Open signup by hand from now on: a moved or an extra lesson.

    `groups: []` opens one queue for every group of the course. Needs a
    working queue (enabled, active course, valid config).
    """
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, _source = _queue_student_context(course_id)
    closes_at = parse_closes_at(body.closes_at, now, settings.tz)
    session = _defense_queue().open_manual(course_id, settings, body.groups, closes_at, now)
    logger.info(f"Defense queue {course_id}: manual session {session['id']} opened for {session['groups'] or 'all groups'}")
    return {**_admin_queue_state(course_id, course_info, settings, None, now), "session": session}


@app.post("/admin/courses/{course_id}/queue/sessions/{session_id}/close")
@limiter.limit("60/minute")
def admin_queue_close_signup(
    request: Request, course_id: str, session_id: QueueIdPath, admin: str = Depends(require_admin)
):
    """Stop new signups; those already waiting can still be called."""
    state, session = _admin_queue_action(
        request, course_id, lambda queue, now: queue.close_signup(course_id, session_id, now)
    )
    return {**state, "session": session}


@app.post("/admin/courses/{course_id}/queue/sessions/{session_id}/reopen")
@limiter.limit("60/minute")
def admin_queue_reopen_signup(
    request: Request, course_id: str, session_id: QueueIdPath, admin: str = Depends(require_admin)
):
    """Undo "close signup" while the session's signup window lasts."""
    state, session = _admin_queue_action(
        request, course_id, lambda queue, now: queue.reopen_signup(course_id, session_id, now)
    )
    return {**state, "session": session}


@app.post("/admin/courses/{course_id}/queue/sessions/{session_id}/finish")
@limiter.limit("60/minute")
def admin_queue_finish_session(
    request: Request, course_id: str, session_id: QueueIdPath, admin: str = Depends(require_admin)
):
    """Finish the reception ("Завершить приём"): close signup and expire everyone still waiting."""
    state, session = _admin_queue_action(
        request, course_id, lambda queue, now: queue.finish_session(course_id, session_id, now)
    )
    return {**state, "session": session}


@app.post("/admin/courses/{course_id}/queue/sessions/{session_id}/call-next")
@limiter.limit("60/minute")
def admin_queue_call_next(
    request: Request,
    course_id: str,
    session_id: QueueIdPath,
    body: QueueCallNextRequest = QueueCallNextRequest(),
    admin: str = Depends(require_admin),
):
    """Call the head of the queue; several students may be called at once."""
    state, entry = _admin_queue_action(
        request,
        course_id,
        lambda queue, now: queue.call_next(course_id, session_id, now, body.expected_entry_id),
    )
    return {**state, "entry": entry}


@app.post("/admin/courses/{course_id}/queue/entries/{entry_id}/outcome")
@limiter.limit("60/minute")
def admin_queue_set_outcome(
    request: Request,
    course_id: str,
    entry_id: QueueIdPath,
    body: QueueOutcomeRequest = QueueOutcomeRequest(),
    admin: str = Depends(require_admin),
):
    """
    Record how a defense ended (or fix a wrong click).

    Only the queue history changes: the grade in the spreadsheet stays the
    teacher's business.
    """
    state, entry = _admin_queue_action(
        request, course_id, lambda queue, now: queue.set_outcome(course_id, entry_id, body.outcome, now)
    )
    return {**state, "entry": entry}


@app.post("/admin/courses/{course_id}/queue/entries/{entry_id}/return")
@limiter.limit("60/minute")
def admin_queue_return(request: Request, course_id: str, entry_id: QueueIdPath, admin: str = Depends(require_admin)):
    """Put a called or no-show student back at their old place (same day only)."""
    state, entry = _admin_queue_action(
        request, course_id, lambda queue, now: queue.return_to_queue(course_id, entry_id, now)
    )
    return {**state, "entry": entry}


@app.get("/admin/courses/{course_id}/queue/history")
@limiter.limit("120/minute")
def admin_queue_history(
    request: Request,
    response: Response,
    course_id: str,
    day_from: str | None = Query(None, alias="from"),
    day_to: str | None = Query(None, alias="to"),
    group: str | None = None,
    q: str | None = None,
    lab: str | None = None,
    admin: str = Depends(require_admin),
):
    """
    Queue entries of a period (YYYY-MM-DD, inclusive, course timezone), newest first.

    Without dates - the last 30 days. Works for an archived course and
    with a broken config too: the history is in the database. At most
    HISTORY_LIMIT entries; `truncated` says that there were more.

    Raises:
        QueueError: INVALID_PERIOD - not a date, a date out of
        HISTORY_FIRST_DAY..HISTORY_LAST_DAY, `from` after `to`, or a period
        longer than HISTORY_MAX_DAYS
    """
    _check_queue_origin(request)
    now = _queue_now()
    course_info, settings, _config_error = _queue_admin_context(course_id)
    tz = _queue_tz(course_info, settings)
    last = parse_history_day(day_to) if day_to else course_day(now, tz)
    default_first = max(last - timedelta(days=QUEUE_HISTORY_DEFAULT_DAYS - 1), HISTORY_FIRST_DAY)
    first = parse_history_day(day_from) if day_from else default_first
    if first > last:
        raise QueueError("INVALID_PERIOD")

    entries, truncated = _defense_queue().history(
        course_id, first, last, tz, now, group=group, query=q, lab_key=lab
    )
    _renew_admin_session(request, response)
    return {"entries": entries, "from": first.isoformat(), "to": last.isoformat(), "truncated": truncated}
