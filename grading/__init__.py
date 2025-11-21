"""
Grading module for lab submissions.

This module contains functions for various aspects of lab grading:
- penalty: Calculate penalty points for late submissions
- taskid: Extract and validate task IDs from logs
- ci_checker: Evaluate CI check results
- github_client: GitHub API client
- sheets_client: Google Sheets helpers
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

from .github_client import (
    GitHubClient,
    CommitInfo,
    check_forbidden_modifications,
    get_default_forbidden_patterns,
)

from .sheets_client import (
    find_student_row,
    find_lab_column_by_name,
    calculate_lab_column,
    can_overwrite_cell,
    prepare_grade_update,
    StudentLocation,
    LabColumn,
    GradeUpdate,
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
    # github_client
    "GitHubClient",
    "CommitInfo",
    "check_forbidden_modifications",
    "get_default_forbidden_patterns",
    # sheets_client
    "find_student_row",
    "find_lab_column_by_name",
    "calculate_lab_column",
    "can_overwrite_cell",
    "prepare_grade_update",
    "StudentLocation",
    "LabColumn",
    "GradeUpdate",
]
