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
