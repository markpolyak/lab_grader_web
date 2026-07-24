"""Настоящие проверки rate limit всех публичных endpoint сценария `/join`."""

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import main


@pytest.fixture(autouse=True)
def reset_rate_limit_storage():
    """Изолировать счётчики запросов между тестами без ожидания реального времени."""

    main.limiter.reset()
    yield
    main.limiter.reset()


@pytest.fixture
def configured_join(monkeypatch):
    """Подменить только чтение курса, не обращаясь к YAML или GitHub."""

    config = main.JoinLabConfig(
        course_id="test-course",
        course_name="Test course",
        lab_id="01",
        lab_name="ЛР0.1",
        organization="test-org",
        github_prefix="task-01",
        template_owner="teacher-org",
        template_repository="template",
    )
    monkeypatch.setattr(main, "get_join_lab_config", lambda course_id, lab_id: config)
    monkeypatch.setattr(main, "GITHUB_OAUTH_CLIENT_ID", None)
    monkeypatch.setattr(main, "GITHUB_OAUTH_CLIENT_SECRET", None)
    return config


@pytest.mark.rate_limit
def test_join_info_returns_429_after_sixty_requests(configured_join):
    with TestClient(main.app, client=("203.0.113.10", 50000)) as client:
        for _ in range(60):
            assert client.get("/join/test-course/01").status_code == 200

        assert client.get("/join/test-course/01").status_code == 429


@pytest.mark.rate_limit
def test_join_start_returns_429_after_ten_requests(configured_join):
    with TestClient(main.app, client=("203.0.113.11", 50000)) as client:
        for _ in range(10):
            response = client.get(
                "/join/test-course/01/start",
                follow_redirects=False,
            )
            assert response.status_code == 303

        assert client.get(
            "/join/test-course/01/start",
            follow_redirects=False,
        ).status_code == 429


@pytest.mark.rate_limit
def test_join_callback_query_cannot_bypass_twenty_request_limit():
    with TestClient(main.app, client=("203.0.113.12", 50000)) as client:
        for index in range(20):
            response = client.get(
                "/join/callback",
                params={"code": f"code-{index}", "state": f"state-{index}"},
                follow_redirects=False,
            )
            assert response.status_code == 303

        response = client.get(
            "/join/callback",
            params={"code": "new-code", "state": "new-state"},
            follow_redirects=False,
        )
        assert response.status_code == 429


@pytest.mark.rate_limit
def test_arbitrary_forwarded_for_header_cannot_bypass_limit(configured_join):
    with TestClient(main.app, client=("203.0.113.13", 50000)) as client:
        for index in range(10):
            response = client.get(
                "/join/test-course/01/start",
                headers={"X-Forwarded-For": f"198.51.100.{index + 1}"},
                follow_redirects=False,
            )
            assert response.status_code == 303

        response = client.get(
            "/join/test-course/01/start",
            headers={"X-Forwarded-For": "198.51.100.250"},
            follow_redirects=False,
        )
        assert response.status_code == 429


@pytest.mark.rate_limit
def test_trusted_proxy_separates_real_client_addresses(configured_join):
    proxy_app = ProxyHeadersMiddleware(main.app, trusted_hosts=["trusted-proxy"])
    with TestClient(
        proxy_app,
        client=("trusted-proxy", 50000),
    ) as client:
        for _ in range(10):
            response = client.get(
                "/join/test-course/01/start",
                headers={"X-Forwarded-For": "198.51.100.10"},
                follow_redirects=False,
            )
            assert response.status_code == 303

        assert client.get(
            "/join/test-course/01/start",
            headers={"X-Forwarded-For": "198.51.100.10"},
            follow_redirects=False,
        ).status_code == 429

        assert client.get(
            "/join/test-course/01/start",
            headers={"X-Forwarded-For": "198.51.100.11"},
            follow_redirects=False,
        ).status_code == 303
