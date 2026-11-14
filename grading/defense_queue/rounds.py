"""Журналируемые действия над отдельными попытками и списком отсутствующих."""
import json
from . import operations as op
from .storage import QueueClosed, QueueConflict
from .timing import service_ends_at


def preferences(store, course_id):
    with store.connection() as con:
        row = con.execute('SELECT auto_repeat,undo_seconds FROM queue_settings WHERE course_id=?', (course_id,)).fetchone()
    return dict(auto_repeat=bool(row['auto_repeat']), undo_seconds=row['undo_seconds']) if row else dict(auto_repeat=True, undo_seconds=5)


def save_preferences(store, course_id, *, auto_repeat, undo_seconds, now):
    if type(auto_repeat) is not bool or type(undo_seconds) is not int or not 1 <= undo_seconds <= 10:
        raise ValueError('Время отмены должно быть целым числом от 1 до 10 секунд')
    with store.transaction() as con:
        con.execute('''INSERT INTO queue_settings(course_id,auto_repeat,undo_seconds,updated_at) VALUES (?,?,?,?)
            ON CONFLICT(course_id) DO UPDATE SET auto_repeat=excluded.auto_repeat,undo_seconds=excluded.undo_seconds,updated_at=excluded.updated_at''',
            (course_id,int(auto_repeat),undo_seconds,now))


def decorate_view(store, view, *, now):
    session = view['session']
    attempts = view['attempts']
    latest = {row['student_id']:row for row in attempts if row['status'] != 'withdrawn'}
    active = {row['student_id'] for row in attempts if row['status'] in ('waiting','called')}
    with store.connection() as con:
        students = {r['github_login']:dict(r) for r in con.execute('SELECT * FROM students')}
        public_logins = {r['student_id']:r['github_login'] or '' for r in con.execute('SELECT student_id,github_login FROM queue_members WHERE course_id=? AND group_id=?',(session['course_id'],session['group_id']))}
        catalog = [dict(row) for row in con.execute('''SELECT s.student_id,s.full_name,s.github_login,
            (SELECT c.candidate_id FROM queue_candidates c WHERE c.student_id=s.student_id AND c.course_id=? AND c.group_id=? AND c.status='eligible') AS candidate_id
            FROM students s WHERE EXISTS (SELECT 1 FROM queue_candidates c WHERE c.student_id=s.student_id AND c.course_id=? AND c.group_id=?) OR EXISTS (SELECT 1 FROM queue_members m WHERE m.student_id=s.student_id AND m.course_id=? AND m.group_id=?) ORDER BY s.full_name''',
            (session['course_id'],session['group_id'],session['course_id'],session['group_id'],session['course_id'],session['group_id']))]
        for item in catalog:
            item['github_login'] = public_logins.get(item['student_id'],item['github_login'])
    rows = []
    for attempt in attempts:
        if attempt['status'] in ('waiting','called','defended','failed'):
            rows.append(dict(row_key=f"entry-{attempt['entry_id']}",student_id=attempt['student_id'],
                full_name=attempt['full_name_snapshot'],github=public_logins.get(attempt['student_id'],attempt['github_snapshot']),lab_key=attempt['lab_key'],
                priority=attempt['priority'],attempt=attempt,is_repeat=bool(attempt['is_repeat'])))
    absent = []
    for row in view['entries']:
        sid = row.get('student_id') or students[row['github'].lower()]['student_id']
        last = latest.get(sid)
        missing = sid not in active and (last['status']=='no_show' if last else not row['will_attend'])
        if missing:
            absent.append(dict(row,row_key=f'absent-{sid}',student_id=sid,attempt=last if last and last['status']=='no_show' else None))
        elif now < session['starts_at'] and row['will_attend'] and not any(r['student_id']==sid for r in rows):
            rows.append(dict(row,row_key=f"candidate-{row['candidate_id']}",student_id=sid,is_repeat=False))
    rows.sort(key=lambda row:(row['is_repeat'],(row.get('attempt') or {}).get('entry_id',row.get('position') or 0)))
    return dict(view,round_entries=rows,absent_entries=absent,student_catalog=catalog,
                preferences=preferences(store,session['course_id']))


