# Отправка решения sensei-mcp

Цель PR: **`markpolyak/lab_grader_web` → ветка `hackathon/queue`**.
Требования: [issue #62](https://github.com/markpolyak/lab_grader_web/issues/62).
Команда по регистрации — **sensei-mcp**, не имя приватного репозитория.
Два ADR находятся в этом каталоге; [текст PR](PR_DESCRIPTION.md) готов к вставке.

## 1. Создать настоящий публичный форк исходного репозитория

Открыть https://github.com/markpolyak/lab_grader_web/fork и создать форк на своём
аккаунте, например `colosss/lab_grader_web`. Это должен быть форк **исходного**
репозитория, а не новый импорт приватного `lab-grader-sensei-mcp`.
Копирования только default branch достаточно: ветку решения отправим из
существующего локального checkout с сохранённой общей историей.

Не менять публичность приватного origin. Не использовать `push --mirror`:
для сдачи требуется одна выбранная ветка, а не все служебные refs.

## 2. Сохранить локальные исправления и документы

Сейчас код исправлений находится в рабочем дереве: одного старого HEAD недостаточно.
Из корня текущего checkout, в ветке `feature/queue`:

```bash
git status --short --branch
git add -u
git add docs/hackathon/sensei-mcp \
  queues/tests/test_queue_persistence_failures.py \
  queues/tests/test_retained_queues.py \
  tests/test_course_config_concurrency.py
git diff --cached --name-status
git diff --cached --check
```

`git add -u` включает изменения отслеживаемых файлов, в том числе удаления старых
путей ADR; новые документы и regression-тесты перечислены отдельно. Просмотреть
список перед commit. Не добавлять `credentials.json`, `.env`, runtime YAML из
queue_state, локальные аудиты `errors.md`/`AUDIT_ISSUES.md` или настоящие ключи.
`git add .` для этого шага не нужен.

```bash
git commit -m "fix(queue): preserve history and safely synchronize persisted state"
```

Если какие-то из этих изменений уже закоммичены, не создавать пустой commit:
проверить `git status` и коммитить только оставшееся. Код и документы должны
оказаться в HEAD до push. При необходимости отдельно сохранить ветку в приватном
origin — это не замена отправке в публичный форк.

## 3. Добавить отдельный remote и отправить одну ветку

Ниже предполагается форк `colosss/lab_grader_web`. Если он создан на другом
аккаунте или с другим именем, заменить URL и имя владельца в compare-ссылке.
Существующие origin/upstream не перенастраивать.

```bash
git remote -v
git remote add submission git@github.com:colosss/lab_grader_web.git
git fetch upstream hackathon/queue
git merge-base --is-ancestor upstream/hackathon/queue HEAD
git diff --stat upstream/hackathon/queue...HEAD
git ls-files -- credentials.json .env queue_state
git log HEAD --format='%h %s' -- credentials.json \
  docs/hackathon/lab-grader-sensei-mcp/AUDIT_ISSUES.md
```

Если remote submission уже существует, сначала проверить его URL, не добавлять
дубликат. Проверка merge-base должна завершиться с кодом 0: история решения
уже основана на целевой ветке. Последние две команды для этой рабочей копии
не должны выводить секретные файлы/случайный аудит в истории выбранного HEAD.
Проверить также новые staged-файлы, а не только HEAD.

```bash
git push submission HEAD:refs/heads/hackathon/sensei-mcp
```

Эта команда публикует текущий HEAD в одной ветке публичного форка и не меняет
tracking приватной feature/queue. Не использовать --force. Если имя ветки в
форке уже занято чужим/другим решением, выбрать новое свободное имя и указать его
при создании PR.

## 4. Открыть PR на GitHub

На исходном репозитории: Pull requests → New pull request → compare across forks.
Выбрать явно:

| Поле | Значение |
| --- | --- |
| base repository | `markpolyak/lab_grader_web` |
| base branch | **`hackathon/queue`**, не main |
| head repository | публичный форк, например `colosss/lab_grader_web` |
| compare branch | `hackathon/sensei-mcp` |

Удобная compare-ссылка для указанного владельца:

https://github.com/markpolyak/lab_grader_web/compare/hackathon/queue...colosss:lab_grader_web:hackathon/sensei-mcp?expand=1

Заголовок: **Очередь на защиту лабораторных — sensei-mcp**.
Вставить содержимое PR_DESCRIPTION.md. Ссылки на ADR и запуск при необходимости
оформить как полные URL файлов вашей опубликованной ветки, если preview GitHub
не разрешает относительные ссылки в теле PR.

Перед Create pull request посмотреть Files changed: два ADR в sensei-mcp,
новые regression-тесты и исправления присутствуют, секретов и локального аудита
нет. Открыть обычный PR для ревью; не нажимать Merge. Проверить, что PR
отображается именно в исходном репозитории, и сохранить его URL.

Справка GitHub: [PR из форка](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request-from-a-fork).

## 5. Проверки и перекрёстное ревью

В PR открыть Checks: тесты запускаются workflow исходного проекта. Для первого
внешнего PR GitHub может ожидать разрешения сопровождающего на запуск — это
не эквивалент проваленного теста. Не добавлять Google JSON или личный PAT в
публичный форк ради запуска pytest: unit-тесты используют фиктивное окружение.

После назначения другой команды изучить её PR, проверить сценарий и оставить
конкретные замечания с файлом, ожидаемым/фактическим поведением и воспроизведением.
По заданию ревью проходит 09–11.11; сейчас важно открыть свой PR в срок.
Назначение и факт ревью не отмечать выполненными заранее.

## Финальный список

- [ ] Ветка включает незакоммиченные до этого исправления.
- [ ] В публичный форк отправлена правильная ветка, без --mirror/--force.
- [ ] PR направлен в `markpolyak/lab_grader_web:hackathon/queue`.
- [ ] В описании четыре участника и принятые допущения.
- [ ] Два ADR в `docs/hackathon/sensei-mcp/` соответствуют финальной реализации.
- [ ] Ограничения и частичное выполнение дополнения 06.11 описаны честно.
- [ ] Проверены Files changed, отсутствие ключей, состояние Checks и URL PR.
- [ ] После назначения выполнено ревью другой команды.
