"""
Propagate template repository updates to student repositories via fork PRs.

Only applies to labs with `repo-provisioning: fork` (see issue #51) - a real
fork relationship is what lets GitHub build a cross-repo PR between the
template and each student repository. Opening a PR is a *proposal*: merging
stays up to the student (or the teacher, if they have access), never a
forced push. See issue #52 for the full design.

Job state is a module-level in-memory dict, deliberately not persisted:
the backend runs as a single uvicorn worker (see docs/COURSE_CONFIG.md /
PROJECT_DESCRIPTION.md) so this is safe as long as `--workers` is never
added. A restart loses the status of an in-flight job, but not its work -
PRs already created stay created, and re-running is safe (an already-open
PR is reported back as `pr_exists`).
"""
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .github_client import GitHubClient, is_rate_limited

logger = logging.getLogger(__name__)

# Pause between individual create_pull_request calls, to stay clear of
# GitHub's secondary rate limit. A module constant so tests can patch it to
# 0 instead of actually sleeping through a 200-repo run.
PR_CREATE_PAUSE_SECONDS = 1

# How many finished jobs to keep around for GET /admin/propagate-jobs/{id}.
MAX_JOBS_KEPT = 20

# Service branch created in each student fork, pointing at the template's tip
# commit. A cross-repo PR (head="owner:branch") cannot be used here: when the
# template and the forks share an owner, GitHub resolves head to the base repo
# itself and answers "No commits between master and master" - verified on live
# GitHub, see issue #52. Placing the commit as a branch inside the fork and
# opening an ordinary same-repo PR is what actually works, and it only works
# because a fork shares object storage with its template.
TEMPLATE_UPDATE_BRANCH = "template-update"

PR_TITLE = "Обновление стартового кода лабораторной работы"
PR_BODY = (
    "Преподаватель обновил стартовый код лабораторной работы в репозитории-шаблоне.\n\n"
    "Это предложение изменений, а не принудительный push: слияние остаётся полностью "
    "на ваше усмотрение. Если конфликтов при слиянии нет, GitHub позволит влить "
    "изменения одной кнопкой; если есть - их нужно разрешить вручную, как в обычном PR.\n\n"
    "Если изменения вам не подходят, pull request можно закрыть без слияния."
)


class PropagateSetupError(Exception):
    """
    Raised when a step shared by both dry-run and the real run fails before
    any per-repository work starts (template unreadable, forks/org repos
    list unavailable). Distinct from a per-repository error, which never
    aborts the whole job - see PropagateResult.
    """
    pass


@dataclass
class PropagateResult:
    """Outcome of processing a single repository."""
    repo: str
    status: str  # will_process (dry-run only) | pr_created | up_to_date | pr_exists | not_a_fork | error
    pr_url: str | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "status": self.status,
            "pr_url": self.pr_url,
            "message": self.message,
        }


@dataclass
class PropagateJob:
    """State of one background propagate-template-update run."""
    job_id: str
    course_id: str
    lab_id: str
    status: str = "running"  # running | done | failed
    started_at: str = ""
    finished_at: str | None = None
    total: int = 0
    processed: int = 0
    results: list[PropagateResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "course_id": self.course_id,
            "lab_id": self.lab_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "processed": self.processed,
            "results": [r.to_dict() for r in self.results],
            "error": self.error,
        }


# Module-level job store. Guarded by _jobs_lock since BackgroundTasks run in
# FastAPI's threadpool (one thread per in-flight request), not the main
# thread - see docs/PROJECT_DESCRIPTION.md for the single-worker assumption
# this whole module relies on.
_jobs: "OrderedDict[str, PropagateJob]" = OrderedDict()
_running_lab_keys: set[tuple[str, str]] = set()
_jobs_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_propagate_job(job_id: str) -> PropagateJob | None:
    """Look up a job by id (used by GET /admin/propagate-jobs/{job_id})."""
    with _jobs_lock:
        return _jobs.get(job_id)


