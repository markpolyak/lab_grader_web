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


_STATUS_MAP: dict[str, int] = {
    "SESSION_REQUIRED": 401, "ALREADY_IN_QUEUE": 409, "NOT_IN_QUEUE": 404,
    "NOT_WAITING": 409, "INVALID_STATUS": 400, "INVALID_SHIFT": 400,
    "CI_NOT_PASSED": 400, "CI_PENDING": 409, "ALREADY_PASSED": 400,
    "NO_TEAM": 400, "QUEUE_CLOSED": 403,
}

class QueueError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message
        self.http_status = _STATUS_MAP.get(code, 500)  # plain attribute
        super().__init__(f"{code}: {message}")