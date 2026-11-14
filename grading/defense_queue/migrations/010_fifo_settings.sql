ALTER TABLE queue_settings ADD COLUMN fifo INTEGER NOT NULL DEFAULT 0 CHECK(fifo IN (0,1));
ALTER TABLE queue_settings ADD COLUMN opens_before_minutes INTEGER NOT NULL DEFAULT 15 CHECK(opens_before_minutes BETWEEN 0 AND 1440);
