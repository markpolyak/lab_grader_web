"""Occupied source lessons remain manageable across refreshes and restarts."""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from grading.queue_eligibility import EligibleStudentLab
from queues.domain import DefenseResult, EntryStatus, Student
from queues.repositories.storage.file import FileQueueRepository
from queues.usecase.queue_history_service import QueueHistoryService
from queues.usecase.queue_service import QueueService

MONDAY = date(2026, 11, 9)
NOW = datetime.fromisoformat('2026-11-09T10:00:00+03:00')
COURSES = {'os': {'name': 'ОС', 'university': 'ГУАП'}}


def document(day=MONDAY, start='10:00', end='11:30'):
    return {'university': 'suai', 'days': {day.isoformat(): [
        {'subject': 'ОС', 'kind': 'ЛР', 'groups': ['4431'], 'starts-at': start, 'ends-at': end},
    ]}}


def eligible():
    return EligibleStudentLab(student=Student('Иван', 'Иванов', '', 'student'),
                              full_name='Иванов Иван', group_id='4431', lab_id='1', lab_name='ЛР1')


def load(tmp_path, doc=None, week=MONDAY):
    service = QueueService(FileQueueRepository(tmp_path / 'queue_state'))
    schedule = service.generate_week(COURSES, [doc if doc is not None else document()], week_start=week)['os']
    return service, schedule


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr('queues.usecase.queue_service._now', lambda: NOW)


@pytest.mark.parametrize('restart', [False, True])
def test_provider_move_retains_old_queue_for_service_but_not_new_registration(tmp_path, restart):
    service, schedule = load(tmp_path)
    old = schedule.lessons[0]
    entry = service.join(old.id, eligible(), now=NOW)
    moved = document(start='10:30', end='12:00')
    if restart:
        service, schedule = load(tmp_path, moved)
    else:
        schedule = service.generate_week(COURSES, [moved], week_start=MONDAY)['os']
    assert len(schedule.lessons) == 1
    current = schedule.lessons[0]
    assert current.id != old.id
    assert {item.id for item in service.admin_lessons('os', MONDAY)} == {old.id, current.id}
    service.reconcile(now=NOW + timedelta(minutes=45))
    assert service.get_lesson('os', old.id).queue.closed
    assert service.active_entry('os', 'student')[1].id == entry.id
    service.call_next(old.id, now=NOW + timedelta(minutes=45))
    service.finish(old.id, entry.id, DefenseResult.DEFENDED, now=NOW + timedelta(minutes=46))
    service.join(current.id, eligible(), now=NOW + timedelta(minutes=47))
    assert service.active_entry('os', 'student')[0].id == current.id
    assert service.history('os')[0][0]['result'] == DefenseResult.DEFENDED


@pytest.mark.parametrize('restart', [False, True])
def test_removed_source_lesson_stays_visible_until_waiting_students_are_served(tmp_path, restart):
    service, schedule = load(tmp_path)
    old = schedule.lessons[0]
    entry = service.join(old.id, eligible(), now=NOW)
    removed = {'university': 'suai', 'days': {}}
    if restart:
        service, schedule = load(tmp_path, removed)
    else:
        schedule = service.generate_week(COURSES, [removed], week_start=MONDAY)['os']
    assert schedule.lessons == []
    assert [item.id for item in service.admin_lessons('os', MONDAY)] == [old.id]
    service.call_next(old.id, now=NOW)
    service.finish(old.id, entry.id, DefenseResult.DEFENDED, now=NOW)
    assert service.active_entry('os', 'student') is None


@pytest.mark.parametrize('called', [False, True])
def test_old_week_active_queue_is_restored_expired_and_saved(tmp_path, called):
    service, schedule = load(tmp_path)
    old = schedule.lessons[0]
    entry = service.join(old.id, eligible(), now=NOW)
    if called:
        service.call_next(old.id, now=NOW)
    next_week = MONDAY + timedelta(days=7)
    restarted, current = load(tmp_path, document(next_week), next_week)
    assert restarted.get_record(old.id) is not None
    restarted.reconcile(now=NOW + timedelta(days=7))
    saved = restarted.repository.get(old.id)
    assert saved.entry_metadata[entry.id].status == EntryStatus.EXPIRED
    assert saved.entry_metadata[entry.id].finished_at == NOW.replace(hour=0) + timedelta(days=1)
    assert restarted.active_entry('os', 'student') is None
    assert restarted.history('os')[0][0]['status'] == EntryStatus.EXPIRED
    assert old.id not in {item.id for item in current.lessons}
    restarted.join(current.lessons[0].id, eligible(), now=NOW + timedelta(days=7))
    again, _ = load(tmp_path, document(next_week), next_week)
    assert again.repository.get(old.id).entry_metadata[entry.id].status == EntryStatus.EXPIRED
    assert again.history('os')[0][0]['status'] == EntryStatus.EXPIRED


