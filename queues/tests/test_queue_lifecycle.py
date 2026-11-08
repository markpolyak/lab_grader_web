"""Clock boundaries and archive isolation against persisted schedule refreshes."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import yaml

from grading.queue_eligibility import EligibleStudentLab
from queues.domain import DefenseResult, EntryStatus, Student
from queues.domain.err import InvalidStateError, QueueClosedError
from queues.domain.status import RegistrationMode
from queues.repositories.storage.file import FileQueueRepository
from queues.tests.test_queue_service import MONDAY, courses, document
from queues.usecase.queue_history_service import QueueHistoryService
from queues.usecase.queue_service import QueueService


def at(value):
    return datetime.fromisoformat('2026-11-09T' + value + '+03:00')


def eligible(github='student'):
    return EligibleStudentLab(student=Student('Иван', 'Иванов', '', github),
                              full_name='Иванов Иван', group_id='4431', lab_id='1', lab_name='ЛР1')


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr('queues.usecase.queue_service._now', lambda: at('15:10'))
    service = QueueService(FileQueueRepository(tmp_path / 'state'))
    schedule = service.generate_week(courses(), [document()], week_start=MONDAY)['os']
    return service, schedule, schedule.lessons[0]


@pytest.mark.parametrize('clock,opened', [
    ('14:54:59', False), ('14:55', True), ('15:10', True),
    ('16:39:59', True), ('16:40', False), ('23:59:59', False),
])
def test_automatic_registration_boundaries(state, clock, opened):
    service, _, lesson = state
    service.reconcile(now=at(clock))
    assert lesson.queue.closed is not opened
    if opened:
        service.join(lesson.id, eligible(), now=at(clock))
    else:
        with pytest.raises(QueueClosedError):
            service.join(lesson.id, eligible(), now=at(clock))


def test_custom_window_and_configurable_lead_time(state):
    service, _, lesson = state
    service.opens_before_minutes = 30
    service.join(lesson.id, eligible(), now=at('14:40'))
    service.update_queue(lesson.id, registration_opens_at=at('15:00'),
                         registration_closes_at=at('16:00'), service_until=at('20:00'))
    record = service.get_record(lesson.id)
    assert not record.is_open_at(at('14:59:59'))
    assert record.is_open_at(at('15:00'))
    assert not record.is_open_at(at('16:00'))
    service.reconcile(now=at('20:00'))
    assert service.active_entry('os', 'student') is None


def test_service_continues_after_lesson_then_expires_and_persists(state):
    service, _, lesson = state
    first = service.join(lesson.id, eligible('first'), now=at('15:00'))
    second = service.join(lesson.id, eligible('second'), now=at('15:01'))
    third = service.join(lesson.id, eligible('third'), now=at('15:02'))
    service.call_next(lesson.id, now=at('17:00'))
    service.finish(lesson.id, first.id, DefenseResult.DEFENDED, now=at('18:00'))
    service.call_next(lesson.id, now=at('23:59:59'))
    midnight = at('00:00') + timedelta(days=1)
    service.reconcile(now=midnight)
    record = service.repository.get(lesson.id)
    assert record.entry_metadata[first.id].status == EntryStatus.FINISHED
    for entry in (second, third):
        assert record.entry_metadata[entry.id].status == EntryStatus.EXPIRED
        assert record.entry_metadata[entry.id].finished_at == midnight
    assert service.active_entry('os', 'second') is None
    with pytest.raises(InvalidStateError):
        service.finish(lesson.id, second.id, now=midnight)


def test_manual_override_survives_refresh_but_not_service_deadline(state):
    service, _, lesson = state
    service.set_registration(lesson.id, closed=True)
    service.reconcile(now=at('15:10'))
    assert lesson.queue.closed
    service.set_registration(lesson.id, closed=False)
    service.generate_week(courses(), [document()], week_start=MONDAY)
    record = service.get_record(lesson.id)
    assert record.registration_mode == RegistrationMode.MANUALLY_OPENED
    assert record.is_open_at(at('17:00'))
    assert not record.is_open_at(at('00:00') + timedelta(days=1))
    service.update_queue(lesson.id, cancelled=True)
    assert not service.get_record(lesson.id).is_open_at(at('15:10'))


@pytest.mark.parametrize('action', [
    'set_registration', 'call_next', 'finish', 'leave', 'remove_entry',
    'update_result', 'update_queue', 'delete_queue', 'save_lesson', 'join',
])
def test_all_lesson_mutations_are_rejected_while_archiving(state, action):
    service, schedule, lesson = state
    entry = service.join(lesson.id, eligible(), now=at('15:10'))
    snapshot = deepcopy(service.get_record(lesson.id))
    service.begin_archive(schedule.id, lesson.id)
    calls = {
        'set_registration': lambda: service.set_registration(lesson.id, closed=False),
        'call_next': lambda: service.call_next(lesson.id),
        'finish': lambda: service.finish(lesson.id, entry.id),
        'leave': lambda: service.leave('os', entry.id, 'student'),
        'remove_entry': lambda: service.remove_entry(lesson.id, entry.id),
        'update_result': lambda: service.update_result(lesson.id, entry.id, DefenseResult.ABSENT),
        'update_queue': lambda: service.update_queue(lesson.id, cancelled=True),
        'delete_queue': lambda: service.delete_queue(lesson.id),
        'save_lesson': lambda: service.save_lesson(lesson.id),
        'join': lambda: service.join(lesson.id, eligible('other')),
    }
    with pytest.raises(InvalidStateError, match='сохраняется'):
        calls[action]()
    record = service.get_record(lesson.id)
    assert record.version == snapshot.version
    assert record.entry_metadata == snapshot.entry_metadata
    service.cancel_archive(lesson.id)
    service.set_registration(lesson.id, closed=True)


@pytest.mark.parametrize('manual', [False, True])
def test_refresh_during_external_save_then_archive_and_restart(state, manual, tmp_path):
    service, schedule, lesson = state
    if manual:
        record = service.create_manual('os', 'Операционные системы', week_start=MONDAY,
                                       group_ids=['4431'], starts_at=at('15:10'), ends_at=at('16:40'))
        lesson = service.get_lesson('os', record.id)
    service.join(lesson.id, eligible(), now=at('15:10'))
    history = AsyncMock()
    schedule_file = tmp_path / 'schedule' / 'suai' / 'term' / 'teacher.yaml'
    schedule_file.parent.mkdir(parents=True)
    schedule_file.write_text(yaml.safe_dump(document()))

    async def save(snapshot, **kwargs):
        original_queue = lesson.queue
        service.load_saved_week(courses(), tmp_path / 'schedule', week_start=MONDAY)
        assert lesson.queue is original_queue
        assert lesson.queue._archiving
        service.reconcile(now=at('00:00') + timedelta(days=1))
        with pytest.raises(InvalidStateError):
            service.set_registration(lesson.id, closed=False)

    history.save.side_effect = save
    asyncio.run(QueueHistoryService(service, history).save_history(schedule.id, lesson.id))
    assert service.get_record(lesson.id) is None
    assert service.active_entry('os', 'student') is None
    assert service.repository.get(lesson.id).archived
    restarted = QueueService(service.repository)
    restarted.generate_week(courses(), [document()], week_start=MONDAY)
    assert restarted.get_lesson('os', lesson.id) is None


def test_archive_clears_local_state_after_end_of_day_expiry(state, monkeypatch):
    service, schedule, lesson = state
    entry = service.join(lesson.id, eligible(), now=at('15:10'))
    monkeypatch.setattr('queues.usecase.queue_service._now',
                        lambda: at('00:00') + timedelta(days=1))
    service.reconcile()
    assert service.repository.get(lesson.id).entry_metadata[entry.id].status == EntryStatus.EXPIRED
    asyncio.run(QueueHistoryService(service, AsyncMock()).save_history(schedule.id, lesson.id))
    record = service.repository.get(lesson.id)
    assert record.archived
    assert record.queue.entries == []
    assert record.entry_metadata == {}
    items, _ = service.history('os')
    assert items == []


@pytest.mark.parametrize('clock,finished', [
    ('15:10', False), ('16:39:59', False), ('16:40', True), ('17:00', True),
])
def test_archive_candidates_use_lesson_end(state, monkeypatch, clock, finished):
    service, schedule, lesson = state
    monkeypatch.setattr('queues.usecase.queue_service._now', lambda: at(clock))
    assert service.finished_lessons() == ([(schedule.id, lesson.id)] if finished else [])


def test_expiry_write_failure_can_be_retried(state, monkeypatch):
    service, _, lesson = state
    entry = service.join(lesson.id, eligible(), now=at('15:10'))
    original_save = service.repository.save
    monkeypatch.setattr(service.repository, 'save', lambda *a, **kw: (_ for _ in ()).throw(OSError('disk full')))
    midnight = at('00:00') + timedelta(days=1)
    with pytest.raises(OSError):
        service.reconcile(now=midnight)
    assert service.get_record(lesson.id).entry_metadata[entry.id].status == EntryStatus.WAITING
    monkeypatch.setattr(service.repository, 'save', original_save)
    service.reconcile(now=midnight)
    assert service.repository.get(lesson.id).entry_metadata[entry.id].status == EntryStatus.EXPIRED


def test_empty_manual_queue_cannot_be_deleted_while_archiving(state):
    service, schedule, lesson = state
    record = service.create_manual('os', 'ОС', week_start=MONDAY, group_ids=['4431'],
                                   starts_at=at('15:10'), ends_at=at('16:40'))
    service.begin_archive(schedule.id, record.id)
    with pytest.raises(InvalidStateError, match='сохраняется'):
        service.delete_queue(record.id)
    # Creating a distinct lesson must not unfreeze or replace an existing queue.
    service.create_manual('os', 'ОС', week_start=MONDAY, group_ids=['4431'],
                          starts_at=at('17:00'), ends_at=at('18:30'))
    assert service.get_record(record.id).queue._archiving
    service.complete_archive(record.id)


def test_failed_external_save_unfreezes_after_refresh_and_keeps_entries(state):
    service, schedule, lesson = state
    entry = service.join(lesson.id, eligible(), now=at('15:10'))

    async def save(*args, **kwargs):
        service.generate_week(courses(), [document()], week_start=MONDAY)
        raise TimeoutError('offline')

    history = AsyncMock()
    history.save.side_effect = save
    with pytest.raises(TimeoutError):
        asyncio.run(QueueHistoryService(service, history).save_history(schedule.id, lesson.id))
    assert not lesson.queue._archiving
    assert service.active_entry('os', 'student')[1].id == entry.id
    service.call_next(lesson.id, now=at('15:10'))
