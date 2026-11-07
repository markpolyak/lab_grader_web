from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import RLock
from uuid import NAMESPACE_URL, uuid5
import uuid
from copy import deepcopy
from zoneinfo import ZoneInfo

import yaml

from ..domain.lesson import Lesson
from ..domain.schedule import Schedule


class QueueService:
    def __init__(self):
        self.schedules: dict[str, Schedule] = {}
        # Retain schedules of previous weeks and lessons removed from current YAML.
        self._schedules: dict[uuid.UUID, Schedule] = {}
        self._lessons: dict = {}
        self._archived_lesson_ids: set[uuid.UUID] = set()
        self._archive_week: date | None = None
        self._lock = RLock()

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
                for lesson_id, candidate in lessons.items():
                    if lesson_id in self._archived_lesson_ids:
                        continue
                    lesson = self._lessons.get(lesson_id)
                    if lesson is None:
                        lesson = candidate
                        self._lessons[lesson_id] = lesson
                    elif not lesson.queue._archiving:
                        lesson.room = candidate.room
                    schedules[course_id].lessons.append(lesson)
                schedules[course_id].lessons.sort(key=lambda lesson: (
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
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        with self._lock:
            return [
                (schedule.id, lesson.id)
                for schedule in self._schedules.values() for lesson in schedule.lessons
                if lesson.id in self._lessons and lesson.ends_at <= now
                and not lesson.queue._archiving
            ]
