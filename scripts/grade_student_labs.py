#!/usr/bin/env python3
"""
Grade all labs for one student via the real grade_lab endpoint logic.

Writes v/x to Google Sheets when checks pass; schedules plagiarism BackgroundTasks.

Example:
  python scripts/grade_student_labs.py --course os-2025-spring --group 4333K --github dorofeevalexru
  python scripts/grade_student_labs.py --course os-2025-spring --group 4333K --github dorofeevalexru --labs 2 5
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from fastapi import BackgroundTasks
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# Import app pieces after env is loaded
from main import GradeRequest, get_course_by_id, grade_lab  # noqa: E402


def _fake_request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/grade",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }

    async def receive():
        return {"type": "http.request"}

    request = Request(scope, receive)
    if not hasattr(request.state, "view_rate_limit"):
        request.state.view_rate_limit = None
    return request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--course", required=True, help="Course id from index.yaml")
    parser.add_argument("--group", required=True, help="Worksheet title, e.g. 4333K")
    parser.add_argument("--github", required=True, help="Student GitHub login")
    parser.add_argument("--labs", nargs="*", help="Lab ids to grade (default: all with files:)")
    args = parser.parse_args()

    for key in ("GITHUB_TOKEN", "ADMIN_LOGIN", "ADMIN_PASSWORD"):
        if not os.environ.get(key):
            print(f"Missing {key} in environment / .env")
            return 1

    course = get_course_by_id(args.course)
    labs = course.get("labs") or {}
    lab_ids = args.labs if args.labs else [
        lid for lid, cfg in labs.items()
        if isinstance(cfg, dict) and cfg.get("files")
    ]

    print(f"Course={args.course} group={args.group} github={args.github}")
    print(f"Labs: {lab_ids}")
    print(f"PLAGIARISM_SHADOW_MODE={os.environ.get('PLAGIARISM_SHADOW_MODE')}")

    request = _fake_request()
    grade_request = GradeRequest(github=args.github)
    background_tasks = BackgroundTasks()

    results = []
    for lab_id in lab_ids:
        cfg = labs.get(str(lab_id)) or labs.get(lab_id) or {}
        short = cfg.get("short-name") or f"ЛР{lab_id}"
        # grade_lab accepts lab_id like ЛР5 or numeric; use short-name like UI
        lab_path_id = short
        print(f"\n=== Grading {lab_path_id} (id={lab_id}) ===")
        try:
            result = grade_lab(
                request,
                args.course,
                args.group,
                lab_path_id,
                grade_request,
                background_tasks,
            )
            print(result)
            results.append((lab_path_id, result))
        except Exception as exc:
            detail = getattr(exc, "detail", str(exc))
            print(f"FAIL: {detail}")
            results.append((lab_path_id, {"status": "error", "detail": detail}))

    # Run plagiarism / other background tasks now (same as after HTTP response)
    print("\n=== Running background tasks (plagiarism etc.) ===")
    import asyncio

    async def _run_bg():
        await background_tasks()

    asyncio.run(_run_bg())

    print("\n=== Summary ===")
    for name, res in results:
        if isinstance(res, dict):
            print(f"{name}: status={res.get('status')} result={res.get('result')} msg={res.get('message') or res.get('detail')}")
        else:
            print(f"{name}: {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
