# sensei-mcp: запуск и проверка очереди

Команда: **@Weit145 @M1sterius @KorolevskiiKorol @colosss**.
[Регистрация](https://github.com/markpolyak/lab_grader_web/issues/64#issuecomment-6561465266).

- [ADR: хранение и история](001-queue-storage.md).
- [ADR: расписание и источники](002-schedule.md).
- [Текст PR](PR_DESCRIPTION.md).
- [Пошаговая отправка решения](SUBMISSION.md).
- Общая настройка Google/GitHub: [DEVELOPMENT](../../DEVELOPMENT.md).
- Подробные сценарии/API: [SCHEDULE](../../SCHEDULE.md).

## Область решения

На датированное лабораторное/практическое занятие создаётся очередь. Участники
хранятся в порядке записи, waiting/called считаются активными. Ожидающего можно
вызвать; одновременно допускается несколько called. Завершённая запись остаётся
в истории и не блокирует повторную запись студента в конец очереди.

Сервис сам открывает регистрацию за 15 минут до пары и закрывает в конце пары.
Ожидающие обслуживаются до срока обслуживания, по умолчанию до полуночи по
Москве. Ручная регистрация сохраняет режим. Источник расписания обновляется
фоном, пользовательские запросы читают сохранённый снимок.

Ограничения и незавершённые части перечислены в PR_DESCRIPTION и ADR; наличие
API не означает, что все действия уже доступны кнопками интерфейса.

## Установка для локального запуска

Из корня проекта, Python 3.11+ и Node.js 20+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
```

В `.env` заполнить GITHUB_TOKEN, ADMIN_LOGIN, ADMIN_PASSWORD, SECRET_KEY.
Для проверки очереди без внешних интеграций допустимы искусственные значения;
для настоящего допуска и экспорта нужны рабочие ключи и свои тестовые таблицы.

```dotenv
CREDENTIALS_FILE=credentials.json
QUEUE_WEEK_START=2026-11-09
SCHEDULE_CONFIG=schedule/offline.yaml
QUEUE_STATE_DIR=queue_state
```

`credentials.json` — service account, положенный в корень только локально.
Он исключён из Git и Docker build context. Не вставлять содержимое в PR или логи.
Если нужен реальный Google-сценарий, предоставить client_email из JSON доступ
к своим таблицам. Для истории использовать отдельную таблицу: настройка
`course.google.queue-history-spreadsheet` либо `QUEUE_HISTORY_SPREADSHEET_ID`.
Отсутствие настроенной Google-истории не отключает локальный expiry и API истории.

В рабочем примере есть os-2026 и сохранённое расписание на указанную неделю.
Данные из courses не дают доступ к настоящей таблице без service account;
для своих тестов заменить spreadsheet/организацию локально и не публиковать ключи.

```bash
# Терминал 1: backend; при применении ограничений сервера — один worker.
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1

# Терминал 2: frontend
cd frontend/courses-front
npm install --legacy-peer-deps
npm run dev
```

Frontend Vite: `http://localhost:5173`; backend: `http://localhost:8000`.
Маршруты admin через `/api/v1` проксируются Vite. Для реальной текущей недели
удалить QUEUE_WEEK_START, для обновления с сайта вернуть
`SCHEDULE_CONFIG=schedule/index.yaml`.

## Docker

В dev Compose из этого репозитория есть bind mount `.:/app`: для JSON в корне
в `.env` указать **`CREDENTIALS_FILE=/app/credentials.json`**. Путь из шаблона
`/app/google-credentials/credentials.json` подходит только при соответствующем
mount, как в production-примере.

```bash
docker compose up -d --build
docker compose logs --tail 80 backend
```

Frontend: `http://localhost:8080`; backend: `http://localhost:8000`.
Админка: `/admin`, реквизиты из `.env`.

Named volumes сохраняют logs, schedule и queue_state при пересоздании контейнера.
Том расписания при первом создании заполняется из образа; последующие правки
`./schedule` на хосте автоматически в него не копируются. Парсер работает с томом.
Старые bind-данные перед сменой конфигурации нужно перенести отдельно.
`docker compose down` сохраняет тома; `down -v` удаляет их и историю.

В production JSON монтируется отдельно и желательно read-only, образы его не
содержат. Требование одного worker сохраняется; WEB_CONCURRENCY не должен
включать несколько процессов.

## Тесты без настоящих ключей

```bash
ADMIN_LOGIN=test ADMIN_PASSWORD=test SECRET_KEY=test GITHUB_TOKEN=dummy \
  .venv/bin/python -m pytest tests/ queues/tests/ -q --tb=short

cd frontend/courses-front
npm run lint
npm test
npm run build
```

Последний полный прогон: 1137 backend-тестов, 8 subtests, 16 frontend-тестов;
frontend lint/build успешны. Новые сценарии сохранности и гонок перечислены в ADR.
При изменении кода повторить проверки, эти цифры не являются обещанием результата
для другой версии зависимостей.

## Проверка рабочего сценария

1. Открыть `/course/os-2026`, затем очередь и занятие. В offline-режиме проверить
   фиксированную неделю; состояние окна зависит от настоящего времени сервера.
2. Через admin открыть регистрацию или создать ручное занятие своей группы.
3. Настроить отдельную тестовую таблицу: GitHub/ФИО студента, группа, значение
   успешной проверки нужной лабы. Join читает эти данные; без доступа вернёт 503.
4. Записаться, проверить место, вызвать следующего. API поддерживает несколько
   вызванных одновременно и разные результаты; UI пока имеет ограничения.
5. Перезапустить backend: очередь должна сохраниться. После изменения времени
   источника старые ожидающие остаются доступны преподавателю, новая запись
   относится к актуальному занятию.
6. Завершить занятие с настроенной отдельной таблицей истории. Локальная история
   результатов остаётся доступной через API и после успешного экспорта/рестарта.

## Основные HTTP-маршруты

В таблице показаны пути backend; через frontend nginx используется `/api/v1`.
Admin требует штатную admin cookie. Изменение прав доступа в решение не включалось.

| Метод и путь | Назначение |
| --- | --- |
| `GET /courses/{course_id}/lessons` | Занятия актуальной недели |
| `GET /courses/{course_id}/lessons/{lesson_id}/queue` | Снимок очереди |
| `GET /courses/{course_id}/queue?github=...&group_id=...` | Активная запись и место |
| `POST /courses/{course_id}/queue/join` | Записаться после проверки допуска |
| `POST /courses/{course_id}/queue/leave` | Выйти из ожидающей записи |
| `GET /admin/courses/{course_id}/queues` | Очереди, включая сохранённые активные |
| `PUT /admin/courses/{course_id}/queues/{queue_id}/registration` | Ручная регистрация |
| `POST /admin/courses/{course_id}/queues/{queue_id}/call-next` | Вызвать следующего |
| `POST /admin/courses/{course_id}/queues/{queue_id}/entries/{entry_id}/finish` | Завершить; outcome: defended / not_defended / absent |
| `GET /admin/courses/{course_id}/queue-history` | История с результатами |
| `POST /admin/courses/{course_id}/queues/{queue_id}/complete` | Выгрузить и архивировать занятие |

Полные схемы JSON и остальные команды: `/docs` или `/openapi.json` backend.
Google-экспорт — список участников, полная история результатов — локальные YAML.
Оценки из queue-команд в существующие ячейки не записываются.
