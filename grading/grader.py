"""
Lab grading orchestrator.

This module provides the main LabGrader class that orchestrates
all grading checks and returns structured results.
"""
from dataclasses import dataclass, field
from typing import Any
from enum import Enum

from .github_client import (
    GitHubClient,
    check_forbidden_modifications,
    get_default_forbidden_patterns,
)
from .ci_checker import (
    parse_check_runs,
    filter_relevant_jobs,
    evaluate_ci_results,
    get_ci_config_jobs,
    format_ci_result_string,
)
from .sheets_client import (
    find_student_row,
    find_lab_column_by_name,
    calculate_lab_column,
    can_overwrite_cell,
)


class GradeStatus(Enum):
    """Possible grading outcomes."""
    UPDATED = "updated"      # Grade successfully written
    REJECTED = "rejected"    # Cell protected, grade not written
    PENDING = "pending"      # CI checks not complete
    ERROR = "error"          # Error during grading


@dataclass
class GradeResult:
    """Result of a grading operation."""
    status: GradeStatus
    result: str | None       # "v", "x", "v-N", or None
    message: str
    passed: str | None       # "3/4 тестов пройдено"
    checks: list[str] = field(default_factory=list)  # Summary of each check
    current_grade: str | None = None  # Existing grade if rejected
    error_code: str | None = None     # For programmatic error handling


@dataclass
class RepoValidationResult:
    """Result of repository validation checks."""
    valid: bool
    error_message: str | None = None
    error_code: str | None = None
    missing_files: list[str] = field(default_factory=list)
    forbidden_modifications: list[str] = field(default_factory=list)


