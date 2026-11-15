#!/usr/bin/env python3
"""Добавить вымышленных студентов в локальное файловое хранилище очередей.

Перед запуском остановите backend, затем запустите его снова. Скрипт не
обращается к Google Sheets и не меняет оценки; предназначен для локальной проверки.
"""
import argparse
from datetime import date
import os
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from grading.course_index import iter_course_configs  # noqa: E402
from grading.queue_eligibility import EligibleStudentLab  # noqa: E402
from queues.domain.student import Student  # noqa: E402
from queues.repositories.storage.file import FileQueueRepository  # noqa: E402
from queues.usecase.queue_service import QueueService  # noqa: E402


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", default="os-2026")
    parser.add_argument("--lesson-id", type=UUID)
    parser.add_argument("--week-start", type=date.fromisoformat)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.count <= 20:
        parser.error("--count должен быть от 1 до 20")
    week = args.week_start
    if week is None and os.getenv("QUEUE_WEEK_START"):
        week = date.fromisoformat(os.environ["QUEUE_WEEK_START"])
    courses = dict(iter_course_configs(str(ROOT / "courses")))
    repository = FileQueueRepository(os.getenv("QUEUE_STATE_DIR", str(ROOT / "queue_state")))
    service = QueueService(repository)
    service.load_saved_week(courses, os.getenv("SCHEDULE_DIR", str(ROOT / "schedule")), week_start=week)
    schedule = service.schedules.get(args.course)
    if schedule is None:
        parser.error("Нет активного расписания ГУАП для указанного курса")
    lessons = service.admin_lessons(args.course, schedule.week_start)
    lesson = next((item for item in lessons if item.id == args.lesson_id), None) if args.lesson_id else next((item for item in lessons if not item.cancelled), None)
    if lesson is None or not lesson.groups or lesson.cancelled:
        parser.error("Выберите существующее неотменённое занятие с группой")
    labs = courses[args.course].get("labs", {})
    if not labs:
        parser.error("В курсе нет лабораторных работ")
    lab_id = next((str(key) for key in labs if str(key) == "1"), str(next(iter(labs))))
    lab = next(value for key, value in labs.items() if str(key) == lab_id)
    service.set_registration(lesson.id, closed=False)
    print(f"Курс: {schedule.course_name}; неделя: {schedule.week_start}")
    print(f"Занятие: {lesson.id}; начало: {lesson.starts_at.isoformat()}")
    for number in range(1, args.count + 1):
        github = f"queue-demo-{number}"
        if service.active_entry(args.course, github) is not None:
            print(f"{github}: активная запись уже есть, пропускаю")
            continue
        student = Student(f"Студент{number}", "Тестовый", "", github)
        service.join(lesson.id, EligibleStudentLab(
            student=student, full_name=f"Тестовый Студент{number}",
            group_id=lesson.groups[0], lab_id=lab_id, lab_name=lab.get("short-name", lab_id),
        ))
        print(f"Тестовый Студент{number} | {github} | {lab.get('short-name', lab_id)}")
    print("Запустите backend и откройте занятие в админке. Повторный запуск не дублирует активные тестовые записи.")


if __name__ == "__main__":
    main()
