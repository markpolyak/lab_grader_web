"""Tests for grading orchestrator."""
import pytest
from unittest.mock import MagicMock

from grading.grader import LabGrader, GradeResult, GradeStatus
from grading.github_client import GitHubClient, CommitInfo


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
        """Create basic lab config dict."""
        return {"github-prefix": "lab1"}

    def test_missing_required_file(self, grader, mock_github):
        """Test error when required file is missing."""
        config = {"github-prefix": "lab1", "files": ["main.cpp"]}
        mock_github.check_required_files.return_value = ["main.cpp"]

        result = grader.check_repository("org", "lab1-user", config)

        assert result is not None
        assert result.status == GradeStatus.ERROR
        assert "main.cpp" in result.message

    def test_all_files_exist(self, grader, mock_github):
        """Test success when all required files exist."""
        config = {"github-prefix": "lab1", "files": ["main.cpp"]}
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])

        result = grader.check_repository("org", "lab1-user", config)

        assert result is None  # No error

    def test_no_workflows(self, grader, mock_github, basic_config):
        """Test error when workflows directory is missing."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = False

        result = grader.check_repository("org", "lab1-user", basic_config)

        assert result is not None
        assert result.status == GradeStatus.ERROR
        assert "workflows" in result.message.lower()

    def test_no_commits(self, grader, mock_github, basic_config):
        """Test error when repository has no commits."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = None

        result = grader.check_repository("org", "lab1-user", basic_config)

        assert result is not None
        assert result.status == GradeStatus.ERROR
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
        config = {"github-prefix": "lab1", "forbidden-files": ["test_main.py"]}
        mock_github.get_all_modified_files.return_value = ["main.cpp", "lib.cpp"]

        result = grader.check_forbidden_files("org", "lab1-user", config)

        assert result is None

    def test_no_forbidden_files_config(self, grader, mock_github):
        """Test returns None when forbidden-files not configured."""
        config = {"github-prefix": "lab1"}  # No forbidden-files key

        result = grader.check_forbidden_files("org", "lab1-user", config)

        assert result is None
        mock_github.get_all_modified_files.assert_not_called()

    def test_test_main_modified(self, grader, mock_github):
        """Test warning when test_main.py is modified."""
        config = {"github-prefix": "lab1", "forbidden-files": ["test_main.py"]}
        mock_github.get_all_modified_files.return_value = ["main.cpp", "test_main.py"]

        result = grader.check_forbidden_files("org", "lab1-user", config)

        assert result is not None
        assert "test_main.py" in result.message
        assert result.violations == ["test_main.py"]

    def test_tests_folder_modified(self, grader, mock_github):
        """Test warning when tests/ folder is modified."""
        config = {"github-prefix": "lab1", "forbidden-files": ["tests/"]}
        mock_github.get_all_modified_files.return_value = ["main.cpp", "tests/test_unit.py"]

        result = grader.check_forbidden_files("org", "lab1-user", config)

        assert result is not None
        assert "tests/test_unit.py" in result.violations

    def test_multiple_violations(self, grader, mock_github):
        """Test warning with multiple forbidden files."""
        config = {"github-prefix": "lab1", "forbidden-files": ["test_main.py", "tests/"]}
        mock_github.get_all_modified_files.return_value = [
            "main.cpp", "test_main.py", "tests/helper.py"
        ]

        result = grader.check_forbidden_files("org", "lab1-user", config)

        assert result is not None
        assert len(result.violations) == 2
        assert "test_main.py" in result.violations
        assert "tests/helper.py" in result.violations


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
        return {"github-prefix": "lab1"}

    def test_all_checks_pass(self, grader, mock_github):
        """Test grade 'v' when all CI checks pass."""
        # Use explicit ci.workflows to control filtering
        config = {"github-prefix": "lab1", "ci": {"workflows": ["test", "lint"]}}
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
            {"name": "lint", "conclusion": "success", "html_url": "url2", "completed_at": "2024-01-15T10:01:00Z"},
        ]

        result = grader.evaluate_ci("org", "lab1-user", config)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v"
        assert "2/2" in result.passed

    def test_some_checks_fail(self, grader, mock_github):
        """Test grade 'x' when some CI checks fail."""
        # Use explicit ci.workflows to control filtering
        config = {"github-prefix": "lab1", "ci": {"workflows": ["test", "lint"]}}
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
            {"name": "lint", "conclusion": "failure", "html_url": "url2", "completed_at": "2024-01-15T10:01:00Z"},
        ]

        result = grader.evaluate_ci("org", "lab1-user", config)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "x"
        assert "1/2" in result.passed

    def test_checks_pending(self, grader, mock_github, basic_config):
        """Test pending status when checks are still running."""
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": None, "html_url": "url1"},
        ]

        result = grader.evaluate_ci("org", "lab1-user", basic_config)

        assert result.status == GradeStatus.PENDING
        assert result.result is None

    def test_no_check_runs(self, grader, mock_github, basic_config):
        """Test pending when no check runs found."""
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = []

        result = grader.evaluate_ci("org", "lab1-user", basic_config)

        assert result.status == GradeStatus.PENDING

    def test_configured_jobs_filter(self, grader, mock_github):
        """Test that only configured jobs are evaluated."""
        config = {"github-prefix": "lab1", "ci": {"workflows": ["test"]}}
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
            {"name": "lint", "conclusion": "failure", "html_url": "url2", "completed_at": "2024-01-15T10:01:00Z"},
        ]

        result = grader.evaluate_ci("org", "lab1-user", config)

        # Only "test" should be evaluated, which passed
        assert result.status == GradeStatus.UPDATED
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
        return {"github-prefix": "lab1"}

    def test_full_success_flow(self, grader, mock_github, basic_config):
        """Test complete successful grading flow."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": "2024-01-15T10:00:00Z"},
        ]

        result = grader.grade("org", "student1", basic_config)

        assert result.status == GradeStatus.UPDATED
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

        assert result.status == GradeStatus.REJECTED
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

        assert result.status == GradeStatus.UPDATED
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

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v"

    def test_stops_on_repository_error(self, grader, mock_github, basic_config):
        """Test that repository errors stop the flow."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = False

        result = grader.grade("org", "student1", basic_config)

        assert result.status == GradeStatus.ERROR
        assert "workflows" in result.message.lower()
        # CI should not be called
        mock_github.get_check_runs.assert_not_called()


