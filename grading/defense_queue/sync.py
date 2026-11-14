"""Синхронизация прочитанной таблицы: сетевой доступ и запись оценок отсутствуют."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .sheet_state import select_queue_lab, parse_lab_cell, read_deadline
from .roster import member_student, initialize_session


def course_timezone(course):
    value = course.get('queue', {}).get('timezone', course.get('timezone', 'Europe/Moscow'))
    offset = re.fullmatch(r'UTC([+-])(\d{1,2})', value)
    if offset:
        value = f"Etc/GMT{'-' if offset[1] == '+' else '+'}{int(offset[2])}"
    ZoneInfo(value)
    return value


def parse_queue_deadline(value, course, parse):
    parsed = parse(value)
    if parsed is None:
        try: parsed = datetime.fromisoformat(value)
        except (ValueError, TypeError): return None
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(course_timezone(course)))
    return parsed


def sync_group(store, course_id, group_id, course, grid, *, parse_deadline, now):
    with store.transaction():
        return _sync_group(store, course_id, group_id, course, grid, parse_deadline=parse_deadline, now=now)


def _sync_group(store, course_id, group_id, course, grid, *, parse_deadline, now):
    def cell(row, col):
        return grid[row - 1][col - 1].strip() if row <= len(grid) and col <= len(grid[row - 1]) else ''

    if len(grid) < 2 or 'GitHub' not in grid[0]:
        raise ValueError('В таблице отсутствует заголовок GitHub или строка лабораторных')
    labs = {str(key): value for key, value in course.get('labs', {}).items()}
    queue = course.get('queue', {})
    configured_order = queue.get('required_labs_by_group', {}).get(group_id, queue.get('required_labs', list(labs)))
    order = [str(key) for key in configured_order]
    lab_positions = {key: index+1 for index, key in enumerate(labs)}
    if not order or any(key not in labs for key in order) or len(set(order)) != len(order):
        raise ValueError('Некорректный порядок обязательных работ')
    columns = {}
    for key, lab in labs.items():
        matches = [index + 1 for index, value in enumerate(grid[1]) if value == lab.get('short-name')]
        if len(matches) == 1:
            columns[key] = matches[0]
        elif key in order:
            raise ValueError(f'Не найден однозначный столбец работы {key}')
    github_col = grid[0].index('GitHub') + 1
    name_col = course.get('google', {}).get('student-name-column', 1) + 1
    students = []
    logins = set()
    for row in range(3, len(grid) + 1):
        name, login = cell(row, name_col), cell(row, github_col).lower()
        if not name:
            continue
        if login and login in logins:
            raise ValueError('GitHub повторяется в таблице группы')
        logins.add(login)
        students.append((name, login, {key: cell(row, col) for key, col in columns.items()}))
    zone = course_timezone(course)
    deadlines = {}
    sheet_deadlines = {}
    markers = queue.get('no_deadline_markers', [])
    if not isinstance(markers, list) or any(not isinstance(marker, str) or not marker.strip() for marker in markers):
        raise ValueError('no_deadline_markers: нужен список непустых строк')
    for key in labs:
        raw = cell(queue.get('deadline_row', 1), columns[key]) if key in columns else ''
        state = read_deadline(raw, parse=parse_deadline, explicitly_no_deadline=raw.strip() in markers)
        sheet_deadlines[key] = {'available': state.available, 'value': int(state.value.timestamp()) if state.value else None,
                               'source': state.source, 'raw': raw}
        if key in order:
            deadlines[key] = state
    overrides = queue.get('deadlines', {})
    for key in order:
        if key in overrides:
            value = overrides[key]
            deadlines[key] = read_deadline('', parse=parse_deadline,
                                          manual=parse_deadline(str(value)) if value is not None else None,
                                          explicitly_no_deadline=value is None)
            if value is not None and deadlines[key].value is None:
                raise ValueError(f'Не удалось разобрать ручной срок работы {key}')
    store.configure_group(course_id, group_id, title=course.get('name', course_id), timezone=zone)
    imported = 0
    seen = set()
    occurrences = {}
    for name, login, cells in students:
        occurrence = occurrences.get(name, 0)
        occurrences[name] = occurrence + 1
        student_id = member_student(store, course_id, group_id, name, login, now=now, occurrence=occurrence)
        seen.add(student_id)
        cells = {key: cells.get(key, '') for key in order}
        with store.transaction() as con:
            for key, value in cells.items():
                if not parse_lab_cell(value).defended:
                    continue
                previous = con.execute("SELECT candidate_id FROM queue_candidates WHERE course_id=? AND student_id=? AND lab_key=? AND status='eligible'", (course_id, student_id, key)).fetchall()
                for candidate in previous:
                    con.execute("UPDATE queue_candidates SET status='defended' WHERE candidate_id=?", (candidate[0],))
                    store.event(con, candidate[0], 'sheet_defended', now)
                if not con.execute("SELECT 1 FROM queue_candidates WHERE course_id=? AND student_id=? AND lab_key=? AND status='defended'", (course_id,student_id,key)).fetchone():
                    deadline = deadlines[key]
                    cursor = con.execute("INSERT INTO queue_candidates(course_id,group_id,student_id,lab_key,lab_order,deadline_at,deadline_available,qualified_at,status) VALUES (?,?,?,?,?,?,?,?,'defended')", (course_id,group_id,student_id,key,lab_positions[key],int(deadline.value.timestamp()) if deadline.value else None,int(deadline.available),now))
                    store.event(con,cursor.lastrowid,'sheet_defended',now)
            for key, value in cells.items():
                if parse_lab_cell(value).defended:
                    for candidate in con.execute("SELECT candidate_id FROM queue_candidates WHERE course_id=? AND student_id=? AND lab_key=? AND status='defended'", (course_id,student_id,key)):
                        if not con.execute("SELECT 1 FROM queue_events WHERE candidate_id=? AND kind='sheet_defended'", (candidate[0],)).fetchone():
                            store.event(con,candidate[0],'sheet_defended',now)
            defended = frozenset(row[0] for row in con.execute("SELECT lab_key FROM queue_candidates WHERE course_id=? AND student_id=? AND status='defended'", (course_id, student_id)))
            key, target, completed = select_queue_lab(order, cells, defended_in_history=defended)
            old = con.execute("SELECT candidate_id,lab_key,group_id FROM queue_candidates WHERE course_id=? AND student_id=? AND status='eligible'", (course_id, student_id)).fetchone()
            if old and (old['lab_key'] != key or old['group_id'] != group_id):
                con.execute("UPDATE queue_candidates SET status='inactive' WHERE candidate_id=?", (old['candidate_id'],))
                store.event(con, old['candidate_id'], 'eligibility_changed', now)
        if key is not None:
            deadline = deadlines[key]
            store.qualify(course_id, group_id, student_id, key, lab_positions[key],
                          deadline_at=int(deadline.value.timestamp()) if deadline.value else None,
                          deadline_available=deadline.available, now=now)
            imported += 1
        deadline = deadlines.get(target)
        with store.transaction() as con:
            con.execute('''INSERT INTO queue_members(course_id,group_id,student_id,full_name,github_login,
                admitted_lab_key,target_lab_key,target_order,deadline_at,deadline_available,completed,queue_state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(course_id,group_id,student_id) DO UPDATE SET
                full_name=excluded.full_name,github_login=excluded.github_login,admitted_lab_key=excluded.admitted_lab_key,
                target_lab_key=excluded.target_lab_key,target_order=excluded.target_order,deadline_at=excluded.deadline_at,
                deadline_available=excluded.deadline_available,completed=excluded.completed,queue_state=excluded.queue_state,updated_at=excluded.updated_at''',
                (course_id,group_id,student_id,name,login or None,key,target,lab_positions.get(target),
                 int(deadline.value.timestamp()) if deadline and deadline.value else None,
                 int(deadline.available) if deadline else 1,int(completed),
                 'not_attending' if completed else 'eligible' if key else 'not_ready',now,now))
    with store.transaction() as con:
        for row in con.execute("SELECT c.candidate_id,c.student_id FROM queue_candidates c WHERE c.course_id=? AND c.group_id=? AND c.status='eligible'", (course_id,group_id)).fetchall():
            if row['student_id'] not in seen:
                con.execute("UPDATE queue_candidates SET status='inactive' WHERE candidate_id=?", (row['candidate_id'],))
                store.event(con,row['candidate_id'],'eligibility_changed',now)
        for member in con.execute('SELECT student_id FROM queue_members WHERE course_id=? AND group_id=?',(course_id,group_id)).fetchall():
            if member['student_id'] not in seen:
                con.execute('DELETE FROM queue_members WHERE course_id=? AND group_id=? AND student_id=?',(course_id,group_id,member['student_id']))
        for session in con.execute('SELECT session_id FROM sessions WHERE course_id=? AND group_id=?',(course_id,group_id)).fetchall():
            initialize_session(con, session['session_id'], now=now)
        # An old waiting place must not retain the priority of a retired lab.
        retired = con.execute("""SELECT e.* FROM queue_entries e JOIN queue_candidates c USING(candidate_id)
            WHERE e.course_id=? AND e.group_id=? AND e.status='waiting' AND e.voided_at IS NULL
            AND c.status='inactive'""",(course_id,group_id)).fetchall()
        for entry in retired:
            con.execute("UPDATE queue_entries SET status='withdrawn',closed_at=?,voided_at=?,version=version+1 WHERE entry_id=?",(now,now,entry['entry_id']))
            con.execute('''INSERT INTO queue_events(session_id,entry_id,candidate_id,actor_kind,actor_key,kind,details_json,occurred_at)
                VALUES (?,?,?,'system','sheet-sync','eligibility_changed','{}',?)''',
                (entry['session_id'],entry['entry_id'],entry['candidate_id'],now))
            session=con.execute('SELECT * FROM sessions WHERE session_id=?',(entry['session_id'],)).fetchone()
            replacement=con.execute("SELECT * FROM queue_candidates WHERE course_id=? AND student_id=? AND status='eligible'",(course_id,entry['student_id'])).fetchone()
            if replacement and session['starts_at'] <= now < session['ends_at'] and session['enrollment_status']!='cancelled':
                from .operations import _insert_attempt
                _insert_attempt(con,replacement,session,request_key=f"retarget:{entry['entry_id']}:{replacement['candidate_id']}",repeat=True,now=now)
    return {'students': len(students), 'eligible': imported, 'deadlines_available': all(d.available for d in deadlines.values()),
            'sheet_deadlines': sheet_deadlines, 'timezone': zone,
            'deadlines': {key: {'available': d.available, 'value': int(d.value.timestamp()) if d.value else None} for key, d in deadlines.items()}}