def _evict_old_jobs_locked() -> None:
    """Keep at most MAX_JOBS_KEPT jobs, oldest first. Must hold _jobs_lock."""
    if len(_jobs) <= MAX_JOBS_KEPT:
        return
    for job_id in list(_jobs.keys()):
        if len(_jobs) <= MAX_JOBS_KEPT:
            break
        # Never evict a job that's still running - it would leave
        # _running_lab_keys pointing at a job GET can no longer find.
        if _jobs[job_id].status == "running":
            continue
        del _jobs[job_id]


def try_start_propagate_job(course_id: str, lab_id: str) -> PropagateJob | None:
    """
    Atomically create and register a new running job for (course_id, lab_id),
    unless one is already running for that same pair.

    Returns:
        The new PropagateJob, or None if a job for this lab is already
        running (caller should respond HTTP 409).
    """
    with _jobs_lock:
        if (course_id, lab_id) in _running_lab_keys:
            return None
        job = PropagateJob(job_id=uuid.uuid4().hex, course_id=course_id, lab_id=lab_id, started_at=_now())
        _jobs[job.job_id] = job
        _running_lab_keys.add((course_id, lab_id))
        _evict_old_jobs_locked()
        return job


def _finish_job_locked(job: PropagateJob, status: str, error: str | None = None) -> None:
    job.status = status
    job.error = error
    job.finished_at = _now()
    _running_lab_keys.discard((job.course_id, job.lab_id))


def _list_target_forks(
    github_client: GitHubClient,
    org: str,
    github_prefix: str,
    template_owner: str,
    template_name: str,
) -> tuple[list[dict], list[dict], str]:
    """
    Resolve which repositories a propagate run would touch.

    Returns:
        (target_forks, not_a_fork_repos, template_head_sha)

    Raises:
        PropagateSetupError: template unreadable, its branch tip unreadable,
        or forks/org repos list unavailable
    """
    template = github_client.get_repo(template_owner, template_name)
    if template is None:
        raise PropagateSetupError("Репозиторий-шаблон не найден или недоступен")
    template_default_branch = template.get("default_branch") or "main"

    # The tip commit itself, not just the branch name: it is what gets placed
    # into each fork as TEMPLATE_UPDATE_BRANCH (see issue #52).
    ref = github_client.get_ref(template_owner, template_name, f"heads/{template_default_branch}")
    template_head_sha = ((ref or {}).get("object") or {}).get("sha")
    if not template_head_sha:
        raise PropagateSetupError(
            f"Не удалось прочитать ветку {template_default_branch} репозитория-шаблона"
        )

    forks = github_client.list_forks(template_owner, template_name)
    if forks is None:
        raise PropagateSetupError("Не удалось получить список форков репозитория-шаблона")

    prefix = f"{github_prefix}-"
    org_lower = org.lower()
    target_forks = [
        fork
        for fork in forks
        if (fork.get("owner") or {}).get("login", "").lower() == org_lower
        and fork.get("name", "").startswith(prefix)
    ]
    target_names = {fork["name"] for fork in target_forks}

    org_repos = github_client.list_org_repos(org)
    if org_repos is None:
        raise PropagateSetupError("Не удалось получить список репозиториев организации")

    # The template usually lives in the same org and often matches the lab's
    # prefix itself (github-prefix: os-task1 + template-repo: .../os-task1-template),
    # and it obviously isn't a student repo left out of the update.
    template_own_name = template_name.lower() if org_lower == template_owner.lower() else None
    not_a_fork = [
        repo
        for repo in org_repos
        if repo.get("name", "").startswith(prefix)
        and repo.get("name") not in target_names
        and repo.get("name", "").lower() != template_own_name
    ]

    return target_forks, not_a_fork, template_head_sha


