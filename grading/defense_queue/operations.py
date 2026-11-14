"""Операции преподавателя. Авторизация выполняется HTTP-слоем."""
from datetime import date, datetime, timezone
import json
from uuid import uuid4

from .policy import priority_for
from .runtime import queue_view
from .storage import QueueClosed, QueueConflict
from .sync import course_timezone
from .timing import service_ends_at


def _event(con, session_id, kind, now, *, actor, entry_id=None, candidate_id=None, details=None):
    con.execute('''INSERT INTO queue_events(session_id,entry_id,candidate_id,actor_kind,actor_key,kind,details_json,occurred_at)
        VALUES (?,?,?,'teacher',?,?,?,?)''', (session_id, entry_id, candidate_id, actor, kind, json.dumps(details or {}, ensure_ascii=False), now))


def effective_course(store, course_id, course):
    result = dict(course, queue=dict(course.get('queue', {})))
    with store.connection() as con:
        settings = con.execute('SELECT * FROM queue_settings WHERE course_id=?', (course_id,)).fetchone()
    if settings:
        if settings['policy_override_set']:
            result['queue']['random'] = bool(settings['random_override'])
            result['queue']['deadlines'] = json.loads(settings['deadlines_json'])
        result['queue']['fifo'] = bool(settings['fifo'])
        result['queue']['opens_before_minutes'] = settings['opens_before_minutes']
        result['queue']['schedule'] = dict(result['queue'].get('schedule', {}), **json.loads(settings['schedule_json']))
    return result


def save_settings(store, course_id, *, random, deadlines, now, fifo=False, opens_before_minutes=15):
    if type(random) is not bool or not isinstance(deadlines, dict):
        raise ValueError('Нужны режим очереди и словарь дедлайнов')
    if type(fifo) is not bool or fifo and random or type(opens_before_minutes) is not int or not 0 <= opens_before_minutes <= 1440:
        raise ValueError('Выберите один режим и окно записи от 0 до 1440 минут')
    with store.transaction() as con:
        con.execute('''INSERT INTO queue_settings(course_id,random_override,deadlines_json,updated_at,policy_override_set)
            VALUES (?,?,?,?,1) ON CONFLICT(course_id) DO UPDATE SET
            random_override=excluded.random_override,deadlines_json=excluded.deadlines_json,updated_at=excluded.updated_at,policy_override_set=1''',
            (course_id, int(random), json.dumps(deadlines), now))

        con.execute('UPDATE queue_settings SET fifo=?,opens_before_minutes=? WHERE course_id=?', (int(fifo),opens_before_minutes,course_id))
        for session in con.execute("SELECT session_id,starts_at FROM sessions WHERE course_id=? AND starts_at>? AND enrollment_mode!='daily'", (course_id,now)).fetchall():
            con.execute('UPDATE sessions SET enrollment_mode=?,opens_at=? WHERE session_id=?', ('fifo' if fifo else 'legacy',session['starts_at']-opens_before_minutes*60 if fifo else min(now,session['starts_at']),session['session_id']))


def _insert_attempt(con, candidate, session, *, request_key, repeat, now, rank=None, snapshot=None):
    student = con.execute('SELECT * FROM students WHERE student_id=?', (candidate['student_id'],)).fetchone()
    cursor = con.execute('''INSERT INTO queue_entries(request_key,session_id,course_id,group_id,teacher_key,student_id,
        candidate_id,lab_key,lab_order,full_name_snapshot,github_snapshot,eligibility_snapshot,eligibility_checked_at,
        deadline_at,deadline_available,priority,is_repeat,joined_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'admitted',?,?,?,?,?,?) ON CONFLICT(request_key) DO NOTHING''',
        (request_key, session['session_id'], session['course_id'], session['group_id'], session['teacher_key'],
         candidate['student_id'], candidate['candidate_id'], candidate['lab_key'], candidate['lab_order'],
         snapshot['full_name'] if snapshot else student['full_name'], snapshot['github'] if snapshot else student['github_login'], now, candidate['deadline_at'], candidate['deadline_available'], rank, int(repeat), (snapshot.get('attendance_at') or now) if snapshot else now))
    return cursor.lastrowid


