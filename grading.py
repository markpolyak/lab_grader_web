from github import Github
from pdfminer.high_level import extract_text
from PyPDF2 import PdfReader
from pdf2image import convert_from_bytes
import pytesseract
import io
import re


def check_pdf_content(name_file, name_repository, name_item, name_lab, num_group, name_student, main_sections, name_branch=None, sha_commit=None, token=None):
    # проверяем, что все обязательные параметры указаны
    if not all([name_file, name_repository, name_item, name_lab, num_group, name_student]):
        print("Все обязательные параметры должны быть указаны")
        return {"first_page": False, "missing_sections": main_sections}

    # скачивание файла
    try:
        if token: # если токен указан
            g = Github(token) # создаем экземпляр объект с аутентификацией
        else:
            Github() # иначе анонимный доступ

        # получаем доступ к указанному репозиторию
        repo = g.get_repo(name_repository)

        # если имя ветки не указано
        if not name_branch:
            # запрашиваем файл из ветки по умолчанию
            file_content = repo.get_contents(name_file)
        else:
            if not sha_commit: # если не указан коммит
                # запрашиваем файл из последнего коммита указанной ветки
                file_content = repo.get_contents(name_file, ref=name_branch)
            else: # иначе запрашиваем из указанного коммита
                file_content = repo.get_contents(name_file, ref=sha_commit)
        # декодируем содержимое файла в бинарные данные (PDF)
        content = file_content.decoded_content
    except Exception as e:
        print(f"Ошибка при скачивании файла {name_file} из {name_repository}: {e}")
        return {"first_page": False, "missing_sections": main_sections}

    # проверка количества страниц
    try:
        # создаем объект из бинарных данных
        pdf = PdfReader(io.BytesIO(content))
        # получаем количество страниц
        num_pages = len(pdf.pages)
        if num_pages < 2:
            print("PDF содержит только одну страницу, проверка разделов невозможна")
            return {"first_page": False, "missing_sections": main_sections}
    except Exception as e:
        print(f"Ошибка при чтении PDF: {e}")
        return {"first_page": False, "missing_sections": main_sections}

    # пытаемся извлечь текст с первой и с остальных страниц
    # с помощью pdfminer.six
    # и переводим текст в нижний регистр
    first_page_text = extract_text(io.BytesIO(content), page_numbers=[0]).lower()
    all_pages_text = extract_text(io.BytesIO(content), page_numbers=range(1, num_pages)).lower()

    # если не удалось извлечь, пробуем OCR
    if not first_page_text or not all_pages_text:
        print("Текст не извлечен с помощью pdfminer, пробуем OCR")
        try:
            images = convert_from_bytes(content)
            first_page_text = pytesseract.image_to_string(images[0], lang='rus+eng').lower() if images else ""
            all_pages_text = " ".join(pytesseract.image_to_string(img, lang='rus+eng').lower() for img in images[1:]) if len(images) > 1 else ""
        except Exception as e:
            print(f"Ошибка при использовании OCR: {e}")
            return {"first_page": False, "missing_sections": main_sections}
    # удаляем лишние пробелы, знаки препинания
    # для нормализации текста
    first_page_text = re.sub(r'[^\w\s]', '', re.sub(r'\s+', ' ', first_page_text.strip()))
    all_pages_text = re.sub(r'[^\w\s]', '', re.sub(r'\s+', ' ', all_pages_text.strip()))

    # выводим извлеченный текста
    print(f"Текст первой страницы: '{first_page_text}'")
    print(f"Текст остальных страниц: '{all_pages_text[:1000]}...'")  # Ограничение для читаемости

    # проверяем, получилось ли извлечь текст
    if not first_page_text:
        print("Не удалось извлечь текст с первой страницы")
        return {"first_page": False, "missing_sections": main_sections}
    if not all_pages_text and num_pages > 1:
        print("Не удалось извлечь текст с остальных страниц")
        return {"first_page": False, "missing_sections": main_sections}

    formatted_name = format_name(name_student)

    # удаляем знаки препинания и
    # переводим в нижний регистр входные параметры
    substrings = [re.sub(r'[^\w\s]', '', s.lower()) for s in [name_item, name_lab, num_group, formatted_name] if s]


    # вызываем функцию для проверки присутствия
    # всех подстрок на одной странице
    first_page_valid = check_substring_exist(first_page_text, substrings)

    # тоже самое, но с остальными страницами для
    # проверки соответствующих основных разделов
    main_sections = [re.sub(r'[^\w\s]', '', s.lower()) for s in main_sections if s]
    missing_sections = check_sections_in_text(all_pages_text, main_sections)

    # выводим результаты
    if first_page_valid:
        print("\nПервая страница соответствует требованиям")
    else:
        print("\nНе все данные присутствуют на первой странице")

    if missing_sections:
        print(f"Отсутствуют следующие разделы: {missing_sections}")
    else:
        print("Все основные разделы присутствуют.")

    return {"first_page": first_page_valid, "missing_sections": missing_sections}

# функция, которая проверяет присутствуют ли
# основные разделы в тексте
def check_sections_in_text(text, sections):
    missing_sections = []
    for section in sections:
        if section not in text:
            missing_sections.append(section)
    return missing_sections

# проверяет, присутствуют ли
# все подстроки в тексте
def check_substring_exist(text, substrings):
    for substring in substrings:
        if substring not in text:
            return False
    return True

def format_name(full_name):
    # разделяем полное имя на части
    parts = full_name.strip().split()
    
    # проверяем, что имя состоит из трех частей
    if len(parts) != 3:
        return full_name  # Возвращаем исходное имя, если формат неверный
    
    surname, first_name, patronymic = parts
    
    # Формируем имя в формате И. О. Фамилия
    return f"{first_name[0]}. {patronymic[0]}. {surname}"
