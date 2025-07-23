import requests
import re
from io import StringIO


def extract_latex_content(text):
    """Парсер LaTeX-контента с обработкой таблиц и спецформатирования"""
    # Удаление комментариев
    text = re.sub(r'%.*', '', text)
    
    # Извлечение содержимого между \begin{document} и \end{document}
    doc_match = re.search(r'\\begin{document}(.*?)\\end{document}', text, re.DOTALL)
    if not doc_match:
        return ""
    content = doc_match.group(1)
    
    # Обработка таблиц - извлечение текста из ячеек
    content = re.sub(r'\\begin{tabular}.*?\\end{tabular}', 
                    lambda m: ' '.join(re.findall(r'\{([^{}]*)\}', m.group())), 
                    content, flags=re.DOTALL)
    
    # Удаление LaTeX-команд с сохранением их аргументов
    content = re.sub(r'\\[a-zA-Z]+\*?\s*\{([^{}]*)\}', r'\1', content)
    content = re.sub(r'\\[a-zA-Z]+\*?\s*', ' ', content)
    
    # Удаление оставшихся спецсимволов
    content = re.sub(r'[{}]', '', content)
    content = re.sub(r'\\[^\s]*', '', content)
    
    # Нормализация пробелов
    content = ' '.join(content.split())
    return content

def extract_titlepage_content(text):
    """Извлекает содержимое титульного листа"""
    # Удаление комментариев
    text = re.sub(r'%.*', '', text)
    
    # Извлечение содержимого между \begin{titlepage} и \end{titlepage}
    titlepage_match = re.search(r'\\begin{titlepage}(.*?)\\end{titlepage}', text, re.DOTALL)
    if not titlepage_match:
        return ""
    content = titlepage_match.group(1)
    
    # Обработка содержимого (аналогично extract_latex_content)
    content = re.sub(r'\\[a-zA-Z]+\*?\s*\{([^{}]*)\}', r'\1', content)
    content = re.sub(r'\\[a-zA-Z]+\*?\s*', ' ', content)
    content = re.sub(r'[{}]', '', content)
    content = re.sub(r'\\[^\s]*', '', content)
    content = ' '.join(content.split())
    
    return content
    
def convert_fullname_to_initials(fullname):
    """Преобразует 'Фамилия Имя Отчество' в 'И.О.Фамилия'"""
    parts = fullname.split()
    if len(parts) == 3:
        surname, name, patronymic = parts
        return f"{name[0]}.{patronymic[0]}.{surname}"
    return fullname

def grade_report_latex(repo, filename, subject, lab_name, group, student_name, required_sections, branch=None, commit=None, github_token=None):
    """Улучшенная функция проверки LaTeX-отчета"""
    # Скачивание файла
    ref = commit if commit else branch if branch else 'main'
    api_url = f"https://api.github.com/repos/{repo}/contents/{filename}?ref={ref}"
    
    try:
        # Заголовки для GitHub API
        headers = {
            'Accept': 'application/vnd.github.v3+json',
        }
        if github_token:
            headers['Authorization'] = f'token {github_token}'
        
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        
        # Декодируем содержимое файла из base64
        import base64
        content = base64.b64decode(response.json()['content']).decode('utf-8')
        
    except Exception as e:
        raise Exception(f"Ошибка при загрузке файла через GitHub API: {str(e)}")

    # Извлечение содержимого титульного листа
    titlepage_text = extract_titlepage_content(content)
    
    # Извлечение основного содержимого
    full_text = extract_latex_content(content)
    
    # Преобразование 'Фамилия Имя Отчество' в 'И.О.Фамилия'
    student_name = convert_fullname_to_initials(student_name)
    
    # Проверка титульной информации (только в titlepage)
    title_checks = {
        "subject": subject.lower() in titlepage_text.lower(),
        "lab_name": lab_name.lower() in titlepage_text.lower(),
        "group": str(group).lower() in titlepage_text.lower(),
        "student_name": student_name.lower() in titlepage_text.lower().replace(" ", "")
    }
    
    # Проверка разделов (ищем как \section{Name}, так и \section*{Name})
    sections = re.findall(r'\\section\*?\{([^{}]*)\}', content)
    section_checks = {sec: any(sec.lower() in s.lower() for s in sections) 
                     for sec in required_sections}
    
    return {
        "title_page": title_checks,
        "sections": section_checks
    }
