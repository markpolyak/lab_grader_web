"""Run registered research models and export their saved statistics."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "source"))

import common
import models
from runner import run_and_save
from study import write_csv
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--only-zero", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()
    out = ROOT / "results" / "original"
    if not args.export_only:
        targets = [models.MODELS["priority_clamped_zero"]] if args.only_zero else models.MODELS.values()
        for cls in targets:
            run_and_save(cls(), args.runs, args.seed, str(out / cls.name))
    rows = []
    for name in sorted(models.MODELS):
        directory = out / name
        if not directory.is_dir() or not (directory / "all_scores.npy").exists():
            continue
        flat = np.load(directory / "all_scores.npy")
        per = json.loads((directory / "per_run_stats.json").read_text())
        write_csv(directory / "per_run_stats.csv", per)
        hist = np.bincount(flat, minlength=51)
        write_csv(directory / "score_distribution.csv", [dict(score=i, count=int(c), share=float(c / flat.size)) for i, c in enumerate(hist)])
        meta = json.loads((directory / "metadata.json").read_text())
        rows.append(dict(model=directory.name, mean=float(flat.mean()), median=float(np.median(flat)), std=float(flat.std()), share45=float((flat >= 33).mean()), share345=float((flat >= 25).mean()), share345_with_hypothetical_exam=float((flat + common.EXAM_BONUS >= 25).mean()), zero_share=float((flat == 0).mean()), n_total=flat.size, n_runs=meta["params"]["n_runs"]))
    write_csv(out / "comparison.csv", rows)
    (ROOT / "original_source_hashes.json").write_text(json.dumps({str(p.relative_to(ROOT / "source")): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "source").rglob("*.py"))}, indent=2) + "\n")


if __name__ == "__main__":
    main()
