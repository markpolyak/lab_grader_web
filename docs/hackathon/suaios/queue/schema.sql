-- Снимок схемы после миграций 001–008. Проверяется test_documented_schema_matches_migrations.

CREATE TABLE courses (
    course_id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    schedule_source TEXT NOT NULL CHECK (schedule_source IN ('university', 'manual', 'mixed', 'none')),
    university_key TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);

CREATE TABLE study_groups (
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    group_id TEXT NOT NULL,
    title TEXT NOT NULL,
    PRIMARY KEY (course_id, group_id)
);

CREATE TABLE students (
    student_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL CHECK (length(trim(full_name)) > 0),
    github_login TEXT COLLATE NOCASE UNIQUE,
    created_at INTEGER NOT NULL
);

CREATE TABLE course_students (
    course_id TEXT NOT NULL,
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    group_id TEXT NOT NULL,
    source_student_key TEXT NOT NULL,
    PRIMARY KEY (course_id, student_id),
    UNIQUE (course_id, source_student_key),
    FOREIGN KEY (course_id, group_id) REFERENCES study_groups(course_id, group_id)
);

CREATE TABLE schedule_overrides (
    override_id TEXT PRIMARY KEY NOT NULL,
    course_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('cancel_session', 'add_session', 'move_session')),
    target_session_id TEXT REFERENCES sessions(session_id),
    local_date TEXT NOT NULL,
    starts_at INTEGER,
    ends_at INTEGER,
    reason TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (course_id, group_id) REFERENCES study_groups(course_id, group_id),
    FOREIGN KEY (target_session_id, course_id, group_id)
        REFERENCES sessions(session_id, course_id, group_id),
    CHECK ((kind = 'cancel_session' AND target_session_id IS NOT NULL AND starts_at IS NULL AND ends_at IS NULL)
        OR (kind IN ('add_session', 'move_session') AND starts_at IS NOT NULL AND ends_at IS NOT NULL AND starts_at < ends_at)),
    CHECK (kind != 'move_session' OR target_session_id IS NOT NULL)
);

CREATE UNIQUE INDEX one_enabled_cancel_per_session
    ON schedule_overrides(target_session_id)
    WHERE enabled = 1 AND kind = 'cancel_session';

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    course_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    teacher_key TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('university', 'manual', 'extra', 'day')),
    local_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    opens_at INTEGER NOT NULL,
    starts_at INTEGER NOT NULL,
    ends_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    enrollment_status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (enrollment_status IN ('scheduled', 'open', 'closed', 'cancelled')),
    schedule_revision INTEGER NOT NULL DEFAULT 1 CHECK (schedule_revision >= 1),
    schedule_checked_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL, enrollment_mode TEXT NOT NULL DEFAULT 'legacy' CHECK(enrollment_mode IN ('legacy','fifo','daily')),
    UNIQUE (course_id, group_id, source_key),
    UNIQUE (session_id, course_id, group_id),
    UNIQUE (session_id, course_id, group_id, teacher_key),
    FOREIGN KEY (course_id, group_id) REFERENCES study_groups(course_id, group_id),
    CHECK (opens_at <= starts_at AND starts_at < ends_at AND ends_at <= expires_at)
);

CREATE INDEX sessions_by_group_date ON sessions(course_id, group_id, local_date);

CREATE TABLE queue_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    lab_key TEXT NOT NULL,
    lab_order INTEGER NOT NULL CHECK (lab_order >= 1),
    deadline_at INTEGER,
    deadline_available INTEGER NOT NULL DEFAULT 1 CHECK (deadline_available IN (0, 1)),
    qualified_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'eligible' CHECK (status IN ('eligible', 'defended', 'inactive')),
    UNIQUE (candidate_id, course_id, group_id, student_id),
    FOREIGN KEY (course_id, group_id) REFERENCES study_groups(course_id, group_id)
);

CREATE UNIQUE INDEX one_eligible_candidate_per_course
    ON queue_candidates(course_id, student_id) WHERE status='eligible';

