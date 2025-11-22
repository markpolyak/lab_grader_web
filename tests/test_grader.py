"""
Unit tests for grading/grader.py

Tests the LabGrader orchestrator.
"""
import pytest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.grader import (
    LabGrader,
    GradeResult,
    GradeStatus,
    RepoValidationResult,
    build_repo_name,
)
from grading.github_client import GitHubClient, CommitInfo


@pytest.fixture
def mock_github_client():
    """Create a mock GitHub client."""
    client = MagicMock(spec=GitHubClient)
    return client


@pytest.fixture
def sample_lab_config():
    """Sample lab configuration."""
    return {
        "github-prefix": "test-task1",
        "short-name": "ЛР1",
        "files": ["test_main.py"],
    }


@pytest.fixture
def sample_lab_config_with_ci():
    """Lab config with CI jobs specified."""
    return {
        "github-prefix": "test-task1",
        "short-name": "ЛР1",
        "files": ["test_main.py"],
        "ci": {"workflows": ["run-autograding-tests", "cpplint"]},
    }


class TestLabGraderValidateRepository:
    """Tests for validate_repository method."""

    def test_all_checks_pass(self, mock_github_client):
        """All validation checks pass."""
        mock_github_client.check_required_files.return_value = []
        mock_github_client.has_workflows_directory.return_value = True
        mock_github_client.get_latest_commit.return_value = CommitInfo(
            sha="abc123",
            files=[]
        )

        grader = LabGrader(mock_github_client)
        result = grader.validate_repository("org", "repo", ["test_main.py"])

        assert result.valid is True
        assert result.error_message is None

    def test_missing_required_files(self, mock_github_client):
        """Validation fails when required files missing."""
        mock_github_client.check_required_files.return_value = ["missing.py"]

        grader = LabGrader(mock_github_client)
        result = grader.validate_repository("org", "repo", ["missing.py"])

        assert result.valid is False
        assert result.error_code == "MISSING_FILES"
        assert "missing.py" in result.missing_files

    def test_no_workflows_directory(self, mock_github_client):
        """Validation fails when workflows directory missing."""
        mock_github_client.check_required_files.return_value = []
        mock_github_client.has_workflows_directory.return_value = False

        grader = LabGrader(mock_github_client)
        result = grader.validate_repository("org", "repo", [])

        assert result.valid is False
        assert result.error_code == "NO_WORKFLOWS"

    def test_no_commits(self, mock_github_client):
        """Validation fails when no commits."""
        mock_github_client.check_required_files.return_value = []
        mock_github_client.has_workflows_directory.return_value = True
        mock_github_client.get_latest_commit.return_value = None

        grader = LabGrader(mock_github_client)
        result = grader.validate_repository("org", "repo", [])

        assert result.valid is False
        assert result.error_code == "NO_COMMITS"

    def test_forbidden_modification(self, mock_github_client):
        """Validation fails when test_main.py modified."""
        mock_github_client.check_required_files.return_value = []
        mock_github_client.has_workflows_directory.return_value = True
        mock_github_client.get_latest_commit.return_value = CommitInfo(
            sha="abc123",
            files=[{"filename": "test_main.py", "status": "modified"}]
        )

        grader = LabGrader(mock_github_client)
        result = grader.validate_repository("org", "repo", ["test_main.py"])

        assert result.valid is False
        assert result.error_code == "FORBIDDEN_MODIFICATION"
        assert "test_main.py" in result.forbidden_modifications


