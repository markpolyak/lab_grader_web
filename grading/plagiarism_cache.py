"""
Local cache of student source files for plagiarism detection.

Downloads only the files listed in lab config (`files:`) into:
  {cache_root}/{course_id}/{lab_id}/{org}/{repo_name}/{filename}

Notebooks (`.ipynb`) are converted to `.py` before comparison so engines
like compare50/JPlag see source code rather than notebook JSON.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .github_client import GitHubClient

logger = logging.getLogger(__name__)

DEFAULT_CACHE_ROOT = "plagiarism_cache"


@dataclass
class CachedSubmission:
    """Paths of files cached for one student repository."""

    org: str
    repo: str
    student: str
    directory: Path
    files: list[Path]


def get_cache_root(cache_root: str | Path | None = None) -> Path:
    """Resolve plagiarism cache root directory."""
    if cache_root is not None:
        return Path(cache_root)
    env_root = os.environ.get("PLAGIARISM_CACHE_DIR")
    if env_root:
        return Path(env_root)
    return Path(DEFAULT_CACHE_ROOT)


def submission_cache_dir(
    course_id: str,
    lab_id: str,
    org: str,
    repo: str,
    cache_root: str | Path | None = None,
) -> Path:
    """
    Return cache directory for a single submission.

    Layout: {cache_root}/{course_id}/{lab_id}/{org}/{repo}/
    """
    return get_cache_root(cache_root) / course_id / str(lab_id) / org / repo


def extract_student_from_repo(repo: str, github_prefix: str) -> str:
    """
    Extract GitHub username from repo name `{prefix}-{username}`.

    Falls back to the full repo name if the prefix does not match.
    """
    expected = f"{github_prefix}-"
    if repo.startswith(expected):
        return repo[len(expected):]
    return repo


def convert_ipynb_to_py(ipynb_path: str | Path, check_existing: bool = False) -> Path:
    """
    Convert a Jupyter notebook to a plain Python script (code cells only).

    Uses stdlib JSON parsing — no nbconvert dependency required.

    Args:
        ipynb_path: Path to the `.ipynb` file
        check_existing: If True and `.py` already exists, reuse it

    Returns:
        Path to the generated `.py` file
    """
    ipynb_path = Path(ipynb_path)
    if ipynb_path.suffix.lower() != ".ipynb":
        raise ValueError(f"Input file must be a .ipynb file, got: {ipynb_path}")

    py_path = ipynb_path.with_suffix(".py")
    if check_existing and py_path.exists():
        return py_path

    with open(ipynb_path, encoding="utf-8") as f:
        notebook = json.load(f)

    code_parts: list[str] = []
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", [])
        if isinstance(source, list):
            code_parts.append("".join(source))
        elif isinstance(source, str):
            code_parts.append(source)

    py_path.write_text("\n\n".join(code_parts) + ("\n" if code_parts else ""), encoding="utf-8")
    logger.debug("Converted %s -> %s", ipynb_path, py_path)
    return py_path


def _resolve_repo_file_path(filename: str, local_path: str | None) -> str:
    """
    Build the path inside the GitHub repository for a configured file.

    If `local_path` is set and `filename` is not already under it, join them.
    Grading still checks `files:` as given; plagiarism may use `local-path`
    when sources live in a subdirectory (documented moss/plagiarism option).
    """
    if not local_path:
        return filename
    normalized = filename.replace("\\", "/")
    prefix = local_path.strip("/").replace("\\", "/")
    if normalized == prefix or normalized.startswith(prefix + "/"):
        return filename
    return f"{prefix}/{filename}"


def cache_submission_files(
    github: GitHubClient,
    course_id: str,
    lab_id: str,
    org: str,
    repo: str,
    files: list[str],
    *,
    local_path: str | None = None,
    cache_root: str | Path | None = None,
    github_prefix: str | None = None,
) -> CachedSubmission | None:
    """
    Download required lab files for one repo into the local plagiarism cache.

    Converts `.ipynb` files to `.py` after download. Skips missing files
    (logs a warning) — returns None only if nothing was cached.

    Args:
        github: Authenticated GitHub client
        course_id: Course identifier from index.yaml
        lab_id: Lab identifier
        org: GitHub organization
        repo: Repository name (e.g. os-task1-student)
        files: File paths from lab config `files:`
        local_path: Optional subdirectory in the repo (from moss/plagiarism config)
        cache_root: Override cache root directory
        github_prefix: Repo name prefix, used to derive student login

    Returns:
        CachedSubmission with paths to comparison-ready files, or None
    """
    if not files:
        logger.warning("No files configured for %s/%s lab %s — nothing to cache", org, repo, lab_id)
        return None

    dest_dir = submission_cache_dir(course_id, lab_id, org, repo, cache_root)
    dest_dir.mkdir(parents=True, exist_ok=True)

    cached_files: list[Path] = []
    for filename in files:
        repo_path = _resolve_repo_file_path(filename, local_path)
        content = github.get_file_content(org, repo, repo_path)
        if content is None and local_path and repo_path != filename:
            # Fallback: try path as listed in files: (matches grading behavior)
            content = github.get_file_content(org, repo, filename)
            repo_path = filename

        if content is None:
            logger.warning("File %s not found in %s/%s — skipping", repo_path, org, repo)
            continue

        # Preserve relative path under the repo cache dir (use original filename layout)
        local_file = dest_dir / filename
        local_file.parent.mkdir(parents=True, exist_ok=True)
        local_file.write_bytes(content)
        logger.info("Cached %s/%s/%s -> %s", org, repo, repo_path, local_file)

        if local_file.suffix.lower() == ".ipynb":
            py_file = convert_ipynb_to_py(local_file)
            cached_files.append(py_file)
        else:
            cached_files.append(local_file)

    if not cached_files:
        return None

    student = extract_student_from_repo(repo, github_prefix) if github_prefix else repo
    return CachedSubmission(
        org=org,
        repo=repo,
        student=student,
        directory=dest_dir,
        files=cached_files,
    )


def list_cached_submissions(
    course_id: str,
    lab_id: str,
    cache_root: str | Path | None = None,
) -> list[Path]:
    """
    List submission directories already present in the cache for a lab.

    Each entry is `{cache_root}/{course_id}/{lab_id}/{org}/{repo}`.
    """
    lab_root = get_cache_root(cache_root) / course_id / str(lab_id)
    if not lab_root.is_dir():
        return []

    submissions: list[Path] = []
    for org_dir in sorted(lab_root.iterdir()):
        if not org_dir.is_dir():
            continue
        for repo_dir in sorted(org_dir.iterdir()):
            if repo_dir.is_dir():
                submissions.append(repo_dir)
    return submissions
