"""Поправки курса поверх вузовского или ручного основного расписания."""
from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

from .sync import course_timezone


def validate_overrides(rows):
    from .schedule import normalize_lessons
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError('schedule_overrides: нужен массив не более 500 поправок')
    result, keys = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Поправка расписания должна быть объектом')
        key, kind, groups = row.get('key'), row.get('kind'), row.get('groups')
        if not isinstance(key, str) or not key.strip() or len(key) > 128 or key in keys:
            raise ValueError('Поправке нужен уникальный непустой key длиной до 128 символов')
        if kind not in ('cancel', 'add', 'move'):
            raise ValueError('Поправка: cancel, add или move')
        if not isinstance(groups, list) or not groups or any(not isinstance(g, str) or not g.strip() or len(g) > 128 for g in groups) or len(set(groups)) != len(groups):
            raise ValueError('Поправке нужен непустой список уникальных групп')
        value = dict(row)
        if 'source_key' in row and (not isinstance(row['source_key'], str) or not row['source_key'].strip()):
            raise ValueError('source_key должен быть непустой строкой')
        if kind == 'cancel':
            try:
                date.fromisoformat(row.get('date', ''))
            except (TypeError, ValueError):
                raise ValueError('Для отмены нужна date в формате ГГГГ-ММ-ДД') from None
        else:
            lesson = normalize_lessons([dict(key=key, starts_at=row.get('starts_at'), ends_at=row.get('ends_at'),
                                            teacher_key=row.get('teacher_key', 'main'), source_kind='extra')])[0]
            value.update(starts_at=lesson['starts_at'], ends_at=lesson['ends_at'])
            if kind == 'move' and not row.get('source_key'):
                raise ValueError('Для переноса нужен source_key исходного занятия')
        result.append(value)
        keys.add(key)
    return result


def apply_overrides(store, course_id, group_id, course, rows, *, now):
    from .operations import cancel_session, move_session, _event
    from .schedule import import_sessions
    zone = course_timezone(course)
    store.configure_group(course_id, group_id, title=course.get('name', course_id), timezone=zone)
    with store.transaction() as con:
        for row in rows:
            if group_id not in row['groups']:
                continue
            signature = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if row['kind'] == 'add':
                if row['starts_at'] > now:
                    import_sessions(store, course_id, group_id, course, [dict(key='override:'+row['key'],
                        starts_at=row['starts_at'], ends_at=row['ends_at'], teacher_key=row.get('teacher_key', 'main'), source_kind='extra')], now=now)
                continue
            if row['kind'] == 'move':
                sessions = con.execute('SELECT * FROM sessions WHERE course_id=? AND group_id=? AND source_key=?',
                                       (course_id, group_id, row['source_key'])).fetchall()
            else:
                sessions = con.execute("SELECT * FROM sessions WHERE course_id=? AND group_id=? AND local_date=? AND source_kind!='extra'",
                                       (course_id, group_id, row['date'])).fetchall()
            for session in sessions:
                sid = session['session_id']
                if session['starts_at'] <= now or (row.get('source_key') and session['source_key'] != row['source_key']):
                    continue
                if con.execute("SELECT 1 FROM queue_events WHERE session_id=? AND kind='config_schedule_override' AND details_json=?", (sid, signature)).fetchone():
                    continue
                last = con.execute("SELECT actor_key FROM queue_events WHERE session_id=? AND kind IN ('schedule_changed','cancelled','restored') ORDER BY event_id DESC LIMIT 1", (sid,)).fetchone()
                if last and last['actor_key'] != 'course-config':
                    continue  # Более позднее ручное решение преподавателя важнее YAML.
                if row['kind'] == 'cancel':
                    if session['enrollment_status'] != 'cancelled':
                        cancel_session(store, sid, now=now, actor='course-config')
                else:
                    if row['starts_at'] <= now:
                        continue
                    move_session(store, sid, starts_at=row['starts_at'], ends_at=row['ends_at'],
                                 local_date=datetime.fromtimestamp(row['starts_at'], ZoneInfo(zone)).date().isoformat(), now=now, actor='course-config')
                _event(con, sid, 'config_schedule_override', now, actor='course-config', details=json.loads(signature))
