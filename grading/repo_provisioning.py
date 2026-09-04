"""
Student repository provisioning for the /join flow.

Replaces the "create repo from template + invite student" part of GitHub
Classroom, plus the separate github-reinvite tool that used to compensate
for pending invitations silently expiring after 7 days. See
docs/REPO_GENERATION_PLAN.md for the full design.
"""
import logging
from dataclasses import dataclass
from enum import Enum

from .github_client import GitHubClient

logger = logging.getLogger(__name__)


def _is_rate_limited(resp) -> bool:
    """
    Detect a GitHub rate-limit response hiding behind a 403.

    GitHub answers both "you don't have permission" and "you hit a
    (secondary) rate limit" with HTTP 403 - distinguishing them matters
    because only the second one should be reported as retryable.
    """
    if resp.headers.get("Retry-After"):
        return True
    if resp.headers.get("X-RateLimit-Remaining") == "0":
        return True
    try:
        message = resp.json().get("message", "")
    except ValueError:
        message = ""
    return "rate limit" in message.lower()


class ProvisionStatus(Enum):
    """Outcome of a repository provisioning attempt."""
    OK = "ok"
    ERROR = "error"


@dataclass
class ProvisionResult:
    """Result of provisioning a student repository."""
    status: ProvisionStatus
    repo_name: str | None = None
    repo_url: str | None = None
    message: str = ""
    error_code: str | None = None  # For programmatic error handling


