"""Validate saved artifacts; do not rerun Monte Carlo or contact any services."""
import csv
import gzip
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import numpy as np

from study import ROOT, POLICIES, paired_interval


def read_csv(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def check_array(scores, stats_rows, hist_rows):
    assert np.issubdtype(scores.dtype, np.integer)
    assert ((scores >= 0) & (scores <= 50)).all()
    assert len(scores) == len(stats_rows)
    hist = np.bincount(scores.ravel(), minlength=51)
    assert len(hist_rows) == 51
    for score, row in enumerate(hist_rows):
        assert int(row["score"]) == score and int(row["count"]) == hist[score]
        np.testing.assert_allclose(float(row["share"]), hist[score] / scores.size, atol=1e-12)
    for metric, actual in (("mean", scores.mean(axis=1)), ("median", np.median(scores, axis=1)), ("std", scores.std(axis=1))):
        np.testing.assert_allclose([float(r[metric]) for r in stats_rows], actual, atol=1e-12)
    if "share45" in stats_rows[0]:
        for metric, actual in (("share45", (scores >= 33).mean(axis=1)), ("share345", (scores >= 25).mean(axis=1)), ("share5", (scores >= 45).mean(axis=1)), ("zero_share", (scores == 0).mean(axis=1))):
            np.testing.assert_allclose([float(r[metric]) for r in stats_rows], actual, atol=1e-12)


def main():
    checks = []
    for test in (ROOT / "test_study.py", ROOT.parents[1] / "docs" / "queue" / "test_schema.py"):
        # Use this environment, including externally supplied local package paths.
        import os
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
        proc = subprocess.run([sys.executable, str(test)], capture_output=True, text=True, env=env)
        if proc.returncode:
            raise AssertionError(proc.stdout + proc.stderr)
        checks.append(dict(test=str(test.relative_to(ROOT.parents[1])), output=proc.stderr.strip()))
    original = ROOT / "results" / "original"
    original_runs = 0
    for directory in sorted(original.iterdir()):
        if not directory.is_dir():
            continue
        scores = np.load(directory / "all_scores.npy").reshape(-1, 30)
        assert scores.shape == (10_000, 30)
        check_array(scores, read_csv(directory / "per_run_stats.csv"), read_csv(directory / "score_distribution.csv"))
        original_runs += len(scores)
    assert {p.name for p in original.iterdir() if p.is_dir()} == {
        "random", "priority", "priority_clamped", "priority_clamped_zero",
        "most_passed", "target_three", "surname_k", "odd_number"}
    assert (np.load(original / "target_three" / "all_scores.npy") <= 29).all()
    finalists = ROOT / "results" / "finalists"
    protocol = json.loads((finalists / "protocol.json").read_text())
    scenario_configs = {s["name"]: s for s in protocol["scenarios"]}
    total_runs = 0
    total_students = 0
    per_run = {}
    for path in sorted(finalists.glob("*/*/summary.json")):
        summary = json.loads(path.read_text())
        scenario, seed = path.parent.parent.name, int(path.parent.name)
        config = scenario_configs[scenario]
        assert config == summary["config"]
        for policy in POLICIES:
            scores = np.load(path.parent / f"{policy}_scores.npz")["scores"]
            assert scores.shape == (protocol["runs"], config["students"])
            rows = read_csv(path.parent / f"{policy}_runs.csv.gz")
            check_array(scores, rows, read_csv(path.parent / f"{policy}_histogram.csv"))
            saved = summary["models"][policy]
            np.testing.assert_allclose(saved["pooled"]["mean"], scores.mean())
            np.testing.assert_allclose(saved["pooled"]["median"], np.median(scores))
            np.testing.assert_allclose(saved["pooled"]["std"], scores.std())
            assert sum(saved["grade_counts"].values()) == scores.size
            for metric, value in saved["averaged_over_runs"].items():
                np.testing.assert_allclose(value, np.mean([float(r[metric]) for r in rows]))
            for row in rows:
                assert 0 <= float(row["services"]) <= float(row["capacity_available"])
                assert float(row["wanted"]) == float(row["services"]) + float(row["queue_rejects"])
                assert 0 <= float(row["completed_labs"]) <= config["labs"]
            total_runs += len(scores)
            total_students += scores.size
            per_run[scenario, seed, policy] = rows
    for row in read_csv(finalists / "paired_comparisons.csv"):
        scenario, seed = row["scenario"], int(row["seed"])
        a = np.array([float(r[row["metric"]]) for r in per_run[scenario, seed, "clamp_minus1"]])
        b = np.array([float(r[row["metric"]]) for r in per_run[scenario, seed, row["comparator"]]])
        np.testing.assert_allclose([float(row[k]) for k in ("delta", "ci95_low", "ci95_high")], paired_interval(a, b), atol=1e-12)
    for name, sha in json.loads((ROOT / "original_source_hashes.json").read_text()).items():
        assert hashlib.sha256((ROOT / "source" / name).read_bytes()).hexdigest() == sha
    exam = read_csv(finalists / "exam_scenarios.csv")
    assert len(exam) == 11 * len(POLICIES)
    for policy in POLICIES:
        scores = np.concatenate([np.load(finalists / "baseline" / str(seed) / f"{policy}_scores.npz")["scores"]
                                 for seed in protocol["baseline_seeds"]])
        for row in [r for r in exam if r["policy"] == policy]:
            adjusted = np.minimum(50, scores + int(row["bonus"]))
            np.testing.assert_allclose(float(row["share345"]), (adjusted >= 25).mean())
            np.testing.assert_allclose(float(row["share3"]), ((adjusted >= 25) & (adjusted <= 32)).mean())
            np.testing.assert_allclose(float(row["newly_passing_share"]), ((scores < 25) & (adjusted >= 25)).mean())
    # Check local Markdown targets. Fragments and external references are left to the renderer.
    markdown = [ROOT / "README.md", *list((ROOT.parents[1] / "docs" / "adr").glob("*.md")), *list((ROOT.parents[1] / "docs" / "queue").glob("*.md"))]
    link_count = 0
    for path in markdown:
        for raw in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            target = raw.split("#")[0]
            if not target or re.match(r"[a-z]+:", target):
                continue
            resolved = (path.parent / unquote(target)).resolve()
            generated = {ROOT / "validation.json", ROOT / "artifact_hashes.json"}
            assert resolved.exists() or resolved in generated, (path, target)
            link_count += 1
    report = dict(status="passed", original_runs=original_runs, finalist_runs=total_runs, total_runs=total_runs + original_runs, finalist_student_outcomes=total_students, scenario_count=len(scenario_configs), local_links_checked=link_count, sqlite_version=sqlite3.sqlite_version, checks=checks, invariants=["score range and integer dtype", "all 51 histogram bins and shares", "per-run mean/median/population std and grade shares", "pooled and averaged statistics", "grade totals", "service capacity and wanted=served+rejected", "all paired CI values", "eight models and target-three threshold", "all exam scenarios from saved scores", "model source hashes", "local Markdown targets"])
    (ROOT / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    artifacts = [p for p in ROOT.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != "artifact_hashes.json"]
    (ROOT / "artifact_hashes.json").write_text(json.dumps({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(artifacts)}, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
