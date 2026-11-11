# Deployment Guide

## Overview

This guide explains how to deploy and manage Lab Grader Web in production using Docker and GitHub Container Registry (GHCR).

## Architecture

- **CI/CD**: GitHub Actions builds Docker images on workflow dispatch
- **Registry**: Images are pushed to `ghcr.io/markpolyak/lab_grader_*`
- **Deployment**: Docker Compose on production server
- **Auto-updates**: Watchtower monitors `main` branch for updates

## Image Tagging Strategy

When you trigger the CI workflow from any branch, the following tags are created:

| Tag | Example | Description |
|-----|---------|-------------|
| Branch name | `main`, `feature-xyz` | Current branch name |
| `latest` | `latest` | Only for main branch |
| SHA with prefix | `main-abc1234` | Branch name + commit SHA |

**Examples:**

```bash
# Building from 'main' branch creates:
ghcr.io/markpolyak/lab_grader_frontend:main
ghcr.io/markpolyak/lab_grader_frontend:latest
ghcr.io/markpolyak/lab_grader_frontend:main-abc1234

# Building from 'feature-auth' branch creates:
ghcr.io/markpolyak/lab_grader_frontend:feature-auth
ghcr.io/markpolyak/lab_grader_frontend:feature-auth-xyz5678
```

## Server Setup

### Initial Setup

1. **Copy deployment script to server:**

```bash
# On your local machine
scp scripts/switch-branch.sh your-server:/opt/labgrader/
ssh your-server "chmod +x /opt/labgrader/switch-branch.sh"
```

2. **Create docker-compose.yaml on server:**

```yaml
# /opt/labgrader/docker-compose.yaml
services:
  frontend:
    image: ghcr.io/markpolyak/lab_grader_frontend:main
    networks:
      - caddy-proxy
    labels:
      caddy: labgrader.markpolyak.ru
      caddy.reverse_proxy: "{{upstreams 8080}}"

  backend:
    image: ghcr.io/markpolyak/lab_grader_backend:main
    volumes:
      - ./google-credentials:/app/google-credentials/
      - queue-data:/app/data   # база очереди на защиту, см. раздел ниже
    environment:
      CREDENTIALS_FILE: /app/google-credentials/credentials.json
      GITHUB_TOKEN: ${GITHUB_TOKEN}
      ADMIN_LOGIN: ${ADMIN_LOGIN}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD}
      SECRET_KEY: ${SECRET_KEY}
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL}
      FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS}
      QUEUE_DB_PATH: /app/data/queue.sqlite3
    labels:
      caddy: labgrader.markpolyak.ru
      caddy.handle_path: /api/v1*
      caddy.handle_path.0_reverse_proxy: "{{upstreams 8000}}"
    networks:
      - caddy-proxy

networks:
  caddy-proxy:
    external: true

volumes:
  queue-data:
```

Полный вариант с комментариями (логи, курсы, вход через GitHub) - в
`docker-compose.example.yaml`.

3. **Create .env file with secrets:**

```bash
# /opt/labgrader/.env
GITHUB_TOKEN=your_github_token
ADMIN_LOGIN=your_admin_login
ADMIN_PASSWORD=your_secure_password
SECRET_KEY=your_secret_key
# Публичный адрес сервиса: из него собирается секретная ссылка /j/{token},
# которую админка показывает преподавателю
PUBLIC_BASE_URL=https://labgrader.markpolyak.ru
# Адреса обратного прокси - см. раздел ниже
FORWARDED_ALLOW_IPS=172.18.0.0/16
```

### Ограничение частоты запросов за обратным прокси

Лимиты запросов считаются по адресу клиента (`request.client.host`). За
обратным прокси этот адрес - адрес прокси, поэтому **все студенты делят одну
корзину лимита**: в момент открытия контрольной работы группа из 30 человек
одновременно откроет ссылку, и часть из них получит `429`. Обратная сторона
та же: перебор секретного токена ограничивается глобально, а не по нарушителю.

Бэкенд запускается с `--proxy-headers`, а список адресов прокси задаётся
переменной `FORWARDED_ALLOW_IPS`:

