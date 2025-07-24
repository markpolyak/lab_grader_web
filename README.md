# Телеграм-бот для проверки лабораторных работ

## Запуск

1. Создайте файл .env в корне проекта и заполните по примеру

       ADMIN_LOGIN=ваш_логин_админа
       ADMIN_PASSWORD=ваш_пароль_админа
       SECRET_KEY=сгенерированный_секретный_ключ
       GITHUB_TOKEN=ваш_github_токен
       TELEGRAM_BOT_TOKEN=токен_вашего_бота

2. Создайте файл credentials.json в корне проекта
3. В терминале выполнить команду:

       docker compose up --build

## Команды бота
- /start - Запуск бота, регистрация студента, главное меню студента (для авторизованных)

## Как получить значения переменных окружения и файла credentials.json
### Файл .env:
- ADMIN_LOGIN/ADMIN_PASSWORD

  Произвольные логин и пароль администратора приложения

- SECRET_KEY

  Переменная хранящая секретный ключ для безопасности приложения.

- GITHUB_TOKEN

    1. Перейдите: GitHub Settings → Developer Settings → Personal Access Tokens → Generate new token

    2. Выберите scopes: repo, admin:org, user

    3. Скопируйте токен (отображается только один раз!)

- TELEGRAM_BOT_TOKEN

    1. Создайте бота через @BotFather

    2. Используйте команду /newbot

    3. Скопируйте токен из сообщения BotFather
 
### Файл credentials.json:

Необходим для работы с Google Sheets API.

Инструкция получения:

1. Создайте проект в Google Cloud Console

2. Включите API:

    - Google Sheets API

    - Google Drive API

3. Создайте сервисный аккаунт:

    - APIs & Services → Credentials → Create Credentials → Service Account

    - Заполните имя (например app-service-account)

    - Роль: Project → Editor

4. Сгенерируйте ключ:

    В настройках сервисного аккаунта → Keys → Add Key → Create new key → JSON

5. Скачанный файл поместите в корень проекта как credentials.json

## Доступ к приложению
- Веб-интерфейс: http://localhost:5173

- Админ-панель: http://localhost:5173/admin

- Telegram-бот: @lab_auditor_bot

## Важные примечания
1. Не коммитьте конфиденциальные файлы:


        .env
        credentials.json
2. Для доступа к Google Sheets:

    - Откройте нужную таблицу

    - Нажмите "Share"

    - Добавьте email из credentials.json (поле client_email)

3. При изменении портов обновите docker-compose.yml:

        ports:
          - "Новый_порт:8000"
