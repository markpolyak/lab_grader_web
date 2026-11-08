from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import RLock
from uuid import NAMESPACE_URL, uuid5
import uuid
from copy import deepcopy
from zoneinfo import ZoneInfo

import yaml

from ..domain.err import (
    AlreadyQueuedError,
    EntryNotFoundError,
    EntryNotOwnedError,
    GroupNotAllowedError,
    InvalidStateError,
    QueueClosedError,
    QueueEmptyError,
    QueueNotEmptyError,
    QueueNotDeletableError,
    QueueNotFoundError,
)
from ..domain.lesson import Lesson
from ..domain.queue import Queue
from ..domain.queue_entry import QueueEntry
from ..domain.queue_record import QueueEntryMetadata, QueueRecord
from ..domain.schedule import Schedule
from ..domain.status import DefenseResult, EntryStatus, RegistrationMode
from ..repositories.mappers import (
    ensure_entry_metadata,
    group_ids,
    group_key,
    record_from_lesson,
    restore_record,
    sync_record_with_lesson,
)
from ..repositories.storage.inmemory import InMemoryQueueRepository

ACTIVE_STATUSES = frozenset({EntryStatus.WAITING, EntryStatus.CALLED})
TERMINAL_STATUSES = frozenset(
    {EntryStatus.FINISHED, EntryStatus.WITHDRAWN, EntryStatus.EXPIRED}
)


def _is_queue_lesson(kind: str) -> bool:
    """Очереди формируются только для лабораторных и практических занятий."""
    return kind.strip().casefold().rstrip(".") in {
        "лабораторное занятие", "лабораторная работа", "лр", "лб",
        "практическое занятие", "практическая работа", "практика", "пр", "пз",
    }


def _full_name(student) -> str:
    return " ".join(
        part for part in (student.surname, student.name, student.patronymic) if part
    )


def _now() -> datetime:
    return datetime.now(ZoneInfo("Europe/Moscow"))


def _validate_lesson_times(starts_at: datetime, ends_at: datetime) -> None:
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise ValueError("Время занятия должно содержать часовой пояс")
    if ends_at <= starts_at:
        raise ValueError("Окончание занятия должно быть позже начала")


def _validate_registration(record: QueueRecord) -> None:
    for value in (
        record.registration_opens_at,
        record.registration_closes_at,
        record.service_until,
    ):
        if value is not None and value.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
    if (
        record.registration_opens_at is not None
        and record.registration_closes_at is not None
        and record.registration_opens_at >= record.registration_closes_at
    ):
        raise ValueError("Открытие записи должно быть раньше закрытия")
    if (
        record.registration_closes_at is not None
        and record.service_until is not None
        and record.registration_closes_at > record.service_until
    ):
        raise ValueError("Обслуживание не может закончиться раньше записи")


