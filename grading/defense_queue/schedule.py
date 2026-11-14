"""Адаптер расписания ГУАП и импорт датированных занятий других вузов.

Формат ГУАП: https://github.com/markpolyak/suai-schedule-parser
Преобразование HTML повторяет структуру suai_rasp.parse; внешние процессы не запускаются.
Сеть передаётся как callback, поэтому импорт проверяется без сайта университета.
"""
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
import hashlib
import re
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .sync import course_timezone
from .schedule_sources import adapter

DAYS = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
SLOTS = (('09:30','11:00'), ('11:10','12:40'), ('13:00','14:30'), ('15:10','16:40'),
         ('17:00','18:30'), ('18:40','20:10'), ('20:20','21:50'))


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    @property
    def text(self):
        return ' '.join(' '.join(c.text if isinstance(c, Node) else c for c in self.children).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def has(self, *classes):
        return set(classes).issubset(self.attrs.get('class', '').split())


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node(); self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs); self.stack[-1].children.append(node)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]; break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_suai(html):
    """Получить контракт Lesson из HTML страницы группы ГУАП."""
    root = Tree(html).root
    content = next((n for n in root.walk() if n.has('content')), None)
    if content is None:
        raise ValueError('ГУАП: не найден блок расписания; сохранённые занятия не изменены')
    day = slot = None
    lessons = []
    for node in content.walk():
        if node.tag == 'h4':
            label = node.text.lower()
            day = next((i for i, name in enumerate(DAYS) if name in label), None)
            slot = None
        if node.has('text-danger', 'mt-3'):
            match = re.search(r'(\d+)\s*пара', node.text)
            slot = int(match[1]) if match else None
        if not node.has('mb-3', 'py-2', 'd-flex') or day is None or slot is None:
            continue
        descendants = list(node.walk())
        subject = next((n.text for n in descendants if n.has('lead')), '')
        kind = next((n.text for n in descendants if n.has('fs-6')), '')
        week = 1 if any(n.has('week1') for n in descendants) else 2 if any(n.has('week2') for n in descendants) else 0
        groups, teachers = [], []
        for n in descendants:
            if n.tag != 'a': continue
            query = parse_qs(urlparse(n.attrs.get('href', '')).query)
            for key, target in (('gr', groups), ('pr', teachers)):
                if key in query: target.append({'id': query[key][0], 'name': n.text})
        lessons.append(dict(day=day, slot=slot, week=week, subject=subject, kind=kind, groups=groups, teachers=teachers))
    return lessons


def normalize_lessons(lessons):
    if not isinstance(lessons,list) or len(lessons)>500:
        raise ValueError('Нужен массив не более 500 занятий')
    result=[]
    for row in lessons:
        if not isinstance(row,dict): raise ValueError('Каждое занятие должно быть объектом')
        key=row.get('key');teacher=row.get('teacher_key','main');kind=row.get('source_kind','university')
        if not isinstance(key,str) or not key.strip() or len(key)>512: raise ValueError('Нужен непустой строковый key занятия')
        if not isinstance(teacher,str) or not teacher.strip() or len(teacher)>128: raise ValueError('Нужен ключ преподавателя')
        if kind not in ('university','manual','extra'): raise ValueError('Неизвестный тип занятия')
        def stamp(value):
            try:
                if isinstance(value,bool): raise ValueError()
                if isinstance(value,(int,float)): number=int(value)
                else:
                    dt=datetime.fromisoformat(value)
                    if dt.utcoffset() is None: raise ValueError()
                    number=int(dt.timestamp())
                datetime.fromtimestamp(number,ZoneInfo('UTC'))
                return number
            except (ValueError,TypeError,OverflowError,OSError):
                raise ValueError('Нужна корректная дата с часовым поясом или Unix-время') from None
        start,end=stamp(row.get('starts_at')),stamp(row.get('ends_at'))
        if start>=end: raise ValueError('Окончание должно быть позже начала')
        result.append(dict(key=key,teacher_key=teacher,source_kind=kind,starts_at=start,ends_at=end))
    if len({r['key'] for r in result})!=len(result): raise ValueError('Повторяющийся ключ занятия')
    return result


def validate_schedule(config):
    if not isinstance(config, dict): raise ValueError('Расписание должно быть объектом')
    if not isinstance(config.get('lessons', []), list): raise ValueError('lessons должен быть массивом')
    if len(config.get('lessons', [])) > 500: raise ValueError('В одном импорте допускается до 500 занятий')
    if config.get('semester_end'):
        try: date.fromisoformat(config['semester_end'])
        except (TypeError,ValueError): raise ValueError('Нужна дата конца семестра') from None
    for field in ('kind','teacher_id','teacher_key','university'):
        if field in config and not isinstance(config[field],str): raise ValueError(f'{field}: нужна строка')
    normalize_lessons(config.get('lessons',[]))
    adapter(config.get('source', 'manual')).validate(config)
    return config


