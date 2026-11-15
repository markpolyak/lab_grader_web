"""In-memory caches for the queue read/write paths (задача «лимиты по плану»).

Кэши уменьшают число вызовов Google Sheets API до целевых значений
(COLD GET ≤ 2, WARM GET = 0, STATUS ≤ 1, REGISTER ≤ 3/запись):

- ``get_queue`` / ``invalidate_queue`` / ``append_row_to_cache`` — кэш строк
  дневного листа, TTL 10 с. Ключ ``(spreadsheet_id, today_str)`` — лист
  общий для всех лаб (issue #62), lab_key в ключ не входит.
- ``get_history_values`` / ``invalidate_history`` — кэш листа
  «История_очереди», TTL 120 с (append-only, растёт линейно).
- ``with_retry`` — экспоненциальный backoff с jitter на 429 от Sheets API.

``reset_queue_cache`` вызывается тестами (в т.ч. нагрузочным стендом) для
чистого старта.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from gspread.exceptions import APIError

from .locks import locked_course

T = TypeVar("T")

TTL_SECONDS = 10.0
HISTORY_TTL_SECONDS = 120.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CacheEntry:
    rows: list
    filled_at: float


_queue_cache: dict[tuple[str, str], _CacheEntry] = {}
_history_cache: dict[int, _CacheEntry] = {}
_cache_guard = threading.Lock()


def get_queue(
    spreadsheet_id: str,
    today_str: str,
    builder: Callable[[], list[T]],
) -> list[T]:
    """Cached queue rows; on miss build them under the spreadsheet lock.

    ``locked_course(spreadsheet_id)`` сериализует одновременные cold-read:
    из N параллельных запросов builder() выполняет ровно один, остальные
    берут строки из кэша.
    """
    with locked_course(spreadsheet_id):
        return get_queue_locked(spreadsheet_id, today_str, builder)


def get_queue_locked(
    spreadsheet_id: str,
    today_str: str,
    builder: Callable[[], list[T]],
) -> list[T]:
    """Cached rows when the caller already holds the spreadsheet lock."""
    key = (spreadsheet_id, today_str)
    now = time.monotonic()
    with _cache_guard:
        entry = _queue_cache.get(key)
        if entry is not None and now < entry.filled_at + TTL_SECONDS:
            return list(entry.rows)
    rows = with_retry(builder)
    with _cache_guard:
        _queue_cache[key] = _CacheEntry(list(rows), time.monotonic())
    return list(rows)


def append_row_to_cache(
    spreadsheet_id: str,
    today_str: str,
    row: T,
) -> None:
    """In-place cache update после успешного append_row (без перечитывания).

    Timestamp заполнения не трогаем — TTL продолжает отсчитываться от
    исходного чтения, ручные правки в таблице подхватываются как раньше.
    """
    key = (spreadsheet_id, today_str)
    with _cache_guard:
        entry = _queue_cache.get(key)
        if entry is not None:
            entry.rows.append(row)


def invalidate_queue(spreadsheet_id: str, today_str: str) -> None:
    """Drop the cached queue sheet rows (после мутаций со сменой статусов)."""
    with _cache_guard:
        _queue_cache.pop((spreadsheet_id, today_str), None)


def reset_queue_cache() -> None:
    """Clear all cached rows (изолированные тесты / нагрузочный стенд)."""
    with _cache_guard:
        _queue_cache.clear()


def get_history_values(
    history_worksheet,
    builder: Callable[[], list],
) -> list:
    """Cached «История_очереди» values, TTL 120 c (append-only лист)."""
    key = id(history_worksheet)
    now = time.monotonic()
    with _cache_guard:
        entry = _history_cache.get(key)
        if entry is not None and now < entry.filled_at + HISTORY_TTL_SECONDS:
            return list(entry.rows)
    values = with_retry(builder)
    with _cache_guard:
        _history_cache[key] = _CacheEntry(list(values), time.monotonic())
    return list(values)


def invalidate_history(history_worksheet) -> None:
    """Drop the cached history values (после дописывания строк)."""
    with _cache_guard:
        _history_cache.pop(id(history_worksheet), None)


def with_retry(func: Callable[[], T], max_retries: int = 3) -> T:
    """Экспоненциальный backoff с jitter на 429 от Sheets API (план, П.13)."""
    for attempt in range(max_retries + 1):
        try:
            return func()
        except APIError as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            if status != 429 or attempt == max_retries:
                raise
            sleep_for = (2**attempt) + random.uniform(0, 0.5)
            logger.warning(
                "Sheets 429, retry %d/%d after %.1fs",
                attempt + 1,
                max_retries,
                sleep_for,
            )
            time.sleep(sleep_for)
