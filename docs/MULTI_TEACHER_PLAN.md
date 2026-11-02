# План разработки: несколько преподавателей в админке

Требования и план реализации фичи.

## 1. Постановка задачи

Курсы в сервисе ведут разные преподаватели. Сейчас в админку входят по одной паре `ADMIN_LOGIN`/`ADMIN_PASSWORD`, и вошедший видит и меняет все курсы: конфигурации, массовую проверку, рассылку обновлений шаблона.

Нужно:

- у каждого преподавателя свой вход;
- преподаватель видит и меняет только свои курсы — и в интерфейсе, и при прямом обращении к API;
- один или несколько суперадминистраторов видят всё;
- пароли и учётные записи не хранятся и не администрируются вручную.

## 2. Как устроено сейчас

- `POST /admin/login` (`main.py`) сравнивает логин и пароль с `ADMIN_LOGIN`/`ADMIN_PASSWORD` и ставит cookie `admin_session` — подписанную `signer` строку логина, `max_age=3600`.
- `require_admin` проверяет подпись cookie и равенство логина `ADMIN_LOGIN`; все административные маршруты подключают её через `Depends`.
- Админский список курсов строится фронтендом из публичного `GET /courses?status=...` (`components/course-list`, `isAdmin`).
- Вход студента через GitHub уже есть (`docs/REPO_GENERATION_PLAN.md` §3): `/join/{c}/{l}/start` → github.com → `/join/callback`. Подтверждённый логин получает только `_exchange_code_for_username` из ответа `GET /user`; `state` подписан (`_build_join_state`/`_parse_join_state`) и несёт `course_id`/`lab_id`.
- В конфигурации курса есть `course.github.teachers` — смешанный список имён и GitHub-логинов преподавателей (`_course_teachers`). Сейчас он исключает преподавателей из состава команд (`grading/teams.py`, `grading/bulk.py`).
- Фоновые работы хранят курс: `PropagateJob.course_id` (`grading/propagate.py`), `BulkJob.course_id` (`grading/bulk.py`).

## 3. Архитектурное решение

### 3.1. Личность — GitHub OAuth

Преподаватель входит через тот же OAuth App, что и студенты в `/join`. Логин берётся только из `_exchange_code_for_username`, никогда из запроса. Сервис не хранит паролей, не выдаёт их и не сбрасывает.

### 3.2. Права — из конфигурации курса

Преподаватель курса — пользователь, чей GitHub-логин совпадает (без учёта регистра) с одной из записей `course.github.teachers`. Записи-имена (`"Mark Polyak"`) логином не совпадут и прав не дают. Список правится в YAML курса, как и всё остальное о курсе.

### 3.3. Суперадминистратор

- Логины из переменной окружения `SUPERADMIN_GITHUB_LOGINS` (через запятую, без учёта регистра) — доступ ко всем курсам.
- Вход по `ADMIN_LOGIN`/`ADMIN_PASSWORD` сохраняется как аварийный (GitHub недоступен, OAuth App не настроен) и даёт права суперадминистратора.

### 3.4. Отклонённые варианты

- **Логины и хеши паролей в файле на сервере.** Заводить, менять и сбрасывать пароли пришлось бы вручную; появляется секрет, который надо хранить и ротировать.
- **Отдельное поле `course.admins`.** Дублирует `teachers`: человек, который ведёт курс, и есть тот, кто им управляет. Разделение ролей внутри курса — отдельная задача (§11).
- **Права по членству в GitHub-организации или команде.** Требует scope `read:org` от преподавателя и согласованной структуры команд в каждой организации курса; права становятся невидимы из конфигурации курса.

## 4. Вход через GitHub

### 4.1. `state` с назначением

OAuth App допускает только зарегистрированный callback URL или его подкаталоги, поэтому вход преподавателя использует тот же `/join/callback` (`_oauth_redirect_uri`), а различается назначением в подписанном `state`.

- `_build_join_state` обобщается до `_build_oauth_state(purpose, **payload)`; `purpose` — `"join"` или `"admin"`.
- `_parse_oauth_state` возвращает `purpose` и данные. `state` без `purpose` трактуется как `"join"`: студенты, ушедшие на github.com до деплоя, возвращаются без ошибки (срок жизни `state` — `JOIN_STATE_MAX_AGE`).
- `/join/callback` после разбора `state` с `purpose == "admin"` передаёт управление `_admin_oauth_callback`; ветка `join` не меняется.

### 4.2. Сценарий

