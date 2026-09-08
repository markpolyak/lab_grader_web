"""
Grading module for lab submissions.

This module contains functions for various aspects of lab grading:
- penalty: Calculate penalty points for late submissions
- taskid: Extract and validate task IDs from logs
- ci_checker: Evaluate CI check results
- github_client: GitHub API client
- sheets_client: Google Sheets helpers
- grader: Orchestrator for grading workflow
- repo_provisioning: Orchestrator for the /join student repo creation flow
- teams: Team (group) lab assignments - one repository per team
- propagate: Orchestrator for propagating template updates via fork PRs (admin)
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
    is_rate_limited,
)

from .sheets_client import (
    find_student_row,
    find_lab_column_by_name,
    calculate_lab_column,
    can_overwrite_cell,
    prepare_grade_update,
    parse_deadline,
    get_deadline_from_sheet,
    get_student_order,
    get_decimal_separator,
    cell_from_grid,
    column_values_from_grid,
    find_lab_column_in_grid,
    get_deadline_from_grid,
    get_student_order_from_grid,
    StudentLocation,
    LabColumn,
    GradeUpdate,
)

from .grader import (
    LabGrader,
    GradeResult,
    GradeStatus,
)

from .repo_provisioning import (
    RepoProvisioner,
    ProvisionResult,
    ProvisionStatus,
)

from .teams import (
    TeamConfig,
    TeamConfigError,
    TeamActionResult,
    TeamActionStatus,
    TeamInfo,
    TeamRegistry,
    TeamTitleError,
    TEAMS_CACHE_TTL_SECONDS,
    TEAM_SLUG_RE,
    is_team_lab,
    parse_team_config,
    clean_team_title,
    clean_team_description,
    compose_description,
    parse_description,
    reset_teams_state,
)

from .propagate import (
    PropagateJob,
    PropagateResult,
    PropagateSetupError,
    dry_run_propagation,
    try_start_propagate_job,
    run_propagation,
    get_propagate_job,
    PR_CREATE_PAUSE_SECONDS,
)

from .bulk import (
    SheetContext,
    StudentOutcome,
    BulkJob,
    BulkResult,
    BulkGradingError,
    NameMatchError,
    evaluate_student,
    taskid_column,
    repo_name_for,
    filter_lab_repos,
    extract_full_name,
    normalize_full_name,
    find_row_by_full_name,
    resolve_github_cell,
    try_start_bulk_job,
    get_bulk_job,
    request_bulk_job_cancel,
    run_bulk_grading,
)

from .score import (
    extract_score_from_logs,
    format_score,
    format_grade_with_score,
    ScoreResult,
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
    "is_rate_limited",
    # sheets_client
    "find_student_row",
    "find_lab_column_by_name",
    "calculate_lab_column",
    "can_overwrite_cell",
    "prepare_grade_update",
    "parse_deadline",
    "get_deadline_from_sheet",
    "get_student_order",
    "get_decimal_separator",
    "cell_from_grid",
    "column_values_from_grid",
    "find_lab_column_in_grid",
    "get_deadline_from_grid",
    "get_student_order_from_grid",
    "StudentLocation",
    "LabColumn",
    "GradeUpdate",
    # grader
    "LabGrader",
    "GradeResult",
    "GradeStatus",
    # repo_provisioning
    "RepoProvisioner",
    "ProvisionResult",
    "ProvisionStatus",
    # teams
    "TeamConfig",
    "TeamConfigError",
    "TeamActionResult",
    "TeamActionStatus",
    "TeamInfo",
    "TeamRegistry",
    "TeamTitleError",
    "TEAMS_CACHE_TTL_SECONDS",
    "TEAM_SLUG_RE",
    "is_team_lab",
    "parse_team_config",
    "clean_team_title",
    "clean_team_description",
    "compose_description",
    "parse_description",
    "reset_teams_state",
    # propagate
    "PropagateJob",
    "PropagateResult",
    "PropagateSetupError",
    "dry_run_propagation",
    "try_start_propagate_job",
    "run_propagation",
    "get_propagate_job",
    "PR_CREATE_PAUSE_SECONDS",
    # bulk
    "SheetContext",
    "StudentOutcome",
    "BulkJob",
    "BulkResult",
    "BulkGradingError",
    "NameMatchError",
    "evaluate_student",
    "taskid_column",
    "repo_name_for",
    "filter_lab_repos",
    "extract_full_name",
    "normalize_full_name",
    "find_row_by_full_name",
    "resolve_github_cell",
    "try_start_bulk_job",
    "get_bulk_job",
    "request_bulk_job_cancel",
    "run_bulk_grading",
    # score
    "extract_score_from_logs",
    "format_score",
    "format_grade_with_score",
    "ScoreResult",
]
