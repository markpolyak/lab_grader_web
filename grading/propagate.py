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

Every repository is first compared with the template's tip, in the preview
and again in the real run: a fork that already contains every template commit
is reported as `up_to_date` and left untouched - no service branch, no PR.
"""
import logging
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .github_client import GitHubClient, is_rate_limited

logger = logging.getLogger(__name__)

# Pause between individual create_pull_request calls, to stay clear of
# GitHub's secondary rate limit. A module constant so tests can patch it to
# 0 instead of actually sleeping through a 200-repo run.
PR_CREATE_PAUSE_SECONDS = 1

# Concurrent read-only checks in the preview. The preview is a synchronous
# request, and a compare + PR lookup takes ~1.3 s per fork (measured on a live
# course): serially, a 200-student course would run for minutes. Ten parallel
# checks bring 65 forks down to ~12 s; twenty measured no faster, GitHub
# evidently serializes one token's requests beyond that. Ten reads also stay
# well within the secondary rate limit (100 concurrent requests).
PREVIEW_WORKERS = 10

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

# A commit that touches workflow files can only be referenced by a token with
# the `workflow` scope (fine-grained: Workflows read/write). Without it GitHub
# answers the ref creation with a bare 404, indistinguishable from "no access".
WORKFLOWS_DIR = ".github/workflows/"

COMPARE_FAILED_MESSAGE = "Не удалось сравнить репозиторий с шаблоном"

# GitHub lists at most this many changed files in a comparison; a longer diff
# can't be checked file by file, so it is never treated as already applied.
COMPARE_FILES_LIMIT = 300

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
    # needs_update (dry-run only) | pr_created | pr_updated | pr_exists | up_to_date | not_a_fork | error.
    # pr_updated: an open PR existed and its branch was moved to a newer
    # template commit; pr_exists: the open PR already had it.
    status: str
    pr_url: str | None = None
    message: str = ""
    # Template commits missing from the fork's default branch; None when the
    # repository was never compared (not_a_fork, or the comparison failed).
    commits_behind: int | None = None

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "status": self.status,
            "pr_url": self.pr_url,
            "message": self.message,
            "commits_behind": self.commits_behind,
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


def _by_name(repos: list[dict]) -> list[dict]:
    return sorted(repos, key=lambda repo: repo.get("name", "").lower())


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
        (target_forks, not_a_fork_repos, template_head_sha), both lists sorted
        by name - it is the order the admin page shows and processes them in

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

    return _by_name(target_forks), _by_name(not_a_fork), template_head_sha


@dataclass
class _TemplateDiff:
    """What a fork's default branch lacks compared to the template's tip."""
    commits_behind: int
    touches_workflows: bool
    # The commits are missing, but their changes are already in the files -
    # the previous update PR was squash- or rebase-merged.
    already_applied: bool = False

    @property
    def nothing_to_propose(self) -> bool:
        return self.commits_behind == 0 or self.already_applied


def _changes_already_in_fork(
    github_client: GitHubClient,
    org: str,
    fork_name: str,
    fork_default_branch: str,
    files: list[dict],
) -> bool:
    """
    Whether the template's changes are already in the fork's files even though
    its commits are not.

    "Squash and merge" and "Rebase and merge" put the update PR's changes into
    the default branch as new commits, so the commit comparison keeps
    reporting the template commits as missing and a run would open the same
    PR again. `files` (the template's changes since the fork point, with each
    file's blob SHA at the template tip) are checked against the fork's tree
    instead: every changed file must have exactly the template's content, and
    every removed one must be gone.

    Conservative on purpose - anything it can't verify (a diff over GitHub's
    file limit, a truncated or unreadable tree, a file edited again by the
    student afterwards) counts as not applied, i.e. the previous behaviour.
    """
    if not files:
        # Commits without file changes: a PR would have an empty diff.
        return True
    if len(files) >= COMPARE_FILES_LIMIT:
        return False
    if any(f.get("status") != "removed" and not f.get("sha") for f in files):
        return False

    tree = github_client.get_tree(org, fork_name, fork_default_branch)
    if tree is None or tree.get("truncated"):
        logger.warning(f"Can't read the full tree of {org}/{fork_name}:{fork_default_branch}")
        return False
    blobs = {entry.get("path"): entry.get("sha") for entry in tree.get("tree") or [] if entry.get("type") == "blob"}

    for f in files:
        path = f.get("filename")
        if f.get("status") == "removed":
            if path in blobs:
                return False
            continue
        if f.get("status") == "renamed" and f.get("previous_filename") in blobs:
            return False
        if blobs.get(path) != f.get("sha"):
            return False
    return True


def _compare_with_template(
    github_client: GitHubClient,
    org: str,
    fork: dict,
    template_head_sha: str,
) -> _TemplateDiff | None:
    """
    Compare a fork's default branch with the template's tip commit.

    Commit-based, exactly like the PR it decides about: `ahead_by` counts the
    template commits not reachable from the fork's default branch, so 0 means
    there is nothing to propose - the template hasn't changed since the fork
    was made, or the student already merged an earlier update. When commits
    are missing, the files are checked too (see _changes_already_in_fork).

    Returns:
        The difference, or None if GitHub couldn't compare
    """
    fork_default_branch = fork.get("default_branch") or "main"
    comparison = github_client.compare_commits(org, fork["name"], fork_default_branch, template_head_sha)
    if comparison is None:
        logger.error(f"Failed to compare {org}/{fork['name']}:{fork_default_branch} with template {template_head_sha}")
        return None
    files = comparison.get("files") or []
    commits_behind = int(comparison.get("ahead_by") or 0)
    return _TemplateDiff(
        commits_behind=commits_behind,
        touches_workflows=any((f.get("filename") or "").startswith(WORKFLOWS_DIR) for f in files),
        already_applied=commits_behind > 0
        and _changes_already_in_fork(github_client, org, fork["name"], fork_default_branch, files),
    )


def _preview_fork(
    github_client: GitHubClient,
    org: str,
    fork: dict,
    template_head_sha: str,
) -> PropagateResult:
    """
    Read-only answer to "what would a real run do with this fork".

    `pr_exists` only when the open PR is already built on the template's tip -
    an older open PR still `needs_update` (a run moves its branch), and its
    link is passed along.
    """
    fork_name = fork["name"]
    try:
        diff = _compare_with_template(github_client, org, fork, template_head_sha)
        if diff is None:
            return PropagateResult(repo=fork_name, status="error", message=COMPARE_FAILED_MESSAGE)
        if diff.nothing_to_propose:
            return PropagateResult(repo=fork_name, status="up_to_date", commits_behind=diff.commits_behind)

        # A failed lookup only loses the hint: the real run finds the PR anyway.
        open_prs = github_client.list_pull_requests(org, fork_name, head=TEMPLATE_UPDATE_BRANCH, state="open") or []
    except Exception:
        logger.exception(f"Unexpected error previewing propagation for {org}/{fork_name}")
        return PropagateResult(repo=fork_name, status="error", message=COMPARE_FAILED_MESSAGE)

    pr = open_prs[0] if open_prs else None
    pr_url = pr.get("html_url") if pr else None
    if pr and (pr.get("head") or {}).get("sha") == template_head_sha:
        return PropagateResult(
            repo=fork_name, status="pr_exists", pr_url=pr_url, commits_behind=diff.commits_behind
        )
    return PropagateResult(
        repo=fork_name, status="needs_update", pr_url=pr_url, commits_behind=diff.commits_behind
    )


def dry_run_propagation(
    github_client: GitHubClient,
    org: str,
    github_prefix: str,
    template_owner: str,
    template_name: str,
) -> dict:
    """
    Synchronous, read-only preview of who a real run would send PRs to.

    Every fork is compared with the template (see _preview_fork), a few at a
    time: `needs_update` rows are the ones worth selecting, the rest say why
    not. Results are sorted by repository name.

    Raises:
        PropagateSetupError: see _list_target_forks
    """
    target_forks, not_a_fork, template_head_sha = _list_target_forks(
        github_client, org, github_prefix, template_owner, template_name
    )
    with ThreadPoolExecutor(max_workers=PREVIEW_WORKERS) as pool:
        results = list(
            pool.map(lambda fork: _preview_fork(github_client, org, fork, template_head_sha), target_forks)
        )
    results += [PropagateResult(repo=r["name"], status="not_a_fork") for r in not_a_fork]
    results.sort(key=lambda r: r.repo.lower())
    return {
        "total": sum(1 for r in results if r.status == "needs_update"),
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


def _branch_error_message(resp, touches_workflows: bool) -> str:
    """Teacher-facing reason a service branch couldn't be placed (details go to the log)."""
    if resp.status_code == 404 and touches_workflows:
        return (
            "Обновление меняет файлы в .github/workflows, а у токена сервиса (GITHUB_TOKEN) "
            "нет на это права. Добавьте токену scope workflow (для fine-grained токена - "
            "разрешение Workflows: Read and write) и запустите рассылку ещё раз"
        )
    if resp.status_code in (403, 404):
        return "У токена сервиса (GITHUB_TOKEN) нет права записи в этот репозиторий"
    return f"Не удалось создать ветку {TEMPLATE_UPDATE_BRANCH}: GitHub ответил {resp.status_code}"


