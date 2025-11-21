# Проверка деплоя и отладка

## Текущая ветка и теги образов

**Ветка:** `claude/review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5`
**Docker тег:** `claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5`

## 1. Проверка образов на сервере

Выполните на сервере:

```bash
# Проверить текущие образы
docker images | grep lab_grader

# Проверить какие образы используются контейнерами
docker compose images

# Проверить когда образ был создан
docker inspect ghcr.io/markpolyak/lab_grader_frontend:claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5 | grep Created
docker inspect ghcr.io/markpolyak/lab_grader_backend:claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5 | grep Created
```

## 2. Проверка в GitHub Container Registry

Проверьте на GitHub:
1. Откройте https://github.com/markpolyak/lab_grader_web/pkgs/container/lab_grader_frontend
2. Откройте https://github.com/markpolyak/lab_grader_web/pkgs/container/lab_grader_backend
3. Проверьте есть ли тег `claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5`
4. Проверьте дату создания (должно быть недавно)

## 3. Запуск CI вручную

CI настроен на `workflow_dispatch` (ручной запуск):

1. Откройте https://github.com/markpolyak/lab_grader_web/actions
2. Выберите workflow "CI" (или как он называется)
3. Нажмите "Run workflow"
4. Выберите ветку `claude/review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5`
5. Нажмите "Run workflow"
6. Дождитесь завершения (обычно 3-5 минут)

## 4. Принудительное обновление на сервере

После успешного CI:

```bash
# Очистить локальные образы (опционально, если pull не помогает)
docker rmi ghcr.io/markpolyak/lab_grader_frontend:claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5
docker rmi ghcr.io/markpolyak/lab_grader_backend:claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5

# Скачать свежие образы
docker pull ghcr.io/markpolyak/lab_grader_frontend:claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5
docker pull ghcr.io/markpolyak/lab_grader_backend:claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5

# Пересоздать контейнеры
BRANCH=claude-review-project-docs-01ECGaVURK3mRS1Mtjj1jsP5 docker compose up -d --force-recreate
```

## 5. Проверка исправления gradeLab

После успешного деплоя проверьте в браузере:

1. Откройте Developer Tools (F12)
2. Вкладка Network
3. Попробуйте запустить проверку
4. Найдите запрос к `/grade`
5. Проверьте:
   - URL должен быть: `https://labgrader.markpolyak.ru/api/v1/courses/.../grade`
   - Метод: `POST`
   - Статус: `200` (не 405)

## 6. Проверка версии frontend

В консоли браузера (F12 → Console):

```javascript
// Проверить переменную API_BASE_URL
console.log('API_BASE_URL должен быть: https://labgrader.markpolyak.ru/api/v1')
```

Если `API_BASE_URL` неправильный, значит frontend не обновился.

## 7. Альтернатива: использовать main ветку

Если не получается с веткой, можно:

1. Смержить ветку в main
2. CI автоматически создаст тег `latest`
3. На сервере использовать `main` или `latest`

```bash
# На сервере
BRANCH=main docker compose up -d --pull=always --force-recreate
```

## Диагностика проблемы 405

Ошибка 405 означает, что:
- Запрос идет не на тот URL
- Или reverse proxy не проксирует запрос

Проверьте в логах Caddy:
```bash
docker logs caddy | grep grade
```

Если видите `/api/courses/.../grade` вместо `/api/v1/courses/.../grade` - frontend не обновился.