- **значение по умолчанию пустое** - заголовку `X-Forwarded-For` не доверяет
  никто. Так и должно быть, если backend доступен напрямую: иначе лимит
  обходится подделкой заголовка;
- за прокси укажите адрес или подсеть, с которых прокси обращается к
  backend. Для Caddy в docker-сети это подсеть сети `caddy-proxy`:

```bash
docker network inspect caddy-proxy -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
# -> 172.18.0.0/16  -> FORWARDED_ALLOW_IPS=172.18.0.0/16
```

Проверить результат нужно **до** контрольной работы, а не во время неё:

```bash
# В логах доступа должен быть адрес студента, а не адрес прокси
docker compose logs backend | grep "GET /j/"

# Подделка заголовка с недоверенного адреса лимит обходить не должна:
# адрес в логе останется настоящим
curl -s -o /dev/null -H "X-Forwarded-For: 9.9.9.9" https://labgrader.markpolyak.ru/api/v1/j/aaaaaaaaaa
```

## Очередь на защиту: база SQLite

Очередь на защиту (секция `queue` в конфиге курса, `docs/COURSE_CONFIG.md`) -
единственная часть сервиса со своим состоянием: записи, вызовы и история
защит лежат в одном файле SQLite `QUEUE_DB_PATH` (в контейнере
`/app/data/queue.sqlite3`). Отдельного сервиса базы нет, используется модуль
`sqlite3` стандартной библиотеки. Почему так - ADR
`docs/hackathon/shaverma-driven-development/01-queue-storage.md`.

### Том и права

- Файл должен лежать на томе. Всё, что в контейнере вне томов, пропадает при
  каждом обновлении образа (Watchtower), а вместе с файлом - очередь посреди
  пары и вся история.
- В `docker-compose.example.yaml` это именованный том `queue-data:/app/data`.
  Образ создаёт `/app/data` с владельцем `appuser` (uid/gid 999), и новый
  именованный том при первом монтировании наследует этого владельца - больше
  ничего делать не нужно. `docker compose down -v` удаляет том вместе с
  историей.
- Если вместо тома нужен каталог на хосте (bind mount `./data:/app/data`, как
  у `./logs`), владельцем каталога должен быть uid 999:

  ```bash
  mkdir -p /opt/labgrader/data
  sudo chown 999:999 /opt/labgrader/data
  ```

  Иначе backend стартует (в логе будет ошибка о каталоге, недоступном на
  запись), но запросы к очереди отвечают `503 STORAGE_UNAVAILABLE`: SQLite не
  может открыть файл. То же касается локального `docker-compose.yml`: там весь
  репозиторий смонтирован в `/app`, и база ложится в `./data/` (каталог есть в
  `.gitignore`).
- База работает в режиме WAL: рядом с `queue.sqlite3` появляются
  `queue.sqlite3-wal` и `queue.sqlite3-shm`, поэтому запись нужна в каталог, а
  не только в файл. В WAL опрос очереди и история (чтения) не ждут записи и
  не задерживают её. Исключение - курс с `source: guap`: запрос, который раз в
  30 минут скачивает страницу расписания, сохраняет её копию короткой записью
  и ждёт блокировку не дольше секунды; не дождался - в логе предупреждение,
  копия обновится при следующей загрузке, а опрос отвечает как обычно. Если
  WAL включить не удалось, очередь работает, но в логе предупреждение «не
  удалось включить WAL: ...» или «журнал в режиме ..., а не WAL - опрос и
  запись очереди будут ждать друг друга». Том должен быть на локальном
  диске сервера: на сетевых файловых системах (NFS, SMB) блокировки SQLite
  ненадёжны.
- История хранится бессрочно. Объём небольшой - одна строка на запись
  студента в очередь. Страница истории показывает период не длиннее 400 дней
  и не больше 2000 записей за раз (самые новые), по 100 строк на странице.

### Резервная копия

Копировать `queue.sqlite3` через `cp` нельзя: в режиме WAL последние изменения
лежат в `queue.sqlite3-wal`, и копия одного основного файла их теряет. Утилиты
`sqlite3` в образе нет, поэтому копию делает Python через backup API - это
безопасно и на работающем сервисе:

