"""Разбор состояния работ без сетевых вызовов и записи оценок."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
from typing import Callable, Mapping


CELL = re.compile(r'(?P<checks>v{1,2}|✓{1,2}|✔{1,2}|✅{1,2})(?:@(?P<score>\d+(?:[.,]\d+)?))?(?:-(?P<penalty>\d+))?')


@dataclass(frozen=True)
class LabState:
    admitted: bool = False
    defended: bool = False
    recognized: bool = True
    score: Decimal | None = None
    penalty: int = 0


def parse_lab_cell(raw: str | None) -> LabState:
    value = (raw or '').strip().lower()
    if not value or value == 'x' or value.startswith('?'):
        return LabState()
    match = CELL.fullmatch(value)
    if match is None:
        return LabState(recognized=False)
    defended = len(match['checks']) == 2
    return LabState(admitted=not defended, defended=defended,
                    score=Decimal(match['score'].replace(',', '.')) if match['score'] else None,
                    penalty=int(match['penalty'] or 0))


def first_admitted_lab(required_order: list[str], cells: Mapping[str, str | None], *,
                       defended_in_history: frozenset[str] = frozenset()) -> str | None:
    return select_queue_lab(required_order,cells,defended_in_history=defended_in_history)[0]


def select_queue_lab(required_order, cells, *, defended_in_history=frozenset()):
    """Only work after the last defended lab can determine the student's priority."""
    states = {key: parse_lab_cell(cells.get(key)) for key in required_order}
    frontier = max((index for index, key in enumerate(required_order)
                    if states[key].defended or key in defended_in_history), default=-1)
    following = required_order[frontier + 1:]
    admitted = next((key for key in following if states[key].admitted), None)
    target = admitted or (following[0] if following else None)
    return admitted, target, not following


@dataclass(frozen=True)
class DeadlineState:
    value: datetime | None
    available: bool
    source: str


def read_deadline(raw: str | None, *, parse: Callable[[str], datetime | None],
                  manual: datetime | None = None, explicitly_no_deadline: bool = False) -> DeadlineState:
    if manual is not None:
        if manual.utcoffset() is None:
            raise ValueError('Ручной дедлайн должен содержать часовой пояс')
        if explicitly_no_deadline:
            raise ValueError('Одновременно задан срок и отсутствие срока')
        return DeadlineState(manual, True, 'manual')
    if explicitly_no_deadline:
        return DeadlineState(None, True, 'no_deadline')
    if not raw or not raw.strip():
        return DeadlineState(None, False, 'unavailable')
    # Сетевые ошибки обрабатываются на границе чтения Sheets; этот разбор локальный.
    try:
        value = parse(raw.strip())
    except (ValueError, TypeError, OverflowError):
        value = None
    if value is None or value.utcoffset() is None:
        return DeadlineState(None, False, 'unavailable')
    return DeadlineState(value, True, 'sheet')
