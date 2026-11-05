"""
Quiz runs and endpoints (docs/QUIZ_PLAN.md §5-§9, §14): state machine, time,
answers, shuffling, roster, writing scores, SSE, sign-in, access.
"""
import asyncio
import base64
import json
import os
import sys
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module
from main import app
from grading.admin_access import AdminIdentity
from grading.course_store import LocalCourseStore
from grading.quiz import QuizSettings, build_quiz_file, parse_quiz
from grading.quiz_runs import (
    ANSWER_GRACE_SECONDS,
    QuizRunError,
    QuizRunManager,
    Roster,
    RosterEntry,
    read_roster,
    write_scores,
)

BANK = """# Квиз 5

### Вопрос 1, 50%
**Вариант А:** Первый?
- a ✓
- b
- c
- d

**Вариант Б:** Первый (Б)?
- a
- b ✓
- c
- d

### Вопрос 2, 50%
Второй?
- a
- b
- c ✓
- d
"""


def make_quiz(seconds=20, max_points=2):
    return parse_quiz(BANK, {"short-name": "К5", "max-points": max_points, "seconds-per-question": seconds})


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def roster_of(*logins, groups=None, read_at=0.0):
    entries = {}
    for i, login in enumerate(logins):
        group = (groups or {}).get(login, "4431")
        entries[login.casefold()] = RosterEntry(login=login, group=group, row=3 + i, name=f"Студент {login}")
    return Roster(entries=entries, sheets_without_column=[], read_at=read_at)


class Recorder:
    """Fake roster reader / score writer."""

    def __init__(self, roster):
        self.roster = roster
        self.reads = 0
        self.writes = []
        self.fail_groups = set()

    def read(self, course_info, short_name):
        self.reads += 1
        return self.roster

    def write(self, course_info, short_name, scores):
        self.writes.append({g: [(e.login, s) for e, s in items] for g, items in scores.items()})
        report = {}
        for group, items in scores.items():
            if group in self.fail_groups:
                report[group] = {"written": [], "skipped": [], "missing_column": False, "error": "quota"}
            else:
                report[group] = {"written": [e.login for e, _ in items], "skipped": [],
                                 "missing_column": False, "error": None}
        return report


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def recorder(clock):
    return Recorder(roster_of("alice", "Bob", "carol", groups={"carol": "4432"}, read_at=clock.now))


@pytest.fixture
def manager(recorder, clock, tmp_path):
    return QuizRunManager(recorder.read, recorder.write, log_dir=str(tmp_path / "logs"), clock=clock)


