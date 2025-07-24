from aiogram import *
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from fastapi import HTTPException
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from bot_db_manager import Database
import requests
import markups as mp
import os


bd = Database('bot_db.db')
API_BASE_URL = os.getenv("API_BASE_URL")
router = Router()

class UserRegister(StatesGroup):
    WaitingForName = State()
    WaitingForSurname = State()
    WaitingForPatronim = State()
    WaitingForNickname = State()

class ProfileEdit(StatesGroup):
    WaitingForNewName = State()
    WaitingForNewSurname = State()
    WaitingForNewPatronim = State()
    WaitingForNewNickname = State()

class SelectingData(StatesGroup):
    SelectingCourse = State()
    SelectingGroup = State()
    SelectingLab = State()
    SelectingCheck = State()

@router.message(Command('start'))
async def start(message: Message, state: FSMContext):
    if(not bd.user_exist(message.from_user.id)):
        bd.add_user(message.from_user.id)
        await message.answer("Начало процесса регистрации\n Введите свое имя: ")
        await state.set_state(UserRegister.WaitingForName)
    else:        
        await message.answer("Вы уже зарегистрированны", reply_markup=mp.main_keyboard)


@router.message(UserRegister.WaitingForName)
async def set_user_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name or not all(x.isalpha() or x.isspace() for x in name):
        await message.answer("Пожалуйста, введите корректное имя.")
        return
    bd.set_user_name(message.from_user.id, name)
    await message.answer("Введите свое отчество:")
    await state.set_state(UserRegister.WaitingForPatronim)

@router.message(UserRegister.WaitingForPatronim)
async def set_user_patronim(message: Message, state: FSMContext):
    patronim = message.text.strip()
    if not patronim or not all(x.isalpha() or x.isspace() for x in patronim):
        await message.answer("Пожалуйста, введите корректное отчество.")
        return
    bd.set_user_patronim(message.from_user.id, patronim)
    await message.answer("Введите свою фамилию:")
    await state.set_state(UserRegister.WaitingForSurname)

@router.message(UserRegister.WaitingForSurname)
async def set_user_surname(message: Message, state: FSMContext):
    surname = message.text.strip()
    if not surname or not all(x.isalpha() or x.isspace() for x in surname):
        await message.answer("Пожалуйста, введите корректную фамилию.")
        return
    bd.set_user_surname(message.from_user.id, surname)
    await message.answer("Введите свой GitHub nickname:")
    await state.set_state(UserRegister.WaitingForNickname)

@router.message(UserRegister.WaitingForNickname)
async def set_user_nickname(message: Message, state: FSMContext):
    nickname = message.text.strip()
    if not nickname:
        await message.answer("Пожалуйста, введите корректный GitHub nickname.")
        return
    
    # Допустимые символы для GitHub nickname (согласно документации)
    allowed_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    allowed_set = set(allowed_chars)
    
    # Проверка 1: Длина (1-39 символов)
    if len(nickname) > 39:
        await message.answer("GitHub nickname должен быть короче 40 символов.")
        return
    
    # Проверка 2: Только допустимые символы
    if not all(char in allowed_set for char in nickname):
        await message.answer("GitHub nickname может содержать только: буквы, цифры, дефисы и подчёркивания.")
        return
    
    # Проверка 3: Не может начинаться/заканчиваться дефисом
    if nickname.startswith('-') or nickname.endswith('-'):
        await message.answer("GitHub nickname не может начинаться или заканчиваться дефисом.")
        return
    
    try:
        github_response = requests.get(f"https://api.github.com/users/{nickname}")
        if github_response.status_code != 200:
            raise HTTPException(status_code=404, detail="Пользователь GitHub не найден")
    except Exception:
        raise HTTPException(status_code=500, detail="Ошибка проверки GitHub пользователя")
    bd.set_user_github_nickname(message.from_user.id, nickname)
    await message.answer("Регистрация прошла успешно\nДобро пожаловать!", reply_markup=mp.main_keyboard)
    await state.clear()

@router.message(F.text == '👤 Профиль')
async def menu_profile(message: Message):
    await message.answer(f"Ваш профиль:\nФИО: {bd.get_user_name(message.from_user.id)} {bd.get_user_patronim(message.from_user.id)} {bd.get_user_surname(message.from_user.id)}\nGithub nickname: {bd.get_user_github_nickname(message.from_user.id)}", reply_markup=mp.profile_keyboard)

