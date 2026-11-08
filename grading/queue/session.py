"""Signed queue-session payload helpers for the OAuth integration stage."""

from __future__ import annotations

import base64
import json
from typing import Any

from itsdangerous import BadSignature, TimestampSigner

from .errors import QueueError

QUEUE_SESSION_MAX_AGE = 1800


def build_queue_session(
    signer: TimestampSigner,
    username: str,
    course_id: str,
    group_id: str,
    lab_key: str,
) -> str:
    payload = json.dumps(
        {
            "username": username,
            "course_id": course_id,
            "group_id": group_id,
            "lab_key": lab_key,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii")
    return signer.sign(encoded.encode("ascii")).decode("ascii")


def parse_queue_session(
    signer: TimestampSigner,
    cookie_value: str | None,
) -> dict[str, Any] | None:
    if not cookie_value:
        return None
    try:
        encoded = signer.unsign(cookie_value, max_age=QUEUE_SESSION_MAX_AGE).decode("ascii")
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except (BadSignature, ValueError, TypeError, KeyError):
        return None
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), str) and payload[key]
        for key in ("username", "course_id", "group_id", "lab_key")
    ):
        return None
    return payload


def require_queue_session(
    signer: TimestampSigner,
    cookie_value: str | None,
    course_id: str,
    group_id: str,
    lab_key: str,
) -> str:
    payload = parse_queue_session(signer, cookie_value)
    if payload is None or any(
        payload.get(key) != expected
        for key, expected in (
            ("course_id", course_id),
            ("group_id", group_id),
            ("lab_key", lab_key),
        )
    ):
        raise QueueError("SESSION_REQUIRED", "Нет действующей сессии очереди")
    return payload["username"]
