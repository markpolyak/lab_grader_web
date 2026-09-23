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
from .score import extract_score_from_logs

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
    result: str | None  # Grade value: "v", "x", "v-3", "v@10.5", etc.
    message: str  # User-facing message
    passed: str | None  # "3/4 тестов пройдено"
    checks: list[str] = field(default_factory=list)  # CI check summaries
    current_grade: str | None = None  # Existing grade if rejected
    error_code: str | None = None  # For programmatic error handling
    score: str | None = None  # Score extracted from logs (e.g., "10.5")


@dataclass
class CIEvaluation:
    """Internal result of CI evaluation with full details."""
    grade_result: GradeResult  # The GradeResult to return
    ci_passed: bool  # Whether all CI checks passed
    successful_runs: list[CheckRun] = field(default_factory=list)  # For TASKID/score extraction
    latest_success_time: datetime | None = None  # For penalty calculation
    score: str | None = None  # Extracted score from logs


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
        - Repository exists (verified only once something else fails, see
          _missing_repo_result - it costs a request, and in the normal case
          the answer is already known)
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
                repo_missing = self._missing_repo_result(org, repo_name)
                if repo_missing:
                    return repo_missing
                return GradeResult(
                    status=GradeStatus.ERROR,
                    result=None,
                    message=f"⚠️ Файл {missing[0]} не найден в репозитории",
                    passed=None,
                    error_code="MISSING_FILES",
                )

        # Check workflows directory
        if not self.github.has_workflows_directory(org, repo_name):
            repo_missing = self._missing_repo_result(org, repo_name)
            if repo_missing:
                return repo_missing
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
            repo_missing = self._missing_repo_result(org, repo_name)
            if repo_missing:
                return repo_missing
            return GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message="Нет коммитов в репозитории",
                passed=None,
                error_code="NO_COMMITS",
            )

        return None

    def _missing_repo_result(self, org: str, repo_name: str) -> GradeResult | None:
        """
        Tell "there is no such repository" from "the repository lacks this".

        Every check below answers the same way for both: a missing file, a
        missing .github/workflows and a missing commit list are what a
        nonexistent repository looks like through the API. Reporting the
        first missing file for a repository that was never created sent a
        teacher looking for a file instead of for the repository (seen on a
        live test, where a stray spreadsheet cell produced the repository
        name "r-20").

        Called only after something has already failed, so the extra request
        never happens on the normal path.

        Returns:
            GradeResult with REPO_NOT_FOUND, or None if the repository is there
        """
        if self.github.repo_exists(org, repo_name):
            return None
        logger.warning(f"Repository {org}/{repo_name} does not exist")
        return GradeResult(
            status=GradeStatus.ERROR,
            result=None,
            message=f"⚠️ Репозиторий {org}/{repo_name} не найден",
            passed=None,
            error_code="REPO_NOT_FOUND",
        )

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

            # Extract job ID from html_url (format: .../job/12345)
            if "/job/" in run.html_url:
                try:
                    job_id = int(run.html_url.split("/job/")[-1].split("?")[0])
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
                logger.warning(f"  Job URL doesn't contain /job/: {run.html_url}")

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

    def check_score(
        self,
        org: str,
        repo_name: str,
        successful_runs: list[CheckRun],
        score_patterns: list[str],
    ) -> tuple[str | None, GradeResult | None]:
        """
        Extract score from job logs using configured patterns.

        Reads logs from successful CI jobs and extracts score using pattern list.
        If multiple occurrences found, they must all match (same value).

        Args:
            org: GitHub organization
            repo_name: Repository name
            successful_runs: List of successful CheckRun objects
            score_patterns: List of regex patterns to try

        Returns:
            Tuple of (score_string, error_result)
            - If successful: (score, None)
            - If error: (None, GradeResult with error)

        Note:
            Score patterns are tried in order. First matching pattern is used.
            Score must be consistent across all successful jobs.
        """
        logger.info(f"Score check for {repo_name}: checking {len(successful_runs)} successful job(s)")
        logger.debug(f"Score patterns configured: {len(score_patterns)} pattern(s)")

        score_found = None
        score_error = None

        # Try to get score from any successful job's logs
        for idx, run in enumerate(successful_runs, 1):
            logger.info(f"Checking job {idx}/{len(successful_runs)}: {run.name} (conclusion: {run.conclusion})")

            # Extract job ID from html_url (format: .../job/12345)
            if "/job/" in run.html_url:
                try:
                    job_id = int(run.html_url.split("/job/")[-1].split("?")[0])
                    logger.info(f"  Job ID: {job_id}, URL: {run.html_url}")
                except (ValueError, IndexError):
                    logger.warning(f"  Could not extract job_id from URL: {run.html_url}")
                    continue

                logs = self.github.get_job_logs(org, repo_name, job_id)
                if logs:
                    logger.info(f"  Logs fetched, size: {len(logs)} chars")
                    result = extract_score_from_logs(logs, score_patterns)
                    if result.found is not None:
                        logger.info(f"  ✓ Score found in logs: {result.found}")
                        score_found = result.found
                        break
                    elif result.error:
                        if "несколько" in result.error:
                            # Multiple different scores - this is an error
                            logger.error(f"  ✗ {result.error}")
                            score_error = result.error
                            break
                        else:
                            logger.info(f"  ✗ Score not found in this job's logs: {result.error}")
                else:
                    logger.warning(f"  Could not fetch logs for job {job_id}")
            else:
                logger.warning(f"  Job URL doesn't contain /job/: {run.html_url}")

        if score_error:
            return None, GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message=f"⚠️ {score_error}",
                passed=None,
                error_code="MULTIPLE_SCORES",
            )

        if score_found is None:
            return None, GradeResult(
                status=GradeStatus.ERROR,
                result=None,
                message="⚠️ Баллы не найдены в логах. Убедитесь, что программа выводит набранный балл.",
                passed=None,
                error_code="SCORE_NOT_FOUND",
            )

        logger.info(f"Score extracted successfully: {score_found}")
        return score_found, None

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

        # A job named in the config but absent from the filtered runs is
        # reported by evaluate_ci_results, so only the unconfigured case is
        # answered here.
        if not relevant_runs and not ci_jobs:
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.PENDING,
                    result=None,
                    message="Нет активных CI-проверок ⏳",
                    passed=None,
                ),
                ci_passed=False,
            )

        # Evaluate results. The unfiltered list goes in too: a job still
        # running outside the filter means a configured job may not exist yet.
        ci_result = evaluate_ci_results(relevant_runs, ci_jobs, check_runs)

        if ci_result.ignored:
            logger.info(
                f"Check runs not counted (skipped/neutral): {', '.join(ci_result.ignored)}"
            )

        if ci_result.config_mismatch:
            # Not one configured job name matches what GitHub reported: the
            # names in the config are stale, not the student's work. This must
            # not be written into the student's cell as "x".
            missing = ", ".join(ci_result.missing_jobs)
            logger.warning(f"No configured CI job matched the check runs: {missing}")
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.ERROR,
                    result=None,
                    message=(
                        f"⚠️ Ни одна из джоб, указанных в настройках курса, не найдена среди "
                        f"проверок CI: {missing}. Проверьте ci.workflows в конфигурации "
                        f"лабораторной работы."
                    ),
                    passed=None,
                    checks=ci_result.summary,
                    error_code="CI_JOBS_NOT_FOUND",
                ),
                ci_passed=False,
            )

        if ci_result.total_count == 0:
            # Every check run was skipped: nothing judged the work at all.
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.PENDING,
                    result=None,
                    message="Нет активных CI-проверок ⏳",
                    passed=None,
                    checks=ci_result.summary,
                ),
                ci_passed=False,
            )

        if ci_result.has_pending:
            pending = ", ".join(ci_result.pending_jobs)
            return CIEvaluation(
                grade_result=GradeResult(
                    status=GradeStatus.PENDING,
                    result=None,
                    message=(
                        f"CI-проверки ещё выполняются ⏳ ({pending})"
                        if pending else "CI-проверки ещё выполняются ⏳"
                    ),
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

        # Extract score if patterns are configured (only for passed CI)
        score_value = None
        if ci_result.passed:
            score_patterns = lab_config.get("score", {}).get("patterns", [])
            if score_patterns:
                logger.info(f"Score patterns configured, attempting to extract score")
                score_value, score_error = self.check_score(
                    org, repo_name,
                    successful_runs,
                    score_patterns,
                )
                if score_error:
                    logger.warning(f"Score extraction failed: {score_error.message}")
                    # If score is required but not found, return error
                    return CIEvaluation(
                        grade_result=score_error,
                        ci_passed=False,
                    )
                logger.info(f"Score extracted: {score_value}")

        return CIEvaluation(
            grade_result=GradeResult(
                status=GradeStatus.UPDATED,
                result=final_result,
                message=message,
                passed=result_string,
                checks=ci_result.summary,
                score=score_value,
            ),
            ci_passed=ci_result.passed,
            successful_runs=successful_runs,
            latest_success_time=ci_result.latest_success_time,
            score=score_value,
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
        decimal_separator: str = '.',
    ) -> GradeResult:
        """
        Perform full grading workflow.

        Steps:
        1. Check repository (files, workflows, commits)
        2. Check for forbidden modifications
        3. Evaluate CI results
        4. Extract score from logs (if configured)
        5. Validate TASKID (if required)
        6. Calculate penalty (if deadline provided)
        7. Format grade with score and penalty
        8. Check if grade can be updated (cell protection)

        Args:
            org: GitHub organization
            username: Student's GitHub username
            lab_config: Lab configuration dict from YAML
            current_cell_value: Current value in grade cell (for protection check)
            deadline: Deadline datetime for penalty calculation (None = no penalty)
            expected_taskid: Expected TASKID for validation (None = skip validation)
            decimal_separator: Decimal separator for score formatting ('.' or ',')

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
                logger.info(f"Calculated penalty: {penalty}")

        # Step 6: Format grade with score and penalty
        from .score import format_grade_with_score, format_score

        score_value = ci_evaluation.score
        final_result = "v"

        if score_value is not None:
            # Format score with correct separator and add penalty if present
            final_result = format_grade_with_score("v", score_value, penalty, decimal_separator)
            logger.info(f"Formatted grade with score: {final_result}")
        elif penalty > 0:
            # No score, but penalty exists
            final_result = format_grade_with_penalty("v", penalty)
            logger.info(f"Formatted grade with penalty: {final_result}")

        # Step 7: Check cell protection (if current value provided)
        if current_cell_value is not None:
            if not can_overwrite_cell(current_cell_value):
                return GradeResult(
                    status=GradeStatus.REJECTED,
                    result=current_cell_value,
                    message="⚠️ Работа уже была проверена ранее. Обратитесь к преподавателю для пересдачи.",
                    passed=ci_evaluation.grade_result.passed,
                    checks=ci_evaluation.grade_result.checks,
                    current_grade=current_cell_value,
                    score=score_value,
                )

        # Build final message
        message_parts = []
        if score_value is not None:
            formatted_score = format_score(score_value, decimal_separator)
            message_parts.append(f"Баллы: {formatted_score}")
        if penalty > 0:
            message_parts.append(f"штраф: -{penalty}")

        if message_parts:
            message = f"Результат CI: ✅ Все проверки пройдены ({', '.join(message_parts)})"
        else:
            message = ci_evaluation.grade_result.message

        return GradeResult(
            status=GradeStatus.UPDATED,
            result=final_result,
            message=message,
            passed=ci_evaluation.grade_result.passed,
            checks=ci_evaluation.grade_result.checks,
            score=score_value,
        )
