from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, InlineKeyboardButton, KeyboardButton


main_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👤 Профиль"),
                                                KeyboardButton(text="📚 Выбрать дисциплину")]], 
                                                resize_keyboard=True,
                                                one_time_keyboard=True)

profile_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✏️ Редактировать профиль")],
                                                        [KeyboardButton(text="🔙 Назад")]],
                                                        resize_keyboard=True,
                                                        one_time_keyboard=True)



def courses_keyboard(courses):
    # Создаем список для рядов кнопок
    keyboard = []
    
    # Группируем кнопки по 2 в ряд
    row = []
    for index, course in enumerate(courses, 1):
        # Создаем кнопку для дисциплины
        button = InlineKeyboardButton(
            text=f"{course['name']} ({course['semester']})",
            callback_data=f"course_{course['id']}"
        )
        row.append(button)
        
        # Каждые 2 кнопки или в конце списка создаем новый ряд
        if index % 2 == 0 or index == len(courses):
            keyboard.append(row)
            row = []
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)  # Явно передаем структуру


def groups_keyboard(groups):
    buttons = []
    # Каждая группа в отдельном ряду (вертикальный список)
    for group in groups:
        buttons.append([
            InlineKeyboardButton(
                text=group,
                callback_data=f"group_{group}"
            )
        ])
    # Кнопка "Назад" тоже в отдельном ряду
    buttons.append([
        InlineKeyboardButton(
            text="🔙 Назад к дисциплинам", 
            callback_data="back_to_courses"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def labs_keyboard(labs):
    buttons = []
    # Каждая лабораторная работа в отдельном ряду
    for lab in labs:
        buttons.append([
            InlineKeyboardButton(
                text=lab,
                callback_data=f"lab_{lab}"
            )
        ])
    # Кнопка "Назад" в отдельном ряду
    buttons.append([
        InlineKeyboardButton(
            text="🔙 Назад к группам", 
            callback_data="back_to_groups"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)