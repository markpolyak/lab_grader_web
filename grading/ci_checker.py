"""
CI (Continuous Integration) result checking for lab submissions.

This module contains functions for filtering and evaluating GitHub Actions
check runs to determine if a lab submission passes all required tests.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# Default job names to check if none specified in config
DEFAULT_JOB_NAMES = [
    "run-autograding-tests",
    "test",
    "build",
    "Autograding",
    "autograding",
]

# GitHub reports a finished check run through `conclusion`; while the run is
# still queued or executing, `conclusion` is None. Everything below is a
# finished run, so only None means "wait and come back later".
#
# A failed run is a grade of "x", which the grader may overwrite later, so a
# conclusion that produced no verdict (cancelled, timed out, stale) belongs
# here rather than among the pending ones: leaving it pending would freeze the
# grade forever, while "x" is undone by re-running CI.
FAILURE_CONCLUSIONS = frozenset({
    "failure",
    "timed_out",
    "cancelled",
    "stale",
    "action_required",
})

# A job whose `if:` condition did not hold never ran ("skipped"), and a job
# that deliberately declines to judge reports "neutral". Neither says anything
# about the student's work, so neither is counted - unless the lab config names
# the job explicitly (see evaluate_ci_results).
NOT_APPLICABLE_CONCLUSIONS = frozenset({"skipped", "neutral"})


@dataclass
class CheckRun:
    """Represents a single CI check run."""
    name: str
    conclusion: str | None  # "success", "failure", None (pending/running)
    html_url: str
    completed_at: datetime | None = None


@dataclass
class CIResult:
    """Aggregated result of CI checks."""
    passed: bool
    passed_count: int
    total_count: int
    summary: list[str] = field(default_factory=list)
    latest_success_time: datetime | None = None
    has_pending: bool = False
    ignored: list[str] = field(default_factory=list)
    pending_jobs: list[str] = field(default_factory=list)
    missing_jobs: list[str] = field(default_factory=list)
    config_mismatch: bool = False


def parse_check_runs(check_runs_data: list[dict[str, Any]]) -> list[CheckRun]:
    """
    Parse raw GitHub API check_runs response into CheckRun objects.

    Args:
        check_runs_data: List of check run dicts from GitHub API

    Returns:
        List of CheckRun objects
    """
    result = []
    for run in check_runs_data:
        completed_at = None
        completed_str = run.get("completed_at")
        if completed_str:
            try:
                completed_at = datetime.fromisoformat(completed_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        result.append(CheckRun(
            name=run.get("name", "Unnamed check"),
            conclusion=run.get("conclusion"),
            html_url=run.get("html_url", ""),
            completed_at=completed_at,
        ))
    return result


def filter_relevant_jobs(
    check_runs: list[CheckRun],
    configured_jobs: list[str] | None
) -> list[CheckRun]:
    """
    Filter CI jobs based on configuration.

    If configured_jobs is provided, only those jobs are included.
    If None, jobs matching DEFAULT_JOB_NAMES are used.
    If no matches found with defaults, all jobs are returned.

    Args:
        check_runs: List of all check runs
        configured_jobs: List of job names from lab config (None = use defaults)

    Returns:
        Filtered list of check runs

    Examples:
        >>> runs = [CheckRun("test", "success", "url1"), CheckRun("lint", "failure", "url2")]
        >>> filter_relevant_jobs(runs, ["test"])
        [CheckRun(name='test', ...)]
    """
    if configured_jobs is not None:
        # Filter by explicitly configured jobs
        return [run for run in check_runs if run.name in configured_jobs]

    # Try to find default jobs
    default_matches = [run for run in check_runs if run.name in DEFAULT_JOB_NAMES]

    if default_matches:
        return default_matches

    # If no default jobs found, return all (backwards compatibility).
    # Anything GitHub attaches to the commit lands here too, so say so loudly:
    # a silent fallback is how a lab ends up graded by a check nobody wrote.
    logger.warning(
        "CI jobs are not configured for this lab and none of the default names "
        "(%s) matched; falling back to all %d check runs: %s. "
        "List the grading jobs explicitly under ci.workflows in the course config.",
        ", ".join(DEFAULT_JOB_NAMES),
        len(check_runs),
        ", ".join(run.name for run in check_runs) or "-",
    )
    return check_runs


def evaluate_ci_results(
    check_runs: list[CheckRun],
    configured_jobs: list[str] | None = None,
    all_check_runs: list[CheckRun] | None = None,
) -> CIResult:
    """
    Evaluate CI check results and produce aggregated result.

    A lab passes when every counted check run succeeded - plain logical AND,
    with no "most of them are green" shortcut. What gets counted depends on the
    conclusion (see FAILURE_CONCLUSIONS / NOT_APPLICABLE_CONCLUSIONS) and on
    whether the lab config named the job:

    - a skipped or neutral job nobody asked for is dropped from both sides of
      the ratio: it says nothing about the student's work;
    - a skipped or neutral job the config names is a failure, because the
      config demands a success from it and it did not produce one;
    - a job the config names but GitHub never reported does not count against
      the student: course configs list job names across template generations
      (itmo-ml-2026 names both "run-autograding-tests" and "Test python
      scripts" while the repositories only ever produce the latter), so a name
      that matches nothing is reported through missing_jobs and otherwise
      ignored. Only when NOT ONE configured name matches is this treated as a
      broken config (config_mismatch) - and even then, not while CI is still
      starting up and not when the commit has no check runs at all.

    Args:
        check_runs: List of check runs to evaluate (already filtered)
        configured_jobs: Job names from the lab config, if it names any
        all_check_runs: Every check run on the commit, before filtering.
            Defaults to check_runs. A job filtered out but still running means
            a configured job may yet appear, and a commit with no check runs at
            all is a CI that has not started - neither is a broken config.

    Returns:
        CIResult with aggregated pass/fail status and summary

    Examples:
        >>> runs = [
        ...     CheckRun("test", "success", "url1"),
        ...     CheckRun("lint", "success", "url2")
        ... ]
        >>> result = evaluate_ci_results(runs)
        >>> result.passed
        True
        >>> result.passed_count
        2
    """
    required = set(configured_jobs or [])
    every_run = check_runs if all_check_runs is None else all_check_runs
    ci_in_progress = any(run.conclusion is None for run in every_run)

    summary: list[str] = []
    ignored: list[str] = []
    pending_jobs: list[str] = []
    missing_jobs: list[str] = []
    passed_count = 0
    counted_count = 0
    latest_success: datetime | None = None

    for run in check_runs:
        if run.conclusion == "success":
            counted_count += 1
            passed_count += 1
            summary.append(f"✅ {run.name} — {run.html_url}")
            if run.completed_at:
                if latest_success is None or run.completed_at > latest_success:
                    latest_success = run.completed_at
        elif run.conclusion is None:
            counted_count += 1
            pending_jobs.append(run.name)
            summary.append(f"⏳ {run.name} — {run.html_url}")
        elif run.conclusion in NOT_APPLICABLE_CONCLUSIONS:
            if run.name in required:
                counted_count += 1
                summary.append(
                    f"❌ {run.name} ({run.conclusion}) — джоба требуется по настройкам курса, "
                    f"но результата не дала: {run.html_url}"
                )
            else:
                ignored.append(run.name)
                summary.append(f"⏭️ {run.name} ({run.conclusion}) — не учитывается: {run.html_url}")
        else:
            # "failure" and every other finished-but-not-successful conclusion
            counted_count += 1
            label = run.name if run.conclusion == "failure" else f"{run.name} ({run.conclusion})"
            summary.append(f"❌ {label} — {run.html_url}")

    reported_names = {run.name for run in check_runs}
    missing_jobs = [name for name in configured_jobs or [] if name not in reported_names]
    matched_any = bool(required & reported_names)
    config_mismatch = (
        bool(missing_jobs)
        and not matched_any
        and not ci_in_progress
        and bool(every_run)  # no check runs at all: CI has not started
    )

    for name in missing_jobs:
        if config_mismatch:
            summary.append(f"❓ {name} — джоба не найдена среди проверок коммита")
        else:
            summary.append(f"❓ {name} — джоба не найдена среди проверок коммита, не учитывается")

    if missing_jobs and not matched_any and ci_in_progress:
        # CI has only just started: the configured jobs may still appear.
        pending_jobs.extend(missing_jobs)

    if counted_count == 0 and not config_mismatch and not pending_jobs:
        # Nothing to judge by: no checks at all, or every one of them was
        # skipped. Either way this is not a pass - wait for a real result.
        return CIResult(
            passed=False,
            passed_count=0,
            total_count=0,
            summary=summary,
            ignored=ignored,
            has_pending=True,
        )

    has_pending = bool(pending_jobs)

    if missing_jobs:
        logger.warning(
            "Configured CI jobs absent from the commit's check runs: %s (reported: %s)",
            ", ".join(missing_jobs),
            ", ".join(sorted(reported_names)) or "-",
        )

    return CIResult(
        passed=(
            counted_count > 0
            and passed_count == counted_count
            and not has_pending
            and not config_mismatch
        ),
        passed_count=passed_count,
        total_count=counted_count,
        summary=summary,
        latest_success_time=latest_success,
        has_pending=has_pending,
        ignored=ignored,
        pending_jobs=pending_jobs,
        missing_jobs=missing_jobs,
        config_mismatch=config_mismatch,
    )


def get_ci_config_jobs(lab_config: dict[str, Any]) -> list[str] | None:
    """
    Extract configured job names from lab config.

    Supports both old format (ci.workflows) and new format (ci.jobs).

    Args:
        lab_config: Lab configuration dict

    Returns:
        List of job names or None if not configured

    Examples:
        >>> config = {"ci": {"workflows": ["test", "lint"]}}
        >>> get_ci_config_jobs(config)
        ['test', 'lint']
        >>> config = {"ci": ["workflows"]}  # Old format meaning "check all"
        >>> get_ci_config_jobs(config)
        None
    """
    ci_config = lab_config.get("ci")

    if ci_config is None:
        return None

    # New format: ci.jobs or ci.workflows (dict with list)
    if isinstance(ci_config, dict):
        # Prefer "jobs" over "workflows" for new configs
        jobs = ci_config.get("jobs") or ci_config.get("workflows")
        if isinstance(jobs, list) and jobs:
            return jobs
        return None

    # Old format: ci: [- workflows] meaning "check workflows exist"
    # This doesn't specify which jobs, so return None
    if isinstance(ci_config, list):
        return None

    return None


def format_ci_result_string(passed_count: int, total_count: int) -> str:
    """
    Format CI result as human-readable string.

    Args:
        passed_count: Number of passed checks
        total_count: Total number of checks

    Returns:
        Formatted string like "3/4 тестов пройдено"
    """
    return f"{passed_count}/{total_count} тестов пройдено"
