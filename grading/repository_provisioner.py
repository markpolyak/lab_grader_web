"""Создание репозитория студента и восстановление приглашений в коллабораторы.

Модуль намеренно не зависит от FastAPI и OAuth. Он получает подтверждённый
GitHub login и выполняет операции с репозиторием только через серверный
``GITHUB_TOKEN``. Явная граница не позволяет случайно использовать OAuth-токен
студента для действий с правами организации.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests


class RepositoryProvisionError(Exception):
    """Ошибка GitHub со стабильным публичным кодом и закрытым описанием для логов."""

    def __init__(self, code: str, log_message: str, status_code: int | None = None):
        super().__init__(log_message)
        self.code = code
        self.log_message = log_message
        self.status_code = status_code


@dataclass(frozen=True)
class ProvisionResult:
    """Результат, возвращаемый callback после завершения обязательных операций."""

    organization: str
    repository: str
    repository_url: str
    created: bool
    access_action: str


class GitHubRepositoryClient:
    """GitHub REST-клиент только для подготовки студенческих репозиториев."""

    BASE_URL = "https://api.github.com"
    REQUEST_TIMEOUT = (3.05, 15)

    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }

    def repository_exists(self, organization: str, repository: str) -> bool:
        response = self._request(
            "get",
            f"/repos/{quote(organization, safe='')}/{quote(repository, safe='')}",
        )
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise self._api_error("repository_lookup_failed", "check repository", response)

    def generate_repository(
        self,
        template_owner: str,
        template_repository: str,
        organization: str,
        repository: str,
    ) -> None:
        response = self._request(
            "post",
            (
                f"/repos/{quote(template_owner, safe='')}/"
                f"{quote(template_repository, safe='')}/generate"
            ),
            json={
                "owner": organization,
                "name": repository,
                "private": True,
                "include_all_branches": False,
            },
        )
        if response.status_code == 201:
            return

        code = "template_unavailable" if response.status_code == 404 else "repository_create_failed"
        raise self._api_error(code, "generate repository from template", response)

    def is_direct_collaborator(
        self,
        organization: str,
        repository: str,
        username: str,
    ) -> bool:
        """Найти пользователя среди прямых коллабораторов на всех страницах ответа."""

        page = 1
        expected_login = username.casefold()
        collaborators_url = (
            f"/repos/{quote(organization, safe='')}/{quote(repository, safe='')}/collaborators"
        )

        while True:
            # Параметр affiliation поддерживается endpoint списка. Значение
            # direct исключает доступ через команды и базовые права организации.
            response = self._request(
                "get",
                collaborators_url,
                params={"affiliation": "direct", "per_page": 100, "page": page},
            )
            if response.status_code != 200:
                raise self._api_error(
                    "access_check_failed",
                    "list direct collaborators",
                    response,
                )

            try:
                collaborators = response.json()
            except ValueError as exc:
                raise RepositoryProvisionError(
                    "access_check_failed",
                    "GitHub collaborator list was not valid JSON",
                    response.status_code,
                ) from exc

            if not isinstance(collaborators, list):
                raise RepositoryProvisionError(
                    "access_check_failed",
                    "GitHub collaborator list was not an array",
                    response.status_code,
                )

            for collaborator in collaborators:
                login = (
                    collaborator.get("login")
                    if isinstance(collaborator, dict)
                    else None
                )
                if isinstance(login, str) and login.casefold() == expected_login:
                    return True

            # Неполная страница является последней. Это позволяет обработать
            # репозитории с любым количеством прямых коллабораторов без Link-header.
            if len(collaborators) < 100:
                return False
            page += 1

    def find_pending_invitation(
        self,
        organization: str,
        repository: str,
        username: str,
    ) -> int | None:
        """Найти id открытого приглашения, просмотрев все страницы GitHub."""

        page = 1
        expected_login = username.casefold()

        while True:
            response = self._request(
                "get",
                f"/repos/{quote(organization, safe='')}/{quote(repository, safe='')}/invitations",
                params={"per_page": 100, "page": page},
            )
            if response.status_code != 200:
                raise self._api_error("invitation_lookup_failed", "list invitations", response)

            try:
                invitations = response.json()
            except ValueError as exc:
                raise RepositoryProvisionError(
                    "invitation_lookup_failed",
                    "GitHub invitation list was not valid JSON",
                    response.status_code,
                ) from exc

            if not isinstance(invitations, list):
                raise RepositoryProvisionError(
                    "invitation_lookup_failed",
                    "GitHub invitation list was not an array",
                    response.status_code,
                )

            for invitation in invitations:
                if not isinstance(invitation, dict):
                    continue
                invitee = invitation.get("invitee")
                invitee_login = invitee.get("login") if isinstance(invitee, dict) else None
                invitation_id = invitation.get("id")
                if (
                    isinstance(invitee_login, str)
                    and invitee_login.casefold() == expected_login
                    and isinstance(invitation_id, int)
                ):
                    return invitation_id

            # Неполная страница является последней. Так обрабатываются и
            # репозитории, где открыто больше 100 приглашений, без Link-header.
            if len(invitations) < 100:
                return None
            page += 1

    def delete_invitation(
        self,
        organization: str,
        repository: str,
        invitation_id: int,
    ) -> bool:
        response = self._request(
            "delete",
            (
                f"/repos/{quote(organization, safe='')}/{quote(repository, safe='')}/"
                f"invitations/{invitation_id}"
            ),
        )
        if response.status_code == 204:
            return True
        if response.status_code == 404:
            # Параллельный callback мог уже принять или удалить приглашение.
            # Верхний уровень повторно проверит доступ перед новой отправкой.
            return False
        raise self._api_error("invitation_delete_failed", "delete invitation", response)

    def invite_collaborator(
        self,
        organization: str,
        repository: str,
        username: str,
    ) -> None:
        response = self._request(
            "put",
            (
                f"/repos/{quote(organization, safe='')}/{quote(repository, safe='')}/"
                f"collaborators/{quote(username, safe='')}"
            ),
            json={"permission": "push"},
        )
        # 201 означает создание приглашения. GitHub возвращает 204, если доступ
        # уже есть либо участник организации добавлен напрямую без приглашения.
        if response.status_code in (201, 204):
            return
        raise self._api_error("invitation_create_failed", "invite collaborator", response)

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        try:
            return requests.request(
                method,
                f"{self.BASE_URL}{path}",
                headers=self.headers,
                timeout=self.REQUEST_TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RepositoryProvisionError(
                "github_unavailable",
                f"GitHub API request failed during {method.upper()} {path}: {exc.__class__.__name__}",
            ) from exc

    @staticmethod
    def _api_error(
        default_code: str,
        operation: str,
        response: requests.Response,
    ) -> RepositoryProvisionError:
        # Обычное сообщение GitHub полезно для серверной диагностики и не
        # содержит Authorization header. В браузер оно не передаётся: frontend
        # получает только стабильный публичный код ошибки.
        github_message = "unknown GitHub error"
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("message"), str):
                github_message = payload["message"]
        except ValueError:
            pass

        code = default_code
        is_rate_limited = response.status_code == 429 or (
            response.status_code == 403
            and (
                response.headers.get("X-RateLimit-Remaining") == "0"
                or "Retry-After" in response.headers
                or "rate limit" in github_message.casefold()
            )
        )
        if is_rate_limited:
            code = "github_rate_limit"

        return RepositoryProvisionError(
            code,
            f"Failed to {operation}: {response.status_code} {github_message}",
            response.status_code,
        )


class RepositoryProvisioner:
    """Идемпотентно создать приватный репозиторий и обеспечить доступ студента."""

    def __init__(self, client: GitHubRepositoryClient):
        self.client = client

    def provision(
        self,
        organization: str,
        github_prefix: str,
        template_owner: str,
        template_repository: str,
        join_key: str,
    ) -> ProvisionResult:
        repository = f"{github_prefix}-{join_key}"
        created = False

        if not self.client.repository_exists(organization, repository):
            try:
                self.client.generate_repository(
                    template_owner,
                    template_repository,
                    organization,
                    repository,
                )
                created = True
            except RepositoryProvisionError as exc:
                # Два callback могут одновременно получить 404 и начать
                # создание. Ошибка 409/422 медленного запроса считается гонкой
                # только после GET, подтвердившего появление репозитория.
                if exc.status_code not in (409, 422) or not self.client.repository_exists(
                    organization, repository
                ):
                    raise

        access_action = self._ensure_access(organization, repository, join_key)
        repository_url = (
            f"https://github.com/{quote(organization, safe='')}/{quote(repository, safe='')}"
        )
        return ProvisionResult(
            organization=organization,
            repository=repository,
            repository_url=repository_url,
            created=created,
            access_action=access_action,
        )

    def _ensure_access(self, organization: str, repository: str, username: str) -> str:
        if self.client.is_direct_collaborator(organization, repository, username):
            return "already_has_access"

        invitation_id = self.client.find_pending_invitation(
            organization, repository, username
        )
        if invitation_id is None:
            self.client.invite_collaborator(organization, repository, username)
            return "invited"

        invitation_deleted = self.client.delete_invitation(
            organization, repository, invitation_id
        )
        if not invitation_deleted and self.client.is_direct_collaborator(
            organization, repository, username
        ):
            return "already_has_access"

        self.client.invite_collaborator(organization, repository, username)
        return "reinvited"
