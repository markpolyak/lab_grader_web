"""
Lab grading orchestrator.

This module provides the main LabGrader class that orchestrates
all grading operations: GitHub checks, CI evaluation, and result formatting.
"""
import logging
from dataclasses import dataclass, field
from typing import Any

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
from .sheets_client import can_overwrite_cell

logger = logging.getLogger(__name__)


@dataclass
class GradeResult:
    """Result of a grading operation."""
    status: str  # "updated", "rejected", "pending", "error"
    result: str | None  # Grade value: "v", "x", "v-3", etc.
    message: str  # User-facing message
    passed: str | None  # "3/4 тестов пройдено"
    checks: list[str] = field(default_factory=list)  # CI check summaries
    current_grade: str | None = None  # Existing grade if rejected


class LabGrader:
    """
    Orchestrates lab grading operations.

    This class coordinates GitHub API calls, CI result evaluation,
    and grade determination without directly interacting with Google Sheets.
    Sheet updates are handled by the caller (endpoint).

    Lab config is passed as dict directly from YAML, no separate config class needed.
    """

    def __init__(self, github_client: GitHubClient):
        """
        Initialize grader with GitHub client.

        Args:
            github_client: Configured GitHubClient instance
        """
        self.github = github_client

    def check_repository(
        self,
        org: str,
        repo_name: str,
        lab_config: dict[str, Any]
    ) -> GradeResult | None:
        """
        Perform repository-level checks.

        Checks:
        - Required files exist
        - Workflows directory exists
        - Repository has commits

        Args:
            org: GitHub organization
            repo_name: Repository name
            lab_config: Lab configuration dict from YAML

        Returns:
            GradeResult with error if check fails, None if all pass
        """
        # Check required files
        required_files = lab_config.get("files", [])
        if required_files:
            missing = self.github.check_required_files(org, repo_name, required_files)
            if missing:
                return GradeResult(
                    status="error",
                    result=None,
                    message=f"⚠️ Файл {missing[0]} не найден в репозитории",
                    passed=None,
                )

        # Check workflows directory
        if not self.github.has_workflows_directory(org, repo_name):
            return GradeResult(
                status="error",
                result=None,
                message="⚠️ Папка .github/workflows не найдена. CI не настроен",
                passed=None,
            )

        # Check for commits
        commit = self.github.get_latest_commit(org, repo_name)
        if commit is None:
            return GradeResult(
                status="error",
                result=None,
                message="Нет коммитов в репозитории",
                passed=None,
            )

        return None

    def check_forbidden_files(
        self,
        org: str,
        repo_name: str,
        lab_config: dict[str, Any]
    ) -> GradeResult | None:
        """
        Check for forbidden file modifications.

        Args:
            org: GitHub organization
            repo_name: Repository name
            lab_config: Lab configuration dict from YAML

        Returns:
            GradeResult with error if violation found, None otherwise
        """
        commit = self.github.get_latest_commit(org, repo_name)
        if commit is None:
            return None

        # Get forbidden patterns from config or defaults
        required_files = lab_config.get("files", [])
        forbidden = lab_config.get("forbidden-modifications", []).copy()
        if not forbidden:
            forbidden = get_default_forbidden_patterns(required_files)

        if not forbidden:
            return None

        violations = check_forbidden_modifications(commit.files, forbidden)

        if violations:
            # Return error for first violation
            if "test_main.py" in violations:
                return GradeResult(
                    status="error",
                    result=None,
                    message="🚨 Нельзя изменять test_main.py",
                    passed=None,
                )
            for v in violations:
                if v.startswith("tests/"):
                    return GradeResult(
                        status="error",
                        result=None,
                        message="🚨 Нельзя изменять папку tests/",
                        passed=None,
                    )
            # Generic message for other forbidden files
            return GradeResult(
                status="error",
                result=None,
                message=f"🚨 Нельзя изменять файл {violations[0]}",
                passed=None,
            )

        return None

    def evaluate_ci(
        self,
        org: str,
        repo_name: str,
        lab_config: dict[str, Any]
    ) -> GradeResult:
        """
        Evaluate CI results for the latest commit.

        Args:
            org: GitHub organization
            repo_name: Repository name
            lab_config: Lab configuration dict from YAML

        Returns:
            GradeResult with CI evaluation
        """
        commit = self.github.get_latest_commit(org, repo_name)
        if commit is None:
            return GradeResult(
                status="error",
                result=None,
                message="Нет коммитов в репозитории",
                passed=None,
            )

        check_runs_data = self.github.get_check_runs(org, repo_name, commit.sha)

        if check_runs_data is None:
            return GradeResult(
                status="error",
                result=None,
                message="Проверки CI не найдены",
                passed=None,
            )

        if not check_runs_data:
            return GradeResult(
                status="pending",
                result=None,
                message="Нет активных CI-проверок ⏳",
                passed=None,
            )

        # Parse and filter check runs
        check_runs = parse_check_runs(check_runs_data)
        ci_jobs = get_ci_config_jobs(lab_config)
        relevant_runs = filter_relevant_jobs(check_runs, ci_jobs)

        if not relevant_runs:
            return GradeResult(
                status="pending",
                result=None,
                message="Нет активных CI-проверок ⏳",
                passed=None,
            )

        # Evaluate results
        ci_result = evaluate_ci_results(relevant_runs)

        if ci_result.has_pending:
            return GradeResult(
                status="pending",
                result=None,
                message="CI-проверки ещё выполняются ⏳",
                passed=format_ci_result_string(ci_result.passed_count, ci_result.total_count),
                checks=ci_result.summary,
            )

        # Determine grade
        final_result = "v" if ci_result.passed else "x"
        result_string = format_ci_result_string(ci_result.passed_count, ci_result.total_count)

        if ci_result.passed:
            message = "Результат CI: ✅ Все проверки пройдены"
        else:
            message = "Результат CI: ❌ Обнаружены ошибки"

        return GradeResult(
            status="updated",
            result=final_result,
            message=message,
            passed=result_string,
            checks=ci_result.summary,
        )

    def grade(
        self,
        org: str,
        username: str,
        lab_config: dict[str, Any],
        current_cell_value: str | None = None
    ) -> GradeResult:
        """
        Perform full grading workflow.

        Steps:
        1. Check repository (files, workflows, commits)
        2. Check for forbidden modifications
        3. Evaluate CI results
        4. Check if grade can be updated (cell protection)

        Args:
            org: GitHub organization
            username: Student's GitHub username
            lab_config: Lab configuration dict from YAML
            current_cell_value: Current value in grade cell (for protection check)

        Returns:
            GradeResult with final status and grade
        """
        github_prefix = lab_config.get("github-prefix", "")
        repo_name = f"{github_prefix}-{username}"
        logger.info(f"Grading repository: {org}/{repo_name}")

        # Step 1: Repository checks
        repo_error = self.check_repository(org, repo_name, lab_config)
        if repo_error:
            return repo_error

        # Step 2: Forbidden files check
        forbidden_error = self.check_forbidden_files(org, repo_name, lab_config)
        if forbidden_error:
            return forbidden_error

        # Step 3: CI evaluation
        ci_result = self.evaluate_ci(org, repo_name, lab_config)

        # If CI is pending or error, return as-is
        if ci_result.status != "updated":
            return ci_result

        # Step 4: Check cell protection (if current value provided)
        if current_cell_value is not None:
            if not can_overwrite_cell(current_cell_value):
                return GradeResult(
                    status="rejected",
                    result=current_cell_value,
                    message="⚠️ Работа уже была проверена ранее. Обратитесь к преподавателю для пересдачи.",
                    passed=ci_result.passed,
                    checks=ci_result.checks,
                    current_grade=current_cell_value,
                )

        return ci_result
