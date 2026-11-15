"""Persistence failures must never publish an uncommitted queue command."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Event
from unittest.mock import AsyncMock
import asyncio

import pytest

from grading.queue_eligibility import EligibleStudentLab
from queues.domain import DefenseResult, EntryStatus, Student
from queues.domain.err import AlreadyQueuedError
from queues.repositories.storage.file import FileQueueRepository
from queues.repositories.storage.file.serialization import record_to_dict
from queues.usecase.queue_history_service import QueueHistoryService
from queues.usecase.queue_service import QueueService

NOW = datetime.fromisoformat('2026-11-09T10:00:00+03:00')
MONDAY = date(2026, 11, 9)
COURSES = {'os': {'name': 'ОС', 'university': 'ГУАП'}}
DOCUMENT = {'university': 'suai', 'days': {'2026-11-09': [
    {'subject': 'ОС', 'kind': 'ЛР', 'groups': ['4431'], 'starts-at': start, 'ends-at': end}
    for start, end in [('10:00', '11:30'), ('12:00', '13:30')]
]}}


def eligible(github='student'):
    return EligibleStudentLab(student=Student('Иван', 'Иванов', '', github),
                              full_name='Иванов Иван', group_id='4431', lab_id='1', lab_name='ЛР1')


def build(repository):
    service = QueueService(repository)
    schedule = service.generate_week(COURSES, [DOCUMENT], week_start=MONDAY)['os']
    return service, schedule, schedule.lessons[0]


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr('queues.usecase.queue_service._now', lambda: NOW)
    return build(FileQueueRepository(tmp_path / 'queues'))


def disk_full(*args, **kwargs):
    raise OSError('disk full')


@pytest.mark.parametrize('command', [
    'join', 'leave', 'call_next', 'finish', 'finish_absent', 'remove_entry',
    'update_result', 'set_registration', 'request_archive',
])
def test_failed_command_restores_state_and_can_be_retried(state, monkeypatch, command):
    service, _, lesson = state
    entry = service.join(lesson.id, eligible(), now=NOW)
    if command in {'finish', 'update_result'}:
        service.call_next(lesson.id, now=NOW)
    if command == 'update_result':
        service.finish(lesson.id, entry.id, DefenseResult.DEFENDED, now=NOW)
    commands = {
        'join': lambda: service.join(lesson.id, eligible('another'), now=NOW),
        'leave': lambda: service.leave('os', entry.id, 'student', now=NOW),
        'call_next': lambda: service.call_next(lesson.id, now=NOW),
        'finish': lambda: service.finish(lesson.id, entry.id, DefenseResult.DEFENDED, now=NOW),
        'finish_absent': lambda: service.finish(lesson.id, entry.id, DefenseResult.ABSENT, now=NOW),
        'remove_entry': lambda: service.remove_entry(lesson.id, entry.id, now=NOW),
        'update_result': lambda: service.update_result(lesson.id, entry.id, DefenseResult.NOT_DEFENDED),
        'set_registration': lambda: service.set_registration(lesson.id, closed=True),
        'request_archive': lambda: service.request_archive(lesson.id),
    }
    record = service.get_record(lesson.id)
    before = record_to_dict(record)
    queue = lesson.queue
    metadata = record.entry_metadata[entry.id]
    with monkeypatch.context() as failure:
        failure.setattr(service.repository, 'save', disk_full)
        with pytest.raises(OSError, match='disk full'):
            commands[command]()
    assert service.get_record(lesson.id) is record
    assert record.queue is queue is lesson.queue
    assert lesson.queue.entries[0] is entry
    assert record.entry_metadata[entry.id] is metadata
    assert record_to_dict(record) == before
    assert record_to_dict(service.repository.get(lesson.id)) == before
    commands[command]()
    assert service.get_record(lesson.id).version == before['version'] + 1


def test_failed_first_join_leaves_no_ghost_entry(state, monkeypatch):
    service, _, lesson = state
    with monkeypatch.context() as failure:
        failure.setattr(service.repository, 'create', disk_full)
        with pytest.raises(OSError):
            service.join(lesson.id, eligible(), now=NOW)
    assert lesson.queue.entries == []
    assert service.get_record(lesson.id).entry_metadata == {}
    assert service.active_entry('os', 'student') is None
    assert service.repository.get(lesson.id) is None
    service.join(lesson.id, eligible(), now=NOW)
    assert len(service.repository.get(lesson.id).queue.entries) == 1


@pytest.mark.parametrize('failure_point', ['serialization', 'replace'])
def test_real_file_write_failure_preserves_previous_yaml(state, monkeypatch, failure_point):
    service, _, lesson = state
    service.join(lesson.id, eligible(), now=NOW)
    path = service.repository.directory / f'{lesson.id}.yaml'
    previous = path.read_bytes()
    with monkeypatch.context() as failure:
        if failure_point == 'serialization':
            def partial_write(data, stream, **kwargs):
                stream.write('queue: [incomplete')
                raise OSError('disk full')
            failure.setattr('queues.repositories.storage.file.queue_repository.yaml.safe_dump', partial_write)
        else:
            failure.setattr('queues.repositories.storage.file.queue_repository.os.replace', disk_full)
        with pytest.raises(OSError):
            service.call_next(lesson.id, now=NOW)
    assert path.read_bytes() == previous
    assert service.get_record(lesson.id).entry_metadata[lesson.queue.entries[0].id].status == EntryStatus.WAITING
    assert list(service.repository.directory.iterdir()) == [path]


def test_failed_finish_cannot_allow_concurrent_duplicate_after_restart(state, monkeypatch):
    service, schedule, lesson = state
    entry = service.join(lesson.id, eligible(), now=NOW)
    service.call_next(lesson.id, now=NOW)
    second = schedule.lessons[1]
    service.set_registration(second.id, closed=False)
    entered, release, join_started = Event(), Event(), Event()
    original_save = service.repository.save

    def fail_finish(record, **kwargs):
        if record.id == lesson.id and record.entry_metadata[entry.id].status == EntryStatus.FINISHED:
            entered.set()
            assert release.wait(5)
            raise OSError('disk full')
        return original_save(record, **kwargs)

    def join_second():
        join_started.set()
        return service.join(second.id, eligible(), now=NOW)

    monkeypatch.setattr(service.repository, 'save', fail_finish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        finish = pool.submit(service.finish, lesson.id, entry.id, DefenseResult.DEFENDED, now=NOW)
        try:
            assert entered.wait(5)
            join = pool.submit(join_second)
            assert join_started.wait(5)
        finally:
            release.set()
        with pytest.raises(OSError):
            finish.result(timeout=5)
        with pytest.raises(AlreadyQueuedError):
            join.result(timeout=5)
    restarted, _, _ = build(service.repository)
    active = [(record, meta) for record in restarted._records.values()
              for meta in record.entry_metadata.values()
              if meta.status in {EntryStatus.WAITING, EntryStatus.CALLED}]
    assert len(active) == 1
    assert active[0][1].status == EntryStatus.CALLED


def test_save_lesson_only_publishes_synced_fields_after_success(state, monkeypatch):
    service, _, lesson = state
    record = service.save_lesson(lesson.id)
    previous_room = record.lesson_room
    lesson.room = 'new room'
    with monkeypatch.context() as failure:
        failure.setattr(service.repository, 'save', disk_full)
        with pytest.raises(OSError):
            service.save_lesson(lesson.id)
    assert record.lesson_room == previous_room
    assert service.repository.get(lesson.id).lesson_room == previous_room
    service.save_lesson(lesson.id)
    assert record.lesson_room == 'new room'


@pytest.mark.parametrize('result', list(DefenseResult))
def test_archived_defense_result_and_time_survive_reload(state, result):
    service, schedule, lesson = state
    entry = service.join(lesson.id, eligible(), now=NOW)
    service.call_next(lesson.id, now=NOW)
    service.finish(lesson.id, entry.id, result, now=NOW)
    service.request_archive(lesson.id)
    history = AsyncMock()
    asyncio.run(QueueHistoryService(service, history).save_history(schedule.id, lesson.id))
    history.save.assert_awaited_once()
    assert service.get_record(lesson.id) is None
    assert service.active_entry('os', 'student') is None
    restarted, _, _ = build(service.repository)
    assert restarted.get_lesson('os', lesson.id) is None
    entries, _ = restarted.history('os')
    assert len(entries) == 1
    assert entries[0]['result'] == result
    assert entries[0]['status'] == EntryStatus.FINISHED
    assert entries[0]['finished_at'] == NOW


def test_failed_archive_commit_retains_history_and_can_be_retried(state, monkeypatch):
    service, schedule, lesson = state
    entry = service.join(lesson.id, eligible(), now=NOW)
    service.call_next(lesson.id, now=NOW)
    service.finish(lesson.id, entry.id, DefenseResult.DEFENDED, now=NOW)
    history = AsyncMock()
    exporter = QueueHistoryService(service, history)
    with monkeypatch.context() as failure:
        failure.setattr(service.repository, 'save', disk_full)
        with pytest.raises(OSError):
            asyncio.run(exporter.save_history(schedule.id, lesson.id))
    assert not lesson.queue._archiving
    assert service.history('os')[0][0]['result'] == DefenseResult.DEFENDED
    asyncio.run(exporter.save_history(schedule.id, lesson.id))
    restarted, _, _ = build(service.repository)
    assert restarted.history('os')[0][0]['result'] == DefenseResult.DEFENDED