CREATE TABLE session_participation (
    session_id TEXT NOT NULL,
    candidate_id INTEGER NOT NULL,
    course_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    student_id INTEGER NOT NULL,
    teacher_key TEXT NOT NULL,
    will_attend INTEGER NOT NULL DEFAULT 0 CHECK (will_attend IN (0, 1)),
    random_key REAL CHECK (random_key >= 0 AND random_key < 1),
    attendance_answered INTEGER NOT NULL DEFAULT 0 CHECK (attendance_answered IN (0, 1)),
    updated_at INTEGER NOT NULL,
    frozen_at INTEGER,
    PRIMARY KEY (session_id, candidate_id),
    FOREIGN KEY (candidate_id, course_id, group_id, student_id)
        REFERENCES queue_candidates(candidate_id, course_id, group_id, student_id),
    FOREIGN KEY (session_id, course_id, group_id, teacher_key)
        REFERENCES sessions(session_id, course_id, group_id, teacher_key)
);

CREATE TRIGGER frozen_participation BEFORE UPDATE ON session_participation
WHEN OLD.frozen_at IS NOT NULL AND NEW.will_attend != OLD.will_attend
AND (SELECT enrollment_mode FROM sessions WHERE session_id=OLD.session_id) = 'legacy'
BEGIN SELECT RAISE(ABORT, 'attendance is frozen for this session'); END;

CREATE TABLE queue_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_key TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    teacher_key TEXT NOT NULL,
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    candidate_id INTEGER NOT NULL,
    lab_key TEXT NOT NULL,
    lab_order INTEGER NOT NULL CHECK (lab_order >= 1),
    full_name_snapshot TEXT NOT NULL,
    github_snapshot TEXT,
    eligibility_snapshot TEXT NOT NULL,
    eligibility_checked_at INTEGER NOT NULL,
    deadline_at INTEGER,
    deadline_available INTEGER NOT NULL DEFAULT 1 CHECK (deadline_available IN (0, 1)),
    priority INTEGER CHECK (priority >= -1),
    is_repeat INTEGER NOT NULL DEFAULT 0 CHECK (is_repeat IN (0, 1)),
    joined_at INTEGER NOT NULL,
    called_at INTEGER,
    closed_at INTEGER,
    status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN
        ('waiting', 'called', 'defended', 'failed', 'no_show', 'withdrawn', 'expired')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1), voided_at INTEGER, rounds_managed INTEGER NOT NULL DEFAULT 0 CHECK(rounds_managed IN (0,1)),
    FOREIGN KEY (session_id, course_id, group_id, teacher_key)
        REFERENCES sessions(session_id, course_id, group_id, teacher_key),
    FOREIGN KEY (candidate_id, course_id, group_id, student_id)
        REFERENCES queue_candidates(candidate_id, course_id, group_id, student_id),
    CHECK ((status IN ('waiting', 'called') AND closed_at IS NULL)
        OR (status NOT IN ('waiting', 'called') AND closed_at IS NOT NULL)),
    CHECK (status != 'called' OR called_at IS NOT NULL),
    CHECK (called_at IS NULL OR called_at >= joined_at),
    CHECK (closed_at IS NULL OR closed_at >= joined_at)
);

CREATE UNIQUE INDEX one_active_student_per_course
    ON queue_entries(course_id, student_id) WHERE status IN ('waiting', 'called');

CREATE INDEX waiting_order ON queue_entries(session_id, is_repeat, (priority IS NULL), priority, entry_id)
    WHERE status = 'waiting';

CREATE INDEX student_history ON queue_entries(course_id, student_id, joined_at);

CREATE TABLE queue_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER REFERENCES queue_entries(entry_id),
    session_id TEXT REFERENCES sessions(session_id),
    candidate_id INTEGER REFERENCES queue_candidates(candidate_id),
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('student', 'teacher', 'system')),
    actor_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    occurred_at INTEGER NOT NULL,
    CHECK (json_valid(details_json)),
    CHECK (session_id IS NOT NULL OR candidate_id IS NOT NULL)
);

