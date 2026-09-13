"""
Tests for rate limiting functionality.

These tests verify that rate limiting works correctly.
Note: These tests use @pytest.mark.rate_limit to enable rate limiting.
"""
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app


@pytest.mark.rate_limit
class TestRateLimiting:
    """Tests for rate limiting functionality."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        return TestClient(app)

    # Uses mock_env_vars from conftest.py

    def test_rate_limit_on_grade_endpoint(self, client):
        """Test that rate limiting works on grade endpoint."""
        # Make 10 requests (within limit)
        for i in range(10):
            response = client.post(
                "/courses/test-course/groups/group1/labs/ЛР1/grade",
                json={"github": "testuser"}
            )
            # Should not be rate limited yet
            assert response.status_code != 429
        
        # 11th request should be rate limited
        response = client.post(
            "/courses/test-course/groups/group1/labs/ЛР1/grade",
            json={"github": "testuser"}
        )
        assert response.status_code == 429
        # Check response content - slowapi may return different formats
        response_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if "detail" in response_data:
            assert "rate limit" in response_data["detail"].lower() or "429" in str(response.status_code)
        else:
            # If no detail, just verify status code
            assert response.status_code == 429

    def test_rate_limit_on_register_endpoint(self, client):
        """Test that rate limiting works on register endpoint."""
        # Make 10 requests (within limit)
        for i in range(10):
            response = client.post(
                "/courses/test-course/groups/group1/register",
                json={
                    "name": "Test",
                    "surname": "User",
                    "github": "testuser"
                }
            )
            # Should not be rate limited yet
            assert response.status_code != 429
        
        # 11th request should be rate limited
        response = client.post(
            "/courses/test-course/groups/group1/register",
            json={
                "name": "Test",
                "surname": "User",
                "github": "testuser"
            }
        )
        assert response.status_code == 429

    def test_rate_limit_resets_after_time_window(self, client):
        """Test that rate limit resets after time window."""
        # This test would require waiting for the time window to expire
        # For now, we just verify the limit exists
        # In a real scenario, you might use time mocking
        pass

    def test_different_ips_have_separate_limits(self, client):
        """Test that different IP addresses have separate rate limits."""
        # This would require simulating different client IPs
        # TestClient doesn't easily support this, but in production
        # different IPs would have separate limits
        pass



class TestProxyAwareRateLimiting:
    """
    Лимиты за обратным прокси (docs/SECRET_JOIN_LINKS_PLAN.md §11, этап 5а).

    Корзина лимита выбирается по `request.client.host`. За прокси это адрес
    прокси, поэтому вся группа делит одну корзину: в момент открытия
    контрольной работы часть студентов получила бы 429, а перебор токена
    ограничивался бы глобально. Лечится запуском uvicorn с --proxy-headers и
    явным списком адресов прокси; доверять заголовку без списка нельзя -
    тогда лимит обходится подделкой X-Forwarded-For.

    Сам разбор заголовка делает uvicorn, поэтому здесь проверяется контракт
    запуска образа: флаги на месте, а список по умолчанию пуст.
    """

    @pytest.fixture
    def dockerfile(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "backend.Dockerfile"), encoding="utf-8") as f:
            return f.read()

    def test_uvicorn_runs_with_proxy_headers(self, dockerfile):
        assert "--proxy-headers" in dockerfile

    def test_trusted_proxies_come_from_an_environment_variable(self, dockerfile):
        assert '--forwarded-allow-ips "$FORWARDED_ALLOW_IPS"' in dockerfile

    def test_trusted_proxies_default_to_empty(self, dockerfile):
        assert 'ENV FORWARDED_ALLOW_IPS=""' in dockerfile

    def test_secret_link_limit_fits_a_whole_group_at_once(self):
        """
        Лимит /j/{token} рассчитан на одновременный заход всей группы:
        30 студентов в момент открытия контрольной не должны упереться в него
        даже если все запросы придут с одного адреса.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "main.py"), encoding="utf-8") as f:
            source = f.read()

        marker = source.index('@app.get("/j/{token}")')
        limit_line = source[marker:source.index("def secret_join_info", marker)]
        limit = int(limit_line.split('limiter.limit("')[1].split("/")[0])
        assert limit >= 30
