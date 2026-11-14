"""Paired Monte Carlo study. Synthetic cohorts only; no application imports."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import platform
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
POLICIES = ("random", "clamp0", "clamp_minus1")
LABELS = {"random": "Случайная", "clamp0": "Приоритет ≥ 0", "clamp_minus1": "Приоритет ≥ −1"}


@dataclass(frozen=True)
class Scenario:
    name: str = "baseline"
    students: int = 30
    labs: int = 10
    weeks: int = 30
    capacity: int = 20
    distribution: str = "normal"
    probability_mean: float = .5
    attendance: float = 1.
    deadline_spacing: int = 3
    penalty: int = 1
    difficulty: bool = False
    release_before_deadline: int | None = None
    cancelled_week: int = 0
    extra_capacity_week: int = 0


def scenarios():
    base = Scenario()
    yield base
    for cap in (10, 15, 25, 30):
        yield replace(base, name=f"capacity_{cap}", capacity=cap)
    for dist in ("uniform", "beta"):
        yield replace(base, name=dist, distribution=dist)
    for mean in (.3, .7):
        yield replace(base, name=f"probability_{mean}", probability_mean=mean)
    for attendance in (.6, .8):
        yield replace(base, name=f"attendance_{attendance}", attendance=attendance)
    for count in (20, 40, 60):
        yield replace(base, name=f"students_{count}", students=count)
    for weeks in (22, 40):
        yield replace(base, name=f"weeks_{weeks}", weeks=weeks)
    for spacing in (2, 4):
        yield replace(base, name=f"deadline_spacing_{spacing}", deadline_spacing=spacing)
    for penalty in (0, 2):
        yield replace(base, name=f"penalty_{penalty}", penalty=penalty)
    yield replace(base, name="lab_difficulty", difficulty=True)
    yield replace(base, name="release_due_minus2", release_before_deadline=2)
    yield replace(base, name="cancel_week10", cancelled_week=10)
    yield replace(base, name="extra_capacity_week10", extra_capacity_week=10)


def priority(deadline_delta, policy):
    if policy == "random":
        return np.zeros_like(deadline_delta)
    return np.maximum(0 if policy == "clamp0" else -1, deadline_delta)


def simulate(config: Scenario, policy: str, runs: int, seed: int):
    """The same cohort/week draws are used for each policy, including absent students."""
    rng = np.random.default_rng(seed)
    shape = (runs, config.students)
    if config.distribution == "uniform":
        p = rng.uniform(0, 1, shape)
    elif config.distribution == "beta":
        p = rng.beta(5, 5, shape)
    else:
        p = np.clip(rng.normal(config.probability_mean, .15, shape), .05, .95)
    current = np.zeros(shape, dtype=np.int16)
    scores = np.zeros(shape, dtype=np.int16)
    times_served = np.zeros(shape, dtype=np.int16)
    waiting = np.zeros(shape, dtype=np.int16)
    max_wait = np.zeros(shape, dtype=np.int16)
    wanted = np.zeros(runs, dtype=np.int32)
    rejected = np.zeros(runs, dtype=np.int32)
    service = np.zeros(runs, dtype=np.int32)
    capacity_available = np.zeros(runs, dtype=np.int32)
    deadlines = np.arange(1, config.labs + 1) * config.deadline_spacing - 1
    for week in range(1, config.weeks + 1):
        # Fixed-size draws prevent a policy's progress from changing future random numbers.
        present = rng.random(shape) < config.attendance
        ties = rng.random(shape)
        rolls = rng.random(shape)
        lab = np.minimum(current, config.labs - 1)
        due = deadlines[lab]
        eligible = (current < config.labs) & present
        if config.release_before_deadline is not None:
            eligible &= week >= np.maximum(1, due - config.release_before_deadline)
        cap = config.capacity
        if week == config.cancelled_week:
            cap = 0
        if week == config.extra_capacity_week:
            cap *= 2
        capacity_available += cap
        wanted += eligible.sum(axis=1)
        keys = np.where(eligible, priority(due - week, policy), 1_000_000)
        order = np.lexsort((ties, keys), axis=1)[:, :min(cap, config.students)]
        selected = np.zeros(shape, dtype=bool)
        np.put_along_axis(selected, order, True, axis=1)
        selected &= eligible
        skipped = eligible & ~selected
        rejected += skipped.sum(axis=1)
        service += selected.sum(axis=1)
        times_served += selected
        waiting = np.where(skipped, waiting + 1, 0)
        max_wait = np.maximum(max_wait, waiting)
        chance = p
        if config.difficulty:
            chance = np.clip(p * (.8 + .4 * lab / max(1, config.labs - 1)), 0, 1)
        passed = selected & (rolls < chance)
        award = np.maximum(0, 5 - config.penalty * np.maximum(0, week - due))
        scores += np.where(passed, award, 0).astype(np.int16)
        current += passed
    metrics = {
        "mean": scores.mean(axis=1), "median": np.median(scores, axis=1),
        "std": scores.std(axis=1, ddof=0),
        "share45": (scores >= 33).mean(axis=1),
        "share345": (scores >= 25).mean(axis=1),
        "share5": (scores >= 45).mean(axis=1),
        "zero_share": (scores == 0).mean(axis=1),
        "queue_rejects": rejected, "wanted": wanted,
        "services": service, "capacity_available": capacity_available,
        "completed_labs": current.mean(axis=1),
        "max_consecutive_skips": max_wait.max(axis=1),
        "never_served_share": (times_served == 0).mean(axis=1),
    }
    return scores, metrics


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(scores, metrics):
    hist = np.bincount(scores.ravel(), minlength=51)
    grades = {str(g): int(hist[lo:hi + 1].sum()) for g, lo, hi in ((2, 0, 24), (3, 25, 32), (4, 33, 44), (5, 45, 50))}
    return {
        "n_runs": len(scores), "n_students_total": scores.size,
        "averaged_over_runs": {key: float(value.mean()) for key, value in metrics.items()},
        "pooled": {"mean": float(scores.mean()), "median": float(np.median(scores)), "std": float(scores.std(ddof=0))},
        "grade_counts": grades,
        "queue_reject_share": float(metrics["queue_rejects"].sum() / max(1, metrics["wanted"].sum())),
        "utilization": float(metrics["services"].sum() / max(1, metrics["capacity_available"].sum())),
    }


def paired_interval(a, b):
    delta = a - b
    mean = float(delta.mean())
    half = float(1.96 * delta.std(ddof=1) / np.sqrt(len(delta)))
    return mean, mean - half, mean + half


def run_study(runs, seeds, out):
    comparisons = []
    all_summary = []
    for scenario in scenarios():
        # Three independent seeds for baseline; sensitivity scenarios use the first seed.
        for seed in seeds if scenario.name == "baseline" else seeds[:1]:
            directory = out / scenario.name / str(seed)
            directory.mkdir(parents=True, exist_ok=True)
            results = {}
            summary = {}
            for policy in POLICIES:
                scores, metrics = simulate(scenario, policy, runs, seed)
                results[policy] = metrics
                np.savez_compressed(directory / f"{policy}_scores.npz", scores=scores)
                write_csv(directory / f"{policy}_runs.csv.gz", [dict(run=i, **{k: v[i].item() for k, v in metrics.items()}) for i in range(runs)])
                hist = np.bincount(scores.ravel(), minlength=51)
                write_csv(directory / f"{policy}_histogram.csv", [dict(score=i, count=int(x), share=float(x / scores.size)) for i, x in enumerate(hist)])
                summary[policy] = summarize(scores, metrics)
                all_summary.append(dict(scenario=scenario.name, seed=seed, policy=policy, **summary[policy]["averaged_over_runs"], queue_reject_share=summary[policy]["queue_reject_share"], utilization=summary[policy]["utilization"]))
            (directory / "summary.json").write_text(json.dumps(dict(config=asdict(scenario), seed=seed, models=summary), ensure_ascii=False, indent=2) + "\n")
            for comparator in ("random", "clamp0"):
                for metric in ("share45", "share345", "mean", "zero_share"):
                    delta, lo, hi = paired_interval(results["clamp_minus1"][metric], results[comparator][metric])
                    comparisons.append(dict(scenario=scenario.name, seed=seed, comparator=comparator, metric=metric, delta=delta, ci95_low=lo, ci95_high=hi, n_runs=runs))
            print(f"completed {scenario.name}, seed={seed}", flush=True)
    write_csv(out / "summary.csv", all_summary)
    write_csv(out / "paired_comparisons.csv", comparisons)
    manifest = dict(runs=runs, baseline_seeds=seeds, sensitivity_seed=seeds[0], scenarios=[asdict(s) for s in scenarios()], python=sys.version, numpy=np.__version__, platform=platform.platform(), std_ddof=0, interval="paired semester-level normal CI, 1.96*SE; descriptive, unadjusted for multiple comparisons")
    (out / "protocol.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10_000)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 1042, 2042])
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "finalists")
    args = parser.parse_args()
    if args.runs < 2 or len(set(args.seeds)) != len(args.seeds):
        parser.error("runs >= 2 and unique seeds are required")
    run_study(args.runs, args.seeds, args.out)
    paths = [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    (ROOT / "source_hashes.json").write_text(json.dumps({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}, indent=2) + "\n")


if __name__ == "__main__":
    main()
