"""
Unit tests for grading/ci_checker.py

Tests CI result filtering and evaluation logic.
"""
import pytest
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.ci_checker import (
    CheckRun,
    CIResult,
    parse_check_runs,
    filter_relevant_jobs,
    evaluate_ci_results,
    get_ci_config_jobs,
    job_matches,
    format_ci_result_string,
    DEFAULT_JOB_NAMES,
)


class TestParseCheckRuns:
    """Tests for parse_check_runs function."""

    def test_parse_complete_check_run(self):
        """Parse check run with all fields."""
        data = [{
            "name": "test",
            "conclusion": "success",
            "html_url": "https://github.com/test/runs/1",
            "completed_at": "2024-01-15T10:00:00Z"
        }]
        result = parse_check_runs(data)
        assert len(result) == 1
        assert result[0].name == "test"
        assert result[0].conclusion == "success"
        assert result[0].html_url == "https://github.com/test/runs/1"
        assert result[0].completed_at is not None

    def test_parse_pending_check_run(self):
        """Parse check run without conclusion (pending)."""
        data = [{
            "name": "test",
            "conclusion": None,
            "html_url": "https://github.com/test/runs/1"
        }]
        result = parse_check_runs(data)
        assert result[0].conclusion is None
        assert result[0].completed_at is None

    def test_parse_empty_list(self):
        """Parse empty check runs list."""
        result = parse_check_runs([])
        assert result == []

    def test_parse_missing_fields(self):
        """Parse check run with missing optional fields."""
        data = [{"name": "test"}]
        result = parse_check_runs(data)
        assert result[0].name == "test"
        assert result[0].conclusion is None
        assert result[0].html_url == ""


class TestFilterRelevantJobs:
    """Tests for filter_relevant_jobs function."""

    def test_filter_by_config(self):
        """Filter by explicitly configured jobs."""
        runs = [
            CheckRun("run-autograding-tests", "success", "url1"),
            CheckRun("cpplint", "success", "url2"),
            CheckRun("other-job", "success", "url3"),
        ]
        filtered = filter_relevant_jobs(runs, ["run-autograding-tests", "cpplint"])
        assert len(filtered) == 2
        names = [r.name for r in filtered]
        assert "run-autograding-tests" in names
        assert "cpplint" in names
        assert "other-job" not in names

    def test_filter_with_empty_config(self):
        """Empty config list returns no jobs."""
        runs = [CheckRun("test", "success", "url1")]
        filtered = filter_relevant_jobs(runs, [])
        assert filtered == []

    def test_use_defaults_when_none(self):
        """Use default jobs when config is None."""
        runs = [
            CheckRun("test", "success", "url1"),
            CheckRun("random-job", "success", "url2"),
            CheckRun("build", "success", "url3"),
        ]
        filtered = filter_relevant_jobs(runs, None)
        # Only default jobs returned (test, build are in DEFAULT_JOB_NAMES)
        assert len(filtered) == 2
        names = [r.name for r in filtered]
        assert "test" in names
        assert "build" in names
        assert "random-job" not in names

    def test_no_defaults_found_returns_all(self):
        """If no default jobs match, return all jobs."""
        runs = [
            CheckRun("custom-job-1", "success", "url1"),
            CheckRun("custom-job-2", "failure", "url2"),
        ]
        filtered = filter_relevant_jobs(runs, None)
        assert len(filtered) == 2

    def test_partial_default_match(self):
        """Return only matching default jobs when some match."""
        runs = [
            CheckRun("test", "success", "url1"),
            CheckRun("custom-job", "success", "url2"),
        ]
        filtered = filter_relevant_jobs(runs, None)
        # Only "test" matches DEFAULT_JOB_NAMES
        assert len(filtered) == 1
        assert filtered[0].name == "test"


