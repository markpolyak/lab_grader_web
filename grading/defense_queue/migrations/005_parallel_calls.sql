-- Несколько независимых защит у одного преподавателя.
DROP INDEX IF EXISTS one_called_per_teacher;
CREATE TABLE queue_commands (
 session_id TEXT NOT NULL REFERENCES sessions(session_id),
 actor_key TEXT NOT NULL,
 request_id TEXT NOT NULL,
 entry_id INTEGER REFERENCES queue_entries(entry_id),
 PRIMARY KEY(session_id, actor_key, request_id)
);
