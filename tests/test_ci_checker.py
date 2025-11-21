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
        assert len(filtered) == 2
        names = [r.name for r in filtered]
        assert "test" in names
        assert "build" in names

    def test_no_defaults_found_returns_all(self):
        """If no default jobs match, return all jobs."""
        runs = [
            CheckRun("custom-job-1", "success", "url1"),
            CheckRun("custom-job-2", "failure", "url2"),
        ]
        filtered = filter_relevant_jobs(runs, None)
        assert len(filtered) == 2

    def test_partial_default_match(self):
        """Return only matching default jobs."""
        runs = [
            CheckRun("test", "success", "url1"),
            CheckRun("custom-job", "success", "url2"),
        ]
        filtered = filter_relevant_jobs(runs, None)
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