def materialize(store, view, course, *, now):
    session = view.get('session')
    if session is None or now < session['starts_at'] or session['enrollment_status'] == 'cancelled':
        return
    with store.transaction() as con:
        if con.execute('SELECT 1 FROM queue_reconciliations WHERE session_id=? AND done_at IS NOT NULL', (session['session_id'],)).fetchone():
            return
        checked = con.execute('SELECT checked_at FROM queue_reconciliations WHERE session_id=?', (session['session_id'],)).fetchone()
        restored = con.execute("SELECT 1 FROM queue_events WHERE session_id=? AND kind='restored' AND json_extract(details_json,'$.revision')=? AND json_extract(details_json,'$.roster_restored')=1", (session['session_id'], session['schedule_revision'])).fetchone()
        locked = restored is not None or now >= service_ends_at(session) or (checked is not None and checked['checked_at'] is not None)
        initial_rows = [] if locked else view['entries']
        for row in initial_rows:
            if not row['will_attend']:
                continue
            candidate = con.execute('SELECT * FROM queue_candidates WHERE candidate_id=?', (row['candidate_id'],)).fetchone()
            initial_key = f"initial:{session['session_id']}:{session['schedule_revision']}:{row['candidate_id']}"
            if session.get('enrollment_mode') != 'legacy':
                signup = con.execute("SELECT max(event_id) FROM queue_events WHERE session_id=? AND candidate_id=? AND kind='attending'", (session['session_id'],row['candidate_id'])).fetchone()[0]
                initial_key += f":{signup}"
            if con.execute("SELECT 1 FROM queue_entries WHERE candidate_id=? AND session_id=? AND (status NOT IN ('expired','withdrawn') OR request_key=?)", (candidate['candidate_id'], session['session_id'], initial_key)).fetchone():
                continue
            if con.execute("SELECT 1 FROM queue_entries WHERE course_id=? AND student_id=? AND session_id!=? AND status IN ('waiting','called')", (session['course_id'], candidate['student_id'], session['session_id'])).fetchone():
                continue
            candidate = dict(candidate)
            for field in ('deadline_at','deadline_available'):
                if field in row: candidate[field] = row[field]
            rank = row.get('priority')
            if 'priority' not in row and candidate['deadline_at'] is not None and candidate['deadline_available']:
                rank = priority_for(datetime.fromtimestamp(candidate['deadline_at'], timezone.utc),
                                    datetime.fromtimestamp(session['starts_at'], timezone.utc),
                                    week_anchor=date.fromisoformat(course.get('queue', {}).get('semester_week_anchor', '1970-01-05')),
                                    timezone=course_timezone(course))
            _insert_attempt(con, candidate, session, request_key=initial_key, repeat=False, rank=rank, now=now, snapshot=row)
        con.execute('''INSERT INTO queue_reconciliations(session_id,due_at) VALUES (?,?)
            ON CONFLICT(session_id) DO NOTHING''',
            (session['session_id'], min(session['ends_at'] + 600, service_ends_at(session))))


def _confirmed(con, session_id, student_id):
    row = con.execute("""SELECT will_attend FROM session_participation WHERE session_id=? AND student_id=?
        AND attendance_answered=1 ORDER BY updated_at DESC,candidate_id DESC LIMIT 1""", (session_id, student_id)).fetchone()
    return bool(row and row['will_attend'])


