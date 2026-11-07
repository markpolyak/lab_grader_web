from uuid import UUID

from .queue_entry import QueueEntry
from .student import Student


class Queue:
    def __init__(self, *, closed: bool = True) -> None:
        self.closed = closed
        self.entries: list[QueueEntry] = []
        self._archiving = False

    def add_entry(
        self,
        student: Student,
        lab_id: str,
        lab_name: str | None = None,
        *,
        active_entry_ids: set[UUID] | None = None,
    ) -> QueueEntry:
        if self._archiving:
            raise ValueError("Очередь сохраняется в историю")
        if self.closed:
            raise ValueError("Очередь закрыта для записи")
        if not lab_id.strip():
            raise ValueError("Укажите лабораторную работу")
        github = student.github.strip().casefold()
        if not github:
            raise ValueError("Укажите GitHub-логин студента")
        checked = (
            self.entries
            if active_entry_ids is None
            else [entry for entry in self.entries if entry.id in active_entry_ids]
        )
        if any(entry.student.github.strip().casefold() == github for entry in checked):
            raise ValueError("Студент уже стоит в очереди")

        entry = QueueEntry(student=student, lab_id=lab_id.strip(), lab_name=lab_name)
        self.entries.append(entry)
        return entry

    def get_position(
        self, *, full_name: str | None = None, github: str | None = None
    ) -> int:
        """Найти место студента по ФИО либо GitHub-логину."""
        return self._find_index(full_name=full_name, github=github) + 1

    def move_down(
        self, *, full_name: str | None = None, github: str | None = None
    ) -> bool:
        """Уступить место студенту ниже."""
        if self._archiving:
            raise ValueError("Очередь сохраняется в историю")
        index = self._find_index(full_name=full_name, github=github)
        if index == len(self.entries) - 1:
            return False
        self.entries[index], self.entries[index + 1] = (
            self.entries[index + 1],
            self.entries[index],
        )
        return True

    def _find_index(self, *, full_name: str | None, github: str | None) -> int:
        if (full_name is None) == (github is None):
            raise ValueError("Укажите либо ФИО, либо GitHub-логин студента")

        if github is not None:
            query = github.strip().casefold()
            values = [entry.student.github.strip().casefold() for entry in self.entries]
        else:
            assert full_name is not None
            query = self._normalize_full_name(full_name)
            values = [
                self._normalize_full_name(
                    f"{entry.student.surname} {entry.student.name} {entry.student.patronymic}"
                )
                for entry in self.entries
            ]

        if not query:
            raise ValueError("Поисковый запрос не должен быть пустым")
        matches = [index for index, value in enumerate(values) if value == query]
        if not matches:
            raise LookupError("Студент не найден в очереди")
        if len(matches) > 1:
            raise ValueError("Найдено несколько записей; уточните студента по GitHub-логину")
        return matches[0]

    @staticmethod
    def _normalize_full_name(value: str) -> str:
        return " ".join(value.split()).casefold().replace("ё", "е")
