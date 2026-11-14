ALTER TABLE queue_entries ADD COLUMN voided_at INTEGER;
ALTER TABLE queue_entries ADD COLUMN rounds_managed INTEGER NOT NULL DEFAULT 0 CHECK(rounds_managed IN (0,1));
ALTER TABLE queue_settings ADD COLUMN auto_repeat INTEGER NOT NULL DEFAULT 1 CHECK(auto_repeat IN (0,1));
ALTER TABLE queue_settings ADD COLUMN undo_seconds INTEGER NOT NULL DEFAULT 5 CHECK(undo_seconds BETWEEN 1 AND 10);
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
