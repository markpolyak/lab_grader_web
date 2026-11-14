"""Фиксация занятий и возобновляемая сверка. Задания живут в SQLite."""
import json
import logging
import threading
import time

from .operations import effective_course, teacher_view, promote_next_labs
from .schedule import refresh_schedule
from .runtime import saved_deadlines_available
from .timing import service_ends_at

logger = logging.getLogger(__name__)


def reconcile(store, session_id, *, now, sync):
    """Lease исключает параллельный запуск, сбой сети оставляет задание для повтора."""
    with store.transaction() as con:
        task = con.execute('SELECT * FROM queue_reconciliations WHERE session_id=?', (session_id,)).fetchone()
        if not task or task['done_at'] or task['due_at'] > now or task['claimed_at'] is not None and task['claimed_at'] > now-300:
            return False
        session = con.execute('SELECT * FROM sessions WHERE session_id=?',(session_id,)).fetchone()
        revision = session['schedule_revision']
        final_at = service_ends_at(session)
        final = now >= final_at
        attempt = task['attempts'] + 1
        con.execute('UPDATE queue_reconciliations SET claimed_at=?,attempts=attempts+1 WHERE session_id=?', (now,session_id))
    try:
        sync()  # Принудительное чтение Sheets, без транзакции и без записи оценок.
        with store.transaction() as con:
            current = con.execute('SELECT r.claimed_at,r.attempts,s.schedule_revision,s.enrollment_status FROM queue_reconciliations r JOIN sessions s USING(session_id) WHERE session_id=?',(session_id,)).fetchone()
            if current['claimed_at'] != now or current['attempts'] != attempt or current['schedule_revision'] != revision or current['enrollment_status'] == 'cancelled':
                return False
            rows = con.execute("SELECT e.*,c.status AS candidate_status FROM queue_entries e JOIN queue_candidates c USING(candidate_id) WHERE e.session_id=? AND e.status IN ('waiting','called')", (session_id,)).fetchall()
            for row in rows:
                if not final and row['candidate_status'] != 'defended':
                    continue
                status = 'defended' if row['candidate_status'] == 'defended' else 'expired'
                con.execute('UPDATE queue_entries SET status=?,closed_at=?,version=version+1 WHERE entry_id=?', (status,now,row['entry_id']))
                con.execute("INSERT INTO queue_events(session_id,entry_id,candidate_id,actor_kind,actor_key,kind,occurred_at) VALUES (?,?,?,'system','reconciliation',?,?)", (session_id,row['entry_id'],row['candidate_id'],'sheet_defended' if status=='defended' else 'session_finished',now))
            if final:
                con.execute("UPDATE sessions SET enrollment_status='closed' WHERE session_id=? AND enrollment_status!='cancelled'", (session_id,))
                con.execute('UPDATE queue_reconciliations SET done_at=?,claimed_at=NULL,error=NULL WHERE session_id=?', (now,session_id))
            else:
                con.execute('UPDATE queue_reconciliations SET checked_at=?,due_at=?,claimed_at=NULL,error=NULL WHERE session_id=?', (now,final_at,session_id))
        return True
    except Exception as error:
        with store.transaction() as con:
            con.execute('UPDATE queue_reconciliations SET claimed_at=NULL,due_at=?,error=? WHERE session_id=? AND claimed_at=? AND attempts=?', (now+60,str(error)[:500],session_id,now,attempt))
        logger.warning('Queue reconciliation failed for %s: %s', session_id, type(error).__name__)
        return False


class QueueWorker:
    def __init__(self, store, courses, sync, fetch, *, interval=30):
        self.store, self.courses, self.sync, self.fetch, self.interval = store,courses,sync,fetch,interval
        self.stop_event = threading.Event()
        self.thread = None
        self.last_schedule = {}

    def tick(self, now=None):
        now = int(time.time()) if now is None else now
        for course_id, original in self.courses():
            if not original.get('queue', {}).get('enabled'): continue
            course = effective_course(self.store, course_id, original)
            with self.store.connection() as con:
                groups = {r[0] for r in con.execute('SELECT group_id FROM study_groups WHERE course_id=?', (course_id,))}
            groups.update(course.get('queue', {}).get('schedule', {}))
            from .schedule_overrides import validate_overrides
            for override in validate_overrides(course.get('queue', {}).get('schedule_overrides', [])):
                groups.update(override['groups'])
            for group_id in groups:
                try:
                    key = (course_id,group_id)
                    if now-self.last_schedule.get(key,0) >= 3600:
                        try:
                            refresh_schedule(self.store,course_id,group_id,course,fetch=self.fetch,now=now)
                            self.last_schedule[key] = now
                        except Exception:
                            logger.exception('Schedule refresh failed for %s/%s; keeping existing lessons',course_id,group_id)
                    with self.store.connection() as con:
                        sessions = [dict(r) for r in con.execute("SELECT * FROM sessions WHERE course_id=? AND group_id=? AND starts_at<=? AND enrollment_status!='cancelled' AND NOT EXISTS (SELECT 1 FROM queue_reconciliations r WHERE r.session_id=sessions.session_id AND r.done_at IS NOT NULL)", (course_id,group_id,now))]
                    available = saved_deadlines_available(self.store,course_id,group_id)
                    for session in sessions:
                        # Заморозка использует сохранённый допуск до обновления таблицы.
                        teacher_view(self.store,course_id,group_id,course,deadlines_available=available,now=now,session_id=session['session_id'])
                        def sync():
                            _, summary = self.sync(course_id,group_id,original,force=True)
                            if summary.get('stale') or summary.get('source_frozen'):
                                raise RuntimeError('Сверка отложена: таблица недоступна или в группе идёт занятие')
                        reconcile(self.store,session['session_id'],now=now,sync=sync)
                    # Подхват новых допусков даже без открытой страницы.
                    self.sync(course_id,group_id,original)
                    for session in sessions:
                        promote_next_labs(self.store, session['session_id'], now=now, actor='recovery')
                except Exception:
                    logger.exception('Queue background update failed for %s/%s',course_id,group_id)

    def start(self):
        if self.thread and self.thread.is_alive(): return
        def loop():
            while not self.stop_event.is_set():
                try: self.tick()
                except Exception: logger.exception('Queue worker tick failed')
                self.stop_event.wait(self.interval)
        self.thread = threading.Thread(target=loop,name='defense-queue',daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
