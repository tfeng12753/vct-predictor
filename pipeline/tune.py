"""Hyperparameter search on a window that ends before the reported backtest.

    python -m pipeline.tune [db_path]
Scores each component's own map-level log loss on 2024-01-01 .. EVAL_START.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .data import load
from .engine import EVAL_START, run

WIN = (pd.Timestamp("2024-01-01"), EVAL_START)


def ll(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def score(data, params, col):
    _, maps_df, series_df, _ = run(data, verbose=False, params=params, components_only=True)
    w = maps_df[(maps_df["date"] >= WIN[0]) & (maps_df["date"] < WIN[1])]
    if col == "p_selo":
        w = series_df[(series_df["date"] >= WIN[0]) & (series_df["date"] < WIN[1])]
    return ll(w[col], w["win1"]), len(w)


def grid(data, key, col, space):
    names = list(space)
    res = []
    for vals in itertools.product(*space.values()):
        kw = dict(zip(names, vals))
        s, n = score(data, {key: kw}, col)
        res.append((s, kw))
        print(f"  {key} {kw}: {s:.4f} (n={n})", flush=True)
    res.sort(key=lambda r: r[0])
    print(f"BEST {key}: {res[0][1]} -> {res[0][0]:.4f}\n", flush=True)
    return res[0][1]


def main():
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    data = load(db) if db else load()
    best = {}
    best["rm"] = grid(data, "rm", "p_round", {"C": [0.02, 0.05, 0.1, 0.25], "halflife_days": [90, 180, 365], "map_scale": [0.35, 0.6]})
    best["melo"] = grid(data, "melo", "p_melo", {"k": [16, 24, 32], "k_map": [6, 12, 20], "roster_regress": [0.2, 0.5]})
    best["pelo"] = grid(data, "pelo", "p_pelo", {"k": [12, 20, 30], "k_ind": [0, 10, 25]})
    best["selo"] = grid(data, "selo", "p_selo", {"k": [24, 40, 60], "season_carry": [0.6, 0.8]})
    print("RESULT", best)


if __name__ == "__main__":
    main()