def dry_run_propagation(
    github_client: GitHubClient,
    org: str,
    github_prefix: str,
    template_owner: str,
    template_name: str,
) -> dict:
    """
    Synchronous, read-only preview of who a real run would send PRs to.

    Does not check whether each fork's code is actually behind the template -
    that would mean a request per repository. It only answers "who is in the
    fork network under this prefix" vs "who isn't" (`not_a_fork`).

    Raises:
        PropagateSetupError: see _list_target_forks
    """
    target_forks, not_a_fork, _template_head_sha = _list_target_forks(
        github_client, org, github_prefix, template_owner, template_name
    )
    results = [PropagateResult(repo=f["name"], status="will_process") for f in target_forks]
    results += [PropagateResult(repo=r["name"], status="not_a_fork") for r in not_a_fork]
    return {
        "total": len(target_forks),
        "not_a_fork_count": len(not_a_fork),
        "results": [r.to_dict() for r in results],
    }


def _parse_retry_after(resp, default: float = 5.0) -> float:
    value = resp.headers.get("Retry-After")
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    return default


def _response_message(resp) -> str:
    """
    Text to classify a GitHub error by.

    A 422 puts "Validation Failed" in the top-level `message` and the actual
    reason in `errors[].message` ("No commits between ...", "A pull request
    already exists for ..."), so both are joined here - reading only the
    top-level field would classify every expected outcome as an error
    (verified on live GitHub, see issue #52).
    """
    try:
        payload = resp.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""

    parts = [payload.get("message", "")]
    errors = payload.get("errors")
    if isinstance(errors, list):
        parts += [e.get("message", "") for e in errors if isinstance(e, dict)]
    return " ".join(part for part in parts if part)


def _place_template_branch(github_client: GitHubClient, org: str, fork_name: str, sha: str) -> str | None:
    """
    Point TEMPLATE_UPDATE_BRANCH in the fork at the template's tip commit,
    creating the branch or moving an existing one.

    Moving a branch that an open PR is built on is deliberate: the PR picks
    up the new commits instead of a second one being opened.

    Returns:
        None on success, or an error message for the per-repo result
    """
    resp = github_client.create_ref(org, fork_name, f"refs/heads/{TEMPLATE_UPDATE_BRANCH}", sha)
    if resp.status_code == 201:
        return None

    if resp.status_code == 422 and "already exists" in _response_message(resp).lower():
        update_resp = github_client.update_ref(
            org, fork_name, f"heads/{TEMPLATE_UPDATE_BRANCH}", sha, force=True
        )
        if update_resp.status_code == 200:
            return None
        logger.error(
            f"Failed to move {TEMPLATE_UPDATE_BRANCH} in {org}/{fork_name}: "
            f"{update_resp.status_code} {update_resp.text[:500]}"
        )
        return update_resp.text[:500]

    logger.error(
        f"Failed to create {TEMPLATE_UPDATE_BRANCH} in {org}/{fork_name}: "
        f"{resp.status_code} {resp.text[:500]}"
    )
    return resp.text[:500]


def _create_pr_for_fork(
    github_client: GitHubClient,
    org: str,
    fork: dict,
    template_head_sha: str,
) -> PropagateResult:
    """
    Open (or discover the state of) a single update PR, per the response
    table in issue #52. One rate-limit retry; anything else is final.
    """
    fork_name = fork["name"]
    fork_default_branch = fork.get("default_branch") or "main"
    head = TEMPLATE_UPDATE_BRANCH

    branch_error = _place_template_branch(github_client, org, fork_name, template_head_sha)
    if branch_error:
        return PropagateResult(repo=fork_name, status="error", message=branch_error)

    resp = github_client.create_pull_request(
        org, fork_name, head=head, base=fork_default_branch, title=PR_TITLE, body=PR_BODY
    )

    if resp.status_code == 403 and is_rate_limited(resp):
        retry_after = _parse_retry_after(resp)
        logger.warning(f"Rate limited creating PR for {org}/{fork_name}, retrying after {retry_after}s")
        time.sleep(retry_after)
        resp = github_client.create_pull_request(
            org, fork_name, head=head, base=fork_default_branch, title=PR_TITLE, body=PR_BODY
        )

    if resp.status_code == 201:
        return PropagateResult(repo=fork_name, status="pr_created", pr_url=resp.json().get("html_url"))

    if resp.status_code == 422:
        message = _response_message(resp)
        if "No commits between" in message:
            return PropagateResult(repo=fork_name, status="up_to_date", message=message)
        if "A pull request already exists" in message:
            existing = github_client.list_pull_requests(org, fork_name, head=head, state="open")
            pr_url = existing[0].get("html_url") if existing else None
            return PropagateResult(repo=fork_name, status="pr_exists", pr_url=pr_url, message=message)
        logger.error(f"PR creation validation failed for {org}/{fork_name}: {resp.text[:500]}")
        return PropagateResult(repo=fork_name, status="error", message=resp.text[:500])

    if resp.status_code == 403 and is_rate_limited(resp):
        logger.error(f"Still rate limited creating PR for {org}/{fork_name} after one retry")
        return PropagateResult(repo=fork_name, status="error", message=resp.text[:500])

    logger.error(f"Unexpected status creating PR for {org}/{fork_name}: {resp.status_code} {resp.text[:500]}")
    return PropagateResult(repo=fork_name, status="error", message=resp.text[:500])


