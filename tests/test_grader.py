"""Tests for grading orchestrator."""
import pytest
from unittest.mock import MagicMock, patch

from grading.grader import LabGrader, LabConfig, GradeResult
from grading.github_client import GitHubClient, CommitInfo


class TestLabConfig:
    """Tests for LabConfig dataclass."""

    def test_from_dict_minimal(self):
        """Test creating config with minimal data."""
        config = {"github-prefix": "lab1"}
        lab_config = LabConfig.from_dict(config, 1)

        assert lab_config.github_prefix == "lab1"
        assert lab_config.short_name is None
        assert lab_config.required_files == []
        assert lab_config.ci_jobs is None

    def test_from_dict_full(self):
        """Test creating config with all fields."""
        config = {
            "github-prefix": "os-task2",
            "short-name": "ЛР2",
            "files": ["lab2.cpp", "Makefile"],
            "forbidden-modifications": ["test_main.py"],
            "ci": {"workflows": ["test", "lint"]},
            "taskid-max": 20,
            "taskid-shift": 4,
            "ignore-task-id": True,
            "penalty-max": 9,
        }
        lab_config = LabConfig.from_dict(config, 2)

        assert lab_config.github_prefix == "os-task2"
        assert lab_config.short_name == "ЛР2"
        assert lab_config.required_files == ["lab2.cpp", "Makefile"]
        assert lab_config.ci_jobs == ["test", "lint"]
        assert lab_config.taskid_max == 20
        assert lab_config.taskid_shift == 4
        assert lab_config.ignore_task_id is True
        assert lab_config.penalty_max == 9


class TestLabGraderCheckRepository:
    """Tests for LabGrader.check_repository."""

    @pytest.fixture
    def mock_github(self):
        """Create mock GitHub client."""
        return MagicMock(spec=GitHubClient)

    @pytest.fixture
    def grader(self, mock_github):
        """Create grader with mock client."""
        return LabGrader(mock_github)

    @pytest.fixture
    def basic_config(self):
        """Create basic lab config."""
        return LabConfig(github_prefix="lab1")

    def test_missing_required_file(self, grader, mock_github, basic_config):
        """Test error when required file is missing."""
        basic_config.required_files = ["main.cpp"]
        mock_github.check_required_files.return_value = ["main.cpp"]

        result = grader.check_repository("org", "lab1-user", basic_config)

        assert result is not None
        assert result.status == "error"
        assert "main.cpp" in result.message

    def test_all_files_exist(self, grader, mock_github, basic_config):
        """Test success when all required files exist."""
        basic_config.required_files = ["main.cpp"]
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])

        result = grader.check_repository("org", "lab1-user", basic_config)

        assert result is None  # No error

    def test_no_workflows(self, grader, mock_github, basic_config):
        """Test error when workflows directory is missing."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = False

        result = grader.check_repository("org", "lab1-user", basic_config)

        assert result is not None
        assert result.status == "error"
        assert "workflows" in result.message.lower()

    def test_no_commits(self, grader, mock_github, basic_config):
        """Test error when repository has no commits."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = None

        result = grader.check_repository("org", "lab1-user", basic_config)

        assert result is not None
        assert result.status == "error"
        assert "коммит" in result.message.lower()


class TestLabGraderCheckForbiddenFiles:
    """Tests for LabGrader.check_forbidden_files."""

    @pytest.fixture
    def mock_github(self):
        return MagicMock(spec=GitHubClient)

    @pytest.fixture
    def grader(self, mock_github):
        return LabGrader(mock_github)

    def test_no_violations(self, grader, mock_github):
        """Test success when no forbidden files are modified."""
        config = LabConfig(
            github_prefix="lab1",
            required_files=["test_main.py"],
        )
        mock_github.get_latest_commit.return_value = CommitInfo(
            sha="abc123",
            files=[{"filename": "main.cpp", "status": "modified"}]
        )

        result = grader.check_forbidden_files("org", "lab1-user", config)

        assert result is None

    def test_test_main_modified(self, grader, mock_github):
        """Test error when test_main.py is modified."""
        config = LabConfig(
            github_prefix="lab1",
            required_files=["test_main.py"],
        )
        mock_github.get_latest_commit.return_value = CommitInfo(
            sha="abc123",
            files=[{"filename": "test_main.py", "status": "modified"}]
        )

        result = grader.check_forbidden_files("org", "lab1-user", config)

        assert result is not None
        assert result.status == "error"
        assert "test_main.py" in result.message

    def test_tests_folder_modified(self, grader, mock_github):
        """Test error when tests/ folder is modified."""
        config = LabConfig(
            github_prefix="lab1",
            required_files=["test_main.py"],
        )
        mock_github.get_latest_commit.return_value = CommitInfo(
            sha="abc123",
            files=[{"filename": "tests/test_unit.py", "status": "removed"}]
        )

        result = grader.check_forbidden_files("org", "lab1-user", config)

        assert result is not None
        assert result.status == "error"
        assert "tests/" in result.message


