
```markdown
# Система обнаружения плагиата с помошью compare50/ Plagiarism Detection System

## Быстрый старт / Quick Start

### 1. Установка / Installation
```bash
pip install -r requirements.txt
pip install compare50
```

### 2. Настройка / Configuration
```bash
echo "GITHUB_TOKEN=ваш_токен" > .env
cp credentials.example.json credentials.json
```

Конфигурация `courses/{course_id}.yaml`:
```yaml
labs:
  "1":
    plagiarism:
      enabled: true     # Включить проверку
      threshold: 7.5    # Порог сходства (0-100)
      reference_files: [data/distribution/lab1.cpp]  # Эталонные файлы
```

### 3. Запуск проверки / Running Checks
```bash
# Запуск API сервера
uvicorn main:app --reload

# Или прямое выполнение
python -m services.plagiarism.checker --course ваш_идентификатор_курса
```

## Основные возможности / Key Features
- **Автоматическое обнаружение плагиата** с использованием Compare50  
  *Automated code similarity detection using Compare50*
- **Интеграция с GitHub CI** (проверяет только успешные сборки)  
  *GitHub CI integration (only checks passing builds)*
- **Экспорт результатов** в Google Таблицы  
  *Results exported to Google Sheets*
- **Генерация HTML отчетов**  
  *HTML reports generation*
- **REST API + кнопка в интерфейсе**  
  *REST API + frontend button*

## Базовое использование / Basic Usage
1. Настройте YAML-файл курса  
   *Configure your course YAML file*
2. Запустите проверку через:  
   *Run the check via:*
   - API: `POST /api/plagiarism/run/{course_id}`
   - CLI: `python -m services.plagiarism.checker --course ваш_идентификатор_курса`
   - Интерфейс: Кнопка "Запустить проверку на плагиат"  
     *Frontend: Click "Run Plagiarism Check" button*
3. Просмотр результатов:  
   *View results:*
   - HTML: `reports/comparisons/{курс}/{лаба}/index.html`  
     *HTML: reports/comparisons/{course}/{lab}/index.html*
   - Google Таблицы: Настроенная колонка статуса  
     *Google Sheets: Configured status column*

## Требования / Requirements
- Python 3.10+
- Compare50
- Токен GitHub (права repo/workflow)  
  *GitHub token (repo/workflow permissions)*
- Аккаунт Google Service Account  
  *Google Service Account*
