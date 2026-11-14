"""Group membership and persistent session order, independent of lab admission."""
import hashlib

from .storage import initialize_session


def member_student(store, course_id, group_id, name, login, *, now, occurrence=0):
    """A sheet row without GitHub keeps its identity when registration supplies it."""
    digest = hashlib.sha256(f'{course_id}\0{group_id}\0{name}\0{occurrence}'.encode()).hexdigest()
    placeholder = '__sheet__' + digest
    with store.transaction() as con:
        old = con.execute('''SELECT m.student_id FROM queue_members m JOIN students s USING(student_id)
            WHERE m.course_id=? AND m.group_id=? AND s.github_login=?''',
            (course_id, group_id, placeholder)).fetchone()
        if not login:
            return old['student_id'] if old else store.student(name, placeholder, now=now)
        real = con.execute('SELECT student_id FROM students WHERE github_login=?', (login,)).fetchone()
        if old and real is None:
            con.execute('UPDATE students SET github_login=?,full_name=? WHERE student_id=?',
                        (login, name, old['student_id']))
            return old['student_id']
        student_id = store.student(name, login, now=now)
        if old and old['student_id'] != student_id:
            # The login may already exist from another group/course. Keep this group's allotted slot.
            slots = con.execute('''SELECT r.* FROM session_roster r JOIN sessions s USING(session_id)
                WHERE r.student_id=? AND s.course_id=? AND s.group_id=?''',
                (old['student_id'], course_id, group_id)).fetchall()
            for slot in slots:
                con.execute('DELETE FROM session_roster WHERE session_id=? AND student_id=?',
                            (slot['session_id'], student_id))
                con.execute('UPDATE session_roster SET student_id=? WHERE session_id=? AND student_id=?',
                            (student_id, slot['session_id'], old['student_id']))
        return student_id


def ordering_rows(store, session, *, now, mode):
    """Return None for manually populated stores without an imported group roster."""
    with store.transaction() as con:
        members = con.execute('''SELECT * FROM queue_members WHERE course_id=? AND group_id=?
            ORDER BY created_at,student_id''', (session['course_id'], session['group_id'])).fetchall()
        if not members:
            return None
        initialize_session(con, session['session_id'], now=now)
        result = []
        for member in members:
            if mode in ('priority','fifo') and member['completed']:
                continue
            ordering = con.execute('SELECT * FROM session_roster WHERE session_id=? AND student_id=?',
                                   (session['session_id'], member['student_id'])).fetchone()
            candidate = con.execute('''SELECT * FROM queue_candidates
                WHERE course_id=? AND group_id=? AND student_id=? AND status='eligible' ''',
                (session['course_id'], session['group_id'], member['student_id'])).fetchone()
            admitted = candidate is not None and candidate['lab_key'] == member['admitted_lab_key']
            if admitted and session['enrollment_mode'] == 'legacy' and now >= session['starts_at'] and candidate['qualified_at'] > session['starts_at']:
                admitted = False
            participation = con.execute('''SELECT * FROM session_participation
                WHERE session_id=? AND student_id=? AND attendance_answered=1
                ORDER BY updated_at DESC,candidate_id DESC LIMIT 1''',
                (session['session_id'], member['student_id'])).fetchone()
            result.append(dict(
                member, candidate_id=candidate['candidate_id'] if admitted else -member['student_id'],
                lab_key=member['admitted_lab_key'] if admitted else member['target_lab_key'],
                github_login=member['github_login'] or '',
                qualified_at=candidate['qualified_at'] if admitted else member['created_at'],
                order_at=ordering['ordinal'], random_key=ordering['random_key'],
                will_attend=bool(admitted and participation and participation['will_attend']),
                attendance_answered=bool(participation),
                attendance_at=participation['updated_at'] if participation else None,
                frozen_at=session['starts_at'] if now >= session['starts_at'] else None,
                ready=admitted, virtual_priority=not admitted,
            ))
        return result
