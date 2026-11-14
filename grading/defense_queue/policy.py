"""Чистые правила порядка; без доступа к Sheets, SQLite и HTTP."""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class QueueMode:
    name: Literal["random", "priority", "fifo"]
    locked: bool


def resolve_mode(*, deadlines_available: bool, random_override: bool = False, fifo: bool = False) -> QueueMode:
    if fifo:
        return QueueMode("fifo", locked=False)
    if not deadlines_available:
        return QueueMode("random", locked=True)
    return QueueMode("random" if random_override else "priority", locked=False)


def priority_for(deadline: datetime, session_start: datetime, *,
                 week_anchor: date, timezone: str) -> int:
    if deadline.utcoffset() is None or session_start.utcoffset() is None:
        raise ValueError("Дедлайн и начало занятия должны содержать часовой пояс")
    zone = ZoneInfo(timezone)
    deadline_week = (deadline.astimezone(zone).date() - week_anchor).days // 7
    session_week = (session_start.astimezone(zone).date() - week_anchor).days // 7
    return max(-1, deadline_week - session_week)


@dataclass(frozen=True)
class CandidateOrder:
    candidate_id: int
    qualified_at: int
    deadline: datetime | None
    random_key: float
    repeat_order: int | None = None
    deadline_available: bool = True


def order_candidates(candidates: list[CandidateOrder], *, mode: QueueMode,
                     session_start: datetime, week_anchor: date,
                     timezone: str) -> list[CandidateOrder]:
    """random_key создаётся один раз при включении в состав и хранится в БД."""
    if mode.name not in ("random", "priority", "fifo"):
        raise ValueError("Неизвестный режим очереди")
    if len({c.candidate_id for c in candidates}) != len(candidates):
        raise ValueError("Участник повторяется в составе")

    def key(candidate: CandidateOrder):
        if candidate.repeat_order is not None:
            return (1, candidate.repeat_order, candidate.qualified_at, candidate.candidate_id)
        if mode.name == "fifo":
            rank = candidate.qualified_at
        elif mode.name == "random":
            if not 0 <= candidate.random_key < 1:
                raise ValueError("Случайный ключ должен находиться в диапазоне [0, 1)")
            rank = candidate.random_key
        else:
            if not candidate.deadline_available:
                raise ValueError("Срок не удалось получить: требуется случайный режим")
            rank = (float("inf") if candidate.deadline is None else
                    priority_for(candidate.deadline, session_start,
                                 week_anchor=week_anchor, timezone=timezone))
        return (0, rank, candidate.qualified_at, candidate.candidate_id)

    return sorted(candidates, key=key)