```bash
docker compose exec backend python -c "import sqlite3; sqlite3.connect('/app/data/queue.sqlite3').backup(sqlite3.connect('/app/data/queue-backup.sqlite3'))"
docker compose cp backend:/app/data/queue-backup.sqlite3 ./queue-backup-$(date +%F).sqlite3
```

Получившийся файл самодостаточен, его можно копировать как обычно.
Восстановление - при остановленном backend:

```bash
docker compose stop backend
VOL=$(docker volume inspect -f '{{.Mountpoint}}' labgrader_queue-data)  # имя тома: docker volume ls
sudo cp queue-backup-2026-11-09.sqlite3 "$VOL/queue.sqlite3"
sudo rm -f "$VOL/queue.sqlite3-wal" "$VOL/queue.sqlite3-shm"   # журнал старого файла испортил бы новый
sudo chown 999:999 "$VOL/queue.sqlite3"
docker compose start backend
```

### Один процесс backend

Backend рассчитан на один процесс uvicorn: в `backend.Dockerfile` нет
`--workers`. **Но без `--workers` uvicorn берёт число процессов из
`WEB_CONCURRENCY`**, и одна строка `WEB_CONCURRENCY=4` в окружении незаметно
запускает четыре. Не задавайте эту переменную.

- Очередь несколько процессов переживает: каждое изменение - транзакция
  SQLite `BEGIN IMMEDIATE`, правило «одна активная запись на студента»
  держит уникальный индекс в самой базе, а копию расписания ГУАП процессы
  берут друг у друга из той же базы.
- Остальной проект - нет. Командные лабораторные, массовая проверка и рассылка
  шаблона держат блокировки и состояние работ в памяти процесса, лимиты
  запросов тоже считаются отдельно в каждом процессе. Кэш таблицы курса у
  очереди тоже свой у каждого процесса: свежая отметка `v` из проверки в
  другом процессе станет видна очереди с задержкой до 60 секунд.

### Адреса сервиса и вход студента

- Студент входит в очередь через тот же GitHub OAuth App и тот же колбэк
  `/join/callback`, что и при получении репозитория (`.env.example`):
  отдельный Authorization callback URL регистрировать не нужно. Без
  `GITHUB_OAUTH_CLIENT_ID` и `GITHUB_OAUTH_CLIENT_SECRET` студент
  возвращается на страницу очереди с сообщением «Вход через GitHub не
  настроен на сервере», в логе - ошибка о ненастроенном OAuth App.
- `FRONTEND_URL` - куда браузер возвращается после входа
  (`{FRONTEND_URL}/course/{course_id}/queue`) и с какого адреса принимаются
  запросы очереди (ниже). Значение по умолчанию `http://localhost:8080`
  годится только для локальной разработки в `docker compose`; при
  `npm run dev` нужен `http://localhost:5173` (`docs/DEVELOPMENT.md`).
- Все запросы к очереди - действия (POST) и чтение (GET состояния, списка
  работ, страницы преподавателя и истории) - принимаются только со страниц
  самого сервиса: если в запросе есть заголовок `Origin`, он должен совпасть
  с адресом из `FRONTEND_URL`, `PUBLIC_BASE_URL`, с адресом самого запроса к
  backend или иметь тот же `host:port`, что заголовок `Host` запроса (схема
  при этом не важна: за TLS-прокси backend видит `http`). Иначе ответ
  `403 BAD_ORIGIN`, в логе - «Запрос к очереди ... с чужого Origin ...
  отклонён; задайте FRONTEND_URL=..., если это ваш фронтенд». Caddy передаёт
  исходный `Host` сам, nginx - только с
  `proxy_set_header Host $host`; если прокси подменяет `Host`, на сервере
  `FRONTEND_URL` и `PUBLIC_BASE_URL` должны быть публичным адресом сервиса
  (`https://labgrader.markpolyak.ru`). Признак ошибки: страница очереди не
  загружается или действия отвечают «Запрос отклонён».
