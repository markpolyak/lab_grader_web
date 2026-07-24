#!/usr/bin/env python3
"""
Local plagiarism smoke-test — no Google, org token optional.

Modes:
  1) demo (default)  — synthetic local files, zero network
  2) repos           — download files from GitHub repos YOU can read
                       (personal token / public repos is enough)

Examples (from repo root):

  # Pure local demo
  python scripts/plagiarism_local_demo.py

  # Your own / public repos (needs GITHUB_TOKEN with read access)
  python scripts/plagiarism_local_demo.py repos ^
    --repos markpolyak/some-lab-alice markpolyak/some-lab-bob ^
    --files main.py ^
    --prefix some-lab

Results:
  plagiarism_cache/local-demo/...
  plagiarism_cache/plagiarism.db
  Optional HTML report under _reports/
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from grading.github_client import GitHubClient
from grading.plagiarism import (
    compare_submission_against_cache,
    filter_matches_above_threshold,
    run_compare50,
)
from grading.plagiarism_cache import cache_submission_files, get_cache_root
from grading.plagiarism_store import list_matches, upsert_matches

IDENTICAL = "\n".join(
    [
        "def foo(x):",
        "    total = 0",
        "    for i in range(x):",
        "        total += i * 2",
        "        if i % 3 == 0:",
        "            total -= 1",
        "    return total",
        "",
        "def helper(values):",
        "    result = []",
        "    for v in values:",
        "        result.append(foo(v))",
        "    return result",
        "",
        "print(helper([1, 2, 3, 4, 5]))",
    ]
)

DIFFERENT = "\n".join(
    [
        "class Calculator:",
        "    def __init__(self, name):",
        "        self.name = name",
        "        self.history = []",
        "",
        "    def compute(self, items):",
        "        acc = 1",
        "        for item in items:",
        "            acc *= max(item, 1)",
        "            self.history.append(acc)",
        "        return acc",
        "",
        "calc = Calculator('demo')",
        "print(calc.compute([2, 3, 4]))",
    ]
)


def _print_matches(matches) -> None:
    if not matches:
        print("No matches above threshold.")
        return
    print(f"{'student_a':<20} {'student_b':<20} similarity")
    print("-" * 55)
    for m in matches:
        sim = getattr(m, "similarity", m.get("similarity") if isinstance(m, dict) else 0)
        a = getattr(m, "student_a", m.get("student_a") if isinstance(m, dict) else "?")
        b = getattr(m, "student_b", m.get("student_b") if isinstance(m, dict) else "?")
        print(f"{a:<20} {b:<20} {sim:.0%}")


def run_demo(cache_root: Path, threshold: float) -> int:
    course_id, lab_id, org, prefix = "local-demo", "1", "demo-org", "lab"
    base = cache_root / course_id / lab_id / org

    alice = base / f"{prefix}-alice"
    bob = base / f"{prefix}-bob"
    carol = base / f"{prefix}-carol"
    for d in (alice, bob, carol):
        d.mkdir(parents=True, exist_ok=True)

    (alice / "main.py").write_text(IDENTICAL, encoding="utf-8")
    (bob / "main.py").write_text(IDENTICAL, encoding="utf-8")
    (carol / "main.py").write_text(DIFFERENT, encoding="utf-8")

    print(f"Wrote fixtures under {base}")
    matches = compare_submission_against_cache(
        course_id,
        lab_id,
        org,
        f"{prefix}-alice",
        cache_root=cache_root,
        github_prefix=prefix,
        write_report=True,
    )
    flagged = filter_matches_above_threshold(matches, threshold)
    db = cache_root / "plagiarism.db"
    upsert_matches(course_id, lab_id, flagged, db_path=db)

    print(f"\nThreshold: {threshold:.0%}")
    _print_matches(flagged)
    stored = list_matches(course_id, lab_id, db_path=db)
    print(f"\nSQLite rows in {db}: {len(stored)}")
    print("Admin UI would show these via GET /courses/local-demo/labs/1/plagiarism")
    return 0 if flagged else 1


def run_repos(
    cache_root: Path,
    *,
    repos: list[str],
    files: list[str],
    prefix: str,
    threshold: float,
    token: str,
) -> int:
    github = GitHubClient(token)
    course_id, lab_id = "local-demo", "repos"

    cached_dirs = []
    for full in repos:
        if "/" not in full:
            print(f"Skip invalid repo (need org/name): {full}")
            continue
        org, repo = full.split("/", 1)
        result = cache_submission_files(
            github,
            course_id,
            lab_id,
            org,
            repo,
            files,
            cache_root=cache_root,
            github_prefix=prefix,
        )
        if result is None:
            print(f"WARN: nothing cached for {full}")
        else:
            print(f"Cached {full} -> {result.directory}")
            cached_dirs.append(result.directory)

    if len(cached_dirs) < 2:
        print("Need at least 2 successfully cached repos to compare.")
        return 1

    # Compare first as "new", rest as archive (incremental style)
    new_dir = cached_dirs[0]
    org = new_dir.parent.name
    repo = new_dir.name

    matches = compare_submission_against_cache(
        course_id,
        lab_id,
        org,
        repo,
        cache_root=cache_root,
        github_prefix=prefix,
        write_report=True,
    )
    flagged = filter_matches_above_threshold(matches, threshold)
    db = cache_root / "plagiarism.db"
    upsert_matches(course_id, lab_id, flagged, db_path=db)
    print(f"\nThreshold: {threshold:.0%}")
    _print_matches(flagged)
    print(f"SQLite: {db}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Local plagiarism smoke-test")
    parser.add_argument(
        "mode",
        nargs="?",
        default="demo",
        choices=["demo", "repos"],
        help="demo = local fixtures; repos = download from GitHub",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Cache root (default: ./plagiarism_cache or PLAGIARISM_CACHE_DIR)",
    )
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument(
        "--repos",
        nargs="+",
        default=[],
        help="org/repo list (repos mode)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=["main.py"],
        help="File paths inside each repo (repos mode)",
    )
    parser.add_argument(
        "--prefix",
        default="lab",
        help="Repo name prefix before student login, e.g. os-task1",
    )
    args = parser.parse_args()

    if args.cache_dir:
        os.environ["PLAGIARISM_CACHE_DIR"] = args.cache_dir
    cache_root = get_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    print(f"Cache root: {cache_root.resolve()}")

    if args.mode == "demo":
        return run_demo(cache_root, args.threshold)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("Set GITHUB_TOKEN (personal token with read access is enough).")
        return 1
    if len(args.repos) < 2:
        print("Provide at least two --repos org/name ...")
        return 1
    return run_repos(
        cache_root,
        repos=args.repos,
        files=args.files,
        prefix=args.prefix,
        threshold=args.threshold,
        token=token,
    )


if __name__ == "__main__":
    raise SystemExit(main())