1. `GET /admin/login/github` → 302 на github.com (scope `read:user`, `state` с `purpose: "admin"`). Без `GITHUB_OAUTH_CLIENT_ID`/`SECRET` — 503 с сообщением, как у `/join/.../start`.
2. github.com → `/join/callback?code&state`.
3. `_exchange_code_for_username` → логин. Ошибка обмена → редирект на `{FRONTEND_URL}/admin?error=oauth_failed`.
4. Роль: `superadmin`, если логин в `SUPERADMIN_GITHUB_LOGINS`; иначе `teacher`, если логин есть в `teachers` хотя бы одного курса индекса; иначе вход отклоняется — редирект на `{FRONTEND_URL}/admin?error=not_a_teacher`.
5. Cookie `admin_session` (§5) и редирект на `{FRONTEND_URL}/admin/courses`.

Принадлежность к курсам на шаге 4 проверяется только чтобы не пускать посторонних; права на конкретный курс вычисляются на каждый запрос (§6), поэтому правка `teachers` действует сразу, без перевхода.

## 5. Сессия

- `admin_session` — подписанный `signer` JSON `{"login": ..., "role": "superadmin"|"teacher", "via": "github"|"password"}`, кодирование как у `state` (base64 + подпись). `max_age=3600`, `httponly`, `path="/"` — как сейчас.
- `POST /admin/login` (аварийный вход) ставит ту же cookie с `role: "superadmin"`, `via: "password"`.
- Cookie старого формата (строка логина) не разбирается — повторный вход. Отдельной миграции не нужно.
- `POST /admin/logout` не меняется.

## 6. Проверка прав

- `AdminIdentity` (dataclass: `login`, `role`, `via`).
- `require_admin(request) -> AdminIdentity`: проверяет подпись и срок cookie; для `role == "superadmin"` и `via == "github"` дополнительно сверяет логин с текущим `SUPERADMIN_GITHUB_LOGINS` (снятие права действует без ожидания истечения cookie).
- `is_course_teacher(course_info, login) -> bool`: сравнение с `_course_teachers(course_info)` без учёта регистра.
- `require_course_access(course_id)` — зависимость поверх `require_admin`: суперадмин — всегда; преподаватель — если `is_course_teacher`. Иначе `404 "Курс не найден"` — тот же ответ, что для несуществующего курса: существование чужого курса не подтверждается.
- Доступ к фоновой работе: `can_access_course(identity, job.course_id)`; нет доступа — тот же 404, что для неизвестного `job_id`.
- Скрытые (`status: hidden`) и архивные курсы преподаватель в админке видит наравне с активными.

## 7. Эндпоинты

### 7.1. Новые

| Метод | Путь | Описание |
|---|---|---|
| GET | `/admin/login/github` | Начало входа через GitHub (§4.2) |
| GET | `/admin/courses` | Курсы, доступные вошедшему, всех статусов; элементы — как у `GET /courses`, плюс `status` и `priority` |

### 7.2. Изменения в существующих

| Маршрут | Было | Стало |
|---|---|---|
| `GET /admin/check-auth` | `{"authenticated": true}` | `{"authenticated": true, "login": ..., "role": ..., "via": ...}` |
| `GET /courses/{id}/edit`, `PUT /courses/{id}/edit` | `require_admin` | `require_course_access` |
| `DELETE /courses/{id}` | `require_admin` | `require_course_access` |
| `GET /admin/courses/{id}/labs` | `require_admin` | `require_course_access` |
| `POST /admin/courses/{id}/labs/{lab}/propagate-template-update` | `require_admin` | `require_course_access` |
| `POST /admin/courses/{id}/groups/{g}/labs/{lab}/bulk-grade` | `require_admin` | `require_course_access` |
| `GET /admin/propagate-jobs/{job_id}` | `require_admin` | + доступ к `job.course_id` |
| `GET /admin/bulk-grade-jobs/{job_id}`, `POST .../cancel` | `require_admin` | + доступ к `job.course_id` |
| `POST /courses/upload` | `require_admin` | преподаватель — только если его логин есть в `teachers` загружаемого YAML, иначе 400 «Добавьте свой GitHub-логин в course.github.teachers» |

Дополнительно для `PUT /courses/{id}/edit`: если вошедший — преподаватель (не суперадмин) и в новой версии его логина нет в `teachers`, сохранение отклоняется с 400 «Нельзя удалить себя из преподавателей курса». Проверка выполняется до записи, рядом с `_validate_join_sections`.

Публичные маршруты (`GET /courses`, `/join/...`, проверка работы) не меняются.

## 8. Конфигурация и окружение

