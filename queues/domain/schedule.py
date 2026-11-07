from .lesson import Lesson


class Schedule:
    def __init__(self) -> None:
        self.lessons: list[Lesson] = []
