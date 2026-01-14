import sqlite3
import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.ext import MessageHandler, filters, ConversationHandler

from config import TOKEN

DB_PATH = "users.db"

WAITING_FOR_FULL_NAME = 1
WAITING_FOR_CONFIRMATION = 2
WAITING_FOR_GITHUB = 3

CHOOSING_COURSE = 10
CHOOSING_LAB = 11

# Инициализация таблицы пользователей
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        language TEXT DEFAULT 'ru',
        group_id TEXT,
        surname TEXT,
        name TEXT,
        patronymic TEXT,
        github TEXT,
        authorized INTEGER DEFAULT 0,
        last_message TEXT,
        last_buttons TEXT
    )
    """)
    conn.commit()
    conn.close()



# Добавление пользователя или обновление записи
def upsert_user(user_id: int, language: str = "ru"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, language) VALUES (?, ?)", (user_id, language))
    conn.commit()
    conn.close()



# Обновление языка пользователя
def set_language(user_id: int, language: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET language = ? WHERE user_id = ?", (language, user_id))
    conn.commit()
    conn.close()



# Получение языка пользователя
def get_language(user_id: int) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "ru"



# Текст на русском
def get_welcome_text_ru() -> str:
    return (
        "👋 Добро пожаловать!\n\n"
        "Этот бот создан для обучающихся в университете ГУАП, которые проходят курсы Поляка М. Д.\n\n"
        "Что можно сделать с его помощью?\n"
        "✅ Записать свой GitHub-ник в Google-таблицу курса\n"
        "✅ Запустить проверку выполнения тестов в репозиториях\n\n"
        "📌 Далее потребуется указать свои данные для доступа к функционалу бота.\n\n"
        "Готовы начать? Выбирайте нужную команду! 🚀"
    )



# Текст на английском
def get_welcome_text_en() -> str:
    return (
        "👋 Welcome!\n\n"
        "This bot is designed for SUAI University students attending M. D. Polyak's courses.\n\n"
        "What can it do?\n"
        "✅ Add your GitHub username to the course's Google Sheet\n"
        "✅ Check test completion in repositories\n\n"
        "📌 You'll need to provide some information to access the bot's features.\n\n"
        "Ready to begin? Select an action! 🚀"
    )



# Формирование клавиатуры
def get_keyboard(language: str) -> InlineKeyboardMarkup:
    if language == "ru":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Switch to English", callback_data="lang_en")],
            [InlineKeyboardButton("Продолжить", callback_data="continue")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Переключиться на русский", callback_data="lang_ru")],
            [InlineKeyboardButton("Continue", callback_data="continue")]
        ])



def get_main_keyboard(lang: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Записать GitHub-ник в таблицу", callback_data="register_github")],
            [InlineKeyboardButton("🔄 Синхронизировать GitHub-ник", callback_data="sync_github")],
            [InlineKeyboardButton("🧪 Проверить выполнение тестов", callback_data="check_tests")],
            [InlineKeyboardButton("Switch to English", callback_data="lang_en2")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Submit GitHub username", callback_data="register_github")],
            [InlineKeyboardButton("🔄 Sync GitHub username", callback_data="sync_github")],
            [InlineKeyboardButton("🧪 Check test results", callback_data="check_tests")],
            [InlineKeyboardButton("Переключиться на русский", callback_data="lang_ru2")]
        ])


def update_last_menu(user_id, msg, keyboard):
    import json
    buttons_data = [[{"text": b.text, "callback_data": b.callback_data} for b in row] for row in keyboard.inline_keyboard]
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE users SET last_message = ?, last_buttons = ? WHERE user_id = ?",
        (msg, json.dumps(buttons_data), user_id)
    )
    conn.commit()
    conn.close()



# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_full_name", None)
    context.user_data.pop("awaiting_github", None)
    context.user_data.pop("auth_data", None)
    
    user_id = update.effective_user.id
    upsert_user(user_id)

    # Проверяем авторизацию и читаем last_message + last_buttons
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT authorized, last_message, last_buttons FROM users WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if row and row[0] == 1:
        lang = get_language(user_id)
        # Сообщение о повторной авторизации
        msg = "🔐 Вы уже авторизовались." if lang == "ru" else "🔐 You're already signed in."
        await update.message.reply_text(msg)

        # Если есть сохранённое последнее сообщение
        last_msg, last_buttons_json = row[1], row[2]
        if last_msg and last_buttons_json:
            import json
            buttons_data = json.loads(last_buttons_json)
            # Восстанавливаем клавиатуру
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(item["text"], callback_data=item["callback_data"])
                    for item in row_data
                ]
                for row_data in buttons_data
            ])
            await update.message.reply_text(last_msg, reply_markup=keyboard)
        return

    # Если не авторизован — как было
    language = get_language(user_id)
    text = get_welcome_text_ru() if language == "ru" else get_welcome_text_en()
    keyboard = get_keyboard(language)
    await update.message.reply_text(text, reply_markup=keyboard)



# Обработчик всех кнопок
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data == "lang_en":
        set_language(user_id, "en")
        await query.edit_message_text(get_welcome_text_en(), reply_markup=get_keyboard("en"))
    
    elif data == "lang_ru":
        set_language(user_id, "ru")
        await query.edit_message_text(get_welcome_text_ru(), reply_markup=get_keyboard("ru"))
    
    elif data == "continue":
        lang = get_language(user_id)
        if lang == "ru":
            prompt = "Введите номер группы и ФИО в формате:\n`1234 Фамилия Имя Отчество`"
        else:
            prompt = "Enter your group number and full name in this format:\n`1234 Lastname Firstname Patronymic`"
        await query.message.edit_text(prompt, parse_mode="Markdown")
        context.user_data["awaiting_full_name"] = True
        return WAITING_FOR_FULL_NAME
    
    elif data == "confirm_auth":
        context.user_data.pop("awaiting_full_name", None)
        lang = get_language(user_id)
    
        auth_data = context.user_data.get("auth_data")
        if not auth_data:
            await query.message.edit_text(
                "⚠️ Внутренняя ошибка. Повторите попытку." 
                if get_language(user_id) == "ru" 
                else "⚠️ Internal error. Please try again."
            )
            
            next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
            keyboard = get_main_keyboard(lang)
            await query.message.reply_text(next_msg, reply_markup=keyboard)

            update_last_menu(user_id, next_msg, keyboard)
            
            return ConversationHandler.END

        cursor = sqlite3.connect(DB_PATH).cursor()
        cursor.execute("""
            UPDATE users SET group_id = ?, surname = ?, name = ?, patronymic = ?, authorized = 1
            WHERE user_id = ?
        """, (
            auth_data["group_id"],
            auth_data["surname"],
            auth_data["name"],
            auth_data["patronymic"],
            user_id
        ))
        cursor.connection.commit()
        cursor.connection.close()

        msg = "✅ Успешная авторизация!" if lang == "ru" else "✅ Authorization successful!"
        await query.message.edit_text(msg)

        # Отображение следующего сообщения с кнопками
        next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
        
        keyboard = get_main_keyboard(lang)
        
        await query.message.reply_text(next_msg, reply_markup=keyboard)

        import json
        
        buttons_data = [
            [
                {"text": btn.text, "callback_data": btn.callback_data}
                for btn in row_buttons
            ]
            for row_buttons in keyboard.inline_keyboard
        ]

        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE users SET last_message = ?, last_buttons = ? WHERE user_id = ?",
            (next_msg, json.dumps(buttons_data), user_id)
        )
        conn.commit()
        conn.close()
        
        context.user_data.pop("auth_data", None)

        return ConversationHandler.END
    
    elif data == "cancel_auth":
        context.user_data.pop("awaiting_full_name", None)
        context.user_data.pop("auth_data", None)
    
        lang = get_language(user_id)
        text = get_welcome_text_ru() if lang == "ru" else get_welcome_text_en()
        keyboard = get_keyboard(lang)
        await query.message.edit_text(text, reply_markup=keyboard)
        return ConversationHandler.END
        
    elif data == "register_github":
        lang = get_language(user_id)
        prompt = (
            "Введите свой GitHub-ник:"
            if lang == "ru"
            else "Enter your GitHub username:"
        )
        await query.message.edit_text(prompt)
        context.user_data["awaiting_github"] = True
        return WAITING_FOR_GITHUB
        
    elif data == "confirm_github":
        github = context.user_data.pop("pending_github", None)
        if not github:
            await query.message.edit_text("⚠️ GitHub-ник не найден в памяти.")
            return ConversationHandler.END

        # вызов handle_github_submission как подфункции
        return await handle_github_submission(query, context, github)

    elif data == "cancel_github":
        context.user_data.pop("pending_github", None)

        lang = get_language(user_id)
        next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
        keyboard = get_main_keyboard(lang)

        await query.message.edit_text(next_msg, reply_markup=keyboard)

        update_last_menu(user_id, next_msg, keyboard)

        return ConversationHandler.END
        
    elif data == "sync_github":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT group_id, surname, name, patronymic FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()

        lang = get_language(user_id)

        if not row or not all(row):
            msg = "⚠️ Сначала укажите ФИО и группу." if lang == "ru" \
                else "⚠️ Please provide your full name and group first."
            await query.message.edit_text(msg)

            next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
            keyboard = get_main_keyboard(lang)
            await query.message.reply_text(next_msg, reply_markup=keyboard)

            update_last_menu(user_id, next_msg, keyboard)
            
            return ConversationHandler.END

        group_id_db, surname, name, patronymic = row

        async with httpx.AsyncClient(base_url="http://backend:8000") as client:
            try:
                r = await client.get("/courses")
                r.raise_for_status()
                courses = r.json()
            except Exception:
                msg = "⚠️ Не удалось получить список курсов." if lang == "ru" \
                    else "⚠️ Failed to retrieve course list."
                await query.message.edit_text(msg)

                next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
                keyboard = get_main_keyboard(lang)
                await query.message.reply_text(next_msg, reply_markup=keyboard)

                update_last_menu(user_id, next_msg, keyboard)
                
                return ConversationHandler.END

            course_id = None
            for i, _ in enumerate(courses, start=1):
                r = await client.get(f"/courses/{i}/groups")
                if r.status_code == 200 and group_id_db in r.json():
                    course_id = i
                    break

            if not course_id:
                msg = "⚠️ Курс не найден." if lang == "ru" else "⚠️ Course not found."
                await query.message.edit_text(msg)

                next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
                keyboard = get_main_keyboard(lang)
                await query.message.reply_text(next_msg, reply_markup=keyboard)

                update_last_menu(user_id, next_msg, keyboard)
                
                return ConversationHandler.END

            payload = {
                "surname": surname,
                "name": name,
                "patronymic": patronymic
            }

            try:
                r = await client.post(f"/courses/{course_id}/groups/{group_id_db}/github", json=payload, timeout=httpx.Timeout(15.0))
                r.raise_for_status()
                github = r.json().get("github")
            except httpx.HTTPStatusError as e:
                detail = e.response.json().get("detail", "")
                msg = f"⚠️ {detail}" if lang == "ru" else f"⚠️ {detail}"
                await query.message.edit_text(msg)

                next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
                keyboard = get_main_keyboard(lang)
                await query.message.reply_text(next_msg, reply_markup=keyboard)

                update_last_menu(user_id, next_msg, keyboard)
                
                return ConversationHandler.END

            # Сохраняем GitHub в БД
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE users SET github = ? WHERE user_id = ?", (github, user_id))
            conn.commit()
            conn.close()

            msg = "✅ GitHub-ник синхронизирован из таблицы." if lang == "ru" \
                else "✅ GitHub username synchronized from the table."
            await query.message.edit_text(msg)

            next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
            keyboard = get_main_keyboard(lang)
            await query.message.reply_text(next_msg, reply_markup=keyboard)

            update_last_menu(user_id, next_msg, keyboard)

            return ConversationHandler.END
    
    elif data == "check_tests":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT group_id, github FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()

        lang = get_language(user_id)
        
        if not row or not row[0] or not row[1]:
            await query.message.edit_text("⚠️ Сначала укажите ФИО, группу и GitHub-ник." if lang == "ru" else "⚠️ Please first provide your group, name and GitHub username.")
            
            lang = get_language(user_id)
            next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
            keyboard = get_main_keyboard(lang)
            await query.message.reply_text(next_msg, reply_markup=keyboard)

            update_last_menu(user_id, next_msg, keyboard)
            
            return ConversationHandler.END

        group_id_db, github = row
        context.user_data["test_github"] = github
        context.user_data["test_group"] = group_id_db

        async with httpx.AsyncClient(base_url="http://backend:8000") as client:
            try:
                resp = await client.get("/courses")
                resp.raise_for_status()
                courses = resp.json()
            except Exception:
                msg = "⚠️ Не удалось получить список курсов." if lang == "ru" else "⚠️ Failed to fetch course list."
                await query.message.edit_text(msg)
                next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
                keyboard = get_main_keyboard(lang)
                await query.message.reply_text(next_msg, reply_markup=keyboard)

                update_last_menu(user_id, next_msg, keyboard)
                
                return ConversationHandler.END

            matching_courses = []
            for i, course in enumerate(courses, start=1):
                r = await client.get(f"/courses/{i}/groups")
                if r.status_code == 200 and group_id_db in r.json():
                    matching_courses.append((i, course["name"]))

        if not matching_courses:
            msg = "❌ Не найдено подходящих курсов." if lang == "ru" else "❌ No matching courses found."
            await query.message.edit_text(msg)
            
            next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
            keyboard = get_main_keyboard(lang)
            await query.message.reply_text(next_msg, reply_markup=keyboard)

            update_last_menu(user_id, next_msg, keyboard)
            
            return ConversationHandler.END

        if len(matching_courses) == 1:
            context.user_data["test_course"] = matching_courses[0][0]
            return await show_lab_buttons(query, context)

        # Выбор курса вручную
        keyboard = [[InlineKeyboardButton(name, callback_data=f"select_course_{cid}")] for cid, name in matching_courses]
        
        keyboard.append([InlineKeyboardButton("↩️ Отмена" if lang == "ru" else "↩️ Cancel", callback_data="cancel_test")])
        
        await query.message.edit_text("📘 Выберите курс:" if lang == "ru" else "📘 Select a course:", reply_markup=InlineKeyboardMarkup(keyboard))
        
        return CHOOSING_COURSE

    elif data.startswith("check_lab_"):
        lab_id = data.removeprefix("check_lab_")
        course_id = context.user_data.get("test_course")
        group_id = context.user_data.get("test_group")
        github = context.user_data.get("test_github")
        lang = get_language(user_id)

        if not all([course_id, group_id, github]):
            msg = "⚠️ Внутренняя ошибка. Не хватает данных." if lang == "ru" \
                else "⚠️ Internal error: missing data."
            await query.message.edit_text(msg)
            
            next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
            keyboard = get_main_keyboard(lang)
            await query.message.reply_text(next_msg, reply_markup=keyboard)

            update_last_menu(user_id, next_msg, keyboard)
            
            return ConversationHandler.END

        await query.message.edit_text("⏳ Проверка выполняется..." if lang == "ru" else "⏳ Running tests...")

        async with httpx.AsyncClient(base_url="http://backend:8000") as client:
            try:
                r = await client.post(
                    f"/courses/{course_id}/groups/{group_id}/labs/{lab_id}/grade",
                    json={"github": github},
                    timeout=httpx.Timeout(60.0)
                )

                if r.status_code == 200:
                    data = r.json()
                    passed = data.get("passed", "")
                    result = data.get("result", "")
                    message = data.get("message", "")
                    checks = data.get("checks", [])

                    status_line = (
                        "✅ Все тесты пройдены успешно." if result == "✓"
                        else "❌ Обнаружены ошибки в тестах."
                    ) if lang == "ru" else (
                        "✅ All tests passed successfully." if result == "✓"
                        else "❌ Errors detected in tests."
                    )

                    full_msg = (
                        f"{status_line}\n"
                        f"{message}\n"
                        f"{passed}\n\n" +
                        "\n".join(checks)
                    )
                    await query.message.edit_text(full_msg, disable_web_page_preview=True)

                else:
                    try:
                        detail = r.json().get("detail", "")
                    except Exception:
                        detail = ""

                    if r.status_code == 400:
                        detail_map = {
                            "Missing course configuration": (
                                "❌ Отсутствует конфигурация курса.",
                                "❌ Course configuration is missing."
                            ),
                            "Столбец 'GitHub' не найден": (
                                "❌ Столбец 'GitHub' не найден в таблице.",
                                "❌ Column 'GitHub' not found in spreadsheet."
                            )
                        }
                    elif r.status_code == 403:
                        detail_map = {
                            "🚨 test_main.py был изменён или удалён": (
                                "❌ test_main.py был изменён или удалён.",
                                "❌ test_main.py was modified or deleted."
                            ),
                            "🚨 Изменён файл в папке tests/": (
                                "❌ В папке tests/ был изменён или удалён файл.",
                                "❌ A file in the `tests/` folder was modified or deleted."
                            ),
                            "🚨 Изменён файл в папке .github/workflows/": (
                                "❌ В папке .github/workflows/ был изменён или удалён файл.",
                                "❌ A file in the .github/workflows/ folder was modified or deleted."
                            )
                        }
                    elif r.status_code == 404:
                        detail_map = {
                            "Не удалось получить список коммитов": (
                                "❌ Не удалось получить список коммитов.",
                                "❌ Failed to fetch commit list."
                            ),
                            "Нет коммитов в репозитории": (
                                "❌ В репозитории отсутствуют коммиты.",
                                "❌ No commits found in repository."
                            ),
                            "Проверки CI не найдены": (
                                "❌ CI-проверки не найдены.",
                                "❌ CI checks not found."
                            ),
                            "Группа не найдена в Google Таблице": (
                                "❌ Группа не найдена в Google Таблице.",
                                "❌ Group not found in spreadsheet."
                            ),
                            "GitHub логин не найден в таблице": (
                                "❌ GitHub логин не найден в таблице.",
                                "❌ GitHub login not found in spreadsheet."
                            )
                        }
                    else:
                        detail_map = {}

                    if detail in detail_map:
                        msg = detail_map[detail][0] if lang == "ru" else detail_map[detail][1]
                    else:
                        msg = (
                            f"❌ Неизвестная ошибка {r.status_code}: {detail}"
                            if lang == "ru"
                            else f"❌ Unknown error {r.status_code}: {detail}"
                        )

                    await query.message.edit_text(msg)

            except Exception:
                msg = "⚠️ Ошибка при выполнении запроса." if lang == "ru" \
                    else "⚠️ Failed to perform the request."
                await query.message.edit_text(msg)
                
        lang = get_language(user_id)
        next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
        keyboard = get_main_keyboard(lang)
        await query.message.reply_text(next_msg, reply_markup=keyboard)

        update_last_menu(user_id, next_msg, keyboard)

        return ConversationHandler.END
        
    elif data.startswith("select_course_"):
        course_id = int(data.removeprefix("select_course_"))
        context.user_data["test_course"] = course_id
        return await show_lab_buttons(query, context)
        
    elif data == "cancel_test":
        lang = get_language(user_id)
        text = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
        keyboard = get_main_keyboard(lang)

        await query.message.edit_text(text, reply_markup=keyboard)

        update_last_menu(user_id, text, keyboard)

        # Чистим context.user_data от временных значений
        context.user_data.pop("test_github", None)
        context.user_data.pop("test_group", None)
        context.user_data.pop("test_course", None)

        return ConversationHandler.END
        
    elif data == "lang_en2":
            set_language(user_id, "en")
            text = "📌 Select an action:"
            keyboard = get_main_keyboard("en")
            await query.edit_message_text(text, reply_markup=keyboard)

            update_last_menu(user_id, text, keyboard)

    elif data == "lang_ru2":
            set_language(user_id, "ru")
            text = "📌 Выберите действие:"
            keyboard = get_main_keyboard("ru")
            await query.edit_message_text(text, reply_markup=keyboard)

            update_last_menu(user_id, text, keyboard)



async def handle_github_submission(query, context, github: str) -> int:
    user_id = query.from_user.id
    lang = get_language(user_id)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT group_id, surname, name, patronymic FROM users WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        await query.message.edit_text(
            "⚠️ Внутренняя ошибка. Повторите попытку." if lang == "ru"
            else "⚠️ Internal error. Please try again."
        )
        
        next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
        keyboard = get_main_keyboard(lang)
        await query.message.reply_text(next_msg, reply_markup=keyboard)

        update_last_menu(user_id, next_msg, keyboard)
        
        return ConversationHandler.END

    group_id_db, surname, name, patronymic = row

    async with httpx.AsyncClient(base_url="http://backend:8000") as client:
        resp = await client.get("/courses")
        resp.raise_for_status()
        courses = resp.json()

        course_id = None
        for i, _ in enumerate(courses, start=1):
            r = await client.get(f"/courses/{i}/groups")
            if r.status_code == 200 and group_id_db in r.json():
                course_id = i
                break

        payload = {
            "surname":    surname,
            "name":       name,
            "patronymic": patronymic,
            "github":     github
        }

        reg = await client.post(
            f"/courses/{course_id}/groups/{group_id_db}/register",
            json=payload,
            timeout=httpx.Timeout(15.0)
        )
    
    save_github = False
    
    if reg.status_code == 200:
        resp_json = reg.json()
        status = resp_json.get("status")
        if status == "already_registered":
            msg = "ℹ️ Этот GitHub-ник уже был записан ранее." if lang == "ru" \
                else "ℹ️ This GitHub username was already submitted earlier."
        else:
            msg = "✅ Ваш GitHub-ник успешно записан в таблицу." if lang == "ru" \
                else "✅ Your GitHub username has been successfully saved to the table."
            save_github = True
    elif reg.status_code == 400:
        msg = "⚠️ Ошибка в структуре таблицы курса. Обратитесь к преподавателю." if lang == "ru" \
            else "⚠️ Course table is misconfigured. Please contact your instructor."
    elif reg.status_code == 404:
        msg = "❌ Студент или GitHub-ник не найден. Убедитесь, что вы ввели всё правильно." if lang == "ru" \
            else "❌ Student or GitHub user not found. Please check your input."
    elif reg.status_code == 409:
        msg = "🚫 GitHub-ник уже был указан ранее. Для его изменения обратитесь к преподавателю." if lang == "ru" \
            else "🚫 Your GitHub username was already submitted earlier. Contact your instructor to change it."
    elif reg.status_code == 500:
        msg = "⚠️ Внутренняя ошибка сервера. Повторите попытку позже." if lang == "ru" \
            else "⚠️ Internal server error. Please try again later."
    else:
        msg = f"❓ Неизвестная ошибка (код {reg.status_code})" if lang == "ru" \
            else f"❓ Unknown error (code {reg.status_code})"

    await query.message.edit_text(msg)

    next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
    keyboard = get_main_keyboard(lang)
    await query.message.reply_text(next_msg, reply_markup=keyboard)

    import json
    buttons_data = [[{"text": b.text, "callback_data": b.callback_data} for b in row] for row in keyboard.inline_keyboard]
    
    if save_github:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE users SET github = ?, last_message = ?, last_buttons = ? WHERE user_id = ?",
            (github, next_msg, json.dumps(buttons_data), user_id)
        )
        conn.commit()
        conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE users SET last_message = ?, last_buttons = ? WHERE user_id = ?",
            (next_msg, json.dumps(buttons_data), user_id)
        )
        conn.commit()
        conn.close()

    return ConversationHandler.END
    
    

async def handle_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("awaiting_full_name", False):
        return ConversationHandler.END
        
    user_id = update.effective_user.id
    text = update.message.text.strip()
    lang = get_language(user_id)

    parts = text.split(maxsplit=4)
    if len(parts) < 3:
        msg = "❌ Неверный формат. Попробуйте снова: `1234 Фамилия Имя Отчество`" if lang == "ru" \
            else "❌ Invalid format. Please try again: `1234 Lastname Firstname Patronymic`"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return WAITING_FOR_FULL_NAME

    group = parts[0]
    surname = parts[1]
    name = parts[2]
    patronymic = parts[3] if len(parts) > 3 else ""

    async with httpx.AsyncClient(base_url="http://backend:8000") as client:
        try:
            resp = await client.get("/courses")
            resp.raise_for_status()
            courses = resp.json()
        except Exception:
            msg = "⚠️ Не удалось получить список курсов." if lang == "ru" else "⚠️ Couldn't retrieve the course list."
            await update.message.reply_text(msg)
            return ConversationHandler.END

        course_id = None
        for i, course in enumerate(courses, start=1):
            try:
                r = await client.get(f"/courses/{i}/groups")
                if r.status_code == 200 and group in r.json():
                    course_id = i
                    break
            except Exception:
                continue

        if course_id is None:
            msg = "❌ Не удалось найти введённый номер группы. Попробуйте снова." if lang == "ru" \
                else "❌ The group you entered wasn't found. Please try again."
            await update.message.reply_text(msg)
            return WAITING_FOR_FULL_NAME

        payload = {
            "surname": surname,
            "name": name,
            "patronymic": patronymic
        }

        try:
            check = await client.post(f"/courses/{course_id}/groups/{group}/check-student", json=payload)
            if check.status_code != 200:
                msg = "❌ Не удалось найти введённые ФИО. Попробуйте снова." if lang == "ru" \
                    else "❌ We couldn't find that name. Please try again."
                await update.message.reply_text(msg)
                return WAITING_FOR_FULL_NAME
        except Exception:
            msg = "⚠️ Ошибка при подключении к серверу." if lang == "ru" else "⚠️ Server connection error."
            await update.message.reply_text(msg)
            return WAITING_FOR_FULL_NAME

    # Сохраняем данные во временный контекст
    context.user_data["auth_data"] = {
        "group_id": group,
        "surname": surname,
        "name": name,
        "patronymic": patronymic
    }

    if lang == "ru":
        confirm_text = (
            f"🔎 Найдены следующие данные:\n"
            f"Группа: {group}\n"
            f"ФИО: {surname} {name} {patronymic}\n\n"
            f"Подтвердить?"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да", callback_data="confirm_auth"),
                InlineKeyboardButton("❌ Нет", callback_data="cancel_auth")
            ]
        ])
    else:
        confirm_text = (
            f"🔎 The following data was found in the table:\n"
            f"Group: {group}\n"
            f"Full name: {surname} {name} {patronymic}\n\n"
            f"Confirm data?"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes", callback_data="confirm_auth"),
                InlineKeyboardButton("❌ No", callback_data="cancel_auth")
            ]
        ])

    await update.message.reply_text(confirm_text, reply_markup=keyboard)
    return WAITING_FOR_CONFIRMATION



async def handle_github(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.pop("awaiting_github", False):
        return ConversationHandler.END

    github = update.message.text.strip()
    context.user_data["pending_github"] = github

    lang = get_language(update.effective_user.id)

    text = (
        f"🔎 Вы ввели GitHub-ник: `{github}`\n\nПодтвердить?"
        if lang == "ru"
        else f"🔎 You entered GitHub username: `{github}`\n\nConfirm?"
    )

    if lang == "ru":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да", callback_data="confirm_github"),
                InlineKeyboardButton("❌ Нет", callback_data="cancel_github")
            ]
        ])
    else:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes", callback_data="confirm_github"),
                InlineKeyboardButton("❌ No", callback_data="cancel_github")
            ]
        ])

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return WAITING_FOR_GITHUB



async def show_lab_buttons(query, context):
    course_id = context.user_data.get("test_course")
    group_id = context.user_data.get("test_group")
    lang = get_language(query.from_user.id)
    user_id = query.from_user.id

    if not course_id or not group_id:
        msg = "⚠️ Внутренняя ошибка. Не указан курс или группа." if lang == "ru" \
            else "⚠️ Internal error: course or group missing."
        await query.message.edit_text(msg)
        
        next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
        keyboard = get_main_keyboard(lang)
        await query.message.reply_text(next_msg, reply_markup=keyboard)

        update_last_menu(user_id, next_msg, keyboard)
        
        return ConversationHandler.END

    async with httpx.AsyncClient(base_url="http://backend:8000") as client:
        try:
            r = await client.get(f"/courses/{course_id}/groups/{group_id}/labs")
            r.raise_for_status()
            labs = r.json()
        except Exception:
            msg = "❌ Не удалось получить список лабораторных работ." if lang == "ru" \
                else "❌ Failed to retrieve lab list."
            await query.message.edit_text(msg)
            
            next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
            keyboard = get_main_keyboard(lang)
            await query.message.reply_text(next_msg, reply_markup=keyboard)

            update_last_menu(user_id, next_msg, keyboard)
            
            return ConversationHandler.END

    if not labs:
        msg = "❌ Нет доступных лабораторных работ." if lang == "ru" \
            else "❌ No available labs found."
        await query.message.edit_text(msg)
        
        next_msg = "📌 Выберите действие:" if lang == "ru" else "📌 Select an action:"
        keyboard = get_main_keyboard(lang)
        await query.message.reply_text(next_msg, reply_markup=keyboard)

        update_last_menu(user_id, next_msg, keyboard)
            
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(lab, callback_data=f"check_lab_{lab}")] for lab in labs]
    keyboard.append([InlineKeyboardButton("↩️ Отмена" if lang == "ru" else "↩️ Cancel", callback_data="cancel_test")])

    await query.message.edit_text(
        "🧪 Выберите лабораторную работу:" if lang == "ru" else "🧪 Select a lab to check:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return CHOOSING_LAB



# Точка входа
def main():
    init_db()
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_button, pattern="^continue$"),
            CallbackQueryHandler(handle_button, pattern="^register_github$")
        ],
        states={
            WAITING_FOR_FULL_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_full_name)
            ],
            WAITING_FOR_CONFIRMATION: [
                CallbackQueryHandler(handle_button, pattern="^(confirm_auth|cancel_auth)$")
            ],
            WAITING_FOR_GITHUB: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_github)
            ],
            CHOOSING_COURSE: [
                CallbackQueryHandler(handle_button, pattern="^select_course_\\d+$")
            ],
            CHOOSING_LAB: [
                CallbackQueryHandler(handle_button, pattern="^check_lab_")
            ],
        },
        fallbacks=[]
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)  # ⬅️ сначала conv_handler
    application.add_handler(CallbackQueryHandler(handle_button))

    application.run_polling()



if __name__ == "__main__":
    main()