class TestEvaluateCIResults:
    """Tests for evaluate_ci_results function."""

    def test_all_passed(self):
        """All checks passed."""
        runs = [
            CheckRun("test", "success", "url1", datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)),
            CheckRun("lint", "success", "url2", datetime(2024, 1, 15, 10, 1, tzinfo=timezone.utc)),
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is True
        assert result.passed_count == 2
        assert result.total_count == 2
        assert result.has_pending is False
        assert len(result.summary) == 2
        assert "✅" in result.summary[0]

    def test_some_failed(self):
        """Some checks failed."""
        runs = [
            CheckRun("test", "success", "url1"),
            CheckRun("lint", "failure", "url2"),
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is False
        assert result.passed_count == 1
        assert result.total_count == 2

    def test_all_failed(self):
        """All checks failed."""
        runs = [
            CheckRun("test", "failure", "url1"),
            CheckRun("lint", "failure", "url2"),
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is False
        assert result.passed_count == 0

    def test_pending_checks(self):
        """Some checks still pending."""
        runs = [
            CheckRun("test", "success", "url1"),
            CheckRun("lint", None, "url2"),  # pending
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is False  # Not passed if any pending
        assert result.has_pending is True
        assert "⏳" in result.summary[1]

    def test_empty_check_runs(self):
        """Empty check runs list."""
        result = evaluate_ci_results([])
        assert result.passed is False
        assert result.total_count == 0
        assert result.has_pending is True

    def test_latest_success_time(self):
        """Track latest success time."""
        runs = [
            CheckRun("test", "success", "url1", datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)),
            CheckRun("lint", "success", "url2", datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc)),
        ]
        result = evaluate_ci_results(runs)
        assert result.latest_success_time == datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc)


