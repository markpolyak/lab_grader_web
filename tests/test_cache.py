from grading.queue import cache
from grading.queue.locks import reset_locks, sheet_lock


def setup_function():
    cache.reset_queue_cache()
    reset_locks()


def test_read_through_cache_hits_until_invalidated():
    calls = []

    def build():
        calls.append("build")
        return [1, 2]

    first = cache.get_queue("lock", "course", "group", "lab", "2026-11-08", build)
    second = cache.get_queue("lock", "course", "group", "lab", "2026-11-08", build)

    assert first == [1, 2]
    assert second == [1, 2]
    assert calls == ["build"]

    cache.invalidate_queue("lock", "course", "group", "lab", "2026-11-08")
    cache.get_queue("lock", "course", "group", "lab", "2026-11-08", build)
    assert calls == ["build", "build"]


def test_cache_expires_and_is_scoped_by_course_and_day(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    calls = []

    def build():
        calls.append("build")
        return calls.copy()

    cache.get_queue("lock", "course", "group", "lab", "2026-11-08", build)
    cache.get_queue("lock", "course", "group", "lab", "2026-11-08", build)
    cache.get_queue("lock", "other", "group", "lab", "2026-11-08", build)
    cache.get_queue("lock", "course", "group", "lab", "2026-11-09", build)
    now[0] += cache.TTL_SECONDS + 1
    cache.get_queue("lock", "course", "group", "lab", "2026-11-08", build)

    assert len(calls) == 4


def test_cache_builder_runs_under_course_lock():
    observed = []

    def build():
        observed.append(sheet_lock("lock").locked())
        return []

    cache.get_queue("lock", "course", "group", "lab", "2026-11-08", build)

    assert observed == [True]


def test_locked_helpers_do_not_reacquire_course_lock():
    observed = []

    with sheet_lock("lock"):
        cache.get_queue_locked(
            "course", "group", "lab", "2026-11-08",
            lambda: observed.append("built") or [1],
        )
        cache.invalidate_queue_locked("course", "group", "lab", "2026-11-08")
        cache.invalidate_course_locked("course")

    assert observed == ["built"]
