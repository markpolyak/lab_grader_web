[Главная](../../README.md) · [Навигация по документации](../../docs/README.md)

# Интерфейс Lab Grader

[Главная](../../README.md) · [Документация](../../docs/README.md) · [Рабочий запуск](../../docs/hackathon/suaios/queue/RUNBOOK.md) · [Песочница](../../docs/hackathon/suaios/queue/SANDBOX.md)

React 19, Vite, существующие формы регистрации и административная панель. Компоненты очереди находятся в `src/components/DefenseQueue/`, переводы — в `src/locales/{ru,en,zh}/queue.json`.

## Запуск

Для совместного запуска frontend/backend из корня репозитория:

```bash
./start-sandbox
# либо стенд на публичных данных
./start-main
```

Отдельный Vite для собственного backend на 8000, из этого каталога:

```bash
npm ci --legacy-peer-deps
VITE_API_BASE_URL=http://localhost:8000 VITE_PROXY_TARGET=http://127.0.0.1:8000 npm run dev -- --host localhost --port 5173 --strictPort
```

Адрес: <http://localhost:5173>. Вход преподавателя: `/admin`; общая панель очередей: `/admin/queue`. Студент начинает с `/course/<course_id>`, выбирает группу, лабораторную и заполняет форму.

`VITE_API_BASE_URL` задаёт адрес запросов API. `VITE_PROXY_TARGET` направляет относительные запросы авторизации на тот же backend. При занятом порте остановить прежний Vite или выбрать другой порт и согласовать `FRONTEND_URL` backend.

## Проверки и сборка

```bash
npm test
npm run build
```

Результат сборки находится в `dist/`. Production-контейнер использует [frontend.Dockerfile](../../frontend.Dockerfile). В README корня опубликован [протокол проверок](../../docs/hackathon/suaios/queue/VERIFICATION.md).
