"""
Compare compare50 vs JPlag on a cached lab corpus (Phase 0 engine choice).

Usage:
  python scripts/compare_engines_jplag_compare50.py --course os-2025-spring --lab 2
  python scripts/compare_engines_jplag_compare50.py --course os-2025-spring --lab 3 --top 30

Requires: Java 21+, tools/jplag.jar (download from JPlag releases), compare50 installed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grading.plagiarism import run_compare50 as run_compare50_engine  # noqa: E402
from grading.plagiarism_cache import (  # noqa: E402
    extract_student_from_repo,
    get_cache_root,
    list_cached_submissions,
)

DEFAULT_JPLAG_JAR = ROOT / "tools" / "jplag.jar"


def list_real_submissions(course_id: str, lab_id: str) -> list[Path]:
    """Cached submission dirs, skipping internal `_reports` / markers."""
    return [
        d
        for d in list_cached_submissions(course_id, lab_id)
        if not d.parent.name.startswith("_") and not d.name.startswith("_")
    ]


def _github_prefix_for_lab(course_id: str, lab_id: str) -> str | None:
    """Best-effort: infer prefix from first cached repo name (os-task2-Student)."""
    subs = list_real_submissions(course_id, lab_id)
    if not subs:
        return None
    name = subs[0].name
    if "-" not in name:
        return None
    return name.rsplit("-", 1)[0]


def _student_label(repo_dir: Path, github_prefix: str | None) -> str:
    return extract_student_from_repo(repo_dir.name, github_prefix)


def prepare_jplag_root(
    course_id: str,
    lab_id: str,
    *,
    work_dir: Path,
    github_prefix: str | None,
) -> tuple[Path, int]:
    """
    Build JPlag input: one subdirectory per student with source files.

    Returns (submissions_root, n_students).
    """
    if work_dir.exists():
        shutil.rmtree(work_dir)
    subs_root = work_dir / "submissions"
    subs_root.mkdir(parents=True)

    n = 0
    for src_dir in list_real_submissions(course_id, lab_id):
        student = _student_label(src_dir, github_prefix)
        # Prefer unique key if student collision across orgs
        org = src_dir.parent.name
        key = f"{org}__{student}"
        dst = subs_root / key
        dst.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in src_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in {
                ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
                ".py", ".java", ".js", ".ts", ".sh",
            }:
                continue
            # Flatten into student dir (JPlag reads all files under submission)
            target = dst / f.name
            if target.exists():
                target = dst / f"{f.parent.name}_{f.name}"
            shutil.copy2(f, target)
            copied += 1
        if copied:
            n += 1
        else:
            shutil.rmtree(dst, ignore_errors=True)
    return subs_root, n


def run_jplag(
    submissions_root: Path,
    *,
    language: str,
    jar: Path,
    result_stem: Path,
    min_similarity: float = 0.0,
) -> list[tuple[str, str, float]]:
    """
    Run JPlag in RUN mode; parse overview.json from the .jplag zip.

    Returns list of (student_a, student_b, similarity 0..1) sorted desc.
    """
    result_stem.parent.mkdir(parents=True, exist_ok=True)
    # Remove previous result
    for p in (
        Path(str(result_stem) + ".jplag"),
        result_stem.with_suffix(".jplag"),
    ):
        if p.exists():
            p.unlink()

    cmd = [
        "java",
        "-jar",
        str(jar),
        "-l",
        language,
        "-M",
        "RUN",
        "-n",
        "-1",
        "-r",
        str(result_stem),
        str(submissions_root),
    ]
    if language == "cpp":
        # insert --normalize before the submissions path
        cmd.insert(-1, "--normalize")

    print("Running:", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print("JPlag stdout:\n", proc.stdout[-2000:])
        print("JPlag stderr:\n", proc.stderr[-2000:])
        raise RuntimeError(f"JPlag failed with code {proc.returncode}")

    jplag_file = Path(str(result_stem) + ".jplag")
    if not jplag_file.exists():
        # newer versions may write differently
        candidates = list(result_stem.parent.glob("*.jplag"))
        if not candidates:
            raise FileNotFoundError(f"No .jplag result near {result_stem}")
        jplag_file = max(candidates, key=lambda p: p.stat().st_mtime)

    matches: list[tuple[str, str, float]] = []
    with zipfile.ZipFile(jplag_file) as zf:
        names = zf.namelist()
        # JPlag 6.2+: topComparisons.json; older: overview.json
        top_name = next(
            (
                n
                for n in (
                    "topComparisons.json",
                    "overview.json",
                )
                if n in names
            ),
            None,
        )
        if top_name is None:
            top_name = next((n for n in names if n.endswith("topComparisons.json")), None)
        if top_name is None:
            top_name = next((n for n in names if n.endswith("overview.json")), None)
        if top_name is None:
            raise FileNotFoundError(
                f"topComparisons/overview missing in {jplag_file}: "
                f"{[n for n in names if not n.startswith('files/') and not n.startswith('comparisons/')][:30]}"
            )
        payload = json.loads(zf.read(top_name))

    if isinstance(payload, list):
        comparisons = payload
    else:
        comparisons = (
            payload.get("top_comparisons")
            or payload.get("topComparisons")
            or payload.get("comparisons")
            or []
        )
    for c in comparisons:
        a = (
            c.get("first_submission")
            or c.get("submissionName1")
            or c.get("firstSubmission")
            or ""
        )
        b = (
            c.get("second_submission")
            or c.get("submissionName2")
            or c.get("secondSubmission")
            or ""
        )
        sim = c.get("similarity")
        if sim is None:
            sims = c.get("similarities") or {}
            sim = sims.get("AVG", sims.get("MAX"))
        if not a or not b or sim is None:
            continue
        sim_f = float(sim)
        if sim_f > 1.0:
            sim_f /= 100.0
        if sim_f < min_similarity:
            continue
        a_s = a.split("__", 1)[-1]
        b_s = b.split("__", 1)[-1]
        if a_s > b_s:
            a_s, b_s = b_s, a_s
        matches.append((a_s, b_s, sim_f))

    matches.sort(key=lambda t: t[2], reverse=True)
    return matches


def run_compare50(
    course_id: str,
    lab_id: str,
    *,
    github_prefix: str | None,
    max_matches: int,
) -> list[tuple[str, str, float]]:
    """Full pairwise corpus compare (same setting as JPlag batch)."""
    dirs = list_real_submissions(course_id, lab_id)
    results = run_compare50_engine(
        dirs,
        github_prefix=github_prefix,
        max_matches=max_matches,
        output_dir=None,
    )
    out = [(m.student_a, m.student_b, float(m.similarity)) for m in results]
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def analyze(
    c50: list[tuple[str, str, float]],
    jpl: list[tuple[str, str, float]],
    *,
    top: int,
    threshold: float,
) -> dict:
    c50_top = c50[:top]
    jpl_top = jpl[:top]
    c50_set = {pair_key(a, b) for a, b, _ in c50_top}
    jpl_set = {pair_key(a, b) for a, b, _ in jpl_top}
    overlap = c50_set & jpl_set

    c50_flag = {pair_key(a, b) for a, b, s in c50 if s >= threshold}
    jpl_flag = {pair_key(a, b) for a, b, s in jpl if s >= threshold}

    # Spearman-like: for shared pairs, compare ranks
    c50_rank = {pair_key(a, b): i for i, (a, b, _) in enumerate(c50_top)}
    jpl_rank = {pair_key(a, b): i for i, (a, b, _) in enumerate(jpl_top)}
    shared_ranks = []
    for p in overlap:
        shared_ranks.append((c50_rank[p], jpl_rank[p]))

    return {
        "compare50_top": c50_top,
        "jplag_top": jpl_top,
        "top_overlap_count": len(overlap),
        "top_overlap_pairs": sorted(overlap),
        "top_only_compare50": sorted(c50_set - jpl_set),
        "top_only_jplag": sorted(jpl_set - c50_set),
        "flagged_compare50": len(c50_flag),
        "flagged_jplag": len(jpl_flag),
        "flagged_overlap": len(c50_flag & jpl_flag),
        "flagged_only_compare50": sorted(c50_flag - jpl_flag)[:20],
        "flagged_only_jplag": sorted(jpl_flag - c50_flag)[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", required=True)
    parser.add_argument("--lab", required=True)
    parser.add_argument("--language", default="cpp", help="JPlag language (cpp, python3, ...)")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--max-matches", type=int, default=200)
    parser.add_argument("--jplag-jar", type=Path, default=DEFAULT_JPLAG_JAR)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Scratch dir (default: plagiarism_cache/_engine_compare/<course>/<lab>)",
    )
    args = parser.parse_args()

    if not args.jplag_jar.is_file():
        raise SystemExit(f"JPlag jar not found: {args.jplag_jar}")

    github_prefix = _github_prefix_for_lab(args.course, args.lab)
    print(f"course={args.course} lab={args.lab} prefix={github_prefix}")

    work = args.work_dir or (
        get_cache_root() / "_engine_compare" / args.course / str(args.lab)
    )
    subs_root, n = prepare_jplag_root(
        args.course, args.lab, work_dir=work, github_prefix=github_prefix
    )
    print(f"Prepared {n} JPlag submissions under {subs_root}")
    if n < 2:
        raise SystemExit("Need at least 2 submissions in cache")

    print("\n=== compare50 ===")
    c50 = run_compare50(
        args.course,
        args.lab,
        github_prefix=github_prefix,
        max_matches=args.max_matches,
    )
    print(f"compare50 pairs: {len(c50)}")
    for a, b, s in c50[:10]:
        print(f"  {s:6.3f}  {a} ↔ {b}")

    print("\n=== JPlag ===")
    jpl = run_jplag(
        subs_root,
        language=args.language,
        jar=args.jplag_jar,
        result_stem=work / "jplag_results",
    )
    print(f"JPlag pairs: {len(jpl)}")
    for a, b, s in jpl[:10]:
        print(f"  {s:6.3f}  {a} ↔ {b}")

    stats = analyze(c50, jpl, top=args.top, threshold=args.threshold)
    print(f"\n=== Overlap top-{args.top} ===")
    print(f"shared pairs: {stats['top_overlap_count']} / {args.top}")
    print(f"only compare50: {len(stats['top_only_compare50'])}")
    print(f"only JPlag:     {len(stats['top_only_jplag'])}")
    print(f"\n=== Flagged @ threshold {args.threshold} ===")
    print(f"compare50: {stats['flagged_compare50']}")
    print(f"JPlag:     {stats['flagged_jplag']}")
    print(f"overlap:   {stats['flagged_overlap']}")
    if stats["flagged_only_compare50"]:
        print("flagged only by compare50 (sample):", stats["flagged_only_compare50"][:8])
    if stats["flagged_only_jplag"]:
        print("flagged only by JPlag (sample):", stats["flagged_only_jplag"][:8])

    out_json = work / "comparison_summary.json"
    payload = {
        "course": args.course,
        "lab": args.lab,
        "language": args.language,
        "n_submissions": n,
        "threshold": args.threshold,
        "top": args.top,
        "compare50_top": [
            {"a": a, "b": b, "similarity": s} for a, b, s in stats["compare50_top"]
        ],
        "jplag_top": [
            {"a": a, "b": b, "similarity": s} for a, b, s in stats["jplag_top"]
        ],
        "stats": {
            k: v
            for k, v in stats.items()
            if k not in ("compare50_top", "jplag_top")
        },
    }
    # make sets JSON-serializable
    for key in (
        "top_overlap_pairs",
        "top_only_compare50",
        "top_only_jplag",
        "flagged_only_compare50",
        "flagged_only_jplag",
    ):
        payload["stats"][key] = [list(p) for p in payload["stats"][key]]

    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_json}")


if __name__ == "__main__":
    main()
