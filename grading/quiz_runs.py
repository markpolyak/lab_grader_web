"""
Running a lecture quiz (docs/QUIZ_PLAN.md §5-§8).

All run state lives in memory of the single backend process and is changed
only on the event loop, under one asyncio.Lock. Blocking work - reading the
roster from Google Sheets, writing scores - runs in threads
(asyncio.to_thread) and never on a student's request path, except the one
roster read when a run is opened (and a re-read for a just-registered student).

Every snapshot sent over SSE is complete (not a delta) and carries a version
number; a reconnecting client simply gets the current one.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from gspread.utils import ValueInputOption, rowcol_to_a1

from grading.quiz import Quiz, QuizFormatError, build_quiz_file, format_points, parse_quiz, student_score
from grading.score import format_score
from grading.sheets_client import cell_from_grid

logger = logging.getLogger(__name__)

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LENGTH = 6
ANSWER_GRACE_SECONDS = 1.0
ROSTER_REREAD_SECONDS = 60
FINISHED_CODE_TTL_SECONDS = 3600
FIRST_STUDENT_ROW = 3
HEADER_ROW = 2
OPTION_LETTERS = "ABCD"
# "In the room only": the QR code on the projector changes every slot; a code
# is accepted during its slot and the next one, so a link lives 15-30 s.
ENTRY_SLOT_SECONDS = 15

LOBBY, QUESTION, REVEAL, FINISHED, CANCELLED = "lobby", "question", "reveal", "finished", "cancelled"
ENDED = (FINISHED, CANCELLED)


class QuizRunError(Exception):
    """A refused action. `code` is stable (the frontend translates it), `status` is the HTTP status."""

    def __init__(self, code: str, status: int, detail: str | None = None, **extra):
        self.code = code
        self.status = status
        self.detail = detail or code
        self.extra = extra
        super().__init__(self.detail)


def quiz_column(values: list[list[str]], short_name: str) -> int | None:
    """
    1-based column whose header in the SECOND row is `short_name` (§2 of the plan).

    Only that row: the first one holds lab deadlines - dates - and a quiz used
    for attendance is named by the lecture's date, so a whole-sheet search
    could land in a lab's column. The header is compared as displayed.
    """
    if not short_name or len(values) < HEADER_ROW:
        return None
    wanted = short_name.strip()
    for idx, value in enumerate(values[HEADER_ROW - 1]):
        if (value or "").strip() == wanted:
            return idx + 1
    return None


# ---------------------------------------------------------------------------
# Roster: who is in the course (§6.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterEntry:
    login: str
    group: str
    row: int
    name: str


@dataclass
class Roster:
    entries: dict[str, RosterEntry]           # casefolded login -> entry
    sheets_without_column: list[str]
    read_at: float

    def find(self, login: str) -> RosterEntry | None:
        return self.entries.get(login.casefold())


def read_roster(spreadsheet, info_sheet: str | None, name_column: int, short_name: str, now: float) -> Roster:
    """
    Read every group sheet once (`get_all_values`): GitHub login -> group, row, name.

    Args:
        name_column: `google.student-name-column` (0-based, as in the course config)
    """
    entries: dict[str, RosterEntry] = {}
    without_column = []
    for worksheet in spreadsheet.worksheets():
        if worksheet.title == info_sheet:
            continue
        values = worksheet.get_all_values()
        header = values[0] if values else []
        if "GitHub" not in header:
            logger.warning(f"Quiz roster: sheet '{worksheet.title}' has no GitHub column, skipped")
            continue
        github_col = header.index("GitHub") + 1
        if quiz_column(values, short_name) is None:
            without_column.append(worksheet.title)
        for row in range(FIRST_STUDENT_ROW, len(values) + 1):
            login = cell_from_grid(values, row, github_col).strip()
            if not login:
                continue
            key = login.casefold()
            if key in entries:
                logger.warning(
                    f"Quiz roster: login '{login}' is in sheets '{entries[key].group}' and "
                    f"'{worksheet.title}' - the first one is used"
                )
                continue
            entries[key] = RosterEntry(
                login=login, group=worksheet.title, row=row,
                name=cell_from_grid(values, row, name_column + 1).strip(),
            )
    return Roster(entries=entries, sheets_without_column=without_column, read_at=now)


# ---------------------------------------------------------------------------
# Writing scores (§7)
# ---------------------------------------------------------------------------


def write_scores(spreadsheet, short_name: str, scores: dict[str, list[tuple[RosterEntry, str]]],
                 separator: str) -> dict[str, dict]:
    """
    Write scores, one `batch_update` per sheet; only empty cells are written.

    Args:
        scores: group -> [(entry, score as "1.5")]

    Returns:
        group -> {"written": [logins], "skipped": [{"login", "name", "value"}],
                  "missing_column": bool, "error": str | None}
    """
    report = {}
    for group, items in scores.items():
        result = {"written": [], "skipped": [], "missing_column": False, "error": None}
        report[group] = result
        try:
            worksheet = spreadsheet.worksheet(group)
            values = worksheet.get_all_values()
            col = quiz_column(values, short_name)
            if col is None:
                result["missing_column"] = True
                continue
            updates = []
            for entry, score in items:
                current = cell_from_grid(values, entry.row, col).strip()
                if current:
                    result["skipped"].append({"login": entry.login, "name": entry.name, "value": current})
                    continue
                updates.append((entry, format_score(score, separator)))
            if updates:
                worksheet.batch_update(
                    [{"range": rowcol_to_a1(e.row, col), "values": [[v]]} for e, v in updates],
                    raw=False, value_input_option=ValueInputOption.user_entered,
                )
            result["written"] = [e.login for e, _ in updates]
        except Exception as e:  # one broken sheet must not lose the others
            logger.exception(f"Quiz: writing scores to sheet '{group}' failed")
            result["error"] = str(e)
    return report


# ---------------------------------------------------------------------------
# Answer log (§8)
# ---------------------------------------------------------------------------


class RunLog:
    """`{LOG_DIR}/quiz/{course_id}/{run_id}.jsonl` - one line per event, for disputes."""

    def __init__(self, log_dir: str | None, course_id: str, run_id: str, clock: Callable[[], float]):
        self.path = os.path.join(log_dir, "quiz", course_id, f"{run_id}.jsonl") if log_dir else None
        self.clock = clock

    def write(self, event: str, **data) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": self.clock(), "event": event, **data}, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error(f"Quiz log {self.path}: {e}")


# ---------------------------------------------------------------------------
# A run
# ---------------------------------------------------------------------------


@dataclass
class Participant:
    entry: RosterEntry
    joined_at: float
    # question index -> option id (its position in the file)
    answers: dict[int, int] = field(default_factory=dict)
    correct: set[int] = field(default_factory=set)


@dataclass
class QuizRun:
    run_id: str
    code: str
    course_id: str
    course_name: str
    quiz_id: str
    quiz: Quiz
    # question index -> index of the variant (А/Б/...) everyone gets
    variant_choice: list[int]
    roster: Roster
    log: RunLog
    created_at: float
    state: str = LOBBY
    question_index: int = -1
    deadline: float | None = None
    ended_at: float | None = None
    participants: dict[str, Participant] = field(default_factory=dict)  # casefolded login
    asked: int = 0
    write_report: dict | None = None
    writing: bool = False
    version: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    reveal_task: asyncio.Task | None = None
    # "In the room only" (chosen when the run is opened): the question text is
    # shown on the projector only, joining needs the QR code currently on the
    # projector and closes when the first question ends.
    in_room: bool = False
    join_secret: bytes = field(default_factory=lambda: secrets.token_bytes(16))

    # -- in the room only --------------------------------------------------

    def entry_token(self, slot: int) -> str:
        digest = hmac.new(self.join_secret, str(slot).encode(), hashlib.sha256).digest()
        return base64.b32encode(digest).decode("ascii")[:8].lower()

    def current_entry_token(self, now: float) -> str:
        return self.entry_token(int(now // ENTRY_SLOT_SECONDS))

    def check_entry_token(self, token: str | None, now: float) -> bool:
        if not token:
            return False
        slot = int(now // ENTRY_SLOT_SECONDS)
        return any(hmac.compare_digest(token.lower(), self.entry_token(s)) for s in (slot, slot - 1))

    def join_open(self) -> bool:
        """New participants: always, or - in the room only - until the first question ends."""
        if not self.in_room:
            return self.state not in ENDED
        return self.state == LOBBY or (self.state == QUESTION and self.question_index == 0)

    # -- helpers -----------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.quiz.questions)

    @property
    def per_student(self) -> bool:
        return self.quiz.settings.variants_per_student

    def variant_index(self, i: int, login: str | None = None) -> int:
        """
        Which variant of question i this student gets: the run's common choice,
        or - for a test (`variants-per-student`) - the student's own, stable
        across reconnects.
        """
        count = len(self.quiz.questions[i].variants)
        if not self.per_student or count == 1:
            return self.variant_choice[i]
        if not login:
            raise ValueError("variants differ per student - a login is required")
        return random.Random(f"{self.run_id}:{login.casefold()}:{i}:variant").randrange(count)

    def variant(self, i: int, login: str | None = None):
        return self.quiz.questions[i].variants[self.variant_index(i, login)]

    def option_order(self, login: str, i: int) -> list[int]:
        """Per-student shuffle, stable across reconnects (§5.3)."""
        order = list(range(len(self.variant(i, login or "-").options)))
        random.Random(f"{self.run_id}:{login.casefold()}:{i}").shuffle(order)
        return order

    def points(self) -> list[float]:
        return self.quiz.question_points()

    def score_of(self, participant: Participant) -> float:
        return student_score(self.points(), participant.correct)

    def bump(self) -> None:
        self.version += 1
        event, self.changed = self.changed, asyncio.Event()
        event.set()

    def remaining_ms(self, now: float) -> int:
        if self.state != QUESTION or self.deadline is None:
            return 0
        return max(0, int((self.deadline - now) * 1000))

    # -- snapshots (§9.3) --------------------------------------------------

    def _base(self, now: float) -> dict:
        return {
            "version": self.version,
            "state": self.state,
            "course": self.course_name,
            "course_id": self.course_id,
            "title": self.quiz.settings.title,
            "question": self.question_index,
            "total": self.total,
            "seconds": self.quiz.settings.seconds_per_question,
            "remaining_ms": self.remaining_ms(now),
        }

    def student_snapshot(self, login: str | None, now: float) -> dict:
        data = self._base(now)
        data["in_room"] = self.in_room
        participant = self.participants.get(login.casefold()) if login else None
        data["joined"] = participant is not None
        if self.state in (QUESTION, REVEAL):
            i = self.question_index
            variant = self.variant(i, login or "-")
            order = self.option_order(login or "", i)
            if not self.in_room:
                # In the room only, the question is on the projector, not on the phone.
                data["text"] = variant.text
            data["options"] = [{"id": o, "text": variant.options[o]} for o in order]
            answer = participant.answers.get(i) if participant else None
            data["answer"] = answer
            if self.state == REVEAL:
                # The correct answer leaves the server only after the deadline.
                data["correct"] = variant.correct
                data["answered_correctly"] = answer is not None and answer == variant.correct
        if self.state == FINISHED and participant:
            data["score"] = format_points(self.score_of(participant))
            data["max_points"] = format_points(self.quiz.settings.max_points)
        return data

    def projector_snapshot(self, now: float) -> dict:
        data = self._base(now)
        data.update({
            "run_id": self.run_id,
            "code": self.code,
            "quiz_id": self.quiz_id,
            "short_name": self.quiz.settings.short_name,
            "joined": len(self.participants),
            "warnings": self.warnings(),
        })
        data["variants_per_student"] = self.per_student
        data["in_room"] = self.in_room
        if self.state in (QUESTION, REVEAL) and self.per_student and len(self.quiz.questions[self.question_index].variants) > 1:
            # No common text to show: everyone has their own variant. The
            # projector shows progress, then how each variant went.
            i = self.question_index
            data["label"] = self.quiz.questions[i].label
            data["answered"] = sum(1 for p in self.participants.values() if i in p.answers)
            if self.state == REVEAL:
                stats = {}
                for p in self.participants.values():
                    v = self.variant_index(i, p.entry.login)
                    row = stats.setdefault(v, {"answered": 0, "correct": 0})
                    if i in p.answers:
                        row["answered"] += 1
                        row["correct"] += int(i in p.correct)
                data["by_variant"] = [
                    {"label": variant.label or str(v + 1), **stats.get(v, {"answered": 0, "correct": 0})}
                    for v, variant in enumerate(self.quiz.questions[i].variants)
                ]
        elif self.state in (QUESTION, REVEAL):
            i = self.question_index
            variant = self.variant(i, "-")
            data["text"] = variant.text
            if not self.in_room or self.state == REVEAL:
                # In the room only the answers are on the phones (each in its own
                # order); the projector shows them with the correct one at reveal.
                data["options"] = [
                    {"id": o, "letter": OPTION_LETTERS[o], "text": text} for o, text in enumerate(variant.options)
                ]
            data["answered"] = sum(1 for p in self.participants.values() if i in p.answers)
            if self.state == REVEAL:
                data["correct"] = variant.correct
                counts = [0] * len(variant.options)
                for p in self.participants.values():
                    if i in p.answers:
                        counts[p.answers[i]] += 1
                data["distribution"] = counts
        if self.state == FINISHED:
            scores = [self.score_of(p) for p in self.participants.values()]
            data["summary"] = {
                "participants": len(scores),
                "average": format_points(sum(scores) / len(scores)) if scores else None,
                "max_points": format_points(self.quiz.settings.max_points),
                "asked": self.asked,
            }
            data["writing"] = self.writing
            data["write_report"] = self.write_report
        return data

    def warnings(self) -> list[str]:
        missing = self.roster.sheets_without_column
        if not missing:
            return []
        return [
            f"В листах {', '.join(missing)} нет столбца «{self.quiz.settings.short_name}»: "
            "результаты этих групп не будут записаны"
        ]

    def summary(self) -> dict:
        return {
            "run_id": self.run_id, "code": self.code, "course_id": self.course_id, "quiz_id": self.quiz_id,
            "state": self.state, "warnings": self.warnings(),
        }


# ---------------------------------------------------------------------------
# All runs of the process
# ---------------------------------------------------------------------------


class QuizRunManager:
    """
    Registry and state machine of quiz runs.

    `roster_reader(course_info, short_name) -> Roster` and
    `score_writer(course_info, short_name, scores) -> report` are blocking and
    called in threads; tests replace them.
    """

    def __init__(
        self,
        roster_reader: Callable,
        score_writer: Callable,
        log_dir: str | None = None,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ):
        self.roster_reader = roster_reader
        self.score_writer = score_writer
        self.log_dir = log_dir
        self.clock = clock
        self.rng = rng or random.SystemRandom()
        self.runs: dict[str, QuizRun] = {}
        self.by_code: dict[str, str] = {}
        self._lock: asyncio.Lock | None = None
        self._course_info: dict[str, dict] = {}

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # -- lookup ------------------------------------------------------------

    def get(self, run_id: str) -> QuizRun | None:
        return self.runs.get(run_id)

    def by_student_code(self, code: str) -> QuizRun | None:
        run = self.runs.get(self.by_code.get(code.lower(), ""))
        if run is None:
            return None
        if run.state in ENDED and run.ended_at is not None and self.clock() - run.ended_at > FINISHED_CODE_TTL_SECONDS:
            return None
        return run

    def active_for_course(self, course_id: str) -> QuizRun | None:
        for run in self.runs.values():
            if run.course_id == course_id and run.state not in ENDED:
                return run
        return None

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self.by_code:
                return code

    def _forget_old(self) -> None:
        now = self.clock()
        for run_id, run in list(self.runs.items()):
            if run.state in ENDED and run.ended_at and now - run.ended_at > 2 * FINISHED_CODE_TTL_SECONDS:
                self.by_code.pop(run.code, None)
                self.runs.pop(run_id, None)

    # -- opening -----------------------------------------------------------

    async def open(self, course_id: str, course_info: dict, quiz_id: str, quiz: Quiz, in_room: bool = False) -> QuizRun:
        """lobby: read the roster, pick the variants, issue the code. 409 if the course has a run going."""
        if in_room and quiz.settings.variants_per_student:
            raise QuizRunError(
                "IN_ROOM_WITH_VARIANTS", 400,
                "Режим «только в аудитории» несовместим с разными вариантами: на экране один общий вопрос",
            )
        existing = self.active_for_course(course_id)
        if existing:
            raise QuizRunError("RUN_IN_PROGRESS", 409, "У курса уже идёт квиз", run_id=existing.run_id)

        roster = await asyncio.to_thread(self.roster_reader, course_info, quiz.settings.short_name)

        async with self.lock:
            existing = self.active_for_course(course_id)
            if existing:
                raise QuizRunError("RUN_IN_PROGRESS", 409, "У курса уже идёт квиз", run_id=existing.run_id)
            self._forget_old()
            run_id = uuid.uuid4().hex
            run = QuizRun(
                run_id=run_id,
                code=self._new_code(),
                course_id=course_id,
                course_name=str(course_info.get("name", course_id)),
                quiz_id=quiz_id,
                quiz=quiz,
                variant_choice=[self.rng.randrange(len(q.variants)) for q in quiz.questions],
                roster=roster,
                log=RunLog(self.log_dir, course_id, run_id, self.clock),
                created_at=self.clock(),
                in_room=in_room,
            )
            self.runs[run_id] = run
            self.by_code[run.code] = run_id
            self._course_info[run_id] = course_info
            run.log.write(
                # Everything needed to rebuild the run after a backend restart
                # (recover): the quiz as it was at opening, the variants drawn.
                "open", run_id=run_id, code=run.code, course_id=course_id, course_name=run.course_name,
                created_at=run.created_at, quiz_text=build_quiz_file(quiz.settings, quiz.body),
                variant_choice=run.variant_choice,
                in_room=in_room, join_secret=run.join_secret.hex(),
                quiz_id=quiz_id, settings=quiz.settings.to_front_matter(),
                variants="per-student" if quiz.settings.variants_per_student
                else [q.variants[v].label for q, v in zip(quiz.questions, run.variant_choice)],
                roster_size=len(roster.entries), sheets_without_column=roster.sheets_without_column,
            )
            return run

    # -- teacher actions ---------------------------------------------------

    async def start(self, run: QuizRun) -> None:
        async with self.lock:
            if run.state != LOBBY:
                raise QuizRunError("BAD_STATE", 409, "Квиз уже начат или завершён")
            self._ask(run, 0)

    async def next(self, run: QuizRun) -> None:
        async with self.lock:
            self._expire_locked(run)
            if run.state != REVEAL:
                raise QuizRunError("BAD_STATE", 409, "Следующий вопрос доступен после показа ответа")
            if run.question_index + 1 < run.total:
                self._ask(run, run.question_index + 1)
            else:
                self._finish_locked(run)

    async def finish(self, run: QuizRun) -> None:
        async with self.lock:
            if run.state == LOBBY or run.state in ENDED:
                raise QuizRunError("BAD_STATE", 409, "Завершить можно только начатый квиз")
            self._finish_locked(run)

    async def cancel(self, run: QuizRun) -> None:
        async with self.lock:
            if run.state in ENDED:
                raise QuizRunError("BAD_STATE", 409, "Квиз уже завершён")
            self._cancel_timer(run)
            run.state = CANCELLED
            run.ended_at = self.clock()
            run.log.write("cancel")
            run.bump()

    async def retry_write(self, run: QuizRun) -> None:
        async with self.lock:
            if run.state != FINISHED or run.writing:
                raise QuizRunError("BAD_STATE", 409, "Запись сейчас невозможна")
            self._start_write(run)

    def _ask(self, run: QuizRun, i: int) -> None:
        now = self.clock()
        run.state = QUESTION
        run.question_index = i
        run.asked = i + 1
        run.deadline = now + run.quiz.settings.seconds_per_question
        run.log.write("question", question=i, deadline=run.deadline)
        run.bump()
        self._cancel_timer(run)
        try:
            run.reveal_task = asyncio.get_running_loop().create_task(
                self._reveal_later(run, i, run.deadline + ANSWER_GRACE_SECONDS - now)
            )
        except RuntimeError:
            run.reveal_task = None

    def _cancel_timer(self, run: QuizRun) -> None:
        if run.reveal_task and not run.reveal_task.done():
            run.reveal_task.cancel()
        run.reveal_task = None

    async def _reveal_later(self, run: QuizRun, i: int, delay: float) -> None:
        await asyncio.sleep(max(0.0, delay))
        await self.reveal_if_due(run, i, force=True)

    async def reveal_if_due(self, run: QuizRun, i: int | None = None, force: bool = False) -> None:
        """question(i) -> reveal(i) once the deadline (plus the grace) has passed."""
        async with self.lock:
            if i is not None and run.question_index != i:
                return
            self._expire_locked(run, force=force)

    def _expire_locked(self, run: QuizRun, force: bool = False) -> None:
        if run.state != QUESTION or run.deadline is None:
            return
        if force or self.clock() > run.deadline + ANSWER_GRACE_SECONDS:
            run.state = REVEAL
            run.log.write("reveal", question=run.question_index)
            run.bump()

    def _finish_locked(self, run: QuizRun) -> None:
        self._cancel_timer(run)
        run.state = FINISHED
        run.ended_at = self.clock()
        run.log.write(
            "finish", asked=run.asked,
            scores={p.entry.login: format_points(run.score_of(p)) for p in run.participants.values()},
        )
        run.bump()
        self._start_write(run)

    # -- writing -----------------------------------------------------------

    def _start_write(self, run: QuizRun) -> None:
        run.writing = True
        run.bump()
        try:
            asyncio.get_running_loop().create_task(self._write(run))
        except RuntimeError:
            run.writing = False

    def _pending_scores(self, run: QuizRun) -> dict[str, list[tuple[RosterEntry, str]]]:
        """Scores not yet written (a retry writes only those - §7)."""
        done = set()
        for group in (run.write_report or {}).values():
            done.update(group.get("written", []))
            done.update(s["login"] for s in group.get("skipped", []))
        scores: dict[str, list] = {}
        for p in run.participants.values():
            if p.entry.login in done:
                continue
            scores.setdefault(p.entry.group, []).append((p.entry, format_points(run.score_of(p))))
        return scores

    async def _write(self, run: QuizRun) -> None:
        pending = self._pending_scores(run)
        try:
            report = await asyncio.to_thread(
                self.score_writer, self._course_info.get(run.run_id, {}), run.quiz.settings.short_name, pending
            ) if pending else {}
        except Exception as e:
            logger.exception(f"Quiz run {run.run_id}: writing scores failed")
            report = {group: {"written": [], "skipped": [], "missing_column": False, "error": str(e)}
                      for group in pending}
        async with self.lock:
            run.write_report = _merge_reports(run.write_report, report)
            run.writing = False
            run.log.write("write", report=report)
            run.bump()

    # -- students ----------------------------------------------------------

    async def join(self, run: QuizRun, login: str, entry_ok: bool = True) -> Participant:
        """
        Args:
            entry_ok: the student came through the QR code currently on the
                projector (checked by the caller); needed only in the room only.
        """
        if run.state in ENDED:
            raise QuizRunError("QUIZ_FINISHED", 410, "Квиз уже завершён")
        existing = run.participants.get(login.casefold())
        if existing is not None:
            return existing
        self._check_join_allowed(run, entry_ok)
        entry = run.roster.find(login)
        if entry is None and self.clock() - run.roster.read_at > ROSTER_REREAD_SECONDS:
            # Maybe registered a minute ago - read the sheets again (in a thread).
            course_info = self._course_info.get(run.run_id, {})
            roster = await asyncio.to_thread(self.roster_reader, course_info, run.quiz.settings.short_name)
            async with self.lock:
                run.roster = roster
            entry = roster.find(login)
        if entry is None:
            raise QuizRunError("NOT_IN_COURSE", 403, "Вас нет в таблице курса: зарегистрируйтесь в своей группе")

        async with self.lock:
            if run.state in ENDED:
                raise QuizRunError("QUIZ_FINISHED", 410, "Квиз уже завершён")
            key = login.casefold()
            participant = run.participants.get(key)
            if participant is None:
                self._check_join_allowed(run, entry_ok)
                participant = Participant(entry=entry, joined_at=self.clock())
                run.participants[key] = participant
                run.log.write("join", login=entry.login, group=entry.group, row=entry.row, name=entry.name)
                run.bump()
            return participant

    @staticmethod
    def _check_join_allowed(run: QuizRun, entry_ok: bool) -> None:
        if not run.in_room:
            return
        if not run.join_open():
            raise QuizRunError("JOIN_CLOSED", 403, "Подключение закрыто: квиз уже идёт")
        if not entry_ok:
            raise QuizRunError("ENTRY_REQUIRED", 403, "Отсканируйте QR-код с экрана в аудитории ещё раз")

    async def answer(self, run: QuizRun, login: str, question: int, option: int) -> bool:
        """Record an answer. Returns whether it is correct (not shown before reveal)."""
        received = self.clock()
        async with self.lock:
            participant = run.participants.get(login.casefold())
            if participant is None:
                raise QuizRunError("NOT_JOINED", 409, "Сначала подключитесь к квизу")
            if run.state == QUESTION and run.deadline is not None and received > run.deadline + ANSWER_GRACE_SECONDS:
                self._expire_locked(run)
            if run.state == REVEAL and question == run.question_index:
                self._log_answer(run, participant, question, option, received, accepted=False)
                raise QuizRunError("TOO_LATE", 410, "Время на ответ вышло")
            if run.state != QUESTION or question != run.question_index:
                raise QuizRunError("WRONG_QUESTION", 400, "Этот вопрос сейчас не задан")
            variant = run.variant(question, participant.entry.login)
            if not isinstance(option, int) or not 0 <= option < len(variant.options):
                raise QuizRunError("WRONG_OPTION", 400, "Нет такого варианта ответа")
            if question in participant.answers:
                raise QuizRunError("ALREADY_ANSWERED", 409, "Ответ на этот вопрос уже принят")
            participant.answers[question] = option
            correct = option == variant.correct
            if correct:
                participant.correct.add(question)
            self._log_answer(run, participant, question, option, received, accepted=True, correct=correct)
            run.bump()
            return correct

    def _log_answer(self, run, participant, question, option, received, accepted, correct=None):
        run.log.write(
            "answer", login=participant.entry.login, question=question,
            variant=run.variant(question, participant.entry.login).label, option=option,
            received=received, accepted=accepted, correct=correct,
        )

    # -- recovery after a restart ---------------------------------------------

    async def recover(self, course_loader: Callable[[str], dict], max_age_seconds: float = 24 * 3600) -> list[QuizRun]:
        """
        Rebuild runs from their answer logs after a backend restart.

        Runs that had not ended continue where they were (a question whose
        deadline passed during the restart is revealed); a finished run whose
        scores were not written yet is written now; a run ended less than an
        hour ago comes back read-only, so students still see their score.
        Phones and the projector reconnect by themselves (EventSource).

        Args:
            course_loader: course_id -> course_info (blocking, run in a thread)
        """
        if not self.log_dir:
            return []
        root = os.path.join(self.log_dir, "quiz")
        if not os.path.isdir(root):
            return []
        now = self.clock()
        recovered = []
        for course_dir in sorted(os.listdir(root)):
            directory = os.path.join(root, course_dir)
            if not os.path.isdir(directory):
                continue
            for filename in sorted(os.listdir(directory)):
                path = os.path.join(directory, filename)
                if not filename.endswith(".jsonl") or now - os.path.getmtime(path) > max_age_seconds:
                    continue
                try:
                    run = await self._recover_one(path, course_loader, now)
                except Exception:
                    logger.exception(f"Quiz recovery: {path} could not be replayed")
                    continue
                if run is not None:
                    recovered.append(run)
        return recovered

    async def _recover_one(self, path: str, course_loader, now: float) -> QuizRun | None:
        events = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    logger.warning(f"Quiz recovery: unreadable line in {path} skipped")
        if not events or events[0].get("event") != "open" or "quiz_text" not in events[0]:
            return None
        opened = events[0]
        if opened["run_id"] in self.runs:
            return None

        ended_at = next((e["ts"] for e in events if e["event"] in ("finish", "cancel")), None)
        written = any(e["event"] == "write" for e in events)
        if ended_at is not None and written and now - ended_at > FINISHED_CODE_TTL_SECONDS:
            return None  # long over and nothing left to do

        try:
            quiz = parse_quiz(opened["quiz_text"])
        except QuizFormatError as e:
            logger.error(f"Quiz recovery: {path}: the quiz no longer parses: {e}")
            return None

        course_info = {}
        try:
            course_info = await asyncio.to_thread(course_loader, opened["course_id"])
        except Exception as e:
            logger.warning(f"Quiz recovery: course {opened['course_id']} not loaded ({e}); scores cannot be written")

        async with self.lock:
            if self.active_for_course(opened["course_id"]) and ended_at is None:
                logger.warning(f"Quiz recovery: course {opened['course_id']} already has a run, {path} skipped")
                return None
            run = QuizRun(
                run_id=opened["run_id"],
                code=opened["code"],
                course_id=opened["course_id"],
                course_name=opened.get("course_name", opened["course_id"]),
                quiz_id=opened.get("quiz_id", ""),
                quiz=quiz,
                variant_choice=list(opened["variant_choice"]),
                # Only the participants are known; anyone else re-reads the
                # sheets on joining (read_at=0 makes the 60 s rule pass).
                roster=Roster(entries={}, sheets_without_column=list(opened.get("sheets_without_column", [])),
                              read_at=0.0),
                log=RunLog(self.log_dir, opened["course_id"], opened["run_id"], self.clock),
                created_at=opened.get("created_at", opened["ts"]),
                in_room=bool(opened.get("in_room")),
            )
            if opened.get("join_secret"):
                run.join_secret = bytes.fromhex(opened["join_secret"])
            report = None
            for e in events[1:]:
                kind = e.get("event")
                if kind == "join":
                    entry = RosterEntry(login=e["login"], group=e["group"], row=e.get("row", 0), name=e.get("name", ""))
                    run.roster.entries[entry.login.casefold()] = entry
                    run.participants.setdefault(entry.login.casefold(), Participant(entry=entry, joined_at=e["ts"]))
                elif kind == "question":
                    run.state, run.question_index = QUESTION, e["question"]
                    run.asked = e["question"] + 1
                    run.deadline = e["deadline"]
                elif kind == "reveal":
                    run.state = REVEAL
                elif kind == "answer" and e.get("accepted"):
                    participant = run.participants.get(e["login"].casefold())
                    if participant is not None:
                        participant.answers[e["question"]] = e["option"]
                        if e.get("correct"):
                            participant.correct.add(e["question"])
                elif kind == "finish":
                    run.state, run.ended_at = FINISHED, e["ts"]
                elif kind == "cancel":
                    run.state, run.ended_at = CANCELLED, e["ts"]
                elif kind == "write":
                    report = _merge_reports(report, e.get("report") or {})
            run.write_report = report

            self.runs[run.run_id] = run
            self.by_code[run.code] = run.run_id
            self._course_info[run.run_id] = course_info
            run.log.write("recover", state=run.state, question=run.question_index,
                          participants=len(run.participants))
            logger.warning(
                f"Quiz run {run.run_id} ({run.course_id}/{run.quiz_id}) recovered after restart: "
                f"{run.state}, question {run.question_index + 1}, {len(run.participants)} participants"
            )

            if run.state == QUESTION:
                if self.clock() > run.deadline + ANSWER_GRACE_SECONDS:
                    self._expire_locked(run)
                else:
                    i = run.question_index
                    run.reveal_task = asyncio.get_running_loop().create_task(
                        self._reveal_later(run, i, run.deadline + ANSWER_GRACE_SECONDS - self.clock())
                    )
            if run.state == FINISHED and not written and course_info:
                self._start_write(run)
            run.bump()
            return run

    # -- SSE -----------------------------------------------------------------

    async def snapshots(self, run: QuizRun, make: Callable[[float], dict], ping_seconds: float = 15.0):
        """
        Yield the current snapshot at once, then one per change; `None` as a
        keep-alive when nothing changed for `ping_seconds`. Ends after the run
        has ended (for the projector: after the write report is in).
        """
        last = None
        last_content = None
        while True:
            # A deadline nobody acted on yet still flips the state for the viewer.
            if run.state == QUESTION and run.deadline is not None and self.clock() > run.deadline + ANSWER_GRACE_SECONDS:
                await self.reveal_if_due(run)
            waiter = run.changed
            if run.version != last:
                last = run.version
                snapshot = make(self.clock())
                # Another student's answer changes the version but not this
                # student's view: 200 phones x 200 answers would otherwise be
                # 40 000 identical events. `remaining_ms` alone does not count.
                content = {k: v for k, v in snapshot.items() if k not in ("version", "remaining_ms")}
                if content != last_content:
                    last_content = content
                    yield snapshot
                if run.state == CANCELLED or (run.state == FINISHED and not run.writing):
                    return
            try:
                await asyncio.wait_for(waiter.wait(), timeout=ping_seconds)
            except asyncio.TimeoutError:
                yield None


def _merge_reports(old: dict | None, new: dict) -> dict:
    merged = {group: dict(data) for group, data in (old or {}).items()}
    for group, data in new.items():
        before = merged.get(group, {"written": [], "skipped": [], "missing_column": False, "error": None})
        merged[group] = {
            "written": list(before.get("written", [])) + list(data.get("written", [])),
            "skipped": list(before.get("skipped", [])) + list(data.get("skipped", [])),
            "missing_column": data.get("missing_column", False),
            "error": data.get("error"),
        }
    return merged
