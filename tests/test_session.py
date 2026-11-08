from itsdangerous import TimestampSigner

from grading.queue.errors import QueueError
from grading.queue.session import (
    build_queue_session,
    parse_queue_session,
    require_queue_session,
)


def test_queue_session_round_trip_and_scope_validation():
    signer = TimestampSigner("test-secret")
    token = build_queue_session(signer, "student", "course", "4431", "lab1")

    payload = parse_queue_session(signer, token)
    assert payload == {
        "username": "student",
        "course_id": "course",
        "group_id": "4431",
        "lab_key": "lab1",
    }
    assert require_queue_session(signer, token, "course", "4431", "lab1") == "student"

    try:
        require_queue_session(signer, token, "course", "4432", "lab1")
    except QueueError as error:
        assert error.code == "SESSION_REQUIRED"
    else:
        raise AssertionError("A session for another group must be rejected")


def test_queue_session_rejects_tampered_or_expired_cookie():
    signer = TimestampSigner("test-secret")
    token = build_queue_session(signer, "student", "course", "4431", "lab1")

    assert parse_queue_session(signer, token + "tampered") is None
    assert parse_queue_session(signer, None) is None