class TestLabGraderPenalty:
    """Tests for penalty calculation integration."""

    @pytest.fixture
    def mock_github(self):
        return MagicMock(spec=GitHubClient)

    @pytest.fixture
    def grader(self, mock_github):
        return LabGrader(mock_github)

    def _setup_successful_ci(self, mock_github, completed_at="2024-03-20T10:00:00Z"):
        """Setup mocks for successful CI run."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {"name": "test", "conclusion": "success", "html_url": "url1", "completed_at": completed_at},
        ]

    def test_on_time_no_penalty(self, grader, mock_github):
        """Test no penalty when submitted before deadline."""
        from datetime import datetime, timezone

        self._setup_successful_ci(mock_github, completed_at="2024-03-14T10:00:00Z")
        deadline = datetime(2024, 3, 15, 23, 59, tzinfo=timezone.utc)
        config = {"github-prefix": "lab1", "penalty-max": 5}

        result = grader.grade("org", "student1", config, deadline=deadline)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v"
        assert "-" not in result.result

    def test_penalty_applied_late_submission(self, grader, mock_github):
        """Test penalty applied when submitted after deadline."""
        from datetime import datetime, timezone

        # Completed 10 days after deadline (2 weeks penalty)
        self._setup_successful_ci(mock_github, completed_at="2024-03-25T10:00:00Z")
        deadline = datetime(2024, 3, 15, 23, 59, tzinfo=timezone.utc)
        config = {"github-prefix": "lab1", "penalty-max": 9}

        result = grader.grade("org", "student1", config, deadline=deadline)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v-2"  # 10 days = 2 weeks
        assert "штраф" in result.message.lower()

    def test_penalty_capped_at_max(self, grader, mock_github):
        """Test penalty doesn't exceed penalty-max."""
        from datetime import datetime, timezone

        # Completed 100 days after deadline
        self._setup_successful_ci(mock_github, completed_at="2024-06-25T10:00:00Z")
        deadline = datetime(2024, 3, 15, 23, 59, tzinfo=timezone.utc)
        config = {"github-prefix": "lab1", "penalty-max": 5}

        result = grader.grade("org", "student1", config, deadline=deadline)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v-5"  # Capped at 5

    def test_no_deadline_no_penalty(self, grader, mock_github):
        """Test no penalty calculation when deadline not provided."""
        self._setup_successful_ci(mock_github, completed_at="2024-06-25T10:00:00Z")
        config = {"github-prefix": "lab1", "penalty-max": 5}

        result = grader.grade("org", "student1", config, deadline=None)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v"


