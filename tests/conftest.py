"""
Pytest configuration and shared fixtures for testing.
"""
import pytest
import os
import sys
from unittest.mock import Mock, MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set required environment variables for testing."""
    monkeypatch.setenv("GITHUB_TOKEN", "test_github_token")
    monkeypatch.setenv("ADMIN_LOGIN", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "test_password")
    monkeypatch.setenv("SECRET_KEY", "test_secret_key")


@pytest.fixture
def sample_course_config():
    """Sample course configuration for testing."""
    return {
        "course": {
            "name": "Test Course",
            "semester": "Spring 2025",
        },
        "github": {
            "organization": "test-org",
        },
        "google": {
            "spreadsheet": "test_spreadsheet_id",
            "student-name-column": 2,
            "lab-column-offset": 1,
        },
        "labs": {
            "1": {
                "github-prefix": "test-task1",
                "short-name": "ЛР1",
                "files": ["test_main.py"],  # Required files that also trigger forbidden modification checks
            },
            "2": {
                "github-prefix": "test-task2",
                "short-name": "ЛР2",
                "taskid-max": 20,
                "taskid-shift": 4,
                "penalty-max": 9,
                "files": ["test_main.py"],
            },
        },
        "_meta": {
            "filename": "test-course.yaml",
            "id": "test-course",
        }
    }


@pytest.fixture
def mock_gspread():
    """Mock gspread client and worksheet."""
    with patch('main.gspread') as mock_gs:
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_worksheet = MagicMock()

        mock_gs.authorize.return_value = mock_client
        mock_client.open_by_key.return_value = mock_spreadsheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet

        # Default worksheet behavior
        mock_worksheet.row_values.return_value = ["№", "ФИО", "GitHub", "ЛР1", "ЛР2"]
        mock_worksheet.col_values.return_value = ["", "", "student1", "student2"]
        mock_worksheet.cell.return_value = MagicMock(value="")

        yield {
            'gspread': mock_gs,
            'client': mock_client,
            'spreadsheet': mock_spreadsheet,
            'worksheet': mock_worksheet
        }


@pytest.fixture
def mock_service_account_creds():
    """Mock ServiceAccountCredentials."""
    with patch('main.ServiceAccountCredentials') as mock_creds:
        mock_creds.from_json_keyfile_name.return_value = MagicMock()
        yield mock_creds


@pytest.fixture
def mock_request():
    """Mock FastAPI Request object for rate limiting."""
    from starlette.requests import Request
    
    # Create a real Request object with minimal scope
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }
    
    async def receive():
        return {"type": "http.request"}
    
    # Create a real Request instance
    request = Request(scope, receive)
    return request


@pytest.fixture
def github_api_success_responses():
    """Standard successful GitHub API responses."""
    return {
        'test_file': {'status': 200, 'json': {'name': 'test_main.py'}},
        'workflows': {'status': 200, 'json': [{'name': 'test.yml'}]},
        'commits': {'status': 200, 'json': [{'sha': 'abc123def456'}]},
        'commit_detail': {
            'status': 200,
            'json': {
                'sha': 'abc123def456',
                'files': [{'filename': 'main.py', 'status': 'modified'}]
            }
        },
        'check_runs': {
            'status': 200,
            'json': {
                'check_runs': [
                    {
                        'name': 'test',
                        'conclusion': 'success',
                        'html_url': 'https://github.com/test/runs/1',
                        'completed_at': '2024-01-15T10:00:00Z'
                    }
                ]
            }
        }
    }


@pytest.fixture
def github_api_all_checks_pass():
    """GitHub API responses where all CI checks pass."""
    return {
        'check_runs': {
            'check_runs': [
                {
                    'name': 'run-autograding-tests',
                    'conclusion': 'success',
                    'html_url': 'https://github.com/test/runs/1',
                    'completed_at': '2024-01-15T10:00:00Z'
                },
                {
                    'name': 'cpplint',
                    'conclusion': 'success',
                    'html_url': 'https://github.com/test/runs/2',
                    'completed_at': '2024-01-15T10:01:00Z'
                }
            ]
        }
    }


@pytest.fixture
def github_api_some_checks_fail():
    """GitHub API responses where some CI checks fail."""
    return {
        'check_runs': {
            'check_runs': [
                {
                    'name': 'run-autograding-tests',
                    'conclusion': 'success',
                    'html_url': 'https://github.com/test/runs/1',
                    'completed_at': '2024-01-15T10:00:00Z'
                },
                {
                    'name': 'cpplint',
                    'conclusion': 'failure',
                    'html_url': 'https://github.com/test/runs/2',
                    'completed_at': '2024-01-15T10:01:00Z'
                }
            ]
        }
    }
