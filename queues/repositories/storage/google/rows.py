"""Pure conversion of domain objects into the queue history sheet schema."""
from zoneinfo import ZoneInfo

from ....domain.lesson import Lesson

HEADERS = ["№", "ФИО", "github", "Лабораторная работа", "Время записи"]


def snapshot_rows(lesson: Lesson, *, course_name: str) -> list[list]:
    if not course_name.strip():
        raise ValueError("Укажите название курса")
    zone = ZoneInfo("Europe/Moscow")
    start, end = lesson.starts_at.astimezone(zone), lesson.ends_at.astimezone(zone)
    end_label = end.strftime("%H:%M") if start.date() == end.date() else end.strftime("%d.%m.%Y %H:%M")
    rows = [
        [f"Занятие: {start:%d.%m.%Y} · {start:%H:%M}–{end_label}"],
        [f"{lesson.kind} · Группы: {', '.join(sorted(lesson.groups)) or '—'}"],
        [f"Курс: {course_name} · Аудитория: {lesson.room or '—'} · Студентов: {len(lesson.queue.entries)}"],
        HEADERS.copy(),
    ]
    if not lesson.queue.entries:
        return rows + [["", "Очередь пустая", "", "", ""]]
    return rows + [[
        position,
        " ".join(filter(None, (entry.student.surname, entry.student.name, entry.student.patronymic))),
        entry.student.github, entry.lab_name or f"ЛР {entry.lab_id}",
        entry.enqueued_time.astimezone(zone).strftime("%d.%m.%Y %H:%M"),
    ] for position, entry in enumerate(lesson.queue.entries, start=1)]