def teacher_view(store, course_id, group_id, course, *, deadlines_available, now, session_id):
    view = queue_view(store, course_id, group_id, course, deadlines_available=deadlines_available, now=now, session_id=session_id)
    if view['session'] is None:
        raise ValueError('Занятие не найдено')
    materialize(store, view, course, now=now)
    with store.connection() as con:
        attempts = [dict(row) for row in con.execute('SELECT * FROM queue_entries WHERE session_id=? AND voided_at IS NULL ORDER BY entry_id', (session_id,))]
        events = [dict(row) for row in con.execute('SELECT e.*,s.full_name,c.lab_key FROM queue_events e LEFT JOIN queue_candidates c USING(candidate_id) LEFT JOIN students s ON s.student_id=c.student_id WHERE e.session_id=? ORDER BY e.event_id DESC LIMIT 200', (session_id,))]
        sync = con.execute('SELECT * FROM queue_reconciliations WHERE session_id=?', (session_id,)).fetchone()
        available_candidates = [dict(row) for row in con.execute("""SELECT c.candidate_id,c.lab_key,s.full_name FROM queue_candidates c JOIN students s USING(student_id)
            WHERE c.course_id=? AND c.group_id=? AND c.status='eligible' AND NOT EXISTS
            (SELECT 1 FROM queue_entries e WHERE e.course_id=c.course_id AND e.student_id=c.student_id AND e.status IN ('waiting','called'))
            ORDER BY c.qualified_at,c.candidate_id""",(course_id,group_id))]
    # A place belongs to a student; changing the admitted lab must not create a second row.
    with store.connection() as con:
        superseded = {row[0] for row in con.execute("SELECT entry_id FROM queue_events WHERE session_id=? AND kind='withdrawn' AND json_extract(details_json,'$.correction_of') IS NOT NULL", (session_id,))}
    latest = {row['student_id']: row for row in attempts if row['entry_id'] not in superseded}
    with store.connection() as con:
        student_ids = {row['github_login'].lower(): row['student_id'] for row in con.execute('SELECT student_id,github_login FROM students')}
    current = {(row.get('student_id') or student_ids[row['github'].lower()]): dict(row) for row in view['entries']}
    with store.connection() as con:
        for candidate in con.execute("""SELECT c.*,s.github_login,s.full_name FROM queue_candidates c
                JOIN students s USING(student_id) WHERE c.course_id=? AND c.group_id=? AND c.status='eligible'""", (course_id, group_id)):
            key = candidate['student_id']
            if key not in latest:
                continue
            base = current.get(key, {})
            if base.get('candidate_id') == candidate['candidate_id']:
                continue
            current[key] = dict(base, candidate_id=candidate['candidate_id'], full_name=candidate['full_name'],
                student_id=key, github=candidate['github_login'], lab_key=candidate['lab_key'], qualified_at=candidate['qualified_at'],
                deadline_at=candidate['deadline_at'], priority=None if view['mode'] in ('random','fifo') or candidate['deadline_at'] is None else priority_for(
                    datetime.fromtimestamp(candidate['deadline_at'], timezone.utc),
                    datetime.fromtimestamp(min(now, view['session']['starts_at']), timezone.utc),
                    week_anchor=date.fromisoformat(course.get('queue', {}).get('semester_week_anchor', '1970-01-05')),
                    timezone=course_timezone(course)),
                will_attend=_confirmed(con, session_id, candidate['student_id']), position=None, state='attending')
    entries = []
    for key in dict.fromkeys([*current, *latest]):
        attempt = latest.get(key)
        row = current.get(key)
        if row is None:
            row = dict(candidate_id=attempt['candidate_id'], full_name=attempt['full_name_snapshot'], student_id=key, github=attempt['github_snapshot'],
                       lab_key=attempt['lab_key'], qualified_at=attempt['joined_at'], will_attend=True,
                       answered=True, position=None, state='attending')
        last_result = next((item for item in reversed(attempts) if item['student_id'] == key
                           and item['status'] in ('defended', 'failed', 'no_show')), None)
        # Only an attempt of this lab may supply its status and priority.
        matching = attempt if attempt and attempt['candidate_id'] == row['candidate_id'] else None
        entries.append(dict(row, attempt=matching, last_result=last_result,
                            can_add_tail=bool(row.get('will_attend') and any(c['candidate_id'] == row['candidate_id'] for c in available_candidates))))
    counters = {}
    attempt_numbers = {}
    for attempt in attempts:
        if attempt['called_at'] is not None or attempt['status'] in ('defended', 'failed', 'no_show'):
            key = (attempt['student_id'], attempt['lab_key'])
            counters[key] = counters.get(key, 0) + 1
            attempt_numbers[attempt['entry_id']] = counters[key]
    for event in events:
        event['attempt_number'] = attempt_numbers.get(event['entry_id'])
        if event['kind'] == 'result_corrected':
            details = json.loads(event['details_json'])
            event['previous_outcome'], event['outcome'] = details.get('from'), details.get('to')
    available_candidates = [c for c in available_candidates if any(row['candidate_id'] == c['candidate_id'] and row['can_add_tail'] for row in entries)]
    active = sorted((row for row in attempts if row['status'] in ('waiting', 'called')),
                    key=lambda row: (row['is_repeat'], row['entry_id']))
    waiting = [row for row in active if row['status'] == 'waiting']
    called = [row for row in active if row['status'] == 'called']
    active_order = {row['candidate_id']: index for index, row in enumerate(active)}
    entries.sort(key=lambda row: (0, active_order[row['candidate_id']]) if row['candidate_id'] in active_order else (1, row.get('roster_order', row['qualified_at'])))
    result = dict(view, entries=entries, attempts=attempts, history=events, available_candidates=available_candidates,
                service_ends_at=service_ends_at(view['session']),
                service_open=view['session']['starts_at'] <= now < service_ends_at(view['session']) and view['session']['enrollment_status'] != 'cancelled',
                current_entry_id=called[0]['entry_id'] if called else None,
                current_entry_ids=[row['entry_id'] for row in called],
                next_entry_id=waiting[0]['entry_id'] if waiting else None,
                stats={status: sum(row['status'] == status for row in attempts)
                       for status in ('waiting', 'called', 'defended', 'failed', 'no_show', 'withdrawn', 'expired')},
                reconciliation=dict(sync) if sync else None)
    from .rounds import decorate_view
    return decorate_view(store, result, now=now)