class RepoProvisioner:
    """
    Creates a student repository from a lab's template repository and makes
    sure the student actually has access to it (creating or re-issuing a
    collaborator invitation as needed).

    Mirrors the LabGrader orchestrator: takes a configured GitHubClient using
    the server's GITHUB_TOKEN (never the student's OAuth token, which is only
    used to confirm the student's username - see docs/REPO_GENERATION_PLAN.md §3).
    """

    def __init__(self, github_client: GitHubClient):
        self.github = github_client

    def provision(
        self,
        org: str,
        github_prefix: str,
        template_repo: str,
        repo_suffix: str,
    ) -> ProvisionResult:
        """
        Ensure `{github_prefix}-{repo_suffix}` exists in `org` (created from
        `template_repo` if missing) and that the student has collaborator
        access to it.

        `repo_suffix` is named generically (not `username`) so that a future
        team-lab variant could pass a team name instead - the algorithm below
        always handles exactly one suffix per call either way (see
        docs/REPO_GENERATION_PLAN.md §9).

        Args:
            org: GitHub organization that owns student repositories
            github_prefix: Repo name prefix from lab config
            template_repo: Template repository as "owner/repo"
            repo_suffix: Suffix identifying the student (their GitHub username)

        Returns:
            ProvisionResult describing success or the specific failure
        """
        repo_name = f"{github_prefix}-{repo_suffix}"

        try:
            template_owner, template_name = template_repo.split("/", 1)
        except ValueError:
            return ProvisionResult(
                status=ProvisionStatus.ERROR,
                message="Некорректно настроен template-repo лабы (ожидается формат 'owner/repo')",
                error_code="INVALID_TEMPLATE_CONFIG",
            )

        create_error = self._ensure_repo_created(org, repo_name, template_owner, template_name)
        if create_error:
            return create_error

        access_error = self._ensure_access(org, repo_name, repo_suffix)
        if access_error:
            return access_error

        return ProvisionResult(
            status=ProvisionStatus.OK,
            repo_name=repo_name,
            repo_url=f"https://github.com/{org}/{repo_name}",
            message="Репозиторий готов",
        )

    def _ensure_repo_created(
        self,
        org: str,
        repo_name: str,
        template_owner: str,
        template_name: str,
    ) -> ProvisionResult | None:
        """
        Create the repo from the template if it doesn't already exist.

        Returns:
            ProvisionResult with an error, or None if the repo exists (or now does)
        """
        if self.github.repo_exists(org, repo_name):
            logger.info(f"Repository {org}/{repo_name} already exists, skipping creation")
            return None

        logger.info(f"Creating {org}/{repo_name} from template {template_owner}/{template_name}")
        resp = self.github.create_repo_from_template(template_owner, template_name, org, repo_name, private=True)

        if resp.status_code in (200, 201):
            return None

        if resp.status_code == 422:
            # Скорее всего гонка: репозиторий уже создан параллельным запросом
            # между проверкой существования и вызовом /generate.
            if self.github.repo_exists(org, repo_name):
                logger.info(f"Repository {org}/{repo_name} appeared concurrently, continuing")
                return None
            logger.error(f"Repo creation validation failed for {org}/{repo_name}: {resp.text[:500]}")
            return ProvisionResult(
                status=ProvisionStatus.ERROR,
                message="Не удалось создать репозиторий: некорректные параметры или репозиторий-шаблон недоступен",
                error_code="CREATE_VALIDATION_FAILED",
            )

        if resp.status_code in (401, 403):
            if _is_rate_limited(resp):
                # GitHub also answers 403 for (secondary) rate limiting, not just missing
                # permissions - this one IS retryable, unlike a real permissions problem
                # (see docs/REPO_GENERATION_PLAN.md §7: "rate limit" is listed as a
                # retryable GitHub API error, distinct from a config/permissions error).
                logger.warning(f"Rate limited creating {org}/{repo_name}: {resp.status_code} {resp.text[:500]}")
                return ProvisionResult(
                    status=ProvisionStatus.ERROR,
                    message="GitHub API временно ограничивает запросы (rate limit). Попробуйте ещё раз через несколько минут",
                    error_code="RATE_LIMITED",
                )
            logger.error(f"Forbidden creating {org}/{repo_name}: {resp.status_code} {resp.text[:500]}")
            return ProvisionResult(
                status=ProvisionStatus.ERROR,
                message="Недостаточно прав для создания репозитория. Обратитесь к преподавателю",
                error_code="CREATE_FORBIDDEN",
            )

        if resp.status_code == 404:
            return ProvisionResult(
                status=ProvisionStatus.ERROR,
                message="Репозиторий-шаблон не найден или недоступен",
                error_code="TEMPLATE_NOT_FOUND",
            )

        logger.error(f"Unexpected status creating {org}/{repo_name}: {resp.status_code} {resp.text[:500]}")
        return ProvisionResult(
            status=ProvisionStatus.ERROR,
            message="Ошибка GitHub API при создании репозитория. Попробуйте ещё раз позже",
            error_code="CREATE_FAILED",
        )

    def _ensure_access(self, org: str, repo_name: str, username: str) -> ProvisionResult | None:
        """
        Make sure `username` has direct collaborator access to the repo,
        re-issuing a pending invitation if one already exists (a plain PUT
        without deleting the stale invitation first does not resend the
        notification - see docs/REPO_GENERATION_PLAN.md §4).

        Returns:
            ProvisionResult with an error, or None if access is now in place
        """
        if self.github.is_direct_collaborator(org, repo_name, username):
            logger.info(f"{username} already has direct access to {org}/{repo_name}")
            return None

        invitations = self.github.list_invitations(org, repo_name)
        if invitations is None:
            return ProvisionResult(
                status=ProvisionStatus.ERROR,
                message="Ошибка GitHub API при проверке приглашений в репозиторий",
                error_code="INVITATIONS_FETCH_FAILED",
            )

        pending = next(
            (inv for inv in invitations if (inv.get("invitee") or {}).get("login") == username),
            None,
        )

        if pending is not None:
            logger.info(f"Re-issuing pending invitation for {username} in {org}/{repo_name}")
            if not self.github.delete_invitation(org, repo_name, pending["id"]):
                return ProvisionResult(
                    status=ProvisionStatus.ERROR,
                    message="Не удалось обновить приглашение в репозиторий. Попробуйте ещё раз позже",
                    error_code="REINVITE_DELETE_FAILED",
                )
        else:
            logger.info(f"No pending invitation for {username} in {org}/{repo_name}, inviting for the first time")

        resp = self.github.add_collaborator(org, repo_name, username)
        if resp.status_code not in (201, 204):
            logger.error(f"Failed to invite {username} to {org}/{repo_name}: {resp.status_code} {resp.text[:500]}")
            return ProvisionResult(
                status=ProvisionStatus.ERROR,
                message="Не удалось предоставить доступ к репозиторию. Попробуйте ещё раз позже",
                error_code="INVITE_FAILED",
            )

        return None
