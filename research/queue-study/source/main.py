import argparse
import os
import sys

from models import MODELS
from common import N_RUNS_DEFAULT, BASE_SEED_DEFAULT
from runner import run_and_save


def main():
    parser = argparse.ArgumentParser(description="Симулятор очереди на сдачу лаб")
    parser.add_argument("model", nargs="?", choices=list(MODELS),
                        help="Имя модели (или --all)")
    parser.add_argument("--all", action="store_true",
                        help="Прогнать все зарегистрированные модели")
    parser.add_argument("--runs", type=int, default=N_RUNS_DEFAULT)
    parser.add_argument("--seed", type=int, default=BASE_SEED_DEFAULT)
    parser.add_argument("--out-root", default="outputs")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    if args.all:
        targets = list(MODELS.values())
    elif args.model:
        targets = [MODELS[args.model]]
    else:
        parser.error("Укажите имя модели или --all")

    for cls in targets:
        model = cls()
        out_dir = os.path.join(args.out_root, model.name)
        run_and_save(model, args.runs, args.seed, out_dir, plot=not args.no_plot)


if __name__ == "__main__":
    sys.exit(main())