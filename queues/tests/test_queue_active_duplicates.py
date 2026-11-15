import pytest

from queues.domain import Queue, Student


def student(github="student"):
    return Student("Иван", "Иванов", "", github)


def test_default_duplicate_check_keeps_colleague_behavior():
    queue = Queue(closed=False)
    queue.add_entry(student(), "1")

    with pytest.raises(ValueError):
        queue.add_entry(student(), "2")


def test_active_ids_allow_duplicate_after_terminal_entry():
    queue = Queue(closed=False)
    first = queue.add_entry(student(), "1")

    second = queue.add_entry(student(), "2", active_entry_ids=set())

    assert first.id != second.id
    assert len(queue.entries) == 2


def test_active_ids_still_reject_duplicate_of_active_entry():
    queue = Queue(closed=False)
    first = queue.add_entry(student(), "1")

    with pytest.raises(ValueError):
        queue.add_entry(student(), "2", active_entry_ids={first.id})