def _place_template_branch(
    github_client: GitHubClient,
    org: str,
    fork_name: str,
    sha: str,
    touches_workflows: bool = False,
) -> tuple[str | None, bool]:
    """
    Point TEMPLATE_UPDATE_BRANCH in the fork at the template's tip commit,
    creating the branch or moving an existing one.

    Moving a branch that an open PR is built on is deliberate: the PR picks
    up the new commits instead of a second one being opened. A branch that
    already points at the commit is left alone.

    Returns:
        (error, moved): error is None on success, or a message for the
        per-repo result; moved is True only when an existing branch was known
        to point elsewhere and was moved - it is what tells "PR updated" from
        "PR already had this commit"
    """
    resp = github_client.create_ref(org, fork_name, f"refs/heads/{TEMPLATE_UPDATE_BRANCH}", sha)
    if resp.status_code == 201:
        return None, False

    if resp.status_code == 422 and "already exists" in _response_message(resp).lower():
        current = github_client.get_ref(org, fork_name, f"heads/{TEMPLATE_UPDATE_BRANCH}")
        current_sha = ((current or {}).get("object") or {}).get("sha")
        if current_sha == sha:
            return None, False
        update_resp = github_client.update_ref(
            org, fork_name, f"heads/{TEMPLATE_UPDATE_BRANCH}", sha, force=True
        )
        if update_resp.status_code == 200:
            # Unreadable old position: the branch moved, but whether an open
            # PR gained anything is unknown - report it as merely existing.
            return None, current_sha is not None
        logger.error(
            f"Failed to move {TEMPLATE_UPDATE_BRANCH} in {org}/{fork_name}: "
            f"{update_resp.status_code} {update_resp.text[:500]}"
        )
        return _branch_error_message(update_resp, touches_workflows), False

    logger.error(
        f"Failed to create {TEMPLATE_UPDATE_BRANCH} in {org}/{fork_name}: "
        f"{resp.status_code} {resp.text[:500]}"
    )
    return _branch_error_message(resp, touches_workflows), False


