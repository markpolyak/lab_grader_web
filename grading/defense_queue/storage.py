"""SQLite-хранилище участников и посещения. Сетевые запросы выполняет вызывающий код."""
from contextlib import contextmanager
from pathlib import Path
import secrets
import sqlite3
import threading


def initialize_session(con, session_id, *, now):
    """Allocate each group member a stable slot in this session's lottery."""
    session = con.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone()
    if session is None:
        return
    members = con.execute('''SELECT student_id FROM queue_members WHERE course_id=? AND group_id=?
        ORDER BY created_at,student_id''', (session['course_id'], session['group_id'])).fetchall()
    ordinal = con.execute('SELECT coalesce(max(ordinal),0) FROM session_roster WHERE session_id=?',
                          (session_id,)).fetchone()[0]
    for member in members:
        if con.execute('SELECT 1 FROM session_roster WHERE session_id=? AND student_id=?',
                       (session_id, member['student_id'])).fetchone():
            continue
        ordinal += 1
        con.execute('INSERT INTO session_roster VALUES (?,?,?,?,?)',
                    (session_id, member['student_id'], secrets.SystemRandom().random(), ordinal, now))


class QueueClosed(ValueError):
    pass


class QueueConflict(ValueError):
    pass


class QueueStore:
    def __init__(self, path: str | Path):
        self._local = threading.local()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                version = con.execute("PRAGMA user_version").fetchone()[0]
                if version > 10:
                    raise ValueError("Версия базы очереди новее поддерживаемой")
                migrations = Path(__file__).with_name("migrations")
                for target in range(version + 1, 11):
                    path = next(migrations.glob(f"{target:03d}_*.sql"))
                    statement = ""
                    for line in path.read_text().splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            con.execute(statement)
                            statement = ""
                    if statement.strip() and not all(line.lstrip().startswith('--') or not line.strip() for line in statement.splitlines()):
                        raise ValueError("Незавершённая миграция")
                    con.execute(f"PRAGMA user_version={target}")
                con.commit()
            except Exception:
                con.rollback()
                raise

    @contextmanager
    def connection(self):
        active = getattr(self._local, "transaction", None)
        if active is not None:
            yield active
            return
        con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def transaction(self):
        # Вложенные операции группы присоединяются к общей атомарной записи.
        active = getattr(self._local, 'transaction', None)
        if active is not None:
            yield active
            return
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            self._local.transaction = con
            try:
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                del self._local.transaction

    @staticmethod
    def event(con, candidate_id, kind, now, *, session_id=None, actor="system", actor_key="queue"):
        con.execute("""INSERT INTO queue_events(candidate_id,session_id,actor_kind,actor_key,kind,occurred_at)
            VALUES (?,?,?,?,?,?)""", (candidate_id, session_id, actor, actor_key, kind, now))

    def configure_group(self, course_id, group_id, *, title, timezone="Europe/Moscow"):
        with self.transaction() as con:
            con.execute("""INSERT INTO courses(course_id,title,timezone,schedule_source)
                VALUES (?,?,?,'manual') ON CONFLICT(course_id) DO UPDATE SET title=excluded.title,timezone=excluded.timezone""", (course_id, title, timezone))
            con.execute("INSERT INTO study_groups VALUES (?,?,?) ON CONFLICT DO NOTHING", (course_id, group_id, group_id))

    def student(self, full_name, github_login, *, now):
        login = github_login.strip().lower()
        if not full_name.strip() or not login:
            raise ValueError("Нужны ФИО и GitHub")
        with self.transaction() as con:
            con.execute("""INSERT INTO students(full_name,github_login,created_at) VALUES (?,?,?)
                ON CONFLICT(github_login) DO UPDATE SET full_name=excluded.full_name""", (full_name.strip(), login, now))
            return con.execute("SELECT student_id FROM students WHERE github_login=?", (login,)).fetchone()[0]

    def qualify(self, course_id, group_id, student_id, lab_key, lab_order, *, deadline_at, deadline_available=True, now):
        """Вызывается только после подтверждения допуска и порядка обязательных работ."""
        with self.transaction() as con:
            old = con.execute("SELECT * FROM queue_candidates WHERE course_id=? AND student_id=? AND status='eligible'", (course_id, student_id)).fetchone()
            if old:
                if old['lab_key'] != lab_key or old['group_id'] != group_id:
                    raise QueueConflict("У студента уже есть другая активная работа")
                con.execute("UPDATE queue_candidates SET deadline_at=?,deadline_available=? WHERE candidate_id=?", (deadline_at, int(deadline_available), old['candidate_id']))
                return old['candidate_id']
            result = con.execute("""INSERT INTO queue_candidates
                (course_id,group_id,student_id,lab_key,lab_order,deadline_at,deadline_available,qualified_at)
                VALUES (?,?,?,?,?,?,?,?)""", (course_id, group_id, student_id, lab_key, lab_order, deadline_at, int(deadline_available), now))
            candidate_id = result.lastrowid
            self.event(con, candidate_id, "qualified", now)
            return candidate_id

    def create_session(self, session_id, course_id, group_id, teacher_key, *, starts_at, ends_at, local_date, now):
        with self.transaction() as con:
            course = con.execute("SELECT timezone FROM courses WHERE course_id=?", (course_id,)).fetchone()
            if course is None:
                raise ValueError("Курс не найден")
            con.execute("""INSERT INTO sessions(session_id,course_id,group_id,teacher_key,source_key,source_kind,
                local_date,timezone,opens_at,starts_at,ends_at,expires_at,created_at)
                VALUES (?,?,?,?,?,'manual',?,?,?,?,?,?,?)""", (session_id, course_id, group_id, teacher_key,
                session_id, local_date, course['timezone'], min(now, starts_at), starts_at, ends_at, ends_at, now))
            initialize_session(con, session_id, now=now)

    def attendance(self, session_id, candidate_id, will_attend: bool, *, now):
        if type(will_attend) is not bool:
            raise ValueError("Ответ о посещении должен быть логическим значением")
        with self.transaction() as con:
            session = con.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            candidate = con.execute("SELECT * FROM queue_candidates WHERE candidate_id=? AND status='eligible'", (candidate_id,)).fetchone()
            if session is None or candidate is None:
                raise ValueError("Занятие или участник не найден")
            continuous = session['enrollment_mode'] != 'legacy'
            if (now >= (session['ends_at'] if continuous else session['starts_at'])
                    or continuous and now < session['opens_at']
                    or session['enrollment_status'] in ('closed', 'cancelled')):
                raise QueueClosed("Состав текущего занятия уже закрыт")
            if (candidate['course_id'], candidate['group_id']) != (session['course_id'], session['group_id']):
                raise ValueError("Участник не относится к группе занятия")
            if continuous and not will_attend:
                attempts = con.execute("SELECT entry_id,status FROM queue_entries WHERE session_id=? AND candidate_id=? AND status IN ('waiting','called')", (session_id,candidate_id)).fetchall()
                if any(row['status'] == 'called' for row in attempts):
                    raise QueueConflict('Вы уже вызваны: обратитесь к преподавателю')
                for row in attempts:
                    con.execute("UPDATE queue_entries SET status='withdrawn',closed_at=?,version=version+1 WHERE entry_id=?", (now,row['entry_id']))
                    self.event(con,candidate_id,'withdrawn',now,session_id=session_id,actor='student',actor_key=str(candidate['student_id']))
            old = con.execute("SELECT * FROM session_participation WHERE session_id=? AND candidate_id=?", (session_id, candidate_id)).fetchone()
            if old and old['attendance_answered'] and bool(old['will_attend']) == will_attend:
                return
            con.execute("""INSERT INTO session_participation
                (session_id,candidate_id,course_id,group_id,student_id,teacher_key,will_attend,attendance_answered,random_key,updated_at)
                VALUES (?,?,?,?,?,?,?,1,?,?) ON CONFLICT(session_id,candidate_id) DO UPDATE SET
                will_attend=excluded.will_attend,attendance_answered=1,updated_at=excluded.updated_at""",
                (session_id, candidate_id, candidate['course_id'], candidate['group_id'], candidate['student_id'], session['teacher_key'], int(will_attend), secrets.SystemRandom().random(), now))
            self.event(con, candidate_id, "attending" if will_attend else "not_attending", now, session_id=session_id, actor="student", actor_key=str(candidate['student_id']))

    def roster(self, session_id):
        with self.connection() as con:
            session = con.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if session is None:
                raise ValueError("Занятие не найдено")
            rows = con.execute("""SELECT c.*,s.full_name,s.github_login,
                COALESCE(p.will_attend,0) AS will_attend,COALESCE(p.attendance_answered,0) AS attendance_answered,
                p.random_key,p.frozen_at,p.updated_at AS attendance_at FROM queue_candidates c JOIN students s USING(student_id)
                LEFT JOIN session_participation p ON p.candidate_id=c.candidate_id AND p.session_id=?
                WHERE c.course_id=? AND c.group_id=? AND c.status='eligible'
                ORDER BY c.qualified_at,c.candidate_id""", (session_id, session['course_id'], session['group_id'])).fetchall()
            return [dict(row) for row in rows]
