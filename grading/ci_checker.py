"""
CI (Continuous Integration) result checking for lab submissions.

This module contains functions for filtering and evaluating GitHub Actions
check runs to determine if a lab submission passes all required tests.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# Default job names to check if none specified in config
DEFAULT_JOB_NAMES = [
    "run-autograding-tests",
    "test",
    "build",
    "Autograding",
    "autograding",
]


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

    # If no default jobs found, return all (backwards compatibility)
    return check_runs


def evaluate_ci_results(check_runs: list[CheckRun]) -> CIResult:
    """
    Evaluate CI check results and produce aggregated result.

    Args:
        check_runs: List of check runs to evaluate (already filtered)

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
    if not check_runs:
        return CIResult(
            passed=False,
            passed_count=0,
            total_count=0,
            summary=[],
            has_pending=True,  # No checks means pending
        )

    summary = []
    passed_count = 0
    latest_success: datetime | None = None
    has_pending = False

    for run in check_runs:
        if run.conclusion == "success":
            emoji = "✅"
            passed_count += 1
            if run.completed_at:
                if latest_success is None or run.completed_at > latest_success:
                    latest_success = run.completed_at
        elif run.conclusion == "failure":
            emoji = "❌"
        else:
            emoji = "⏳"
            has_pending = True

        summary.append(f"{emoji} {run.name} — {run.html_url}")

    return CIResult(
        passed=(passed_count == len(check_runs) and not has_pending),
        passed_count=passed_count,
        total_count=len(check_runs),
        summary=summary,
        latest_success_time=latest_success,
        has_pending=has_pending,
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
