"""
Course config storage (docs/COURSE_STORAGE_PLAN.md §13): LocalCourseStore and
GitHubCourseStore with the GitHub API mocked through `responses`.
"""
import base64
import json
import os
import sys

import pytest
import requests
import responses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.course_store import (
    CourseStoreConfigError,
    GitHubCourseStore,
    LocalCourseStore,
    StoreConflictError,
    StoreUnavailableError,
    content_version,
    create_course_store,
)

REPO = "org/configs"
API = f"https://api.github.com/repos/{REPO}"


# ---------------------------------------------------------------------------
# LocalCourseStore
# ---------------------------------------------------------------------------


@pytest.fixture
def local(tmp_path):
    (tmp_path / "index.yaml").write_bytes(b"courses: []\n")
    (tmp_path / "logos").mkdir()
    (tmp_path / "logos" / "a.png").write_bytes(b"\x89PNG")
    return LocalCourseStore(str(tmp_path))


class TestLocalCourseStore:
    def test_read(self, local):
        stored = local.read("index.yaml")
        assert stored.content == b"courses: []\n"
        assert stored.version == content_version(b"courses: []\n")
        assert local.read("logos/a.png").content == b"\x89PNG"

    def test_missing_and_escaping_paths(self, local):
        assert local.read("nope.yaml") is None
        assert local.read("../etc/passwd") is None
        assert local.read("/etc/passwd") is None

    def test_list(self, local):
        assert local.list() == ["index.yaml", "logos/a.png"]
        assert local.list("logos/") == ["logos/a.png"]

    def test_write_and_create(self, local, tmp_path):
        old = local.read("index.yaml").version
        local.write({"index.yaml": b"courses: [x]\n", "c.yaml": b"course: {}\n"},
                    {"index.yaml": old, "c.yaml": None}, "msg", "alice")
        assert (tmp_path / "index.yaml").read_bytes() == b"courses: [x]\n"
        assert (tmp_path / "c.yaml").read_bytes() == b"course: {}\n"

    def test_conflict_writes_nothing(self, local, tmp_path):
        with pytest.raises(StoreConflictError):
            local.write({"index.yaml": b"x", "c.yaml": b"y"}, {"index.yaml": "stale", "c.yaml": None}, "m", "a")
        assert (tmp_path / "index.yaml").read_bytes() == b"courses: []\n"
        assert not (tmp_path / "c.yaml").exists()

    def test_new_file_that_already_exists_is_a_conflict(self, local):
        with pytest.raises(StoreConflictError):
            local.write({"index.yaml": b"x"}, {"index.yaml": None}, "m", "a")

    def test_delete(self, local, tmp_path):
        local.write({"logos/a.png": None}, {"logos/a.png": content_version(b"\x89PNG")}, "m", "a")
        assert not (tmp_path / "logos" / "a.png").exists()

    def test_no_history(self, local):
        assert local.supports_history is False


# ---------------------------------------------------------------------------
# GitHubCourseStore
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


class FakeRepo:
    """Registers GitHub responses for a repository with given commits."""

    def __init__(self, rsps):
        self.rsps = rsps

    def ref(self, commit, etag='"e1"', status=200):
        body = {"object": {"sha": commit}} if status == 200 else None
        headers = {"ETag": etag} if etag else {}
        return self.rsps.add(responses.GET, f"{API}/git/ref/heads/main", json=body, status=status, headers=headers)

    def tree(self, commit, files, tree_sha=None):
        return self.rsps.add(
            responses.GET, f"{API}/git/trees/{commit}",
            json={"sha": tree_sha or f"tree-{commit}", "truncated": False,
                  "tree": [{"path": p, "type": "blob", "sha": s} for p, s in files.items()]},
        )

    def blob(self, sha, content):
        return self.rsps.add(responses.GET, f"{API}/git/blobs/{sha}", json={"content": b64(content), "encoding": "base64"})


def make_store(tmp_path=None, clock=None, refresh=30):
    return GitHubCourseStore(
        repo=REPO, token="t", refresh_seconds=refresh,
        cache_dir=str(tmp_path / "cache") if tmp_path else None, clock=clock or Clock(),
    )


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


@pytest.fixture
def repo(rsps):
    fake = FakeRepo(rsps)
    fake.ref("c1")
    fake.tree("c1", {"index.yaml": "b-index", "os.yaml": "b-os"})
    fake.blob("b-index", b"courses: []\n")
    fake.blob("b-os", b"course: {name: OS}\n")
    return fake