@router.message(F.text == '✏️ Редактировать профиль')
async def edit_profile(message: Message, state: FSMContext):
    await message.answer("Редактирование профиля\n Введите новое имя:")
    await state.set_state(ProfileEdit.WaitingForNewName)

@router.message(ProfileEdit.WaitingForNewName)
async def set_new_user_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name or not all(x.isalpha() or x.isspace() for x in name):
        await message.answer("Пожалуйста, введите корректное имя.")
        return
    bd.set_user_name(message.from_user.id, name)
    await message.answer("Введите новое отчество:")
    await state.set_state(ProfileEdit.WaitingForNewPatronim)

@router.message(ProfileEdit.WaitingForNewPatronim)
async def set_new_user_patronim(message: Message, state: FSMContext):
    patronim = message.text.strip()
    if not patronim or not all(x.isalpha() or x.isspace() for x in patronim):
        await message.answer("Пожалуйста, введите корректное отчество.")
        return
    bd.set_user_patronim(message.from_user.id, patronim)
    await message.answer("Введите новую фамилию:")
    await state.set_state(ProfileEdit.WaitingForNewSurname)

@router.message(ProfileEdit.WaitingForNewSurname)
async def set_new_user_surname(message: Message, state: FSMContext):
    surname = message.text.strip()
    if not surname or not all(x.isalpha() or x.isspace() for x in surname):
        await message.answer("Пожалуйста, введите корректную фамилию.")
        return
    bd.set_user_surname(message.from_user.id, surname)
    await message.answer("Введите новый GitHub nickname:")
    await state.set_state(ProfileEdit.WaitingForNewNickname)

@router.message(ProfileEdit.WaitingForNewNickname)
async def set_new_user_nickname(message: Message, state: FSMContext):
    nickname = message.text.strip()
    if not nickname:
        await message.answer("Пожалуйста, введите корректный новый GitHub nickname.")
        return
    
    # Допустимые символы для GitHub nickname (согласно документации)
    allowed_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    allowed_set = set(allowed_chars)
    
    # Проверка 1: Длина (1-39 символов)
    if len(nickname) > 39:
        await message.answer("GitHub nickname должен быть короче 40 символов.")
        return
    
    # Проверка 2: Только допустимые символы
    if not all(char in allowed_set for char in nickname):
        await message.answer("GitHub nickname может содержать только: буквы, цифры, дефисы и подчёркивания.")
        return
    
    # Проверка 3: Не может начинаться/заканчиваться дефисом
    if nickname.startswith('-') or nickname.endswith('-'):
        await message.answer("GitHub nickname не может начинаться или заканчиваться дефисом.")
        return
    
    try:
        github_response = requests.get(f"https://api.github.com/users/{nickname}")
        if github_response.status_code != 200:
            raise HTTPException(status_code=404, detail="Пользователь GitHub не найден")
    except Exception:
        raise HTTPException(status_code=500, detail="Ошибка проверки GitHub пользователя")
    bd.set_user_github_nickname(message.from_user.id, nickname)
    bd.set_user_github_nickname(message.from_user.id, nickname)
    await message.answer("Профиль успешно отредактирован")  
    await state.clear()
    await menu_profile(message)

@router.message(F.text == '🔙 Назад')
async def go_back(message: Message):
    await message.answer("Возвращение в главное меню", reply_markup=mp.main_keyboard)


@router.message(F.text == '📚 Выбрать дисциплину')
async def select_course(message: Message, state: FSMContext):
    response = requests.get(f"{API_BASE_URL}/courses")
    print("response:\n", response, "\n\n")
    if response.status_code != 200:
        await message.answer("⚠️ Ошибка получения дисциплин")
        return
    await message.answer("Выберите дисциплину:", reply_markup=mp.courses_keyboard(response.json()))
    await state.set_state(SelectingData.SelectingCourse)