- Лимиты запросов студента в очереди считаются по логину из cookie очереди, а
  не по адресу, поэтому общий NAT аудитории им не мешает. До входа опрос
  очереди (`GET /courses/{course_id}/queue`, раз в 60 с на страницу)
  считается по адресу, лимит 120 в минуту: этого хватает группе за одним NAT.
  Вход (`/courses/{course_id}/queue/login`, `/join/callback`) тоже считается
  по адресу - для него нужен `FORWARDED_ALLOW_IPS` (раздел выше).
- Успешные GET-запросы опроса очереди (`/courses/*/queue`,
  `/admin/courses/*/queue`) в журнал доступа не пишутся: страница очереди
  опрашивает сервер каждые несколько секунд, и за пару набегали бы десятки
  тысяч строк.

## Switching Between Branches

### Method 1: Using the Script (Recommended)

```bash
cd /opt/labgrader

# Switch to a feature branch for testing
./switch-branch.sh feature-new-ui

# Check logs
docker compose logs -f

# Switch back to main
./switch-branch.sh main
```

The script will:
- ✅ Create automatic backup
- ✅ Update docker-compose.yaml
- ✅ Pull new images
- ✅ Restart services
- ✅ Show clear status messages

### Method 2: Manual Switching

```bash
cd /opt/labgrader

# Edit docker-compose.yaml and change :main to :your-branch
nano docker-compose.yaml

# Pull and restart
docker compose pull
docker compose up -d --force-recreate
```

### Method 3: Using Environment Variable

Update your docker-compose.yaml to use variables:

```yaml
services:
  frontend:
    image: ghcr.io/markpolyak/lab_grader_frontend:${BRANCH:-main}
  backend:
    image: ghcr.io/markpolyak/lab_grader_backend:${BRANCH:-main}
```

Then switch with:

```bash
BRANCH=feature-xyz docker compose up -d --pull=always --force-recreate
```

## Watchtower Configuration

Watchtower automatically updates containers when new images are pushed with the same tag.

**Current behavior:**
- Watchtower monitors `main` tag
- When you push new code to `main` and CI builds it, Watchtower auto-updates production

**To prevent auto-updates during testing:**

When testing a feature branch, the tag is different (e.g., `feature-xyz`), so Watchtower won't auto-update. This is intentional and safe.

## Deployment Workflows

### Production Update (Main Branch)

```bash
# On your machine
git push origin main

# Trigger GitHub Actions manually or via workflow_dispatch

# Wait for CI to complete (~5 min)

# Watchtower will auto-update production (if configured)
# Or manually:
ssh your-server
cd /opt/labgrader
docker compose pull
docker compose up -d
```

### Testing a Feature Branch

```bash
# On your machine
git push origin feature-new-auth

# Trigger GitHub Actions for this branch

# On server
ssh your-server
cd /opt/labgrader
./switch-branch.sh feature-new-auth

# Test the changes
# Check logs: docker compose logs -f

# When done testing, switch back
./switch-branch.sh main
```

### Rolling Back to Previous Version

```bash
# Option 1: Use the automatic backup
cd /opt/labgrader
ls -la docker-compose.yaml.backup.*
cp docker-compose.yaml.backup.20250118_143022 docker-compose.yaml
docker compose up -d --force-recreate

# Option 2: Use commit SHA tag
./switch-branch.sh main-abc1234
```

## Monitoring

### View Logs

```bash
# All services
docker compose logs -f

# Just backend
docker compose logs -f backend

# Last 100 lines
docker compose logs --tail=100 backend
```

### Check Running Containers

```bash
docker compose ps
```

### Check Image Versions

```bash
docker compose images
```

## Troubleshooting

### Images Not Pulling

```bash
# Check if you're logged in to GHCR
docker login ghcr.io

# Manually pull to see detailed error
docker pull ghcr.io/markpolyak/lab_grader_backend:main
```

### Services Not Starting

```bash
# Check logs for errors
docker compose logs backend
docker compose logs frontend

# Verify environment variables
docker compose config
```

### Wrong Branch Deployed

```bash
# Check current image tags
grep "image:" docker-compose.yaml

# Restore from backup
ls -la docker-compose.yaml.backup.*
cp docker-compose.yaml.backup.LATEST docker-compose.yaml
docker compose up -d --force-recreate
```

## Best Practices

