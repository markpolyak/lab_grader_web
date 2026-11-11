"""
Parser of the SUAI (ГУАП) timetable pages at https://guap.ru/rasp.

Ported from markpolyak/suai-schedule-parser, files suai_rasp/parse.py and
suai_rasp/model.py at commit 6ee9973
(https://github.com/markpolyak/suai-schedule-parser). Its author, Mark Polyak,
allowed the parser to be used in this project «целиком, частично или как
справочником» in issue #62 (https://github.com/markpolyak/lab_grader_web/issues/62).

What differs from the original:
  * BeautifulSoup runs on the stdlib "html.parser" instead of lxml, so the
    only new dependency is beautifulsoup4;
  * every parse_* function accepts either the HTML text or an already built
    soup, so a page is parsed once and not once per function;
  * a lesson carries its time as printed next to the pair number
    ("4 пара (15:10—16:40)"), LESSON_TIMES is only the fallback;
  * parse_result_banner() tells a real filter result from the empty search
    form and from "nothing found";
  * the conflict-checking part of the model (Schedule, dedup, directories)
    is not needed here and is left out.

The module only turns HTML into weekly slots. Fetching, caching, the saved
copy and laying the slots onto calendar dates live in grading/schedule/guap.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

# Номер пары -> (начало, конец), HH:MM. Запасной вариант, если время пары
# не напечатано рядом с её номером.
LESSON_TIMES = {
    1: ("09:30", "11:00"),
    2: ("11:10", "12:40"),
    3: ("13:00", "14:30"),
    4: ("15:10", "16:40"),
    5: ("17:00", "18:30"),
    6: ("18:40", "20:10"),
    7: ("20:20", "21:50"),
}

WEEK_ANY = 0    # каждую неделю
WEEK_ODD = 1    # верхняя (нечётная), маркер ▲
WEEK_EVEN = 2   # нижняя (чётная), маркер ▼

OUT_OF_GRID = "Вне сетки расписания"

# Values of parse_result_banner()[0].
BANNER_FILTER = "filter"        # "Фильтр: ..." - the page shows a schedule
BANNER_NO_MATCH = "no_match"    # "Совпадений не обнаружено."
BANNER_OTHER = "other"          # empty search form, error page, redesign

_SLOT_RE = re.compile(r"^(\d+)\s+пара")
_SLOT_TIME_RE = re.compile(r"\((\d{1,2}):(\d{2})\s*[—–-]\s*(\d{1,2}):(\d{2})\)")
_ID_RE = re.compile(r"\?(gr|pr|ch|ad)=(\d+)")


@dataclass(frozen=True)
class Ref:
    """A directory entry of the site: numeric id and the displayed name."""
    id: int
    name: str


@dataclass
class RaspLesson:
    """
    One card of the timetable grid (`Lesson` in the original model).

    Cards out of the grid have day=None and slot=None.
    """
    day: str | None
    slot: int | None
    week: int
    kind: str
    subject: str
    room: Ref | None
    chair: Ref | None
    teachers: list[Ref] = field(default_factory=list)
    groups: list[Ref] = field(default_factory=list)
    start: str | None = None     # "HH:MM", московское время, как на сайте
    end: str | None = None

    @property
    def in_grid(self) -> bool:
        return self.day is not None and self.slot is not None


def make_soup(html) -> BeautifulSoup:
    """Parse a page once to pass it to several parse_* functions."""
    if isinstance(html, BeautifulSoup):
        return html
    return BeautifulSoup(html, "html.parser")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _ref(a) -> Ref:
    m = _ID_RE.search(a.get("href", ""))
    return Ref(int(m.group(2)), _clean(a.get_text()))


def parse_result_banner(html) -> tuple[str, str]:
    """
    Classify the page by its `#result` element.

    Returns:
        (kind, text): kind is BANNER_FILTER when the element starts with
        "Фильтр:", BANNER_NO_MATCH for "Совпадений не обнаружено", and
        BANNER_OTHER otherwise (including a page without the element)
    """
    node = make_soup(html).find(id="result")
    if node is None:
        return BANNER_OTHER, ""
    text = _clean(node.get_text())
    if text.startswith("Фильтр:"):
        return BANNER_FILTER, text
    if "Совпадений не обнаружено" in text:
        return BANNER_NO_MATCH, text
    return BANNER_OTHER, text


def parse_meta(html) -> dict:
    """Semester, build date, today's date and the number of the study week."""
    text = _clean(make_soup(html).get_text())
    meta: dict = {}
    m = re.search(r"(Осенний|Весенний) семестр (\d{4}/\d{2}) учебного года", text)
    if m:
        meta["semester"] = m.group(1)
        meta["academic_year"] = m.group(2)
    m = re.search(r"\(сборка: (\d{4}-\d{2}-\d{2})\)", text)
    if m:
        meta["built"] = m.group(1)
    m = re.search(r"Сегодня (.+?) г\.", text)
    if m:
        meta["today"] = m.group(1)
    m = re.search(r"([▲▼])\s*(\d+)\s+(верхняя|нижняя)", text)
    if m:
        meta["week_number"] = int(m.group(2))
        meta["week_parity"] = WEEK_ODD if m.group(3) == "верхняя" else WEEK_EVEN
    return meta


