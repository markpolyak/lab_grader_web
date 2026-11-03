"""
Lecture quiz files: parsing, parameters, scoring (docs/QUIZ_PLAN.md §3-§4).

A quiz is one markdown file in the course config store
(`quizzes/{course_id}/{quiz_id}.md`): a question bank in the format of
`course-maker`'s `quiz_questions.md`, with the quiz parameters in a YAML
front matter written by the server.

Parsing is structural, not by words - the labels ("Вопрос", "Вариант", the
variant letters) are in the course's language:

- `###` heading - a question; a number with `%` at its end is the weight;
- an italic line right after the heading - a subtitle for the teacher only;
- `**Label:**` at the start of a line opens a variant of the question; its text
  is the rest of that line and everything up to the first list item;
- `- ` list items - the answer options; the correct one ends with ` ✓`.

Lines inside fenced code blocks are never structure.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import yaml

CORRECT_MARK = "✓"
OPTIONS_PER_QUESTION = 4
SECONDS_MIN, SECONDS_MAX, SECONDS_DEFAULT = 5, 300, 20
# How long the correct answer stays on screen before the next question starts
# by itself; 0 - only by the teacher's button.
REVEAL_MIN, REVEAL_MAX, REVEAL_DEFAULT = 0, 120, 10

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*$")
_VARIANT_RE = re.compile(r"^\*\*([^*\n]+?)\s*:\s*\*\*\s*(.*)$")
_OPTION_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_ITALIC_RE = re.compile(r"^\*(?!\*)\S")
_QUIZ_ID_RE = re.compile(r"[^a-z0-9]+")


class QuizFormatError(ValueError):
    """The file is not a valid quiz. `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class Variant:
    label: str | None
    text: str
    options: tuple[str, ...]
    correct: int

    def to_dict(self, with_answer: bool = True) -> dict:
        data = {"label": self.label, "text": self.text, "options": list(self.options)}
        if with_answer:
            data["correct"] = self.correct
        return data


@dataclass(frozen=True)
class Question:
    label: str
    subtitle: str | None
    weight: float | None
    variants: tuple[Variant, ...]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "subtitle": self.subtitle,
            "weight": self.weight,
            "variants": [v.to_dict() for v in self.variants],
        }


@dataclass(frozen=True)
class QuizSettings:
    title: str
    short_name: str
    max_points: float
    seconds_per_question: int = SECONDS_DEFAULT
    reveal_seconds: int = REVEAL_DEFAULT
    # A test: each student gets their own variant (А/Б/...) of every question,
    # so a neighbour's screen does not give the answer away. Off - one variant
    # for everybody, picked when the run is opened.
    variants_per_student: bool = False

    def to_front_matter(self) -> dict:
        data = {
            "title": self.title,
            "short-name": self.short_name,
            "max-points": self.max_points,
            "seconds-per-question": self.seconds_per_question,
        }
        if self.reveal_seconds != REVEAL_DEFAULT:
            data["reveal-seconds"] = self.reveal_seconds
        if self.variants_per_student:
            data["variants-per-student"] = True
        return data


@dataclass(frozen=True)
class Quiz:
    settings: QuizSettings
    questions: tuple[Question, ...]
    body: str = field(repr=False)

    def question_points(self) -> list[float]:
        return question_points(self.questions, self.settings.max_points)


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------


def split_front_matter(text: str) -> tuple[dict, str]:
    """
    Split `---\\n<yaml>\\n---\\n<body>`. A file without front matter gives `{}`.

    Raises:
        QuizFormatError: the front matter is not a YAML mapping
    """
    text = text.lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            try:
                meta = yaml.safe_load("\n".join(lines[1:end])) or {}
            except yaml.YAMLError as e:
                raise QuizFormatError([f"Заголовок файла (front matter) не разбирается: {e}"])
            if not isinstance(meta, dict):
                raise QuizFormatError(["Заголовок файла (front matter) должен быть набором полей"])
            return meta, "\n".join(lines[end + 1:])
    return {}, text


def build_quiz_file(settings: QuizSettings, body: str) -> str:
    """The stored file: front matter written by the server + the question bank."""
    meta = yaml.safe_dump(settings.to_front_matter(), allow_unicode=True, sort_keys=False)
    return f"---\n{meta}---\n{body.lstrip(chr(10))}"


def settings_from(values: dict, default_title: str | None = None) -> QuizSettings:
    """
    Validate quiz parameters (from the admin form or a front matter).

    Raises:
        QuizFormatError: listing every invalid parameter
    """
    errors = []
    title = str(values.get("title") or default_title or "").strip()
    if not title:
        errors.append("Не задано название квиза")

    short_name = str(values.get("short-name") or "").strip()
    if not short_name:
        errors.append("Не задано короткое имя (short-name) - заголовок столбца в таблице")

    max_points = None
    try:
        max_points = float(str(values.get("max-points")).replace(",", "."))
        if not math.isfinite(max_points) or max_points <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Максимальный балл (max-points) должен быть числом больше 0")

    seconds = values.get("seconds-per-question")
    if seconds in (None, ""):
        seconds = SECONDS_DEFAULT
    try:
        seconds = int(seconds)
        if not SECONDS_MIN <= seconds <= SECONDS_MAX:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"Время на вопрос должно быть целым числом от {SECONDS_MIN} до {SECONDS_MAX} секунд")

    reveal = values.get("reveal-seconds")
    if reveal in (None, ""):
        reveal = REVEAL_DEFAULT
    try:
        reveal = int(reveal)
        if not REVEAL_MIN <= reveal <= REVEAL_MAX:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(
            f"Время показа ответа должно быть целым числом от {REVEAL_MIN} до {REVEAL_MAX} секунд "
            "(0 - следующий вопрос только по кнопке)"
        )

    if errors:
        raise QuizFormatError(errors)
    if max_points == int(max_points):
        max_points = int(max_points)
    return QuizSettings(
        title=title, short_name=short_name, max_points=max_points, seconds_per_question=seconds,
        reveal_seconds=reveal, variants_per_student=_as_bool(values.get("variants-per-student")),
    )


def _as_bool(value) -> bool:
    """YAML true/false, or a form field: "true", "on", "1"."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "on", "1", "yes", "да")


def quiz_id_from_filename(filename: str) -> str:
    """`Лекция 5 - Quiz_05.md` -> `quiz-05`: the file name without extension, reduced to [a-z0-9-]."""
    stem = filename.rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    quiz_id = _QUIZ_ID_RE.sub("-", stem.lower()).strip("-")
    if not quiz_id:
        raise QuizFormatError(["Имя файла должно содержать латинские буквы или цифры"])
    return quiz_id[:64]


# ---------------------------------------------------------------------------
# Question bank
# ---------------------------------------------------------------------------


def _structural_lines(body: str):
    """Yield (line, in_code) - `in_code` for lines of fenced code blocks, fences included."""
    in_code = False
    fence = None
    for line in body.split("\n"):
        match = _FENCE_RE.match(line)
        if match:
            if not in_code:
                in_code, fence = True, match.group(1)
                yield line, True
                continue
            if match.group(1) == fence:
                yield line, True
                in_code, fence = False, None
                continue
        yield line, in_code


def _question_label(heading: str) -> str:
    """`Вопрос 1.1 — Тип 1 (одиночный выбор), 25%` -> `Вопрос 1.1` (for error messages)."""
    label = _WEIGHT_RE.sub("", heading).strip().rstrip(",").strip()
    for separator in (" — ", " – ", " - "):
        if separator in label:
            label = label.split(separator, 1)[0]
    return label.strip() or heading


def _parse_weight(heading: str) -> float | None:
    match = _WEIGHT_RE.search(heading)
    return float(match.group(1).replace(",", ".")) if match else None


def _clean(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines)


def _parse_question(heading: str, lines: list[tuple[str, bool]], errors: list[str]) -> Question:
    label = _question_label(heading)

    # Optional italic subtitle right after the heading - teacher-only.
    subtitle = None
    rest = list(lines)
    while rest and not rest[0][0].strip() and not rest[0][1]:
        rest.pop(0)
    if rest and not rest[0][1] and _ITALIC_RE.match(rest[0][0].strip()):
        subtitle = rest.pop(0)[0].strip()

    # Split into variants by `**Label:**` lines (outside code).
    chunks: list[tuple[str | None, list[tuple[str, bool]]]] = []
    current: tuple[str | None, list[tuple[str, bool]]] | None = None
    for line, in_code in rest:
        match = None if in_code else _VARIANT_RE.match(line.strip())
        if match:
            current = (match.group(1).strip(), [(match.group(2), False)] if match.group(2).strip() else [])
            chunks.append(current)
            continue
        if current is None:
            current = (None, [])
            chunks.append(current)
        current[1].append((line, in_code))

    labelled = [c for c in chunks if c[0] is not None]
    if labelled:
        # Text before the first label (if any) is not part of any variant.
        chunks = labelled

    variants = []
    for variant_label, variant_lines in chunks:
        where = f"{label}, {variant_label}" if variant_label else label
        text_lines: list[str] = []
        options: list[str] = []
        for line, in_code in variant_lines:
            option = None if in_code else _OPTION_RE.match(line)
            if option:
                options.append(option.group(1).strip())
                continue
            if options:
                # After the list: blank lines and stray notes are not part of the question.
                continue
            text_lines.append(line)

        text = _clean(text_lines)
        correct = [i for i, option in enumerate(options) if option.endswith(CORRECT_MARK)]
        options = [o[: -len(CORRECT_MARK)].rstrip() if o.endswith(CORRECT_MARK) else o for o in options]

        if not text.strip():
            errors.append(f"{where}: пустой текст вопроса")
        if len(options) != OPTIONS_PER_QUESTION:
            errors.append(f"{where}: {len(options)} вариантов ответа (нужно {OPTIONS_PER_QUESTION})")
        if len(correct) != 1:
            errors.append(
                f"{where}: отмечено правильных ответов - {len(correct)} (нужен ровно один {CORRECT_MARK})"
            )
        variants.append(Variant(
            label=variant_label, text=text, options=tuple(options), correct=correct[0] if correct else -1,
        ))

    if not variants:
        errors.append(f"{label}: нет ни текста вопроса, ни вариантов ответа")

    return Question(label=label, subtitle=subtitle, weight=_parse_weight(heading), variants=tuple(variants))


def parse_question_bank(body: str) -> tuple[str | None, tuple[Question, ...]]:
    """
    Parse the markdown question bank.

    Returns:
        (title from the first `#` heading or None, questions)

    Raises:
        QuizFormatError: listing every invalid question/variant
    """
    title = None
    questions = []
    errors: list[str] = []
    heading = None
    collected: list[tuple[str, bool]] = []

    def flush():
        if heading is not None:
            questions.append(_parse_question(heading, collected, errors))

    for line, in_code in _structural_lines(body):
        match = None if in_code else _HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            if level == 1 and title is None:
                title = match.group(2).strip()
            if level <= 3:
                flush()
                heading = match.group(2).strip() if level == 3 else None
                collected = []
                continue
        if heading is not None:
            collected.append((line, in_code))
    flush()

    if not questions:
        errors.insert(0, "В файле нет вопросов (заголовков ###)")
    if errors:
        raise QuizFormatError(errors)
    return title, tuple(questions)


def parse_quiz(text: str, overrides: dict | None = None) -> Quiz:
    """
    Parse a stored quiz file (front matter + bank). `overrides` - parameters from
    the admin form, taking precedence over the front matter.

    Raises:
        QuizFormatError
    """
    meta, body = split_front_matter(text)
    bank_title, questions = parse_question_bank(body)
    settings = settings_from({**meta, **(overrides or {})}, default_title=bank_title)
    return Quiz(settings=settings, questions=questions, body=body)


# ---------------------------------------------------------------------------
# Scoring (§4)
# ---------------------------------------------------------------------------


def question_points(questions, max_points: float) -> list[float]:
    """
    Points for each question: `max_points * weight / sum of weights`; all
    questions weigh the same unless every one of them has a weight.
    """
    weights = [q.weight for q in questions]
    if not weights:
        return []
    if any(w is None for w in weights) or sum(weights) <= 0:
        weights = [1.0] * len(questions)
    total = sum(weights)
    return [max_points * w / total for w in weights]


def format_points(value: float) -> str:
    """Rounded to 2 places, no trailing zeros: 1.5, not 1.50; 2, not 2.00."""
    text = f"{round(value + 1e-12, 2):.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def student_score(points: list[float], correct_questions) -> float:
    """Sum of the points of the questions answered correctly (others - including unasked ones - give 0)."""
    return sum(points[i] for i in set(correct_questions) if 0 <= i < len(points))