1. **Always test feature branches** before merging to main
2. **Use the switch script** - it creates automatic backups
3. **Monitor logs** after switching branches
4. **Keep main stable** - only merge tested code
5. **Document changes** in commit messages for easier rollbacks

## Security Notes

- Never commit `docker-compose.yaml` with secrets to git
- Use `.env` file for sensitive data (also not in git)
- Restrict SSH access to deployment server
- Regularly rotate secrets (ADMIN_PASSWORD, SECRET_KEY, etc.)
- Keep Docker and Watchtower updated

### `SECRET_KEY` и секретные ссылки

`SECRET_KEY` подписывает не только сессии, но и вычисляет токены секретных
ссылок на получение репозитория (`/j/{token}`, см. `docs/COURSE_CONFIG.md`,
секция `join`). Токены нигде не хранятся - они каждый раз пересчитываются из
`SECRET_KEY` и конфига курса, поэтому:

- **смена `SECRET_KEY` немедленно отзывает все секретные ссылки** (и, как и
  раньше, сбрасывает сессии администратора). Разосланные студентам ссылки
  перестанут работать, новые нужно взять в админке и раздать заново;
- менять ключ во время идущей контрольной работы нельзя;
- разные окружения с разными `SECRET_KEY` дают разные ссылки для одного и
  того же конфига - ссылку с тестового стенда нельзя раздавать студентам.

## Reference Commands

```bash
# Quick reference for common tasks

# Switch to feature branch
./switch-branch.sh feature-name

# Back to production
./switch-branch.sh main

# View logs
docker compose logs -f

# Restart without pulling
docker compose restart

# Stop all services
docker compose down

# Start services
docker compose up -d

# Check status
docker compose ps
```

## Course Management

### Adding a New Course

1. **Create course YAML file:**

```bash
# Create new course configuration
nano courses/new-course-2025.yaml
```

2. **Add course logo (optional):**

```bash
# Add logo image to logos directory
cp my-logo.png courses/logos/new-course-2025.png
```

3. **Update course index:**

```yaml
# Edit courses/index.yaml
courses:
  - id: "new-course-2025-spring"
    file: "new-course-2025.yaml"
    logo: "/courses/logos/new-course-2025.png"  # Optional, HTTP path
    status: "active"
    priority: 80
```

4. **Commit changes:**

```bash
git add courses/
git commit -m "Add new course: New Course 2025"
git push
```

### Managing Course Status

Edit `courses/index.yaml` to change course visibility:

```yaml
# Active course (visible in main list)
- id: "os-2025-spring"
  status: "active"

# Archived course (for students with academic debt)
- id: "os-2024-fall"
  status: "archived"

# Hidden course (completely invisible but preserved in git)
- id: "os-2023-spring"
  status: "hidden"
```

### Updating Course Logos

**Option 1: Update existing logo**

```bash
# Replace logo file
cp new-logo.png courses/logos/os-2025-spring.png
git add courses/logos/os-2025-spring.png
git commit -m "Update OS course logo"
git push
```

**Option 2: Change logo path**

```yaml
# Edit courses/index.yaml
- id: "os-2025-spring"
  logo: "/courses/logos/new-os-logo.png"  # New HTTP path
```

**Option 3: Remove logo (use default)**

```yaml
# Edit courses/index.yaml - remove logo field entirely
- id: "os-2025-spring"
  file: "operating-systems-2025.yaml"
  status: "active"
  # logo field removed - will use default
```

### Course Priority and Ordering

Courses are sorted by priority (higher = shown first):

```yaml
courses:
  - id: "os-2025-spring"
    priority: 100  # Shown first

  - id: "ml-2025-spring"
    priority: 90   # Shown second

  - id: "old-course"
    priority: 10   # Shown last
```

### Logo Guidelines

- **Location**: `courses/logos/`
- **Naming**: Match course ID (e.g., `os-2025-spring.png`)
- **Formats**: PNG, SVG, JPG, WebP
- **Size**: 200x200 to 1000x1000 pixels
- **File size**: Keep under 200KB
- **Default**: If no logo specified, `/assets/default.png` is used

See [docs/COURSE_LOGOS.md](COURSE_LOGOS.md) for detailed logo guidelines.
