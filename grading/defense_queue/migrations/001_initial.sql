-- Design executable in isolation. Not a migration applied to the application.
PRAGMA foreign_keys = ON;

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
    created_at INTEGER NOT NULL,
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
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
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
CREATE UNIQUE INDEX one_called_per_teacher
    ON queue_entries(teacher_key) WHERE status = 'called';
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

-- One transaction: change entry + append event + COMMIT, or roll everything back.
-- Connection setup (not a substitute for a persistent local filesystem):
-- PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
-- PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
-- Set user_version only as part of an actual reviewed migration, not this design file.