def calls(rsps, method, suffix):
    return [c for c in rsps.calls if c.request.method == method and c.request.url.split("?")[0].endswith(suffix)]


class TestGitHubReads:
    def test_snapshot_from_ref_tree_and_blobs(self, rsps, repo, tmp_path):
        store = make_store(tmp_path)
        store.initialize()
        stored = store.read("os.yaml")
        assert stored.content == b"course: {name: OS}\n"
        assert stored.version == "b-os"
        assert store.list() == ["index.yaml", "os.yaml"]
        assert store.read("missing.yaml") is None
        assert store.supports_history is True

    def test_reads_within_interval_make_no_requests(self, rsps, repo):
        store = make_store()
        store.initialize()
        before = len(rsps.calls)
        store.read("os.yaml")
        store.list()
        assert len(rsps.calls) == before

    def test_304_does_not_load_the_tree(self, rsps, repo):
        clock = Clock()
        store = make_store(clock=clock)
        store.initialize()
        rsps.replace(responses.GET, f"{API}/git/ref/heads/main", status=304)
        clock.now += 31
        store.read("os.yaml")
        assert len(calls(rsps, "GET", "/git/trees/c1")) == 1
        assert calls(rsps, "GET", "/git/ref/heads/main")[-1].request.headers["If-None-Match"] == '"e1"'

    def test_new_commit_picked_up_after_interval_without_refetching_blobs(self, rsps, repo):
        clock = Clock()
        store = make_store(clock=clock)
        store.initialize()
        seen = []
        store.add_listener(lambda: seen.append(store.commit))

        rsps.replace(responses.GET, f"{API}/git/ref/heads/main", json={"object": {"sha": "c2"}}, headers={"ETag": '"e2"'})
        repo.tree("c2", {"index.yaml": "b-index", "os.yaml": "b-os2"})
        repo.blob("b-os2", b"course: {name: OS2}\n")

        clock.now += 10
        assert store.read("os.yaml").version == "b-os"  # interval not over yet
        clock.now += 25
        assert store.read("os.yaml").content == b"course: {name: OS2}\n"
        assert len(calls(rsps, "GET", "/git/blobs/b-index")) == 1
        assert seen == ["c2"]

    def test_github_down_during_work_keeps_last_snapshot(self, rsps, repo):
        clock = Clock()
        store = make_store(clock=clock)
        store.initialize()
        rsps.replace(responses.GET, f"{API}/git/ref/heads/main", status=502)
        clock.now += 31
        assert store.read("os.yaml").content == b"course: {name: OS}\n"


class TestGitHubStartup:
    def test_starts_from_disk_snapshot_when_github_is_down(self, rsps, repo, tmp_path):
        make_store(tmp_path).initialize()  # writes the disk snapshot
        rsps.replace(responses.GET, f"{API}/git/ref/heads/main", body=requests.ConnectionError("down"))

        store = make_store(tmp_path)
        store.initialize()
        assert store.read("os.yaml").content == b"course: {name: OS}\n"

    def test_neither_github_nor_snapshot_is_a_clear_error(self, rsps, tmp_path):
        rsps.add(responses.GET, f"{API}/git/ref/heads/main", body=requests.ConnectionError("down"))
        with pytest.raises(StoreUnavailableError, match="снимка на диске"):
            make_store(tmp_path).initialize()

    def test_bad_token_without_snapshot_fails(self, rsps, tmp_path):
        rsps.add(responses.GET, f"{API}/git/ref/heads/main", status=401, json={})
        with pytest.raises(StoreUnavailableError):
            make_store(tmp_path).initialize()


def add_write_responses(rsps, commit="c-new", ref_status=200):
    counter = {"n": 0}

    def blob_callback(request):
        counter["n"] += 1
        return 201, {}, json.dumps({"sha": f"new-blob-{counter['n']}"})

    rsps.add_callback(responses.POST, f"{API}/git/blobs", callback=blob_callback)
    rsps.add(responses.POST, f"{API}/git/trees", json={"sha": "tree-new"}, status=201)
    rsps.add(responses.POST, f"{API}/git/commits", json={"sha": commit}, status=201)
    rsps.add(responses.PATCH, f"{API}/git/refs/heads/main", json={}, status=ref_status)


def body(call):
    return json.loads(call.request.body)