def call_next(store, session_id, *, now, actor, request_id=None):
    with store.transaction() as con:
        session = con.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone()
        if not session or now < session['starts_at'] or now >= service_ends_at(session) or session['enrollment_status'] == 'cancelled':
            raise QueueClosed('Приём ещё не начался, завершён или занятие отменено')
        if request_id is not None:
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
                raise ValueError('Некорректный идентификатор команды')
            previous = con.execute('SELECT entry_id FROM queue_commands WHERE session_id=? AND actor_key=? AND request_id=?',
                                   (session_id, actor, request_id)).fetchone()
            if previous is not None:
                return previous['entry_id']
        row = con.execute("SELECT * FROM queue_entries WHERE session_id=? AND status='waiting' ORDER BY is_repeat,entry_id LIMIT 1", (session_id,)).fetchone()
        if request_id is not None:
            con.execute('INSERT INTO queue_commands(session_id,actor_key,request_id,entry_id) VALUES (?,?,?,?)',
                        (session_id, actor, request_id, row['entry_id'] if row else None))
        if not row:
            return None
        con.execute("UPDATE queue_entries SET status='called',called_at=?,version=version+1 WHERE entry_id=?", (now, row['entry_id']))
        _event(con, session_id, 'called', now, actor=actor, entry_id=row['entry_id'], candidate_id=row['candidate_id'])
        return row['entry_id']


def record_result(store, entry_id, outcome, *, now, actor, auto_retry=True):
    if outcome not in ('defended', 'failed', 'no_show'):
        raise ValueError('Неизвестный результат')
    with store.transaction() as con:
        row = con.execute('SELECT * FROM queue_entries WHERE entry_id=?', (entry_id,)).fetchone()
        if not row:
            raise ValueError('Попытка не найдена')
        if row['status'] == outcome:
            return
        if row['status'] != 'called' and not (outcome == 'no_show' and row['status'] == 'waiting'):
            raise QueueConflict('Можно отметить только текущего вызванного студента')
        session = con.execute('SELECT * FROM sessions WHERE session_id=?', (row['session_id'],)).fetchone()
        if now >= service_ends_at(session):
            raise QueueClosed('День приёма завершён')
        con.execute('UPDATE queue_entries SET status=?,closed_at=?,version=version+1 WHERE entry_id=?', (outcome, now, entry_id))
        _event(con, row['session_id'], outcome, now, actor=actor, entry_id=entry_id, candidate_id=row['candidate_id'])
        if outcome == 'defended':
            con.execute("UPDATE queue_candidates SET status='defended' WHERE candidate_id=?", (row['candidate_id'],))
        elif outcome == 'failed' and auto_retry:
            session = con.execute('SELECT * FROM sessions WHERE session_id=?', (row['session_id'],)).fetchone()
            if now < session['ends_at']:
                candidate = con.execute('SELECT * FROM queue_candidates WHERE candidate_id=?', (row['candidate_id'],)).fetchone()
                if candidate['status'] != 'inactive':
                    _insert_attempt(con, candidate, session, request_key=f'retry:{entry_id}', repeat=True, now=now)


