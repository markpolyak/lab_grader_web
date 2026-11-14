CREATE TABLE queue_settings (
    course_id TEXT PRIMARY KEY REFERENCES courses(course_id),
    random_override INTEGER NOT NULL DEFAULT 0 CHECK(random_override IN (0,1)),
    deadlines_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(deadlines_json)),
    updated_at INTEGER NOT NULL
);
CREATE TABLE queue_reconciliations (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    due_at INTEGER NOT NULL,
    done_at INTEGER,
    claimed_at INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX pending_reconciliations ON queue_reconciliations(due_at) WHERE done_at IS NULL;
