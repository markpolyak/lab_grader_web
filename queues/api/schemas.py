"""Pydantic-схемы HTTP-слоя очереди.

Здесь только DTO запросов и ответов: валидация, сериализация и OpenAPI.
Доменные модели лежат в queues/domain, хранение — в queues/repositories.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from ..domain.status import DefenseResult, EntryStatus


class AdminQueueLessonResponse(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    groups: list[str]
    kind: str
    room: str | None


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


class QueueRegistrationRequest(BaseModel):
    closed: bool


class QueueRegistrationResponse(BaseModel):
    queue_id: uuid.UUID
    closed: bool
    version: int


class QueueJoinRequest(BaseModel):
    github: str = Field(..., min_length=1)
    lesson_id: uuid.UUID
    lab_id: str = Field(..., min_length=1)


class QueueLeaveRequest(BaseModel):
    github: str = Field(..., min_length=1)
    entry_id: uuid.UUID


class QueueLeaveResponse(BaseModel):
    left: bool


class QueueFinishRequest(BaseModel):
    outcome: DefenseResult


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
