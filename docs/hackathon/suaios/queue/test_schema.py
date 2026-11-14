"""Design checks against temporary SQLite files; no application data is opened."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

DDL = Path(__file__).with_name("schema.sql").read_text()


def connect(path):
    con = sqlite3.connect(path, timeout=2, isolation_level=None)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    return con


def entry(con, key, student=1, session="s1", group="g1", teacher="t1", status="waiting", priority=-1, repeat=0):
    con.execute("""INSERT INTO queue_candidates
        (course_id,group_id,student_id,lab_key,lab_order,deadline_at,qualified_at)
        VALUES ('c1',?,?,'lab1',1,20,10) ON CONFLICT DO NOTHING""", (group, student))
    candidate_id = con.execute("SELECT candidate_id FROM queue_candidates WHERE course_id='c1' AND student_id=? AND status='eligible'", (student,)).fetchone()[0]
    con.execute("""INSERT INTO queue_entries
        (request_key,session_id,course_id,group_id,teacher_key,student_id,lab_key,lab_order,
        full_name_snapshot,eligibility_snapshot,eligibility_checked_at,deadline_at,
        priority,is_repeat,joined_at,called_at,status,candidate_id)
        VALUES (?,?,'c1',?,?,?,'lab1',1,'Synthetic Student','v',10,20,?,?,10,?,?,?)""",
        (key, session, group, teacher, student, priority, repeat, 11 if status == "called" else None, status, candidate_id))


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "queue.sqlite3")
        self.con = connect(self.path)
        self.con.executescript(DDL)
        self.con.execute("INSERT INTO courses(course_id,title,schedule_source) VALUES ('c1','Test','manual')")
        for group in ("g1", "g2"):
            self.con.execute("INSERT INTO study_groups VALUES ('c1',?,?)", (group, group))
        for student in (1, 2):
            self.con.execute("INSERT INTO students VALUES (?,?,?,0)", (student, f"Synthetic {student}", f"user{student}"))
            self.con.execute("INSERT INTO course_students VALUES ('c1',?,'g1',?)", (student, str(student)))
        for sid, group, teacher in (("s1", "g1", "t1"), ("s2", "g2", "t1"), ("s3", "g2", "t2")):
            self.con.execute("""INSERT INTO sessions
                (session_id,course_id,group_id,teacher_key,source_key,source_kind,local_date,
                timezone,opens_at,starts_at,ends_at,expires_at,created_at)
                VALUES (?,'c1',?,?,?,'manual','2026-11-06','Europe/Moscow',0,10,20,30,0)""", (sid, group, teacher, sid))

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def test_restart_preserves_queue_and_history(self):
        entry(self.con, "r1")
        self.con.execute("INSERT INTO queue_events(session_id,entry_id,actor_kind,actor_key,kind,occurred_at) VALUES ('s1',1,'student','1','enrolled',10)")
        self.con.close()
        self.con = connect(self.path)
        self.assertEqual(self.con.execute("SELECT count(*) FROM queue_entries").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM queue_events").fetchone()[0], 1)

    def test_one_active_entry_across_overlapping_sessions(self):
        entry(self.con, "r1")
        with self.assertRaises(sqlite3.IntegrityError):
            entry(self.con, "r2", session="s2", group="g2")

    def test_session_group_cannot_be_mixed(self):
        with self.assertRaises(sqlite3.IntegrityError):
            entry(self.con, "r1", group="g2")

    def test_same_teacher_can_call_two_students(self):
        entry(self.con, "r1", status="called")
        entry(self.con, "r2", student=2, session="s2", group="g2", status="called")

    def test_repeat_at_tail_even_with_urgent_priority(self):
        entry(self.con, "r1", student=1, priority=10)
        entry(self.con, "r2", student=2, repeat=1, priority=-1)
        rows = self.con.execute("""SELECT student_id FROM queue_entries WHERE status='waiting'
            ORDER BY is_repeat, CASE WHEN is_repeat=0 THEN priority ELSE 0 END, entry_id""").fetchall()
        self.assertEqual(rows, [(1,), (2,)])

    def test_terminal_entry_allows_rejoin_but_cannot_erase_events(self):
        entry(self.con, "r1")
        self.con.execute("UPDATE queue_entries SET status='failed',closed_at=15 WHERE entry_id=1")
        entry(self.con, "r2", repeat=1)
        self.con.execute("INSERT INTO queue_events(session_id,entry_id,actor_kind,actor_key,kind,occurred_at) VALUES ('s1',1,'teacher','t1','failed',15)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("DELETE FROM queue_events")

    def test_entry_and_event_transaction_roll_back_together(self):
        self.con.execute("BEGIN IMMEDIATE")
        entry(self.con, "r1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("INSERT INTO queue_events(session_id,entry_id,actor_kind,actor_key,kind,occurred_at) VALUES ('s2',1,'teacher','t1','called',11)")
        self.con.execute("ROLLBACK")
        self.assertEqual(self.con.execute("SELECT count(*) FROM queue_entries").fetchone()[0], 0)

    def test_simultaneous_enrollment_only_one_wins(self):
        barrier = threading.Barrier(2)
        results = []

        def insert(key):
            con = connect(self.path)
            barrier.wait()
            try:
                con.execute("BEGIN IMMEDIATE")
                entry(con, key)
                con.execute("COMMIT")
                results.append("ok")
            except sqlite3.IntegrityError:
                con.execute("ROLLBACK")
                results.append("duplicate")
            finally:
                con.close()

        threads = [threading.Thread(target=insert, args=(str(i),)) for i in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ["ok", "duplicate"])

    def test_backup_includes_committed_wal_data(self):
        entry(self.con, "r1")
        with sqlite3.connect(Path(self.temp.name) / "backup.sqlite3") as backup:
            self.con.backup(backup)
            self.assertEqual(backup.execute("SELECT count(*) FROM queue_entries").fetchone()[0], 1)
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_cancellation_keeps_existing_entries(self):
        entry(self.con, "r1")
        self.con.execute("UPDATE sessions SET enrollment_status='cancelled' WHERE session_id='s1'")
        self.assertEqual(self.con.execute("SELECT status FROM queue_entries").fetchone()[0], "waiting")

    def participation(self):
        entry(self.con, "r1")
        self.con.execute("""INSERT INTO session_participation
            (session_id,candidate_id,course_id,group_id,student_id,teacher_key,updated_at)
            VALUES ('s1',1,'c1','g1',1,'t1',0)""")

    def test_attendance_can_be_changed_both_ways_before_freeze(self):
        self.participation()
        for value in (0, 1):
            self.con.execute("UPDATE session_participation SET will_attend=?", (value,))
            self.assertEqual(self.con.execute("SELECT will_attend FROM session_participation").fetchone()[0], value)

    def test_frozen_attendance_rejects_student_change(self):
        self.participation()
        self.con.execute("UPDATE session_participation SET will_attend=1,attendance_answered=1")
        self.con.execute("UPDATE session_participation SET frozen_at=10")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("UPDATE session_participation SET will_attend=0")

    def test_move_session_preserves_identity_and_participation(self):
        self.participation()
        self.con.execute("UPDATE sessions SET starts_at=40,ends_at=50,expires_at=60,local_date='2026-11-07' WHERE session_id='s1'")
        self.assertEqual(self.con.execute("SELECT session_id,will_attend FROM session_participation").fetchone(), ("s1", 0))

    def test_default_is_not_attending_and_not_answered(self):
        self.participation()
        self.assertEqual(self.con.execute("SELECT will_attend,attendance_answered FROM session_participation").fetchone(), (0, 0))

    def test_closing_attempt_does_not_remove_persistent_candidate(self):
        entry(self.con, "r1")
        self.con.execute("UPDATE queue_entries SET status='expired',closed_at=30")
        self.assertEqual(self.con.execute("SELECT status FROM queue_candidates").fetchone()[0], "eligible")

    def test_qualification_event_can_exist_before_any_session(self):
        entry(self.con, "r1")
        self.con.execute("""INSERT INTO queue_events(candidate_id,actor_kind,actor_key,kind,occurred_at)
            VALUES (1,'system','sync','qualified',10)""")
        self.assertEqual(self.con.execute("SELECT candidate_id,session_id FROM queue_events").fetchone(), (1, None))

    def test_cancel_and_add_same_day_are_compatible(self):
        self.con.execute("""INSERT INTO schedule_overrides
            (override_id,course_id,group_id,kind,target_session_id,local_date,created_by,created_at)
            VALUES ('cancel','c1','g1','cancel_session','s1','2026-11-06','t1',0)""")
        self.con.execute("""INSERT INTO schedule_overrides
            (override_id,course_id,group_id,kind,local_date,starts_at,ends_at,created_by,created_at)
            VALUES ('extra','c1','g1','add_session','2026-11-06',40,50,'t1',0)""")
        self.assertEqual(self.con.execute("SELECT count(*) FROM schedule_overrides").fetchone()[0], 2)

    def test_override_cannot_target_another_group(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("""INSERT INTO schedule_overrides
                (override_id,course_id,group_id,kind,target_session_id,local_date,created_by,created_at)
                VALUES ('cancel','c1','g2','cancel_session','s1','2026-11-06','t1',0)""")


if __name__ == "__main__":
    unittest.main()
