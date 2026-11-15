"""Per-course locks for queue-sheet operations."""

import threading
from contextlib import contextmanager
from collections.abc import Iterator


_sheet_locks: dict[str, threading.Lock] = {}
_locks_mutex = threading.Lock()


def sheet_lock(course_id: str) -> threading.Lock:
    """Get the lock that serializes operations on one course spreadsheet."""
    with _locks_mutex:
        lock = _sheet_locks.get(course_id)
        if lock is None:
            lock = threading.Lock()
            _sheet_locks[course_id] = lock
        return lock


@contextmanager
def locked_course(course_id: str) -> Iterator[None]:
    """Acquire the course lock around an operation."""
    with sheet_lock(course_id):
        yield


def reset_locks() -> None:
    """Clear locks in isolated tests only."""
    with _locks_mutex:
        _sheet_locks.clear()