def _snapshot(con, course_id, student_id):
    return dict(entries=[dict(r) for r in con.execute('SELECT * FROM queue_entries WHERE course_id=? AND student_id=? ORDER BY entry_id',(course_id,student_id))],
                candidates=[dict(r) for r in con.execute('SELECT candidate_id,status FROM queue_candidates WHERE course_id=? AND student_id=?',(course_id,student_id))])


def _undo_info(row, now):
    return dict(command_id=row['command_id'],action=row['action'],expires_at=row['expires_at'],remaining_ms=max(0,int((row['expires_at']-now)*1000)))


def execute(store, session_id, action, *, now, actor, request_id, entry_id=None, student_id=None):
    if not isinstance(request_id,str) or not 1 <= len(request_id) <= 128:
        raise ValueError('Нужен идентификатор действия')
    with store.transaction() as con:
        previous=con.execute('SELECT * FROM queue_undo WHERE session_id=? AND actor=? AND request_id=?',(session_id,actor,request_id)).fetchone()
        if previous:
            if previous['action'] != action:
                raise QueueConflict('Идентификатор уже использован другим действием')
            return _undo_info(previous,now)
        session=con.execute('SELECT * FROM sessions WHERE session_id=?',(session_id,)).fetchone()
        if not session or now < session['starts_at'] or now >= service_ends_at(session) or session['enrollment_status']=='cancelled':
            raise QueueClosed('Действия доступны после начала неотменённого занятия до конца дня')
        entry=None
        if entry_id is not None:
            entry=con.execute('SELECT * FROM queue_entries WHERE entry_id=? AND session_id=? AND voided_at IS NULL',(entry_id,session_id)).fetchone()
            if entry is None: raise ValueError('Запись не относится к выбранному занятию')
            student_id=entry['student_id']
        if student_id is None: raise ValueError('Выберите студента')
        before=_snapshot(con,session['course_id'],student_id)
        config=preferences(store,session['course_id'])
        if action in ('call','uncall'):
            if entry is None or entry['status'] != ('waiting' if action=='call' else 'called'):
                raise QueueConflict('Состояние строки изменилось. Обновите список.')
            con.execute('UPDATE queue_entries SET status=?,called_at=?,rounds_managed=1,version=version+1 WHERE entry_id=?',
                        ('called' if action=='call' else 'waiting',now if action=='call' else None,entry_id))
            op._event(con,session_id,'called' if action=='call' else 'call_cancelled',now,actor=actor,entry_id=entry_id,candidate_id=entry['candidate_id'])
        elif action in ('pass','fail','absent'):
            if entry is None: raise ValueError('Выберите попытку защиты')
            if entry['status'] not in (('waiting','called') if action=='absent' else ('called',)):
                raise QueueConflict('Запись уже обработана. Обновите список.')
            con.execute('UPDATE queue_entries SET rounds_managed=1 WHERE entry_id=?',(entry_id,))
            op.record_result(store,entry_id,{'pass':'defended','fail':'failed','absent':'no_show'}[action],now=now,actor=actor,auto_retry=config['auto_repeat'])
        elif action in ('arrived','add_student'):
            if now >= session['ends_at']: raise QueueClosed('Занятие закончилось: новую попытку добавить нельзя')
            if con.execute("SELECT 1 FROM queue_entries WHERE course_id=? AND student_id=? AND status IN ('waiting','called')",(session['course_id'],student_id)).fetchone():
                raise QueueConflict('Нельзя добавить студента, который уже есть в очереди')
            candidate=con.execute("SELECT * FROM queue_candidates WHERE course_id=? AND group_id=? AND student_id=? AND status='eligible'",(session['course_id'],session['group_id'],student_id)).fetchone()
            if candidate is None:
                # The prompt explicitly permits another manual row after a completed defense.
                candidate=con.execute('''SELECT c.* FROM queue_entries e JOIN queue_candidates c USING(candidate_id)
                    WHERE e.session_id=? AND e.student_id=? AND e.status='defended' AND e.voided_at IS NULL
                    ORDER BY e.entry_id DESC LIMIT 1''',(session_id,student_id)).fetchone()
            if candidate is None: raise QueueConflict('У студента нет допущенной работы этой группы')
            created=op._insert_attempt(con,candidate,session,request_key=f'round:{session_id}:{actor}:{request_id}',repeat=True,now=now)
            op._event(con,session_id,'arrived' if action=='arrived' else 'added_to_tail',now,actor=actor,entry_id=created,candidate_id=candidate['candidate_id'])
        else: raise ValueError('Неизвестное действие')
        old_ids={r['entry_id'] for r in before['entries']}
        con.execute('UPDATE queue_entries SET rounds_managed=1 WHERE course_id=? AND student_id=? AND entry_id NOT IN (SELECT value FROM json_each(?))',
                    (session['course_id'],student_id,json.dumps(list(old_ids))))
        after=_snapshot(con,session['course_id'],student_id)
        cursor=con.execute('''INSERT INTO queue_undo(session_id,student_id,actor,request_id,action,before_json,after_json,expires_at)
            VALUES (?,?,?,?,?,?,?,?)''',(session_id,student_id,actor,request_id,action,json.dumps(before),json.dumps(after),now+config['undo_seconds']))
        return dict(_undo_info(con.execute('SELECT * FROM queue_undo WHERE command_id=?',(cursor.lastrowid,)).fetchone(),now),_new=True)


