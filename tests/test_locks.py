import threading

from grading.queue.locks import locked_course, reset_locks, sheet_lock


def setup_function():
    reset_locks()


def test_same_course_uses_same_lock_and_other_course_is_independent():
    assert sheet_lock("course-a") is sheet_lock("course-a")
    assert sheet_lock("course-a") is not sheet_lock("course-b")


def test_locked_course_releases_after_exception():
    lock = sheet_lock("course")
    try:
        with locked_course("course"):
            assert lock.locked()
            raise RuntimeError("stop")
    except RuntimeError:
        pass

    assert not lock.locked()


def test_course_lock_serializes_threads():
    lock = sheet_lock("course")
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def first():
        with lock:
            entered.set()
            release.wait(timeout=2)

    def second():
        with lock:
            finished.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    assert entered.wait(timeout=1)
    second_thread.start()
    assert not finished.wait(timeout=0.05)
    release.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert finished.is_set()