def expand_suai(lessons, config, *, now, timezone):
    """Горизонт 8 недель. week_anchor — понедельник первой нечётной недели."""
    validate_schedule(dict(config, source='suai'))
    zone = ZoneInfo(timezone)
    first = datetime.fromtimestamp(now, zone).date()
    anchor = date.fromisoformat(config['week_anchor'])
    until = min(first + timedelta(days=56), date.fromisoformat(config.get('semester_end') or '9999-12-31'))
    result = []
    for lesson in lessons:
        if lesson.get('subject', '').casefold() != config['subject'].strip().casefold(): continue
        if config.get('kind') and lesson.get('kind', '').casefold() != config['kind'].strip().casefold(): continue
        if config.get('teacher_id') and not any(str(t['id']) == str(config['teacher_id']) for t in lesson.get('teachers', [])): continue
        if lesson.get('groups') and not any(str(g['id']) == str(config['group_id']) for g in lesson['groups']): continue
        day = lesson['day']
        if isinstance(day, str): day = DAYS.index(day.lower())
        slot = int(lesson['slot'])
        if not 1 <= slot <= len(SLOTS): raise ValueError('Неизвестный номер пары ГУАП')
        current = first
        while current <= until:
            week = (current - anchor).days // 7 % 2 + 1
            if current.weekday() == day and lesson.get('week', 0) in (0, week):
                a, b = SLOTS[slot-1]
                start = datetime.combine(current, time.fromisoformat(a), zone)
                end = datetime.combine(current, time.fromisoformat(b), zone)
                if start.timestamp() > now:
                    result.append(dict(key=f'suai:{config["group_id"]}:{current}:{slot}', starts_at=int(start.timestamp()),
                                       ends_at=int(end.timestamp()), teacher_key=config.get('teacher_key', 'main'), source_kind='university'))
            current += timedelta(days=1)
    # Одна пара может повторяться карточками аудиторий/подгрупп. Очередь общая.
    return list({row['key']: row for row in result}.values())


def import_sessions(store, course_id, group_id, course, lessons, *, now):
    """Стабильные ключи; ручные переносы/отмены (revision>1) важнее обновления источника."""
    zone = course_timezone(course)
    prepared = []
    for lesson in normalize_lessons(lessons):
        start,end,key=lesson['starts_at'],lesson['ends_at'],lesson['key']
        sid = 'schedule-' + hashlib.sha256(f'{course_id}\n{group_id}\n{key}'.encode()).hexdigest()[:32]
        prepared.append((sid,key,start,end,lesson['teacher_key'],lesson['source_kind']))
    if len({r[1] for r in prepared}) != len(prepared): raise ValueError('Повторяющийся ключ занятия')
    store.configure_group(course_id, group_id, title=course.get('name', course_id), timezone=zone)
    with store.transaction() as con:
        for sid, key, start, end, teacher, kind in prepared:
            con.execute('''INSERT INTO sessions(session_id,course_id,group_id,teacher_key,source_key,source_kind,
                local_date,timezone,opens_at,starts_at,ends_at,expires_at,schedule_checked_at,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(course_id,group_id,source_key) DO UPDATE SET
                starts_at=excluded.starts_at,ends_at=excluded.ends_at,expires_at=excluded.expires_at,
                local_date=excluded.local_date,schedule_checked_at=excluded.schedule_checked_at
                WHERE sessions.schedule_revision=1 AND sessions.starts_at>? AND sessions.enrollment_status!='cancelled' ''',
                (sid,course_id,group_id,teacher,key,kind,datetime.fromtimestamp(start, ZoneInfo(zone)).date().isoformat(),
                 zone,min(now,start),start,end,end,now,now,now))
            from .storage import initialize_session
            initialize_session(con, sid, now=now)
    return len(prepared)


def refresh_schedule(store, course_id, group_id, course, *, fetch, now):
    config = course.get('queue', {}).get('schedule', {}).get(group_id, {})
    validate_schedule(config)
    from .schedule_overrides import validate_overrides, apply_overrides
    overrides = validate_overrides(course.get('queue', {}).get('schedule_overrides', []))
    try:
        lessons = adapter(config.get('source', 'manual')).load(config, fetch=fetch, now=now, timezone=course_timezone(course))
    except Exception:
        # Ручные отмены и добавления действуют даже при недоступном вузовском сайте.
        apply_overrides(store, course_id, group_id, course, overrides, now=now)
        raise
    with store.transaction():
        count = import_sessions(store, course_id, group_id, course, lessons, now=now)
        apply_overrides(store, course_id, group_id, course, overrides, now=now)
    return count