class LabGrader:
    """
    Orchestrates lab grading process.

    This class coordinates all the checks needed to grade a lab:
    1. Validate required files exist
    2. Check workflows directory exists
    3. Get latest commit
    4. Check for forbidden modifications
    5. Evaluate CI results
    6. Update spreadsheet (if checks pass)
    """

    def __init__(self, github_client: GitHubClient):
        """
        Initialize grader with GitHub client.

        Args:
            github_client: Configured GitHubClient instance
        """
        self.github = github_client

    def validate_repository(
        self,
        org: str,
        repo: str,
        required_files: list[str],
        check_forbidden: bool = True
    ) -> RepoValidationResult:
        """
        Validate repository structure and contents.

        Args:
            org: GitHub organization
            repo: Repository name
            required_files: List of required file paths
            check_forbidden: Whether to check for forbidden modifications

        Returns:
            RepoValidationResult with validation status
        """
        # Check required files
        if required_files:
            missing = self.github.check_required_files(org, repo, required_files)
            if missing:
                return RepoValidationResult(
                    valid=False,
                    error_message=f"Файлы не найдены: {', '.join(missing)}",
                    error_code="MISSING_FILES",
                    missing_files=missing
                )

        # Check workflows directory
        if not self.github.has_workflows_directory(org, repo):
            return RepoValidationResult(
                valid=False,
                error_message="Папка .github/workflows не найдена. CI не настроен",
                error_code="NO_WORKFLOWS"
            )

        # Get latest commit
        commit = self.github.get_latest_commit(org, repo)
        if commit is None:
            return RepoValidationResult(
                valid=False,
                error_message="Нет коммитов в репозитории",
                error_code="NO_COMMITS"
            )

        # Check forbidden modifications
        if check_forbidden and required_files:
            forbidden_patterns = get_default_forbidden_patterns(required_files)
            if forbidden_patterns:
                violations = check_forbidden_modifications(
                    commit.files,
                    forbidden_patterns
                )
                if violations:
                    return RepoValidationResult(
                        valid=False,
                        error_message=f"Запрещено изменять: {', '.join(violations)}",
                        error_code="FORBIDDEN_MODIFICATION",
                        forbidden_modifications=violations
                    )

        return RepoValidationResult(valid=True)

    def evaluate_ci(
        self,
        org: str,
        repo: str,
        commit_sha: str,
        configured_jobs: list[str] | None = None
    ) -> tuple[GradeResult | None, list[dict]]:
        """
        Evaluate CI check results for a commit.

        Args:
            org: GitHub organization
            repo: Repository name
            commit_sha: Commit SHA to check
            configured_jobs: List of job names to check (None = use defaults)

        Returns:
            Tuple of (GradeResult if terminal state, raw check_runs list)
        """
        check_runs_raw = self.github.get_check_runs(org, repo, commit_sha)

        if check_runs_raw is None:
            return GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message="Не удалось получить результаты CI",
                passed=None,
                error_code="CI_FETCH_ERROR"
            ), []

        if not check_runs_raw:
            return GradeResult(
                status=GradeStatus.PENDING,
                result=None,
                message="Нет активных CI-проверок ⏳",
                passed=None
            ), []

        # Parse and filter check runs
        check_runs = parse_check_runs(check_runs_raw)
        relevant_runs = filter_relevant_jobs(check_runs, configured_jobs)

        if not relevant_runs:
            return GradeResult(
                status=GradeStatus.PENDING,
                result=None,
                message="Нет активных CI-проверок ⏳",
                passed=None
            ), check_runs_raw

        # Evaluate results
        ci_result = evaluate_ci_results(relevant_runs)

        if ci_result.has_pending:
            return GradeResult(
                status=GradeStatus.PENDING,
                result=None,
                message="CI проверки ещё выполняются ⏳",
                passed=format_ci_result_string(ci_result.passed_count, ci_result.total_count),
                checks=ci_result.summary
            ), check_runs_raw

        return None, check_runs_raw  # No terminal state, continue to grading

    def grade(
        self,
        org: str,
        repo_prefix: str,
        github_username: str,
        lab_config: dict[str, Any],
    ) -> GradeResult:
        """
        Perform full grading for a lab submission.

        Args:
            org: GitHub organization
            repo_prefix: Repository prefix from lab config
            github_username: Student's GitHub username
            lab_config: Lab configuration dict

        Returns:
            GradeResult with grading outcome
        """
        repo_name = f"{repo_prefix}-{github_username}"
        required_files = lab_config.get("files", [])

        # 1. Validate repository
        validation = self.validate_repository(org, repo_name, required_files)
        if not validation.valid:
            return GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message=f"⚠️ {validation.error_message}",
                passed=None,
                error_code=validation.error_code
            )

        # 2. Get latest commit for CI check
        commit = self.github.get_latest_commit(org, repo_name)
        if commit is None:
            return GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message="Нет коммитов в репозитории",
                passed=None,
                error_code="NO_COMMITS"
            )

        # 3. Evaluate CI
        configured_jobs = get_ci_config_jobs(lab_config)
        ci_result, check_runs_raw = self.evaluate_ci(
            org, repo_name, commit.sha, configured_jobs
        )

        if ci_result is not None:
            return ci_result  # Terminal state (pending or error)

        # 4. Calculate final grade from CI results
        check_runs = parse_check_runs(check_runs_raw)
        relevant_runs = filter_relevant_jobs(check_runs, configured_jobs)
        ci_eval = evaluate_ci_results(relevant_runs)

        final_result = "v" if ci_eval.passed else "x"
        result_string = format_ci_result_string(ci_eval.passed_count, ci_eval.total_count)

        message = (
            "✅ Все проверки пройдены" if ci_eval.passed
            else "❌ Обнаружены ошибки"
        )

        return GradeResult(
            status=GradeStatus.UPDATED,  # Note: actual sheet update happens in main.py
            result=final_result,
            message=f"Результат CI: {message}",
            passed=result_string,
            checks=ci_eval.summary
        )


def build_repo_name(repo_prefix: str, github_username: str) -> str:
    """
    Build repository name from prefix and username.

    Args:
        repo_prefix: Repository prefix from lab config
        github_username: Student's GitHub username

    Returns:
        Full repository name
    """
    return f"{repo_prefix}-{github_username}"