class QueueService:
    def __init__(self, repository=None, *, opens_before_minutes: int = 15):
        if opens_before_minutes < 0:
            raise ValueError("Время открытия не может быть отрицательным")
        self.opens_before_minutes = opens_before_minutes
        self.repository = repository or InMemoryQueueRepository()
        self.schedules: dict[str, Schedule] = {}
        # Retain schedules of previous weeks and lessons removed from current YAML.
        self._schedules: dict[uuid.UUID, Schedule] = {}
        self._lessons: dict = {}
        self._archived_lesson_ids: set[uuid.UUID] = set()
        self._archive_week: date | None = None
        self._records: dict = {}
        self._lock = RLock()

    def _assert_mutable(self, lesson_id) -> None:
        lesson = self._lessons.get(lesson_id)
        if lesson is not None and lesson.queue._archiving:
            raise InvalidStateError("Очередь сохраняется в историю")

    def reconcile(self, *, now=None) -> None:
        """Apply clock-derived availability and persist expired entries."""
        now = now or _now()
        if now.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
        with self._lock:
            for lesson_id, record in self._records.items():
                lesson = self._lessons.get(lesson_id)
                if lesson is None or lesson.queue._archiving:
                    continue
                lesson.queue.closed = not record.is_open_at(
                    now, opens_before_minutes=self.opens_before_minutes,
                )
                if now < record.service_deadline():
                    continue
                ensure_entry_metadata(record)
                previous = deepcopy(record.entry_metadata)
                changed = False
                for metadata in record.entry_metadata.values():
                    if metadata.status in ACTIVE_STATUSES:
                        metadata.status = EntryStatus.EXPIRED
                        metadata.finished_at = record.service_deadline()
                        changed = True
                if changed:
                    try:
                        self.save_lesson(lesson_id)
                    except Exception:
                        record.entry_metadata = previous
                        raise

    def load_saved_week(self, courses, directory="schedule", *, week_start=None):
        # Serialize file reads and publication with parser callbacks and HTTP reloads.
        with self._lock:
            return self._load_saved_week(courses, directory, week_start=week_start)

    def _load_saved_week(self, courses, directory, *, week_start):
        documents = [
            yaml.safe_load(path.read_text(encoding="utf-8"))
            for path in sorted(Path(directory).glob("suai/*/*.yaml"))
        ]
        return self.generate_week(courses, documents, week_start=week_start)

    def generate_week(self, courses, documents, *, week_start=None):
        with self._lock:
            return self._generate_week(courses, documents, week_start=week_start)

    def _generate_week(self, courses, documents, *, week_start):
        if week_start is None:
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            week_start = today - timedelta(days=today.weekday())
        if type(week_start) is not date or week_start.weekday() != 0:
            raise ValueError("week_start должен быть датой понедельника")
        week_end = week_start + timedelta(days=7)
        schedules = {
            course_id: Schedule(
                id=uuid5(NAMESPACE_URL, repr((course_id, week_start.isoformat()))),
                course_id=course_id,
                course_name=course["name"],
                week_start=week_start,
            )
            for course_id, course in courses.items()
            if course.get("university", "").strip().casefold() in ("гуап", "suai")
            and course.get("_meta", {}).get("status", "active") == "active"
        }

        # Validate the entire input before changing existing schedules or queues.
        candidates = {course_id: {} for course_id in schedules}
        for document in documents:
            if not isinstance(document, dict):
                raise ValueError("Некорректный YAML расписания")
            if document.get("university") != "suai":
                continue
            zone = ZoneInfo(document.get("timezone", "Europe/Moscow"))
            for day, items in document.get("days", {}).items():
                lesson_date = date.fromisoformat(str(day))
                if not week_start <= lesson_date < week_end:
                    continue
                for item in items:
                    if not _is_queue_lesson(item.get("kind", "")):
                        continue
                    for course_id, schedule in schedules.items():
                        if item.get("subject") != schedule.course_name:
                            continue

                        # Entries without a time cannot become dated lessons.
                        if not item.get("starts-at") or not item.get("ends-at"):
                            raise ValueError("У занятия отсутствует время начала или окончания")

                        start = datetime.combine(lesson_date, time.fromisoformat(item["starts-at"]), zone)
                        end = datetime.combine(lesson_date, time.fromisoformat(item["ends-at"]), zone)
                        groups = tuple(sorted({str(group) for group in item.get("groups", [])}))
                        kind = item["kind"]

                        # The room/teacher can change without replacing the queue.
                        identity = (course_id, start.isoformat(), end.isoformat(), groups, kind)
                        lesson_id = uuid5(NAMESPACE_URL, repr(identity))
                        candidates[course_id][lesson_id] = Lesson(
                            id=lesson_id, starts_at=start, ends_at=end,
                            groups=groups, kind=kind, room=item.get("room"),
                        )
        with self._lock:
            # Clear only after the new week's input has been fully validated.
            if self._archive_week != week_start:
                self._archived_lesson_ids.clear()
                self._archive_week = week_start
            for course_id, lessons in candidates.items():
                schedule = schedules[course_id]
                for lesson_id, candidate in lessons.items():
                    if lesson_id in self._archived_lesson_ids:
                        continue
                    lesson = self._lessons.get(lesson_id)
                    if lesson is None:
                        lesson = candidate
                        self._lessons[lesson_id] = lesson
                    elif not lesson.queue._archiving:
                        lesson.room = candidate.room

                    record = (self._records.get(lesson_id) if lesson.queue._archiving
                              else self.repository.get(lesson_id))
                    if record is not None and record.archived:
                        self._lessons.pop(lesson_id, None)
                        continue
                    if record is None:
                        record = record_from_lesson(schedule, lesson)
                    elif not lesson.queue._archiving:
                        record = restore_record(record, schedule, lesson)
                    self._records[lesson_id] = record

                    if week_start <= lesson.starts_at.astimezone(ZoneInfo("Europe/Moscow")).date() < week_end:
                        schedule.lessons.append(lesson)

                # Manual and rescheduled lessons may have no source entry in
                # this week; restore them from the repository after a restart.
                for record in self.repository.list_by_course(course_id):
                    if record.archived:
                        continue
                    if not (record.manual or record.original_starts_at is not None) or record.id in lessons:
                        continue
                    if record.id in self._archived_lesson_ids:
                        continue
                    if not record.manual and not _is_queue_lesson(record.lesson_kind):
                        continue
                    if record.lesson_starts_at is None or record.lesson_ends_at is None:
                        continue
                    in_week = week_start <= record.lesson_starts_at.astimezone(ZoneInfo("Europe/Moscow")).date() < week_end
                    if not record.manual and not in_week:
                        continue
                    lesson = self._lessons.get(record.id)
                    if lesson is None:
                        lesson = Lesson(
                            id=record.id,
                            starts_at=record.lesson_starts_at,
                            ends_at=record.lesson_ends_at,
                            groups=tuple(group_ids(record)),
                            kind=record.lesson_kind if _is_queue_lesson(record.lesson_kind) else "Практическое занятие",
                            room=record.lesson_room,
                            queue=record.queue,
                            cancelled=record.cancelled,
                            original_starts_at=record.original_starts_at,
                        )
                        self._lessons[record.id] = lesson
                    elif lesson.queue._archiving:
                        record = self._records[record.id]
                    else:
                        lesson.queue = record.queue
                        lesson.starts_at = record.lesson_starts_at
                        lesson.ends_at = record.lesson_ends_at
                        lesson.cancelled = record.cancelled
                        lesson.original_starts_at = record.original_starts_at
                    self._records[record.id] = record
                    if record.manual:
                        lesson.kind = record.lesson_kind if _is_queue_lesson(record.lesson_kind) else "Практическое занятие"
                    if in_week:
                        schedule.lessons.append(lesson)
                    if record.manual:
                        # Keep arbitrary-date manual lessons manageable and
                        # available to the history service after a restart.
                        lesson_date = lesson.starts_at.astimezone(ZoneInfo("Europe/Moscow")).date()
                        monday = lesson_date - timedelta(days=lesson_date.weekday())
                        context_id = uuid5(NAMESPACE_URL, repr((course_id, monday.isoformat())))
                        context = self._schedules.setdefault(context_id, Schedule(
                            id=context_id, course_id=course_id,
                            course_name=schedule.course_name, week_start=monday,
                        ))
                        if all(item.id != lesson.id for item in context.lessons):
                            context.lessons.append(lesson)

                schedule.lessons.sort(key=lambda lesson: (
                    lesson.starts_at, lesson.groups, lesson.kind,
                ))

            # Only publish the current week. Historical and vanished lessons stay
            # in _lessons with their original queues until archiving is implemented.
            for schedule in schedules.values():
                retained = self._schedules.get(schedule.id)
                if retained is None:
                    retained = Schedule(
                        id=schedule.id, course_id=schedule.course_id,
                        course_name=schedule.course_name, week_start=week_start,
                    )
                    self._schedules[schedule.id] = retained
                retained.course_name = schedule.course_name
                known = {lesson.id for lesson in retained.lessons}
                retained.lessons.extend(lesson for lesson in schedule.lessons if lesson.id not in known)
            self.schedules = schedules
            return dict(schedules)

    def begin_archive(self, schedule_id: uuid.UUID, lesson_id: uuid.UUID):
        """Freeze a queue and return its course context and detached snapshot."""
        with self._lock:
            self.reconcile()
            schedule = self._schedules.get(schedule_id)
            if schedule is None:
                raise LookupError("Расписание не найдено")
            lesson = self._lessons.get(lesson_id)
            if lesson is None:
                return None
            if not any(item.id == lesson_id for item in schedule.lessons):
                raise ValueError("Занятие не принадлежит указанному расписанию")
            if lesson.queue._archiving:
                raise RuntimeError("Очередь уже сохраняется")
            snapshot = deepcopy(lesson)
            lesson.queue._archiving = True
            return schedule.course_id, schedule.course_name, snapshot

    def cancel_archive(self, lesson_id: uuid.UUID) -> None:
        with self._lock:
            lesson = self._lessons.get(lesson_id)
            if lesson is not None:
                lesson.queue._archiving = False

    def complete_archive(self, lesson_id: uuid.UUID) -> None:
        """Remove an archived queue only after successful persistence."""
        with self._lock:
            lesson = self._lessons.get(lesson_id)
            if lesson is None:
                return
            if not lesson.queue._archiving:
                raise RuntimeError("Архивирование занятия не начато")
            record = self._records.get(lesson_id)
            if record is not None:
                candidate = deepcopy(record)
                candidate.archived = True
                candidate.queue.closed = True
                candidate.queue._archiving = False
                try:
                    self.repository.save(candidate, expected_version=record.version)
                except QueueNotFoundError:
                    self.repository.create(candidate)
                self._records.pop(lesson_id, None)
            lesson.queue._archiving = False
            if self._archive_week is not None:
                lesson_date = lesson.starts_at.astimezone(ZoneInfo("Europe/Moscow")).date()
                if self._archive_week <= lesson_date < self._archive_week + timedelta(days=7):
                    self._archived_lesson_ids.add(lesson_id)
            self._lessons.pop(lesson_id, None)
            for schedule in (*self.schedules.values(), *self._schedules.values()):
                schedule.lessons[:] = [
                    lesson for lesson in schedule.lessons if lesson.id != lesson_id
                ]


    def finished_lessons(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        now = _now()
        with self._lock:
            return [
                (schedule.id, lesson.id)
                for schedule in self._schedules.values() for lesson in schedule.lessons
                if lesson.id in self._lessons
                and (self._records[lesson.id].service_deadline() <= now
                     if lesson.id in self._records else lesson.ends_at <= now)
                and not lesson.queue._archiving
            ]
    def set_registration(self, lesson_id, *, closed: bool) -> QueueRecord:
        """Открыть или закрыть запись в очередь (write-through)."""
        with self._lock:
            lesson = self._lessons.get(lesson_id)
            record = self._records.get(lesson_id)
            if lesson is None or record is None:
                raise KeyError(f"Занятие {lesson_id} не найдено")
            self._assert_mutable(lesson_id)
            closed = bool(closed)
            if lesson.cancelled and not closed:
                raise InvalidStateError("Отменённое занятие нельзя открыть для записи")
            mode = RegistrationMode.MANUALLY_CLOSED if closed else RegistrationMode.MANUALLY_OPENED
            if lesson.queue.closed == closed and record.registration_mode == mode:
                return record
            record.registration_mode = mode
            lesson.queue.closed = closed
            return self.save_lesson(lesson_id)

    def get_record(self, lesson_id) -> QueueRecord | None:
        """Сохранённое состояние очереди занятия (только чтение)."""
        with self._lock:
            return self._records.get(lesson_id)

    def get_lesson(self, course_id, lesson_id) -> Lesson | None:
        with self._lock:
            record = self._records.get(lesson_id)
            lesson = self._lessons.get(lesson_id)
            return lesson if record is not None and record.course_id == course_id and lesson is not None and _is_queue_lesson(lesson.kind) else None

    def admin_lessons(self, course_id, week_start) -> list[Lesson]:
        """Include manual lessons on any date and lessons moved out of this week."""
        with self._lock:
            schedule = self.schedules.get(course_id)
            result = {lesson.id: lesson for lesson in schedule.lessons} if schedule else {}
            for lesson_id, record in self._records.items():
                original = record.original_starts_at
                if record.course_id == course_id and (record.manual or original is not None and week_start <= original.date() < week_start + timedelta(days=7)):
                    lesson = self._lessons.get(lesson_id)
                    if lesson is not None and _is_queue_lesson(lesson.kind):
                        result[lesson_id] = lesson
            return sorted(result.values(), key=lambda item: (item.starts_at, item.groups, item.kind))

    def save_lesson(self, lesson_id) -> QueueRecord:
        """Сохранить состояние очереди занятия (write-through после мутации)."""
        with self._lock:
            self._assert_mutable(lesson_id)
            record = self._records.get(lesson_id)
            lesson = self._lessons.get(lesson_id)
            if record is None or lesson is None:
                raise KeyError(f"Занятие {lesson_id} не найдено")
            schedule = self.schedules.get(record.course_id)
            if schedule is None:
                raise KeyError(f"Расписание курса {record.course_id} не найдено")

            sync_record_with_lesson(record, schedule, lesson)
            ensure_entry_metadata(record)
            try:
                saved = self.repository.save(record, expected_version=record.version)
            except QueueNotFoundError:
                saved = self.repository.create(record)
            record.version = saved.version
            self._records[lesson_id] = record
            return record

    def create_manual(
        self,
        course_id: str,
        course_name: str,
        *,
        week_start: date,
        group_ids: list[str],
        starts_at: datetime,
        ends_at: datetime,
        registration_opens_at: datetime | None = None,
        registration_closes_at: datetime | None = None,
        service_until: datetime | None = None,
        closed: bool = False,
    ) -> QueueRecord:
        """Создать внештатную очередь, которой нет в YAML расписания."""
        groups = tuple(
            sorted({str(group).strip() for group in group_ids if str(group).strip()})
        )
        if not groups:
            raise ValueError("Укажите хотя бы одну группу")
        _validate_lesson_times(starts_at, ends_at)

        with self._lock:
            lesson = Lesson(
                id=uuid.uuid4(),
                starts_at=starts_at,
                ends_at=ends_at,
                groups=groups,
                kind="Практическое занятие",
                queue=Queue(closed=closed),
            )
            record = QueueRecord(
                id=lesson.id,
                course_id=course_id,
                group_id=group_key(groups),
                registration_opens_at=registration_opens_at,
                registration_closes_at=registration_closes_at,
                service_until=service_until,
                lesson_starts_at=starts_at,
                lesson_ends_at=ends_at,
                queue=lesson.queue,
                manual=True,
                registration_mode=RegistrationMode.MANUALLY_CLOSED if closed else RegistrationMode.MANUALLY_OPENED,
                lesson_kind=lesson.kind,
            )
            _validate_registration(record)

            schedule = self.schedules.get(course_id)
            if schedule is None:
                schedule = Schedule(
                    id=uuid5(
                        NAMESPACE_URL,
                        repr((course_id, week_start.isoformat())),
                    ),
                    course_id=course_id,
                    course_name=course_name,
                    week_start=week_start,
                )
                self.schedules[course_id] = schedule
            lesson_date = starts_at.astimezone(ZoneInfo("Europe/Moscow")).date()
            monday = lesson_date - timedelta(days=lesson_date.weekday())
            context_id = uuid5(NAMESPACE_URL, repr((course_id, monday.isoformat())))
            context = self._schedules.setdefault(context_id, Schedule(
                id=context_id, course_id=course_id,
                course_name=course_name, week_start=monday,
            ))

            # Persist before publishing the lesson in either list.
            saved = self.repository.create(record)
            saved.queue = lesson.queue
            context.lessons.append(lesson)
            if schedule.week_start == monday and context is not schedule:
                schedule.lessons.append(lesson)
            for item in (context, schedule):
                item.lessons.sort(key=lambda lesson: (lesson.starts_at, lesson.groups, lesson.kind))

            self._lessons[lesson.id] = lesson
            self._records[lesson.id] = saved
            return saved

    def update_queue(
        self,
        lesson_id,
        *,
        cancelled: bool | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        registration_opens_at: datetime | None = None,
        registration_closes_at: datetime | None = None,
        service_until: datetime | None = None,
    ) -> QueueRecord:
        """Обновить время занятия и окно записи."""
        with self._lock:
            self._assert_mutable(lesson_id)
            lesson = self._lessons.get(lesson_id)
            record = self._records.get(lesson_id)
            if lesson is None or record is None:
                raise EntryNotFoundError(lesson_id)

            candidate = deepcopy(record)
            candidate.lesson_starts_at = starts_at or lesson.starts_at
            candidate.lesson_ends_at = ends_at or lesson.ends_at
            _validate_lesson_times(candidate.lesson_starts_at, candidate.lesson_ends_at)
            moved = (candidate.lesson_starts_at, candidate.lesson_ends_at) != (lesson.starts_at, lesson.ends_at)
            if moved and not record.manual and candidate.original_starts_at is None:
                candidate.original_starts_at = lesson.starts_at
                candidate.original_ends_at = lesson.ends_at
            if (candidate.lesson_starts_at, candidate.lesson_ends_at) == (candidate.original_starts_at, candidate.original_ends_at):
                candidate.original_starts_at = None
                candidate.original_ends_at = None
            delta = candidate.lesson_starts_at - lesson.starts_at
            for field, value in (
                ("registration_opens_at", registration_opens_at),
                ("registration_closes_at", registration_closes_at),
                ("service_until", service_until),
            ):
                old = getattr(candidate, field)
                setattr(candidate, field, value if value is not None else old + delta if old is not None else None)
            if cancelled is not None:
                candidate.cancelled = cancelled
            if candidate.cancelled:
                candidate.queue.closed = True
            _validate_registration(candidate)
            # Validate and persist before publishing, so failed updates leave
            # both lesson times and the existing queue untouched.
            try:
                saved = self.repository.save(candidate, expected_version=record.version)
            except QueueNotFoundError:
                saved = self.repository.create(candidate)
            lesson.starts_at = saved.lesson_starts_at
            lesson.ends_at = saved.lesson_ends_at
            lesson.original_starts_at = saved.original_starts_at
            lesson.cancelled = saved.cancelled
            lesson.queue.closed = saved.queue.closed
            saved.queue = lesson.queue
            self._records[lesson_id] = saved
            for schedule in self.schedules.values():
                if schedule.course_id != record.course_id:
                    continue
                if not schedule.week_start <= lesson.starts_at.astimezone(ZoneInfo("Europe/Moscow")).date() < schedule.week_start + timedelta(days=7):
                    schedule.lessons[:] = [item for item in schedule.lessons if item.id != lesson_id]
                elif all(item.id != lesson_id for item in schedule.lessons):
                    schedule.lessons.append(lesson)
            for schedule in (*self.schedules.values(), *self._schedules.values()):
                if any(item.id == lesson_id for item in schedule.lessons):
                    schedule.lessons.sort(
                        key=lambda item: (item.starts_at, item.groups, item.kind)
                    )
            return saved

    def delete_queue(self, lesson_id) -> None:
        """Удалить пустую внештатную очередь."""
        with self._lock:
            self._assert_mutable(lesson_id)
            record = self._records.get(lesson_id)
            lesson = self._lessons.get(lesson_id)
            if record is None or lesson is None:
                raise EntryNotFoundError(lesson_id)
            if not record.manual:
                raise QueueNotDeletableError(
                    "Удалять можно только внештатную очередь"
                )
            if lesson.queue.entries:
                raise QueueNotEmptyError()

            try:
                self.repository.delete(lesson_id)
            except QueueNotFoundError:
                pass
            self._records.pop(lesson_id, None)
            self._lessons.pop(lesson_id, None)
            self._archived_lesson_ids.discard(lesson_id)
            for schedule in (*self.schedules.values(), *self._schedules.values()):
                schedule.lessons[:] = [
                    item for item in schedule.lessons if item.id != lesson_id
                ]


    def remove_entry(self, lesson_id, entry_id, *, now=None) -> QueueEntry:
        """Убрать активную запись преподавателем; она остаётся в истории."""
        with self._lock:
            self._assert_mutable(lesson_id)
            lesson = self._lessons.get(lesson_id)
            record = self._records.get(lesson_id)
            if lesson is None or record is None:
                raise EntryNotFoundError(entry_id)
            ensure_entry_metadata(record)
            entry = next(
                (item for item in lesson.queue.entries if item.id == entry_id), None
            )
            if entry is None:
                raise EntryNotFoundError(entry_id)
            metadata = record.entry_metadata[entry.id]
            if metadata.status not in ACTIVE_STATUSES:
                raise InvalidStateError("Удалить можно только активную запись")
            metadata.status = EntryStatus.WITHDRAWN
            metadata.finished_at = now or _now()
            self.save_lesson(lesson_id)
            return entry

    def update_result(
        self, lesson_id, entry_id, outcome: DefenseResult
    ) -> QueueEntry:
        """Исправить результат уже завершённой защиты."""
        with self._lock:
            self._assert_mutable(lesson_id)
            lesson = self._lessons.get(lesson_id)
            record = self._records.get(lesson_id)
            if lesson is None or record is None:
                raise EntryNotFoundError(entry_id)
            ensure_entry_metadata(record)
            entry = next(
                (item for item in lesson.queue.entries if item.id == entry_id), None
            )
            if entry is None:
                raise EntryNotFoundError(entry_id)
            metadata = record.entry_metadata[entry.id]
            if metadata.status != EntryStatus.FINISHED:
                raise InvalidStateError("Исправить можно только завершённую запись")
            metadata.result = outcome
            self.save_lesson(lesson_id)
            return entry

    def active_entry(self, course_id, github) -> tuple[QueueRecord, QueueEntry] | None:
        """Активная запись студента (waiting/called) в пределах курса."""
        query = github.strip().casefold()
        with self._lock:
            for record in self._records.values():
                if record.course_id != course_id:
                    continue
                ensure_entry_metadata(record)
                for entry in record.queue.entries:
                    if (
                        record.entry_metadata[entry.id].status in ACTIVE_STATUSES
                        and entry.student.github.strip().casefold() == query
                    ):
                        return record, entry
        return None

    def join(self, lesson_id, eligible, *, now=None) -> QueueEntry:
        """Записать студента в очередь занятия."""
        with self._lock:
            self._assert_mutable(lesson_id)
            self.reconcile(now=now)
            lesson = self._lessons.get(lesson_id)
            record = self._records.get(lesson_id)
            if lesson is None or record is None:
                raise EntryNotFoundError(lesson_id)
            if lesson.queue.closed:
                raise QueueClosedError()
            if eligible.group_id not in lesson.groups:
                raise GroupNotAllowedError()

            existing = self.active_entry(record.course_id, eligible.student.github)
            if existing is not None:
                raise AlreadyQueuedError(existing[1].id)

            ensure_entry_metadata(record)
            active_ids = {
                entry.id
                for entry in lesson.queue.entries
                if record.entry_metadata[entry.id].status in ACTIVE_STATUSES
            }
            entry = lesson.queue.add_entry(
                eligible.student,
                eligible.lab_id,
                eligible.lab_name,
                active_entry_ids=active_ids,
            )
            record.entry_metadata[entry.id] = QueueEntryMetadata(
                queue_id=record.id,
                sequence=len(lesson.queue.entries),
                status=EntryStatus.WAITING,
            )
            self.save_lesson(lesson_id)
            return entry

    def leave(self, course_id, entry_id, github, *, now=None) -> QueueEntry:
        """Выйти из ожидающей записи; запись остаётся в истории."""
        query = github.strip().casefold()
        with self._lock:
            for record in list(self._records.values()):
                if record.course_id != course_id:
                    continue
                entry = next(
                    (item for item in record.queue.entries if item.id == entry_id),
                    None,
                )
                if entry is None:
                    continue
                if entry.student.github.strip().casefold() != query:
                    raise EntryNotOwnedError()
                self._assert_mutable(record.id)
                ensure_entry_metadata(record)
                metadata = record.entry_metadata[entry.id]
                if metadata.status != EntryStatus.WAITING:
                    raise InvalidStateError("Выйти можно только из ожидающей записи")
                metadata.status = EntryStatus.WITHDRAWN
                metadata.finished_at = now or _now()
                self.save_lesson(record.id)
                return entry
        raise EntryNotFoundError(entry_id)

    def call_next(self, lesson_id, *, now=None) -> QueueEntry:
        """Вызвать первого ожидающего."""
        with self._lock:
            self._assert_mutable(lesson_id)
            self.reconcile(now=now)
            lesson = self._lessons.get(lesson_id)
            record = self._records.get(lesson_id)
            if lesson is None or record is None:
                raise EntryNotFoundError(lesson_id)
            if lesson.cancelled:
                raise InvalidStateError("Занятие отменено")
            ensure_entry_metadata(record)
            waiting = [
                entry
                for entry in lesson.queue.entries
                if record.entry_metadata[entry.id].status == EntryStatus.WAITING
            ]
            if not waiting:
                raise QueueEmptyError()
            entry = waiting[0]
            metadata = record.entry_metadata[entry.id]
            metadata.status = EntryStatus.CALLED
            metadata.called_at = now or _now()
            self.save_lesson(lesson_id)
            return entry

    def finish(
        self, lesson_id, entry_id, outcome: DefenseResult | None = None, *, now=None
    ) -> QueueEntry:
        """Завершить вызванную запись с результатом защиты."""
        with self._lock:
            self._assert_mutable(lesson_id)
            self.reconcile(now=now)
            lesson = self._lessons.get(lesson_id)
            record = self._records.get(lesson_id)
            if lesson is None or record is None:
                raise EntryNotFoundError(entry_id)
            ensure_entry_metadata(record)
            entry = next(
                (item for item in lesson.queue.entries if item.id == entry_id), None
            )
            if entry is None:
                raise EntryNotFoundError(entry_id)
            metadata = record.entry_metadata[entry.id]
            first_waiting = next((item for item in lesson.queue.entries
                                  if record.entry_metadata[item.id].status == EntryStatus.WAITING), None)
            waiting_absence = (
                outcome == DefenseResult.ABSENT
                and metadata.status == EntryStatus.WAITING
                and first_waiting is not None and first_waiting.id == entry.id
            )
            if metadata.status != EntryStatus.CALLED and not waiting_absence:
                raise InvalidStateError("Неявку можно отметить у первого ожидающего; завершить защиту — у вызванного")
            metadata.status = EntryStatus.FINISHED
            metadata.result = outcome
            metadata.finished_at = now or _now()
            self.save_lesson(lesson_id)
            return entry

    def history(
        self,
        course_id,
        *,
        group_id=None,
        date_from=None,
        date_to=None,
        offset=0,
        limit=50,
    ) -> tuple[list[dict], int | None]:
        """История терминальных записей курса, новые сверху."""
        with self._lock:
            records = {
                record.id: record
                for record in self.repository.list_by_course(course_id)
            }
            records.update(self._records)
            items: list[dict] = []
            for record in records.values():
                if record.course_id != course_id:
                    continue
                if group_id is not None and group_id not in group_ids(record):
                    continue
                ensure_entry_metadata(record)
                for entry in record.queue.entries:
                    metadata = record.entry_metadata.get(entry.id)
                    if metadata is None or metadata.status not in TERMINAL_STATUSES:
                        continue
                    finished_at = metadata.finished_at
                    if finished_at is None:
                        continue
                    if date_from is not None and finished_at.date() < date_from:
                        continue
                    if date_to is not None and finished_at.date() > date_to:
                        continue
                    items.append({
                        "queue_id": record.id,
                        "lesson_starts_at": record.lesson_starts_at,
                        "group_id": record.group_id,
                        "github": entry.student.github,
                        "full_name": _full_name(entry.student),
                        "lab_id": entry.lab_id,
                        "lab_name": entry.lab_name,
                        "status": metadata.status,
                        "result": metadata.result,
                        "finished_at": finished_at,
                    })
            items.sort(key=lambda item: item["finished_at"], reverse=True)
            page = items[offset:offset + limit]
            next_offset = offset + limit if offset + limit < len(items) else None
            return page, next_offset
