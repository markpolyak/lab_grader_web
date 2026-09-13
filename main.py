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
from urllib.parse import quote, urlencode
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
# Public address of the service, used to build the secret /j/{token} links the
# admin page shows. Behind a reverse proxy request.base_url can hold the
# container's internal address, so production should set this explicitly
# (docs/SECRET_JOIN_LINKS_PLAN.md §9.1).
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
# Max age (seconds) for the signed OAuth `state` param - see docs/REPO_GENERATION_PLAN.md §3.3.
JOIN_STATE_MAX_AGE = 600
# Student session issued by /join/callback for team labs, so that the team
# endpoints can take the confirmed username from a signed cookie and never
# from the request - see docs/TEAM_ASSIGNMENTS_PLAN.md §6.
JOIN_SESSION_COOKIE = "join_session"
JOIN_SESSION_MAX_AGE = 1800

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


def visible_labs(course_info: dict, now: datetime | None = None) -> list[dict]:
    """Конфиги лаб курса, открытых студенту сейчас (см. find_visible_lab_config)."""
    labs = course_info.get("labs", {})
    if not isinstance(labs, dict):
        return []

    visible = []
    for lab_key in labs:
        resolved = find_visible_lab_config(course_info, str(lab_key), now)
        if resolved is not None:
            visible.append(resolved[1])
    return visible


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
    # Работа, не достигшая join.opens-at, в список не попадает: до публикации
    # её не должно быть видно даже по названию столбца (§5, §7.2 плана).
    labs = [lab["short-name"] for lab in visible_labs(course_info) if "short-name" in lab]

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
    background_tasks.add_task(
        run_propagation, job, github_client, org, github_prefix, template_repo, body.repos
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
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    sheets_client = gspread.authorize(creds)

    try:
        spreadsheet = sheets_client.open_by_key(spreadsheet_id)
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
    background_tasks: BackgroundTasks,
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
    while one is in flight gets HTTP 409.
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
        raise HTTPException(
            status_code=409,
            detail="Проверка этой лабораторной для этой группы уже выполняется",
        )

    logger.info(
        f"Starting bulk grading job {job.job_id} for {course_id}/{group_id}/{lab_id} "
        f"(mode={mode}, dry_run={body.dry_run}, admin={admin})"
    )
    github_client = GitHubClient(GITHUB_TOKEN)
    background_tasks.add_task(
        run_bulk_grading,
        job,
        LabGrader(github_client),
        github_client,
        worksheet,
        spreadsheet,
        course_info,
        lab_config_dict,
        parse_lab_id(lab_key or lab_id),
    )
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