# Что добавлено в lab_grader_web (антиплагиат) — разбор для преподавателя

Документ описывает изменения относительно исходного репозитория (где секция `moss:` в YAML была, но **в коде не работала**).  
Ниже: **что добавлено / где лежит / зачем / как показать руками**.

---

## Кратко: что изменилось в системе

| Было | Стало |
|------|--------|
| Плагиат вручную через MOSS в конце семестра | Автопроверка **в момент сдачи** (`v` в таблицу) |
| Конфиг `moss:` «мёртвый» | Работает как `plagiarism:` / алиас `moss:` |
| Нет локального корпуса | Кэш `plagiarism_cache/` + БД `plagiarism.db` |
| Нет UI по совпадениям | Админка: список пар + кнопка «отметить» |
| — | Note (заметка) на ячейке оценки в Google Sheets |

**Оценка студенту алгоритмом не меняется** — только флаг преподавателю.

---

## 1. Новые модули backend (`grading/`)

### 1.1. `grading/plagiarism_cache.py`
- **Зачем:** скачать из GitHub только нужные файлы лабы и хранить локально.
- **Что делает:** кэш `plagiarism_cache/{курс}/{лаба}/{org}/{repo}/`, конвертация `.ipynb` → `.py`.

**Как показать:**  
1. Открой в проводнике / IDE папку `plagiarism_cache/` (после хотя бы одной проверки).  
2. Зайди в `os-2025-spring` → номер лабы → `suai-os-2025` → репозиторий студента → файлы (`lab2.cpp` и т.п.).

### 1.2. `grading/plagiarism.py`
- **Зачем:** вызвать compare50 и получить пары с similarity 0…1.
- **Что делает:** инкрементальное сравнение «новая работа vs корпус», `basefiles`, порог из YAML.

**Как показать:**  
1. Открой файл в IDE, найди `compare_submission_against_cache` / `run_compare50`.  
2. Либо в консоли: `python scripts/menu.py` → пункт **5** (локальный demo) — увидишь совпадения без Sheets.

### 1.3. `grading/plagiarism_store.py`
- **Зачем:** сохранить пары в SQLite и флаг `reviewed_by_teacher`.
- **Файл БД:** рядом с кэшем — `plagiarism.db` (или путь из `PLAGIARISM_DB_PATH`).

**Как показать:**  
1. `python scripts/menu.py` → **3) Показать совпадения из SQLite**.  
2. Выбери курс и лабу → в консоли список пар и `[✓]` / пусто у review.

### 1.4. `grading/plagiarism_check.py`
- **Зачем:** оркестрация всего фона после `v`.
- **Цепочка:** конфиг → кэш → compare50 → БД → (если не SHADOW) note в Sheets.

**Как показать:**  
1. В `.env` поставь `PLAGIARISM_SHADOW_MODE=false`.  
2. Очисти/поставь `?` в ячейке студента на листе группы.  
3. `python scripts/menu.py` → **1) Оценить лабы студента** → курс / группа / github / лабы.  
4. В логе `logs/labgrader.log` ищи `Plagiarism check for ... above threshold`.  
5. В Google Sheets наведи на ячейку оценки → должна быть **заметка (note)**.

### 1.5. `grading/sheets_comments.py`
- **Зачем:** текст и запись **note** на ячейку (не discussion comment — API Google так не умеет для Sheets).
- **Важно:** оценку не переписывает.

**Как показать:** Google Таблица курса → лист группы (напр. `4333K`) → ячейка с `v` у студента, у которого было совпадение ≥ порога → треугольник / note при наведении.

### 1.6. Дополнение `grading/github_client.py`
- **Зачем:** метод `get_file_content` для скачивания одного файла через GitHub API (нужен кэшу).

**Как показать:** открой `grading/github_client.py`, найди `get_file_content`.

---

## 2. Изменения в `main.py` (точка встраивания)

| Что добавлено | Зачем |
|---------------|--------|
| После успешного `v...` — `background_tasks.add_task(run_plagiarism_check, ...)` | Проверка не блокирует ответ студенту |
| `GET /courses/{course_id}/labs/{lab_id}/plagiarism` | Список пар для админки |
| `POST /courses/{course_id}/labs/{lab_id}/plagiarism/review` | Флаг «я проверил» → `reviewed_by_teacher` в БД |
| В `GET /courses/{id}` поле `has_plagiarism` у лаб | Админка знает, где настроен антиплагиат |

**Как показать API (Swagger):**  
1. Запусти backend: `python scripts/menu.py` → **8** (или `python -m uvicorn main:app --reload --port 8000`).  
2. Открой в браузере: http://127.0.0.1:8000/docs  
3. Найди эндпоинты `.../plagiarism` и `.../plagiarism/review`.

---

## 3. Frontend — админ-UI

### Новые файлы
- `frontend/courses-front/src/components/plagiarism-matches/index.jsx` — таблица пар  
- `frontend/courses-front/src/components/plagiarism-matches/adminLabList.jsx` — список лаб курса  
- `frontend/courses-front/src/components/plagiarism-matches/styled.js`  
- правки `App.jsx` (маршруты)  
- строки в `locales/ru|en|zh/translation.json`

### Маршруты
1. `/admin` — логин  
2. `/admin/courses` — выбор курса  
3. `/admin/courses/:courseId/labs` — лабы  
4. `/admin/courses/:courseId/labs/:labId/plagiarism` — **совпадения**

**Как показать преподавателю (пошагово):**

