"""Model-level experiments beyond single-signal ablation, run on the same protocol:
decide on the validation window, report the holdout, paired bootstrap intervals throughout.

    python -m pipeline.experiments            # all experiments
    python -m pipeline.experiments tier2      # just the tier-2 prior (needs the Challengers crawl)

Writes site/data/experiments.json, which the "How it works" page renders as an experiments log.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .data import load
from .engine import EVAL_START, HOLDOUT_START, PARAMS, run
from .models import logit, sigmoid

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data" / "experiments.json"
INTL = ("masters", "champions")


def nll(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def paired(p_new, p_base, y, n_boot=4000, seed=0):
    d = nll(p_new, y) - nll(p_base, y)
    idx = np.random.default_rng(seed).integers(0, len(d), (n_boot, len(d)))
    b = d[idx].mean(1)
    return {"delta": round(float(d.mean()), 5), "lo": round(float(np.percentile(b, 2.5)), 5),
            "hi": round(float(np.percentile(b, 97.5)), 5), "n": int(len(d))}


def windows(df):
    return {"validation": (df["date"] >= EVAL_START) & (df["date"] < HOLDOUT_START), "holdout": df["date"] >= HOLDOUT_START}


def compare(base: pd.DataFrame, new: pd.DataFrame, col="p_meta_preveto", subset=None):
    k = base[["match_id", "date", "win1", "tier", col]].merge(new[["match_id", col]], on="match_id", suffixes=("_b", "_n")).dropna()
    if subset is not None:
        k = k[subset(k)]
    return {w: paired(k.loc[m, f"{col}_n"], k.loc[m, f"{col}_b"], k.loc[m, "win1"]) for w, m in windows(k).items()}


def decide(res):
    v = res["validation"]
    return "adopted" if v["hi"] < 0 else ("rejected: made validation worse" if v["lo"] > 0 else "not adopted: validation interval includes zero")


# --------------------------------------------------------------------------- experiments

def exp_series_correlation(data, base_s):
    """Shared per-series form shift for international series (see VetoModel.simulate)."""
    out = []
    for sig in (0.4, 0.7, 1.0):
        _, _, s, _ = run(data, verbose=False, params={**PARAMS, "series": {"sigma_regional": 0.0, "sigma_intl": sig}})
        res = compare(base_s, s, subset=lambda k: k["tier"].isin(INTL))
        out.append({"setting": f"σ = {sig}", **res, "decision": decide(res)})
    return {"key": "series_correlation", "title": "Maps within a series are correlated (international series)",
            "hypothesis": "International series forecasts are overconfident because the simulator treats maps as independent.",
            "rows": out, "unit": "series log loss"}


def exp_temperature(base_s):
    """Walk-forward temperature scaling of final series probabilities."""
    s = base_s[base_s["p_meta_preveto"].notna()].sort_values("date").reset_index(drop=True)
    z = logit(s["p_meta_preveto"].values)
    T = np.ones(len(s))
    months = pd.date_range(EVAL_START, s["date"].max() + pd.offsets.MonthBegin(1), freq="MS")
    for i, m0 in enumerate(months[:-1]):
        prev = (s["date"] < m0).values
        if prev.sum() >= 150:
            zz = z[prev].reshape(-1, 1)
            lr = LogisticRegression(C=1e6, fit_intercept=False).fit(np.vstack([zz, -zz]), np.r_[s["win1"][prev], 1 - s["win1"][prev]])
            T[((s["date"] >= m0) & (s["date"] < months[i + 1])).values] = float(np.clip(lr.coef_[0][0], 0.6, 1.2))
    new = s.assign(p_meta_preveto=sigmoid(T * z))
    res = compare(s, new)
    return {"key": "temperature", "title": "Recalibrate the final probabilities (temperature scaling)",
            "hypothesis": "Series forecasts are slightly overconfident overall (holdout calibration slope 0.82).",
            "rows": [{"setting": "walk-forward, refit monthly", **res, "decision": decide(res)}], "unit": "series log loss"}


def exp_tier2(data, base_s):
    """Start newcomers from their Challengers/Ascension rating instead of a flat default."""
    if data.t2_maps is None or len(data.t2_maps) < 2000:
        return {"key": "tier2", "title": "Rate newcomers from their Challengers results", "pending": True,
                "hypothesis": "Promoted teams and rookies are mis-rated at first because tier-one data starts from scratch.",
                "rows": [], "note": f"Waiting for the Challengers crawl ({0 if data.t2_maps is None else len(data.t2_maps)} tier-2 maps so far)."}
    rows = []
    newcomer = None
    for bp, bt in ((0.3, 0.0), (0.6, 0.0), (0.0, 0.5), (0.4, 0.4)):
        _, _, s, _ = run(data, verbose=False, params={**PARAMS, "t2": {"beta_player": bp, "beta_team": bt, "min_maps": 10}})
        res = compare(base_s, s)
        rows.append({"setting": f"players β={bp}, teams β={bt}", **res, "decision": decide(res)})
    return {"key": "tier2", "title": "Rate newcomers from their Challengers results",
            "hypothesis": "Promoted teams and rookies are mis-rated at first because tier-one data starts from scratch.",
            "rows": rows, "unit": "series log loss", "t2_maps": int(len(data.t2_maps))}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    data = load()
    _, _, base_s, _ = run(data, verbose=False)
    prev = json.loads(OUT.read_text()) if OUT.exists() else {"experiments": []}
    done = {e["key"]: e for e in prev.get("experiments", [])}
    jobs = {"series_correlation": lambda: exp_series_correlation(data, base_s),
            "temperature": lambda: exp_temperature(base_s),
            "tier2": lambda: exp_tier2(data, base_s)}
    for key, fn in jobs.items():
        if only and key != only:
            continue
        print(f"running {key}", flush=True)
        done[key] = fn()
        print(json.dumps(done[key].get("rows", done[key].get("note")), indent=1), flush=True)
    order = ["series_correlation", "temperature", "tier2"]
    OUT.write_text(json.dumps({"experiments": [done[k] for k in order if k in done]}, indent=1))


if __name__ == "__main__":
    main()
