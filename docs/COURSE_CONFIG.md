# Опции конфигурации курса

Этот документ описывает все поддерживаемые опции в YAML конфигурации курса.

## Структура файла

```yaml
course:
  # Общая информация о курсе

labs:
  "0":
    # Конфигурация лабораторной работы 0
  "1":
    # Конфигурация лабораторной работы 1

misc:
  # Дополнительные настройки
```

---

## Секция `course`

Общая информация о курсе.

### `name` (обязательно)
**Тип:** `string`
**Описание:** Полное название курса
**Пример:**
```yaml
name: Операционные системы
```

### `alt-names`
**Тип:** `list[string]`
**Описание:** Альтернативные названия курса (для поиска/фильтрации)
**Пример:**
```yaml
alt-names:
  - OS
  - Operating systems
  - ОС
```

### `semester`
**Тип:** `string`
**Описание:** Семестр проведения курса
**Пример:**
```yaml
semester: Осень 2025
```

### `university`
**Тип:** `string`
**Описание:** Название университета
**Пример:**
```yaml
university: ИТМО
```

### `email`
**Тип:** `string`
**Описание:** Контактный email преподавателя
**Пример:**
```yaml
email: teacher@university.edu
```

### `timezone`
**Тип:** `string`
**Описание:** Часовой пояс для расчёта дедлайнов
**Пример:**
```yaml
timezone: UTC+3
```

---

## Секция `course.github`

Настройки интеграции с GitHub.

### `organization` (обязательно)
**Тип:** `string`
**Описание:** Название GitHub организации с репозиториями студентов
**Пример:**
```yaml
github:
  organization: suai-os-2025
```