class TestLabGraderEvaluateCI:
    """Tests for evaluate_ci method."""

    def test_no_check_runs(self, mock_github_client):
        """Pending when no check runs."""
        mock_github_client.get_check_runs.return_value = []

        grader = LabGrader(mock_github_client)
        result, raw = grader.evaluate_ci("org", "repo", "sha123")

        assert result.status == GradeStatus.PENDING
        assert "⏳" in result.message

    def test_check_runs_api_error(self, mock_github_client):
        """Error when API call fails."""
        mock_github_client.get_check_runs.return_value = None

        grader = LabGrader(mock_github_client)
        result, raw = grader.evaluate_ci("org", "repo", "sha123")

        assert result.status == GradeStatus.ERROR
        assert result.error_code == "CI_FETCH_ERROR"

    def test_pending_checks(self, mock_github_client):
        """Pending when some checks not complete."""
        mock_github_client.get_check_runs.return_value = [
            {"name": "run-autograding-tests", "conclusion": "success", "html_url": "url1"},
            {"name": "build", "conclusion": None, "html_url": "url2"},  # pending
        ]

        grader = LabGrader(mock_github_client)
        result, raw = grader.evaluate_ci("org", "repo", "sha123")

        assert result.status == GradeStatus.PENDING
        assert "⏳" in result.message

    def test_all_checks_complete(self, mock_github_client):
        """No terminal state when all checks complete."""
        mock_github_client.get_check_runs.return_value = [
            {"name": "run-autograding-tests", "conclusion": "success", "html_url": "url1"},
            {"name": "build", "conclusion": "success", "html_url": "url2"},
        ]

        grader = LabGrader(mock_github_client)
        result, raw = grader.evaluate_ci("org", "repo", "sha123")

        assert result is None  # No terminal state
        assert len(raw) == 2


class TestLabGraderGrade:
    """Tests for grade method."""

    def test_successful_grading(self, mock_github_client, sample_lab_config):
        """Successful grading flow."""
        mock_github_client.check_required_files.return_value = []
        mock_github_client.has_workflows_directory.return_value = True
        mock_github_client.get_latest_commit.return_value = CommitInfo(
            sha="abc123",
            files=[]
        )
        mock_github_client.get_check_runs.return_value = [
            {"name": "run-autograding-tests", "conclusion": "success", "html_url": "url1"},
        ]

        grader = LabGrader(mock_github_client)
        result = grader.grade("org", "test-task1", "student", sample_lab_config)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v"
        assert "1/1" in result.passed

    def test_failed_ci(self, mock_github_client, sample_lab_config):
        """Grading with failed CI."""
        mock_github_client.check_required_files.return_value = []
        mock_github_client.has_workflows_directory.return_value = True
        mock_github_client.get_latest_commit.return_value = CommitInfo(
            sha="abc123",
            files=[]
        )
        mock_github_client.get_check_runs.return_value = [
            {"name": "run-autograding-tests", "conclusion": "success", "html_url": "url1"},
            {"name": "build", "conclusion": "failure", "html_url": "url2"},
        ]

        grader = LabGrader(mock_github_client)
        result = grader.grade("org", "test-task1", "student", sample_lab_config)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "x"
        assert "1/2" in result.passed

    def test_validation_failure(self, mock_github_client, sample_lab_config):
        """Grading fails on validation."""
        mock_github_client.check_required_files.return_value = ["test_main.py"]

        grader = LabGrader(mock_github_client)
        result = grader.grade("org", "test-task1", "student", sample_lab_config)

        assert result.status == GradeStatus.ERROR
        assert result.error_code == "MISSING_FILES"

    def test_ci_pending(self, mock_github_client, sample_lab_config):
        """Grading returns pending when CI not complete."""
        mock_github_client.check_required_files.return_value = []
        mock_github_client.has_workflows_directory.return_value = True
        mock_github_client.get_latest_commit.return_value = CommitInfo(
            sha="abc123",
            files=[]
        )
        mock_github_client.get_check_runs.return_value = []

        grader = LabGrader(mock_github_client)
        result = grader.grade("org", "test-task1", "student", sample_lab_config)

        assert result.status == GradeStatus.PENDING


class TestBuildRepoName:
    """Tests for build_repo_name function."""

    def test_simple_name(self):
        """Build simple repo name."""
        name = build_repo_name("os-task2", "student123")
        assert name == "os-task2-student123"

    def test_with_special_chars(self):
        """Build name with special characters."""
        name = build_repo_name("ml-lab-1", "user-name")
        assert name == "ml-lab-1-user-name"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
