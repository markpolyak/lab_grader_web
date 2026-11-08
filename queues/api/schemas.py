"""Pydantic-схемы HTTP-слоя очереди.

Здесь только DTO запросов и ответов: валидация, сериализация и OpenAPI.
Доменные модели лежат в queues/domain, хранение — в queues/repositories.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from ..domain.status import DefenseResult, EntryStatus


class AdminQueueLessonResponse(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    groups: list[str]
    kind: str
    room: str | None
    cancelled: bool = False
    original_starts_at: datetime | None = None


class AdminQueueSummaryResponse(BaseModel):
    queue_id: uuid.UUID
    lesson: AdminQueueLessonResponse
    closed: bool
    entries_count: int


class AdminQueuesResponse(BaseModel):
    course_id: str
    course_name: str
    week_start: date
    week_end: date
    queues: list[AdminQueueSummaryResponse]


class AdminQueueEntryResponse(BaseModel):
    id: uuid.UUID
    position: int | None = None
    github: str
    full_name: str
    lab_id: str
    lab_name: str | None = None
    status: EntryStatus
    joined_at: datetime
    called_at: datetime | None = None
    finished_at: datetime | None = None
    result: DefenseResult | None = None


class AdminQueueDetailResponse(BaseModel):
    queue_id: uuid.UUID
    course_id: str
    lesson: AdminQueueLessonResponse
    closed: bool
    version: int
    waiting: list[AdminQueueEntryResponse]
    called: list[AdminQueueEntryResponse]
    finished: list[AdminQueueEntryResponse]
    entries: list[AdminQueueEntryResponse]


class QueueRegistrationRequest(BaseModel):
    closed: bool


class QueueRegistrationResponse(BaseModel):
    queue_id: uuid.UUID
    closed: bool
    version: int


class QueueCreateRequest(BaseModel):
    group_ids: list[str] = Field(..., min_length=1)
    lesson_starts_at: datetime
    lesson_ends_at: datetime
    registration_opens_at: datetime | None = None
    registration_closes_at: datetime | None = None
    service_until: datetime | None = None
    closed: bool = False


class QueueCreateResponse(BaseModel):
    queue_id: uuid.UUID
    version: int


class QueueUpdateRequest(BaseModel):
    cancelled: bool | None = None
    lesson_starts_at: datetime | None = None
    lesson_ends_at: datetime | None = None
    registration_opens_at: datetime | None = None
    registration_closes_at: datetime | None = None
    service_until: datetime | None = None


class QueueUpdateResponse(BaseModel):
    queue_id: uuid.UUID
    version: int


class QueueDeletedResponse(BaseModel):
    deleted: bool


class QueueEntryRemovedResponse(BaseModel):
    removed: bool


class QueueJoinRequest(BaseModel):
    github: str = Field(..., min_length=1)
    full_name: str | None = Field(default=None, min_length=1)
    surname: str | None = None
    name: str | None = None
    patronymic: str | None = None
    lesson_id: uuid.UUID
    lab_id: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_name_parts(self):
        if any(part is not None for part in (self.surname, self.name, self.patronymic)):
            if not (self.surname and self.surname.strip() and self.name and self.name.strip()):
                raise ValueError("Укажите фамилию и имя")
        return self

    @property
    def submitted_full_name(self) -> str | None:
        if self.surname is not None:
            return " ".join(part.strip() for part in (self.surname, self.name, self.patronymic or "") if part and part.strip())
        return self.full_name


class QueueLeaveRequest(BaseModel):
    github: str = Field(..., min_length=1)
    entry_id: uuid.UUID


class QueueLeaveResponse(BaseModel):
    left: bool


class QueueFinishRequest(BaseModel):
    outcome: DefenseResult | None = None


class StudentQueueEntryResponse(BaseModel):
    id: uuid.UUID
    lesson_id: uuid.UUID
    lab_id: str
    lab_name: str | None = None
    status: EntryStatus
    position: int | None = None
    joined_at: datetime
    called_at: datetime | None = None


class StudentQueueStateResponse(BaseModel):
    github: str
    group_id: str
    full_name: str
    entry: StudentQueueEntryResponse | None = None


class QueueParticipantResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    github: str
    lab_id: str
    status: EntryStatus
    lab_name: str | None = None
    position: int | None = None


class QueueLabResponse(BaseModel):
    id: str
    name: str


class LessonQueueResponse(BaseModel):
    queue_id: uuid.UUID
    course_id: str
    course_name: str
    lesson: AdminQueueLessonResponse
    closed: bool
    version: int
    waiting: list[QueueParticipantResponse]
    called: list[QueueParticipantResponse]
    entries: list[QueueParticipantResponse]
    labs: list[QueueLabResponse]


class QueueEntryActionResponse(BaseModel):
    entry: AdminQueueEntryResponse
    version: int


class QueueHistoryEntryResponse(BaseModel):
    queue_id: uuid.UUID
    lesson_starts_at: datetime | None = None
    group_id: str
    github: str
    full_name: str
    lab_id: str
    lab_name: str | None = None
    status: EntryStatus
    result: DefenseResult | None = None
    finished_at: datetime


class QueueHistoryResponse(BaseModel):
    entries: list[QueueHistoryEntryResponse]
    next_cursor: str | None = None
