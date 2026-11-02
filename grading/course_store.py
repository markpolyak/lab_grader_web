"""
Storage of course configs (docs/COURSE_STORAGE_PLAN.md §3-§5).

Two implementations of one interface, chosen by `COURSES_SOURCE`:

- `LocalCourseStore` - the `courses/` directory, read on every call (local
  development and tests; the behaviour before this module existed);
- `GitHubCourseStore` - a private GitHub repository. Reads are served from an
  in-memory snapshot of one branch, refreshed at most every
  `COURSES_REFRESH_SECONDS` and mirrored to disk so the service can start
  while GitHub is down. Every write is one commit made through the Git Data
  API (blobs -> tree -> commit -> ref without force), authored by the teacher.

Concurrent edits are caught by `expected`: the version of each file the
editor saw. A mismatch raises StoreConflictError and nothing is written.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import posixpath
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Protocol

import requests

logger = logging.getLogger(__name__)

INDEX_PATH = "index.yaml"
SNAPSHOT_FILENAME = "snapshot.json"
GITHUB_API = "https://api.github.com"
GITHUB_TIMEOUT_SECONDS = 10


class StoreConflictError(Exception):
    """A file changed since the editor read it - nothing was written."""


class StoreUnavailableError(Exception):
    """The store cannot be reached (or refused the request) - nothing was written."""


@dataclass(frozen=True)
class StoredFile:
    path: str
    content: bytes
    # github: blob sha; local: sha256 of the content
    version: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")


@dataclass(frozen=True)
class Revision:
    sha: str
    date: datetime | None
    # GitHub login, or the commit author's name
    author: str
    message: str

    def to_dict(self) -> dict:
        return {
            "sha": self.sha,
            "date": self.date.isoformat() if self.date else None,
            "author": self.author,
            "message": self.message,
        }


class CourseStore(Protocol):
    def read(self, path: str) -> StoredFile | None: ...

    def list(self, prefix: str = "") -> list[str]: ...

    def write(
        self,
        changes: Mapping[str, bytes | None],
        expected: Mapping[str, str | None],
        message: str,
        author: str,
    ) -> None: ...

    def history(self, path: str, limit: int = 30) -> list[Revision]: ...

    def read_at(self, path: str, sha: str) -> StoredFile | None: ...

    @property
    def supports_history(self) -> bool: ...


def normalize_path(path: str) -> str:
    """
    A store path: relative, forward slashes, no `..`.

    Raises:
        ValueError: the path escapes the store or is empty
    """
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise ValueError(f"Недопустимый путь: {path!r}")
    normalized = posixpath.normpath(path)
    if normalized.startswith("/") or normalized == "." or normalized.split("/")[0] == "..":
        raise ValueError(f"Недопустимый путь: {path!r}")
    return normalized


def content_version(content: bytes) -> str:
    """Version of a file in LocalCourseStore: sha256 of its content."""
    return hashlib.sha256(content).hexdigest()


def write_file_atomically(file_path: str, content: bytes) -> None:
    """
    Write a file entirely or not at all.

    Truncate-and-write left a cut-off YAML when the process died midway; during
    a running test that breaks the link at once. The temporary file is created
    next to the target so that os.replace is an atomic rename on one filesystem.
    """
    directory = os.path.dirname(os.path.abspath(file_path))
    os.makedirs(directory, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".course-", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, file_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _write_order(paths) -> list[str]:
    """Course files before index.yaml: an index never points at a file not yet written."""
    return sorted(paths, key=lambda p: (p == INDEX_PATH, p))


# ---------------------------------------------------------------------------
# Local directory
# ---------------------------------------------------------------------------


class LocalCourseStore:
    """
    The `courses/` directory. Read from disk on every call, as before, so a file
    edited by hand takes effect at once. A multi-file write is not atomic -
    acceptable for development (§5.1 of the plan).
    """

    supports_history = False

    def __init__(self, root: str):
        self.root = root
        self._lock = threading.Lock()

    def _full_path(self, path: str) -> str:
        return os.path.join(self.root, *normalize_path(path).split("/"))

    def read(self, path: str) -> StoredFile | None:
        try:
            full_path = self._full_path(path)
        except ValueError:
            return None
        if not os.path.isfile(full_path):
            return None
        with open(full_path, "rb") as f:
            content = f.read()
        return StoredFile(path=normalize_path(path), content=content, version=content_version(content))

    def list(self, prefix: str = "") -> list[str]:
        if not os.path.isdir(self.root):
            return []
        result = []
        for directory, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(directory, filename), self.root).replace(os.sep, "/")
                if rel.startswith(prefix):
                    result.append(rel)
        return sorted(result)

    def write(self, changes, expected, message, author) -> None:
        with self._lock:
            for path, version in expected.items():
                current = self.read(path)
                current_version = current.version if current else None
                if current_version != version:
                    raise StoreConflictError(path)

            for path in _write_order(changes):
                full_path = self._full_path(path)
                content = changes[path]
                if content is None:
                    if os.path.exists(full_path):
                        os.remove(full_path)
                else:
                    write_file_atomically(full_path, content)

        logger.info(f"Course store (local): {message} (author={author})")

    def history(self, path: str, limit: int = 30) -> list[Revision]:
        raise NotImplementedError("Локальное хранилище не хранит историю версий")

    def read_at(self, path: str, sha: str) -> StoredFile | None:
        raise NotImplementedError("Локальное хранилище не хранит историю версий")


# ---------------------------------------------------------------------------
# Private GitHub repository
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    commit: str
    tree: str
    # path -> blob sha
    files: dict = field(default_factory=dict)
    # blob sha -> content, for exactly the blobs in `files`. Kept per snapshot
    # so a reader holding an older snapshot never finds a blob already dropped.
    contents: dict = field(default_factory=dict)


class _NotFastForward(Exception):
    """The branch moved between reading it and updating the ref."""


class GitHubCourseStore:
    """
    A branch of a private GitHub repository (§5.2-§5.3 of the plan).

    Readers never wait for the network: a refresh due after the interval runs
    in the request that noticed it, under a lock, while other requests keep
    reading the current snapshot.
    """

    supports_history = True

    def __init__(
        self,
        repo: str,
        token: str,
        branch: str = "main",
        refresh_seconds: float = 30,
        cache_dir: str | None = None,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.repo = repo
        self.branch = branch
        self.refresh_seconds = refresh_seconds
        self.cache_dir = cache_dir
        self._session = session or requests.Session()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._clock = clock

        self._snapshot: _Snapshot | None = None
        # blob sha -> content; content behind a sha never changes, so this cache never expires
        self._blobs: dict[str, bytes] = {}
        self._ref_etag: str | None = None
        self._last_check: float | None = None
        self._last_error_logged: float | None = None
        self._refresh_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []

    # -- HTTP --------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{GITHUB_API}/repos/{self.repo}/{path}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            response = self._session.request(
                method, self._url(path), headers=headers, timeout=GITHUB_TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as e:
            raise StoreUnavailableError(f"GitHub недоступен: {e}") from e
        if response.status_code >= 500:
            raise StoreUnavailableError(f"GitHub ответил {response.status_code} на {method} {path}")
        return response

    def _expect_ok(self, response: requests.Response, what: str) -> dict:
        if response.status_code not in (200, 201):
            raise StoreUnavailableError(f"{what}: GitHub ответил {response.status_code}")
        return response.json()

    # -- snapshot ------------------------------------------------------------

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Called after a refresh brought in a new commit (e.g. to re-check token collisions)."""
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in self._listeners:
            try:
                callback()
            except Exception:
                logger.exception("Course store listener failed")

    def initialize(self) -> None:
        """
        Load the first snapshot: from GitHub, or else from the disk copy.

        Raises:
            StoreUnavailableError: neither GitHub nor a snapshot on disk
        """
        try:
            self._refresh(force=True)
            return
        except StoreUnavailableError as e:
            error = e
        if self._load_from_disk():
            logger.warning(
                f"Репозиторий конфигураций {self.repo} недоступен ({error}); "
                f"работа продолжается по снимку с диска, коммит {self._snapshot.commit[:7]}"
            )
            self._last_check = self._clock()
            return
        raise StoreUnavailableError(
            f"Репозиторий конфигураций {self.repo} недоступен ({error}), а снимка на диске "
            f"({self.cache_dir or 'COURSES_CACHE_DIR не задан'}) нет - запуск невозможен"
        )

    def _current(self) -> _Snapshot:
        """The snapshot to read from, refreshing it first when the interval has passed."""
        if self._due():
            if self._refresh_lock.acquire(blocking=self._snapshot is None):
                try:
                    if self._due():
                        self._refresh_locked()
                except StoreUnavailableError as e:
                    self._log_refresh_error(e)
                finally:
                    self._refresh_lock.release()
        if self._snapshot is None:
            raise StoreUnavailableError(f"Нет снимка репозитория конфигураций {self.repo}")
        return self._snapshot

    def _due(self) -> bool:
        return self._last_check is None or self._clock() - self._last_check >= self.refresh_seconds

    def _log_refresh_error(self, error: Exception) -> None:
        now = self._clock()
        if self._last_error_logged is None or now - self._last_error_logged >= self.refresh_seconds:
            logger.error(f"Не удалось обновить снимок конфигураций курсов: {error}")
            self._last_error_logged = now

    def _refresh(self, force: bool) -> None:
        """Refresh under the lock, waiting for it. `force` ignores the interval."""
        with self._refresh_lock:
            if force or self._due():
                self._refresh_locked()

    def _refresh_locked(self) -> None:
        # Counted as a check even when it fails: a dead GitHub is retried after
        # the interval, not on every request.
        self._last_check = self._clock()

        headers = {"If-None-Match": self._ref_etag} if self._ref_etag and self._snapshot else {}
        response = self._request("GET", f"git/ref/heads/{self.branch}", headers=headers)
        if response.status_code == 304:
            return
        data = self._expect_ok(response, f"Ветка {self.branch}")
        commit_sha = data["object"]["sha"]
        etag = response.headers.get("ETag")

        if self._snapshot is not None and commit_sha == self._snapshot.commit:
            self._ref_etag = etag
            return

        tree = self._expect_ok(
            self._request("GET", f"git/trees/{commit_sha}", params={"recursive": "1"}),
            f"Дерево коммита {commit_sha[:7]}",
        )
        if tree.get("truncated"):
            logger.warning(f"Дерево репозитория {self.repo} получено не полностью (truncated)")
        files = {item["path"]: item["sha"] for item in tree.get("tree", []) if item.get("type") == "blob"}

        for blob_sha in set(files.values()) - self._blobs.keys():
            blob = self._expect_ok(self._request("GET", f"git/blobs/{blob_sha}"), f"Блоб {blob_sha[:7]}")
            self._blobs[blob_sha] = base64.b64decode(blob["content"])

        self._install(self._snapshot_of(commit_sha, tree["sha"], files), etag)

    def _snapshot_of(self, commit: str, tree: str, files: dict) -> _Snapshot:
        contents = {sha: self._blobs[sha] for sha in set(files.values())}
        return _Snapshot(commit=commit, tree=tree, files=files, contents=contents)

    def _install(self, snapshot: _Snapshot, etag: str | None) -> None:
        previous = self._snapshot
        self._snapshot = snapshot
        self._ref_etag = etag
        # The cache holds the current tree, not all history.
        self._blobs = dict(snapshot.contents)
        self._save_to_disk()
        if previous is not None and previous.commit != snapshot.commit:
            logger.info(f"Конфигурации курсов обновлены: коммит {snapshot.commit[:7]}")
            self._notify()

    def _snapshot_file(self) -> str | None:
        return os.path.join(self.cache_dir, SNAPSHOT_FILENAME) if self.cache_dir else None

    def _save_to_disk(self) -> None:
        path = self._snapshot_file()
        if not path or self._snapshot is None:
            return
        data = {
            "repo": self.repo,
            "branch": self.branch,
            "commit": self._snapshot.commit,
            "tree": self._snapshot.tree,
            "files": self._snapshot.files,
            "blobs": {
                sha: base64.b64encode(content).decode("ascii")
                for sha, content in self._snapshot.contents.items()
            },
        }
        try:
            write_file_atomically(path, json.dumps(data).encode("utf-8"))
        except OSError as e:
            logger.error(f"Не удалось сохранить снимок конфигураций на диск ({path}): {e}")

    def _load_from_disk(self) -> bool:
        path = self._snapshot_file()
        if not path or not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("repo") != self.repo or data.get("branch") != self.branch:
                logger.warning(f"Снимок на диске ({path}) - от другого репозитория или ветки, не используется")
                return False
            blobs = {sha: base64.b64decode(content) for sha, content in data["blobs"].items()}
            files = data["files"]
            if not set(files.values()) <= blobs.keys():
                logger.warning(f"Снимок на диске ({path}) неполон, не используется")
                return False
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"Снимок на диске ({path}) не читается: {e}")
            return False
        self._blobs = blobs
        self._snapshot = self._snapshot_of(data["commit"], data["tree"], files)
        return True

    # -- reads ---------------------------------------------------------------

    def read(self, path: str) -> StoredFile | None:
        try:
            path = normalize_path(path)
        except ValueError:
            return None
        snapshot = self._current()
        blob_sha = snapshot.files.get(path)
        if blob_sha is None:
            return None
        return StoredFile(path=path, content=snapshot.contents[blob_sha], version=blob_sha)

    def list(self, prefix: str = "") -> list[str]:
        return sorted(p for p in self._current().files if p.startswith(prefix))

    @property
    def commit(self) -> str | None:
        return self._snapshot.commit if self._snapshot else None

    # -- writes --------------------------------------------------------------

    def write(self, changes, expected, message, author) -> None:
        changes = {normalize_path(p): c for p, c in changes.items()}
        expected = {normalize_path(p): v for p, v in expected.items()}

        with self._write_lock:
            for attempt in range(2):
                self._refresh(force=True)
                snapshot = self._snapshot
                for path, version in expected.items():
                    if snapshot.files.get(path) != version:
                        raise StoreConflictError(path)
                try:
                    self._commit(snapshot, changes, message, author)
                    return
                except _NotFastForward:
                    # Someone committed in between. Retried once: a change to
                    # other files passes the expected check, one to the same
                    # files becomes a conflict (§5.3 step 4).
                    logger.info(f"Ветка {self.branch} изменилась во время записи, повтор ({attempt + 1})")
            raise StoreConflictError("Ветка конфигураций меняется слишком часто")

    def _commit(self, snapshot: _Snapshot, changes, message: str, author: str) -> None:
        tree_entries = []
        new_blobs = {}
        for path in _write_order(changes):
            content = changes[path]
            if content is None:
                if path in snapshot.files:
                    tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                continue
            blob = self._expect_ok(
                self._request(
                    "POST", "git/blobs",
                    json={"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
                ),
                f"Создание блоба {path}",
            )
            new_blobs[path] = (blob["sha"], content)
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

        tree = self._expect_ok(
            self._request("POST", "git/trees", json={"base_tree": snapshot.tree, "tree": tree_entries}),
            "Создание дерева",
        )
        commit = self._expect_ok(
            self._request(
                "POST", "git/commits",
                json={
                    "message": message,
                    "tree": tree["sha"],
                    "parents": [snapshot.commit],
                    "author": {"name": author, "email": f"{author}@users.noreply.github.com"},
                },
            ),
            "Создание коммита",
        )
        response = self._request(
            "PATCH", f"git/refs/heads/{self.branch}", json={"sha": commit["sha"], "force": False}
        )
        if response.status_code == 422:
            raise _NotFastForward()
        self._expect_ok(response, f"Обновление ветки {self.branch}")

        files = dict(snapshot.files)
        for path, content in changes.items():
            if content is None:
                files.pop(path, None)
        for path, (blob_sha, _content) in new_blobs.items():
            files[path] = blob_sha

        with self._refresh_lock:
            # Built from the snapshot the commit was made on, not from a cache
            # a concurrent refresh may have replaced meanwhile.
            self._blobs = {**snapshot.contents, **{sha: c for sha, c in new_blobs.values()}}
            self._last_check = self._clock()
            # The ref's ETag is unknown until the next GET - drop it.
            self._install(self._snapshot_of(commit["sha"], tree["sha"], files), None)
        logger.info(f"Конфигурации курсов: коммит {commit['sha'][:7]} «{message}» (author={author})")

    # -- history -------------------------------------------------------------

    def history(self, path: str, limit: int = 30) -> list[Revision]:
        response = self._request(
            "GET", "commits", params={"path": normalize_path(path), "sha": self.branch, "per_page": limit}
        )
        revisions = []
        for item in self._expect_ok(response, f"История {path}"):
            commit = item.get("commit", {})
            commit_author = commit.get("author") or {}
            login = (item.get("author") or {}).get("login")
            date = commit_author.get("date")
            revisions.append(
                Revision(
                    sha=item["sha"],
                    date=datetime.fromisoformat(date.replace("Z", "+00:00")) if date else None,
                    author=login or commit_author.get("name") or "",
                    message=commit.get("message", ""),
                )
            )
        return revisions

    def read_at(self, path: str, sha: str) -> StoredFile | None:
        path = normalize_path(path)
        response = self._request("GET", f"contents/{path}", params={"ref": sha})
        if response.status_code in (404, 422):
            return None
        data = self._expect_ok(response, f"{path} в версии {sha[:7]}")
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        return StoredFile(path=path, content=base64.b64decode(data.get("content", "")), version=data["sha"])


# ---------------------------------------------------------------------------
# Construction from the environment (§9)
# ---------------------------------------------------------------------------


class CourseStoreConfigError(RuntimeError):
    pass


def create_course_store(env: Mapping[str, str], local_root: str):
    """
    Build the store selected by `COURSES_SOURCE` (`local` by default).

    Raises:
        CourseStoreConfigError: unknown source, or `github` without repo/token
        StoreUnavailableError: `github` with neither GitHub nor a snapshot on disk
    """
    source = (env.get("COURSES_SOURCE") or "local").strip().lower()
    if source == "local":
        return LocalCourseStore(local_root)
    if source != "github":
        raise CourseStoreConfigError(f"COURSES_SOURCE должен быть local или github, а не {source!r}")

    repo = (env.get("COURSES_GITHUB_REPO") or "").strip()
    token = (env.get("COURSES_GITHUB_TOKEN") or "").strip()
    if not repo or not token:
        raise CourseStoreConfigError(
            "COURSES_SOURCE=github требует COURSES_GITHUB_REPO и COURSES_GITHUB_TOKEN"
        )
    try:
        refresh_seconds = float(env.get("COURSES_REFRESH_SECONDS") or 30)
    except ValueError:
        raise CourseStoreConfigError("COURSES_REFRESH_SECONDS должен быть числом")

    store = GitHubCourseStore(
        repo=repo,
        token=token,
        branch=(env.get("COURSES_GITHUB_BRANCH") or "main").strip(),
        refresh_seconds=refresh_seconds,
        cache_dir=env.get("COURSES_CACHE_DIR") or "/app/courses-cache",
    )
    store.initialize()
    return store
