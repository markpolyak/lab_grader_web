from enum import StrEnum


class EntryStatus(StrEnum):
    WAITING = "waiting"
    CALLED = "called"
    FINISHED = "finished"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class DefenseResult(StrEnum):
    DEFENDED = "defended"
    NOT_DEFENDED = "not_defended"
    ABSENT = "absent"


class RegistrationMode(StrEnum):
    AUTOMATIC = "automatic"
    MANUALLY_OPENED = "manually_opened"
    MANUALLY_CLOSED = "manually_closed"
