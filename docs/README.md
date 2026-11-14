# Документация

[← Главная](../README.md) · [Запуск](hackathon/suaios/queue/RUNBOOK.md) · [Песочница](hackathon/suaios/queue/SANDBOX.md) · [Проверки](hackathon/suaios/queue/VERIFICATION.md)

## Для просмотра и защиты

| Открыть | Что внутри |
|---|---|
| [Демонстрация и GIF](../README.md#демонстрация) | 50 секунд работы приложения и MP4 со звуком |
| [Скриншоты](assets/screenshots/README.md) | Три экрана в полном размере |
| [Песочница](hackathon/suaios/queue/SANDBOX.md) | Полный путь студента и преподавателя без ключей |
| [Рабочий запуск](hackathon/suaios/queue/RUNBOOK.md) | Собственный курс, окружение, хранение и восстановление |
| [Стенд на публичных данных](hackathon/suaios/queue/LIVE_CHECK.md) | Чтение настоящих таблиц и расписания без изменения оценок |
| [Кнопки и круги](hackathon/suaios/queue/ROUNDS.md) | Вызов, результат, неявка, повтор и отмена действия |
| [Проверки и готовность](hackathon/suaios/queue/VERIFICATION.md) | Протокол прогона и отличия командного решения |
| [Исследование](../research/queue-study/README.md) | Сравнение моделей и графики |
| [ADR](adr/README.md) | Приоритет −1, SQLite и расписание |
| [ADR по шаблону задания](hackathon/suaios/README.md) | Хранение и адаптеры расписания |

## Устройство очереди

| Открыть | Что внутри |
|---|---|
| [Модуль очереди](hackathon/suaios/queue/README.md) | Сущности, состояния и хранение |
| [Карта реализации](hackathon/suaios/queue/IMPLEMENTATION_PLAN.md) | Задачи, модули и проверенные сценарии |
| [API](hackathon/suaios/queue/API.md) | Запросы, ответы и ограничения |
| [Словарь](hackathon/suaios/queue/GLOSSARY.md) | Допуск, предварительное место, занятие и попытка |
| [Интерфейс](hackathon/suaios/queue/DESIGN.md) | Компоненты и визуальные состояния |
| [Схема SQLite](hackathon/suaios/queue/schema.sql) | Снимок после миграции 008; приложение применяет миграции само |
| [Исторический аудит](hackathon/suaios/queue/AUDIT-2026-11-07.md) | Результаты промежуточных этапов, не текущий протокол приёмки |

## Существующий Lab Grader

| Открыть | Что внутри |
|---|---|
| [Разработка](DEVELOPMENT.md) | Своя таблица, service account, GitHub и локальный запуск |
| [Конфигурация курса](COURSE_CONFIG.md) | Лабораторные, CI и ключи конфигурации |
| [Описание сервиса](PROJECT_DESCRIPTION.md) | Возможности базового проекта |
| [Развёртывание](DEPLOYMENT.md) | Docker, сервер, домен и HTTPS |
| [Логирование](LOGGING.md) | Логи и настройка уровней |
| [Логотипы](LOGO_SYSTEM.md) / [Логотипы курсов](COURSE_LOGOS.md) | Графика существующего интерфейса |
| [Скрипты](../scripts/README.md) | Запуск и служебные команды |
| [Frontend](../frontend/courses-front/README.md) | Vite, API и сборка |

<details>
<summary><strong>Материалы исходного проекта по отдельным функциям</strong></summary>

Эти документы описывают самостоятельные подсистемы исходного Lab Grader. Они не заменяют инструкцию запуска очереди.

- [Регистрация репозиториев](REPO_GENERATION_PLAN.md).
- [Командные работы](TEAM_ASSIGNMENTS_PLAN.md).
- [Массовая проверка](BULK_GRADING_PLAN.md).
- [Секретные ссылки](SECRET_JOIN_LINKS_PLAN.md).
- [Рефакторинг проверки](GRADING_REFACTORING_PLAN.md).
- [Проверка заимствований](PLAGIARISM_DETECTION_PLAN.md).
- [Исследование распознавания AI-кода](AI_CODE_DETECTION_RESEARCH.md).
- [Шаблон ADR](hackathon/ADR_TEMPLATE.md).
- [Графика документации](assets/readme/README.md).

</details>