class TestGitHubWrites:
    def test_single_file(self, rsps, repo, tmp_path):
        store = make_store(tmp_path)
        store.initialize()
        add_write_responses(rsps)

        store.write({"os.yaml": b"course: {name: New}\n"}, {"os.yaml": "b-os"}, "os: правка", "alice")

        blob = body(calls(rsps, "POST", "/git/blobs")[0])
        assert base64.b64decode(blob["content"]) == b"course: {name: New}\n"
        tree = body(calls(rsps, "POST", "/git/trees")[0])
        assert tree["base_tree"] == "tree-c1"
        assert tree["tree"] == [{"path": "os.yaml", "mode": "100644", "type": "blob", "sha": "new-blob-1"}]
        commit = body(calls(rsps, "POST", "/git/commits")[0])
        assert commit["parents"] == ["c1"]
        assert commit["author"] == {"name": "alice", "email": "alice@users.noreply.github.com"}
        assert commit["message"] == "os: правка"
        assert body(calls(rsps, "PATCH", "/git/refs/heads/main")[0]) == {"sha": "c-new", "force": False}

        # Visible at once, without waiting for the interval or a re-read
        assert store.read("os.yaml").content == b"course: {name: New}\n"
        assert store.commit == "c-new"
        saved = json.loads((tmp_path / "cache" / "snapshot.json").read_text())
        assert saved["commit"] == "c-new"

    def test_two_files_one_commit(self, rsps, repo):
        store = make_store()
        store.initialize()
        add_write_responses(rsps)

        store.write({"new.yaml": b"course: {}\n", "index.yaml": b"courses: [new]\n"},
                    {"new.yaml": None, "index.yaml": "b-index"}, "new: новый курс", "alice")

        assert len(calls(rsps, "POST", "/git/blobs")) == 2
        assert len(calls(rsps, "POST", "/git/trees")) == 1
        assert len(calls(rsps, "POST", "/git/commits")) == 1
        assert len(calls(rsps, "PATCH", "/git/refs/heads/main")) == 1
        paths = [e["path"] for e in body(calls(rsps, "POST", "/git/trees")[0])["tree"]]
        assert paths == ["new.yaml", "index.yaml"]
        assert store.read("new.yaml").content == b"course: {}\n"

    def test_delete(self, rsps, repo):
        store = make_store()
        store.initialize()
        add_write_responses(rsps)
        store.write({"os.yaml": None}, {"os.yaml": "b-os"}, "rm", "alice")
        assert body(calls(rsps, "POST", "/git/trees")[0])["tree"] == [
            {"path": "os.yaml", "mode": "100644", "type": "blob", "sha": None}
        ]
        assert store.read("os.yaml") is None

    def test_stale_version_is_a_conflict_without_writes(self, rsps, repo):
        store = make_store()
        store.initialize()
        add_write_responses(rsps)
        with pytest.raises(StoreConflictError):
            store.write({"os.yaml": b"x"}, {"os.yaml": "older"}, "m", "alice")
        assert not calls(rsps, "POST", "/git/blobs")
        assert not calls(rsps, "PATCH", "/git/refs/heads/main")

    def test_ref_moved_by_other_file_is_retried(self, rsps, repo):
        store = make_store()
        store.initialize()
        add_write_responses(rsps, ref_status=422)
        # Between the two attempts someone committed a change to index.yaml only
        rsps.replace(responses.GET, f"{API}/git/ref/heads/main", json={"object": {"sha": "c2"}})
        repo.tree("c2", {"index.yaml": "b-index2", "os.yaml": "b-os"})
        repo.blob("b-index2", b"courses: [other]\n")
        rsps.upsert(responses.PATCH, f"{API}/git/refs/heads/main", json={}, status=200)

        # First PATCH -> 422, retry -> 200
        state = {"n": 0}

        def patch_callback(request):
            state["n"] += 1
            return (422, {}, "{}") if state["n"] == 1 else (200, {}, "{}")

        rsps.remove(responses.PATCH, f"{API}/git/refs/heads/main")
        rsps.add_callback(responses.PATCH, f"{API}/git/refs/heads/main", callback=patch_callback)

        store.write({"os.yaml": b"course: {name: Mine}\n"}, {"os.yaml": "b-os"}, "m", "alice")
        assert state["n"] == 2
        assert body(calls(rsps, "POST", "/git/commits")[-1])["parents"] == ["c2"]
        assert store.read("index.yaml").content == b"courses: [other]\n"
        assert store.read("os.yaml").content == b"course: {name: Mine}\n"

    def test_ref_moved_by_same_file_is_a_conflict(self, rsps, repo):
        store = make_store()
        store.initialize()
        add_write_responses(rsps, ref_status=422)
        # The write's first refresh still sees c1; after the 422 the branch is at c2
        heads = iter(["c1", "c2"])
        rsps.remove(responses.GET, f"{API}/git/ref/heads/main")
        rsps.add_callback(
            responses.GET, f"{API}/git/ref/heads/main",
            callback=lambda request: (200, {}, json.dumps({"object": {"sha": next(heads)}})),
        )
        repo.tree("c2", {"index.yaml": "b-index", "os.yaml": "b-os-theirs"})
        repo.blob("b-os-theirs", b"course: {name: Theirs}\n")

        with pytest.raises(StoreConflictError):
            store.write({"os.yaml": b"course: {name: Mine}\n"}, {"os.yaml": "b-os"}, "m", "alice")
        assert len(calls(rsps, "PATCH", "/git/refs/heads/main")) == 1

    def test_5xx_is_unavailable(self, rsps, repo):
        store = make_store()
        store.initialize()
        rsps.add(responses.POST, f"{API}/git/blobs", status=502)
        with pytest.raises(StoreUnavailableError):
            store.write({"os.yaml": b"x"}, {"os.yaml": "b-os"}, "m", "alice")
        assert store.read("os.yaml").content == b"course: {name: OS}\n"

    def test_github_down_on_write_is_unavailable(self, rsps, repo):
        store = make_store()
        store.initialize()
        rsps.replace(responses.GET, f"{API}/git/ref/heads/main", body=requests.ConnectionError("down"))
        with pytest.raises(StoreUnavailableError):
            store.write({"os.yaml": b"x"}, {"os.yaml": "b-os"}, "m", "alice")


