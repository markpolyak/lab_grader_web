from .queue_entry import QueueEntry


class Queue:
    def __init__(self) -> None:
        self.entries: list[QueueEntry] = []

    def add_entry(self, entry: QueueEntry) -> None:
        self.entries.append(entry)
