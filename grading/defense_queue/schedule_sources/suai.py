"""Расписание ГУАП. HTML разбирается общим проверяемым парсером."""
from datetime import date
import re


def validate(config):
    if not re.fullmatch(r'[0-9]{1,20}', str(config.get('group_id', ''))):
        raise ValueError('Нужен числовой ID группы на сайте ГУАП')
    if not isinstance(config.get('subject'), str) or not config['subject'].strip():
        raise ValueError('Нужно точное название предмета')
    try:
        anchor = date.fromisoformat(config.get('week_anchor', ''))
    except (TypeError, ValueError):
        raise ValueError('Нужна дата начала нечётной недели') from None
    if anchor.weekday() != 0:
        raise ValueError('Начало первой нечётной недели должно быть понедельником')


def load(config, *, fetch, now, timezone):
    from ..schedule import parse_suai, expand_suai
    html = fetch(f'https://guap.ru/rasp/?gr={config["group_id"]}')
    return list(config.get('lessons', [])) + expand_suai(parse_suai(html), config, now=now, timezone=timezone)


def group_directory(html):
    """Имя группы → ID сайта из справочника, без привязки к одной учебной группе."""
    from ..schedule import Tree
    root = Tree(html).root
    select = next((node for node in root.walk() if node.tag == 'select' and node.attrs.get('id') == 'selGroup'), None)
    if select is None:
        raise ValueError('На сайте ГУАП не найден справочник групп selGroup')
    groups = {}
    for node in select.walk():
        value = node.attrs.get('value', '')
        if node.tag == 'option' and value.isdigit() and node.text.strip():
            name = node.text.strip()
            if name in groups and groups[name] != value:
                raise ValueError('На сайте ГУАП неоднозначное название группы')
            groups[name] = value
    return groups
