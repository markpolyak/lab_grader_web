"""Domain errors and HTTP status mapping for queue services."""


ERROR_HTTP_STATUS = {
    "SESSION_REQUIRED": 401,
    "NOT_IN_GROUP": 403,
    "QUEUE_CLOSED": 403,
    "ALREADY_IN_QUEUE": 409,
    "ALREADY_PASSED": 400,
    "NOT_IN_QUEUE": 404,
    "NOT_WAITING": 409,
    "INVALID_SHIFT": 400,
    "AMBIGUOUS_STUDENT": 409,
    "CI_PENDING": 409,
    "CI_NOT_PASSED": 400,
    "CI_CHECK_FAILED": 502,
    "NO_TEAM": 400,
    "TEAMS_UNAVAILABLE": 502,
    "SHEETS_UNAVAILABLE": 503,
    "SHEETS_SCHEMA_MISMATCH": 500,
    "INVALID_STATUS": 400,
    "INVALID_QUEUE_SHEET": 500,
}


class QueueError(Exception):
    """Queue service error with a stable API code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        self.http_status = ERROR_HTTP_STATUS.get(code, 400)
        super().__init__(f"{code}: {message}" if message else code)
