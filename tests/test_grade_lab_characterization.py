"""
Characterization tests for grade_lab endpoint.

These tests capture the CURRENT behavior of the grade_lab function.
They serve as a safety net during refactoring - if behavior changes,
these tests will fail, alerting us to unintended changes.

NOTE: These tests mock all external services (GitHub API, Google Sheets).
"""
import pytest
import responses
from unittest.mock import Mock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGradeLabCharacterization:
    """Characterization tests for grade_lab endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_env_vars):
        """Setup for each test."""
        pass

    @pytest.fixture
    def mock_get_course_by_id(self, sample_course_config):
        """Mock the get_course_by_id function."""
        with patch('main.get_course_by_id') as mock:
            mock.return_value = sample_course_config
            yield mock

    @responses.activate
    def test_success_all_checks_pass(
        self,
        mock_get_course_by_id,
        mock_gspread,
        mock_service_account_creds,
        sample_course_config
    ):
        """Test successful grading when all CI checks pass."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-testuser"

        # Mock GitHub API calls
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"name": "test_main.py"},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows",
            json=[{"name": "test.yml"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits",
            json=[{"sha": "abc123"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123",
            json={"sha": "abc123", "files": []},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123/check-runs",
            json={
                "check_runs": [
                    {"name": "test", "conclusion": "success", "html_url": "http://test"}
                ]
            },
            status=200
        )

        # Setup worksheet mock
        mock_gspread['worksheet'].row_values.return_value = ["№", "ФИО", "GitHub", "ЛР1"]
        mock_gspread['worksheet'].col_values.return_value = ["", "", "testuser"]

        # Import and call
        from main import grade_lab, GradeRequest
        request = GradeRequest(github="testuser")
        result = grade_lab("test-course", "group1", "ЛР1", request)

        assert result["status"] == "updated"
        assert result["result"] == "v"
        assert "1/1" in result["passed"]
        mock_gspread['worksheet'].update_cell.assert_called_once()

    @responses.activate
    def test_failure_some_checks_fail(
        self,
        mock_get_course_by_id,
        mock_gspread,
        mock_service_account_creds,
        sample_course_config
    ):
        """Test grading when some CI checks fail."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-testuser"

        # Mock GitHub API calls
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"name": "test_main.py"},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows",
            json=[{"name": "test.yml"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits",
            json=[{"sha": "abc123"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123",
            json={"sha": "abc123", "files": []},
            status=200
        )
        # Note: "build" is in DEFAULT_JOB_NAMES, so it will be checked along with "test"
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123/check-runs",
            json={
                "check_runs": [
                    {"name": "test", "conclusion": "success", "html_url": "http://test"},
                    {"name": "build", "conclusion": "failure", "html_url": "http://build"}
                ]
            },
            status=200
        )

        mock_gspread['worksheet'].row_values.return_value = ["№", "ФИО", "GitHub", "ЛР1"]
        mock_gspread['worksheet'].col_values.return_value = ["", "", "testuser"]

        from main import grade_lab, GradeRequest
        request = GradeRequest(github="testuser")
        result = grade_lab("test-course", "group1", "ЛР1", request)

        assert result["status"] == "updated"
        assert result["result"] == "x"
        assert "1/2" in result["passed"]

    @responses.activate
    def test_missing_test_main_py(
        self,
        mock_get_course_by_id,
        sample_course_config
    ):
        """Test error when test_main.py is missing."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-testuser"

        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"message": "Not Found"},
            status=404
        )

        from main import grade_lab, GradeRequest
        from fastapi import HTTPException

        request = GradeRequest(github="testuser")
        with pytest.raises(HTTPException) as exc_info:
            grade_lab("test-course", "group1", "ЛР1", request)

        assert exc_info.value.status_code == 400
        assert "test_main.py" in exc_info.value.detail

    @responses.activate
    def test_missing_workflows(
        self,
        mock_get_course_by_id,
        sample_course_config
    ):
        """Test error when .github/workflows is missing."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-testuser"

        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"name": "test_main.py"},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows",
            json={"message": "Not Found"},
            status=404
        )

        from main import grade_lab, GradeRequest
        from fastapi import HTTPException

        request = GradeRequest(github="testuser")
        with pytest.raises(HTTPException) as exc_info:
            grade_lab("test-course", "group1", "ЛР1", request)

        assert exc_info.value.status_code == 400
        assert "workflows" in exc_info.value.detail.lower()

    @responses.activate
    def test_no_commits(
        self,
        mock_get_course_by_id,
        sample_course_config
    ):
        """Test error when repository has no commits."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-testuser"

        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"name": "test_main.py"},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows",
            json=[{"name": "test.yml"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits",
            json=[],
            status=200
        )

        from main import grade_lab, GradeRequest
        from fastapi import HTTPException

        request = GradeRequest(github="testuser")
        with pytest.raises(HTTPException) as exc_info:
            grade_lab("test-course", "group1", "ЛР1", request)

        assert exc_info.value.status_code == 404
        assert "коммит" in exc_info.value.detail.lower()

    @responses.activate
    def test_forbidden_test_file_modification(
        self,
        mock_get_course_by_id,
        sample_course_config
    ):
        """Test error when student modifies test_main.py."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-testuser"

        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"name": "test_main.py"},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows",
            json=[{"name": "test.yml"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits",
            json=[{"sha": "abc123"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123",
            json={
                "sha": "abc123",
                "files": [{"filename": "test_main.py", "status": "modified"}]
            },
            status=200
        )

        from main import grade_lab, GradeRequest
        from fastapi import HTTPException

        request = GradeRequest(github="testuser")
        with pytest.raises(HTTPException) as exc_info:
            grade_lab("test-course", "group1", "ЛР1", request)

        assert exc_info.value.status_code == 403
        assert "test_main.py" in exc_info.value.detail

    @responses.activate
    def test_forbidden_tests_folder_modification(
        self,
        mock_get_course_by_id,
        sample_course_config
    ):
        """Test error when student modifies tests/ folder."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-testuser"

        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"name": "test_main.py"},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows",
            json=[{"name": "test.yml"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits",
            json=[{"sha": "abc123"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123",
            json={
                "sha": "abc123",
                "files": [{"filename": "tests/test_example.py", "status": "modified"}]
            },
            status=200
        )

        from main import grade_lab, GradeRequest
        from fastapi import HTTPException

        request = GradeRequest(github="testuser")
        with pytest.raises(HTTPException) as exc_info:
            grade_lab("test-course", "group1", "ЛР1", request)

        assert exc_info.value.status_code == 403
        assert "tests" in exc_info.value.detail.lower()

    @responses.activate
    def test_no_ci_checks_pending(
        self,
        mock_get_course_by_id,
        sample_course_config
    ):
        """Test that empty check_runs returns pending status."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-testuser"

        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"name": "test_main.py"},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows",
            json=[{"name": "test.yml"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits",
            json=[{"sha": "abc123"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123",
            json={"sha": "abc123", "files": []},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123/check-runs",
            json={"check_runs": []},
            status=200
        )

        from main import grade_lab, GradeRequest
        request = GradeRequest(github="testuser")
        result = grade_lab("test-course", "group1", "ЛР1", request)

        assert result["status"] == "pending"
        assert "⏳" in result["message"]

    @responses.activate
    def test_github_user_not_in_spreadsheet(
        self,
        mock_get_course_by_id,
        mock_gspread,
        mock_service_account_creds,
        sample_course_config
    ):
        """Test error when GitHub user not found in spreadsheet."""
        org = sample_course_config["github"]["organization"]
        repo_name = "test-task1-unknownuser"

        # Mock all GitHub calls to succeed
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py",
            json={"name": "test_main.py"},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows",
            json=[{"name": "test.yml"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits",
            json=[{"sha": "abc123"}],
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123",
            json={"sha": "abc123", "files": []},
            status=200
        )
        responses.add(
            responses.GET,
            f"https://api.github.com/repos/{org}/{repo_name}/commits/abc123/check-runs",
            json={"check_runs": [{"name": "test", "conclusion": "success", "html_url": "x"}]},
            status=200
        )

        # User not in spreadsheet
        mock_gspread['worksheet'].row_values.return_value = ["№", "ФИО", "GitHub", "ЛР1"]
        mock_gspread['worksheet'].col_values.return_value = ["", "", "other_user"]

        from main import grade_lab, GradeRequest
        from fastapi import HTTPException

        request = GradeRequest(github="unknownuser")
        with pytest.raises(HTTPException) as exc_info:
            grade_lab("test-course", "group1", "ЛР1", request)

        assert exc_info.value.status_code == 404
        assert "не найден" in exc_info.value.detail.lower()

    def test_missing_course_configuration(self):
        """Test error when course configuration is incomplete."""
        with patch('main.get_course_by_id') as mock:
            mock.return_value = {
                "github": {},  # Missing organization
                "google": {"spreadsheet": "test"},
                "labs": {},
                "_meta": {"id": "test"}
            }

            from main import grade_lab, GradeRequest
            from fastapi import HTTPException

            request = GradeRequest(github="testuser")
            with pytest.raises(HTTPException) as exc_info:
                grade_lab("test-course", "group1", "ЛР1", request)

            assert exc_info.value.status_code == 400


class TestGradeLabResponseFormat:
    """Tests for response format consistency."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_env_vars):
        pass

    @responses.activate
    def test_success_response_format(
        self,
        mock_gspread,
        mock_service_account_creds,
        sample_course_config
    ):
        """Verify success response has all expected fields."""
        with patch('main.get_course_by_id', return_value=sample_course_config):
            org = sample_course_config["github"]["organization"]
            repo_name = "test-task1-testuser"

            # Setup all mocks
            responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}/contents/test_main.py", json={}, status=200)
            responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}/contents/.github/workflows", json=[], status=200)
            responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}/commits", json=[{"sha": "abc"}], status=200)
            responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}/commits/abc", json={"files": []}, status=200)
            responses.add(responses.GET, f"https://api.github.com/repos/{org}/{repo_name}/commits/abc/check-runs",
                         json={"check_runs": [{"name": "t", "conclusion": "success", "html_url": "x"}]}, status=200)

            mock_gspread['worksheet'].row_values.return_value = ["", "", "GitHub"]
            mock_gspread['worksheet'].col_values.return_value = ["", "", "testuser"]

            from main import grade_lab, GradeRequest
            request = GradeRequest(github="testuser")
            result = grade_lab("test-course", "group1", "ЛР1", request)

            # Verify response structure
            assert "status" in result
            assert "result" in result
            assert "message" in result
            assert "passed" in result
            assert "checks" in result
            assert isinstance(result["checks"], list)


class TestParseLabId:
    """Tests for lab ID parsing function."""

    def test_parse_cyrillic_lab_id(self, mock_env_vars):
        """Test parsing Cyrillic lab IDs."""
        from main import parse_lab_id

        assert parse_lab_id("ЛР1") == 1
        assert parse_lab_id("ЛР2") == 2
        assert parse_lab_id("ЛР10") == 10

    def test_parse_latin_lab_id(self, mock_env_vars):
        """Test parsing Latin lab IDs."""
        from main import parse_lab_id

        assert parse_lab_id("lab1") == 1
        assert parse_lab_id("Lab2") == 2

    def test_parse_numeric_only(self, mock_env_vars):
        """Test parsing numeric-only lab IDs."""
        from main import parse_lab_id

        assert parse_lab_id("1") == 1
        assert parse_lab_id("123") == 123

    def test_parse_invalid_lab_id(self, mock_env_vars):
        """Test error on invalid lab ID."""
        from main import parse_lab_id
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            parse_lab_id("abc")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
