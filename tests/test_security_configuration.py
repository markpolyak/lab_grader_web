"""Проверки безопасной конфигурации ключа и очистки OAuth access log."""

import logging
import os
from pathlib import Path
import subprocess
import sys

import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_main_import(tmp_path, secret_key):
    """Импортировать приложение в отдельном процессе с контролируемым окружением."""

    environment = os.environ.copy()
    environment.update(
        {
            "ADMIN_LOGIN": "test-admin",
            "ADMIN_PASSWORD": "test-password",
            "GITHUB_TOKEN": "test-server-token",
            "LOG_DIR": str(tmp_path / "logs"),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if secret_key is None:
        environment.pop("SECRET_KEY", None)
    else:
        environment["SECRET_KEY"] = secret_key

    # Подмена load_dotenv гарантирует, что локальный .env разработчика не
    # изменит сценарий отдельного процесса и не попадёт в диагностический вывод.
    command = (
        "import dotenv; "
        "dotenv.load_dotenv = lambda *args, **kwargs: False; "
        "import main"
    )
    return subprocess.run(
        [sys.executable, "-c", command],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_missing_secret_key_stops_application_without_disclosure(tmp_path):
    result = run_main_import(tmp_path, None)
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "RuntimeError: SECRET_KEY" in output


def test_known_default_secret_key_stops_application_without_disclosure(tmp_path):
    result = run_main_import(tmp_path, main.DEFAULT_SECRET_KEY)
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "RuntimeError: SECRET_KEY" in output
    assert main.DEFAULT_SECRET_KEY not in output


def test_custom_secret_key_allows_application_import_without_logging_key(tmp_path):
    custom_key = "isolated-test-key-that-must-not-appear"
    result = run_main_import(tmp_path, custom_key)
    output = result.stdout + result.stderr

    assert result.returncode == 0
    assert custom_key not in output


def test_uvicorn_access_filter_redacts_callback_secrets():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:50000",
            "GET",
            (
                "/join/callback?code=student-code-secret"
                "&state=signed-state-secret"
                "&authorization=Bearer-secret"
                "&error=access_denied"
            ),
            "1.1",
            303,
        ),
        exc_info=None,
    )

    assert main.OAuthCallbackAccessLogFilter().filter(record) is True
    rendered = record.getMessage()

    assert "student-code-secret" not in rendered
    assert "signed-state-secret" not in rendered
    assert "Bearer-secret" not in rendered
    assert rendered.count("[REDACTED]") == 3
    assert "GET /join/callback?" in rendered
    assert "error=access_denied" in rendered
    assert rendered.endswith('HTTP/1.1" 303')


def test_access_filter_redacts_callback_from_test_client_log():
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: GET %s "HTTP/1.1 303 See Other"',
        args=(
            "http://testserver/join/callback"
            "?code=test-client-code&state=test-client-state",
        ),
        exc_info=None,
    )

    assert main.OAuthCallbackAccessLogFilter().filter(record) is True
    rendered = record.getMessage()

    assert "test-client-code" not in rendered
    assert "test-client-state" not in rendered
    assert rendered.count("[REDACTED]") == 2
    assert "GET http://testserver/join/callback?" in rendered
