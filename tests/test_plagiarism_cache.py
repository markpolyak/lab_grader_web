"""
Unit tests for grading/plagiarism_cache.py
"""
import base64
import json
import os
import sys
from pathlib import Path

import pytest
import responses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.github_client import GitHubClient
from grading.plagiarism_cache import (
    CachedSubmission,
    cache_submission_files,
    convert_ipynb_to_py,
    extract_student_from_repo,
    get_cache_root,
    list_cached_submissions,
    submission_cache_dir,
)


class TestExtractStudentFromRepo:
    def test_with_prefix(self):
        assert extract_student_from_repo("os-task1-alice", "os-task1") == "alice"

    def test_without_matching_prefix(self):
        assert extract_student_from_repo("other-repo", "os-task1") == "other-repo"


class TestCachePaths:
    def test_submission_cache_dir(self, tmp_path):
        path = submission_cache_dir("os-2026", "2", "suai-os", "os-task2-bob", tmp_path)
        assert path == tmp_path / "os-2026" / "2" / "suai-os" / "os-task2-bob"

    def test_get_cache_root_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAGIARISM_CACHE_DIR", str(tmp_path / "custom"))
        assert get_cache_root() == tmp_path / "custom"

    def test_get_cache_root_explicit(self, tmp_path):
        assert get_cache_root(tmp_path) == tmp_path


class TestConvertIpynbToPy:
    def test_converts_code_cells(self, tmp_path):
        nb = {
            "cells": [
                {"cell_type": "markdown", "source": ["# Title"]},
                {"cell_type": "code", "source": ["x = 1\n", "print(x)\n"]},
                {"cell_type": "code", "source": "y = 2\n"},
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        ipynb = tmp_path / "exercises.ipynb"
        ipynb.write_text(json.dumps(nb), encoding="utf-8")

        py_path = convert_ipynb_to_py(ipynb)
        assert py_path == tmp_path / "exercises.py"
        content = py_path.read_text(encoding="utf-8")
        assert "x = 1" in content
        assert "y = 2" in content
        assert "# Title" not in content

    def test_check_existing_reuses_file(self, tmp_path):
        ipynb = tmp_path / "nb.ipynb"
        ipynb.write_text(json.dumps({"cells": []}), encoding="utf-8")
        existing = tmp_path / "nb.py"
        existing.write_text("already here", encoding="utf-8")

        result = convert_ipynb_to_py(ipynb, check_existing=True)
        assert result.read_text(encoding="utf-8") == "already here"

    def test_rejects_non_ipynb(self, tmp_path):
        py = tmp_path / "a.py"
        py.write_text("x = 1", encoding="utf-8")
        with pytest.raises(ValueError):
            convert_ipynb_to_py(py)


def _mock_file_content(org: str, repo: str, path: str, content: bytes, status: int = 200):
    """Register a GitHub contents API mock for a file."""
    url = f"https://api.github.com/repos/{org}/{repo}/contents/{path}"
    if status != 200:
        responses.add(responses.GET, url, json={"message": "Not Found"}, status=status)
        return
    responses.add(
        responses.GET,
        url,
        json={
            "name": path.split("/")[-1],
            "path": path,
            "encoding": "base64",
            "content": base64.b64encode(content).decode("ascii"),
            "type": "file",
        },
        status=200,
    )


class TestCacheSubmissionFiles:
    @responses.activate
    def test_downloads_and_caches_files(self, tmp_path):
        _mock_file_content("org", "lab-alice", "main.cpp", b"int main() {}")
        client = GitHubClient("token")

        result = cache_submission_files(
            client,
            course_id="os-2026",
            lab_id="1",
            org="org",
            repo="lab-alice",
            files=["main.cpp"],
            cache_root=tmp_path,
            github_prefix="lab",
        )

        assert isinstance(result, CachedSubmission)
        assert result.student == "alice"
        assert result.files[0].exists()
        assert result.files[0].read_bytes() == b"int main() {}"
        expected = tmp_path / "os-2026" / "1" / "org" / "lab-alice" / "main.cpp"
        assert expected.exists()

    @responses.activate
    def test_converts_ipynb_on_cache(self, tmp_path):
        nb = {
            "cells": [{"cell_type": "code", "source": ["a = 1\n"]}],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        _mock_file_content(
            "org", "1-bob", "exercises.ipynb",
            json.dumps(nb).encode("utf-8"),
        )
        client = GitHubClient("token")

        result = cache_submission_files(
            client,
            course_id="ml-2026",
            lab_id="1",
            org="org",
            repo="1-bob",
            files=["exercises.ipynb"],
            cache_root=tmp_path,
            github_prefix="1",
        )

        assert result is not None
        assert result.files[0].suffix == ".py"
        assert "a = 1" in result.files[0].read_text(encoding="utf-8")
        assert (result.directory / "exercises.ipynb").exists()

    @responses.activate
    def test_local_path_prefix(self, tmp_path):
        _mock_file_content("org", "lab-carol", "lab3/solution.py", b"print(1)")
        client = GitHubClient("token")

        result = cache_submission_files(
            client,
            course_id="c1",
            lab_id="3",
            org="org",
            repo="lab-carol",
            files=["solution.py"],
            local_path="lab3",
            cache_root=tmp_path,
            github_prefix="lab",
        )

        assert result is not None
        cached = tmp_path / "c1" / "3" / "org" / "lab-carol" / "solution.py"
        assert cached.read_bytes() == b"print(1)"

    @responses.activate
    def test_fallback_when_local_path_misses(self, tmp_path):
        # Prefixed path 404, root path succeeds (matches grading file checks)
        _mock_file_content("org", "lab-dave", "lab1/main.py", b"", status=404)
        _mock_file_content("org", "lab-dave", "main.py", b"ok")
        client = GitHubClient("token")

        result = cache_submission_files(
            client,
            course_id="c1",
            lab_id="1",
            org="org",
            repo="lab-dave",
            files=["main.py"],
            local_path="lab1",
            cache_root=tmp_path,
            github_prefix="lab",
        )

        assert result is not None
        assert result.files[0].read_bytes() == b"ok"

    @responses.activate
    def test_returns_none_when_all_missing(self, tmp_path):
        _mock_file_content("org", "lab-eve", "gone.py", b"", status=404)
        client = GitHubClient("token")

        result = cache_submission_files(
            client,
            course_id="c1",
            lab_id="1",
            org="org",
            repo="lab-eve",
            files=["gone.py"],
            cache_root=tmp_path,
        )
        assert result is None

    def test_returns_none_when_no_files_configured(self, tmp_path):
        client = GitHubClient("token")
        result = cache_submission_files(
            client,
            course_id="c1",
            lab_id="1",
            org="org",
            repo="lab-frank",
            files=[],
            cache_root=tmp_path,
        )
        assert result is None


class TestListCachedSubmissions:
    def test_lists_org_repo_dirs(self, tmp_path):
        (tmp_path / "c1" / "2" / "org-a" / "repo-1").mkdir(parents=True)
        (tmp_path / "c1" / "2" / "org-a" / "repo-2").mkdir(parents=True)
        (tmp_path / "c1" / "2" / "org-b" / "repo-3").mkdir(parents=True)
        # File under lab root should be ignored
        (tmp_path / "c1" / "2" / "readme.txt").write_text("x")

        listed = list_cached_submissions("c1", "2", tmp_path)
        names = {p.name for p in listed}
        assert names == {"repo-1", "repo-2", "repo-3"}

    def test_empty_when_missing(self, tmp_path):
        assert list_cached_submissions("missing", "1", tmp_path) == []
