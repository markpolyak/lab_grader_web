ALTER TABLE sessions ADD COLUMN enrollment_mode TEXT NOT NULL DEFAULT 'legacy' CHECK(enrollment_mode IN ('legacy','fifo','daily'));
DROP TRIGGER frozen_participation;
CREATE TRIGGER frozen_participation BEFORE UPDATE ON session_participation
WHEN OLD.frozen_at IS NOT NULL AND NEW.will_attend != OLD.will_attend
AND (SELECT enrollment_mode FROM sessions WHERE session_id=OLD.session_id) = 'legacy'
BEGIN SELECT RAISE(ABORT, 'attendance is frozen for this session'); END;
