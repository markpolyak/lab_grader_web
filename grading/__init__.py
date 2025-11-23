"""
Grading module for lab submissions.

This module contains functions for various aspects of lab grading:
- penalty: Calculate penalty points for late submissions
- taskid: Extract and validate task IDs from logs
- ci_checker: Evaluate CI check results
- github_client: GitHub API client
- sheets_client: Google Sheets helpers
- grader: Orchestrator for grading workflow
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
    check_forbidden_files_in_list,
    get_default_forbidden_patterns,
)

from .sheets_client import (
    find_student_row,
    find_lab_column_by_name,
    calculate_lab_column,
    can_overwrite_cell,
    prepare_grade_update,
    get_deadline_from_sheet,
    get_student_order,
    set_cell_warning,
    format_forbidden_files_note,
    StudentLocation,
    LabColumn,
    GradeUpdate,
)

from .grader import (
    LabGrader,
    GradeResult,
    GradeStatus,
    ForbiddenFilesWarning,
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
    "check_forbidden_files_in_list",
    "get_default_forbidden_patterns",
    # sheets_client
    "find_student_row",
    "find_lab_column_by_name",
    "calculate_lab_column",
    "can_overwrite_cell",
    "prepare_grade_update",
    "get_deadline_from_sheet",
    "get_student_order",
    "set_cell_warning",
    "format_forbidden_files_note",
    "StudentLocation",
    "LabColumn",
    "GradeUpdate",
    # grader
    "LabGrader",
    "GradeResult",
    "GradeStatus",
    "ForbiddenFilesWarning",
]
