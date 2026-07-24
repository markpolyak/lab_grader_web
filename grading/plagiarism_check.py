"""
Background plagiarism check orchestration.

Called after a successful grade write (`v...`) so the student response
is not blocked by GitHub downloads / compare50.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .github_client import GitHubClient
from .plagiarism import (
    filter_matches_above_threshold,
    get_plagiarism_config,
    compare_submission_against_cache,
)
from .plagiarism_cache import (
    cache_submission_files,
    convert_ipynb_to_py,
    get_cache_root,
    submission_cache_dir,
)
from .plagiarism_store import upsert_matches
from .sheets_comments import notify_teacher_plagiarism

logger = logging.getLogger(__name__)


def _shadow_mode() -> bool:
    """
    When true, run detection and store results but do not notify teachers
    (no Sheets comments). Used for pilot / shadow rollout.
    """
    return os.environ.get("PLAGIARISM_SHADOW_MODE", "").lower() in ("1", "true", "yes")


def _db_path_for_cache(cache_root: str | Path | None) -> Path | None:
    if cache_root is None:
        return None
    return Path(get_cache_root(cache_root)) / "plagiarism.db"


def _ensure_basefiles(
    github: GitHubClient,
    course_id: str,
    lab_id: str,
    basefiles: list[dict[str, str]] | None,
    cache_root: str | Path | None = None,
) -> Path | None:
    """Download template/base files once into `{cache}/_basefiles/`."""
    if not basefiles:
        return None

    base_dir = get_cache_root(cache_root) / course_id / str(lab_id) / "_basefiles"
    base_dir.mkdir(parents=True, exist_ok=True)

    any_file = False
    for entry in basefiles:
        if not isinstance(entry, dict):
            continue
        repo_full = entry.get("repo", "")
        filename = entry.get("filename", "")
        if not repo_full or not filename:
            continue
        parts = repo_full.split("/", 1)
        if len(parts) != 2:
            logger.warning("Invalid basefile repo '%s' — expected org/repo", repo_full)
            continue
        org, repo = parts
        content = github.get_file_content(org, repo, filename)
        if content is None:
            logger.warning("Basefile %s/%s not found", repo_full, filename)
            continue
        dest = base_dir / org / repo / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        if dest.suffix.lower() == ".ipynb":
            convert_ipynb_to_py(dest)
        any_file = True
        logger.info("Cached basefile %s/%s", repo_full, filename)

    return base_dir if any_file else None


def _prefill_additional_orgs(
    github: GitHubClient,
    course_id: str,
    lab_id: str,
    files: list[str],
    github_prefix: str | None,
    additional_orgs: list[str] | None,
    *,
    local_path: str | None = None,
    cache_root: str | Path | None = None,
    max_repos_per_org: int = 200,
) -> int:
    """
    One-time-ish prefill of previous-year orgs into the lab cache.

    Skips repos already present. Returns number of newly cached submissions.
    """
    if not additional_orgs or not github_prefix or not files:
        return 0

    cached = 0
    for add_org in additional_orgs:
        try:
            repos = github.list_repos_with_prefix(add_org, github_prefix)
        except Exception:
            logger.exception("Failed listing repos in additional org %s", add_org)
            continue

        for repo in repos[:max_repos_per_org]:
            dest = submission_cache_dir(course_id, lab_id, add_org, repo, cache_root)
            if dest.is_dir() and any(dest.iterdir()):
                continue
            try:
                result = cache_submission_files(
                    github,
                    course_id,
                    lab_id,
                    add_org,
                    repo,
                    files,
                    local_path=local_path,
                    cache_root=cache_root,
                    github_prefix=github_prefix,
                )
            except Exception:
                logger.exception(
                    "Failed caching additional %s/%s — continuing", add_org, repo
                )
                continue
            if result is not None:
                cached += 1

    if cached:
        logger.info(
            "Prefill additional orgs for %s/%s: cached %d new submissions",
            course_id, lab_id, cached,
        )
    return cached


def run_plagiarism_check(
    course_id: str,
    lab_id: str,
    org: str,
    repo: str,
    lab_config: dict[str, Any],
    github_token: str,
    *,
    cache_root: str | Path | None = None,
    # Optional Sheets notification context (from grade_lab)
    spreadsheet_id: str | None = None,
    worksheet_title: str | None = None,
    cell_row: int | None = None,
    cell_col: int | None = None,
    student: str | None = None,
    credentials_file: str | None = None,
) -> list[dict[str, Any]]:
    """
    Full plagiarism pipeline for one graded submission:

    1. Resolve plagiarism/moss config
    2. Prefill additional orgs (previous years) if configured
    3. Cache submission files
    4. Ensure basefiles are cached
    5. Compare new submission vs existing cache
    6. Persist matches above threshold to SQLite
    7. Notify teacher (cell note) unless SHADOW_MODE

    Returns list of flagged match dicts (above threshold).
    Never raises — errors are logged (background task must not crash the worker).
    """
    try:
        cfg = get_plagiarism_config(lab_config)
        if cfg is None or not cfg.enabled:
            logger.debug("Plagiarism check disabled for %s lab %s", course_id, lab_id)
            return []

        if cfg.engine not in ("compare50", "moss"):
            logger.warning(
                "Plagiarism engine '%s' not implemented — skipping", cfg.engine
            )
            return []

        files = lab_config.get("files") or []
        if not files:
            logger.info("No files: in lab config — skipping plagiarism for %s/%s", course_id, lab_id)
            return []

        github = GitHubClient(github_token)
        github_prefix = lab_config.get("github-prefix")

        # Prefill previous years once per lab (marker file under cache)
        marker = get_cache_root(cache_root) / course_id / str(lab_id) / "_additional_prefilled"
        if cfg.additional and not marker.exists():
            _prefill_additional_orgs(
                github,
                course_id,
                lab_id,
                files,
                github_prefix,
                cfg.additional,
                local_path=cfg.local_path,
                cache_root=cache_root,
            )
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("ok", encoding="utf-8")
            except OSError:
                logger.warning("Could not write prefill marker %s", marker)

        cached = cache_submission_files(
            github,
            course_id,
            lab_id,
            org,
            repo,
            files,
            local_path=cfg.local_path,
            cache_root=cache_root,
            github_prefix=github_prefix,
        )
        if cached is None:
            logger.warning("Failed to cache files for %s/%s — skip plagiarism", org, repo)
            return []

        base_dir = _ensure_basefiles(
            github, course_id, lab_id, cfg.basefiles, cache_root=cache_root
        )

        matches = compare_submission_against_cache(
            course_id,
            lab_id,
            org,
            repo,
            cache_root=cache_root,
            github_prefix=github_prefix,
            max_matches=cfg.max_matches,
            basefiles_dir=base_dir,
            write_report=True,
        )

        flagged = filter_matches_above_threshold(matches, cfg.threshold)
        upsert_matches(
            course_id,
            lab_id,
            flagged,
            db_path=_db_path_for_cache(cache_root),
        )

        flagged_dicts = [
            {
                "student_a": m.student_a,
                "student_b": m.student_b,
                "similarity": m.similarity,
                "details": m.details,
            }
            for m in flagged
        ]

        logger.info(
            "Plagiarism check for %s/%s lab %s: %d matches, %d above threshold %.2f%s",
            org,
            repo,
            lab_id,
            len(matches),
            len(flagged),
            cfg.threshold,
            " [SHADOW]" if _shadow_mode() else "",
        )

        # Teacher notification (skipped in shadow / pilot mode)
        if (
            flagged_dicts
            and not _shadow_mode()
            and spreadsheet_id
            and worksheet_title
            and cell_row
            and cell_col
            and credentials_file
        ):
            graded_student = student or (cached.student if cached else repo)
            # Only notify about pairs involving the graded student
            relevant = [
                m for m in flagged_dicts
                if graded_student in (m["student_a"], m["student_b"])
            ]
            if relevant:
                notify_teacher_plagiarism(
                    spreadsheet_id=spreadsheet_id,
                    worksheet_title=worksheet_title,
                    row=cell_row,
                    col=cell_col,
                    student=graded_student,
                    lab_short_name=lab_config.get("short-name"),
                    matches=relevant,
                    credentials_file=credentials_file,
                )

        return flagged_dicts
    except Exception:
        logger.exception(
            "Plagiarism check failed for %s/%s (course=%s lab=%s)",
            org, repo, course_id, lab_id,
        )
        return []