CREATE INDEX events_by_session ON queue_events(session_id, event_id);

CREATE TRIGGER immutable_event_update BEFORE UPDATE ON queue_events
BEGIN SELECT RAISE(ABORT, 'queue events are append-only'); END;

CREATE TRIGGER immutable_event_delete BEFORE DELETE ON queue_events
BEGIN SELECT RAISE(ABORT, 'queue events are append-only'); END;

CREATE TRIGGER event_entry_session BEFORE INSERT ON queue_events
WHEN NEW.entry_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM queue_entries WHERE entry_id = NEW.entry_id AND session_id = NEW.session_id
)
BEGIN SELECT RAISE(ABORT, 'event session differs from entry session'); END;

CREATE TABLE queue_settings (
    course_id TEXT PRIMARY KEY REFERENCES courses(course_id),
    random_override INTEGER NOT NULL DEFAULT 0 CHECK(random_override IN (0,1)),
    deadlines_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(deadlines_json)),
    updated_at INTEGER NOT NULL
, schedule_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(schedule_json)), policy_override_set INTEGER NOT NULL DEFAULT 0 CHECK(policy_override_set IN (0,1)), auto_repeat INTEGER NOT NULL DEFAULT 1 CHECK(auto_repeat IN (0,1)), undo_seconds INTEGER NOT NULL DEFAULT 5 CHECK(undo_seconds BETWEEN 1 AND 10), fifo INTEGER NOT NULL DEFAULT 0 CHECK(fifo IN (0,1)), opens_before_minutes INTEGER NOT NULL DEFAULT 15 CHECK(opens_before_minutes BETWEEN 0 AND 1440));

CREATE TABLE queue_reconciliations (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    due_at INTEGER NOT NULL,
    done_at INTEGER,
    claimed_at INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
, checked_at INTEGER);

CREATE INDEX pending_reconciliations ON queue_reconciliations(due_at) WHERE done_at IS NULL;

CREATE TABLE queue_sync_state (
    course_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    summary_json TEXT NOT NULL CHECK(json_valid(summary_json)),
    checked_at INTEGER NOT NULL,
    PRIMARY KEY(course_id,group_id),
    FOREIGN KEY(course_id,group_id) REFERENCES study_groups(course_id,group_id)
);

CREATE TABLE queue_commands (
 session_id TEXT NOT NULL REFERENCES sessions(session_id),
 actor_key TEXT NOT NULL,
 request_id TEXT NOT NULL,
 entry_id INTEGER REFERENCES queue_entries(entry_id),
 PRIMARY KEY(session_id, actor_key, request_id)
);

CREATE TABLE queue_undo (
    command_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    actor TEXT NOT NULL,
    request_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL CHECK(json_valid(before_json)),
    after_json TEXT NOT NULL CHECK(json_valid(after_json)),
    expires_at REAL NOT NULL,
    undone_at REAL,
    UNIQUE(session_id,actor,request_id)
);

CREATE TABLE queue_members (
    course_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    full_name TEXT NOT NULL,
    github_login TEXT,
    admitted_lab_key TEXT,
    target_lab_key TEXT,
    target_order INTEGER,
    deadline_at INTEGER,
    deadline_available INTEGER NOT NULL CHECK(deadline_available IN (0,1)),
    completed INTEGER NOT NULL CHECK(completed IN (0,1)),
    queue_state TEXT NOT NULL CHECK(queue_state IN ('eligible','not_ready','not_attending')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(course_id,group_id,student_id),
    FOREIGN KEY(course_id,group_id) REFERENCES study_groups(course_id,group_id)
);

CREATE TABLE session_roster (
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    random_key REAL NOT NULL CHECK(random_key>=0 AND random_key<1),
    ordinal INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(session_id,student_id)
);
