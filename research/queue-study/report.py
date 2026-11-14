"""Build figures and the Markdown article from saved data, without new simulations."""
import csv
import gzip
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from study import LABELS, POLICIES, ROOT, paired_interval, write_csv
from matplotlib.lines import Line2D

OUT = ROOT / "figures"
DATA = ROOT / "results" / "finalists"
ORIGINAL = ROOT / "results" / "original"
COLORS = {"random": "#a1aebf", "clamp0": "#60a5fa", "clamp_minus1": "#2dd4bf"}
NAMES = {"random": "Случайная", "priority": "Без нижней границы", "priority_clamped": "Приоритет ≥ −1", "priority_clamped_zero": "Приоритет ≥ 0", "most_passed": "Больше сдано", "target_three": "Довести до тройки", "surname_k": "Фамилии на К", "odd_number": "Нечётные номера"}
SCENARIOS = {
    "baseline": "Базовый сценарий", "capacity_10": "Вместимость 10", "capacity_15": "Вместимость 15", "capacity_25": "Вместимость 25", "capacity_30": "Вместимость 30",
    "uniform": "p ~ Uniform(0, 1)", "beta": "p ~ Beta(5, 5)",
    "probability_0.3": "Средняя вероятность 0,3", "probability_0.7": "Средняя вероятность 0,7",
    "attendance_0.6": "Посещаемость 60%", "attendance_0.8": "Посещаемость 80%",
    "students_20": "20 студентов", "students_40": "40 студентов", "students_60": "60 студентов",
    "weeks_22": "22 недели", "weeks_40": "40 недель", "deadline_spacing_2": "Дедлайны через 2 недели", "deadline_spacing_4": "Дедлайны через 4 недели",
    "penalty_0": "Без штрафов", "penalty_2": "Штраф 2 балла за неделю",
    "lab_difficulty": "Разная сложность работ", "release_due_minus2": "Готовность за 2 недели до ДД",
    "cancel_week10": "Отмена приёма на 10-й неделе", "extra_capacity_week10": "Удвоение мест на 10-й неделе",
}