class TestGitHubHistory:
    def test_history(self, rsps, repo):
        store = make_store()
        store.initialize()
        rsps.add(
            responses.GET, f"{API}/commits",
            json=[
                {"sha": "c2", "author": {"login": "alice"},
                 "commit": {"message": "os: правка через админку",
                            "author": {"name": "alice", "date": "2026-11-02T10:00:00Z"}}},
                {"sha": "c1", "author": None,
                 "commit": {"message": "import", "author": {"name": "Mark", "date": "2026-10-01T09:00:00Z"}}},
            ],
        )
        revisions = store.history("os.yaml", limit=5)
        assert [(r.sha, r.author, r.message) for r in revisions] == [
            ("c2", "alice", "os: правка через админку"), ("c1", "Mark", "import"),
        ]
        assert revisions[0].date.year == 2026
        request = calls(rsps, "GET", "/commits")[0].request
        assert "path=os.yaml" in request.url and "sha=main" in request.url and "per_page=5" in request.url

    def test_read_at(self, rsps, repo):
        store = make_store()
        store.initialize()
        rsps.add(responses.GET, f"{API}/contents/os.yaml",
                 json={"type": "file", "sha": "b-old", "content": b64(b"course: {name: Old}\n")})
        stored = store.read_at("os.yaml", "c0")
        assert stored.content == b"course: {name: Old}\n"
        assert "ref=c0" in calls(rsps, "GET", "/contents/os.yaml")[0].request.url

    def test_read_at_missing(self, rsps, repo):
        store = make_store()
        store.initialize()
        rsps.add(responses.GET, f"{API}/contents/os.yaml", status=404, json={})
        assert store.read_at("os.yaml", "c0") is None


class TestCreateCourseStore:
    def test_local_by_default(self, tmp_path):
        assert isinstance(create_course_store({}, str(tmp_path)), LocalCourseStore)

    def test_github_without_repo_or_token(self, tmp_path):
        with pytest.raises(CourseStoreConfigError):
            create_course_store({"COURSES_SOURCE": "github", "COURSES_GITHUB_REPO": REPO}, str(tmp_path))

    def test_unknown_source(self, tmp_path):
        with pytest.raises(CourseStoreConfigError):
            create_course_store({"COURSES_SOURCE": "s3"}, str(tmp_path))

    def test_github(self, rsps, repo, tmp_path):
        store = create_course_store(
            {"COURSES_SOURCE": "github", "COURSES_GITHUB_REPO": REPO, "COURSES_GITHUB_TOKEN": "t",
             "COURSES_CACHE_DIR": str(tmp_path)},
            "unused",
        )
        assert store.read("os.yaml").version == "b-os"
