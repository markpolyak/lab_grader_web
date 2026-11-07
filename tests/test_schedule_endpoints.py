import yaml
import pytest
from fastapi.testclient import TestClient

import main as main_module


@pytest.fixture
def schedule_admin_client(tmp_path, monkeypatch):
    courses_dir = tmp_path / "courses"
    schedules_dir = tmp_path / "schedules"
    courses_dir.mkdir()
    schedules_dir.mkdir()
    (courses_dir / "test-course.yaml").write_text(
        yaml.safe_dump(
            {
                "course": {
                    "groups": [
                        {"id": 4432},
                        {"id": "4433", "guap_group_id": "guap-4433"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main_module, "COURSES_DIR", str(courses_dir))
    monkeypatch.setattr(main_module, "SCHEDULES_DIR", str(schedules_dir))
    monkeypatch.setattr(main_module.limiter, "enabled", False)

    client = TestClient(main_module.app)
    client.cookies.set(
        "admin_session",
        main_module.signer.sign(main_module.ADMIN_LOGIN.encode()).decode(),
    )
    return client


def test_schedule_groups_endpoint_reads_course_config(schedule_admin_client):
    response = schedule_admin_client.get(
        "/admin/courses/test-course/schedule/groups"
    )

    assert response.status_code == 200
    assert response.json() == {
        "course_id": "test-course",
        "groups": [
            {"id": "4432", "guap_group_id": "4432"},
            {"id": "4433", "guap_group_id": "guap-4433"},
        ],
    }


def test_refresh_all_endpoint_returns_partial_results(
    schedule_admin_client, monkeypatch
):
    result = {
        "course_id": "test-course",
        "filename": "test-course.yaml",
        "refreshed": {"4432": 5},
        "errors": {"4433": "group missing from snapshot"},
    }
    monkeypatch.setattr(
        main_module, "refresh_all_groups", lambda **_kwargs: result
    )

    response = schedule_admin_client.post(
        "/admin/courses/test-course/schedule/refresh-all"
    )

    assert response.status_code == 207
    assert response.json() == result


def test_schedule_groups_endpoint_requires_admin():
    response = TestClient(main_module.app).get(
        "/admin/courses/test-course/schedule/groups"
    )

    assert response.status_code == 401
