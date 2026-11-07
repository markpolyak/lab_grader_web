from dataclasses import dataclass


@dataclass
class Student:
    name: str
    surname: str
    patronymic: str
    github: str
    group_id: str | None = None

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.surname, self.name, self.patronymic) if part)