@router.callback_query(SelectingData.SelectingCourse, F.data.startswith("course_"))
async def select_group(callback: CallbackQuery, state: FSMContext):
    course_id=callback.data.split("_")[1]
    await state.update_data(course_id=course_id)
    data = await state.get_data()

    response=requests.get(f"{API_BASE_URL}/courses/{data["course_id"]}/groups")
    if response.status_code != 200:
        await callback.message.answer("⚠️ Ошибка получения групп")
        return
    await callback.message.edit_text("Выберите группу:", reply_markup=mp.groups_keyboard(response.json()))
    await state.set_state(SelectingData.SelectingGroup)

@router.callback_query(SelectingData.SelectingGroup, F.data.startswith("group_"))
async def select_lab(callback: CallbackQuery, state: FSMContext):
    group_id=callback.data.split("_")[1]
    await state.update_data(group_id=group_id)
    data = await state.get_data()

    response=requests.get(f"{API_BASE_URL}/courses/{data["course_id"]}/groups/{data["group_id"]}/labs")
    if response.status_code != 200:
        await callback.message.answer("⚠️ Ошибка получения лабораторных работ")
        return
    await callback.message.edit_text("Выберите лабораторную работу:", reply_markup=mp.labs_keyboard(response.json()))
    await state.set_state(SelectingData.SelectingLab)


@router.callback_query(SelectingData.SelectingLab, F.data.startswith("lab_"))
async def select_check(callback: CallbackQuery, state: FSMContext):
    lab_id = callback.data.split("_")[1]
    await state.update_data(lab_id=lab_id)
    data = await state.get_data()

    await callback.message.answer(
        f"Вы выбрали:\n"
        f"Дисциплина: {data['course_id']}\n"
        f"Группа: {data['group_id']}\n"
        f"Лабораторная: {data['lab_id']}\n\n"
        f"Начать проверку?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="confirm_check")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_check")]
        ])
    )

    await state.set_state(SelectingData.SelectingCheck)

@router.callback_query(SelectingData.SelectingCheck, F.data == "confirm_check")
async def confirm_check(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    
    registration_data = {
            "name": bd.get_user_name(callback.from_user.id),
            "surname": bd.get_user_surname(callback.from_user.id),
            "patronymic": bd.get_user_patronim(callback.from_user.id),
            "github": bd.get_user_github_nickname(callback.from_user.id)
        }

    reg_response = requests.post(f"{API_BASE_URL}/courses/{data["course_id"]}/groups/{data["group_id"]}/register", json=registration_data)

    if reg_response.status_code != 200:
        await callback.message.answer("⚠️ Ошибка проверки данных студента.\n Проверьте свои данные или обратитесь к преподавателю.")
        return        
    else:
        await callback.message.answer("🔄 Проверка запущена.")

    grade_data = {"github": bd.get_user_github_nickname(callback.from_user.id)}
    response = requests.post(f"{API_BASE_URL}/courses/{data["course_id"]}/groups/{data["group_id"]}/labs/{data["lab_id"]}/grade", json=grade_data)
    result = response.json()
    if response.status_code == 200 and result["result"] == '✓':
        await callback.message.answer('✅ Все проверки пройдены', reply_markup=mp.main_keyboard)
    else:
        await callback.message.answer('❌ Обнаружены ошибки', reply_markup=mp.main_keyboard)
    await state.clear()

@router.callback_query(SelectingData.SelectingCheck, F.data == "cancel_check")
async def cancel_check(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Проверка отменена.\nПереход в главное меню", reply_markup=mp.main_keyboard)
    await state.clear()

@router.callback_query(F.data == "back_to_courses")
async def back_to_courses(callback: CallbackQuery, state: FSMContext):
    response = requests.get(f"{API_BASE_URL}/courses")
    print("response:\n", response, "\n\n")
    if response.status_code != 200:
        await callback.message.answer("⚠️ Ошибка получения дисциплин")
        return
    await callback.message.answer("Выберите дисциплину:", reply_markup=mp.courses_keyboard(response.json()))
    await state.set_state(SelectingData.SelectingCourse)

@router.callback_query(F.data == "back_to_groups")
async def back_to_groups(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    response=requests.get(f"{API_BASE_URL}/courses/{data["course_id"]}/groups")
    if response.status_code != 200:
        await callback.message.answer("⚠️ Ошибка получения групп")
        return
    await callback.message.edit_text("Выберите группу:", reply_markup=mp.groups_keyboard(response.json()))
    await state.set_state(SelectingData.SelectingGroup)



