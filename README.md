# Lab Grader Web

Веб-сервис для приёма лабораторных работ: студент регистрирует GitHub-аккаунт,
сервис проверяет его репозиторий и результаты CI и записывает оценку в
Google-таблицу курса. Для преподавателя — админка с конфигурациями курсов,
массовой проверкой, созданием репозиториев студентов и командными работами.

Backend — FastAPI (`main.py`, логика проверки — в `grading/`), frontend —
React + Vite (`frontend/courses-front/`), курсы — YAML-файлы в `courses/`.

## Документация

| Документ | О чём |
|---|---|
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Как поднять проект локально: таблица, GitHub, ключи, запуск, тесты |
| [docs/PROJECT_DESCRIPTION.md](docs/PROJECT_DESCRIPTION.md) | Что умеет сервис, архитектура, API |
| [docs/COURSE_CONFIG.md](docs/COURSE_CONFIG.md) | Все опции конфигурации курса |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Развёртывание на сервере |
| [CLAUDE.md](CLAUDE.md) | Соглашения проекта (для людей и ИИ-агентов) |

## Быстрый старт

```bash
cp .env.example .env    # заполнить, см. docs/DEVELOPMENT.md
docker compose up --build
```

## Лицензия

MIT, см. [LICENSE](LICENSE).