def _create_pr_for_fork(
    github_client: GitHubClient,
    org: str,
    fork: dict,
    template_head_sha: str,
) -> PropagateResult:
    """
    Open (or discover the state of) a single update PR, per the response
    table in issue #52. One rate-limit retry; anything else is final.

    The fork is compared with the template first, again even after a preview
    (a student may have merged in between): with nothing to propose, nothing
    is written to the repository.
    """
    fork_name = fork["name"]
    fork_default_branch = fork.get("default_branch") or "main"
    head = TEMPLATE_UPDATE_BRANCH

    diff = _compare_with_template(github_client, org, fork, template_head_sha)
    if diff is None:
        return PropagateResult(repo=fork_name, status="error", message=COMPARE_FAILED_MESSAGE)
    if diff.nothing_to_propose:
        return PropagateResult(repo=fork_name, status="up_to_date", commits_behind=diff.commits_behind)
    behind = diff.commits_behind

    branch_error, branch_moved = _place_template_branch(
        github_client, org, fork_name, template_head_sha, diff.touches_workflows
    )
    if branch_error:
        return PropagateResult(repo=fork_name, status="error", message=branch_error, commits_behind=behind)

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
        return PropagateResult(
            repo=fork_name, status="pr_created", pr_url=resp.json().get("html_url"), commits_behind=behind
        )

    if resp.status_code == 422:
        message = _response_message(resp)
        if "No commits between" in message:
            return PropagateResult(repo=fork_name, status="up_to_date", message=message, commits_behind=0)
        if "A pull request already exists" in message:
            existing = github_client.list_pull_requests(org, fork_name, head=head, state="open")
            pr_url = existing[0].get("html_url") if existing else None
            return PropagateResult(
                repo=fork_name,
                status="pr_updated" if branch_moved else "pr_exists",
                pr_url=pr_url,
                message=message,
                commits_behind=behind,
            )
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
    only_repos: list[str] | None = None,
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

    if only_repos is not None:
        # Пересечение с уже вычисленным списком форков: посторонняя строка
        # в выборке просто не пройдёт, отдельная валидация не нужна.
        selected = set(only_repos)
        target_forks = [fork for fork in target_forks if fork["name"] in selected]
        if not target_forks:
            logger.warning(
                f"Propagate job {job.job_id}: selection {sorted(selected)} matched no fork of {template_repo}"
            )
            with _jobs_lock:
                _finish_job_locked(
                    job, "failed", "Ни один из выбранных репозиториев не является форком шаблона"
                )
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
    only_repos: list[str] | None = None,
) -> None:
    """
    Background worker entry point for a propagate-template-update run.

    A plain `def`, not `async def`, so FastAPI's BackgroundTasks executes it
    in the threadpool instead of blocking the event loop on synchronous
    `requests` calls (see issue #52).

    `only_repos` limits the run to the named repositories (as shown in the
    dry-run summary); None means every fork of the lab, the previous
    behaviour.

    Wraps the actual work so that the job is always closed out. _list_target_forks
    converts the expected GitHub failures into PropagateSetupError, but a bare
    `requests` timeout on api.github.com raises RequestException straight
    through - and a job left in "running" keeps (course_id, lab_id) locked in
    _running_lab_keys, so every later run for that lab would 409 (and the admin
    page would poll a never-finishing job) until the backend restarts.
    """
    try:
        _run_propagation(job, github_client, org, github_prefix, template_repo, only_repos)
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
