import os
import json
import numpy as np

from common import (
    simulate_one, plot_histogram, plot_grade_bars, plot_zero_separate_bars,
    N_STUDENTS, N_LABS, N_WEEKS, CAPACITY, DEADLINES, GRADE_BINS,
    PASS_CHANCE_DIST, EXAM_BONUS,
)

def run_and_save(model, n_runs: int, base_seed: int, out_dir: str,
                 plot: bool = True):
    os.makedirs(out_dir, exist_ok=True)
    print(f"\n=== {model.name} — {model.description} ===")
    print(f"Прогонов: {n_runs}, seed0={base_seed}, out_dir={out_dir}")

    all_scores = np.empty(n_runs * N_STUDENTS, dtype=np.int16)
    per_run = []

    for i in range(n_runs):
        sc = simulate_one(model, seed=base_seed + i)
        all_scores[i * N_STUDENTS:(i + 1) * N_STUDENTS] = sc
        per_run.append({
            "run":    i,
            "mean":   float(sc.mean()),
            "median": float(np.median(sc)),
            "std":    float(sc.std(ddof=0)),
        })
        if (i + 1) % 1000 == 0:
            print(f"  {i + 1}/{n_runs}")

    # ---- сохранение данных ----
    np.save(os.path.join(out_dir, "all_scores.npy"), all_scores)
    with open(os.path.join(out_dir, "per_run_stats.json"), "w", encoding="utf-8") as f:
        json.dump(per_run, f, ensure_ascii=False)

       # --- 4 категории (automat_bars.png) ---
    counts = np.bincount(all_scores, minlength=51)
    total = int(all_scores.size)
    grade_counts = {}
    for g, (lo, hi, color) in GRADE_BINS.items():
        cnt = int(counts[lo:hi + 1].sum())
        grade_counts[g] = {
            "range": [lo, hi], "count": cnt,
            "percent": 100.0 * cnt / total, "color": color,
        }

    # --- 5 категорий: 0 баллов отдельно (automat_bars_with_zero.png) ---
    zero_count = int(counts[0])
    zero_separate_counts = {
        "0": {
            "range":   [0, 0],
            "count":   zero_count,
            "percent": 100.0 * zero_count / total,
            "color":   "gray",
        }
    }
    for g, (lo, hi, color) in GRADE_BINS.items():
        lo2 = 1 if g == "2" else lo            # для "2" исключаем 0 из диапазона
        cnt = int(counts[lo2:hi + 1].sum())
        zero_separate_counts[g] = {
            "range":   [lo2, hi],
            "count":   cnt,
            "percent": 100.0 * cnt / total,
            "color":   color,
        }

    # --- МЕТРИКА: доля с автоматом 3/4/5 ---
    top345_count   = sum(grade_counts[g]["count"] for g in ("3", "4", "5"))
    top345_percent = 100.0 * top345_count / total

    # --- МЕТРИКА: Автомат + Экзамен 3/4/5 (на симуляцию и графики не влияет) ---
    # каждому студенту +EXAM_BONUS, затем то же условие score >= 25
    top345_exam_count   = int((all_scores + EXAM_BONUS >= 25).sum())
    top345_exam_percent = 100.0 * top345_exam_count / total

    # --- агрегаты ---
    means   = np.array([s["mean"]   for s in per_run])
    medians = np.array([s["median"] for s in per_run])
    stds    = np.array([s["std"]    for s in per_run])

    aggregate = {
        "model": model.name,
        "description": model.description,
        "averaged_over_runs": {
            "mean_of_means":   float(means.mean()),
            "mean_of_medians": float(medians.mean()),
            "mean_of_stds":    float(stds.mean()),
        },
        "pooled_over_runs": {
            "mean":   float(all_scores.mean()),
            "median": float(np.median(all_scores)),
            "std":    float(all_scores.std(ddof=0)),
        },
        "automat_pass_rate": {
            "description":     "доля студентов с итоговым баллом >= 25 (оценки 3/4/5)",
            "threshold_score": 25,
            "count":           int(top345_count),
            "percent":         float(top345_percent),
            "fail_count":      int(total - top345_count),
            "fail_percent":    float(100.0 - top345_percent),
        },
        "n_runs":    n_runs,
        "n_per_run": N_STUDENTS,
        "n_total":   int(all_scores.size),
    }

    # ---- сохранение всех JSON (порядок любой, но все — здесь) ----
    with open(os.path.join(out_dir, "aggregate_stats.json"), "w", encoding="utf-8") as f:
        json.dump(aggregate, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "grade_counts.json"), "w", encoding="utf-8") as f:
        json.dump(grade_counts, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "zero_separate_counts.json"), "w", encoding="utf-8") as f:
        json.dump(zero_separate_counts, f, ensure_ascii=False, indent=2)

    # ---- метаданные модели + параметры ----
    metadata = {
        "model": model.name,
        "description": model.description,
        "params": {
            "N_STUDENTS": N_STUDENTS, "N_LABS": N_LABS, "N_WEEKS": N_WEEKS,
            "CAPACITY": CAPACITY, "MAX_POINTS_PER_LAB": 5,
            "DEADLINES": DEADLINES.tolist(),
            "GRADE_BINS": {g: [lo, hi] for g, (lo, hi, _) in GRADE_BINS.items()},
            "pass_chance_dist": PASS_CHANCE_DIST,
            "exam_bonus": EXAM_BONUS,
            "n_runs": n_runs, "base_seed": base_seed,
        },
        "class": f"{type(model).__module__}.{type(model).__name__}",
    }
    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    # ---- графики ----
    if plot:
        plot_histogram(counts, out_dir,
                       title=f"{model.name} — распределение баллов ({n_runs} прогонов)")
        plot_grade_bars(grade_counts, out_dir)
        plot_zero_separate_bars(zero_separate_counts, out_dir)

    print("Агрегаты:", aggregate["averaged_over_runs"])
    print("Пул:    ", aggregate["pooled_over_runs"])

    # for g, d in grade_counts.items():
    #     print(f"  {g}: {d['count']:>7} ({d['percent']:5.2f}%)")
    
    print("5 категорий (0 отдельно):")
    for g, d in zero_separate_counts.items():
        print(f"  {g}: {d['count']:>7} ({d['percent']:5.2f}%)  диапазон {d['range']}")
    print(f"  Автомат 3/4/5: {top345_count:>7} ({top345_percent:5.2f}%)")
    
    print(f"  Автомат+Экзамен 3/4/5 (бонус +{EXAM_BONUS}): "
          f"{top345_exam_count:>7} ({top345_exam_percent:5.2f}%)")

    print(f"  Дельта: "
              f"{(top345_exam_percent - top345_percent):5.2f}")
    
    return all_scores, aggregate, grade_counts