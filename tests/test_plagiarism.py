"""
Unit tests for grading/plagiarism.py

Uses real compare50 API on local fixtures (identical / different / shared template).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.plagiarism import (
    DEFAULT_PLAGIARISM_THRESHOLD,
    PlagiarismConfig,
    compare_submission_against_cache,
    filter_matches_above_threshold,
    get_plagiarism_config,
    run_compare50,
)


IDENTICAL_CODE = "\n".join(
    [
        "def foo(x):",
        "    total = 0",
        "    for i in range(x):",
        "        total += i * 2",
        "        if i % 3 == 0:",
        "            total -= 1",
        "    return total",
        "",
        "def helper(values):",
        "    result = []",
        "    for v in values:",
        "        result.append(foo(v))",
        "    return result",
        "",
        "print(helper([1, 2, 3, 4, 5]))",
    ]
)

DIFFERENT_CODE = "\n".join(
    [
        "class Calculator:",
        "    def __init__(self, name):",
        "        self.name = name",
        "        self.history = []",
        "",
        "    def compute(self, items):",
        "        acc = 1",
        "        for item in items:",
        "            acc *= max(item, 1)",
        "            self.history.append(acc)",
        "        return acc",
        "",
        "calc = Calculator('demo')",
        "print(calc.compute([2, 3, 4]))",
    ]
)

TEMPLATE_CODE = "\n".join(
    [
        "# Lab template — do not copy solutions",
        "def main():",
        "    print('TODO: implement')",
        "",
        "if __name__ == '__main__':",
        "    main()",
    ]
)


def _write_submission(root: Path, org: str, repo: str, code: str, filename: str = "main.py") -> Path:
    dest = root / org / repo
    dest.mkdir(parents=True, exist_ok=True)
    (dest / filename).write_text(code, encoding="utf-8")
    return dest


class TestGetPlagiarismConfig:
    def test_missing_returns_none(self):
        assert get_plagiarism_config({}) is None
        assert get_plagiarism_config({"files": ["a.py"]}) is None

    def test_moss_alias(self):
        cfg = get_plagiarism_config({"moss": {"language": "python", "threshold": 0.7}})
        assert isinstance(cfg, PlagiarismConfig)
        assert cfg.language == "python"
        assert cfg.threshold == 0.7
        assert cfg.engine == "compare50"

    def test_plagiarism_preferred_over_moss(self):
        cfg = get_plagiarism_config({
            "moss": {"language": "c"},
            "plagiarism": {"language": "python", "engine": "compare50"},
        })
        assert cfg.language == "python"

    def test_default_threshold(self):
        cfg = get_plagiarism_config({"plagiarism": {"language": "cc"}})
        assert cfg.threshold == DEFAULT_PLAGIARISM_THRESHOLD

    def test_disabled(self):
        cfg = get_plagiarism_config({"plagiarism": False})
        assert cfg is not None
        assert cfg.enabled is False


class TestRunCompare50:
    def test_identical_code_high_similarity(self, tmp_path):
        a = _write_submission(tmp_path, "org", "lab-alice", IDENTICAL_CODE)
        b = _write_submission(tmp_path, "org", "lab-bob", IDENTICAL_CODE)

        matches = run_compare50([a, b], github_prefix="lab")

        assert matches, "identical submissions should produce a match"
        top = matches[0]
        assert {top.student_a, top.student_b} == {"alice", "bob"}
        assert top.similarity == pytest.approx(1.0)

    def test_different_code_no_or_low_match(self, tmp_path):
        a = _write_submission(tmp_path, "org", "lab-alice", IDENTICAL_CODE)
        b = _write_submission(tmp_path, "org", "lab-bob", DIFFERENT_CODE)

        matches = run_compare50([a, b], github_prefix="lab")
        # Completely different structure typically yields no scores
        assert all(m.similarity < 0.5 for m in matches)

    def test_shared_template_excluded(self, tmp_path):
        # Both students start from template + identical solution body
        solution = IDENTICAL_CODE
        a = _write_submission(tmp_path / "subs", "org", "lab-alice", TEMPLATE_CODE + "\n" + solution)
        b = _write_submission(tmp_path / "subs", "org", "lab-bob", TEMPLATE_CODE + "\n" + solution)
        distro = tmp_path / "distro"
        distro.mkdir()
        (distro / "template.py").write_text(TEMPLATE_CODE, encoding="utf-8")

        matches = run_compare50([a, b], distro_dirs=[distro], github_prefix="lab")
        # Still similar due to shared solution, but should not crash; template stripped
        assert isinstance(matches, list)

    def test_incremental_new_vs_archives(self, tmp_path):
        new = _write_submission(tmp_path, "org", "lab-alice", IDENTICAL_CODE)
        old1 = _write_submission(tmp_path, "org", "lab-bob", IDENTICAL_CODE)
        old2 = _write_submission(tmp_path, "org", "lab-carol", DIFFERENT_CODE)

        matches = run_compare50(
            [new],
            archive_dirs=[old1, old2],
            github_prefix="lab",
        )
        assert matches
        assert matches[0].similarity == pytest.approx(1.0)
        students = {matches[0].student_a, matches[0].student_b}
        assert "alice" in students
        assert "bob" in students

    def test_not_enough_submissions(self, tmp_path):
        only = _write_submission(tmp_path, "org", "lab-alice", IDENTICAL_CODE)
        assert run_compare50([only], github_prefix="lab") == []


class TestCompareAgainstCache:
    def test_compares_new_against_existing(self, tmp_path):
        lab_root = tmp_path / "course" / "1"
        _write_submission(lab_root, "org", "lab-bob", IDENTICAL_CODE)
        _write_submission(lab_root, "org", "lab-alice", IDENTICAL_CODE)

        matches = compare_submission_against_cache(
            "course",
            "1",
            "org",
            "lab-alice",
            cache_root=tmp_path,
            github_prefix="lab",
            write_report=False,
        )
        assert matches
        assert matches[0].similarity == pytest.approx(1.0)


class TestFilterMatches:
    def test_filters_by_threshold(self):
        from grading.plagiarism import PlagiarismMatch

        matches = [
            PlagiarismMatch("a", "b", 0.9),
            PlagiarismMatch("a", "c", 0.4),
            PlagiarismMatch("b", "c", 0.6),
        ]
        filtered = filter_matches_above_threshold(matches, 0.6)
        assert [m.similarity for m in filtered] == [0.9, 0.6]
