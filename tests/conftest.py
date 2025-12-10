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


@pytest.fixture(autouse=True)
def disable_rate_limiting(request):
    """Disable rate limiting in tests by patching the limiter.

    By default, rate limiting is disabled for all tests to avoid interference.
    To test rate limiting functionality, mark your test with @pytest.mark.rate_limit.
    """
    # Check if test is marked to test rate limiting
    if request.node.get_closest_marker("rate_limit"):
        # Don't disable rate limiting for this test
        yield
        return

    # Only patch if main module is actually imported
    # This avoids importing main (and its dependencies like gspread)
    # for tests that don't need it
    if 'main' not in sys.modules:
        # main not imported yet - no need to patch
        yield
        return

    # Patch limiter after main module is imported
    # We need to patch _check_request_limit to do nothing
    # and also ensure view_rate_limit is set to avoid AttributeError

    def noop_check(*args, **kwargs):
        """No-op function to disable rate limiting in tests."""
        # Extract request from args (it's the second argument: self, request, func, sync)
        if len(args) >= 2:
            request = args[1]
            # Set view_rate_limit to avoid AttributeError in wrapper
            if hasattr(request, 'state') and not hasattr(request.state, 'view_rate_limit'):
                request.state.view_rate_limit = None

    # Patch using the full path to the method
    patcher = patch('main.limiter._check_request_limit', noop_check, create=False)
    patcher.start()
    yield
    patcher.stop()


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line(
        "markers", "rate_limit: mark test to enable rate limiting (for testing rate limit functionality)"
    )


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
    
    # Initialize state attribute that slowapi expects
    # This is needed even when rate limiting is disabled
    if not hasattr(request.state, 'view_rate_limit'):
        request.state.view_rate_limit = None
    
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