def _week_of(block) -> int:
    marker = block.find("div", recursive=False)
    if marker is None:
        return WEEK_ANY
    cls = marker.get("class") or []
    if "week1" in cls:
        return WEEK_ODD
    if "week2" in cls:
        return WEEK_EVEN
    return WEEK_ANY


def _slot_time(node, slot: int) -> tuple[str | None, str | None]:
    """Time of a pair: "(15:10—16:40)" from the page, else LESSON_TIMES."""
    small = node.find("small")
    m = _SLOT_TIME_RE.search(_clean(small.get_text()) if small else _clean(node.get_text()))
    if m:
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        if h1 < 24 and m1 < 60 and h2 < 24 and m2 < 60 and (h1, m1) < (h2, m2):
            return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"
    return LESSON_TIMES.get(slot, (None, None))


def parse_lessons(html) -> list[RaspLesson]:
    """Parse a page of the extended view (/rasp) into a list of lessons."""
    content = make_soup(html).find("div", class_="content")
    if content is None:
        return []

    lessons: list[RaspLesson] = []
    day: str | None = None
    slot: int | None = None
    start = end = None

    for node in content.find_all(["h4", "div"], recursive=True):
        txt = _clean(node.get_text())
        if node.name == "h4":
            if txt == OUT_OF_GRID:
                day, slot = None, None
            elif txt in DAYS:
                day, slot = txt, None
            start = end = None
            continue

        cls = node.get("class") or []
        if "text-danger" in cls and "mt-3" in cls:
            m = _SLOT_RE.match(txt)
            if m:
                slot = int(m.group(1))
                start, end = _slot_time(node, slot)
            continue

        # карточка занятия: <div class="mb-3 py-2 d-flex gap-2">
        if "mb-3" in cls and "d-flex" in cls and "py-2" in cls:
            lesson = _parse_card(node, day, slot, start, end)
            if lesson:
                lessons.append(lesson)

    return lessons


def _parse_card(block, day, slot, start=None, end=None) -> RaspLesson | None:
    kind_el = block.find("div", class_="fs-6")
    subj_el = block.find("div", class_="lead")
    if kind_el is None or subj_el is None:
        return None

    body = subj_el.parent
    room = chair = None
    teachers: list[Ref] = []
    groups: list[Ref] = []

    for a in body.find_all("a", href=True):
        m = _ID_RE.search(a["href"])
        if not m:
            continue
        kind = m.group(1)
        if kind == "ad":
            room = _ref(a)
        elif kind == "ch":
            chair = _ref(a)
        elif kind == "pr":
            teachers.append(_ref(a))
        elif kind == "gr":
            groups.append(_ref(a))

    in_grid = slot is not None
    return RaspLesson(
        day=day if in_grid else None,
        slot=slot,
        week=_week_of(block),
        kind=_clean(kind_el.get_text()),
        subject=_clean(subj_el.get_text()),
        room=room,
        chair=chair,
        teachers=teachers,
        groups=groups,
        start=start if in_grid else None,
        end=end if in_grid else None,
    )