def _run_propagation(
    job: PropagateJob,
    github_client: GitHubClient,
    org: str,
    github_prefix: str,
    template_repo: str,
) -> None:
    """
    Body of a propagate-template-update run. Call run_propagation instead -
    it is what guarantees the job never stays stuck in "running".

    A single repo failing never aborts the job - only a failure to read the
    template or list forks/org repos (before any PR is attempted) fails it
    wholesale.
    """
    try:
        template_owner, template_name = template_repo.split("/", 1)
    except ValueError:
        with _jobs_lock:
            _finish_job_locked(job, "failed", "Некорректно настроен template-repo лабы (ожидается 'owner/repo')")
        return

    try:
        target_forks, not_a_fork, template_head_sha = _list_target_forks(
            github_client, org, github_prefix, template_owner, template_name
        )
    except PropagateSetupError as e:
        logger.error(f"Propagate job {job.job_id} ({org}, {github_prefix}) failed to start: {e}")
        with _jobs_lock:
            _finish_job_locked(job, "failed", str(e))
        return

    with _jobs_lock:
        job.total = len(target_forks)
        job.results = [PropagateResult(repo=r["name"], status="not_a_fork") for r in not_a_fork]

    for index, fork in enumerate(target_forks):
        try:
            result = _create_pr_for_fork(github_client, org, fork, template_head_sha)
        except Exception:
            logger.exception(f"Unexpected error creating PR for {org}/{fork.get('name')}")
            result = PropagateResult(repo=fork.get("name", "?"), status="error", message="Внутренняя ошибка")

        with _jobs_lock:
            job.results.append(result)
            job.processed += 1

        if index < len(target_forks) - 1:
            time.sleep(PR_CREATE_PAUSE_SECONDS)

    with _jobs_lock:
        _finish_job_locked(job, "done")


def run_propagation(
    job: PropagateJob,
    github_client: GitHubClient,
    org: str,
    github_prefix: str,
    template_repo: str,
) -> None:
    """
    Background worker entry point for a propagate-template-update run.

    A plain `def`, not `async def`, so FastAPI's BackgroundTasks executes it
    in the threadpool instead of blocking the event loop on synchronous
    `requests` calls (see issue #52).

    Wraps the actual work so that the job is always closed out. _list_target_forks
    converts the expected GitHub failures into PropagateSetupError, but a bare
    `requests` timeout on api.github.com raises RequestException straight
    through - and a job left in "running" keeps (course_id, lab_id) locked in
    _running_lab_keys, so every later run for that lab would 409 (and the admin
    page would poll a never-finishing job) until the backend restarts.
    """
    try:
        _run_propagation(job, github_client, org, github_prefix, template_repo)
    except Exception:
        logger.exception(f"Propagate job {job.job_id} ({org}, {github_prefix}) crashed")
    finally:
        with _jobs_lock:
            if job.status == "running":
                _finish_job_locked(
                    job,
                    "failed",
                    "Непредвиденная ошибка при рассылке обновлений. Попробуйте ещё раз позже",
                )