def add_to_tail(store, session_id, candidate_id, *, now, actor):
    with store.transaction() as con:
        candidate = con.execute("SELECT * FROM queue_candidates WHERE candidate_id=? AND status='eligible'", (candidate_id,)).fetchone()
        session = con.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone()
        if candidate is None or session is None or (candidate['course_id'], candidate['group_id']) != (session['course_id'], session['group_id']):
            raise ValueError('Работа уже защищена или больше не допущена. Обновите список и выберите текущую работу студента.')
        if session['enrollment_status'] == 'cancelled' or now >= session['ends_at']:
            raise QueueClosed('Занятие отменено или закончилось')
        if not _confirmed(con, session_id, candidate['student_id']):
            raise QueueConflict('Студент не подтвердил участие в этом занятии')
        old = con.execute("SELECT entry_id FROM queue_entries WHERE course_id=? AND student_id=? AND status IN ('waiting','called')", (candidate['course_id'], candidate['student_id'])).fetchone()
        if old:
            raise QueueConflict('У студента уже есть активная попытка')
        entry_id = _insert_attempt(con, candidate, session, request_key=f'manual:{session_id}:{candidate_id}:{uuid4().hex}', repeat=True, now=now)
        _event(con, session_id, 'added_to_tail', now, actor=actor, entry_id=entry_id, candidate_id=candidate_id)
        return entry_id


def withdraw(store, entry_id, *, now, actor):
    with store.transaction() as con:
        row = con.execute('SELECT * FROM queue_entries WHERE entry_id=?', (entry_id,)).fetchone()
        if not row:
            raise ValueError('Попытка не найдена')
        if row['status'] == 'withdrawn':
            return
        if row['status'] not in ('waiting', 'called'):
            raise QueueConflict('Попытка уже завершена')
        con.execute("UPDATE queue_entries SET status='withdrawn',closed_at=?,version=version+1 WHERE entry_id=?", (now, entry_id))
        _event(con, row['session_id'], 'withdrawn', now, actor=actor, entry_id=entry_id, candidate_id=row['candidate_id'])


def move_session(store, session_id, *, starts_at, ends_at, local_date, now, actor):
    if starts_at <= now:
        raise ValueError('Перенести занятие можно только на будущее время')
    if starts_at >= ends_at:
        raise ValueError('Окончание должно быть позже начала')
    with store.transaction() as con:
        session = con.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone()
        if session is None:
            raise ValueError('Занятие не найдено')
        if con.execute("SELECT 1 FROM queue_entries WHERE session_id=? AND (called_at IS NOT NULL OR status IN ('defended','failed','no_show'))", (session_id,)).fetchone():
            raise QueueConflict('Приём уже начат: создайте дополнительное занятие вместо переноса истории')
        con.execute('''UPDATE sessions SET opens_at=?,starts_at=?,ends_at=?,expires_at=?,local_date=?,
            enrollment_status='scheduled',schedule_revision=schedule_revision+1 WHERE session_id=?''',
            (min(now, starts_at), starts_at, ends_at, ends_at, local_date, session_id))
        con.execute("UPDATE queue_entries SET status='expired',closed_at=? WHERE session_id=? AND status='waiting'", (now, session_id))
        con.execute('UPDATE session_participation SET frozen_at=NULL WHERE session_id=?', (session_id,))
        # Старый снимок остаётся в истории; новое занятие использует новую ревизию.
        _event(con, session_id, 'schedule_changed', now, actor=actor,
               details={'old_start': session['starts_at'], 'starts_at': starts_at, 'ends_at': ends_at})
        con.execute('UPDATE queue_reconciliations SET due_at=?,done_at=NULL,checked_at=NULL,claimed_at=NULL WHERE session_id=?', (min(ends_at + 600, service_ends_at(dict(session, ends_at=ends_at))), session_id))


def cancel_session(store, session_id, *, now, actor):
    with store.transaction() as con:
        session = con.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone()
        if session is None:
            raise ValueError('Занятие не найдено')
        if session['enrollment_status'] == 'cancelled':
            return
        if now >= service_ends_at(session):
            raise QueueClosed('День приёма уже завершён: история занятия не отменяется')
        entries = [dict(row) for row in con.execute("SELECT entry_id,status,version FROM queue_entries WHERE session_id=? AND status IN ('waiting','called')", (session_id,))]
        con.execute("UPDATE sessions SET enrollment_status='cancelled',schedule_revision=schedule_revision+1 WHERE session_id=?", (session_id,))
        con.execute("UPDATE queue_entries SET status='expired',closed_at=?,version=version+1 WHERE session_id=? AND status IN ('waiting','called')", (now, session_id))
        _event(con, session_id, 'cancelled', now, actor=actor,
               details={'previous_status': session['enrollment_status'], 'entries': entries})


