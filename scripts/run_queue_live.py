#!/usr/bin/env python3
"""Локальная очередь на публичных данных, без Google/GitHub-ключей и записи оценок."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
COURSE_ID, GROUP_ID = 'queue-live-os', '4434'
SPREADSHEET = '1mwFswSqBZHQjvfU7JtiT_f5yQMCCgjyGYrhjxLgBSus'


def local_directory(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to((ROOT / 'data').resolve()):
        raise ValueError('Данные локальной проверки должны находиться внутри data/ (он исключён из Git)')
    path.mkdir(parents=True, exist_ok=True)
    return path


def base_course():
    import yaml
    original = yaml.safe_load((ROOT / 'courses/operating-systems-2026.yaml').read_text())['course']
    course = deepcopy(original)
    course['name'] = 'ОС — локальная проверка публичных данных'
    course['queue'] = {
        'enabled': True, 'timezone': 'Europe/Moscow', 'registration_disabled': True,
        'semester_week_anchor': '2026-08-31',
        'sheet_source': {'type': 'public_xlsx'},
        'no_deadline_markers': ['-'],
        'required_labs': [key for key in course['labs'] if key not in ('0', '01')],
        'schedule': {},
    }
    course['google']['spreadsheet'] = SPREADSHEET
    return course


def select_sheet_labs(course, grid):
    """Локальный курс содержит только работы, присутствующие в предоставленном листе."""
    course = deepcopy(course)
    header = grid[1]
    course['labs'] = {key: lab for key, lab in course['labs'].items() if lab.get('short-name') in header}
    if not course['labs']:
        raise ValueError('Не найдены столбцы работ из конфигурации. Нужна проверка структуры листа')
    course['queue']['required_labs'] = [key for key in course['queue'].get('required_labs', course['labs']) if key in course['labs']]
    return course


def allowed_path(path, method, groups=(GROUP_ID,)):
    public = f'/courses/{COURSE_ID}'
    queue_paths = [prefix for group in groups for prefix in (f'{public}/groups/{group}/queue', f'/admin/courses/{COURSE_ID}/groups/{group}/queue')]
    settings = f'/admin/courses/{COURSE_ID}/queue/settings'
    queue_path = any(path == prefix or path.startswith(prefix + '/') for prefix in queue_paths) or path == settings
    if method == 'OPTIONS':
        return True
    if method in ('GET', 'HEAD'):
        return queue_path or path in [public+f'/groups/{group}/labs' for group in groups] or path in ('/courses', public, public+'/groups',
                                      '/admin/check-auth', '/admin/queue/sessions', '/docs', '/openapi.json', '/redoc')
    return path == '/admin/login' or queue_path


def inspect_sources(course, *, fetch, now):
    from grading.defense_queue.public_sheet import read_public_workbook, list_public_groups
    from grading.defense_queue.schedule_sources.suai import load, group_directory
    from grading.defense_queue.schedule import normalize_lessons
    from grading.defense_queue.course_requirements import scored_labs
    workbook = read_public_workbook(course)
    groups = list_public_groups(course)
    groups = ([GROUP_ID] if GROUP_ID in groups else []) + [group for group in groups if group != GROUP_ID]
    directory = {name.casefold(): value for name, value in group_directory(fetch('https://guap.ru/rasp/')).items()}
    graph = workbook.get(course.get('google', {}).get('info-sheet', 'График'), [])
    labels = {label for group in groups for label in workbook[group][1]}
    course['labs'] = {key: lab for key, lab in course['labs'].items() if lab.get('short-name') in labels}
    if not course['labs']:
        raise ValueError('Не найдены столбцы лабораторных работ')
    course['queue']['sheet_source']['groups'] = groups
    course['queue']['schedule'] = {}
    required, lessons, status = {}, {}, {}
    for group in groups:
        scores = scored_labs(graph, group)
        required[group] = [key for key, lab in course['labs'].items()
                           if key != '0' and lab.get('short-name') in workbook[group][1]
                           and scores.get(lab.get('short-name'), True)]
        if not required[group]:
            raise ValueError(f'Для группы {group} не найдены работы, требующие защиты')
        group_id = directory.get(group.casefold())
        if group_id is None:
            course['queue']['schedule'][group] = {'source': 'manual', 'university': '', 'match_status': 'group_not_found'}
            lessons[group], status[group] = [], 'group_not_found'
            continue
        source = {'source': 'suai', 'university': 'ГУАП', 'group_id': group_id,
                  'subject': 'Операционные системы', 'kind': 'Лабораторное занятие',
                  'teacher_id': '39614', 'teacher_key': 'polyak', 'week_anchor': '2026-08-31'}
        course['queue']['schedule'][group] = source
        lessons[group] = normalize_lessons(load(source, fetch=fetch, now=now, timezone=course['queue']['timezone']))
        status[group] = 'ok' if lessons[group] else 'no_matching_labs'
    course['queue']['required_labs_by_group'] = required
    return course, {group: workbook[group] for group in groups}, lessons, status


def prepare(args):
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    # Нельзя подхватить настоящий GitHub-токен или включить фоновые обращения до подготовки курса.
    os.environ.update(GITHUB_TOKEN='public-read-only', QUEUE_WORKER_ENABLED='0')
    import requests
    def fetch(url):
        response = requests.get(url, timeout=(5, 20))
        response.raise_for_status()
        response.encoding = 'utf-8'
        return response.text
    now = int(time.time())
    directory = local_directory(args.data_dir)
    try:
        course, grids, lessons, schedule_status = inspect_sources(base_course(), fetch=fetch, now=now)
        from grading.defense_queue.storage import QueueStore
        from grading.defense_queue.sync import sync_group, parse_queue_deadline
        from grading.sheets_client import parse_deadline
        # Диагностика не меняет предыдущую локальную историю и не сохраняет выгрузку студентов.
        groups = {}
        with tempfile.TemporaryDirectory(dir=directory) as temp:
            store = QueueStore(Path(temp)/'check.sqlite3')
            for group, grid in grids.items():
                summary = sync_group(store, COURSE_ID, group, course, grid,
                                     parse_deadline=lambda value: parse_queue_deadline(value, course, parse_deadline), now=now)
                groups[group] = {'students': summary['students'], 'eligible': summary['eligible'],
                                 'defense_labs': course['queue']['required_labs_by_group'][group],
                                 'deadlines_available': summary['deadlines_available'],
                                 'sheet_deadlines': summary['sheet_deadlines'],
                                 'sessions': len(lessons[group]), 'schedule_status': schedule_status[group],
                                 'next_session': datetime.fromtimestamp(min(row['starts_at'] for row in lessons[group]), timezone.utc).isoformat() if lessons[group] else None}
        report = {'checked_at': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'status': 'ok', 'groups': groups,
                  'students': sum(group['students'] for group in groups.values()),
                  'eligible': sum(group['eligible'] for group in groups.values()),
                  'sessions': sum(len(rows) for rows in lessons.values())}

    except Exception as error:
        # В отчёте нет ФИО, логинов, содержимого таблицы или секретов.
        report = {'checked_at': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'status': 'error',
                  'error_type': type(error).__name__, 'message': str(error)[:800]}
        (directory/'last-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        raise
    (directory/'last-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return directory, course, lessons


def create_app(directory, course, lessons, frontend_port=5173):
    configs = directory/'courses'
    configs.mkdir(exist_ok=True)
    (configs/'live.yaml').write_text(json.dumps({'course': course}, ensure_ascii=False), encoding='utf-8')
    (configs/'index.yaml').write_text(json.dumps({'version':'1.0','courses':[
        {'id':COURSE_ID,'file':'live.yaml','status':'active','priority':100}]}), encoding='utf-8')
    password = secrets.token_urlsafe(12)
    os.environ.update(ADMIN_LOGIN='local', ADMIN_PASSWORD=password, SECRET_KEY=secrets.token_hex(32),
                      QUEUE_DB_PATH=str(directory/'queue.sqlite3'), LOG_DIR=str(directory/'logs'))
    import main
    from fastapi.responses import JSONResponse
    from grading.defense_queue.runtime import queue_store
    from grading.defense_queue.schedule import import_sessions
    main.COURSES_DIR, main.INDEX_FILE = str(configs), str(configs/'index.yaml')
    if isinstance(lessons, list):
        lessons = {GROUP_ID: lessons}
    groups = list(course['queue']['sheet_source'].get('groups', lessons))
    for group, rows in lessons.items():
        import_sessions(queue_store(), COURSE_ID, group, course, rows, now=int(time.time()))
    os.environ['QUEUE_WORKER_ENABLED'] = '1'

    @main.app.middleware('http')
    async def public_data_boundary(request, call_next):
        if not allowed_path(request.url.path, request.method, groups):
            return JSONResponse(status_code=409, content={'detail': 'Локальная проверка: внешние источники доступны только для чтения. Регистрация, GitHub и запись оценок отключены.'})
        return await call_next(request)

    print('\nВнешние источники: только чтение. Действия очереди сохраняются только в локальной SQLite.', flush=True)
    print(f'Вход: http://localhost:{frontend_port}/admin', flush=True)
    print(f'Логин: local; пароль: {password}', flush=True)
    print(f'Панель: http://localhost:{frontend_port}/admin/courses/{COURSE_ID}/queue', flush=True)
    group = GROUP_ID if GROUP_ID in groups else groups[0]
    first = course['queue'].get('required_labs_by_group', {}).get(group, course['queue']['required_labs'])[0]
    print(f'Студент: http://localhost:{frontend_port}/course/{COURSE_ID}', flush=True)
    print(f'Данные и отчёт: {directory}\n', flush=True)
    return main.app


def reset_local_state(directory):
    """Called only after the local server has stopped. Preserve an ignored backup."""
    import sqlite3
    from contextlib import closing
    database = directory/'queue.sqlite3'
    if not database.exists():
        return None
    archive = directory/'backups'/f'queue-before-reset-{time.time_ns()}.sqlite3'
    archive.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(archive)) as destination:
        source.backup(destination)
    for path in (database, Path(str(database)+'-wal'), Path(str(database)+'-shm')):
        path.unlink(missing_ok=True)
    print(f'Локальная история очищена. Резервная копия: {archive}', flush=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Проверить оба источника и структуру, сохранить обезличенный отчёт, без сервера')
    parser.add_argument('--data-dir', default='data/queue-live', help='Отдельный каталог внутри data/')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--reset', action='store_true', help='После остановки сервера: архивировать локальную БД и начать с чистых данных источников')
    args = parser.parse_args()
    if args.reset and args.check:
        parser.error('--reset используется только при запуске сервера')
    try:
        if args.reset:
            import socket
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', args.port))
                except OSError as error:
                    raise ValueError('Перед очисткой остановите локальный сервер (Ctrl+C в его терминале)') from error
        directory, course, lessons = prepare(args)
        if args.reset:
            reset_local_state(directory)
        if not args.check:
            import uvicorn
            uvicorn.run(create_app(directory, course, lessons), host='127.0.0.1', port=args.port)
    except Exception as error:
        print(f'Проверка не завершена: {type(error).__name__}: {error}', file=sys.stderr)
        print('Проверь интернет и доступ к экспорту Google-таблицы и сайту ГУАП. Отчёт: data/queue-live/last-check.json (при другом --data-dir смотри указанный каталог).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