async def settle():
    """Let tasks created by the manager (writing scores) run."""
    for _ in range(5):
        await asyncio.sleep(0)
    await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestStateMachine:
    async def test_full_run(self, manager, recorder):
        run = await manager.open("os", {"name": "ОС"}, "k5", make_quiz())
        assert run.state == "lobby" and len(run.code) == 6
        await manager.join(run, "alice")
        await manager.start(run)
        assert (run.state, run.question_index) == ("question", 0)
        await manager.reveal_if_due(run, 0, force=True)
        assert run.state == "reveal"
        await manager.next(run)
        assert (run.state, run.question_index) == ("question", 1)
        await manager.reveal_if_due(run, 1, force=True)
        await manager.next(run)
        assert run.state == "finished"
        await settle()
        assert run.writing is False
        assert run.write_report["4431"]["written"] == ["alice"]

    async def test_invalid_transitions(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        with pytest.raises(QuizRunError) as e:
            await manager.next(run)
        assert e.value.code == "BAD_STATE"
        with pytest.raises(QuizRunError):
            await manager.finish(run)          # nothing asked yet
        await manager.start(run)
        with pytest.raises(QuizRunError):
            await manager.start(run)
        with pytest.raises(QuizRunError):
            await manager.next(run)            # still in question

    async def test_second_run_on_course_is_409(self, manager):
        first = await manager.open("os", {}, "k5", make_quiz())
        with pytest.raises(QuizRunError) as e:
            await manager.open("os", {}, "k6", make_quiz())
        assert e.value.status == 409 and e.value.extra["run_id"] == first.run_id
        await manager.open("ml", {}, "k5", make_quiz())  # another course is fine

    async def test_cancel_writes_nothing_and_frees_course(self, manager, recorder):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        await manager.start(run)
        await manager.cancel(run)
        await settle()
        assert run.state == "cancelled" and recorder.writes == []
        with pytest.raises(QuizRunError):
            await manager.cancel(run)
        await manager.open("os", {}, "k5", make_quiz())

    async def test_finish_early(self, manager, recorder):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        await manager.start(run)
        await manager.answer(run, "alice", 0, run.variant(0).correct)
        await manager.finish(run)
        await settle()
        # Question 2 never asked: 0 for it, max not recomputed -> 1 of 2
        assert recorder.writes == [{"4431": [("alice", "1")]}]
        assert run.projector_snapshot(0)["summary"]["asked"] == 1


# ---------------------------------------------------------------------------
# Time and answers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestAnswers:
    @pytest.fixture
    async def started(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz(seconds=20))
        for login in ("alice", "Bob"):
            await manager.join(run, login)
        await manager.start(run)
        return run

    async def test_before_deadline(self, manager, started, clock):
        clock.now += 19
        assert await manager.answer(started, "alice", 0, started.variant(0).correct) is True

    async def test_within_grace(self, manager, started, clock):
        clock.now += 20 + ANSWER_GRACE_SECONDS - 0.1
        await manager.answer(started, "alice", 0, 0)
        assert 0 in started.participants["alice"].answers

    async def test_after_grace_is_too_late_and_reveals(self, manager, started, clock):
        clock.now += 20 + ANSWER_GRACE_SECONDS + 0.1
        with pytest.raises(QuizRunError) as e:
            await manager.answer(started, "alice", 0, 0)
        assert (e.value.code, e.value.status) == ("TOO_LATE", 410)
        assert started.state == "reveal"

    async def test_reveal_by_timer(self, manager, clock):
        run = await manager.open("os", {}, "k5", make_quiz(seconds=5))
        await manager.start(run)
        task = run.reveal_task
        assert task is not None
        # The timer sleeps deadline + grace; run it without waiting 6 real seconds
        task.cancel()
        await manager._reveal_later(run, 0, 0)
        assert run.state == "reveal"

    async def test_reveal_by_timer_ignores_old_question(self, manager, started):
        await manager.reveal_if_due(started, 0, force=True)
        await manager.next(started)
        await manager.reveal_if_due(started, 0, force=True)   # stale timer of question 0
        assert started.state == "question" and started.question_index == 1

    async def test_first_answer_is_final(self, manager, started):
        await manager.answer(started, "alice", 0, 1)
        with pytest.raises(QuizRunError) as e:
            await manager.answer(started, "alice", 0, 0)
        assert (e.value.code, e.value.status) == ("ALREADY_ANSWERED", 409)
        assert started.participants["alice"].answers == {0: 1}

    async def test_wrong_question(self, manager, started):
        with pytest.raises(QuizRunError) as e:
            await manager.answer(started, "alice", 1, 0)
        assert (e.value.code, e.value.status) == ("WRONG_QUESTION", 400)

    async def test_answer_in_lobby_and_reveal(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        with pytest.raises(QuizRunError) as e:
            await manager.answer(run, "alice", 0, 0)
        assert e.value.code == "WRONG_QUESTION"
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        with pytest.raises(QuizRunError) as e:
            await manager.answer(run, "alice", 0, 0)
        assert e.value.code == "TOO_LATE"

    async def test_not_joined_and_bad_option(self, manager, started):
        with pytest.raises(QuizRunError) as e:
            await manager.answer(started, "carol", 0, 0)
        assert e.value.code == "NOT_JOINED"
        with pytest.raises(QuizRunError) as e:
            await manager.answer(started, "alice", 0, 7)
        assert e.value.code == "WRONG_OPTION"

    async def test_log_has_every_answer(self, manager, started, clock, tmp_path):
        clock.now += 3
        await manager.answer(started, "alice", 0, 0)
        clock.now += 30
        with pytest.raises(QuizRunError):
            await manager.answer(started, "Bob", 0, 1)
        lines = [json.loads(l) for l in open(started.log.path, encoding="utf-8")]
        answers = [l for l in lines if l["event"] == "answer"]
        assert [(a["login"], a["accepted"]) for a in answers] == [("alice", True), ("Bob", False)]
        assert answers[0]["received"] == clock.now - 30
        assert lines[0]["event"] == "open" and lines[0]["variants"][0] in ("Вариант А", "Вариант Б")


# ---------------------------------------------------------------------------
# Shuffling and snapshots
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestSnapshots:
    async def test_shuffle_is_stable_per_student_and_differs_between_students(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        assert run.option_order("alice", 0) == run.option_order("ALICE", 0)
        orders = {tuple(run.option_order(f"s{i}", 0)) for i in range(30)}
        assert len(orders) > 1
        assert sorted(run.option_order("alice", 0)) == [0, 1, 2, 3]

    async def test_correct_answer_hidden_until_reveal(self, manager, clock):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        await manager.start(run)
        student = run.student_snapshot("alice", clock.now)
        projector = run.projector_snapshot(clock.now)
        assert "correct" not in student and "correct" not in projector
        assert [o["id"] for o in student["options"]] == run.option_order("alice", 0)
        assert [o["letter"] for o in projector["options"]] == ["A", "B", "C", "D"]
        assert student["remaining_ms"] == 20000

        await manager.answer(run, "alice", 0, run.variant(0).correct)
        await manager.reveal_if_due(run, 0, force=True)
        student = run.student_snapshot("alice", clock.now)
        assert student["correct"] == run.variant(0).correct and student["answered_correctly"] is True
        projector = run.projector_snapshot(clock.now)
        assert projector["distribution"][run.variant(0).correct] == 1

    async def test_same_variant_for_everyone(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.start(run)
        texts = {run.student_snapshot(login, 0)["text"] for login in ("alice", "Bob", "x")}
        assert len(texts) == 1

    async def test_score_in_finished_snapshot(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz(max_points=2))
        await manager.join(run, "alice")
        await manager.join(run, "Bob")
        await manager.start(run)
        await manager.answer(run, "alice", 0, run.variant(0).correct)
        await manager.finish(run)
        assert run.student_snapshot("alice", 0)["score"] == "1"
        assert run.student_snapshot("bob", 0)["score"] == "0"
        assert run.projector_snapshot(0)["summary"]["average"] == "0.5"

    async def test_sse_sequence(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        seen = []

        async def consume():
            async for snap in manager.snapshots(run, lambda now: run.student_snapshot("alice", now), ping_seconds=5):
                seen.append(snap and snap["state"])

        task = asyncio.get_running_loop().create_task(consume())
        await asyncio.sleep(0.01)
        await manager.start(run)
        await asyncio.sleep(0.01)
        await manager.reveal_if_due(run, 0, force=True)
        await asyncio.sleep(0.01)
        await manager.finish(run)
        await asyncio.wait_for(task, 2)
        assert seen[0] == "lobby"
        assert "question" in seen and "reveal" in seen
        assert seen[-1] == "finished"

    async def test_sse_ping(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        gen = manager.snapshots(run, run.projector_snapshot, ping_seconds=0.01)
        assert (await gen.__anext__())["state"] == "lobby"
        assert await gen.__anext__() is None
        await gen.aclose()


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


class FakeWorksheet:
    def __init__(self, title, values):
        self.title = title
        self.values = values
        self.updates = []

    def get_all_values(self):
        return self.values

    def batch_update(self, data, **kwargs):
        self.updates.append((data, kwargs))


class FakeSpreadsheet:
    def __init__(self, *sheets):
        self.sheets = {s.title: s for s in sheets}

    def worksheets(self):
        return list(self.sheets.values())

    def worksheet(self, title):
        return self.sheets[title]


def group_sheet(title, rows, with_column=True):
    header = ["", "", "", "GitHub"]
    names = ["№", "ФИО", "К5" if with_column else "ЛР1", ""]
    return FakeWorksheet(title, [header, names] + rows)


class TestRoster:
    def test_read(self):
        spreadsheet = FakeSpreadsheet(
            FakeWorksheet("График", [["x"]]),
            group_sheet("4431", [["1", "Иванов", "", "Alice"], ["2", "Петров", "", ""]]),
            group_sheet("4432", [["1", "Сидоров", "", "bob"], ["2", "Дубль", "", "ALICE"]], with_column=False),
        )
        roster = read_roster(spreadsheet, "График", 1, "К5", 5.0)
        assert roster.find("alice") == RosterEntry(login="Alice", group="4431", row=3, name="Иванов")
        assert roster.find("BOB").group == "4432"
        assert roster.sheets_without_column == ["4432"]
        assert len(roster.entries) == 2

    @pytest.mark.anyio
    async def test_not_in_course_and_reread_after_60s(self, manager, recorder, clock):
        run = await manager.open("os", {}, "k5", make_quiz())
        reads = recorder.reads
        with pytest.raises(QuizRunError) as e:
            await manager.join(run, "dave")
        assert (e.value.code, e.value.status) == ("NOT_IN_COURSE", 403)
        assert recorder.reads == reads  # read just now - not again

        clock.now += 61
        recorder.roster = roster_of("alice", "dave", read_at=clock.now)
        participant = await manager.join(run, "DAVE")
        assert participant.entry.login == "dave"
        assert recorder.reads == reads + 1

    @pytest.mark.anyio
    async def test_warning_for_sheets_without_column(self, manager, recorder):
        recorder.roster.sheets_without_column = ["4432", "4433"]
        run = await manager.open("os", {}, "k5", make_quiz())
        assert run.summary()["warnings"] == [
            "В листах 4432, 4433 нет столбца «К5»: результаты этих групп не будут записаны"
        ]


# ---------------------------------------------------------------------------
# Writing scores
# ---------------------------------------------------------------------------


class TestWriteScores:
    def test_only_empty_cells_and_report(self):
        sheet = group_sheet("4431", [["1", "Иванов", "", "alice"], ["2", "Петров", "н", "bob"]])
        spreadsheet = FakeSpreadsheet(sheet)
        alice = RosterEntry("alice", "4431", 3, "Иванов")
        bob = RosterEntry("bob", "4431", 4, "Петров")
        report = write_scores(spreadsheet, "К5", {"4431": [(alice, "1.5"), (bob, "0")]}, ",")
        assert report["4431"]["written"] == ["alice"]
        assert report["4431"]["skipped"] == [{"login": "bob", "name": "Петров", "value": "н"}]
        (data, kwargs), = sheet.updates
        assert data == [{"range": "C3", "values": [["1,5"]]}]
        assert kwargs["raw"] is False

    def test_missing_column_and_broken_sheet(self):
        spreadsheet = FakeSpreadsheet(group_sheet("4431", [["1", "И", "", "a"]], with_column=False))
        entry = RosterEntry("a", "4431", 3, "И")
        report = write_scores(spreadsheet, "К5", {"4431": [(entry, "1")], "9999": [(entry, "1")]}, ".")
        assert report["4431"]["missing_column"] is True
        assert report["9999"]["error"]

    @pytest.mark.anyio
    async def test_zero_for_joined_nothing_for_absent(self, manager, recorder):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        await manager.start(run)
        await manager.finish(run)
        await settle()
        assert recorder.writes == [{"4431": [("alice", "0")]}]   # Bob and carol never joined

    @pytest.mark.anyio
    async def test_retry_writes_only_unwritten(self, manager, recorder):
        recorder.fail_groups = {"4432"}
        run = await manager.open("os", {}, "k5", make_quiz())
        for login in ("alice", "carol"):
            await manager.join(run, login)
        await manager.start(run)
        await manager.finish(run)
        await settle()
        assert run.write_report["4432"]["error"] == "quota"

        recorder.fail_groups = set()
        await manager.retry_write(run)
        await settle()
        assert recorder.writes[-1] == {"4432": [("carol", "0")]}
        assert run.write_report["4432"]["error"] is None
        assert run.write_report["4431"]["written"] == ["alice"]


# ---------------------------------------------------------------------------
# HTTP: student sign-in, join, answer; admin quiz files and runs; access
# ---------------------------------------------------------------------------


ALICE_T = AdminIdentity(login="alice-t", role="teacher", via="github")


@pytest.fixture
def env(tmp_path, monkeypatch, recorder, clock):
    courses = tmp_path / "courses"
    courses.mkdir()
    for cid, teachers in (("os", ["alice-t"]), ("ml", ["bob-t"])):
        (courses / f"{cid}.yaml").write_text(
            yaml.dump({"course": {"name": cid.upper(), "github": {"teachers": teachers},
                                  "google": {"spreadsheet": "s"}}}), encoding="utf-8")
    (courses / "index.yaml").write_text(yaml.dump({"courses": [
        {"id": "os", "file": "os.yaml"}, {"id": "ml", "file": "ml.yaml"}]}), encoding="utf-8")
    monkeypatch.setattr(main_module, "COURSE_STORE", LocalCourseStore(str(courses)))
    manager = QuizRunManager(recorder.read, recorder.write, log_dir=str(tmp_path / "logs"), clock=clock)
    monkeypatch.setattr(main_module, "QUIZ_RUNS", manager)
    monkeypatch.setattr(main_module.limiter, "enabled", False)
    monkeypatch.setattr(main_module, "FRONTEND_URL", "http://front")
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "http://public")
    return courses


@pytest.fixture
def client(env):
    with TestClient(app) as c:
        yield c


def as_teacher(client, identity=ALICE_T):
    client.cookies.set("admin_session", main_module._build_admin_session(identity))
    return client


def as_student(client, login):
    client.cookies.set("student_session", main_module._build_student_session(login))
    return client


def upload(client, course="os", name="lecture-5.md", text=BANK, **form):
    data = {"short-name": "К5", "max-points": "2", **form}
    return client.post(f"/admin/courses/{course}/quizzes", files={"file": (name, text.encode(), "text/markdown")},
                       data=data)


class TestQuizFiles:
    def test_upload_list_preview_delete(self, client, env):
        as_teacher(client)
        response = upload(client, **{"seconds-per-question": "30"})
        assert response.status_code == 201
        data = response.json()
        assert data.pop("version")
        assert data == {"id": "lecture-5", "title": "Квиз 5", "short_name": "К5", "questions": 2,
                        "max_points": "2", "seconds_per_question": 30, "reveal_seconds": 10,
                        "variants_per_student": False, "max_variants": 2}
        stored = (env / "quizzes" / "os" / "lecture-5.md").read_text(encoding="utf-8")
        assert stored.startswith("---\ntitle: Квиз 5\nshort-name: К5\n")

        assert [q["id"] for q in client.get("/admin/courses/os/quizzes").json()] == ["lecture-5"]
        preview = client.get("/admin/courses/os/quizzes/lecture-5").json()
        assert preview["items"][0]["variants"][1]["correct"] == 1
        assert preview["items"][0]["points"] == "1"

        assert upload(client).status_code == 409
        assert client.delete("/admin/courses/os/quizzes/lecture-5").status_code == 200
        assert client.get("/admin/courses/os/quizzes").json() == []

    def test_format_errors_listed(self, client):
        as_teacher(client)
        response = upload(client, text="### Вопрос 1\n**Вариант А:** Q?\n- a ✓\n- b ✓\n- c\n")
        assert response.status_code == 400
        assert len(response.json()["errors"]) == 2

    def test_bad_parameters(self, client):
        as_teacher(client)
        assert upload(client, **{"max-points": "0"}).status_code == 400

    def test_front_matter_values_are_defaults(self, client, env):
        as_teacher(client)
        text = build_quiz_file(QuizSettings("Свой", "К9", 3, 15), BANK)
        response = client.post("/admin/courses/os/quizzes",
                               files={"file": ("q.md", text.encode(), "text/markdown")}, data={})
        assert response.status_code == 201
        assert response.json()["short_name"] == "К9" and response.json()["seconds_per_question"] == 15

    @pytest.mark.parametrize("method,path", [
        ("GET", "/admin/courses/ml/quizzes"),
        ("GET", "/admin/courses/ml/quizzes/x"),
        ("DELETE", "/admin/courses/ml/quizzes/x"),
        ("POST", "/admin/courses/ml/quizzes/x/runs"),
    ])
    def test_foreign_course_is_404(self, client, method, path):
        as_teacher(client)
        response = client.request(method, path)
        assert response.status_code == 404 and response.json() == {"detail": "Курс не найден"}


@pytest.fixture
def run_info(client):
    as_teacher(client)
    assert upload(client).status_code == 201
    response = client.post("/admin/courses/os/quizzes/lecture-5/runs")
    assert response.status_code == 201
    return response.json()


class TestRunsHttp:
    def test_open_returns_code_and_link(self, run_info):
        assert run_info["link"] == f"http://public/q/{run_info['code']}"
        assert run_info["state"] == "lobby"

    def test_second_run_is_409_with_run_id(self, client, run_info):
        response = client.post("/admin/courses/os/quizzes/lecture-5/runs")
        assert response.status_code == 409 and response.json()["run_id"] == run_info["run_id"]

    def test_whole_flow(self, client, run_info, recorder):
        code, run_id = run_info["code"], run_info["run_id"]
        teacher_cookie = client.cookies.get("admin_session")
        client.cookies.clear()

        assert client.get(f"/quiz/{code}").json()["logged_in"] is False
        assert client.post(f"/quiz/{code}/join").status_code == 401

        as_student(client, "Alice")
        assert client.post(f"/quiz/{code}/join").json()["group"] == "4431"

        client.cookies.set("admin_session", teacher_cookie)
        assert client.post(f"/admin/quiz-runs/{run_id}/start").json()["state"] == "question"

        info = client.get(f"/quiz/{code}").json()
        assert info["state"] == "question" and "correct" not in info
        option = main_module.QUIZ_RUNS.get(run_id).variant(0).correct
        assert client.post(f"/quiz/{code}/answer", json={"question": 0, "option": option}).json() == {"accepted": True}
        again = client.post(f"/quiz/{code}/answer", json={"question": 0, "option": option})
        assert again.status_code == 409 and again.json()["detail"] == "ALREADY_ANSWERED"

        assert client.post(f"/admin/quiz-runs/{run_id}/finish").json()["state"] == "finished"
        assert client.get(f"/quiz/{code}").json()["score"] == "1"

        # SSE of a finished run: the final snapshot, then the stream ends
        response = client.get(f"/admin/quiz-runs/{run_id}/events")
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events[-1]["state"] == "finished"

    def test_not_in_course(self, client, run_info):
        client.cookies.clear()
        as_student(client, "stranger")
        response = client.post(f"/quiz/{run_info['code']}/join")
        assert response.status_code == 403 and response.json()["detail"] == "NOT_IN_COURSE"

    def test_unknown_code(self, client):
        assert client.get("/quiz/zzzzzz").status_code == 404
        assert client.get("/quiz/bad").json()["detail"] == "Квиз не найден или уже завершён"

    def test_foreign_run_is_404(self, client, run_info):
        as_teacher(client, AdminIdentity(login="bob-t", role="teacher", via="github"))
        for method, suffix in (("GET", ""), ("POST", "/start"), ("POST", "/cancel"), ("GET", "/events")):
            response = client.request(method, f"/admin/quiz-runs/{run_info['run_id']}{suffix}")
            assert response.status_code == 404
        unknown = client.get("/admin/quiz-runs/nope")
        assert unknown.status_code == 404


class TestStudentSignIn:
    def test_start_puts_student_purpose_and_code_in_state(self, client, monkeypatch):
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", "id")
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", "secret")
        response = client.get("/quiz/login/start", params={"code": "abc234"}, follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        assert main_module._parse_oauth_state(state) == ("student", {"code": "abc234"})

    def test_callback_sets_long_session(self, client, monkeypatch):
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", "id")
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", "secret")
        monkeypatch.setattr(main_module, "_exchange_code_for_username", lambda code, uri: "Alice")
        state = main_module._build_oauth_state("student", code="abc234")
        response = client.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert response.headers["location"] == "http://front/q/abc234"
        cookie = response.headers["set-cookie"]
        assert "student_session=" in cookie and f"Max-Age={main_module.STUDENT_SESSION_MAX_AGE}" in cookie
        value = response.cookies.get("student_session").strip('"')
        request = type("R", (), {"cookies": {"student_session": value}})()
        assert main_module.student_login(request) == "Alice"

    def test_callback_failure(self, client, monkeypatch):
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_ID", "id")
        monkeypatch.setattr(main_module, "GITHUB_OAUTH_CLIENT_SECRET", "secret")
        monkeypatch.setattr(main_module, "_exchange_code_for_username", lambda code, uri: None)
        state = main_module._build_oauth_state("student", code="abc234")
        response = client.get("/join/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert response.headers["location"] == "http://front/q/abc234?error=oauth_failed"

    def test_rate_limit_key_is_the_login(self):
        def request_with(cookies):
            r = type("R", (), {})()
            r.cookies = cookies
            r.client = type("C", (), {"host": "10.0.0.1"})()
            r.headers = {}
            return r

        same_nat = [request_with({"student_session": main_module._build_student_session(l)}) for l in ("a", "B")]
        assert [main_module.student_rate_key(r) for r in same_nat] == ["student:a", "student:b"]
        assert main_module.student_rate_key(request_with({})) == "10.0.0.1"


@pytest.mark.anyio
async def test_sse_skips_unchanged_student_snapshots(manager):
    """Another student's answer must not send this student an identical snapshot."""
    run = await manager.open("os", {}, "k5", make_quiz())
    for login in ("alice", "Bob"):
        await manager.join(run, login)
    await manager.start(run)
    seen = []

    async def consume():
        async for snap in manager.snapshots(run, lambda now: run.student_snapshot("alice", now), ping_seconds=5):
            seen.append(snap)

    task = asyncio.get_running_loop().create_task(consume())
    await asyncio.sleep(0.01)
    await manager.answer(run, "Bob", 0, 0)      # alice's view unchanged
    await asyncio.sleep(0.01)
    assert len(seen) == 1
    await manager.answer(run, "alice", 0, 0)    # alice's own answer - sent
    await asyncio.sleep(0.01)
    assert len(seen) == 2 and seen[-1]["answer"] == 0
    await manager.cancel(run)
    await asyncio.wait_for(task, 2)


class TestQuizColumn:
    """Attendance quizzes are named by the lecture date; row 1 holds lab deadlines (dates too)."""

    def sheet(self):
        return FakeWorksheet("4431", [
            ["", "", "12.11.2026", "Посещаемость лекций", "", "GitHub"],
            ["№", "ФИО", "ЛР1", "05.11.2026", "12.11.2026", ""],
            ["1", "Иванов", "", "1", "", "alice"],
        ])

    def test_only_second_row_counts(self):
        from grading.quiz_runs import quiz_column
        values = self.sheet().values
        assert quiz_column(values, "12.11.2026") == 5      # not 3 - the lab deadline in row 1
        assert quiz_column(values, " 05.11.2026 ") == 4
        assert quiz_column(values, "Иванов") is None       # student rows are not headers
        assert quiz_column(values, "19.11.2026") is None

    def test_scores_go_to_the_lecture_column(self):
        sheet = self.sheet()
        entry = RosterEntry("alice", "4431", 3, "Иванов")
        report = write_scores(FakeSpreadsheet(sheet), "12.11.2026", {"4431": [(entry, "0.5")]}, ",")
        assert report["4431"]["written"] == ["alice"]
        assert sheet.updates[0][0] == [{"range": "E3", "values": [["0,5"]]}]



# ---------------------------------------------------------------------------
# Tests (контрольные): a variant per student
# ---------------------------------------------------------------------------

BANK3 = """### Вопрос 1, 50%
**Вариант А:** Первый А?
- a ✓
- b
- c
- d

**Вариант Б:** Первый Б?
- a
- b ✓
- c
- d

**Вариант В:** Первый В?
- a
- b
- c ✓
- d

### Вопрос 2, 50%
Общий второй?
- a
- b
- c
- d ✓
"""


def make_test_quiz(per_student=True):
    return parse_quiz(BANK3, {"title": "КР1", "short-name": "КР1", "max-points": 10,
                              "variants-per-student": "true" if per_student else ""})


class TestVariantSetting:
    def test_front_matter_round_trip(self):
        quiz = make_test_quiz()
        assert quiz.settings.variants_per_student is True
        text = build_quiz_file(quiz.settings, quiz.body)
        assert "variants-per-student: true" in text
        assert parse_quiz(text).settings.variants_per_student is True

    def test_off_by_default_and_not_written(self):
        quiz = make_test_quiz(per_student=False)
        assert quiz.settings.variants_per_student is False
        assert "variants-per-student" not in build_quiz_file(quiz.settings, quiz.body)


@pytest.mark.anyio
class TestVariantsPerStudent:
    @pytest.fixture
    async def run(self, manager, recorder):
        recorder.roster = roster_of(*[f"s{i}" for i in range(30)], read_at=0)
        run = await manager.open("os", {}, "kr1", make_test_quiz())
        for i in range(30):
            await manager.join(run, f"s{i}")
        await manager.start(run)
        return run

    async def test_stable_and_different(self, run):
        indices = [run.variant_index(0, f"s{i}") for i in range(30)]
        assert indices == [run.variant_index(0, f"S{i}") for i in range(30)]   # same after reconnect
        assert len(set(indices)) == 3                                          # neighbours differ
        assert {run.variant_index(1, f"s{i}") for i in range(30)} == {0}       # one variant - shared

    async def test_student_sees_own_variant(self, run):
        for i in range(30):
            login = f"s{i}"
            snap = run.student_snapshot(login, 0)
            assert snap["text"] == run.variant(0, login).text
            assert "correct" not in snap

    async def test_answer_checked_against_own_variant(self, manager, run):
        results = {}
        for i in range(30):
            login = f"s{i}"
            results[login] = await manager.answer(run, login, 0, run.variant(0, login).correct)
        assert all(results.values())
        await manager.reveal_if_due(run, 0, force=True)
        for i in range(30):
            assert run.student_snapshot(f"s{i}", 0)["answered_correctly"] is True

    async def test_projector_shows_no_text_and_stats_by_variant(self, manager, run):
        snap = run.projector_snapshot(0)
        assert snap["variants_per_student"] is True
        assert "text" not in snap and "options" not in snap
        assert snap["label"] == "Вопрос 1"
        await manager.answer(run, "s0", 0, run.variant(0, "s0").correct)
        await manager.reveal_if_due(run, 0, force=True)
        by_variant = run.projector_snapshot(0)["by_variant"]
        assert [v["label"] for v in by_variant] == ["Вариант А", "Вариант Б", "Вариант В"]
        assert sum(v["answered"] for v in by_variant) == 1
        assert sum(v["correct"] for v in by_variant) == 1
        assert "correct" not in run.projector_snapshot(0)

    async def test_shared_question_still_shown_on_projector(self, manager, run):
        await manager.reveal_if_due(run, 0, force=True)
        await manager.next(run)
        snap = run.projector_snapshot(0)
        assert snap["text"] == "Общий второй?"

    async def test_log_records_variant(self, manager, run):
        await manager.answer(run, "s1", 0, 0)
        lines = [json.loads(l) for l in open(run.log.path, encoding="utf-8")]
        answer = [l for l in lines if l["event"] == "answer"][0]
        assert answer["variant"] == run.variant(0, "s1").label
        assert lines[0]["variants"] == "per-student"


def test_upload_with_variants_per_student(client):
    as_teacher(client)
    response = upload(client, name="kr1.md", text=BANK3, title="КР1", **{"variants-per-student": "on"})
    assert response.status_code == 201
    assert response.json()["variants_per_student"] is True
    assert response.json()["max_variants"] == 3


# ---------------------------------------------------------------------------
# Recovery after a backend restart: runs rebuilt from the answer log
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestRecovery:
    @pytest.fixture
    def restart(self, recorder, clock, tmp_path):
        """A new manager on the same log directory - what a restarted backend gets."""
        def make():
            return QuizRunManager(recorder.read, recorder.write, log_dir=str(tmp_path / "logs"), clock=clock)
        return make

    async def course(self, course_id):
        return {"name": course_id}

    def loader(self, course_id):
        return {"name": course_id.upper(), "google": {"spreadsheet": "s"}}

    async def test_continues_mid_question(self, manager, restart, clock):
        run = await manager.open("os", {"name": "ОС"}, "k5", make_quiz())
        for login in ("alice", "Bob"):
            await manager.join(run, login)
        await manager.start(run)
        clock.now += 3
        await manager.answer(run, "alice", 0, run.variant(0).correct)

        new = restart()
        (recovered,) = await new.recover(self.loader)
        assert (recovered.run_id, recovered.code) == (run.run_id, run.code)
        assert new.by_student_code(run.code) is recovered
        assert (recovered.state, recovered.question_index) == ("question", 0)
        assert recovered.deadline == run.deadline
        assert recovered.participants["alice"].answers == {0: run.variant(0).correct}
        assert recovered.participants["alice"].correct == {0}
        assert recovered.variant(0).text == run.variant(0).text

        # The quiz goes on: Bob answers, the first answer of alice stays final
        await new.answer(recovered, "Bob", 0, 1)
        with pytest.raises(QuizRunError) as e:
            await new.answer(recovered, "alice", 0, 1)
        assert e.value.code == "ALREADY_ANSWERED"
        await new.reveal_if_due(recovered, 0, force=True)
        await new.next(recovered)
        assert recovered.question_index == 1

    async def test_deadline_passed_during_restart_reveals(self, manager, restart, clock):
        run = await manager.open("os", {}, "k5", make_quiz(seconds=10))
        await manager.start(run)
        clock.now += 60
        (recovered,) = await restart().recover(self.loader)
        assert recovered.state == "reveal"

    async def test_finished_but_not_written_is_written(self, manager, restart, recorder, clock):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        await manager.start(run)
        await manager.answer(run, "alice", 0, run.variant(0).correct)
        # finish logged, but the process dies before the write
        async with manager.lock:
            manager._cancel_timer(run)
            run.state = "finished"
            run.ended_at = clock.now
            run.log.write("finish", asked=1, scores={})
        assert recorder.writes == []

        new = restart()
        (recovered,) = await new.recover(self.loader)
        await settle()
        assert recorder.writes == [{"4431": [("alice", "1")]}]
        assert recovered.write_report["4431"]["written"] == ["alice"]

    async def test_new_joins_reread_the_roster(self, manager, restart, recorder):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        new = restart()
        (recovered,) = await new.recover(self.loader)
        reads = recorder.reads
        await new.join(recovered, "carol")      # was not a participant before the restart
        assert recorder.reads == reads + 1

    async def test_per_student_variants_survive(self, manager, restart, recorder):
        recorder.roster = roster_of(*[f"s{i}" for i in range(10)], read_at=0)
        run = await manager.open("os", {}, "kr1", make_test_quiz())
        await manager.start(run)
        (recovered,) = await restart().recover(self.loader)
        assert [recovered.variant_index(0, f"s{i}") for i in range(10)] == \
               [run.variant_index(0, f"s{i}") for i in range(10)]

    async def test_quiz_text_is_taken_from_the_log(self, manager, restart):
        run = await manager.open("os", {}, "k5", make_quiz())
        (recovered,) = await restart().recover(self.loader)
        assert recovered.quiz.settings == run.quiz.settings
        assert [q.label for q in recovered.quiz.questions] == [q.label for q in run.quiz.questions]

    async def test_ended_runs(self, manager, restart, clock):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        await manager.start(run)
        await manager.finish(run)
        await settle()
        # Within the hour: back read-only, the score is still shown
        (recovered,) = await restart().recover(self.loader)
        assert recovered.state == "finished"
        assert recovered.student_snapshot("alice", clock.now)["score"] == "0"
        # Long over and written: not recovered at all
        clock.now += 2 * 3600
        assert await restart().recover(self.loader) == []

    async def test_cancelled_run_does_not_block_course(self, manager, restart):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.cancel(run)
        new = restart()
        await new.recover(self.loader)
        await new.open("os", {}, "k5", make_quiz())     # no 409

    async def test_broken_line_skipped_and_no_duplicates(self, manager, restart):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        with open(run.log.path, "a", encoding="utf-8") as f:
            f.write("{not json\n")
        new = restart()
        (recovered,) = await new.recover(self.loader)
        assert "alice" in recovered.participants
        assert await new.recover(self.loader) == []     # already there


# ---------------------------------------------------------------------------
# "In the room only": question on the projector, changing QR, early join only
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestInRoom:
    @pytest.fixture
    async def run(self, manager):
        return await manager.open("os", {}, "k5", make_quiz(), in_room=True)

    async def test_entry_token_lives_15_to_30_seconds(self, run, clock):
        clock.now = 1_000_005.0          # 5 s into a 15 s slot
        token = run.current_entry_token(clock.now)
        assert run.check_entry_token(token, clock.now)
        assert run.check_entry_token(token.upper(), clock.now)
        clock.now += 15                  # next slot - still valid
        assert run.check_entry_token(token, clock.now)
        clock.now += 15                  # two slots later - expired
        assert not run.check_entry_token(token, clock.now)
        assert not run.check_entry_token("", clock.now)
        assert not run.check_entry_token("aaaaaaaa", clock.now)

    async def test_join_needs_entry_and_closes_after_first_question(self, manager, run):
        with pytest.raises(QuizRunError) as e:
            await manager.join(run, "alice", entry_ok=False)
        assert (e.value.code, e.value.status) == ("ENTRY_REQUIRED", 403)
        await manager.join(run, "alice", entry_ok=True)
        await manager.start(run)
        await manager.join(run, "Bob", entry_ok=True)          # still question 1 - allowed
        await manager.reveal_if_due(run, 0, force=True)
        with pytest.raises(QuizRunError) as e:
            await manager.join(run, "carol", entry_ok=True)
        assert e.value.code == "JOIN_CLOSED"
        # Already joined students reconnect without a token
        assert (await manager.join(run, "alice", entry_ok=False)).entry.login == "alice"

    async def test_question_only_on_projector_answers_shuffled_on_phone(self, manager, run):
        await manager.join(run, "alice")
        await manager.start(run)
        student = run.student_snapshot("alice", 0)
        assert "text" not in student and student["in_room"] is True
        assert [o["id"] for o in student["options"]] == run.option_order("alice", 0)
        assert student["options"][0]["text"]                     # answers are on the phone
        projector = run.projector_snapshot(0)
        assert projector["text"] == run.variant(0).text
        assert "options" not in projector                        # not on the projector...
        await manager.reveal_if_due(run, 0, force=True)
        projector = run.projector_snapshot(0)
        assert len(projector["options"]) == 4 and "distribution" in projector   # ...until reveal

    async def test_off_by_default(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice", entry_ok=False)
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        await manager.join(run, "Bob", entry_ok=False)           # late join is fine
        assert "text" in run.student_snapshot("alice", 0)

    async def test_incompatible_with_variants_per_student(self, manager):
        with pytest.raises(QuizRunError) as e:
            await manager.open("os", {}, "kr1", make_test_quiz(), in_room=True)
        assert (e.value.code, e.value.status) == ("IN_ROOM_WITH_VARIANTS", 400)

    async def test_survives_restart(self, manager, run, recorder, clock, tmp_path):
        token = run.current_entry_token(clock.now)
        new = QuizRunManager(recorder.read, recorder.write, log_dir=str(tmp_path / "logs"), clock=clock)
        (recovered,) = await new.recover(lambda c: {})
        assert recovered.in_room is True
        assert recovered.check_entry_token(token, clock.now)


class TestInRoomHttp:
    @pytest.fixture
    def opened(self, client):
        as_teacher(client)
        assert upload(client).status_code == 201
        response = client.post("/admin/courses/os/quizzes/lecture-5/runs", json={"in_room": True})
        assert response.status_code == 201
        return response.json()

    def test_link_carries_current_token(self, opened):
        run = main_module.QUIZ_RUNS.get(opened["run_id"])
        assert opened["in_room"] is True
        assert opened["link"] == f"http://public/q/{run.code}?t={run.current_entry_token(main_module.QUIZ_RUNS.clock())}"

    def test_pass_from_qr_survives_sign_in(self, client, opened):
        run = main_module.QUIZ_RUNS.get(opened["run_id"])
        client.cookies.clear()
        # Opened without the token (link from a chat): no entry
        info = client.get(f"/quiz/{run.code}").json()
        assert info["entry_ok"] is False and info["in_room"] is True
        # Scanned from the projector: a pass cookie, then sign-in, then join
        token = run.current_entry_token(main_module.QUIZ_RUNS.clock())
        assert client.get(f"/quiz/{run.code}", params={"t": token}).json()["entry_ok"] is True
        as_student(client, "alice")
        assert client.post(f"/quiz/{run.code}/join").status_code == 200

    def test_join_without_pass_is_refused(self, client, opened):
        run = main_module.QUIZ_RUNS.get(opened["run_id"])
        client.cookies.clear()
        as_student(client, "alice")
        response = client.post(f"/quiz/{run.code}/join")
        assert response.status_code == 403 and response.json()["detail"] == "ENTRY_REQUIRED"

    def test_pass_of_another_run_does_not_count(self, client, opened):
        run = main_module.QUIZ_RUNS.get(opened["run_id"])
        client.cookies.clear()
        client.cookies.set("quiz_pass", main_module.signer.sign(b"other-run").decode())
        as_student(client, "alice")
        assert client.post(f"/quiz/{run.code}/join").status_code == 403

    def test_variants_quiz_refused(self, client):
        as_teacher(client)
        assert upload(client, name="kr1.md", text=BANK3, title="КР1",
                      **{"variants-per-student": "on"}).status_code == 201
        response = client.post("/admin/courses/os/quizzes/kr1/runs", json={"in_room": True})
        assert response.status_code == 400 and response.json()["detail"] == "IN_ROOM_WITH_VARIANTS"

    def test_open_without_body_still_works(self, client):
        as_teacher(client)
        upload(client)
        response = client.post("/admin/courses/os/quizzes/lecture-5/runs")
        assert response.status_code == 201 and response.json()["in_room"] is False
        assert "?t=" not in response.json()["link"]


class TestDateColumns:
    """The API returns a date header as displayed: `03.11` when the format hides the year."""

    VALUES = [
        ["", "", "", "Посещаемость лекций", "", "", "GitHub"],
        ["", "Ф.И.О.", "ЛР0", "03.11", "10.11", "1.12", ""],
    ]

    @pytest.mark.parametrize("name,col", [
        ("03.11.2026", 4), ("03.11", 4), ("3.11.2026", 4), ("2026-11-03", 4), ("03/11/26", 4),
        ("01.12.2026", 6), ("10.11", 5),
        ("04.11.2026", None), ("ЛР0", 3),
    ])
    def test_match(self, name, col):
        from grading.quiz_runs import quiz_column
        assert quiz_column(self.VALUES, name) == col

    def test_exact_text_wins(self):
        from grading.quiz_runs import quiz_column
        values = [[], ["03.11", "03.11.2026"]]
        assert quiz_column(values, "03.11.2026") == 2

    def test_years_must_agree_when_both_given(self):
        from grading.quiz_runs import quiz_column
        assert quiz_column([[], ["03.11.2025"]], "03.11.2026") is None

    def test_parse(self):
        from grading.quiz_runs import parse_date_label
        assert parse_date_label("03.11.2026") == (3, 11, 2026)
        assert parse_date_label("3.11") == (3, 11, None)
        assert parse_date_label("ЛР1") is None
        assert parse_date_label("31.13") is None


# ---------------------------------------------------------------------------
# Auto-advance after reveal, ending a question early, pause; editing a quiz
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestPacing:
    async def test_reveal_seconds_setting(self):
        assert make_quiz().settings.reveal_seconds == 10
        quiz = parse_quiz(BANK, {"short-name": "К5", "max-points": 1, "reveal-seconds": 0})
        assert quiz.settings.reveal_seconds == 0
        text = build_quiz_file(quiz.settings, quiz.body)
        assert "reveal-seconds: 0" in text
        assert "reveal-seconds" not in build_quiz_file(make_quiz().settings, BANK)   # default not written
        with pytest.raises(Exception):
            parse_quiz(BANK, {"short-name": "К5", "max-points": 1, "reveal-seconds": 500})

    async def test_auto_advance_after_reveal(self, manager, clock):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        assert run.next_at == clock.now + 10
        assert run.projector_snapshot(clock.now)["next_in_ms"] == 10000
        task = run.advance_task
        task.cancel()                                  # don't wait 10 real seconds
        await manager._advance_later(run, 0, 0)
        assert (run.state, run.question_index) == ("question", 1)
        assert run.projector_snapshot(clock.now)["next_in_ms"] is None

    async def test_auto_advance_after_last_question_finishes(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        await manager.next(run)
        await manager.reveal_if_due(run, 1, force=True)
        run.advance_task.cancel()
        await manager._advance_later(run, 1, 0)
        assert run.state == "finished"

    async def test_stale_advance_does_nothing(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        await manager.next(run)                        # teacher pressed "Дальше" first
        await manager._advance_later(run, 0, 0)        # the old timer fires
        assert (run.state, run.question_index) == ("question", 1)

    async def test_zero_means_manual(self, manager):
        quiz = parse_quiz(BANK, {"short-name": "К5", "max-points": 1, "reveal-seconds": 0})
        run = await manager.open("os", {}, "k5", quiz)
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        assert run.next_at is None and run.advance_task is None

    async def test_end_question_early(self, manager, clock):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.join(run, "alice")
        await manager.join(run, "Bob")
        await manager.start(run)
        await manager.answer(run, "alice", 0, run.variant(0).correct)
        clock.now += 5                                  # well before the 20 s deadline
        await manager.end_question(run)
        assert run.state == "reveal"
        with pytest.raises(QuizRunError) as e:
            await manager.answer(run, "Bob", 0, 0)
        assert e.value.code == "TOO_LATE"
        with pytest.raises(QuizRunError):
            await manager.end_question(run)             # not in a question any more

    async def test_hold(self, manager):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.start(run)
        with pytest.raises(QuizRunError):
            await manager.hold(run)                     # nothing to hold during a question
        await manager.reveal_if_due(run, 0, force=True)
        await manager.hold(run)
        assert run.next_at is None
        await manager._advance_later(run, 0, 0)         # a timer that slipped through does nothing
        assert run.state == "reveal"
        await manager.next(run)
        assert run.question_index == 1

    async def test_recovered_reveal_advances(self, manager, recorder, clock, tmp_path):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        run.advance_task.cancel()
        clock.now += 60                                 # restarted long after next_at
        new = QuizRunManager(recorder.read, recorder.write, log_dir=str(tmp_path / "logs"), clock=clock)
        (recovered,) = await new.recover(lambda c: {})
        await asyncio.sleep(0.01)
        assert (recovered.state, recovered.question_index) == ("question", 1)

    async def test_recovered_hold_stays(self, manager, recorder, clock, tmp_path):
        run = await manager.open("os", {}, "k5", make_quiz())
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        await manager.hold(run)
        clock.now += 60
        new = QuizRunManager(recorder.read, recorder.write, log_dir=str(tmp_path / "logs"), clock=clock)
        (recovered,) = await new.recover(lambda c: {})
        await asyncio.sleep(0.01)
        assert recovered.state == "reveal" and recovered.next_at is None


class TestPacingHttp:
    def test_end_question_and_hold(self, client):
        as_teacher(client)
        upload(client)
        run_id = client.post("/admin/courses/os/quizzes/lecture-5/runs").json()["run_id"]
        client.post(f"/admin/quiz-runs/{run_id}/start")
        snap = client.post(f"/admin/quiz-runs/{run_id}/end-question").json()
        assert snap["state"] == "reveal" and snap["next_in_ms"] == 10000
        snap = client.post(f"/admin/quiz-runs/{run_id}/hold").json()
        assert snap["next_in_ms"] is None
        assert client.post(f"/admin/quiz-runs/{run_id}/hold").status_code == 200   # idempotent in reveal


class TestEditQuiz:
    def form(self, version, **changes):
        return {"title": "Квиз 5", "short-name": "К5", "max-points": "2", "seconds-per-question": 20,
                "reveal-seconds": 10, "variants-per-student": False, "version": version, **changes}

    def test_change_column_keeps_questions(self, client, env):
        as_teacher(client)
        version = upload(client).json()["version"]
        response = client.put("/admin/courses/os/quizzes/lecture-5", json=self.form(version, **{"short-name": "03.11"}))
        assert response.status_code == 200
        assert response.json()["short_name"] == "03.11" and response.json()["version"] != version
        stored = (env / "quizzes" / "os" / "lecture-5.md").read_text(encoding="utf-8")
        assert "short-name: '03.11'" in stored
        assert "### Вопрос 1, 50%" in stored and stored.count("---\n") == 2
        assert client.get("/admin/courses/os/quizzes").json()[0]["short_name"] == "03.11"

    def test_stale_version_is_409(self, client):
        as_teacher(client)
        version = upload(client).json()["version"]
        assert client.put("/admin/courses/os/quizzes/lecture-5", json=self.form(version, title="A")).status_code == 200
        response = client.put("/admin/courses/os/quizzes/lecture-5", json=self.form(version, title="B"))
        assert response.status_code == 409

    def test_invalid_values(self, client):
        as_teacher(client)
        version = upload(client).json()["version"]
        response = client.put("/admin/courses/os/quizzes/lecture-5", json=self.form(version, **{"max-points": "0"}))
        assert response.status_code == 400 and response.json()["errors"]

    def test_foreign_course(self, client):
        as_teacher(client)
        response = client.put("/admin/courses/ml/quizzes/x", json=self.form("v"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Leaderboard: points first, then less time on correct answers; logins only
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestLeaderboard:
    @pytest.fixture
    async def run(self, manager, recorder):
        recorder.roster = roster_of("alice", "bob", "carol", "dave", read_at=0)
        run = await manager.open("os", {"name": "ОС"}, "k5", make_quiz())
        for login in ("alice", "bob", "carol", "dave"):
            await manager.join(run, login)
        return run

    async def answer_at(self, manager, run, clock, login, q, correct, after):
        start = run.deadline - run.quiz.settings.seconds_per_question
        clock.now = start + after
        option = run.variant(q, login).correct if correct else (run.variant(q, login).correct + 1) % 4
        await manager.answer(run, login, q, option)

    async def test_points_first_then_speed(self, manager, run, clock):
        await manager.start(run)
        await self.answer_at(manager, run, clock, "alice", 0, True, 8)
        await self.answer_at(manager, run, clock, "bob", 0, True, 3)      # faster than alice
        await self.answer_at(manager, run, clock, "carol", 0, False, 1)   # fastest but wrong
        await manager.reveal_if_due(run, 0, force=True)
        board = run.projector_snapshot(clock.now)["leaderboard"]
        # carol and dave have no points - not on the board (their places on the phone stay)
        assert [(r["place"], r["name"]) for r in board] == [(1, "bob"), (2, "alice")]
        assert board[0]["points"] == "1" and board[0]["seconds"] == 3.0
        assert run.projector_snapshot(clock.now)["fastest"] == {"name": "bob", "seconds": 3.0}

        await manager.next(run)
        await self.answer_at(manager, run, clock, "alice", 1, True, 2)
        await manager.finish(run)
        final = run.projector_snapshot(clock.now)["leaderboard"]
        # alice: 2 correct beats bob's 1 correct however fast he was
        assert [r["name"] for r in final[:2]] == ["alice", "bob"]
        assert final[0]["points"] == "2" and final[0]["seconds"] == 10.0

    async def test_places_on_phones(self, manager, run, clock):
        await manager.start(run)
        await self.answer_at(manager, run, clock, "bob", 0, True, 3)
        snap = run.student_snapshot("bob", clock.now)
        assert "place" not in snap                     # not during the question
        await manager.reveal_if_due(run, 0, force=True)
        assert (run.student_snapshot("bob", clock.now)["place"], run.student_snapshot("bob", clock.now)["of"]) == (1, 4)
        assert run.student_snapshot("dave", clock.now)["place"] == 2

    async def test_hidden_keeps_place_without_login(self, manager, run, clock):
        await manager.set_hidden(run, "bob", True)
        await manager.start(run)
        await self.answer_at(manager, run, clock, "bob", 0, True, 3)
        await manager.reveal_if_due(run, 0, force=True)
        snap = run.projector_snapshot(clock.now)
        assert snap["leaderboard"][0] == {"place": 1, "name": None, "points": "1", "seconds": 3.0}
        assert snap["fastest"]["name"] is None
        assert "bob" not in json.dumps(snap)
        assert run.student_snapshot("bob", clock.now)["hidden"] is True

    async def test_never_names_from_the_sheet(self, manager, run, clock):
        await manager.start(run)
        await self.answer_at(manager, run, clock, "alice", 0, True, 3)
        await manager.reveal_if_due(run, 0, force=True)
        assert "Студент" not in json.dumps(run.projector_snapshot(clock.now), ensure_ascii=False)

    async def test_lobby_names_newest_first(self, manager, run):
        assert run.projector_snapshot(0)["joined_names"][:2] == ["dave", "carol"]

    async def test_leaderboard_off(self, manager, recorder, clock):
        run = await manager.open("ml", {}, "k5", make_quiz(), leaderboard=False)
        await manager.join(run, "alice")
        await manager.start(run)
        await manager.reveal_if_due(run, 0, force=True)
        assert "leaderboard" not in run.projector_snapshot(clock.now)
        assert "place" not in run.student_snapshot("alice", clock.now)

    async def test_hidden_on_join_and_recovery(self, manager, recorder, clock, tmp_path):
        recorder.roster = roster_of("alice", "eve", read_at=0)
        run = await manager.open("ml", {}, "k5", make_quiz())
        await manager.join(run, "eve", hidden=True)
        await manager.join(run, "alice")
        await manager.start(run)
        clock.now += 4
        await manager.answer(run, "alice", 0, run.variant(0).correct)
        new = QuizRunManager(recorder.read, recorder.write, log_dir=str(tmp_path / "logs"), clock=clock)
        recovered = {r.course_id: r for r in await new.recover(lambda c: {})}["ml"]
        assert recovered.participants["eve"].hidden is True
        assert recovered.participants["alice"].answer_seconds[0] == pytest.approx(4.0)
        assert recovered.leaderboard is True


class TestLeaderboardHttp:
    def test_join_hidden_and_toggle(self, client):
        as_teacher(client)
        upload(client)
        info = client.post("/admin/courses/os/quizzes/lecture-5/runs", json={"leaderboard": True}).json()
        run = main_module.QUIZ_RUNS.get(info["run_id"])
        client.cookies.clear()
        as_student(client, "alice")
        assert client.post(f"/quiz/{run.code}/join", json={"hidden": True}).status_code == 200
        assert run.participants["alice"].hidden is True
        assert client.post(f"/quiz/{run.code}/visibility", json={"hidden": False}).json() == {"hidden": False}
        assert run.participants["alice"].hidden is False
        assert client.post(f"/quiz/{run.code}/join").status_code == 200         # no body is fine too

    def test_open_without_leaderboard(self, client):
        as_teacher(client)
        upload(client)
        info = client.post("/admin/courses/os/quizzes/lecture-5/runs", json={"leaderboard": False}).json()
        assert main_module.QUIZ_RUNS.get(info["run_id"]).leaderboard is False
