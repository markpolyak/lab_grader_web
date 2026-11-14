import os
import json
from dataclasses import dataclass
import numpy as np

# ---------- Распределение "потенциала" студентов ----------
# "uniform": U(0,1), стандартное отклонение около 0.29
# "normal":  N(0.5, 0.15) clip - центр 0.5, без крайностей
# "beta": Beta(5,5), среднее 0.5, стандартное отклонение около 0.151
PASS_CHANCE_DIST  = "normal"

# ---------- Параметры модели ----------
N_STUDENTS        = 30
N_LABS            = 10
N_WEEKS           = 30
CAPACITY          = 20
MAX_POINTS_PER_LAB = 5
N_RUNS_DEFAULT    = 10_000
BASE_SEED_DEFAULT = 42
EXAM_BONUS = 5

DEADLINES = np.array([(i + 1) * 3 - 1 for i in range(N_LABS)], dtype=int)

GRADE_BINS = {
    "2": (0,  24, "red"),
    "3": (25, 32, "orange"),
    "4": (33, 44, "lightblue"),
    "5": (45, 50, "green"),
}


@dataclass
class SimState:
    current_lab: np.ndarray        # (N,) — номер текущей лабы у каждого студента
    completion_week: np.ndarray    # (N, N_LABS) — неделя сдачи, −1 если не сдал
    pass_chances: np.ndarray       # (N,) — выбранное распределение, фиксировано на прогон
    deadlines: np.ndarray          # (N_LABS,)
    week: int                      # текущая неделя (1..N_WEEKS)
    n_labs: int
    rng: np.random.Generator

    def active(self) -> np.ndarray:
        return np.where(self.current_lab < self.n_labs)[0]


def draw_pass_chances(rng: np.random.Generator, n: int) -> np.ndarray:
    if PASS_CHANCE_DIST == "uniform":
        return rng.uniform(0.0, 1.0, size=n)
    if PASS_CHANCE_DIST == "beta":
        return rng.beta(5.0, 5.0, size=n)
    # "normal" (по умолчанию)
    p = rng.normal(0.5, 0.15, size=n)
    return np.clip(p, 0.05, 0.95)

# ---------- Один прогон ----------
def simulate_one(model, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pass_chances = draw_pass_chances(rng, N_STUDENTS)
    current_lab = np.zeros(N_STUDENTS, dtype=int)
    completion_week = np.full((N_STUDENTS, N_LABS), -1, dtype=int)

    for week in range(1, N_WEEKS + 1):
        state = SimState(
            current_lab=current_lab,
            completion_week=completion_week,
            pass_chances=pass_chances,
            deadlines=DEADLINES,
            week=week,
            n_labs=N_LABS,
            rng=rng,
        )
        if state.active().size == 0:
            break

        order = model.order_queue(state)          # индексы студентов в порядке очереди
        to_check = order[:CAPACITY]
        if to_check.size == 0:
            continue

        rolls = rng.random(to_check.size)
        for idx, s in enumerate(to_check):
            if rolls[idx] < pass_chances[s]:
                lab = current_lab[s]
                completion_week[s, lab] = week
                current_lab[s] += 1

    return _compute_scores(completion_week)


def _compute_scores(completion_week: np.ndarray) -> np.ndarray:
    scores = np.zeros(N_STUDENTS, dtype=int)
    for s in range(N_STUDENTS):
        total = 0
        for lab in range(N_LABS):
            w = completion_week[s, lab]
            if w == -1:
                continue
            late = max(0, w - DEADLINES[lab])
            total += max(0, MAX_POINTS_PER_LAB - late)
        scores[s] = total
    return scores


# ---------- Визуализация ----------
def plot_histogram(counts: np.ndarray, out_dir: str, title: str = ""):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(14, 6))
    for grade, (lo, hi, color) in GRADE_BINS.items():
        xs = np.arange(lo, hi + 1)
        ax.bar(xs, counts[lo:hi + 1], color=color, edgecolor="black",
               linewidth=0.5)
    for x in (24.5, 32.5, 44.5):
        ax.axvline(x, color="gray", linestyle="--", linewidth=0.8, alpha=0.6)
    ax.set_xlabel("Баллы (0–50)")
    ax.set_ylabel("Кол-во студентов (сумма за все прогоны)")
    ax.set_title(title or "Распределение баллов")
    ax.set_xticks(range(0, 51, 2))
    ax.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "histogram.png"), dpi=150)
    plt.close(fig)


def plot_grade_bars(grade_counts: dict, out_dir: str):
    """Столбики: доля студентов с каждым автоматом (%)."""
    import matplotlib.pyplot as plt

    grades   = list(grade_counts.keys())                       # ["2","3","4","5"]
    percents = [grade_counts[g]["percent"] for g in grades]
    colors   = [grade_counts[g]["color"]   for g in grades]

    fig, ax = plt.subplots(figsize=(9, 6))
    bars = ax.bar(grades, percents, color=colors, edgecolor="black", linewidth=1.2)

    # подписи с процентами над столбиками
    for bar, p in zip(bars, percents):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.5,
            f"{p:.1f}%",
            ha="center", va="bottom",
            fontsize=11, fontweight="bold",
        )

    ax.set_xlabel("Автомат (оценка)")
    ax.set_ylabel("Доля студентов, %")
    ax.set_title("Процентное соотношение автоматов")
    ax.set_ylim(0, max(percents) * 1.15 if max(percents) > 0 else 1)
    ax.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "automat_bars.png"), dpi=150)
    plt.close(fig)

def plot_zero_separate_bars(zero_separate_counts: dict, out_dir: str):
    """5 столбиков: 0 баллов отдельно, затем 2/3/4/5 (диапазон '2' = 1–24)."""
    import matplotlib.pyplot as plt

    labels   = list(zero_separate_counts.keys())               # ["0","2","3","4","5"]
    percents = [zero_separate_counts[k]["percent"] for k in labels]
    colors   = [zero_separate_counts[k]["color"]   for k in labels]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(labels, percents, color=colors, edgecolor="black", linewidth=1.2)

    for bar, p in zip(bars, percents):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.4,
            f"{p:.1f}%",
            ha="center", va="bottom",
            fontsize=11, fontweight="bold",
        )

    ax.set_xlabel("Баллы (0 — отдельно, далее автомат)")
    ax.set_ylabel("Доля студентов, %")
    ax.set_title("Соотношение автоматов (0 баллов — отдельно)")
    ax.set_ylim(0, max(percents) * 1.18 if max(percents) > 0 else 1)
    ax.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "automat_bars_with_zero.png"), dpi=150)
    plt.close(fig)
