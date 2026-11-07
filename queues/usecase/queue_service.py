from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import RLock
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import yaml

from ..domain.lesson import Lesson
from ..domain.schedule import Schedule


class QueueService:
    def __init__(self):
        self.schedules: dict[str, Schedule] = {}
        self._lessons: dict = {}
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
            for course_id, lessons in candidates.items():
                for lesson_id, candidate in lessons.items():
                    lesson = self._lessons.get(lesson_id)
                    if lesson is None:
                        lesson = candidate
                        self._lessons[lesson_id] = lesson
                    else:
                        lesson.room = candidate.room
                    schedules[course_id].lessons.append(lesson)
                schedules[course_id].lessons.sort(key=lambda lesson: (
                    lesson.starts_at, lesson.groups, lesson.kind,
                ))
                
            # Only publish the current week. Historical and vanished lessons stay
            # in _lessons with their original queues until archiving is implemented.
            self.schedules = schedules
            return dict(schedules)
