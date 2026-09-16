#!/usr/bin/env python3
"""
Напечатать секретные ссылки на получение репозитория (docs/SECRET_JOIN_LINKS_PLAN.md §9.2).

Запасной способ получить ту же ссылку, что показывает админка на странице
/admin/courses/{course_id}/labs: он нужен, когда преподаватель и так работает
с конфигами локально или на сервере, а также чтобы сверить ссылку, не заходя
в админку.

Собственной логики здесь нет - и ссылка, и состояние окна считаются теми же
функциями grading/join_links.py, что и на сервере, поэтому разойтись с ним
скрипт не может.

Примеры:
    python scripts/join-link.py                        # все секретные лабы всех курсов
    python scripts/join-link.py --course os-2026       # только один курс
    python scripts/join-link.py --course os-2026 --lab 7
    python scripts/join-link.py --base-url https://labgrader.example.ru

SECRET_KEY берётся из окружения или из .env рядом с конфигами; без него
ссылку вычислить нельзя.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.course_index import iter_course_configs  # noqa: E402
from grading.join_links import (  # noqa: E402
    STATE_CLOSED,
    STATE_NOT_OPEN,
    JoinConfigError,
    is_secret_lab,
    lab_token,
    parse_join_config,
    parse_window,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_secret_key(env_file: str) -> str | None:
    """SECRET_KEY из окружения, иначе из .env (той же переменной, что у сервера)."""
    key = os.getenv("SECRET_KEY")
    if key:
        return key

    if not os.path.exists(env_file):
        return None

    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "SECRET_KEY":
                return value.strip().strip('"').strip("'")
    return None


def describe_window(lab_config: dict, timezone_str: str | None) -> str:
    """Состояние окна одной строкой."""
    try:
        window = parse_window(lab_config, timezone_str)
    except JoinConfigError as e:
        return f"ошибка конфигурации: {e}"

    state = window.state()
    if state == STATE_NOT_OPEN:
        if window.opens_at is None:
            return "не открыта (join.opens-at не задан)"
        return f"откроется {window.opens_at:%d.%m.%Y %H:%M}"
    if state == STATE_CLOSED:
        return f"приём закрыт {window.closes_at:%d.%m.%Y %H:%M}"
    if window.closes_at is not None:
        return f"открыта, приём до {window.closes_at:%d.%m.%Y %H:%M}"
    return "открыта, бессрочно"


def matches_lab(lab_key: str, lab_config: dict, wanted: str | None) -> bool:
    if wanted is None:
        return True
    return wanted in (lab_key, str(lab_config.get("short-name")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Секретные ссылки на получение репозитория")
    parser.add_argument("--course", help="id курса из courses/index.yaml")
    parser.add_argument("--lab", help="ключ лабы в конфиге или её short-name")
    parser.add_argument(
        "--base-url",
        default=os.getenv("PUBLIC_BASE_URL") or os.getenv("FRONTEND_URL") or "",
        help="публичный адрес сервиса (по умолчанию PUBLIC_BASE_URL или FRONTEND_URL)",
    )
    parser.add_argument(
        "--courses-dir",
        default=os.path.join(PROJECT_ROOT, "courses"),
        help="каталог с конфигами курсов",
    )
    args = parser.parse_args()

    secret_key = load_secret_key(os.path.join(PROJECT_ROOT, ".env"))
    if not secret_key:
        print(
            "SECRET_KEY не найден ни в окружении, ни в .env - без него ссылку вычислить нельзя",
            file=sys.stderr,
        )
        return 2

    base_url = args.base_url.rstrip("/")
    found = 0

    for course_id, course_info in iter_course_configs(args.courses_dir):
        if args.course and course_id != args.course:
            continue

        labs = course_info.get("labs")
        if not isinstance(labs, dict):
            continue

        timezone_str = course_info.get("timezone")
        for lab_key, lab_config in labs.items():
            lab_key = str(lab_key)
            if not is_secret_lab(lab_config) or not matches_lab(lab_key, lab_config, args.lab):
                continue

            try:
                parse_join_config(lab_config, timezone_str)
                token = lab_token(secret_key, course_id, lab_key, lab_config)
            except JoinConfigError as e:
                print(f"{course_id}  лаба {lab_key}: ошибка конфигурации: {e}", file=sys.stderr)
                continue

            found += 1
            short_name = lab_config.get("short-name", lab_key)
            print(f"Курс:    {course_id}")
            print(f"Лаба:    {lab_key} ({short_name})")
            print(f"Ссылка:  {base_url}/j/{token}")
            print(f"Окно:    {describe_window(lab_config, timezone_str)}")
            print()

    if not found:
        print("Секретных ссылок не найдено", file=sys.stderr)
        return 1

    if not base_url:
        print(
            "Внимание: адрес сервиса не задан - укажите --base-url или PUBLIC_BASE_URL",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
