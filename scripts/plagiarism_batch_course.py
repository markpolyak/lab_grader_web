#!/usr/bin/env python3
"""
Batch plagiarism run for an entire course org (no grade write, no Sheets).

Loads course YAML via index id (e.g. os-2025-spring), for each lab with
moss:/plagiarism: + files: downloads matching repos from the course GitHub
org, runs compare50 across the corpus, stores matches in SQLite.

Does NOT write "v" to Google Sheets — that happens only via POST .../grade.

Example:

  python scripts/plagiarism_batch_course.py --course os-2025-spring

  # Only labs 2 and 5, skip previous-year additional orgs
  python scripts/plagiarism_batch_course.py --course os-2025-spring --labs 2 5 --skip-additional

  # Limit repos per lab (smoke)
  python scripts/plagiarism_batch_course.py --course os-2025-spring --max-repos 15
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from grading.github_client import GitHubClient
from grading.plagiarism import (
    filter_matches_above_threshold,
    get_plagiarism_config,
    run_compare50,
)
from grading.plagiarism_cache import (
    cache_submission_files,
    convert_ipynb_to_py,
    get_cache_root,
    list_cached_submissions,
)
from grading.plagiarism_store import upsert_matches


def load_course_by_id(course_id: str) -> tuple[dict, str]:
    """Return (course_config_dict, course_id) from courses/index.yaml."""
    index_path = ROOT / "courses" / "index.yaml"
    with open(index_path, encoding="utf-8") as f:
        index = yaml.safe_load(f)

    entry = None
    for item in index.get("courses", []):
        if item.get("id") == course_id:
            entry = item
            break
    if entry is None:
        raise SystemExit(f"Course id '{course_id}' not found in courses/index.yaml")

    course_path = ROOT / "courses" / entry["file"]
    with open(course_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "course" not in data:
        raise SystemExit(f"Invalid course file: {course_path}")
    return data["course"], course_id


def ensure_basefiles(
    github: GitHubClient,
    course_id: str,
    lab_id: str,
    basefiles: list | None,
    cache_root: Path,
) -> Path | None:
    if not basefiles:
        return None
    base_dir = cache_root / course_id / str(lab_id) / "_basefiles"
    base_dir.mkdir(parents=True, exist_ok=True)
    any_file = False
    for entry in basefiles:
        if not isinstance(entry, dict):
            continue
        repo_full = entry.get("repo", "")
        filename = entry.get("filename", "")
        if not repo_full or not filename or "/" not in repo_full:
            continue
        org, repo = repo_full.split("/", 1)
        content = github.get_file_content(org, repo, filename)
        if content is None:
            print(f"  WARN basefile missing: {repo_full}/{filename}")
            continue
        dest = base_dir / org / repo / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        if dest.suffix.lower() == ".ipynb":
            convert_ipynb_to_py(dest)
        any_file = True
    return base_dir if any_file else None


def cache_org_lab(
    github: GitHubClient,
    *,
    course_id: str,
    lab_id: str,
    org: str,
    prefix: str,
    files: list[str],
    local_path: str | None,
    cache_root: Path,
    max_repos: int | None,
) -> int:
    repos = github.list_repos_with_prefix(org, prefix)
    if max_repos is not None:
        repos = repos[:max_repos]
    print(f"  {org}: {len(repos)} repos with prefix '{prefix}-'")
    cached = 0
    for repo in repos:
        result = cache_submission_files(
            github,
            course_id,
            lab_id,
            org,
            repo,
            files,
            local_path=local_path,
            cache_root=cache_root,
            github_prefix=prefix,
        )
        if result is not None:
            cached += 1
    return cached


def run_lab(
    github: GitHubClient,
    *,
    course_id: str,
    lab_id: str,
    lab_cfg: dict,
    org: str,
    cache_root: Path,
    threshold: float,
    skip_additional: bool,
    max_repos: int | None,
    write_report: bool,
) -> int:
    plag = get_plagiarism_config(lab_cfg)
    if plag is None or not plag.enabled:
        print(f"[lab {lab_id}] skip — no moss:/plagiarism:")
        return 0

    files = lab_cfg.get("files") or []
    prefix = lab_cfg.get("github-prefix")
    if not files or not prefix:
        print(f"[lab {lab_id}] skip — missing files: or github-prefix")
        return 0

    short = lab_cfg.get("short-name", lab_id)
    print(f"\n=== Lab {lab_id} ({short}) prefix={prefix} files={files} ===")

    n = cache_org_lab(
        github,
        course_id=course_id,
        lab_id=lab_id,
        org=org,
        prefix=prefix,
        files=files,
        local_path=plag.local_path,
        cache_root=cache_root,
        max_repos=max_repos,
    )
    print(f"  cached from course org: {n}")

    if not skip_additional and plag.additional:
        for add_org in plag.additional:
            try:
                n_add = cache_org_lab(
                    github,
                    course_id=course_id,
                    lab_id=lab_id,
                    org=add_org,
                    prefix=prefix,
                    files=files,
                    local_path=plag.local_path,
                    cache_root=cache_root,
                    max_repos=max_repos,
                )
                print(f"  cached from {add_org}: {n_add}")
            except Exception as exc:
                print(f"  WARN additional org {add_org}: {exc}")

    base_dir = ensure_basefiles(
        github, course_id, lab_id, plag.basefiles, cache_root
    )

    dirs = list_cached_submissions(course_id, lab_id, cache_root)
    # Exclude _basefiles / _reports style dirs if any slipped in
    dirs = [d for d in dirs if not d.name.startswith("_")]
    if len(dirs) < 2:
        print(f"  not enough submissions ({len(dirs)}) — skip compare")
        return 0

    report_dir = None
    if write_report:
        report_dir = cache_root / course_id / str(lab_id) / "_reports" / "batch_all"

    print(f"  comparing {len(dirs)} submissions...")
    matches = run_compare50(
        dirs,
        distro_dirs=[base_dir] if base_dir else [],
        max_matches=plag.max_matches,
        github_prefix=prefix,
        output_dir=report_dir,
    )
    thr = plag.threshold if threshold is None else threshold
    flagged = filter_matches_above_threshold(matches, thr)
    db = cache_root / "plagiarism.db"
    upsert_matches(course_id, lab_id, flagged, db_path=db)

    print(f"  matches total={len(matches)} flagged(>={thr:.0%})={len(flagged)}")
    for m in flagged[:15]:
        print(f"    {m.student_a} ↔ {m.student_b}: {m.similarity:.0%}")
    if len(flagged) > 15:
        print(f"    ... and {len(flagged) - 15} more")
    if report_dir:
        print(f"  report: {report_dir / 'index.html'}")
    return len(flagged)


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch plagiarism for a course")
    parser.add_argument("--course", required=True, help="Course id from index.yaml")
    parser.add_argument("--labs", nargs="*", help="Optional lab ids to include (default: all)")
    parser.add_argument("--skip-additional", action="store_true",
                        help="Do not download moss.additional orgs")
    parser.add_argument("--max-repos", type=int, default=None,
                        help="Cap repos per org (smoke tests)")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Override lab threshold")
    parser.add_argument("--no-report", action="store_true", help="Skip HTML reports")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set (put it in .env)")
        return 1

    course, course_id = load_course_by_id(args.course)
    org = (course.get("github") or {}).get("organization")
    if not org:
        print("Course has no github.organization")
        return 1

    labs = course.get("labs") or {}
    lab_ids = args.labs if args.labs else list(labs.keys())

    cache_root = get_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    github = GitHubClient(token)

    print(f"Course {course_id} org={org}")
    print(f"Cache: {cache_root.resolve()}")
    print(f"Labs: {lab_ids}")

    total_flagged = 0
    for lab_id in lab_ids:
        lab_cfg = labs.get(str(lab_id)) or labs.get(lab_id)
        if not isinstance(lab_cfg, dict):
            print(f"[lab {lab_id}] not found in YAML")
            continue
        total_flagged += run_lab(
            github,
            course_id=course_id,
            lab_id=str(lab_id),
            lab_cfg=lab_cfg,
            org=org,
            cache_root=cache_root,
            threshold=args.threshold,
            skip_additional=args.skip_additional,
            max_repos=args.max_repos,
            write_report=not args.no_report,
        )

    print(f"\nDone. Total flagged pairs: {total_flagged}")
    print(f"SQLite: {cache_root / 'plagiarism.db'}")
    print(f"Admin: /admin/courses/{course_id}/labs → plagiarism")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
