#!/usr/bin/env python3
"""
Интерактивное консольное меню для типичных задач lab-grader.

Запуск из корня репозитория:

  python scripts/menu.py

Сохраняет последние course/group в .labgrader_cli.json (в gitignore).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / ".labgrader_cli.json"
SCRIPTS = ROOT / "scripts"
FRONTEND_DIR = ROOT / "frontend" / "courses-front"
LOG_DIR = ROOT / "logs"
BACKEND_PORT = 8000
FRONTEND_PORT_START = 5173
FRONTEND_PORT_END = 5190

sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def clear_hint() -> None:
    print()


def pause() -> None:
    input("\n[Enter] вернуться в меню… ")


def ask(prompt: str, default: str | None = None) -> str:
    if default:
        raw = input(f"{prompt} [{default}]: ").strip()
        return raw or default
    while True:
        raw = input(f"{prompt}: ").strip()
        if raw:
            return raw
        print("  Нужно значение.")


def ask_optional(prompt: str, default: str = "") -> str:
    raw = input(f"{prompt} [{default}]: ").strip() if default else input(f"{prompt} (пусто = пропуск): ").strip()
    return raw if raw else default


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "д", "да", "1")


def pick_from_list(title: str, items: list[tuple[str, str]], *, allow_custom: bool = False) -> str | None:
    """
    items: list of (id, label). Returns chosen id or None if cancelled.
    """
    if not items and not allow_custom:
        print("  Список пуст.")
        return None
    print(f"\n{title}")
    for i, (item_id, label) in enumerate(items, 1):
        print(f"  {i}) {item_id}  —  {label}")
    if allow_custom:
        print("  c) ввести вручную")
    print("  0) отмена")
    raw = input("Выбор: ").strip().lower()
    if raw in ("", "0"):
        return None
    if allow_custom and raw == "c":
        return ask("Введите значение")
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        return items[int(raw) - 1][0]
    print("  Неверный выбор.")
    return None


# ---------------------------------------------------------------------------
# State / course loading
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"  (не удалось сохранить состояние: {exc})")


def list_courses() -> list[tuple[str, str]]:
    index_path = ROOT / "courses" / "index.yaml"
    with open(index_path, encoding="utf-8") as f:
        index = yaml.safe_load(f) or {}
    out: list[tuple[str, str]] = []
    for item in index.get("courses", []):
        cid = item.get("id")
        if not cid:
            continue
        status = item.get("status", "?")
        out.append((cid, f"{item.get('file', '')} [{status}]"))
    return out


def load_course_config(course_id: str) -> dict:
    index_path = ROOT / "courses" / "index.yaml"
    with open(index_path, encoding="utf-8") as f:
        index = yaml.safe_load(f) or {}
    entry = next((c for c in index.get("courses", []) if c.get("id") == course_id), None)
    if not entry:
        raise SystemExit(f"Курс '{course_id}' не найден в index.yaml")
    path = ROOT / "courses" / entry["file"]
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["course"]


def lab_choices(course: dict) -> list[tuple[str, str]]:
    labs = course.get("labs") or {}
    items: list[tuple[str, str]] = []
    for lid, cfg in labs.items():
        if not isinstance(cfg, dict):
            continue
        short = cfg.get("short-name") or f"ЛР{lid}"
        plag = "plagiarism" if (cfg.get("plagiarism") is not None or cfg.get("moss") is not None) else "—"
        files = ",".join(cfg.get("files") or []) or "—"
        items.append((str(lid), f"{short}  files={files}  {plag}"))
    return items


def pick_course(state: dict) -> str | None:
    courses = list_courses()
    default = state.get("course_id")
    # Put default first if present
    if default:
        courses = sorted(courses, key=lambda x: 0 if x[0] == default else 1)
    chosen = pick_from_list("Курс:", courses, allow_custom=True)
    if chosen:
        state["course_id"] = chosen
        save_state(state)
    return chosen


def pick_labs(course: dict) -> list[str] | None:
    items = lab_choices(course)
    print("\nЛабораторные:")
    for i, (lid, label) in enumerate(items, 1):
        print(f"  {i}) {lid}  —  {label}")
    print("  a) все с files:")
    print("  0) отмена")
    raw = input("Номера через пробел (например: 0 1 2 3) или a: ").strip().lower()
    if raw in ("", "0"):
        return None
    if raw == "a":
        return [
            lid
            for lid, cfg in (course.get("labs") or {}).items()
            if isinstance(cfg, dict) and cfg.get("files")
        ]
    # support both indices and lab ids
    chosen: list[str] = []
    id_by_index = {str(i): lid for i, (lid, _) in enumerate(items, 1)}
    valid_ids = {lid for lid, _ in items}
    for tok in raw.replace(",", " ").split():
        if tok in id_by_index:
            chosen.append(id_by_index[tok])
        elif tok in valid_ids:
            chosen.append(tok)
        else:
            print(f"  Пропуск неизвестного: {tok}")
    return chosen or None


def run_script(script_name: str, args: list[str]) -> int:
    cmd = [sys.executable, str(SCRIPTS / script_name), *args]
    print("\n→", " ".join(cmd))
    print("-" * 60)
    result = subprocess.run(cmd, cwd=str(ROOT))
    print("-" * 60)
    print(f"Код выхода: {result.returncode}")
    return result.returncode


def env_status() -> None:
    keys = [
        "GITHUB_TOKEN",
        "ADMIN_LOGIN",
        "ADMIN_PASSWORD",
        "SECRET_KEY",
        "CREDENTIALS_FILE",
        "PLAGIARISM_SHADOW_MODE",
        "PLAGIARISM_CACHE_DIR",
        "PLAGIARISM_DB_PATH",
    ]
    print("\nОкружение (.env):")
    for key in keys:
        val = os.environ.get(key)
        if not val:
            print(f"  {key}= (не задано)")
        elif "TOKEN" in key or "PASSWORD" in key or "SECRET" in key:
            print(f"  {key}=***{val[-4:] if len(val) >= 4 else '****'}")
        else:
            print(f"  {key}={val}")
    cred = os.environ.get("CREDENTIALS_FILE", "credentials.json")
    print(f"  credentials exists: {Path(cred).is_file()}")
    jar = ROOT / "tools" / "jplag.jar"
    print(f"  tools/jplag.jar: {jar.is_file()} ({jar})")
    print(f"  last course: {load_state().get('course_id')}")
    print(f"  last group:  {load_state().get('group')}")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def action_grade_student(state: dict) -> None:
    print("\n=== Оценка лаб студента (Sheets + плагиат/notes) ===")
    course_id = pick_course(state)
    if not course_id:
        return
    group = ask("Группа (лист таблицы)", state.get("group", "4333K"))
    state["group"] = group
    save_state(state)
    github = ask("GitHub логин студента", state.get("github", ""))
    state["github"] = github
    save_state(state)

    course = load_course_config(course_id)
    labs = pick_labs(course)
    if labs is None:
        return

    shadow = os.environ.get("PLAGIARISM_SHADOW_MODE", "")
    print(f"\nPLAGIARISM_SHADOW_MODE={shadow!r}")
    if shadow.lower() in ("1", "true", "yes"):
        print("  Внимание: shadow — notes в таблицу НЕ пишутся.")

    args = ["--course", course_id, "--group", group, "--github", github, "--labs", *labs]
    if not ask_yes_no("Запустить оценку?", True):
        return
    run_script("grade_student_labs.py", args)


def action_batch_plagiarism(state: dict) -> None:
    print("\n=== Пакетный плагиат по курсу (без записи в Sheets) ===")
    course_id = pick_course(state)
    if not course_id:
        return
    course = load_course_config(course_id)
    labs = pick_labs(course)
    args = ["--course", course_id]
    if labs:
        args += ["--labs", *labs]
    if ask_yes_no("Пропустить additional (прошлые годы)?", False):
        args.append("--skip-additional")
    max_repos = ask_optional("Лимит репозиториев на лабу (пусто = все)", "")
    if max_repos.isdigit():
        args += ["--max-repos", max_repos]
    if not ask_yes_no("Запустить?", True):
        return
    run_script("plagiarism_batch_course.py", args)


def action_show_matches(state: dict) -> None:
    print("\n=== Совпадения из SQLite ===")
    from grading.plagiarism_store import list_matches

    course_id = pick_course(state)
    if not course_id:
        return
    course = load_course_config(course_id)
    labs = pick_labs(course)
    if not labs:
        return
    threshold_raw = ask("Мин. similarity (0..1)", "0.6")
    try:
        threshold = float(threshold_raw.replace(",", "."))
    except ValueError:
        threshold = 0.6
    only_unreviewed = ask_yes_no("Только без review?", False)

    for lab_id in labs:
        rows = list_matches(
            course_id,
            lab_id,
            min_similarity=threshold,
            include_reviewed=not only_unreviewed,
        )
        short = ((course.get("labs") or {}).get(lab_id) or {}).get("short-name") or lab_id
        print(f"\n{short} (lab {lab_id}): {len(rows)} пар ≥ {threshold}")
        for m in rows[:40]:
            rev = "✓" if m.reviewed_by_teacher else " "
            print(f"  [{rev}] {m.similarity:5.1%}  {m.student_a} ↔ {m.student_b}")
        if len(rows) > 40:
            print(f"  … ещё {len(rows) - 40}")


def action_compare_engines(state: dict) -> None:
    print("\n=== Сравнение compare50 vs JPlag ===")
    jar = ROOT / "tools" / "jplag.jar"
    if not jar.is_file():
        print(f"Нет {jar}")
        print("Скачайте jplag-*-jar-with-dependencies.jar с GitHub releases в tools/jplag.jar")
        return
    course_id = pick_course(state)
    if not course_id:
        return
    course = load_course_config(course_id)
    labs = pick_labs(course)
    if not labs:
        return
    language = ask("Язык JPlag (cpp / python3 / …)", "cpp")
    top = ask("Top-N для overlap", "25")
    for lab_id in labs:
        args = [
            "--course", course_id,
            "--lab", lab_id,
            "--language", language,
            "--top", top,
            "--threshold", "0.6",
            "--max-matches", "200",
        ]
        run_script("compare_engines_jplag_compare50.py", args)


def action_local_demo(_: dict) -> None:
    print("\n=== Локальный demo плагиата (без Sheets) ===")
    if ask_yes_no("Синтетический demo (без сети)?", True):
        run_script("plagiarism_local_demo.py", [])
    else:
        print("Для режима repos используйте вручную:")
        print("  python scripts/plagiarism_local_demo.py repos --repos ... --files ...")


def action_shadow_hint(_: dict) -> None:
    print("\n=== Shadow mode ===")
    print(f"Сейчас PLAGIARISM_SHADOW_MODE={os.environ.get('PLAGIARISM_SHADOW_MODE')!r}")
    print("Меняется в файле .env:")
    print("  PLAGIARISM_SHADOW_MODE=true   — считать, но не писать notes")
    print("  PLAGIARISM_SHADOW_MODE=false  — писать notes в таблицу")
    print("После правки .env перезапустите меню (load_dotenv при старте).")


# ---------------------------------------------------------------------------
# Admin panel: start backend + frontend
# ---------------------------------------------------------------------------

def _http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _detached_popen(cmd: list[str], *, cwd: Path, log_path: Path) -> subprocess.Popen:
    """Start a long-running process with stdout/stderr appended to a log file."""
    log_f = open(log_path, "a", encoding="utf-8")
    env = os.environ.copy()
    # Windows consoles/workers often default to cp1251; emoji in main.py prints crash uvicorn --reload child
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    kwargs: dict = {
        "cwd": str(cwd),
        "stdout": log_f,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _ensure_frontend_deps() -> bool:
    if (FRONTEND_DIR / "node_modules").is_dir():
        return True
    print("node_modules нет — ставлю зависимости (npm.cmd install --legacy-peer-deps)…")
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    proc = subprocess.run(
        [npm, "install", "--legacy-peer-deps"],
        cwd=str(FRONTEND_DIR),
    )
    if proc.returncode != 0:
        print("  Не удалось установить зависимости фронта.")
        return False
    return True


def _find_running_frontend_port() -> int | None:
    for port in range(FRONTEND_PORT_START, FRONTEND_PORT_END + 1):
        if _http_ok(f"http://127.0.0.1:{port}/"):
            return port
    return None


def _wait_backend(timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _http_ok(f"http://127.0.0.1:{BACKEND_PORT}/docs"):
            return True
        time.sleep(0.5)
    return False


def _start_backend(state: dict) -> bool:
    if _http_ok(f"http://127.0.0.1:{BACKEND_PORT}/docs"):
        print(f"  Backend уже работает: http://127.0.0.1:{BACKEND_PORT}")
        return True

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "menu_backend.log"
    log_path.write_text("", encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--reload",
        "--host",
        "127.0.0.1",
        "--port",
        str(BACKEND_PORT),
    ]
    print("  Запуск backend:", " ".join(cmd))
    proc = _detached_popen(cmd, cwd=ROOT, log_path=log_path)
    state["backend_pid"] = proc.pid
    save_state(state)
    print(f"  PID={proc.pid}, лог: {log_path}")
    print("  Жду готовности…")
    if _wait_backend():
        print(f"  Backend готов: http://127.0.0.1:{BACKEND_PORT}")
        return True
    print("  Backend не ответил вовремя. Смотри лог:", log_path)
    return False


def _start_frontend(state: dict) -> int | None:
    existing = _find_running_frontend_port()
    if existing is not None:
        print(f"  Frontend уже работает: http://127.0.0.1:{existing}")
        return existing

    if not FRONTEND_DIR.is_dir():
        print(f"  Нет каталога {FRONTEND_DIR}")
        return None
    if not _ensure_frontend_deps():
        return None

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "menu_frontend.log"
    log_path.write_text("", encoding="utf-8")

    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    cmd = [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(FRONTEND_PORT_START)]
    print("  Запуск frontend:", " ".join(cmd))
    proc = _detached_popen(cmd, cwd=FRONTEND_DIR, log_path=log_path)
    state["frontend_pid"] = proc.pid
    save_state(state)
    print(f"  PID={proc.pid}, лог: {log_path}")
    print("  Жду URL Vite…")

    local_re = re.compile(r"Local:\s+(https?://\S+)", re.I)
    port_re = re.compile(r":(\d+)/?")
    deadline = time.time() + 90.0
    while time.time() < deadline:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        m = local_re.search(text)
        if m:
            url = m.group(1).rstrip("/")
            pm = port_re.search(url)
            port = int(pm.group(1)) if pm else FRONTEND_PORT_START
            if _http_ok(f"http://127.0.0.1:{port}/"):
                print(f"  Frontend готов: {url}")
                return port
        found = _find_running_frontend_port()
        if found is not None:
            print(f"  Frontend готов: http://127.0.0.1:{found}")
            return found
        if proc.poll() is not None:
            print("  Frontend процесс завершился. Лог:")
            print(text[-2000:] or "(пусто)")
            return None
        time.sleep(0.5)

    print("  Frontend не поднялся вовремя. Смотри лог:", log_path)
    return None


def action_start_admin(state: dict) -> None:
    print("\n=== Запуск админ-панели (backend + frontend) ===")
    print("Нужны: Python (uvicorn), Node.js / npm.cmd")

    ok_back = _start_backend(state)
    if not ok_back:
        print("Без backend логин в админку не сработает.")
        if not ask_yes_no("Всё равно пробовать поднять frontend?", True):
            return

    port = _start_frontend(state)
    if port is None:
        print("Не удалось определить URL фронта.")
        return

    admin_url = f"http://127.0.0.1:{port}/admin"
    courses_url = f"http://127.0.0.1:{port}/admin/courses"
    api_url = f"http://127.0.0.1:{BACKEND_PORT}/docs"
    state["admin_url"] = admin_url
    state["frontend_port"] = port
    save_state(state)

    print()
    print("=" * 60)
    print("  Админ-панель готова")
    print(f"  URL:     {admin_url}")
    print(f"  Курсы:   {courses_url}")
    print(f"  API:     {api_url}")
    print(f"  Логин:   из .env  ADMIN_LOGIN / ADMIN_PASSWORD")
    print("=" * 60)
    print()
    print("Серверы работают в фоне (закрой окна/процессы вручную, когда закончишь).")

    if ask_yes_no("Открыть админку в браузере?", True):
        try:
            webbrowser.open(admin_url)
            print("  Браузер открыт.")
        except Exception as exc:
            print(f"  Не удалось открыть браузер: {exc}")
            print(f"  Скопируй URL: {admin_url}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

MENU = """
╔══════════════════════════════════════════════════╗
║           Lab Grader — консольное меню           ║
╚══════════════════════════════════════════════════╝
  1) Оценить лабы студента  (Sheets + плагиат/notes)
  2) Пакетный плагиат по курсу  (кэш + SQLite, без Sheets)
  3) Показать совпадения из SQLite
  4) Сравнить compare50 vs JPlag
  5) Локальный demo плагиата
  6) Статус окружения (.env, jplag, …)
  7) Подсказка по SHADOW_MODE
  8) Запустить админ-панель (backend + frontend) → URL
  0) Выход
"""


def main() -> int:
    os.chdir(ROOT)
    state = load_state()
    actions = {
        "1": action_grade_student,
        "2": action_batch_plagiarism,
        "3": action_show_matches,
        "4": action_compare_engines,
        "5": action_local_demo,
        "6": lambda s: env_status(),
        "7": action_shadow_hint,
        "8": action_start_admin,
    }

    while True:
        shadow = os.environ.get("PLAGIARISM_SHADOW_MODE", "")
        admin = state.get("admin_url") or "—"
        print(MENU)
        print(f"  course={state.get('course_id') or '—'}  group={state.get('group') or '—'}  "
              f"SHADOW={shadow or 'off'}")
        print(f"  admin_url={admin}")
        choice = input("\nКоманда: ").strip()
        if choice in ("0", "q", "quit", "exit"):
            print("Пока.")
            return 0
        action = actions.get(choice)
        if not action:
            print("Неизвестная команда.")
            continue
        try:
            action(state)
        except KeyboardInterrupt:
            print("\n(прервано)")
        except Exception as exc:
            print(f"\nОшибка: {exc}")
        pause()


if __name__ == "__main__":
    raise SystemExit(main())
