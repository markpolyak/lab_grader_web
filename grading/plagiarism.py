"""
Plagiarism detection engine wrapper.

Uses compare50's Python API (not the CLI) so it works both in Linux Docker
and on Windows during development. Scores are normalized to [0, 1] relative
to the max raw score in the current run (same approach as compare50's HTML UI).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .plagiarism_cache import (
    extract_student_from_repo,
    get_cache_root,
    list_cached_submissions,
)

logger = logging.getLogger(__name__)

DEFAULT_PLAGIARISM_THRESHOLD = 0.6
DEFAULT_ENGINE = "compare50"
DEFAULT_MAX_MATCHES = 50

# File extensions considered for comparison (ipynb already converted to .py in cache)
COMPARABLE_SUFFIXES = {
    ".py", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    ".java", ".js", ".ts", ".go", ".rs", ".cs", ".rb", ".php",
    ".swift", ".kt", ".kts", ".m", ".mm", ".scala", ".sh", ".bash",
    ".r", ".R", ".sql", ".txt", ".md",
}


@dataclass
class PlagiarismMatch:
    """Normalized plagiarism match between two students."""

    student_a: str
    student_b: str
    similarity: float  # 0..1
    details: str = ""
    raw_score: float = 0.0


@dataclass
class PlagiarismConfig:
    """Resolved plagiarism settings for a lab (from plagiarism: or moss:)."""

    engine: str = DEFAULT_ENGINE
    language: str | None = None
    threshold: float = DEFAULT_PLAGIARISM_THRESHOLD
    local_path: str | None = None
    additional: list[str] | None = None
    basefiles: list[dict[str, str]] | None = None
    max_matches: int = DEFAULT_MAX_MATCHES
    enabled: bool = True


def get_plagiarism_config(lab_config: dict[str, Any] | None) -> PlagiarismConfig | None:
    """
    Resolve plagiarism settings from lab config.

    Prefers `plagiarism:`; falls back to deprecated `moss:` alias.
    Returns None if neither section is present (lab has no plagiarism check).
    """
    if not lab_config:
        return None

    raw = lab_config.get("plagiarism")
    if raw is None:
        raw = lab_config.get("moss")
    if raw is None:
        return None
    if raw is False:
        return PlagiarismConfig(enabled=False)
    if not isinstance(raw, dict):
        return PlagiarismConfig()

    threshold = raw.get("threshold", DEFAULT_PLAGIARISM_THRESHOLD)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = DEFAULT_PLAGIARISM_THRESHOLD
    threshold = max(0.0, min(1.0, threshold))

    max_matches = raw.get("max-matches", DEFAULT_MAX_MATCHES)
    try:
        max_matches = int(max_matches)
    except (TypeError, ValueError):
        max_matches = DEFAULT_MAX_MATCHES

    return PlagiarismConfig(
        engine=str(raw.get("engine", DEFAULT_ENGINE)).lower(),
        language=raw.get("language"),
        threshold=threshold,
        local_path=raw.get("local-path"),
        additional=list(raw.get("additional") or []),
        basefiles=list(raw.get("basefiles") or []),
        max_matches=max(1, max_matches),
        enabled=True,
    )


def _collect_comparable_files(directory: Path) -> list[str]:
    """Return relative paths of comparable source files under directory."""
    files: list[str] = []
    if not directory.is_dir():
        return files
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in COMPARABLE_SUFFIXES:
            continue
        # Prefer converted .py over original .ipynb (ipynb not in suffix list anyway)
        files.append(str(path.relative_to(directory)).replace("\\", "/"))
    return files


def _student_from_submission_dir(submission_dir: Path, github_prefix: str | None = None) -> str:
    """Derive student login from `{org}/{repo}` cache directory."""
    repo = submission_dir.name
    if github_prefix:
        return extract_student_from_repo(repo, github_prefix)
    # Heuristic: last segment after final '-'
    if "-" in repo:
        return repo.rsplit("-", 1)[-1]
    return repo


def _build_compare50_submission(
    directory: Path,
    preprocessor,
    *,
    is_archive: bool = False,
):
    """Build a compare50.Submission from a cached directory."""
    from compare50 import Submission

    files = _collect_comparable_files(directory)
    if not files:
        return None
    return Submission(
        directory,
        files,
        preprocessor=preprocessor,
        is_archive=is_archive,
    )


def _normalize_scores(raw_scores: list[tuple[str, str, float]]) -> list[PlagiarismMatch]:
    """Normalize raw engine scores to similarity in [0, 1]."""
    if not raw_scores:
        return []
    max_score = max(score for _, _, score in raw_scores)
    if max_score <= 0:
        return [
            PlagiarismMatch(a, b, 0.0, raw_score=score)
            for a, b, score in raw_scores
        ]
    return [
        PlagiarismMatch(
            student_a=a,
            student_b=b,
            similarity=round(score / max_score, 4),
            raw_score=score,
        )
        for a, b, score in raw_scores
    ]


def run_compare50(
    submission_dirs: list[Path],
    *,
    archive_dirs: list[Path] | None = None,
    distro_dirs: list[Path] | None = None,
    max_matches: int = DEFAULT_MAX_MATCHES,
    github_prefix: str | None = None,
    output_dir: Path | None = None,
) -> list[PlagiarismMatch]:
    """
    Compare submissions with compare50 structure pass.

    Args:
        submission_dirs: Primary submissions (cross-compared with each other
            and against archives)
        archive_dirs: Archive submissions (e.g. previous years / other students
            when doing incremental check). Not cross-compared with each other.
        distro_dirs: Base/template code directories to exclude from matching
        max_matches: Max number of top pairs to keep
        github_prefix: Repo prefix for extracting student logins
        output_dir: Optional directory for HTML report (index.html)

    Returns:
        Normalized list of PlagiarismMatch ordered by similarity desc
    """
    from compare50 import rank, compare, _api, _data
    from compare50._data import Preprocessor
    from compare50.passes import structure
    from compare50._renderer import render as render_results

    # Windows default encoding (cp1251 etc.) breaks on UTF-8 student sources
    def _read_utf8(self, size=-1):
        with open(self.path, encoding="utf-8", errors="replace") as f:
            return f.read(size)

    _data.File.read = _read_utf8  # type: ignore[method-assign]

    # Serial executor: safe under Windows and FastAPI workers
    _api.Executor = _api.FauxExecutor

    archive_dirs = archive_dirs or []
    distro_dirs = distro_dirs or []

    preprocessor = Preprocessor(structure.preprocessors)

    submissions = []
    for d in submission_dirs:
        sub = _build_compare50_submission(d, preprocessor, is_archive=False)
        if sub is not None:
            submissions.append(sub)

    archives = []
    for d in archive_dirs:
        sub = _build_compare50_submission(d, preprocessor, is_archive=True)
        if sub is not None:
            archives.append(sub)

    ignored_files = set()
    for d in distro_dirs:
        sub = _build_compare50_submission(d, preprocessor, is_archive=False)
        if sub is not None:
            ignored_files.update(sub.files)

    if len(submissions) + len(archives) < 2:
        logger.info("Not enough submissions to compare (%d + %d)", len(submissions), len(archives))
        return []

    if not submissions:
        logger.info("No primary submissions to compare")
        return []

    with _api.progress_bar("Ranking", disable=True):
        scores = rank(submissions, archives, ignored_files, structure, n=max_matches)

    raw: list[tuple[str, str, float]] = []
    for score in scores:
        if score.score <= 0:
            continue
        student_a = _student_from_submission_dir(Path(score.sub_a.path), github_prefix)
        student_b = _student_from_submission_dir(Path(score.sub_b.path), github_prefix)
        # Stable ordering of pair
        if student_a > student_b:
            student_a, student_b = student_b, student_a
        raw.append((student_a, student_b, float(score.score)))

    matches = _normalize_scores(raw)

    if output_dir is not None and scores:
        try:
            import builtins

            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            with _api.progress_bar("Comparing", disable=True):
                pass_to_results = {structure: compare(scores, ignored_files, structure)}

            # compare50 HTML renderer uses open() without encoding; on Windows
            # that is often cp1251 and fails on UTF-8 student sources.
            _real_open = builtins.open

            def _open_utf8(file, mode="r", *args, **kwargs):
                if "b" not in mode and "encoding" not in kwargs:
                    kwargs["encoding"] = "utf-8"
                    kwargs.setdefault("errors", "replace")
                return _real_open(file, mode, *args, **kwargs)

            builtins.open = _open_utf8  # type: ignore[assignment]
            try:
                index_path = render_results(pass_to_results, output_dir)
            finally:
                builtins.open = _real_open  # type: ignore[assignment]

            for match in matches:
                match.details = str(index_path)
        except Exception:
            logger.exception("Failed to render compare50 HTML report to %s", output_dir)

    return matches


def compare_submission_against_cache(
    course_id: str,
    lab_id: str,
    org: str,
    repo: str,
    *,
    cache_root: str | Path | None = None,
    github_prefix: str | None = None,
    max_matches: int = DEFAULT_MAX_MATCHES,
    basefiles_dir: Path | None = None,
    write_report: bool = True,
) -> list[PlagiarismMatch]:
    """
    Incremental check: compare one newly cached submission against all others.

    The new submission is the only regular submission; everyone else in the
    lab cache is treated as archive (not cross-compared among themselves).
    """
    new_dir = get_cache_root(cache_root) / course_id / str(lab_id) / org / repo
    if not new_dir.is_dir():
        logger.warning("New submission cache missing: %s", new_dir)
        return []

    others = [
        d for d in list_cached_submissions(course_id, lab_id, cache_root)
        if d.resolve() != new_dir.resolve()
    ]
    if not others:
        logger.info("No other submissions in cache for %s/%s — skipping compare", course_id, lab_id)
        return []

    report_dir = None
    if write_report:
        report_dir = (
            get_cache_root(cache_root)
            / course_id
            / str(lab_id)
            / "_reports"
            / f"{org}_{repo}"
        )

    distro = [basefiles_dir] if basefiles_dir and basefiles_dir.is_dir() else []

    return run_compare50(
        [new_dir],
        archive_dirs=others,
        distro_dirs=distro,
        max_matches=max_matches,
        github_prefix=github_prefix,
        output_dir=report_dir,
    )


def filter_matches_above_threshold(
    matches: Iterable[PlagiarismMatch],
    threshold: float = DEFAULT_PLAGIARISM_THRESHOLD,
) -> list[PlagiarismMatch]:
    """Return matches with similarity >= threshold, sorted desc."""
    filtered = [m for m in matches if m.similarity >= threshold]
    return sorted(filtered, key=lambda m: m.similarity, reverse=True)


def run_plagiarism_engine(
    engine: str,
    submission_dirs: list[Path],
    **kwargs,
) -> list[PlagiarismMatch]:
    """Dispatch to the selected plagiarism engine."""
    engine = (engine or DEFAULT_ENGINE).lower()
    if engine in ("compare50", "moss"):  # moss alias maps to local compare50 for now
        return run_compare50(submission_dirs, **kwargs)
    raise ValueError(
        f"Unsupported plagiarism engine '{engine}'. "
        "Supported: compare50 (jplag/dolos not implemented yet)."
    )
