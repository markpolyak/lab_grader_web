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

from main import app, limiter


@pytest.mark.rate_limit
class TestRateLimiting:
    """Tests for rate limiting functionality."""

    @pytest.fixture(autouse=True)
    def reset_limiter(self):
        """Очищать in-memory счётчики, чтобы тесты не зависели от порядка запуска."""

        limiter.reset()
        yield
        limiter.reset()

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
        """Проверить сброс минутного окна без реального ожидания."""

        payload = {"login": "wrong", "password": "wrong"}
        for _ in range(5):
            assert client.post("/admin/login", json=payload).status_code == 401
        assert client.post("/admin/login", json=payload).status_code == 429

        # MemoryStorage удаляет счётчик при истёкшем timestamp. Переводим только
        # созданные этим тестом записи в истёкшее состояние, не запуская sleep.
        for key in list(limiter._storage.expirations):
            limiter._storage.expirations[key] = 0

        assert client.post("/admin/login", json=payload).status_code == 401

    def test_different_ips_have_separate_limits(self):
        """Проверить независимые счётчики для двух адресов клиента."""

        payload = {"login": "wrong", "password": "wrong"}
        with (
            TestClient(app, client=("203.0.113.21", 50000)) as first_client,
            TestClient(app, client=("203.0.113.22", 50000)) as second_client,
        ):
            for _ in range(5):
                assert first_client.post("/admin/login", json=payload).status_code == 401

            assert first_client.post("/admin/login", json=payload).status_code == 429
            assert second_client.post("/admin/login", json=payload).status_code == 401