def restore_session(store, session_id, *, now, actor):
    with store.transaction() as con:
        session = con.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone()
        if session is None:
            raise ValueError('Занятие не найдено')
        if session['enrollment_status'] != 'cancelled':
            return
        if now >= service_ends_at(session):
            raise QueueClosed('День приёма уже завершён: назначьте новое занятие')
        cancellation = con.execute("SELECT * FROM queue_events WHERE session_id=? AND kind='cancelled' ORDER BY event_id DESC LIMIT 1", (session_id,)).fetchone()
        details = json.loads(cancellation['details_json']) if cancellation else {}
        entries = details.get('entries')
        if entries is None and cancellation:
            # Совместимость с отменами старой версии, где не было снимка состояний.
            entries = [{'entry_id': row['entry_id'], 'status': 'called' if row['called_at'] else 'waiting', 'version': row['version']-1}
                       for row in con.execute("SELECT * FROM queue_entries WHERE session_id=? AND status='expired' AND closed_at=?", (session_id, cancellation['occurred_at']))]
        count = 0
        for old in entries or []:
            row = con.execute('SELECT e.*,c.status AS candidate_status FROM queue_entries e JOIN queue_candidates c USING(candidate_id) WHERE e.entry_id=? AND e.session_id=?', (old['entry_id'], session_id)).fetchone()
            if row is None or row['status'] != 'expired' or row['version'] != old['version']+1 or row['candidate_status'] != 'eligible':
                continue
            if con.execute("SELECT 1 FROM queue_entries WHERE course_id=? AND student_id=? AND status IN ('waiting','called')", (row['course_id'], row['student_id'])).fetchone():
                raise QueueConflict('У участника уже есть активная запись на другом занятии; завершите её перед восстановлением')
            con.execute('UPDATE queue_entries SET status=?,closed_at=NULL,version=version+1 WHERE entry_id=?', (old['status'], row['entry_id']))
            count += 1
        status = details.get('previous_status', 'scheduled')
        if status not in ('scheduled', 'open', 'closed'):
            status = 'scheduled'
        revision = session['schedule_revision'] + 1
        con.execute('UPDATE sessions SET enrollment_status=?,schedule_revision=? WHERE session_id=?', (status, revision, session_id))
        frozen = con.execute("SELECT details_json FROM queue_events WHERE session_id=? AND kind='roster_frozen' ORDER BY event_id DESC LIMIT 1", (session_id,)).fetchone()
        roster_restored = now >= session['starts_at'] and frozen is not None
        if roster_restored:
            snapshot = json.loads(frozen['details_json'])
            snapshot['session'] = dict(con.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone())
            snapshot['attendance_open'] = False
            _event(con, session_id, 'roster_frozen', now, actor=actor, details=snapshot)
        else:
            con.execute('UPDATE session_participation SET frozen_at=NULL WHERE session_id=?', (session_id,))
        con.execute('UPDATE queue_reconciliations SET due_at=?,done_at=NULL,checked_at=NULL,claimed_at=NULL,error=NULL WHERE session_id=?',
                    (min(session['ends_at']+600, service_ends_at(session)), session_id))
        _event(con, session_id, 'restored', now, actor=actor,
               details={'revision': revision, 'roster_restored': roster_restored, 'entries_restored': count})


def save_schedule(store, course_id, group_id, config, *, now):
    from .schedule import validate_schedule
    validate_schedule(config)
    with store.transaction() as con:
        row = con.execute('SELECT schedule_json FROM queue_settings WHERE course_id=?', (course_id,)).fetchone()
        configs = json.loads(row[0]) if row else {}
        configs[group_id] = config
        con.execute("INSERT INTO queue_settings(course_id,schedule_json,updated_at) VALUES (?,?,?) ON CONFLICT(course_id) DO UPDATE SET schedule_json=excluded.schedule_json,updated_at=excluded.updated_at", (course_id, json.dumps(configs, ensure_ascii=False), now))


def set_enrollment(store, session_id, *, closed, now, actor):
    with store.transaction() as con:
        session = con.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone()
        if not session or session['enrollment_status'] == 'cancelled' or now >= session['starts_at']:
            raise QueueClosed('Посещение можно менять только до начала неотменённого занятия')
        con.execute('UPDATE sessions SET enrollment_status=? WHERE session_id=?', ('closed' if closed else 'scheduled',session_id))
        _event(con,session_id,'enrollment_closed' if closed else 'enrollment_opened',now,actor=actor)