class TestConclusionClasses:
    """Tests for how each GitHub conclusion is classified."""

    def test_skipped_job_is_not_counted(self):
        """A skipped job nobody configured drops out of the ratio entirely."""
        runs = [
            CheckRun("grade", "success", "url1"),
            CheckRun("personalize", "skipped", "url2"),
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is True
        assert result.has_pending is False
        assert result.passed_count == 1
        assert result.total_count == 1
        assert result.ignored == ["personalize"]
        assert "⏭️" in result.summary[1]

    def test_neutral_job_is_not_counted(self):
        """A neutral job declines to judge, so it is not counted either."""
        runs = [
            CheckRun("grade", "success", "url1"),
            CheckRun("style-hint", "neutral", "url2"),
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is True
        assert result.total_count == 1
        assert result.ignored == ["style-hint"]

    def test_all_checks_skipped_is_not_a_pass(self):
        """Nothing judged the work: pending, never a checkmark."""
        runs = [
            CheckRun("personalize", "skipped", "url1"),
            CheckRun("notify", "skipped", "url2"),
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is False
        assert result.has_pending is True
        assert result.total_count == 0
        assert result.ignored == ["personalize", "notify"]

    @pytest.mark.parametrize(
        "conclusion", ["failure", "timed_out", "cancelled", "stale", "action_required"]
    )
    def test_unsuccessful_conclusions_fail_instead_of_hanging(self, conclusion):
        """A finished run without success is a failure, not an endless wait."""
        runs = [
            CheckRun("grade", "success", "url1"),
            CheckRun("lint", conclusion, "url2"),
        ]
        result = evaluate_ci_results(runs)
        assert result.passed is False
        assert result.has_pending is False
        assert result.passed_count == 1
        assert result.total_count == 2
        assert "❌" in result.summary[1]

    def test_unknown_conclusion_fails_rather_than_pending(self):
        """A conclusion GitHub adds later must not freeze the grade."""
        runs = [CheckRun("grade", "brand_new_conclusion", "url1")]
        result = evaluate_ci_results(runs)
        assert result.passed is False
        assert result.has_pending is False
        assert result.total_count == 1

    def test_only_none_conclusion_is_pending(self):
        """Pending means exactly one thing: the run has not finished."""
        runs = [
            CheckRun("grade", "success", "url1"),
            CheckRun("lint", None, "url2"),
        ]
        result = evaluate_ci_results(runs)
        assert result.has_pending is True
        assert result.pending_jobs == ["lint"]


class TestConfiguredJobs:
    """Tests for evaluate_ci_results with jobs named in the lab config."""

    def test_all_configured_jobs_must_succeed(self):
        """Logical AND: one red job is enough to withhold the checkmark."""
        runs = [
            CheckRun("grade", "success", "url1"),
            CheckRun("cpplint", "failure", "url2"),
        ]
        result = evaluate_ci_results(runs, ["grade", "cpplint"])
        assert result.passed is False
        assert result.passed_count == 1
        assert result.total_count == 2

    def test_all_configured_jobs_green_passes(self):
        runs = [
            CheckRun("grade", "success", "url1"),
            CheckRun("cpplint", "success", "url2"),
        ]
        result = evaluate_ci_results(runs, ["grade", "cpplint"])
        assert result.passed is True
        assert result.passed_count == 2
        assert result.total_count == 2

    def test_configured_job_skipped_is_a_failure(self):
        """The config demands a success from this job; skipped is not one."""
        runs = [
            CheckRun("grade", "success", "url1"),
            CheckRun("cpplint", "skipped", "url2"),
        ]
        result = evaluate_ci_results(runs, ["grade", "cpplint"])
        assert result.passed is False
        assert result.total_count == 2
        assert result.ignored == []
        assert "❌" in result.summary[1]

    def test_a_missing_required_job_withholds_the_grade(self):
        """One configured job green, the other absent: no checkmark.

        The config lists the jobs that must succeed. A job that produced no
        result did not succeed, so the student is not passed - whatever the
        reason the check run is missing.
        """
        runs = [CheckRun("Test python scripts", "success", "url1")]
        result = evaluate_ci_results(runs, ["run-autograding-tests", "Test python scripts"])
        assert result.passed is False
        assert result.missing_jobs == ["run-autograding-tests"]
        assert result.has_pending is False
        assert "❌" in result.summary[1]

    def test_no_configured_name_matches(self):
        """Every name in the config is absent: nothing was verified."""
        runs = [CheckRun("unrelated", "success", "url1")]
        result = evaluate_ci_results([], ["grade", "cpplint"], runs)
        assert result.passed is False
        assert result.missing_jobs == ["grade", "cpplint"]
        assert result.has_pending is False

    def test_missing_required_job_waits_while_ci_is_starting(self):
        """Right after a push the jobs may simply not exist yet."""
        runs = [CheckRun("unrelated", None, "url1")]
        result = evaluate_ci_results([], ["grade", "cpplint"], runs)
        assert result.passed is False
        assert result.missing_jobs == []
        assert result.has_pending is True
        assert result.pending_jobs == ["grade", "cpplint"]

    def test_commit_without_check_runs_waits(self):
        """CI never started: waiting, not a verdict."""
        result = evaluate_ci_results([], ["grade", "cpplint"], [])
        assert result.passed is False
        assert result.missing_jobs == []
        assert result.has_pending is True
        assert result.total_count == 0

    def test_partial_match_waits_for_the_running_job(self):
        """A pending job among the matched runs keeps the whole check waiting."""
        runs = [CheckRun("grade", None, "url1")]
        result = evaluate_ci_results(runs, ["grade", "cpplint"])
        assert result.passed is False
        assert result.has_pending is True
        assert result.pending_jobs == ["grade", "cpplint"]

    def test_required_job_missing_while_another_finished(self):
        """No job is running any more, so the absent one is final."""
        runs = [CheckRun("grade", "success", "url1")]
        result = evaluate_ci_results(runs, ["grade", "cpplint"], runs)
        assert result.passed is False
        assert result.missing_jobs == ["cpplint"]
        assert result.has_pending is False


class TestJobNamePatterns:
    """Tests for glob patterns in ci.workflows job names."""

    def test_literal_name_matches_exactly(self):
        assert job_matches("grade", "grade") is True
        assert job_matches("grade", "grade (3.11)") is False

    def test_pattern_matches_runner_image_versions(self):
        """The same job under two windows-runner images."""
        pattern = "build (MSVC, Visual Studio *)"
        assert job_matches(pattern, "build (MSVC, Visual Studio 17 2022)") is True
        assert job_matches(pattern, "build (MSVC, Visual Studio 18 2026)") is True
        assert job_matches(pattern, "build (MINGW64, MinGW Makefiles)") is False

    def test_pattern_filters_check_runs(self):
        runs = [
            CheckRun("build (MSVC, Visual Studio 18 2026)", "success", "url1"),
            CheckRun("build (MINGW64, MinGW Makefiles)", "success", "url2"),
        ]
        relevant = filter_relevant_jobs(runs, ["build (MSVC, Visual Studio *)"])
        assert [run.name for run in relevant] == ["build (MSVC, Visual Studio 18 2026)"]

    def test_pattern_still_has_to_match_something(self):
        """A pattern matching nothing is a missing required job, as before."""
        runs = [CheckRun("run-autograding-tests", "success", "url1")]
        result = evaluate_ci_results(runs, ["run-autograding-tests", "build (MSVC, *)"], runs)
        assert result.passed is False
        assert result.missing_jobs == ["build (MSVC, *)"]

    def test_every_run_matching_a_pattern_must_succeed(self):
        """Two matrix jobs under one pattern: both are required."""
        runs = [
            CheckRun("build (MSVC, Visual Studio 18 2026)", "success", "url1"),
            CheckRun("build (MSVC, Visual Studio 17 2022)", "failure", "url2"),
        ]
        result = evaluate_ci_results(runs, ["build (MSVC, *)"], runs)
        assert result.passed is False
        assert result.passed_count == 1
        assert result.total_count == 2

    def test_skipped_run_matching_a_pattern_is_required_too(self):
        """A pattern names the job as surely as a literal does."""
        runs = [CheckRun("build (MSVC, Visual Studio 18 2026)", "skipped", "url1")]
        result = evaluate_ci_results(runs, ["build (MSVC, *)"], runs)
        assert result.passed is False
        assert result.ignored == []
        assert result.total_count == 1

    def test_os_lab3_config_covers_both_generations(self):
        """The real ОС-2026 ЛР3 set, old and new runner images alike."""
        jobs = ["run-autograding-tests", "build (MINGW64, MinGW Makefiles)",
                "build (MSVC, Visual Studio *)"]
        for msvc in ("build (MSVC, Visual Studio 17 2022)",
                     "build (MSVC, Visual Studio 18 2026)"):
            runs = [
                CheckRun("run-autograding-tests", "success", "url1"),
                CheckRun("build (MINGW64, MinGW Makefiles)", "success", "url2"),
                CheckRun(msvc, "success", "url3"),
            ]
            result = evaluate_ci_results(filter_relevant_jobs(runs, jobs), jobs, runs)
            assert result.passed is True, msvc
            assert result.total_count == 3


class TestControlWorkRegression:
    """The exact shape that froze every control work repository."""

    def test_unconfigured_lab_ignores_skipped_personalize(self):
        """Old-format config (ci: [- workflows]) must not hang on skipped."""
        runs = [
            CheckRun("personalize", "skipped", "url1"),
            CheckRun("grade", "success", "url2", datetime(2026, 9, 18, 3, 36, 46, tzinfo=timezone.utc)),
            CheckRun("check", "success", "url3"),
        ]
        relevant = filter_relevant_jobs(runs, get_ci_config_jobs({"ci": ["workflows"]}))
        result = evaluate_ci_results(relevant)
        assert result.passed is True
        assert result.has_pending is False
        assert result.passed_count == 2
        assert result.total_count == 2
        assert result.latest_success_time == datetime(2026, 9, 18, 3, 36, 46, tzinfo=timezone.utc)

    def test_configured_lab_grades_by_named_job(self):
        """New-format config narrows the run set to the grading job."""
        runs = [
            CheckRun("personalize", "skipped", "url1"),
            CheckRun("grade", "success", "url2"),
            CheckRun("check", "success", "url3"),
        ]
        jobs = get_ci_config_jobs({"ci": {"workflows": ["grade"]}})
        relevant = filter_relevant_jobs(runs, jobs)
        result = evaluate_ci_results(relevant, jobs)
        assert [run.name for run in relevant] == ["grade"]
        assert result.passed is True
        assert result.total_count == 1


class TestGetCIConfigJobs:
    """Tests for get_ci_config_jobs function."""

    def test_new_format_with_workflows(self):
        """Extract jobs from ci.workflows (new dict format)."""
        config = {"ci": {"workflows": ["run-autograding-tests", "cpplint"]}}
        result = get_ci_config_jobs(config)
        assert result == ["run-autograding-tests", "cpplint"]

    def test_new_format_with_jobs(self):
        """Extract jobs from ci.jobs (preferred new format)."""
        config = {"ci": {"jobs": ["test", "lint"]}}
        result = get_ci_config_jobs(config)
        assert result == ["test", "lint"]

    def test_jobs_preferred_over_workflows(self):
        """ci.jobs takes precedence over ci.workflows."""
        config = {"ci": {"jobs": ["new-test"], "workflows": ["old-test"]}}
        result = get_ci_config_jobs(config)
        assert result == ["new-test"]

    def test_old_format_list(self):
        """Old format ci: [- workflows] returns None."""
        config = {"ci": ["workflows"]}
        result = get_ci_config_jobs(config)
        assert result is None

    def test_no_ci_config(self):
        """No CI config returns None."""
        config = {}
        result = get_ci_config_jobs(config)
        assert result is None

    def test_empty_workflows_list(self):
        """Empty workflows list returns None."""
        config = {"ci": {"workflows": []}}
        result = get_ci_config_jobs(config)
        assert result is None


class TestFormatCIResultString:
    """Tests for format_ci_result_string function."""

    def test_all_passed(self):
        """Format when all tests passed."""
        result = format_ci_result_string(3, 3)
        assert result == "3/3 тестов пройдено"

    def test_some_failed(self):
        """Format when some tests failed."""
        result = format_ci_result_string(2, 5)
        assert result == "2/5 тестов пройдено"

    def test_none_passed(self):
        """Format when no tests passed."""
        result = format_ci_result_string(0, 3)
        assert result == "0/3 тестов пройдено"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