def undo(store, session_id, command_id, *, now, actor):
    with store.transaction() as con:
        command=con.execute('SELECT * FROM queue_undo WHERE command_id=? AND session_id=? AND actor=?',(command_id,session_id,actor)).fetchone()
        if command is None: raise ValueError('Действие не найдено')
        if command['undone_at'] is not None: return
        if now >= command['expires_at']: raise QueueConflict('Время отмены действия истекло')
        session=con.execute('SELECT * FROM sessions WHERE session_id=?',(session_id,)).fetchone()
        if session['enrollment_status']=='cancelled' or now >= service_ends_at(session): raise QueueClosed('Занятие отменено или день завершён')
        if con.execute('SELECT 1 FROM queue_undo WHERE session_id IN (SELECT session_id FROM sessions WHERE course_id=?) AND student_id=? AND command_id>? AND undone_at IS NULL',
                        (session['course_id'],command['student_id'],command_id)).fetchone():
            raise QueueConflict('После этого действия состояние студента уже изменилось')
        before,after=json.loads(command['before_json']),json.loads(command['after_json'])
        current=_snapshot(con,session['course_id'],command['student_id'])
        if current['entries'] != after['entries']: raise QueueConflict('Запись уже изменена другим действием или сверкой')
        # Free active slots before restoring earlier active rows. Events remain append-only.
        con.execute("UPDATE queue_entries SET status='withdrawn',voided_at=?,closed_at=? WHERE course_id=? AND student_id=? AND entry_id NOT IN (SELECT value FROM json_each(?))",(now,now,session['course_id'],command['student_id'],json.dumps([r['entry_id'] for r in before['entries']])))
        for old in before['entries']:
            values={k:v for k,v in old.items() if k!='entry_id'}
            con.execute('UPDATE queue_entries SET '+','.join(f'{key}=?' for key in values)+' WHERE entry_id=?',(*values.values(),old['entry_id']))
        for old in before['candidates']:
            following=next((c for c in after['candidates'] if c['candidate_id']==old['candidate_id']),old)
            if old['status'] != following['status']:
                if old['status']=='eligible':
                    con.execute("UPDATE queue_candidates SET status='inactive' WHERE course_id=? AND student_id=? AND status='eligible'",(session['course_id'],command['student_id']))
                con.execute('UPDATE queue_candidates SET status=? WHERE candidate_id=?',(old['status'],old['candidate_id']))
        con.execute('UPDATE queue_undo SET undone_at=? WHERE command_id=?',(now,command_id))
        old_versions={row['entry_id']:row['version'] for row in before['entries']}
        changed=next((row for row in after['entries'] if old_versions.get(row['entry_id'])!=row['version']),None)
        op._event(con,session_id,'action_undone',now,actor=actor,
                  entry_id=changed['entry_id'] if changed else None,candidate_id=changed['candidate_id'] if changed else None,
                  details={'command_id':command_id,'action':command['action']})