1. В консоли: `python scripts/menu.py` → **8) Запустить админ-панель**.  
2. Дождись вывода URL, например: `http://127.0.0.1:5173/admin` (порт может быть 5173–518x).  
3. Открой этот URL (меню само предложит браузер).  
4. Введи `ADMIN_LOGIN` / `ADMIN_PASSWORD` из `.env` → **Login**.  
5. Нажми курс (например, с `os-2025-spring` / ОС).  
6. Нажми лабораторную, где написано «проверка настроена».  
7. Увидишь таблицу: студенты, %, reviewed.  
8. Нажми **«Отметить»** у пары → в БД `reviewed_by_teacher=1` (в UI станет «да»).  
   Это **только чекбокс в БД**, в Google Sheets и студенту ничего не уходит.

---

## 4. Скрипты (`scripts/`)

| Файл | Зачем | Как запустить / показать |
|------|--------|---------------------------|
| `menu.py` | Единое консольное меню | `python scripts/menu.py` |
| `grade_student_labs.py` | Прогнать сдачу + фон плагиата | Меню → **1** |
| `plagiarism_batch_course.py` | Batch по курсу без Sheets | Меню → **2** |
| `plagiarism_local_demo.py` | Демо без Google | Меню → **5** |
| `compare_engines_jplag_compare50.py` | Сравнение с JPlag | Меню → **4** (нужен `tools/jplag.jar`) |
| `experiment_sheet_cell_comment.py` | Исследование якоря Drive comments | опционально, для защиты «почему notes» |

---

## 5. Конфиг, Docker, зависимости, доки

| Файл | Что сделано | Зачем |
|------|-------------|--------|
| `requirements.txt` | `compare50>=1.2.0` | Движок плагиата |
| `docker-compose.example.yaml` | volume `plagiarism_cache`, env `PLAGIARISM_*` | Данные не пропадают в Docker |
| `.env.example` | `PLAGIARISM_SHADOW_MODE`, пути кэша/БД | Пилот без notes / настройка путей |
| `.gitignore` | `plagiarism_cache/`, `plagiarism.db`, `.labgrader_cli.json`, `tools/*.jar` | Не коммитить кэш и секреты состояния |
| `docs/COURSE_CONFIG.md` | Раздел plagiarism / notes | Как писать YAML |
| `docs/PROJECT_DESCRIPTION.md` | Описание фичи | Обзор проекта |
| `docs/PLAGIARISM_DETECTION_PLAN.md` | План + решения (notes, compare50 vs JPlag) | Исходная постановка и итоги исследования |
| `docs/PRACTICE_REPORT_PLAGIARISM.md` | Текст отчёта по практике | Для сдачи отчёта |
| `CLAUDE.md` | Команда `python scripts/menu.py` | Быстрый вход для разработки |
| YAML курсов | уже был `moss:` — теперь **читается кодом** как алиас | Не ломает старые конфиги |

**Как показать конфиг:**  
Открой `courses/operating-systems-2025.yaml` (или актуальный курс) → секция `moss:` / `plagiarism:` у лабы (language, additional, threshold…).

**Как показать shadow-режим:**  
`.env` → `PLAGIARISM_SHADOW_MODE=true` → оценка пройдёт, БД заполнится, **note в таблице не появится**. Меню → **7** — подсказка.

---

## 6. Тесты (`tests/`)

| Файл | Что покрывает |
|------|----------------|
| `tests/test_plagiarism_cache.py` | кэш, ipynb |
| `tests/test_plagiarism.py` | compare50 на фикстурах |
| `tests/test_plagiarism_store.py` | SQLite + review |
| `tests/test_sheets_comments.py` | формат текста note / A1 |

**Как показать:**  
```powershell
cd C:\Users\sonchikeslicho\Desktop\lab_grader_web-main
python -m pytest tests/test_plagiarism_cache.py tests/test_plagiarism.py tests/test_plagiarism_store.py tests/test_sheets_comments.py -v
```

---

## 7. Рекомендуемый сценарий демонстрации (15–20 минут)

1. **Консоль:** `python scripts/menu.py` → **8** → скопировать URL админки из вывода.  
2. **Браузер:** открыть `/admin` → логин → курс → лаба → список пар → «Отметить».  
3. **Swagger:** http://127.0.0.1:8000/docs → показать endpoints plagiarism.  
4. **Проводник:** папка `plagiarism_cache/` + файл `plagiarism.db`.  
5. **Google Sheets:** note на ячейке (если уже был прогон с `SHADOW=false`).  
6. **IDE:** коротко пройти файлы `grading/plagiarism_*.py` и место в `main.py` с `BackgroundTasks`.  
7. **Консоль:** меню **3** — те же пары из БД; меню **4** (по желанию) — цифры compare50 vs JPlag из отчёта.

---

## 8. Чего в изменениях нет (чтобы не ждали)

- Автозамены `v` → `x` при плагиате  
- Детекции ИИ-кода  
- Настоящих discussion-комментариев Google к ячейке (технически недоступны через API)  
- Движка JPlag в Docker-проде (только исследование; в проде — compare50)

---

## 9. Шпаргалка команд

```powershell
# Меню всего
python scripts/menu.py

# Админка = пункт 8 в меню
# URL смотри в выводе, обычно:
#   http://127.0.0.1:5173/admin   (порт может отличаться)
#   http://127.0.0.1:8000/docs

# Тесты антиплагиата
python -m pytest tests/test_plagiarism*.py tests/test_sheets_comments.py -v
```

Логин админки: переменные `ADMIN_LOGIN` / `ADMIN_PASSWORD` в `.env`.