### `teachers`
**Тип:** `list[string]`
**Описание:** Список преподавателей (имена и GitHub username'ы)
**Пример:**
```yaml
github:
  teachers:
    - "Mark Polyak"
    - markpolyak
```

---

## Секция `course.google`

Настройки интеграции с Google Sheets.

### `spreadsheet` (обязательно)
**Тип:** `string`
**Описание:** ID таблицы Google Sheets (из URL)
**Пример:**
```yaml
google:
  spreadsheet: 1BoVLNZpP6zSc7DPSVkbxaQ_oJZcZ_f-zyMCfF1gw-PM
```

### `info-sheet` (обязательно)
**Тип:** `string`
**Описание:** Название листа с информацией о студентах и оценками
**Пример:**
```yaml
google:
  info-sheet: График
```

### `task-id-column` (обязательно)
**Тип:** `integer`
**Описание:** Номер колонки с номерами вариантов (TASKID). Нумерация с 0.
**Примечание:** В конфиге указывается 0-based индекс, система автоматически конвертирует в 1-based для gspread API.
**Пример:**
```yaml
google:
  task-id-column: 0  # Колонка A
```

### `student-name-column` (обязательно)
**Тип:** `integer`
**Описание:** Номер колонки с именами студентов. Нумерация с 0.
**Пример:**
```yaml
google:
  student-name-column: 1  # Колонка B
```

### `lab-column-offset`
**Тип:** `integer`
**По умолчанию:** `0`
**Описание:** Смещение колонок с лабораторными работами относительно `student-name-column`.
**Пример:**
```yaml
google:
  lab-column-offset: 1  # Лабы начинаются через 1 колонку после имени студента
```

---

## Секция `course.staff`

Список преподавателей и ассистентов.

**Тип:** `list[dict]`
**Описание:** Информация о преподавателях для отображения на frontend
**Пример:**
```yaml
staff:
  - name: Поляк Марк Дмитриевич
    title: ст. преп.
    status: лектор
  - name: Иванов Иван Иванович
    title: ассистент
    status: лабораторные работы
```

---

## Секция `course.labs`

Конфигурация лабораторных работ. Ключ - номер лабораторной работы (строка).

### `github-prefix` (обязательно)
**Тип:** `string`
**Описание:** Префикс названия репозитория. Полное имя репозитория: `{github-prefix}-{username}`
**Пример:**
```yaml
labs:
  "1":
    github-prefix: os-task1  # Репозиторий: os-task1-studentname
```

### `short-name` (обязательно)
**Тип:** `string`
**Описание:** Краткое название лабораторной работы (для заголовка колонки в Google Sheets)
**Пример:**
```yaml
labs:
  "1":
    short-name: ЛР1
```

---

## CI/CD опции

### `ci`
**Тип:** `list` или `dict`
**Описание:** Настройка проверки GitHub Actions workflows

**Формат 1 (простой):**
```yaml
ci:
  - workflows  # Проверяет все найденные workflows
```

**Формат 2 (с указанием конкретных jobs):**
```yaml
ci:
  workflows:
    - run-autograding-tests
    - cpplint
    - build (MINGW64, MinGW Makefiles)
```

**Примечание:** Если указаны конкретные jobs, будут проверяться только они. Если не указаны, используются DEFAULT_JOB_NAMES из `ci_checker.py`.

---

## TASKID опции

### `taskid-max`
**Тип:** `integer`
**Описание:** Максимальный номер варианта для валидации
**Пример:**
```yaml
taskid-max: 25
```

### `taskid-shift`
**Тип:** `integer`
**По умолчанию:** `0`
**Описание:** Смещение для расчёта ожидаемого TASKID. Формула: `expected = (taskid_from_sheet + shift - 1) % max + 1`
**Пример:**
```yaml
taskid-max: 20
taskid-shift: 4
# Студент с TASKID=1 → ожидается (1+4-1)%20+1 = 5
```

### `ignore-task-id`
**Тип:** `boolean`
**По умолчанию:** `false`
**Описание:** Отключает проверку TASKID из логов
**Пример:**
```yaml
ignore-task-id: True
```

---

## Штрафы (Penalty)

### `penalty-max`
**Тип:** `integer`
**По умолчанию:** `0`
**Описание:** Максимальное количество штрафных баллов
**Пример:**
```yaml
penalty-max: 9
```

### `penalty-strategy`
**Тип:** `string`
**Возможные значения:** `"weekly"`, `"daily"`
**По умолчанию:** `"weekly"`
**Описание:** Стратегия начисления штрафов:
- `weekly`: 1 балл за каждую начатую неделю просрочки
- `daily`: 1 балл за каждый день просрочки

**Пример:**
```yaml
penalty-strategy: weekly
```

---

## Извлечение баллов (Score)

### `score.patterns`
**Тип:** `list[string]`
**Описание:** Список regex паттернов для извлечения баллов из логов CI. Паттерны пробуются по порядку, используется первый совпавший. Первая capturing group `()` должна содержать число баллов.

**Важно:**
- В YAML используйте одинарные кавычки `'...'` для паттернов
- Backslash в regex НЕ нужно экранировать дважды (в одинарных кавычках YAML backslash литеральный)
- Паттерны выполняются с флагами `re.MULTILINE | re.IGNORECASE`
- Принимаются оба разделителя `.` и `,` в числах
- Если паттерны заданы, но баллы не найдены → ошибка

**Пример:**
```yaml
score:
  patterns:
    - 'ПРЕДВАРИТЕЛЬНАЯ.*?ОЦЕНКА.*?ЖУРНАЛ:\s*(\d+(?:[.,]\d+)?)'  # "ПРЕДВАРИТЕЛЬНАЯ ОЦЕНКА В ЖУРНАЛ: 10.0"
    - 'ИТОГО:\s*(\d+(?:[.,]\d+)?)\s*баллов'                     # "ИТОГО: 100 баллов"
    - '##\[notice\]Points\s+(\d+(?:[.,]\d+)?)/\d+'              # "##[notice]Points 10/10"
    - 'Score\s+is\s+(\d+(?:[.,]\d+)?)'                          # "Score is 10.5"
    - 'Total:\s+(\d+(?:[.,]\d+)?)'                              # "Total: 10"
```

**Формат вывода в Google Sheets:**
- `v@10.5` - балл с десятичным разделителем (автоопределение из locale таблицы)
- `v@10.5-3` - балл со штрафом
- `v-3` - только штраф (если score не настроен)
- `v` - просто зачёт (без score и штрафа)

---

## Проверка файлов

### `files`
**Тип:** `list[string]`
**Описание:** Список обязательных файлов в репозитории студента
**Пример:**
```yaml
files:
  - lab1.sh
  - README.md
```

### `forbidden-modifications`
**Тип:** `list[string]`
**Описание:** Список файлов/директорий, которые студент не может изменять
**По умолчанию:** Если не указано, автоматически запрещается изменение `test_main.py` и `tests/` (если они в `files`)
**Пример:**
```yaml
forbidden-modifications:
  - test_main.py
  - tests/
  - .github/workflows/
```

---

## MOSS (Проверка плагиата)

### `moss.language` (обязательно)
**Тип:** `string`
**Описание:** Язык программирования для проверки MOSS
**Возможные значения:** `c`, `cc`, `python`, `java`, и др.
**Пример:**
```yaml
moss:
  language: cc
```

### `moss.max-matches`
**Тип:** `integer`
**По умолчанию:** `250`
**Описание:** Максимальное количество совпадений для отображения
**Пример:**
```yaml
moss:
  max-matches: 1000
```

### `moss.local-path`
**Тип:** `string`
**Описание:** Путь к директории с файлами в репозитории (если файлы не в корне)
**Пример:**
```yaml
moss:
  local-path: lab3
```

### `moss.additional`
**Тип:** `list[string]`
**Описание:** Список дополнительных GitHub организаций для проверки (старые годы обучения)
**Пример:**
```yaml
moss:
  additional:
    - suai-os-2023
    - suai-os-2024
```

### `moss.basefiles`
**Тип:** `list[dict]`
**Описание:** Базовые файлы (шаблоны), которые исключаются из проверки на плагиат
**Пример:**
```yaml
moss:
  basefiles:
    - repo: teacher/template-repo
      filename: lab3.cpp
    - repo: teacher/template-repo
      filename: examples/helper.cpp
```

---

## Требования к отчёту

### `report`
**Тип:** `list[string]`
**Описание:** Обязательные разделы в отчёте (для проверки на frontend)
**Пример:**
```yaml
report:
  - Цель работы
  - Индивидуальное задание
  - Результат выполнения работы
  - Исходный код программы с комментариями
  - Выводы
```

---

## Валидация (дополнительные проверки)

### `validation.commits`
**Тип:** `list[dict]`
**Описание:** Правила проверки коммитов

**Параметры правила:**
- `filter`: `"message"` (сообщение коммита) или `"console"` (изменённые файлы)
- `contains`: строка для поиска (опционально)
- `min-count`: минимальное количество коммитов

**Пример:**
```yaml
validation:
  commits:
    - filter: message
      contains: lab5
      min-count: 3  # Минимум 3 коммита с упоминанием "lab5"
    - filter: console
      min-count: 1  # Минимум 1 коммит с файлом "console"
```

### `validation.issues`
**Тип:** `list[dict]`
**Описание:** Правила проверки issues

**Параметры правила:**
- `filter`: `"message"` (заголовок/тело issue)
- `contains`: строка для поиска
- `min-count`: минимальное количество issues

**Пример:**
```yaml
validation:
  issues:
    - filter: message
      contains: lab6
      min-count: 3  # Минимум 3 issues с упоминанием "lab6"
```

---

## Секция `misc`

Дополнительные настройки системы.

### `requests-timeout`
**Тип:** `integer`
**По умолчанию:** `5`
**Описание:** Таймаут для HTTP запросов к GitHub API (в секундах)
**Пример:**
```yaml
misc:
  requests-timeout: 10
```

---

## Полный пример конфигурации

```yaml
---
course:
  name: Операционные системы
  alt-names:
    - OS
    - ОС
  semester: Осень 2025
  university: ГУАП
  email: teacher@university.edu
  timezone: UTC+3
  github:
    organization: suai-os-2025
    teachers:
      - "Mark Polyak"
      - markpolyak
  google:
    spreadsheet: 1BoVLNZpP6zSc7DPSVkbxaQ_oJZcZ_f-zyMCfF1gw-PM
    info-sheet: График
    task-id-column: 0
    student-name-column: 1
    lab-column-offset: 1
  staff:
    - name: Поляк Марк Дмитриевич
      title: ст. преп.
      status: лектор

  labs:
    "1":
      github-prefix: os-task1
      short-name: ЛР1
      taskid-max: 25
      taskid-shift: 0
      penalty-max: 6
      penalty-strategy: weekly
      ci:
        workflows:
          - run-autograding-tests
          - cpplint
      files:
        - lab1.sh
      forbidden-modifications:
        - test_main.py
        - tests/
      score:
        patterns:
          - 'Score:\s*(\d+(?:[.,]\d+)?)'
          - 'Total:\s*(\d+(?:[.,]\d+)?)\s*points'
      moss:
        language: c
        max-matches: 1000
        local-path: lab1
        additional:
          - suai-os-2024
        basefiles:
          - repo: teacher/templates
            filename: lab1.sh
      report:
        - Цель работы
        - Индивидуальное задание
        - Результат выполнения работы
        - Исходный код программы
        - Выводы
      validation:
        commits:
          - filter: message
            contains: lab1
            min-count: 2

misc:
  requests-timeout: 5
```

---

## Примечания

1. **Кодировка:** Все YAML файлы должны быть в UTF-8
2. **Regex паттерны:** В одинарных кавычках YAML backslash литеральный, экранирование не требуется
3. **Индексация колонок:** В конфиге 0-based (A=0, B=1), система автоматически конвертирует для Google Sheets API
4. **Локаль Google Sheets:** Десятичный разделитель для score автоматически определяется из настроек таблицы
5. **GitHub API encoding:** Система автоматически обрабатывает UTF-8 логи с Cyrillic символами
