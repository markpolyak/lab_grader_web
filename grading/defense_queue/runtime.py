"""Подключение очереди к приложению; Sheets-клиент передаёт main.py."""
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import hashlib
from functools import lru_cache
import os
import json
import secrets
import threading
import time

from .policy import CandidateOrder, order_candidates, resolve_mode, priority_for
from .storage import QueueStore
from .sync import course_timezone, sync_group

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_CACHE = {}


@lru_cache(maxsize=8)
def _store(path):
    return QueueStore(path)


def queue_store():
    return _store(os.getenv('QUEUE_DB_PATH', 'data/queue.sqlite3'))


def saved_deadlines_available(store, course_id, group_id):
    with store.connection() as con:
        saved = con.execute('SELECT summary_json FROM queue_sync_state WHERE course_id=? AND group_id=?',(course_id,group_id)).fetchone()
        if saved:
            return bool(json.loads(saved[0])['deadlines_available'])
        return all(row[0] for row in con.execute("SELECT deadline_available FROM queue_candidates WHERE course_id=? AND group_id=? AND status='eligible'",(course_id,group_id)))


def sync_queue(course_id, group_id, course, *, fetch_grid, parse_deadline, force=False):
    if not course.get('queue', {}).get('enabled', False):
        raise ValueError('Очередь не включена для курса')
    store = queue_store()
    from .operations import effective_course
    course = effective_course(store, course_id, course)
    key = (str(store.path), course_id, group_id, json.dumps(course.get('queue', {}), sort_keys=True, default=str))
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    with lock:
        cached = _CACHE.get(key)
        now = int(time.time())
        with store.connection() as con:
            active = con.execute("""SELECT session_id FROM sessions WHERE course_id=? AND group_id=?
                AND starts_at<=? AND ends_at>? AND source_kind!='day'
                AND enrollment_status!='cancelled'""", (course_id,group_id,now,now)).fetchall()
        if active:
            available = saved_deadlines_available(store, course_id, group_id)
            for session in active:
                queue_view(store,course_id,group_id,course,deadlines_available=available,now=now,session_id=session[0])
            # During class, queue commands use SQLite; even force must not read Sheets.
            with store.connection() as con:
                saved = con.execute('SELECT summary_json FROM queue_sync_state WHERE course_id=? AND group_id=?', (course_id,group_id)).fetchone()
                eligible = con.execute("SELECT count(*) FROM queue_candidates WHERE course_id=? AND group_id=? AND status='eligible'", (course_id,group_id)).fetchone()[0]
            summary = json.loads(saved[0]) if saved else dict(deadlines_available=available,eligible=eligible,deadlines={},sheet_deadlines={},timezone=course_timezone(course))
            return store, dict(summary,source_frozen=True,stale=False)
        if not force and cached and time.monotonic() - cached[0] < 15:
            return store, cached[1]
        # Preserve completed class snapshots before the next external sync.
        with store.connection() as con:
            started = con.execute("SELECT session_id FROM sessions WHERE course_id=? AND group_id=? AND starts_at<=? AND enrollment_status!='cancelled'", (course_id,group_id,now)).fetchall()
        available = saved_deadlines_available(store,course_id,group_id)
        for session in started:
            queue_view(store,course_id,group_id,course,deadlines_available=available,now=now,session_id=session[0])
        # Сеть вызывается вне транзакций SQLite.
        try:
            grid = fetch_grid()
        except Exception:
            if cached:
                return store, dict(cached[1], stale=True)
            with store.connection() as con:
                saved = con.execute('SELECT summary_json FROM queue_sync_state WHERE course_id=? AND group_id=?', (course_id,group_id)).fetchone()
            if saved:
                return store, dict(json.loads(saved[0]), stale=True)
            raise
        result = sync_group(store, course_id, group_id, course, grid,
                            parse_deadline=parse_deadline, now=int(time.time()))
        with store.transaction() as con:
            con.execute('INSERT INTO queue_sync_state(course_id,group_id,summary_json,checked_at) VALUES (?,?,?,?) ON CONFLICT(course_id,group_id) DO UPDATE SET summary_json=excluded.summary_json,checked_at=excluded.checked_at', (course_id,group_id,json.dumps(result),int(time.time())))
        _CACHE[key] = (time.monotonic(), result)
        return store, result