class TestLabGraderTaskId:
    """Tests for TASKID validation integration."""

    @pytest.fixture
    def mock_github(self):
        return MagicMock(spec=GitHubClient)

    @pytest.fixture
    def grader(self, mock_github):
        return LabGrader(mock_github)

    def _setup_successful_ci(self, mock_github, job_id=12345):
        """Setup mocks for successful CI run."""
        mock_github.check_required_files.return_value = []
        mock_github.has_workflows_directory.return_value = True
        mock_github.get_latest_commit.return_value = CommitInfo(sha="abc123", files=[])
        mock_github.get_check_runs.return_value = [
            {
                "name": "test",
                "conclusion": "success",
                "html_url": f"https://github.com/org/repo/actions/runs/1/jobs/{job_id}",
                "completed_at": "2024-03-14T10:00:00Z"
            },
        ]

    def test_taskid_valid(self, grader, mock_github):
        """Test success when TASKID matches expected."""
        self._setup_successful_ci(mock_github)
        mock_github.get_job_logs.return_value = "2024-01-15T10:30:00.000Z TASKID is 5\n"
        config = {"github-prefix": "lab1", "taskid-max": 20}

        result = grader.grade("org", "student1", config, expected_taskid=5)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v"

    def test_taskid_mismatch(self, grader, mock_github):
        """Test error when TASKID doesn't match expected."""
        self._setup_successful_ci(mock_github)
        mock_github.get_job_logs.return_value = "2024-01-15T10:30:00.000Z TASKID is 10\n"
        config = {"github-prefix": "lab1", "taskid-max": 20}

        result = grader.grade("org", "student1", config, expected_taskid=5)

        assert result.status == GradeStatus.ERROR
        assert result.result == "?! Wrong TASKID!"
        assert "вариант" in result.message.lower()

    def test_taskid_not_found(self, grader, mock_github):
        """Test error when TASKID not found in logs."""
        self._setup_successful_ci(mock_github)
        mock_github.get_job_logs.return_value = "2024-01-15T10:30:00.000Z No taskid here\n"
        config = {"github-prefix": "lab1", "taskid-max": 20}

        result = grader.grade("org", "student1", config, expected_taskid=5)

        assert result.status == GradeStatus.ERROR
        assert result.result == "?! Wrong TASKID!"
        assert "не найден" in result.message.lower()

    def test_taskid_ignored_when_flag_set(self, grader, mock_github):
        """Test TASKID check skipped when ignore-task-id is True."""
        self._setup_successful_ci(mock_github)
        mock_github.get_job_logs.return_value = "2024-01-15T10:30:00.000Z No taskid\n"
        config = {"github-prefix": "lab1", "taskid-max": 20, "ignore-task-id": True}

        result = grader.grade("org", "student1", config, expected_taskid=5)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v"
        # Logs should not be fetched
        mock_github.get_job_logs.assert_not_called()

    def test_no_expected_taskid_skips_check(self, grader, mock_github):
        """Test TASKID check skipped when expected_taskid is None."""
        self._setup_successful_ci(mock_github)
        config = {"github-prefix": "lab1", "taskid-max": 20}

        result = grader.grade("org", "student1", config, expected_taskid=None)

        assert result.status == GradeStatus.UPDATED
        assert result.result == "v"
        # Logs should not be fetched
        mock_github.get_job_logs.assert_not_called()
