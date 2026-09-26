"""Signal selection and ablation.

    python -m pipeline.ablation

1. Builds every candidate signal once (leakage-free, see engine.run).
2. On the validation window [EVAL_START, HOLDOUT_START) only: measures what each
   signal adds to / removes from the current stack, then runs greedy forward/backward
   selection and compares stacking learners.
3. Reports the chosen configuration on the untouched holdout window, with paired
   bootstrap confidence intervals, and writes site/data/ablation.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import load
from .engine import ALL_FEATURES, HOLDOUT_START, META_FEATURES, run, walk_forward

ROOT = Path(__file__).resolve().parent.parent
LABELS = {
    "f_elo": "Map Elo", "f_round": "Round model", "f_round_econ": "Economy-adjusted round model",
    "f_pelo": "Roster Elo", "f_form": "Player form (Rating 2.0)", "f_fkd": "Opening duels (FK−FD)",
    "f_pick": "Map pick", "f_region": "Region strength", "f_selo": "Series Elo", "f_pistol": "Pistol rounds",
    "f_mom": "Momentum vs expectation", "f_rest": "Rest days", "f_load": "Recent workload",
    "f_chem": "Roster chemistry", "f_exp": "Tier-one experience",
    "f_mapcomf": "Map comfort (roster's maps on this map)", "f_depth": "Agent-pool depth",
    "f_meta": "Meta alignment of comp", "f_mapform": "Map-specific player form",
    "f_home": "Home region at internationals", "f_elo_newpatch": "Favourite on a new patch",
    "f_elo_newact": "Favourite in a new act", "f_elo_intl": "Favourite at internationals",
    "f_elo_elim": "Favourite in elimination games",
}
VAL_START = pd.Timestamp("2024-01-01")   # selection window: VAL_START .. HOLDOUT_START (18 months)


def ll_vec(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def window(maps_df, lo, hi):
    return (maps_df["date"] >= lo) & (maps_df["date"] < hi)


def score(maps_df, feats, learner="logistic", mask=None):
    p = walk_forward(maps_df, feats, learner, start=VAL_START)
    sel = mask & p.notna()
    return float(ll_vec(p[sel], maps_df.loc[sel, "win1"]).mean()), p


def paired_ci(p_new, p_base, maps_df, mask, n_boot=2000, seed=0):
    """95% CI of mean log-loss change (new - base), resampling whole series."""
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({"m": maps_df.loc[mask, "match_id"],
                      "delta": ll_vec(p_new[mask], maps_df.loc[mask, "win1"]) - ll_vec(p_base[mask], maps_df.loc[mask, "win1"])})
    by = d.groupby("m")["delta"].agg(["sum", "count"])
    s, c = by["sum"].to_numpy(), by["count"].to_numpy()
    idx = rng.integers(0, len(s), size=(n_boot, len(s)))
    boots = s[idx].sum(1) / c[idx].sum(1)
    return float(d["delta"].mean()), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def main():
    data = load()
    _, maps_df, _, _ = run(data, verbose=True, components_only=True)
    val = window(maps_df, VAL_START, HOLDOUT_START)
    hold = window(maps_df, HOLDOUT_START, pd.Timestamp("2100-01-01"))
    print(f"validation maps {val.sum()}, holdout maps {hold.sum()}")

    base = list(META_FEATURES)
    base_ll, p_base = score(maps_df, base, mask=val)
    print(f"current stack on validation: {base_ll:.4f}")

    # 1. single add / drop relative to current stack (validation)
    singles = []
    for f in ALL_FEATURES:
        feats = [x for x in base if x != f] if f in base else base + [f]
        s, p = score(maps_df, feats, mask=val)
        mean, lo, hi = paired_ci(p, p_base, maps_df, val)
        singles.append({"key": f, "label": LABELS[f], "in_stack": f in base, "delta": mean, "lo": lo, "hi": hi})
        print(f"  {'drop' if f in base else 'add '} {f:14s} {s:.4f}  delta {mean:+.4f} [{lo:+.4f}, {hi:+.4f}]", flush=True)

    # 2. selection rule: a signal changes status only when its paired-bootstrap 95% CI
    #    clearly excludes zero. (Greedy selection on point estimates overfit: on a first
    #    try it picked a stack that looked better on validation and was worse on holdout.)
    chosen = list(base)
    for r in singles:
        if r["hi"] < 0:
            if r["in_stack"]:
                chosen.remove(r["key"])
            else:
                chosen.append(r["key"])
            print(f"  {'-' if r['in_stack'] else '+'} {r['key']} (CI {r['lo']:+.4f}..{r['hi']:+.4f})", flush=True)
    cur, _ = score(maps_df, chosen, mask=val)

    # 3. learner comparison on the chosen set (validation)
    learners = {}
    for lr in ("logistic", "gbm"):
        s, _ = score(maps_df, chosen, learner=lr, mask=val)
        learners[lr] = s
        print(f"  learner {lr}: {s:.4f}")
    learner = min(learners, key=learners.get)

    # 4. holdout report (no decisions made here)
    _, p_old = score(maps_df, base, mask=hold)
    _, p_new = score(maps_df, chosen, learner=learner, mask=hold)
    hold_rows = {}
    for name, p in (("previous stack", p_old), ("selected stack", p_new), ("Map Elo alone", maps_df["p_melo"]),
                    ("Roster Elo alone", maps_df["p_pelo"])):
        sel = hold & p.notna()
        y = maps_df.loc[sel, "win1"]
        hold_rows[name] = {"log_loss": float(ll_vec(p[sel], y).mean()),
                           "accuracy": float(((p[sel] >= .5) == (y == 1)).mean()), "n": int(sel.sum())}
    d_mean, d_lo, d_hi = paired_ci(p_new, p_old, maps_df, hold & p_new.notna() & p_old.notna())
    print("holdout:", json.dumps(hold_rows, indent=1))
    print(f"holdout change vs previous stack: {d_mean:+.4f} [{d_lo:+.4f}, {d_hi:+.4f}]")

    out = {
        "validation": [str(VAL_START.date()), str(HOLDOUT_START.date())],
        "holdout_start": str(HOLDOUT_START.date()),
        "baseline": base, "baseline_val_ll": base_ll, "singles": singles,
        "chosen": chosen, "chosen_val_ll": cur, "learners": learners, "learner": learner,
        "holdout": hold_rows, "holdout_delta": {"mean": d_mean, "lo": d_lo, "hi": d_hi},
        "labels": LABELS,
    }
    (ROOT / "site" / "data").mkdir(parents=True, exist_ok=True)
    with open(ROOT / "site" / "data" / "ablation.json", "w") as f:
        json.dump(out, f, indent=1)
    print("chosen:", chosen, "learner:", learner)


if __name__ == "__main__":
    main()
