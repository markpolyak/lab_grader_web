"""
Lab grading orchestrator.

This module provides the main LabGrader class that orchestrates
all grading operations: GitHub checks, CI evaluation, and result formatting.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
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
    CheckRun,
)
from .sheets_client import can_overwrite_cell
from .penalty import calculate_penalty, format_grade_with_penalty, PenaltyStrategy
from .taskid import extract_taskid_from_logs, calculate_expected_taskid, validate_taskid

logger = logging.getLogger(__name__)


class GradeStatus(Enum):
    """Possible grading outcomes."""
    UPDATED = "updated"      # Grade successfully determined
    REJECTED = "rejected"    # Cell protected, grade not written
    PENDING = "pending"      # CI checks not complete
    ERROR = "error"          # Error during grading


@dataclass
class GradeResult:
    """Result of a grading operation."""
    status: GradeStatus
    result: str | None  # Grade value: "v", "x", "v-3", etc.
    message: str  # User-facing message
    passed: str | None  # "3/4 тестов пройдено"
    checks: list[str] = field(default_factory=list)  # CI check summaries
    current_grade: str | None = None  # Existing grade if rejected
    error_code: str | None = None  # For programmatic error handling


@dataclass
class CIEvaluation:
    """Internal result of CI evaluation with full details."""
    grade_result: GradeResult  # The GradeResult to return
    ci_passed: bool  # Whether all CI checks passed
    successful_runs: list[CheckRun] = field(default_factory=list)  # For TASKID extraction
    latest_success_time: datetime | None = None  # For penalty calculation


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
                    status=GradeStatus.ERROR,
                    result=None,
                    message=f"⚠️ Файл {missing[0]} не найден в репозитории",
                    passed=None,
                    error_code="MISSING_FILES",
                )

        # Check workflows directory
        if not self.github.has_workflows_directory(org, repo_name):
            return GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message="⚠️ Папка .github/workflows не найдена. CI не настроен",
                passed=None,
                error_code="NO_WORKFLOWS",
            )

        # Check for commits
        commit = self.github.get_latest_commit(org, repo_name)
        if commit is None:
            return GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message="Нет коммитов в репозитории",
                passed=None,
                error_code="NO_COMMITS",
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
                    status=GradeStatus.ERROR,
                    result=None,
                    message="🚨 Нельзя изменять test_main.py",
                    passed=None,
                    error_code="FORBIDDEN_MODIFICATION",
                )
            for v in violations:
                if v.startswith("tests/"):
                    return GradeResult(
                        status=GradeStatus.ERROR,
                        result=None,
                        message="🚨 Нельзя изменять папку tests/",
                        passed=None,
                        error_code="FORBIDDEN_MODIFICATION",
                    )
            # Generic message for other forbidden files
            return GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message=f"🚨 Нельзя изменять файл {violations[0]}",
                passed=None,
                error_code="FORBIDDEN_MODIFICATION",
            )

        return None

    def check_taskid(
        self,
        org: str,
        repo_name: str,
        successful_runs: list[CheckRun],
        expected_taskid: int,
    ) -> GradeResult | None:
        """
        Check TASKID from job logs matches expected value.

        Reads logs from successful CI jobs and extracts TASKID.
        If TASKID doesn't match expected, returns error result.

        Args:
            org: GitHub organization
            repo_name: Repository name
            successful_runs: List of successful CheckRun objects
            expected_taskid: Expected TASKID for this student

        Returns:
            GradeResult with error if TASKID mismatch, None if OK
        """
        logger.info(f"TASKID check for {repo_name}: checking {len(successful_runs)} successful job(s)")
        logger.info(f"Expected TASKID: {expected_taskid}")

        taskid_found = None
        taskid_error = None

        # Try to get TASKID from any successful job's logs
        for run in successful_runs:
            logger.info(f"Checking job: {run.name} (conclusion: {run.conclusion})")

            # Extract job ID from html_url (format: .../jobs/12345)
            if "/jobs/" in run.html_url:
                try:
                    job_id = int(run.html_url.split("/jobs/")[-1].split("?")[0])
                    logger.info(f"  Job ID: {job_id}, URL: {run.html_url}")
                except (ValueError, IndexError):
                    logger.warning(f"  Could not extract job_id from URL: {run.html_url}")
                    continue

                logs = self.github.get_job_logs(org, repo_name, job_id)
                if logs:
                    logger.info(f"  Logs fetched, size: {len(logs)} chars")
                    result = extract_taskid_from_logs(logs)
                    if result.found is not None:
                        logger.info(f"  ✓ TASKID found in logs: {result.found}")
                        taskid_found = result.found
                        break
                    elif result.error:
                        if "несколько" in result.error:
                            # Multiple different TASKIDs - this is an error
                            logger.error(f"  ✗ {result.error}")
                            taskid_error = result.error
                            break
                        else:
                            logger.info(f"  ✗ TASKID not found in this job's logs: {result.error}")
                else:
                    logger.warning(f"  Could not fetch logs for job {job_id}")
            else:
                logger.warning(f"  Job URL doesn't contain /jobs/: {run.html_url}")

        if taskid_error:
            return GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message=f"⚠️ {taskid_error}",
                passed=None,
                error_code="MULTIPLE_TASKIDS",
            )

        if taskid_found is None:
            return GradeResult(
                status=GradeStatus.ERROR,
                result="?! Wrong TASKID!",
                message="⚠️ TASKID не найден в логах. Убедитесь, что программа выводит номер варианта.",
                passed=None,
                error_code="TASKID_NOT_FOUND",
            )

        is_valid, error_msg = validate_taskid(taskid_found, expected_taskid)
        if not is_valid:
            logger.warning(f"Wrong TASKID for {repo_name}: found {taskid_found}, expected {expected_taskid}")
            return GradeResult(
                status=GradeStatus.ERROR,
                result="?! Wrong TASKID!",
                message=f"⚠️ {error_msg}. Вы выполнили чужой вариант!",
                passed=None,
                error_code="WRONG_TASKID",
            )

        logger.info(f"TASKID validated: {taskid_found} matches expected {expected_taskid}")
        return None

    def _evaluate_ci_internal(
        self,
        org: str,
        repo_name: str,
        lab_config: dict[str, Any]
    ) -> CIEvaluation:
        """
        Evaluate CI results with full details for internal use.

        Returns CIEvaluation with successful_runs and latest_success_time
        for TASKID validation and penalty calculation.

        Args:
            org: GitHub organization
            repo_name: Repository name
            lab_config: Lab configuration dict from YAML

        Returns:
            CIEvaluation with full CI details
        """
        commit = self.github.get_latest_commit(org, repo_name)
        if commit is None:
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.ERROR,
                    result=None,
                    message="Нет коммитов в репозитории",
                    passed=None,
                ),
                ci_passed=False,
            )

        check_runs_data = self.github.get_check_runs(org, repo_name, commit.sha)

        if check_runs_data is None:
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.ERROR,
                    result=None,
                    message="Проверки CI не найдены",
                    passed=None,
                ),
                ci_passed=False,
            )

        if not check_runs_data:
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.PENDING,
                    result=None,
                    message="Нет активных CI-проверок ⏳",
                    passed=None,
                ),
                ci_passed=False,
            )

        # Parse and filter check runs
        check_runs = parse_check_runs(check_runs_data)
        logger.info(f"Total check runs found: {len(check_runs)}")
        for run in check_runs:
            logger.debug(f"  Check run: {run.name} (conclusion: {run.conclusion})")

        ci_jobs = get_ci_config_jobs(lab_config)
        if ci_jobs:
            logger.info(f"CI jobs configured in lab config: {ci_jobs}")
        else:
            logger.info("No specific CI jobs configured - will use all relevant jobs")

        relevant_runs = filter_relevant_jobs(check_runs, ci_jobs)
        logger.info(f"Relevant check runs after filtering: {len(relevant_runs)}")
        for run in relevant_runs:
            logger.info(f"  Relevant job: {run.name} (conclusion: {run.conclusion})")

        if not relevant_runs:
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.PENDING,
                    result=None,
                    message="Нет активных CI-проверок ⏳",
                    passed=None,
                ),
                ci_passed=False,
            )

        # Evaluate results
        ci_result = evaluate_ci_results(relevant_runs)

        if ci_result.has_pending:
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.PENDING,
                    result=None,
                    message="CI-проверки ещё выполняются ⏳",
                    passed=format_ci_result_string(ci_result.passed_count, ci_result.total_count),
                    checks=ci_result.summary,
                ),
                ci_passed=False,
            )

        # Get successful runs for TASKID extraction
        successful_runs = [run for run in relevant_runs if run.conclusion == "success"]
        logger.info(f"Successful runs for TASKID extraction: {len(successful_runs)}")
        for run in successful_runs:
            logger.info(f"  Success job: {run.name}")

        # Determine grade
        final_result = "v" if ci_result.passed else "x"
        result_string = format_ci_result_string(ci_result.passed_count, ci_result.total_count)

        if ci_result.passed:
            message = "Результат CI: ✅ Все проверки пройдены"
        else:
            message = "Результат CI: ❌ Обнаружены ошибки"

        return CIEvaluation(
            grade_result=GradeResult(
                status=GradeStatus.UPDATED,
                result=final_result,
                message=message,
                passed=result_string,
                checks=ci_result.summary,
            ),
            ci_passed=ci_result.passed,
            successful_runs=successful_runs,
            latest_success_time=ci_result.latest_success_time,
        )

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
        evaluation = self._evaluate_ci_internal(org, repo_name, lab_config)
        return evaluation.grade_result

    def grade(
        self,
        org: str,
        username: str,
        lab_config: dict[str, Any],
        current_cell_value: str | None = None,
        deadline: datetime | None = None,
        expected_taskid: int | None = None,
    ) -> GradeResult:
        """
        Perform full grading workflow.

        Steps:
        1. Check repository (files, workflows, commits)
        2. Check for forbidden modifications
        3. Evaluate CI results
        4. Validate TASKID (if required)
        5. Calculate penalty (if deadline provided)
        6. Check if grade can be updated (cell protection)

        Args:
            org: GitHub organization
            username: Student's GitHub username
            lab_config: Lab configuration dict from YAML
            current_cell_value: Current value in grade cell (for protection check)
            deadline: Deadline datetime for penalty calculation (None = no penalty)
            expected_taskid: Expected TASKID for validation (None = skip validation)

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

        # Step 3: CI evaluation (use internal method for full details)
        ci_evaluation = self._evaluate_ci_internal(org, repo_name, lab_config)

        # If CI is pending or error, return as-is
        if ci_evaluation.grade_result.status != GradeStatus.UPDATED:
            return ci_evaluation.grade_result

        # If CI failed, return failure without TASKID/penalty checks
        if not ci_evaluation.ci_passed:
            return ci_evaluation.grade_result

        # Step 4: Validate TASKID (if required)
        ignore_taskid = lab_config.get("ignore-task-id", False)
        if expected_taskid is not None and not ignore_taskid:
            taskid_error = self.check_taskid(
                org, repo_name,
                ci_evaluation.successful_runs,
                expected_taskid,
            )
            if taskid_error:
                return taskid_error

        # Step 5: Calculate penalty (if deadline provided)
        final_result = "v"  # CI passed
        penalty = 0
        penalty_max = lab_config.get("penalty-max", 0)

        if deadline is not None and ci_evaluation.latest_success_time is not None:
            # Get penalty strategy from config (default: WEEKLY)
            strategy_name = lab_config.get("penalty-strategy", "weekly")
            try:
                strategy = PenaltyStrategy(strategy_name)
            except ValueError:
                strategy = PenaltyStrategy.WEEKLY

            penalty = calculate_penalty(
                completed_at=ci_evaluation.latest_success_time,
                deadline=deadline,
                penalty_max=penalty_max,
                strategy=strategy,
            )

            if penalty > 0:
                final_result = format_grade_with_penalty("v", penalty)
                logger.info(f"Applied penalty {penalty} for late submission: {final_result}")

        # Step 6: Check cell protection (if current value provided)
        if current_cell_value is not None:
            if not can_overwrite_cell(current_cell_value):
                return GradeResult(
                    status=GradeStatus.REJECTED,
                    result=current_cell_value,
                    message="⚠️ Работа уже была проверена ранее. Обратитесь к преподавателю для пересдачи.",
                    passed=ci_evaluation.grade_result.passed,
                    checks=ci_evaluation.grade_result.checks,
                    current_grade=current_cell_value,
                )

        # Build final message
        if penalty > 0:
            message = f"Результат CI: ✅ Все проверки пройдены (штраф: -{penalty})"
        else:
            message = ci_evaluation.grade_result.message

        return GradeResult(
            status=GradeStatus.UPDATED,
            result=final_result,
            message=message,
            passed=ci_evaluation.grade_result.passed,
            checks=ci_evaluation.grade_result.checks,
        )
