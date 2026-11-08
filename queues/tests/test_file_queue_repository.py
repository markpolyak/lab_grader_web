from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import yaml

from queues.domain import (
    DefenseResult,
    EntryStatus,
    QueueEntry,
    QueueEntryMetadata,
    QueueRecord,
    RegistrationMode,
    Student,
)
from queues.domain.err import (
    QueueAlreadyExistsError,
    QueueNotFoundError,
    QueueVersionConflictError,
)
from queues.repositories.storage.file import (
    FileQueueRepository,
    QueueStorageCorruptedError,
)
from queues.repositories.storage.file.serialization import record_to_dict


def make_record(*, course_id="os", group_id="1234") -> QueueRecord:
    start = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
    return QueueRecord(
        course_id=course_id,
        group_id=group_id,
        lesson_starts_at=start,
        lesson_ends_at=start + timedelta(minutes=90),
        registration_opens_at=start - timedelta(minutes=15),
        registration_closes_at=start + timedelta(minutes=90),
        service_until=start.replace(hour=23, minute=59),
    )


def add_entry(record: QueueRecord, github="student") -> QueueEntry:
    record.queue.closed = False
    entry = record.queue.add_entry(
        Student("Иван", "Иванов", "Иванович", github), "1", "ЛР1"
    )
    record.entry_metadata[entry.id] = QueueEntryMetadata(
        queue_id=record.id,
        sequence=1,
        status=EntryStatus.CALLED,
        called_at=record.lesson_starts_at,
    )
    return entry


def test_round_trip_preserves_every_field(tmp_path):
    repo = FileQueueRepository(tmp_path)
    record = make_record()
    entry = add_entry(record)
    record.entry_metadata[entry.id].status = EntryStatus.FINISHED
    record.entry_metadata[entry.id].result = DefenseResult.DEFENDED
    record.entry_metadata[entry.id].finished_at = record.lesson_ends_at
    record.registration_mode = RegistrationMode.MANUALLY_OPENED

    repo.create(record)
    restored = repo.get(record.id)

    assert record_to_dict(restored) == record_to_dict(record)
    assert restored.entry_metadata[entry.id].result == DefenseResult.DEFENDED
    assert restored.registration_mode == RegistrationMode.MANUALLY_OPENED


def test_state_survives_a_new_repository_instance(tmp_path):
    first = FileQueueRepository(tmp_path)
    record = make_record()
    add_entry(record)
    first.create(record)

    second = FileQueueRepository(tmp_path)
    restored = second.get(record.id)

    assert restored.queue.closed is False
    assert restored.queue.entries[0].student.github == "student"
    assert restored.queue.get_position(github="student") == 1


def test_create_does_not_overwrite_existing_state(tmp_path):
    repo = FileQueueRepository(tmp_path)
    record = make_record()
    repo.create(record)
    add_entry(record)

    with pytest.raises(QueueAlreadyExistsError):
        repo.create(record)

    assert repo.get(record.id).queue.entries == []


def test_save_increments_version_and_rejects_stale_snapshot(tmp_path):
    repo = FileQueueRepository(tmp_path)
    record = repo.create(make_record())
    stale = repo.get(record.id)
    record.queue.closed = False

    saved = repo.save(record, expected_version=0)
    assert saved.version == 1
    assert repo.get(record.id).version == 1

    stale.queue.closed = True
    with pytest.raises(QueueVersionConflictError):
        repo.save(stale, expected_version=0)
    assert repo.get(record.id).queue.closed is False


def test_missing_record_and_mismatched_version(tmp_path):
    repo = FileQueueRepository(tmp_path)
    with pytest.raises(QueueNotFoundError):
        repo.save(make_record(), expected_version=0)

    record = repo.create(make_record())
    record.version = 1
    with pytest.raises(ValueError):
        repo.save(record, expected_version=0)


def test_list_by_course_and_group_ignores_other_files(tmp_path):
    repo = FileQueueRepository(tmp_path)
    repo.create(make_record())
    repo.create(make_record(group_id="5678"))
    repo.create(make_record(course_id="ml"))
    (tmp_path / "notes.txt").write_text("not a queue", encoding="utf-8")

    assert len(repo.list_by_course("os")) == 2
    assert len(repo.list_by_course("os", group_id="1234")) == 1
    assert repo.list_by_course("unknown") == []


def test_corrupt_file_is_quarantined_and_reported(tmp_path):
    repo = FileQueueRepository(tmp_path)
    queue_id = uuid4()
    path = tmp_path / f"{queue_id}.yaml"
    path.write_text("queue: [broken", encoding="utf-8")

    with pytest.raises(QueueStorageCorruptedError):
        repo.get(queue_id)

    assert not path.exists()
    assert list(tmp_path.glob(f"{queue_id}.yaml.corrupt-*"))
    assert repo.get(queue_id) is None


def test_list_skips_a_corrupt_file_instead_of_hiding_the_rest(tmp_path):
    repo = FileQueueRepository(tmp_path)
    good = repo.create(make_record())
    (tmp_path / f"{uuid4()}.yaml").write_text("queue: [broken", encoding="utf-8")

    assert [record.id for record in repo.list_by_course("os")] == [good.id]


def test_write_leaves_no_temporary_files_and_valid_yaml(tmp_path):
    repo = FileQueueRepository(tmp_path)
    record = repo.create(make_record())

    assert [path.name for path in tmp_path.iterdir()] == [f"{record.id}.yaml"]
    data = yaml.safe_load((tmp_path / f"{record.id}.yaml").read_text(encoding="utf-8"))
    assert data["id"] == str(record.id)


def test_concurrent_saves_let_only_one_win(tmp_path):
    repo = FileQueueRepository(tmp_path)
    original = repo.create(make_record())
    first = repo.get(original.id)
    second = repo.get(original.id)

    def save(record):
        try:
            return repo.save(record, expected_version=0)
        except QueueVersionConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, (first, second)))

    assert sum(isinstance(result, QueueRecord) for result in results) == 1
    assert sum(isinstance(result, QueueVersionConflictError) for result in results) == 1
    assert repo.get(original.id).version == 1


def test_directory_defaults_to_environment(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("QUEUE_STATE_DIR", str(state_dir))

    repo = FileQueueRepository()
    record = repo.create(make_record())

    assert (state_dir / f"{record.id}.yaml").exists()


def test_delete_removes_the_state_file(tmp_path):
    repo = FileQueueRepository(tmp_path)
    record = repo.create(make_record())

    repo.delete(record.id)

    assert repo.get(record.id) is None
    assert not (tmp_path / f"{record.id}.yaml").exists()
    with pytest.raises(QueueNotFoundError):
        repo.delete(record.id)
