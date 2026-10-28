# Локальная разработка

Как поднять сервис у себя: со своей Google-таблицей, своей GitHub-организацией
и своими ключами. Боевые таблицы и организации курсов для разработки не нужны
и недоступны.

## 1. Что понадобится

- Python 3.11+ и Node.js 20+ (или Docker с Docker Compose);
- аккаунт Google (для таблицы и service account);
- аккаунт GitHub.

## 2. Google Sheets

### 2.1 Service account

1. В [Google Cloud Console](https://console.cloud.google.com/) создайте проект.
2. Включите для него **Google Sheets API** и **Google Drive API**
   (APIs & Services → Library).
3. APIs & Services → Credentials → Create credentials → **Service account**.
   Роли не нужны.
4. У созданного service account: Keys → Add key → JSON. Сохраните файл
   как `credentials.json` в корень репозитория. Он уже в `.gitignore` —
   не коммитьте его.
5. Запомните e-mail service account (`...@...iam.gserviceaccount.com`).

### 2.2 Таблица

Создайте Google-таблицу и откройте к ней доступ на редактирование для e-mail
service account. ID таблицы — часть URL между `/d/` и `/edit`.

Листы:

- **информационный лист** (например, `График`) — может быть пустым, его имя
  указывается в конфигурации курса и исключается из списка групп;
- **по листу на группу**, имя листа = название группы (например, `4431`).

Структура листа группы (так её читает код: `main.py`, `grading/sheets_client.py`):

|   | A | B | C | D | E |
|---|---|---|---|---|---|
| **1** | | | `31.12.2026` | | `GitHub` |
| **2** | `№` | `ФИО` | `ЛР1` | `ЛР2` | |
| **3** | `1` | `Иванов Иван Иванович` | | | `ivanov-gh` |
| **4** | `2` | `Петров Пётр Петрович` | `v` | | |

- **Строка 1**: заголовок `GitHub` над столбцом логинов (ищется именно в
  первой строке) и, при желании, дедлайны над столбцами лабораторных.
- **Строка 2**: короткие имена лабораторных (`short-name` из конфигурации
  курса), начиная со столбца C.
- **Со строки 3** — студенты: номер варианта, ФИО (регистрация сравнивает
  его точно, вместе с отчеством), оценки, логин GitHub.

Оценку можно поставить руками: например, `v` в ячейке лабораторной выглядит
для сервиса так же, как результат успешной проверки.

## 3. GitHub

### 3.1 Организация и токен

1. Создайте организацию (бесплатный план подходит):
   <https://github.com/organizations/plan>. Репозитории студентов ищутся в ней
   по имени `{github-prefix}-{логин}`.
2. Создайте personal access token (classic) со scopes `repo`, `read:org`,
   `workflow`: <https://github.com/settings/tokens>.

Для фич, которые не обращаются к GitHub, токен всё равно нужен: без
`GITHUB_TOKEN` приложение не стартует.

### 3.2 OAuth App (вход студента через GitHub)

Нужен для всего, что опирается на подтверждённый логин студента (`/join/...`,
командные лабораторные).

1. <https://github.com/settings/developers> → OAuth Apps → New OAuth App.
2. Homepage URL: `http://localhost:8080` (или `http://localhost:5173` при
   запуске frontend через `npm run dev`).
3. Authorization callback URL: `http://localhost:8000/join/callback`.
4. Client ID и Client secret — в `.env` (ниже).

## 4. Конфигурация курса

Создайте файл курса, например `courses/dev-course.yaml`
(все опции — в `docs/COURSE_CONFIG.md`):

```yaml
course:
    name: Тестовый курс
    semester: Осень 2026
    university: ГУАП
    timezone: UTC+3
    github:
        organization: <ваша-организация>
    google:
        spreadsheet: <ID-таблицы>
        info-sheet: График
        task-id-column: 0
        student-name-column: 1
        lab-column-offset: 1
labs:
    "1":
        github-prefix: lab1
        short-name: ЛР1
    "2":
        github-prefix: lab2
        short-name: ЛР2
```

и добавьте его в `courses/index.yaml`:

```yaml
  - id: "dev-course"
    file: "dev-course.yaml"
    status: "active"
    priority: 1000
```

Не включайте эти изменения в pull request.

## 5. Переменные окружения

Скопируйте `.env.example` в `.env` и заполните:

```bash
GITHUB_TOKEN=ghp_...
ADMIN_LOGIN=admin
ADMIN_PASSWORD=admin
SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
CREDENTIALS_FILE=credentials.json

GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
FRONTEND_URL=http://localhost:8080   # или http://localhost:5173 для npm run dev
```

## 6. Запуск

### Docker

```bash
docker compose up --build
# Frontend: http://localhost:8080, backend: http://localhost:8000
```

Каталог репозитория смонтирован в контейнер backend, поэтому `credentials.json`,
`.env` и `courses/` берутся из рабочей копии.

### Без Docker

```bash
# backend
pip install -r requirements-dev.txt
uvicorn main:app --reload --port 8000

# frontend (в другом терминале)
cd frontend/courses-front
npm install --legacy-peer-deps
npm run dev            # http://localhost:5173
```

Админка: `/admin` во frontend, логин и пароль — `ADMIN_LOGIN` /
`ADMIN_PASSWORD`.

## 7. Тесты

Тесты не ходят в Google и GitHub, но `main.py` при импорте требует
переменные окружения — достаточно фиктивных значений:

```bash
ADMIN_LOGIN=test ADMIN_PASSWORD=test SECRET_KEY=test GITHUB_TOKEN=dummy \
    pytest tests/ -v

cd frontend/courses-front && npm test
```

Те же тесты запускает CI (`.github/workflows/tests.yml`) на каждый push и
pull request.
