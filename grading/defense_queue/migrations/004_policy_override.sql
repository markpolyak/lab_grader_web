ALTER TABLE queue_settings ADD COLUMN policy_override_set INTEGER NOT NULL DEFAULT 0 CHECK(policy_override_set IN (0,1));
UPDATE queue_settings SET policy_override_set=1;
