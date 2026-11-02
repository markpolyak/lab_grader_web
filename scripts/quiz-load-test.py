#!/usr/bin/env python3
"""
Load test of a quiz run (docs/QUIZ_PLAN.md §11): N fake students join, keep an
SSE connection open and answer every question after a random delay.

Students are signed in by forging `student_session` with the test stand's
SECRET_KEY - so run it ONLY against a test stand, whose roster (the course
spreadsheet) contains the logins `{prefix}0 .. {prefix}{N-1}`.

The quiz is either driven by the teacher on the projector, or by this script
(`--admin-login/--admin-password`, the emergency sign-in): start, wait for the
reveal, next, ... finish.

    python scripts/quiz-load-test.py --base-url http://127.0.0.1:8000 --code abc234 \\
        --participants 200 --drive --admin-login admin --admin-password ...

Report: how many answers were accepted (all of them must be, when sent before
the deadline) and how far apart the same event reached different students.
"""
import argparse
import asyncio
import base64
import json
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict

import httpx
from itsdangerous import TimestampSigner


def student_cookie(secret_key: str, login: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"login": login}).encode()).decode()
    return TimestampSigner(secret_key).sign(payload.encode()).decode()


class Stats:
    def __init__(self):
        self.joined = 0
        self.join_errors = Counter()
        self.answers = Counter()          # accepted / error code
        self.late_sends = 0               # answers this script itself sent after the deadline
        self.received = defaultdict(list)  # snapshot version -> [monotonic time received]
        self.versions_state = {}
        self.final_scores = []


async def read_events(response):
    """Parse `data:` lines of an SSE stream into snapshots."""
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            event, buffer = buffer.split("\n\n", 1)
            for line in event.splitlines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])


async def participant(i, args, stats, ready):
    login = f"{args.prefix}{i}"
    cookies = {"student_session": student_cookie(args.secret_key, login)}
    async with httpx.AsyncClient(base_url=args.base_url, cookies=cookies, timeout=httpx.Timeout(30, read=None)) as client:
        response = await client.post(f"/quiz/{args.code}/join")
        if response.status_code != 200:
            stats.join_errors[response.json().get("detail", response.status_code)] += 1
            ready.release()
            return
        stats.joined += 1
        answered = set()
        released = False
        async with client.stream("GET", f"/quiz/{args.code}/events") as stream:
            async for snap in read_events(stream):
                now = time.monotonic()
                stats.received[snap["version"]].append(now)
                stats.versions_state[snap["version"]] = (snap["state"], snap.get("question"))
                if not released:
                    ready.release()
                    released = True
                if snap["state"] == "question" and snap["question"] not in answered and snap.get("options"):
                    answered.add(snap["question"])
                    budget = max(0.0, snap["remaining_ms"] / 1000 - 1.0)
                    delay = random.uniform(0, min(args.max_delay, budget))
                    asyncio.get_running_loop().create_task(
                        answer(client, args, stats, snap, delay, now + snap["remaining_ms"] / 1000)
                    )
                if snap["state"] == "finished":
                    stats.final_scores.append(snap.get("score"))
                if snap["state"] in ("finished", "cancelled"):
                    break


async def answer(client, args, stats, snap, delay, deadline):
    await asyncio.sleep(delay)
    if time.monotonic() > deadline:
        stats.late_sends += 1
    option = random.choice(snap["options"])["id"]
    response = await client.post(f"/quiz/{args.code}/answer", json={"question": snap["question"], "option": option})
    stats.answers["accepted" if response.status_code == 200 else response.json().get("detail", response.status_code)] += 1


async def drive(args):
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as admin:
        r = await admin.post("/admin/login", json={"login": args.admin_login, "password": args.admin_password})
        r.raise_for_status()
        info = (await admin.get(f"/quiz/{args.code}")).json()
        run_id = args.run_id
        await admin.post(f"/admin/quiz-runs/{run_id}/start")
        for i in range(info["total"]):
            while True:
                snap = (await admin.get(f"/admin/quiz-runs/{run_id}")).json()
                if snap["state"] == "reveal":
                    break
                await asyncio.sleep(0.5)
            print(f"  question {i + 1}: answered {snap['answered']} of {snap['joined']}, distribution {snap['distribution']}")
            await admin.post(f"/admin/quiz-runs/{run_id}/next")


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", required=True, help="API address, e.g. https://host/api/v1 or http://127.0.0.1:8000")
    parser.add_argument("--code", required=True, help="the run's 6-character code")
    parser.add_argument("--participants", type=int, default=200)
    parser.add_argument("--prefix", default="load-", help="logins are {prefix}0..{prefix}N-1")
    parser.add_argument("--max-delay", type=float, default=15.0, help="max seconds before answering")
    parser.add_argument("--secret-key", default=os.getenv("SECRET_KEY"), help="the stand's SECRET_KEY")
    parser.add_argument("--drive", action="store_true", help="press the teacher's buttons too")
    parser.add_argument("--run-id")
    parser.add_argument("--admin-login", default=os.getenv("ADMIN_LOGIN"))
    parser.add_argument("--admin-password", default=os.getenv("ADMIN_PASSWORD"))
    args = parser.parse_args()
    if not args.secret_key:
        sys.exit("SECRET_KEY is required (--secret-key or the environment)")
    args.code = args.code.lower()
    if args.drive and not args.run_id:
        sys.exit("--drive needs --run-id (the projector URL is /admin/quiz-runs/<run_id>)")

    stats = Stats()
    ready = asyncio.Semaphore(0)
    started = time.monotonic()
    tasks = [asyncio.create_task(participant(i, args, stats, ready)) for i in range(args.participants)]
    for _ in range(args.participants):
        await ready.acquire()
    print(f"{stats.joined} joined in {time.monotonic() - started:.1f}s, join errors: {dict(stats.join_errors)}")

    if args.drive:
        await drive(args)
    await asyncio.gather(*tasks)

    print(f"answers: {dict(stats.answers)}; sent after the deadline by this script: {stats.late_sends}")
    print("event delivery spread (same snapshot, first to last participant):")
    spreads = []
    for version in sorted(stats.received):
        times = stats.received[version]
        if len(times) < max(2, stats.joined // 2):
            continue  # a per-participant snapshot (e.g. own answer), not a broadcast
        spread = max(times) - min(times)
        spreads.append(spread)
    if spreads:
        print(f"  broadcasts: {len(spreads)}, median {statistics.median(spreads) * 1000:.0f} ms, "
              f"max {max(spreads) * 1000:.0f} ms")
    scores = Counter(stats.final_scores)
    print(f"final scores: {dict(scores)}")
    expected = stats.joined * len({q for s, q in stats.versions_state.values() if s == "question"})
    ok = stats.answers.get("accepted", 0) == expected - stats.late_sends
    print("RESULT:", "OK - every answer sent in time was accepted" if ok else "PROBLEM - see the counts above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
