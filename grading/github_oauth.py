"""Минимальный серверный GitHub OAuth flow для идентификации студента.

Полученный здесь OAuth-токен намеренно не передаётся вызывающему коду. Он нужен
приложению ровно для одной операции: узнать у GitHub, какой аккаунт подтвердил
запрос. Репозиторий создаётся и доступ выдаётся отдельным серверным токеном,
указанным в ``GITHUB_TOKEN``.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

import requests


class GitHubOAuthError(Exception):
    """Классифицированная ошибка GitHub OAuth с безопасным публичным кодом."""

    def __init__(self, code: str, log_message: str):
        super().__init__(log_message)
        self.code = code
        self.log_message = log_message


@dataclass(frozen=True)
class GitHubOAuthConfig:
    """Значения, необходимые для GitHub OAuth Web Application Flow."""

    client_id: str
    client_secret: str
    callback_url: str


class GitHubOAuthClient:
    """Идентифицировать GitHub-пользователя через OAuth authorization code."""

    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
    CURRENT_USER_URL = "https://api.github.com/user"
    REQUEST_TIMEOUT = (3.05, 10)

    def __init__(self, config: GitHubOAuthConfig):
        self.config = config

    def build_authorization_url(self, state: str) -> str:
        """Собрать redirect браузера, запросив только доступ к данным профиля.

        Web-слой создаёт и подписывает ``state``. Клиент передаёт значение без
        изменений, чтобы callback связал подтверждённый GitHub-аккаунт именно с
        тем курсом и лабораторной, для которых была начата авторизация.
        """

        query = urlencode(
            {
                "client_id": self.config.client_id,
                "redirect_uri": self.config.callback_url,
                "scope": "read:user",
                "state": state,
            }
        )
        return f"{self.AUTHORIZE_URL}?{query}"

    def get_verified_username(self, code: str) -> str:
        """Обменять ``code`` и вернуть login, полученный через ``GET /user``.

        Access token остаётся локальной переменной, не попадает в исключения и
        не возвращается другому слою. Это исключает его случайное сохранение в
        ответах приложения, redirect URL или обычных логах.
        """

        access_token = self._exchange_code(code)
        try:
            return self._get_username(access_token)
        finally:
            # Строку Python нельзя гарантированно стереть из памяти, однако
            # удаление ссылки явно ограничивает время жизни токена и исключает
            # его повторное использование при дальнейшей работе с репозиторием.
            access_token = None

    def _exchange_code(self, code: str) -> str:
        try:
            response = requests.post(
                self.ACCESS_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "code": code,
                    "redirect_uri": self.config.callback_url,
                },
                timeout=self.REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise GitHubOAuthError(
                "oauth_unavailable",
                f"GitHub token exchange request failed: {exc.__class__.__name__}",
            ) from exc

        # GitHub может вернуть OAuth-ошибку в JSON даже с HTTP 200, поэтому тело
        # ответа необходимо проверять независимо от HTTP-кода.
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubOAuthError(
                "oauth_unavailable",
                f"GitHub token exchange returned non-JSON response ({response.status_code})",
            ) from exc

        if not isinstance(payload, dict):
            raise GitHubOAuthError(
                "oauth_unavailable",
                f"GitHub token exchange returned an unexpected JSON value ({response.status_code})",
            )

        if response.status_code == 429 or response.status_code >= 500:
            raise GitHubOAuthError(
                "oauth_unavailable",
                f"GitHub token exchange is temporarily unavailable ({response.status_code})",
            )

        access_token = payload.get("access_token")
        if response.status_code != 200 or not isinstance(access_token, str) or not access_token:
            github_error = payload.get("error", "missing_access_token")
            raise GitHubOAuthError(
                "oauth_failed",
                f"GitHub rejected OAuth code exchange: {github_error} ({response.status_code})",
            )

        return access_token

    def _get_username(self, access_token: str) -> str:
        try:
            response = requests.get(
                self.CURRENT_USER_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
                timeout=self.REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise GitHubOAuthError(
                "oauth_unavailable",
                f"GitHub current-user request failed: {exc.__class__.__name__}",
            ) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise GitHubOAuthError(
                "oauth_unavailable",
                f"GitHub current-user endpoint is temporarily unavailable ({response.status_code})",
            )

        if response.status_code != 200:
            raise GitHubOAuthError(
                "oauth_failed",
                f"GitHub current-user request failed with status {response.status_code}",
            )

        try:
            username = response.json().get("login")
        except (ValueError, AttributeError) as exc:
            raise GitHubOAuthError(
                "oauth_failed",
                "GitHub current-user response did not contain a JSON object",
            ) from exc

        if not isinstance(username, str) or not username.strip():
            raise GitHubOAuthError(
                "oauth_failed",
                "GitHub current-user response did not contain a login",
            )

        return username.strip()
