[Главная](../README.md) · [Навигация по документации](../docs/README.md)

# Скрипты

[Главная](../README.md) · [Документация](../docs/README.md) · [Запуск](../docs/hackathon/suaios/queue/RUNBOOK.md)

Команды ниже выполняются из корня репозитория.

| Команда | Назначение |
|---|---|
| `./start-sandbox` | Frontend и backend песочницы, симуляция Google/GitHub |
| `./start-sandbox --check` | Проверка API песочницы без запуска серверов |
| `./start-main` | Frontend и backend стенда на публичной таблице и расписании |
| `./start-main --check` | Проверка внешних источников, нужен интернет |
| `./start-main --data-dir data/queue-live-second` | Независимый локальный экземпляр стенда |
| `.venv/bin/python scripts/check_docs.py` | Проверка локальных ссылок Markdown и HTML в документации |
| `.venv/bin/python scripts/render_readme_art.py` | Пересборка обложек; требуется Pillow |

Запуск с другими портами:

```bash
./start-sandbox --port 8011 --frontend-port 5181
./start-main --port 8001 --frontend-port 5174
```

`--backend-only` отключает запуск Vite. `--reset` используется только для осознанного сброса локального профиля после остановки прежнего backend. Подробности сохранения: [песочница](../docs/hackathon/suaios/queue/SANDBOX.md) и [стенд](../docs/hackathon/suaios/queue/LIVE_CHECK.md).

## Снимки для README

`capture_queue_screenshots.cjs` снимает три экрана настоящей песочницы через Playwright: [установка, запуск и результаты](../docs/assets/screenshots/README.md).

## Дополнительные инструменты

- `run_queue_demo.py` — прежний упрощённый показ очереди с готовыми допусками; полный путь регистрации доступен через `start-sandbox`.
- `run_queue_live.py` — запуск только backend публичного стенда.
- `join-link.py` — секретные ссылки существующего сервиса, [описание](../docs/SECRET_JOIN_LINKS_PLAN.md).
- `switch-branch.sh` — переключение тегов контейнеров на настроенном сервере. Изменяет Compose и перезапускает сервисы; это не команда локального просмотра. Перед использованием настроить пути и образы по [инструкции развёртывания](../docs/DEPLOYMENT.md).