def ensure_daily_session(store, course_id, group_id, course, *, now):
    if not course.get('queue', {}).get('enabled') or course.get('queue', {}).get('schedule'):
        return
    zone = ZoneInfo(course_timezone(course))
    day = datetime.fromtimestamp(now, zone).date()
    start = datetime.combine(day, datetime.min.time(), zone)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
    digest = hashlib.sha256(f'{course_id}\0{group_id}'.encode()).hexdigest()[:24]
    session_id = f'daily:{digest}:{day}'
    with store.transaction() as con:
        if con.execute("SELECT 1 FROM sessions WHERE course_id=? AND group_id=? AND ends_at>? AND enrollment_mode!='daily'", (course_id,group_id,now)).fetchone():
            return
        if con.execute('SELECT 1 FROM sessions WHERE session_id=?', (session_id,)).fetchone():
            return
        store.create_session(session_id, course_id, group_id, 'daily', starts_at=int(start.timestamp()), ends_at=int(end.timestamp()), local_date=str(day), now=now)
        con.execute("UPDATE sessions SET enrollment_mode='daily',source_kind='day',opens_at=starts_at WHERE session_id=?", (session_id,))


def queue_view(store, course_id, group_id, course, *, deadlines_available, now, session_id=None):
    from .operations import effective_course
    course = effective_course(store, course_id, course)
    if session_id is None:
        ensure_daily_session(store, course_id, group_id, course, now=now)
    with store.connection() as con:
        if session_id:
            session = con.execute('SELECT * FROM sessions WHERE session_id=? AND course_id=? AND group_id=?',
                                  (session_id, course_id, group_id)).fetchone()
        else:
            session = con.execute("""SELECT * FROM sessions WHERE course_id=? AND group_id=?
                AND (starts_at>? OR ((enrollment_mode!='legacy' OR ?) AND ends_at>?)) AND enrollment_status NOT IN ('cancelled','closed')
                ORDER BY starts_at,session_id LIMIT 1""", (course_id, group_id, now, int(course.get('queue', {}).get('fifo', False)), now)).fetchone()
    if session is not None and session['enrollment_mode'] != 'daily' and now < session['ends_at']:
        fifo = course.get('queue', {}).get('fifo', False)
        with store.transaction() as con:
            con.execute('UPDATE sessions SET enrollment_mode=?,opens_at=? WHERE session_id=?',
                        ('fifo' if fifo else 'legacy', session['starts_at'] - course.get('queue', {}).get('opens_before_minutes', 15)*60 if fifo else session['opens_at'], session['session_id']))
            session = con.execute('SELECT * FROM sessions WHERE session_id=?', (session['session_id'],)).fetchone()
    continuous = session is not None and session['enrollment_mode'] != 'legacy'
    if session is not None and not continuous and now >= session['starts_at']:
        with store.connection() as con:
            frozen = con.execute("SELECT details_json FROM queue_events WHERE session_id=? AND kind='roster_frozen' AND json_extract(details_json,'$.session.schedule_revision')=? ORDER BY event_id LIMIT 1", (session['session_id'], session['schedule_revision'])).fetchone()
        if frozen:
            result = json.loads(frozen[0])
            result['session'] = dict(session)
            return result
    mode = resolve_mode(deadlines_available=deadlines_available,
                        random_override=course.get('queue', {}).get('random', False),
                        fifo=course.get('queue', {}).get('fifo', False))
    if session is None:
        with store.connection() as con:
            pending = con.execute('''SELECT c.candidate_id,c.lab_key,c.qualified_at,s.full_name,s.github_login
                FROM queue_candidates c JOIN students s USING(student_id)
                WHERE c.course_id=? AND c.group_id=? AND c.status='eligible'
                ORDER BY c.qualified_at,c.candidate_id''', (course_id, group_id)).fetchall()
        entries = [dict(candidate_id=row['candidate_id'], lab_key=row['lab_key'],
                        qualified_at=row['qualified_at'], full_name=row['full_name'], github=row['github_login'],
                        will_attend=False, answered=False, position=None, state='unconfirmed') for row in pending]
        return {'session': None, 'mode': mode.name, 'mode_locked': mode.locked,
                'entries': entries, 'attendance_open': False,
                'message': 'Ближайшее занятие ещё не назначено; порядок уточнится после назначения'}
    # Ключи создаются один раз, включая ещё не ответивших участников.
    with store.transaction() as con:
        candidates = con.execute("SELECT * FROM queue_candidates WHERE course_id=? AND group_id=? AND status='eligible' AND qualified_at<=?",
                                 (course_id, group_id, now if continuous or now < session['starts_at'] else session['starts_at'])).fetchall()
        for candidate in candidates:
            previous = con.execute("""SELECT will_attend,attendance_answered,updated_at FROM session_participation
                WHERE session_id=? AND student_id=? AND attendance_answered=1
                ORDER BY updated_at DESC,candidate_id DESC LIMIT 1""", (session['session_id'],candidate['student_id'])).fetchone()
            con.execute("""INSERT INTO session_participation(session_id,candidate_id,course_id,group_id,
                student_id,teacher_key,random_key,updated_at,will_attend,attendance_answered) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id,candidate_id) DO NOTHING""", (session['session_id'], candidate['candidate_id'],
                course_id, group_id, candidate['student_id'], session['teacher_key'], secrets.SystemRandom().random(), previous['updated_at'] if previous else now,
                previous['will_attend'] if previous else 0, previous['attendance_answered'] if previous else 0))
        if not continuous and now >= session['starts_at']:
            con.execute('UPDATE session_participation SET frozen_at=? WHERE session_id=? AND frozen_at IS NULL',
                        (session['starts_at'], session['session_id']))
    from .roster import ordering_rows
    rows = ordering_rows(store, session, now=now, mode=mode.name)
    if rows is None:
        rows = store.roster(session['session_id'])
    if not continuous and now >= session['starts_at']:
        rows = [row for row in rows if row['frozen_at'] is not None]
    rows = [dict(row) for row in rows]
    if mode.name == 'fifo':
        with store.connection() as con:
            for row in rows:
                event = con.execute("SELECT max(event_id) FROM queue_events WHERE session_id=? AND candidate_id=? AND kind='attending'", (session['session_id'],row['candidate_id'])).fetchone()[0]
                row['signup_order'] = event if row['will_attend'] and event is not None else float('inf')
    zone = course_timezone(course)
    anchor = date.fromisoformat(course.get('queue', {}).get('semester_week_anchor', '1970-01-05'))
    candidates = [CandidateOrder(row['candidate_id'], row['signup_order'] if mode.name == 'fifo' else row.get('order_at', row['qualified_at']),
                  datetime.fromtimestamp(row['deadline_at'], tz=timezone.utc) if row['deadline_at'] is not None else None,
                  row['random_key'], deadline_available=bool(row['deadline_available'])) for row in rows]
    ordered = order_candidates(candidates, mode=mode, session_start=datetime.fromtimestamp(min(now, session['starts_at']), tz=timezone.utc),
                               week_anchor=anchor, timezone=zone)
    by_id = {row['candidate_id']: row for row in rows}
    entries = []
    position = 0
    for candidate in ordered:
        row = by_id[candidate.candidate_id]
        if row['will_attend']:
            position += 1
        entries.append({'student_id': row.get('student_id'), 'ready': row.get('ready', True),
                        'virtual_priority': row.get('virtual_priority', False), 'roster_order': len(entries),
                        'candidate_id': row['candidate_id'], 'full_name': row['full_name'],
                        'github': row['github_login'], 'lab_key': row['lab_key'],
                        'qualified_at': row['qualified_at'], 'deadline_at': row['deadline_at'], 'deadline_available': bool(row['deadline_available']), 'priority': None if mode.name in ('random','fifo') or candidate.deadline is None else priority_for(candidate.deadline, datetime.fromtimestamp(min(now, session['starts_at']), timezone.utc), week_anchor=anchor, timezone=zone), 'will_attend': bool(row['will_attend']),
                        'answered': bool(row['attendance_answered']),
                        'attendance_at': row['attendance_at'] if row['attendance_answered'] else None,
                        'position': position if row['will_attend'] else None,
                        'state': 'attending' if row['will_attend'] else 'declined' if row['attendance_answered'] else 'unconfirmed'})
    result = {'session': dict(session), 'mode': mode.name, 'mode_locked': mode.locked,
              'entries': entries, 'attendance_open': (session['opens_at'] <= now < session['ends_at'] if continuous else now < session['starts_at']) and session['enrollment_status'] not in ('closed', 'cancelled'),
              'enrollment_opens_at': session['opens_at'], 'continuous_enrollment': continuous}
    if not continuous and now >= session['starts_at']:
        with store.transaction() as con:
            frozen = con.execute("SELECT details_json FROM queue_events WHERE session_id=? AND kind='roster_frozen' AND json_extract(details_json,'$.session.schedule_revision')=? ORDER BY event_id LIMIT 1", (session['session_id'], session['schedule_revision'])).fetchone()
            if frozen:
                return json.loads(frozen[0])
            con.execute("""INSERT INTO queue_events(session_id,actor_kind,actor_key,kind,details_json,occurred_at)
                VALUES (?,'system','queue','roster_frozen',?,?)""", (session['session_id'], json.dumps(result, ensure_ascii=False), now))
    return result