- `docs/COURSE_CONFIG.md`, `course.github.teachers`: GitHub-логины из списка дают доступ к курсу в админке; записи-имена допустимы и прав не дают.
- Новая переменная `SUPERADMIN_GITHUB_LOGINS` — в `.env.example`, `docs/DEPLOYMENT.md`, `docs/DEVELOPMENT.md`, `CLAUDE.md`.
- `GITHUB_OAUTH_CLIENT_ID`/`SECRET` теперь нужны и для входа преподавателей; без них работает только аварийный вход.

## 9. Frontend

- `components/admin/AdminLogin.jsx`: основная кнопка «Войти через GitHub» (переход на `/api/v1/admin/login/github`, не `fetch`); форма логина и пароля — свёрнутый блок «Аварийный вход». Показ ошибки из `?error=` (`oauth_failed`, `not_a_teacher`).
- `ProtectedRoute.jsx`: данные `check-auth` (логин, роль) передаются в админку; в шапке админки — логин и роль, кнопка выхода.
- `components/course-list` в режиме `isAdmin` берёт список из `/api/v1/admin/courses`.
- Ответ 404 на маршрутах курса в админке — сообщение «Курс не найден или нет доступа».
- Переводы ru/en/zh.

## 10. Журналирование

- Вход: логин, роль, способ (`github`/`password`); отказ во входе с причиной.
- Отказ в доступе к курсу или работе: логин и `course_id`/`job_id`.
- OAuth `code`, токены и содержимое cookie не журналируются.

## 11. Не входит в объём

- Роли внутри курса (ассистент без права правки конфигурации и т. п.).
- Интерфейс управления списком преподавателей — список правится в YAML курса.
- Изменение полей `index.yaml` (`status`, `priority`, `logo`) из админки, кроме существующего скрытия курса.
- Хранение конфигураций вне репозитория — отдельная задача (`docs/COURSE_STORAGE_PLAN.md`); она использует `AdminIdentity.login` как автора изменения.

## 12. Тесты

Новый файл `tests/test_admin_access.py` и дополнения к `tests/test_admin_endpoints.py`, `tests/test_join_endpoints.py`:

- `_build_oauth_state`/`_parse_oauth_state`: назначение сохраняется; `state` без `purpose` разбирается как `join`; подделанный и просроченный `state` отклоняются;
- `/join/callback` с `purpose: "admin"`: суперадмин, преподаватель, посторонний (редирект с `not_a_teacher`), сбой обмена `code`; ветка `join` ведёт себя как прежде (существующие тесты проходят без изменений);
- `require_admin`: cookie нового формата, старого формата (отказ), просроченная, суперадмин, исключённый из `SUPERADMIN_GITHUB_LOGINS` после входа (отказ);
- `is_course_teacher`: регистр, записи-имена, пустой и отсутствующий список;
- каждый маршрут из §7.2: свой курс — 200, чужой — 404 с тем же телом, что у несуществующего курса, суперадмин — 200;
- фоновые работы чужого курса: статус и отмена — 404;
- `GET /admin/courses`: преподаватель видит только свои курсы, включая скрытые; суперадмин — все;
- загрузка курса без себя в `teachers` — 400; сохранение курса без себя в `teachers` — 400 для преподавателя, 200 для суперадмина;
- аварийный вход по паролю даёт права суперадмина.

## 13. Чек-лист приёмки

**Этап 1. Вход**
- [ ] Преподаватель входит через GitHub и попадает в список своих курсов.
- [ ] Пользователь, которого нет ни в одном `teachers` и в `SUPERADMIN_GITHUB_LOGINS`, видит понятный отказ.
- [ ] Аварийный вход по `ADMIN_LOGIN`/`ADMIN_PASSWORD` работает и даёт полный доступ.
- [ ] Студенческий сценарий `/join` не изменился (включая командные лабы и секретные ссылки).

**Этап 2. Права**
- [ ] Прямые запросы к API чужого курса и чужих фоновых работ — 404.
- [ ] Правка `teachers` действует без перевхода.
- [ ] Нельзя загрузить курс без себя и удалить себя из `teachers` при сохранении (преподаватель).

**Этап 3. Frontend и документация**
- [ ] Кнопка входа через GitHub, свёрнутый аварийный вход, логин и роль в шапке, переводы ru/en/zh.
- [ ] `SUPERADMIN_GITHUB_LOGINS` описана в `.env.example`, `docs/DEPLOYMENT.md`, `docs/DEVELOPMENT.md`.
- [ ] `docs/COURSE_CONFIG.md` описывает новое значение `teachers`; `docs/PROJECT_DESCRIPTION.md` — вход преподавателей; `CLAUDE.md` — раздел о правах в админке.
- [ ] Тесты из §12 проходят.
