"""Чтение публичных CSV/XLSX Google Sheets без ключей и операций записи."""
import csv
import io
import re
import threading
import time

import requests

MAX_BYTES = 4 * 1024 * 1024


def public_sheet_config(course):
    config = course.get('queue', {}).get('sheet_source')
    if config is None:
        return None
    if not isinstance(config, dict) or config.get('type') not in ('public_csv', 'public_xlsx'):
        raise ValueError('queue.sheet_source: поддерживается type public_csv или public_xlsx')
    spreadsheet = course.get('google', {}).get('spreadsheet')
    if not isinstance(spreadsheet, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', spreadsheet):
        raise ValueError('Нужен корректный ID публичной Google-таблицы')
    if config['type'] == 'public_xlsx':
        if 'groups' in config and (not isinstance(config['groups'], list) or any(not isinstance(group, str) or not group.strip() for group in config['groups'])):
            raise ValueError('groups должен содержать имена групп')
        return config
    sheets = config.get('worksheets')
    if not isinstance(sheets, dict) or not sheets:
        raise ValueError('Нужен словарь worksheets: группа → gid')
    for group, gid in sheets.items():
        if not isinstance(group, str) or not group.strip() or isinstance(gid, bool) or not re.fullmatch(r'[0-9]{1,20}', str(gid)):
            raise ValueError('worksheets: нужны строковые имена групп и числовые gid')
    return config


def parse_public_csv(content):
    if len(content) > MAX_BYTES:
        raise ValueError('Публичная таблица превышает допустимый размер')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('Экспорт таблицы должен использовать UTF-8') from None
    try:
        rows = list(csv.reader(io.StringIO(text, newline=''), strict=True))
    except csv.Error:
        raise ValueError('Не удалось разобрать CSV публичной таблицы') from None
    # Не принимать HTML входа Google или страницу ошибки как пустую таблицу.
    if len(rows) < 2 or sum(value.strip() == 'GitHub' for value in rows[0]) != 1:
        raise ValueError('Экспорт не содержит ожидаемые строки заголовков и GitHub. Проверьте публичный доступ и gid')
    if len(rows) > 10000 or any(len(row) > 1000 for row in rows):
        raise ValueError('Слишком большая таблица для очереди')
    return [[cell.strip() for cell in row] for row in rows]


def read_public_grid(course, group_id):
    config = public_sheet_config(course)
    if config is not None and config['type'] == 'public_xlsx':
        if group_id not in list_public_groups(course):
            raise ValueError('Группа не найдена среди листов Google-таблицы')
        return read_public_workbook(course)[group_id]
    if config is None or group_id not in config['worksheets']:
        raise ValueError('Для группы не задан публичный gid')
    spreadsheet = course['google']['spreadsheet']
    url = f'https://docs.google.com/spreadsheets/d/{spreadsheet}/export'
    # Путь фиксирован: произвольный URL/хост из конфигурации не запрашивается.
    with requests.get(url, params={'format': 'csv', 'gid': str(config['worksheets'][group_id])},
                      timeout=(5, 20), stream=True) as response:
        response.raise_for_status()
        content_type = response.headers.get('content-type', '').lower()
        if 'html' in content_type:
            raise ValueError('Вместо CSV Google вернул HTML. Проверьте доступ к таблице без входа')
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('Публичная таблица превышает допустимый размер')
            chunks.append(chunk)
    return parse_public_csv(b''.join(chunks))


_WORKBOOKS = {}
_WORKBOOK_LOCK = threading.Lock()


def read_public_workbook(course):
    from .workbook import parse_workbook
    public_sheet_config(course)
    spreadsheet = course['google']['spreadsheet']
    with _WORKBOOK_LOCK:
        previous = _WORKBOOKS.get(spreadsheet)
        if previous and time.monotonic() - previous[0] < 30:
            return previous[1]
        url = f'https://docs.google.com/spreadsheets/d/{spreadsheet}/export'
        with requests.get(url, params={'format': 'xlsx'}, timeout=(5, 30), stream=True) as response:
            response.raise_for_status()
            if 'html' in response.headers.get('content-type', '').lower():
                raise ValueError('Google вернул HTML вместо книги. Проверьте публичный доступ')
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Публичная книга превышает допустимый размер')
                chunks.append(chunk)
        workbook = parse_workbook(b''.join(chunks))
        if not any(len(grid) >= 2 and 'GitHub' in grid[0] for grid in workbook.values()):
            raise ValueError('В книге не найдены листы групп с заголовком GitHub')
        # Ограниченный кэш одной загрузки для нескольких групп; сырая книга на диск не пишется.
        if len(_WORKBOOKS) >= 8:
            _WORKBOOKS.pop(next(iter(_WORKBOOKS)))
        _WORKBOOKS[spreadsheet] = (time.monotonic(), workbook)
        return workbook


def list_public_groups(course):
    source = public_sheet_config(course)
    if source['type'] == 'public_csv':
        return list(source['worksheets'])
    found = [name for name, grid in read_public_workbook(course).items()
             if name != course.get('google', {}).get('info-sheet') and len(grid) >= 2 and 'GitHub' in grid[0]]
    selected = source.get('groups')
    return [name for name in selected if name in found] if selected is not None else found