def test_restored_old_lesson_keeps_original_archive_context(tmp_path, monkeypatch):
    service, schedule = load(tmp_path)
    old = schedule.lessons[0]
    entry = service.join(old.id, eligible(), now=NOW)
    service.call_next(old.id, now=NOW)
    service.finish(old.id, entry.id, DefenseResult.DEFENDED, now=NOW)
    next_week = MONDAY + timedelta(days=7)
    restarted, _ = load(tmp_path, document(next_week), next_week)
    monkeypatch.setattr('queues.usecase.queue_service._now', lambda: NOW + timedelta(days=7))
    assert (schedule.id, old.id) in restarted.finished_lessons()
    history = AsyncMock()
    asyncio.run(QueueHistoryService(restarted, history).save_history(schedule.id, old.id))
    history.save.assert_awaited_once()
    assert restarted.get_lesson('os', old.id) is None
    again, _ = load(tmp_path, document(next_week), next_week)
    assert again.get_record(old.id) is None
    assert again.history('os')[0][0]['result'] == DefenseResult.DEFENDED


def test_restore_then_failed_expiry_keeps_old_queue_retryable(tmp_path, monkeypatch):
    service, schedule = load(tmp_path)
    old = schedule.lessons[0]
    entry = service.join(old.id, eligible(), now=NOW)
    next_week = MONDAY + timedelta(days=7)
    restored, _ = load(tmp_path, document(next_week), next_week)
    def fail(*args, **kwargs):
        raise OSError('disk full')
    with monkeypatch.context() as failure:
        failure.setattr(restored.repository, 'save', fail)
        with pytest.raises(OSError):
            restored.reconcile(now=NOW + timedelta(days=7))
    assert restored.get_record(old.id).entry_metadata[entry.id].status == EntryStatus.WAITING
    assert old.id in {item.id for item in restored.admin_lessons('os', next_week)}
    assert restored.repository.get(old.id).entry_metadata[entry.id].status == EntryStatus.WAITING
    restored.reconcile(now=NOW + timedelta(days=7))
    assert restored.repository.get(old.id).entry_metadata[entry.id].status == EntryStatus.EXPIRED


def test_inflight_archive_retains_frozen_queue_when_provider_moves_it(tmp_path):
    service, schedule = load(tmp_path)
    old = schedule.lessons[0]
    entry = service.join(old.id, eligible(), now=NOW)
    original_queue = old.queue
    service.begin_archive(schedule.id, old.id)
    refreshed = service.generate_week(COURSES, [document(start='10:30', end='12:00')], week_start=MONDAY)['os']
    assert old.id not in {item.id for item in refreshed.lessons}
    assert service.get_record(old.id).queue is original_queue
    assert original_queue._archiving
    service.complete_archive(old.id)
    assert service.repository.get(old.id).entry_metadata[entry.id].status == EntryStatus.EXPIRED


def test_lifespan_expires_previous_week_without_google_history(tmp_path, monkeypatch):
    import main
    service, schedule = load(tmp_path)
    old = schedule.lessons[0]
    entry = service.join(old.id, eligible(), now=NOW)
    next_week = MONDAY + timedelta(days=7)
    directory = tmp_path / 'schedule' / 'suai' / 'term'
    directory.mkdir(parents=True)
    (directory / 'teacher.yaml').write_text(yaml.safe_dump(document(next_week)))
    monkeypatch.setenv('SCHEDULE_DIR', str(tmp_path / 'schedule'))
    monkeypatch.setenv('QUEUE_STATE_DIR', str(tmp_path / 'queue_state'))
    monkeypatch.setenv('QUEUE_WEEK_START', next_week.isoformat())
    monkeypatch.setenv('QUEUE_HISTORY_SPREADSHEET_ID', '')
    monkeypatch.setattr('queues.usecase.queue_service._now', lambda: NOW + timedelta(days=7))
    monkeypatch.setattr(main, 'iter_course_configs', lambda: iter(COURSES.items()))
    monkeypatch.setattr(main, 'ScheduleParser', Mock(return_value=Mock()))
    async def check():
        app = SimpleNamespace(state=SimpleNamespace())
        async with main.lifespan(app):
            restored = app.state.queue_service
            assert restored.get_record(old.id).entry_metadata[entry.id].status == EntryStatus.EXPIRED
            assert restored.history('os')[0][0]['status'] == EntryStatus.EXPIRED
            assert restored.repository.get(old.id).entry_metadata[entry.id].status == EntryStatus.EXPIRED
    asyncio.run(check())
