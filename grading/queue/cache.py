"""Read-through cache for active queue sheets."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from .locks import locked_course

T = TypeVar("T")
QueueCacheKey = tuple[str, str, str, str]
TTL_SECONDS = 30


@dataclass(frozen=True)
class _CacheEntry:
    rows: tuple[object, ...]
    expires_at: float


_queue_cache: dict[QueueCacheKey, _CacheEntry] = {}
_cache_guard = threading.Lock()


def get_queue(
    lock_key: str,
    course_id: str,
    group_id: str,
    lab_key: str,
    today: str,
    builder: Callable[[], list[T]],
) -> list[T]:
    """Get cached rows or build them while holding the course lock.

    ``lock_key`` is the spreadsheet ID — the actual shared resource that
    ``sheet_lock`` serializes.  ``course_id`` is used only as part of the
    cache key so that rows from different courses never collide.
    """
    with locked_course(lock_key):
        return get_queue_locked(course_id, group_id, lab_key, today, builder)


def get_queue_locked(
    course_id: str,
    group_id: str,
    lab_key: str,
    today: str,
    builder: Callable[[], list[T]],
) -> list[T]:
    """Get/build rows when the caller already holds ``sheet_lock(lock_key)``."""
    key = (course_id, group_id, lab_key, today)
    now = time.monotonic()
    with _cache_guard:
        entry = _queue_cache.get(key)
        if entry is not None and now < entry.expires_at:
            return list(entry.rows)

    rows = builder()
    with _cache_guard:
        _queue_cache[key] = _CacheEntry(tuple(rows), now + TTL_SECONDS)
    return list(rows)


def invalidate_queue(
    lock_key: str,
    course_id: str,
    group_id: str,
    lab_key: str,
    today: str,
) -> None:
    """Invalidate one key, acquiring the course lock."""
    with locked_course(lock_key):
        invalidate_queue_locked(course_id, group_id, lab_key, today)


def invalidate_queue_locked(
    course_id: str,
    group_id: str,
    lab_key: str,
    today: str,
) -> None:
    """Invalidate one key when the caller already owns the course lock."""
    key = (course_id, group_id, lab_key, today)
    with _cache_guard:
        _queue_cache.pop(key, None)


def invalidate_course(
    lock_key: str,
    course_id: str,
) -> None:
    """Invalidate all cached daily queues for a course."""
    with locked_course(lock_key):
        invalidate_course_locked(course_id)


def invalidate_course_locked(course_id: str) -> None:
    """Invalidate a course cache when the caller already owns its lock."""
    with _cache_guard:
        stale_keys = [key for key in _queue_cache if key[0] == course_id]
        for key in stale_keys:
            _queue_cache.pop(key, None)


def reset_queue_cache() -> None:
    """Clear all cached rows in isolated tests only."""
    with _cache_guard:
        _queue_cache.clear()
