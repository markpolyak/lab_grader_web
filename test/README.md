# Автотесты для FastAPI-приложения

В проекте реализованы модульные тесты с использованием `pytest` для проверки основных эндпоинтов FastAPI-приложения.

## Установка зависимостей

1. Клонируйте репозиторий (если ещё не):
   ```bash
   git clone <URL-репозитория>
   cd lab_grader_web
   ```

2. Активируйте виртуальное окружение (если не создано — создайте):
   ```bash
   python -m venv .venv
   source .venv/bin/activate       # для Linux/macOS
   .venv\Scripts\activate        # для Windows
   ```

3. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```

## .env файл

Перед запуском тестов убедитесь, что в корне проекта существует файл `.env` со следующими переменными:

```
ADMIN_LOGIN=admin
ADMIN_PASSWORD=password
SECRET_KEY=supersecret
GITHUB_TOKEN=dummy
```

## Запуск автотестов

Тесты расположены в директории `test/`.

Для запуска всех тестов:
```bash
pytest -v
```

Для проверки покрытия кода:
```bash
pytest --cov=main --cov-report=term-missing
```

## Структура тестов

- `test/test_auth.py` — тесты авторизации, проверки сессии и выхода
- `test/test_courses.py` — тесты эндпоинтов загрузки, редактирования, получения курсов и регистрации студентов
