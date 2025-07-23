from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_admin_login_success():
    """
    Проверяет успешную аутентификацию администратора при корректных логине и пароле.
    """
    response = client.post("/api/admin/login", json={
        "login": "admin",
        "password": "password"
    })
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


def test_admin_login_failure():
    """
    Проверяет, что при некорректных логине и пароле возвращается статус 401 (неавторизован)
    """
    response = client.post("/api/admin/login", json={
        "login": "wrong",
        "password": "wrong"
    })
    assert response.status_code == 401


def test_logout_without_login():
    """
    Проверяет, что выход из системы корректно очищает куки, даже если пользователь не был залогинен.
    """
    response = client.post("/api/admin/logout")
    assert response.status_code == 200
    assert response.json() == {"message": "Logged out"}
    assert "admin_session" not in response.cookies or not response.cookies.get("admin_session")


def test_check_auth_success():
    """
    Проверяет успешную проверку авторизации при наличии действительного токена сессии администратора.
    """
    login_response = client.post("/api/admin/login", json={
        "login": "admin",
        "password": "password"
    })
    assert login_response.status_code == 200
    assert login_response.json()["authenticated"] is True

    client.cookies.set("admin_session", login_response.cookies.get("admin_session"))

    check_response = client.get("/api/admin/check-auth")
    assert check_response.status_code == 200
    assert check_response.json()["authenticated"] is True


def test_check_auth_missing_cookie():
    """
    Проверяет поведение при отсутствии cookie сессии —
    должен вернуться статус 401 и сообщение о её отсутствии.
    """
    client.cookies.clear()
    response = client.get("/api/admin/check-auth")
    assert response.status_code == 401
    assert response.json()["detail"] == "Нет сессии"


def test_check_auth_invalid_cookie():
    """
    Проверяет реакцию системы на поддельный или недействительный токен авторизации —
    ожидается 401 и сообщение об ошибке.
    """
    client.cookies.set("admin_session", "fake-invalid-token")
    response = client.get("/api/admin/check-auth")
    assert response.status_code == 401
    assert "Невалидная" in response.json()["detail"]


def test_logout_after_login():
    """
    Проверяет, что после логина и последующего выхода из системы куки сессии корректно удаляются.
    """
    client.cookies.clear()

    login_response = client.post("/api/admin/login", json={
        "login": "admin",
        "password": "password"
    })
    assert login_response.status_code == 200

    assert any(c.name == "admin_session" for c in client.cookies.jar)

    logout_response = client.post("/api/admin/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "Logged out"

    assert not any(c.name == "admin_session" for c in client.cookies.jar)

