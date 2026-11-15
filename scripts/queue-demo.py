#!/usr/bin/env python3
"""Загрузить тестовую неделю из YAML без сети и вывести Schedule → Lesson → Queue.

python -i scripts/queue-demo.py --week-start 2026-11-09 оставляет переменную
service в интерактивном сеансе для работы с настоящими объектами очередей.
"""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from grading.course_index import iter_course_configs  # noqa: E402
from queues.usecase.queue_service import QueueService  # noqa: E402


def load_demo():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week-start", type=date.fromisoformat, help="Понедельник в формате YYYY-MM-DD")
    parser.add_argument("--schedule-dir", type=Path, default=PROJECT_ROOT / "schedule")
    parser.add_argument("--courses-dir", type=Path, default=PROJECT_ROOT / "courses")
    args = parser.parse_args()
    service = QueueService()
    service.load_saved_week(
        dict(iter_course_configs(str(args.courses_dir))),
        args.schedule_dir, week_start=args.week_start,
    )
    print(json.dumps([
        {
            "course_id": schedule.course_id,
            "course_name": schedule.course_name,
            "week_start": schedule.week_start.isoformat(),
            "lessons": [
                {
                    "id": str(lesson.id),
                    "starts_at": lesson.starts_at.isoformat(),
                    "ends_at": lesson.ends_at.isoformat(),
                    "groups": lesson.groups,
                    "kind": lesson.kind,
                    "room": lesson.room,
                    "queue": {"closed": lesson.queue.closed, "entries": len(lesson.queue.entries)},
                }
                for lesson in schedule.lessons
            ],
        }
        for schedule in service.schedules.values()
    ], ensure_ascii=False, indent=2))
    return service


if __name__ == "__main__":
    service = load_demo()
