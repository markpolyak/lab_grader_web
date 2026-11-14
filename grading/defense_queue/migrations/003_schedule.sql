ALTER TABLE queue_settings ADD COLUMN schedule_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(schedule_json));
CREATE TABLE queue_sync_state (
    course_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    summary_json TEXT NOT NULL CHECK(json_valid(summary_json)),
    checked_at INTEGER NOT NULL,
    PRIMARY KEY(course_id,group_id),
    FOREIGN KEY(course_id,group_id) REFERENCES study_groups(course_id,group_id)
);
