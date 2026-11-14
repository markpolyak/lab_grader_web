"""Student enrollment pauses through the current class and its ten-minute reconciliation window."""
from .operations import teacher_view
from .runtime import queue_view


def current_session(store, course_id, group_id, *, now):
    with store.connection() as con:
        row=con.execute("""SELECT * FROM sessions WHERE course_id=? AND group_id=? AND starts_at<=?
            AND ends_at+600>? AND enrollment_status!='cancelled' ORDER BY starts_at DESC,session_id LIMIT 1""",
            (course_id,group_id,now,now)).fetchone()
    return dict(row) if row else None


def student_view(store, course_id, group_id, course, *, deadlines_available, now, session_id=None):
    from .runtime import ensure_daily_session
    from .operations import effective_course
    course = effective_course(store, course_id, course)
    if session_id is None:
        ensure_daily_session(store, course_id, group_id, course, now=now)
    current=current_session(store,course_id,group_id,now=now)
    if current and (session_id is None or session_id==current['session_id']):
        view=teacher_view(store,course_id,group_id,course,deadlines_available=deadlines_available,now=now,session_id=current['session_id'])
        # Public rendering shares the queue order and statuses, never the command journal or catalog.
        def public_row(row):
            item={key:row.get(key) for key in ('row_key','full_name','github','lab_key','priority','is_repeat')}
            if row.get('attempt'):
                item['attempt']={key:row['attempt'].get(key) for key in ('status','is_repeat')}
            return item
        if view.get('continuous_enrollment') and now < current['ends_at']:
            return dict(session=view['session'], mode=view['mode'], mode_locked=view['mode_locked'],
                        attendance_open=view['attendance_open'], read_only=False,
                        continuous_enrollment=True, enrollment_opens_at=view['enrollment_opens_at'],
                        entries=[{key:row.get(key) for key in ('candidate_id','student_id','full_name','github','ready','will_attend','answered','position','state','priority')} for row in view['entries']],
                        round_entries=[public_row(row) for row in view['round_entries']],
                        absent_entries=[public_row(row) for row in view['absent_entries']])
        return dict(session=view['session'],mode=view['mode'],mode_locked=view['mode_locked'],
                    attendance_open=False,read_only=True,enrollment_opens_at=current['ends_at']+600,
                    entries=[],round_entries=[public_row(row) for row in view['round_entries']],
                    absent_entries=[public_row(row) for row in view['absent_entries']])
    view=queue_view(store,course_id,group_id,course,deadlines_available=deadlines_available,now=now,session_id=session_id)
    view['read_only']=False
    if current:
        view['attendance_open']=False
        view['enrollment_opens_at']=current['ends_at']+600
    return view