def read_csv(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", dpi=170, facecolor=fig.get_facecolor(), bbox_inches="tight")
    fig.savefig(OUT / f"{name}.svg", facecolor=fig.get_facecolor(), bbox_inches="tight")
    svg = OUT / f"{name}.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    plt.close(fig)


def main():
    OUT.mkdir(exist_ok=True)
    plt.rcParams.update({"figure.facecolor": "#101b2c", "axes.facecolor": "#101b2c", "axes.edgecolor": "#52647d", "text.color": "#edf3fa", "axes.labelcolor": "#d5dfed", "xtick.color": "#d5dfed", "ytick.color": "#d5dfed", "font.family": "DejaVu Sans", "font.size": 11, "axes.spines.top": False, "axes.spines.right": False, "grid.alpha": .15, "grid.color": "#9fb1c6", "legend.facecolor": "#18263b", "legend.edgecolor": "#52647d", "svg.hashsalt": "queue-study-v1"})
    original = read_csv(ORIGINAL / "comparison.csv")
    summary = read_csv(DATA / "summary.csv")
    comparisons = read_csv(DATA / "paired_comparisons.csv")
    protocol = json.loads((DATA / "protocol.json").read_text())
    seeds = protocol["baseline_seeds"]
    arrays = {p: np.concatenate([np.load(DATA / "baseline" / str(s) / f"{p}_scores.npz")["scores"] for s in seeds]) for p in POLICIES}
    per = {p: [row for seed in seeds for row in read_csv(DATA / "baseline" / str(seed) / f"{p}_runs.csv.gz")] for p in POLICIES}
    base = {p: dict(mean=float(arrays[p].mean()), median=float(np.median(arrays[p])), std=float(arrays[p].std()), share345=float((arrays[p] >= 25).mean()), zero=float((arrays[p] == 0).mean())) for p in POLICIES}

    ordered = sorted(original, key=lambda r: float(r["share345"]))
    fig, ax = plt.subplots(figsize=(12, 6.5))
    bars = ax.barh([NAMES[r["model"]] for r in ordered], [100 * float(r["share345"]) for r in ordered], color=["#2dd4bf" if r["model"] == "priority_clamped" else "#60a5fa" for r in ordered], height=.65)
    for bar, row in zip(bars, ordered):
        ax.text(bar.get_width() + .7, bar.get_y() + bar.get_height() / 2, f'{float(row["share345"]) * 100:.2f}%', va="center")
    ax.set(xlabel="Студенты с 3–5, %", title="Сравнение восьми моделей", xlim=(0, 100))
    ax.grid(axis="x")
    save(fig, "all-models")

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5), sharey=True)
    grade_colors = ["#f87171", "#fbbf24", "#60a5fa", "#2dd4bf"]
    for ax, policy in zip(axes, POLICIES):
        flat = arrays[policy].ravel()
        shares = [100 * ((flat >= lo) & (flat <= hi)).mean() for lo, hi in ((0, 24), (25, 32), (33, 44), (45, 50))]
        ax.bar(["2", "3", "4", "5"], shares, color=grade_colors)
        for i, value in enumerate(shares):
            ax.text(i, value + .6, f"{value:.2f}%", ha="center", fontsize=10)
        ax.set(title=LABELS[policy], xlabel="Оценка", ylim=(0, 55))
        ax.grid(axis="y")
    axes[0].set_ylabel("Доля студентов, %")
    fig.suptitle("Финалисты · 30 000 семестров на вариант · общий случайный фон", y=1.04)
    save(fig, "finalists-grades")

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True, sharey=True)
    for ax, policy in zip(axes, POLICIES):
        hist = np.bincount(arrays[policy].ravel(), minlength=51)
        score_colors = np.select([np.arange(51) <= 24, np.arange(51) <= 32, np.arange(51) <= 44], grade_colors[:3], default=grade_colors[3])
        ax.bar(np.arange(51), hist / hist.sum() * 100, color=score_colors, width=.85)
        for boundary in (24.5, 32.5, 44.5):
            ax.axvline(boundary, color="#d5dfed", linestyle="--", linewidth=1, alpha=.8)
        for x, label in ((12, "2: 0–24"), (28.5, "3: 25–32"), (38.5, "4: 33–44"), (47.5, "5: 45–50")):
            ax.text(x, .94, label, transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=9)
        ax.set(title=LABELS[policy], ylabel="Студенты, %")
        ax.grid(axis="y")
    axes[-1].set(xlabel="Баллы за семестр", xlim=(-1, 51))
    fig.tight_layout()
    save(fig, "finalists-scores")

    sensitivity = [r for r in comparisons if r["comparator"] == "clamp0" and r["metric"] == "share345" and int(r["seed"]) == seeds[0]]
    sensitivity.reverse()
    fig, ax = plt.subplots(figsize=(12, 11))
    for i, row in enumerate(sensitivity):
        mean, lo, hi = [100 * float(row[key]) for key in ("delta", "ci95_low", "ci95_high")]
        ax.errorbar(mean, i, xerr=[[max(0, mean - lo)], [max(0, hi - mean)]], fmt="o", color="#2dd4bf" if lo > 0 else "#fbbf24" if hi < 0 else "#a1aebf", capsize=3)
    ax.set_yticks(range(len(sensitivity)), [SCENARIOS[r["scenario"]] for r in sensitivity])
    ax.axvline(0, color="#d5dfed", alpha=.6)
    ax.set(xlabel="Разность доли 3–5: (−1) − (0), процентные пункты; 95% ДИ", title="Где выбор нижней границы меняет результат")
    ax.grid(axis="x")
    ax.legend(handles=[Line2D([], [], marker="o", color="#2dd4bf", linestyle="none", label="−1: доля 3–5 выше (ДИ > 0)"),
                       Line2D([], [], marker="o", color="#fbbf24", linestyle="none", label="0: доля 3–5 выше (ДИ < 0)"),
                       Line2D([], [], marker="o", color="#a1aebf", linestyle="none", label="95% ДИ включает ноль")], loc="lower left", fontsize=9)
    fig.text(.02, -.015, "Точка: средняя разность. Отрезок: 95% доверительный интервал. Справа от нуля результат выше у −1.", fontsize=10)
    save(fig, "sensitivity")

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    caps = [10, 15, 20, 25, 30]
    for policy in POLICIES:
        rows = [next(r for r in summary if r["policy"] == policy and int(r["seed"]) == seeds[0] and r["scenario"] == ("baseline" if cap == 20 else f"capacity_{cap}")) for cap in caps]
        for ax, metric in zip(axes, ("share345", "zero_share")):
            ax.plot(caps, [float(r[metric]) * 100 for r in rows], marker="o", label=LABELS[policy], color=COLORS[policy], linewidth=2)
            ax.grid()
            ax.set_xticks(caps)
            ax.set_xlabel("Мест приёма за неделю")
    axes[0].set(title="Основной критерий", ylabel="Студенты с 3–5, %")
    axes[1].set(title="Побочный риск", ylabel="Студенты с нулём баллов, %")
    axes[1].legend()
    save(fig, "capacity")

    fig, ax = plt.subplots(figsize=(9, 5))
    for policy in POLICIES:
        ax.scatter(base[policy]["zero"] * 100, base[policy]["share345"] * 100, s=180, color=COLORS[policy])
        ax.annotate(LABELS[policy], (base[policy]["zero"] * 100, base[policy]["share345"] * 100), xytext=(8, 9 if policy != "clamp_minus1" else -20), textcoords="offset points")
    ax.set(xlabel="Студенты с нулём баллов, % (меньше лучше)", ylabel="Студенты с 3–5, % (больше лучше)", title="Компромисс в базовом сценарии", xlim=(1.5, 8), ylim=(63, 86))
    ax.grid()
    save(fig, "tradeoff")

    original_rows = "\n".join(f'| {NAMES[r["model"]]} | {100 * float(r["share345"]):.2f}% | {100 * float(r["zero_share"]):.2f}% | {float(r["mean"]):.2f} |' for r in sorted(original, key=lambda r: -float(r["share345"])))
    base_rows = "\n".join(f'| «{LABELS[p]}» | {100 * base[p]["share345"]:.2f}% | {100 * base[p]["zero"]:.2f}% | {base[p]["mean"]:.3f} | {base[p]["median"]:.1f} | {base[p]["std"]:.3f} |' for p in POLICIES)
    delta_rows = []
    for comparator in ("random", "clamp0"):
        for metric, name in (("share345", "Доля 3–5, п. п."), ("zero_share", "Нули, п. п."), ("mean", "Средние баллы")):
            a = np.array([float(r[metric]) for r in per["clamp_minus1"]])
            b = np.array([float(r[metric]) for r in per[comparator]])
            mult = 1 if metric == "mean" else 100
            mean, lo, hi = [x * mult for x in paired_interval(a, b)]
            delta_rows.append(f"| «Приоритет ≥ −1» против «{LABELS[comparator]}» | {name} | {mean:+.3f} | [{lo:+.3f}; {hi:+.3f}] |")
    sensitivity_rows = []
    for scenario in protocol["scenarios"]:
        name = scenario["name"]
        rows = {p: next(r for r in summary if r["policy"] == p and r["scenario"] == name and int(r["seed"]) == seeds[0]) for p in POLICIES}
        delta = (float(rows["clamp_minus1"]["share345"]) - float(rows["clamp0"]["share345"])) * 100
        sensitivity_rows.append(f'| {SCENARIOS[name]} | {float(rows["random"]["share345"]) * 100:.2f}% | {float(rows["clamp0"]["share345"]) * 100:.2f}% | {float(rows["clamp_minus1"]["share345"]) * 100:.2f}% | {delta:+.3f} |')
    detailed = []
    for policy in POLICIES:
        avg = {key: np.mean([float(r[key]) for r in per[policy]]) for key in ("mean", "median", "std", "completed_labs", "max_consecutive_skips")}
        rejected = sum(float(r["queue_rejects"]) for r in per[policy])
        wanted = sum(float(r["wanted"]) for r in per[policy])
        detailed.append(f'| «{LABELS[policy]}» | {avg["mean"]:.3f} | {avg["median"]:.3f} | {avg["std"]:.3f} | {avg["completed_labs"]:.3f} | {avg["max_consecutive_skips"]:.3f} | {100 * rejected / wanted:.2f}% |')
    gallery = "\n".join(f'| {NAMES[r["model"]]} | [Баллы](results/original/{r["model"]}/histogram.png) · [Оценки](results/original/{r["model"]}/automat_bars.png) · [Нули отдельно](results/original/{r["model"]}/automat_bars_with_zero.png) | [Данные](results/original/{r["model"]}) |' for r in original)
    exam_rows = []
    exam_delta_rows = []
    exam_records = []
    for bonus in range(11):
        rates = {}
        for policy in POLICIES:
            adjusted = np.minimum(50, arrays[policy] + bonus)
            success = (adjusted >= 25).mean(axis=1)
            rescued = ((arrays[policy] < 25) & (adjusted >= 25)).mean(axis=1)
            exact_three = ((adjusted >= 25) & (adjusted <= 32)).mean(axis=1)
            rates[policy] = success
            exam_records.append(dict(bonus=bonus, policy=policy, share345=float(success.mean()), share3=float(exact_three.mean()), newly_passing_share=float(rescued.mean())))
        delta, lo, hi = paired_interval(rates["clamp_minus1"], rates["clamp0"])
        if bonus in (0, 1, 3, 5, 10):
            exam_rows.append(f'| +{bonus} | {100 * rates["random"].mean():.2f}% | {100 * rates["clamp0"].mean():.2f}% | {100 * rates["clamp_minus1"].mean():.2f}% | {100 * delta:+.3f} |')
            exam_delta_rows.append(f'| +{bonus} | {100 * delta:+.3f} | [{100 * lo:+.3f}; {100 * hi:+.3f}] |')
    write_csv(DATA / "exam_scenarios.csv", exam_records)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    for policy in POLICIES:
        points = [r for r in exam_records if r["policy"] == policy]
        for ax, metric in zip(axes, ("share345", "share3")):
            ax.plot([r["bonus"] for r in points], [r[metric] * 100 for r in points], marker="o", color=COLORS[policy], label=LABELS[policy])
            ax.grid()
            ax.set_xlabel("Дополнительные баллы за экзамен\n(одинаковые для всех)")
    axes[0].set(title="Не ниже тройки после экзамена", ylabel="Студенты с 3–5, %")
    axes[1].set(title="Итоговая оценка ровно 3", ylabel="Студенты с тройкой, %")
    axes[0].legend()
    fig.tight_layout()
    save(fig, "exam")
    template = (ROOT / "article.md.in").read_text()
    values = dict(original_rows=original_rows, base_rows=base_rows,
                  delta_rows="\n".join(delta_rows), sensitivity_rows="\n".join(sensitivity_rows),
                  detailed="\n".join(detailed), gallery=gallery,
                  exam_rows="\n".join(exam_rows), exam_delta_rows="\n".join(exam_delta_rows))
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", value)
    (ROOT / "README.md").write_text(template, encoding="utf-8")
    (ROOT / "environment.json").write_text(json.dumps({"matplotlib": matplotlib.__version__, "numpy": np.__version__}, indent=2) + "\n")
    print("Generated article and seven figures (PNG + SVG)")


if __name__ == "__main__":
    main()
