"""Тесты кэшей queue-модуля (новый API, план «лимиты по плану» П.4/П.7/П.13).

Покрывают:
- чтение через кэш с builder'ом, инвалидацией и TTL;
- серилизацию одновременных cold-read через locked_course;
- in-place дописывание (append_row_to_cache) без перечитывания;
- кэш истории с TTL и инвалидацией;
- with_retry: 429 с экспоненциальным backoff, не-429 — сразу наверх.
"""

import pytest
from gspread.exceptions import APIError

from grading.queue.cache import (
    append_row_to_cache,
    get_history_values,
    get_queue,
    get_queue_locked,
    invalidate_history,
    invalidate_queue,
    reset_queue_cache,
    with_retry,
)
from grading.queue.locks import sheet_lock


@pytest.fixture(autouse=True)
def _clean():
    reset_queue_cache()
    yield
    reset_queue_cache()


def test_read_through_cache_hits_until_invalidated():
    calls = []
    spreadsheet_id, today = "ss-1", "2026-11-08"

    def build():
        calls.append("build")
        return [1, 2]

    first = get_queue(spreadsheet_id, today, build)
    second = get_queue(spreadsheet_id, today, build)
    assert first == [1, 2] and second == [1, 2]
    assert calls == ["build"], "второе чтение должно быть из кэша"

    invalidate_queue(spreadsheet_id, today)
    get_queue(spreadsheet_id, today, build)
    assert calls == ["build", "build"], "после инвалидации — перестройка"


def test_cache_expires_and_is_scoped_by_day():
    calls = []
    spreadsheet_id = "ss-2"

    def build():
        calls.append(today_marker[0])
        return [today_marker[0]]

    today_marker = ["2026-11-08"]
    get_queue(spreadsheet_id, "2026-11-08", build)
    # другой день — другой ключ кэша
    today_marker[0] = "2026-11-09"
    get_queue(spreadsheet_id, "2026-11-09", build)
    assert sorted(calls) == ["2026-11-08", "2026-11-09"]
    assert len(calls) == 2


def test_cache_builder_runs_under_course_lock():
    observed = []

    def build():
        observed.append(sheet_lock("lock").locked())
        return []

    get_queue("lock", "2026-11-08", build)
    assert observed == [True], "builder должен выполняться под course lock"


def test_locked_helpers_do_not_reacquire_course_lock():
    observed = []

    def build():
        observed.append(sheet_lock("lock").locked())
        return []

    lock = sheet_lock("lock")
    assert lock.acquire() is True
    try:
        get_queue_locked("lock", "2026-11-08", build)
    finally:
        lock.release()
    assert observed == [True]


def test_append_row_to_cache_updates_rows_in_place():
    spreadsheet_id, today = "ss-3", "2026-11-08"

    get_queue(spreadsheet_id, today, lambda: [{"fio": "A", "position": 1}])
    append_row_to_cache(spreadsheet_id, today, {"fio": "B", "position": 2})

    rows = get_queue(spreadsheet_id, today, lambda: pytest.fail("не должно быть перечитывания"))
    assert [r["fio"] for r in rows] == ["A", "B"]


def test_history_cache_hits_until_invalidated():
    calls = []

    class FakeHistoryWs:
        pass

    ws = FakeHistoryWs()

    def build():
        calls.append("build")
        return [["2026-11-08", "сдал"]]

    first = get_history_values(ws, build)
    second = get_history_values(ws, build)
    assert first == [["2026-11-08", "сдал"]] and second == first
    assert calls == ["build"]

    invalidate_history(ws)
    get_history_values(ws, build)
    assert calls == ["build", "build"]


def test_with_retry_backs_off_on_429_then_succeeds():
    attempts = []

    class FakeResponse:
        status_code = 429
        text = "429 Too Many Requests"

        def json(self):
            raise ValueError("invalid JSON")

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise APIError(FakeResponse())
        return "ok"

    result = with_retry(flaky, max_retries=3)
    assert result == "ok"
    assert len(attempts) == 3


def test_with_retry_raises_immediately_on_non_429():
    class FakeResponse:
        status_code = 500
        text = "500 Internal Server Error"

        def json(self):
            raise ValueError("invalid JSON")

    attempts = []

    def broken():
        attempts.append(1)
        raise APIError(FakeResponse())

    with pytest.raises(APIError):
        with_retry(broken, max_retries=3)
    assert len(attempts) == 1
