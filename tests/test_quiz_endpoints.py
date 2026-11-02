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
        assert response.json() == {"id": "lecture-5", "title": "Квиз 5", "short_name": "К5", "questions": 2,
                                   "max_points": "2", "seconds_per_question": 30}
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