class TestLabGraderEvaluateCI:
    """Tests for LabGrader.evaluate_ci."""

    @pytest.fixture
    def mock_github(self):
        return MagicMock(spec=GitHubClient)

    @pytest.fixture
    def grader(self, mock_github):
        return LabGrader(mock_github)

    @pytest.fixture
    def basic_config(self):
        return LabConfig(github_prefix="lab1")

    def test_all_checks_pass(self, grader, mock_github):
        """Test grade 'v' when all CI checks pass."""
        # Use explicit ci_jobs to control filtering
        config = LabConfig(github_prefix="lab1", ci_jobs=["test", "lint"])
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
            {"name": "lint", "conclusion": "success", "html_url": "url2", "completed_at": "2024-01-15T10:01:00Z"},
        ]

        result = grader.evaluate_ci("org", "lab1-user", config)

        assert result.status == "updated"
        assert result.result == "v"
        assert "2/2" in result.passed

    def test_some_checks_fail(self, grader, mock_github):
        """Test grade 'x' when some CI checks fail."""
        # Use explicit ci_jobs to control filtering
        config = LabConfig(github_prefix="lab1", ci_jobs=["test", "lint"])
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
            {"name": "lint", "conclusion": "failure", "html_url": "url2", "completed_at": "2024-01-15T10:01:00Z"},
        ]

        result = grader.evaluate_ci("org", "lab1-user", config)

        assert result.status == "updated"
        assert result.result == "x"
        assert "1/2" in result.passed

    def test_checks_pending(self, grader, mock_github, basic_config):
        """Test pending status when checks are still running."""
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": None, "html_url": "url1"},
        ]

        result = grader.evaluate_ci("org", "lab1-user", basic_config)

        assert result.status == "pending"
        assert result.result is None

    def test_no_check_runs(self, grader, mock_github, basic_config):
        """Test pending when no check runs found."""
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = []

        result = grader.evaluate_ci("org", "lab1-user", basic_config)

        assert result.status == "pending"

    def test_configured_jobs_filter(self, grader, mock_github):
        """Test that only configured jobs are evaluated."""
        config = LabConfig(github_prefix="lab1", ci_jobs=["test"])
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
            {"name": "lint", "conclusion": "failure", "html_url": "url2", "completed_at": "2024-01-15T10:01:00Z"},
        ]

        result = grader.evaluate_ci("org", "lab1-user", config)

        # Only "test" should be evaluated, which passed
        assert result.status == "updated"
        assert result.result == "v"
        assert "1/1" in result.passed


class TestLabGraderGrade:
    """Tests for LabGrader.grade (full workflow)."""

    @pytest.fixture
    def mock_github(self):
        return MagicMock(spec=GitHubClient)

    @pytest.fixture
    def grader(self, mock_github):
        return LabGrader(mock_github)

    @pytest.fixture
    def basic_config(self):
        return LabConfig(github_prefix="lab1")

    def test_full_success_flow(self, grader, mock_github, basic_config):
        """Test complete successful grading flow."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
        ]

        result = grader.grade("org", "student1", basic_config)

        assert result.status == "updated"
        assert result.result == "v"

    def test_cell_protection_rejects_update(self, grader, mock_github, basic_config):
        """Test that existing grade prevents update."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
        ]

        # Cell already has grade "v"
        result = grader.grade("org", "student1", basic_config, current_cell_value="v")

        assert result.status == "rejected"
        assert result.current_grade == "v"

    def test_cell_with_x_allows_update(self, grader, mock_github, basic_config):
        """Test that 'x' grade can be overwritten."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
        ]

        result = grader.grade("org", "student1", basic_config, current_cell_value="x")

        assert result.status == "updated"
        assert result.result == "v"

    def test_cell_with_question_mark_allows_update(self, grader, mock_github, basic_config):
        """Test that '?' prefixed grade can be overwritten."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
        ]

        result = grader.grade("org", "student1", basic_config, current_cell_value="?! Wrong TASKID")

        assert result.status == "updated"
        assert result.result == "v"

    def test_stops_on_repository_error(self, grader, mock_github, basic_config):
        """Test that repository errors stop the flow."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = False

        result = grader.grade("org", "student1", basic_config)

        assert result.status == "error"
        assert "workflows" in result.message.lower()
        # CI should not be called
        mock_github.get_check_runs.assert_not_called()
