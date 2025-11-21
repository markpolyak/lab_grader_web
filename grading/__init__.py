"""
Grading module for lab submissions.

This module contains pure functions for various aspects of lab grading:
- penalty: Calculate penalty points for late submissions
- taskid: Extract and validate task IDs from logs
- ci_checker: Evaluate CI check results
"""

from .penalty import (
    calculate_penalty,
    format_grade_with_penalty,
    PenaltyStrategy,
)

from .taskid import (
    extract_taskid_from_logs,
    calculate_expected_taskid,
    validate_taskid,
    TaskIdResult,
)

from .ci_checker import (
    CheckRun,
    CIResult,
    parse_check_runs,
    filter_relevant_jobs,
    evaluate_ci_results,
    get_ci_config_jobs,
    format_ci_result_string,
    DEFAULT_JOB_NAMES,
)

__all__ = [
    # penalty
    "calculate_penalty",
    "format_grade_with_penalty",
    "PenaltyStrategy",
    # taskid
    "extract_taskid_from_logs",
    "calculate_expected_taskid",
    "validate_taskid",
    "TaskIdResult",
    # ci_checker
    "CheckRun",
    "CIResult",
    "parse_check_runs",
    "filter_relevant_jobs",
    "evaluate_ci_results",
    "get_ci_config_jobs",
    "format_ci_result_string",
    "DEFAULT_JOB_NAMES",
]