def promote_next_labs(store, session_id, *, now, actor):
    """Повтор синхронизации после сетевого сбоя не теряет следующую защиту."""
    with store.transaction() as con:
        session=con.execute('SELECT * FROM sessions WHERE session_id=?',(session_id,)).fetchone()
        if not session or not session['starts_at'] <= now < session['ends_at'] or session['enrollment_status']=='cancelled':
            return
        candidates=con.execute("""SELECT DISTINCT c.candidate_id FROM queue_candidates c
            JOIN queue_entries old ON old.student_id=c.student_id AND old.course_id=c.course_id
            WHERE old.session_id=? AND old.status='defended' AND old.rounds_managed=0 AND c.status='eligible'
            AND c.group_id=? AND c.lab_order>old.lab_order
            AND NOT EXISTS(SELECT 1 FROM queue_entries e WHERE e.session_id=? AND e.candidate_id=c.candidate_id)
            AND NOT EXISTS(SELECT 1 FROM queue_entries e WHERE e.course_id=c.course_id AND e.student_id=c.student_id AND e.status IN ('waiting','called'))
            ORDER BY c.candidate_id""",(session_id,session['group_id'],session_id)).fetchall()
        for row in candidates:
            candidate = con.execute('SELECT student_id FROM queue_candidates WHERE candidate_id=?', (row['candidate_id'],)).fetchone()
            if not _confirmed(con, session_id, candidate['student_id']):
                continue
            add_to_tail(store,session_id,row['candidate_id'],now=now,actor=actor)


def correct_result(store, entry_id, outcome, *, now, actor):
    if outcome not in ('defended', 'failed', 'no_show'):
        raise ValueError('Неизвестный результат')
    with store.transaction() as con:
        row = con.execute('SELECT * FROM queue_entries WHERE entry_id=?', (entry_id,)).fetchone()
        if row is None or row['status'] not in ('defended', 'failed', 'no_show'):
            raise QueueConflict('Исправить можно только завершённую защиту или неявку')
        if row['status'] == outcome:
            return
        if con.execute("SELECT 1 FROM queue_entries WHERE course_id=? AND student_id=? AND entry_id>? AND (called_at IS NOT NULL OR status IN ('defended','failed','no_show'))", (row['course_id'],row['student_id'],entry_id)).fetchone():
            raise QueueConflict('Уже началась следующая попытка. Сначала исправьте её результат.')
        pending = con.execute("SELECT entry_id,candidate_id,session_id FROM queue_entries WHERE course_id=? AND student_id=? AND entry_id>? AND status='waiting'", (row['course_id'],row['student_id'],entry_id)).fetchall()
        for child in pending:
            con.execute("UPDATE queue_entries SET status='withdrawn',closed_at=?,version=version+1 WHERE entry_id=?", (now,child['entry_id']))
            _event(con, child['session_id'], 'withdrawn', now, actor=actor, entry_id=child['entry_id'],candidate_id=child['candidate_id'],details={'correction_of':entry_id})
        if row['status'] == 'defended':
            con.execute("UPDATE queue_candidates SET status='inactive' WHERE course_id=? AND student_id=? AND status='eligible'", (row['course_id'],row['student_id']))
            con.execute("UPDATE queue_candidates SET status='eligible' WHERE candidate_id=?", (row['candidate_id'],))
        con.execute('UPDATE queue_entries SET status=?,version=version+1 WHERE entry_id=?', (outcome,entry_id))
        _event(con, row['session_id'], 'result_corrected', now, actor=actor,entry_id=entry_id,candidate_id=row['candidate_id'],details={'from':row['status'],'to':outcome})
        if outcome == 'defended':
            con.execute("UPDATE queue_candidates SET status='defended' WHERE candidate_id=?", (row['candidate_id'],))
        elif outcome == 'failed':
            session = con.execute('SELECT * FROM sessions WHERE session_id=?', (row['session_id'],)).fetchone()
            if now < session['ends_at'] and _confirmed(con,row['session_id'],row['student_id']):
                candidate = con.execute('SELECT * FROM queue_candidates WHERE candidate_id=?', (row['candidate_id'],)).fetchone()
                _insert_attempt(con,candidate,session,request_key=f'correction:{entry_id}:{row["version"]}',repeat=True,now=now)
