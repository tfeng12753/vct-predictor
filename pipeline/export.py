"""Run the whole pipeline and write the JSON the static site reads.

    python -m pipeline.export
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .data import load, parse_veto
from .engine import EVAL_START, HOLDOUT_START, META_FEATURES, base_features, run
from . import insights
from .validate import health
from .models import series_score_dist, series_win_prob
from .tournament import simulate as simulate_tournament

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data"
ACTIVE_DAYS = 300
FOCUS_EVENT = 2766  # Valorant Champions 2026

FEATURE_LABELS = {
    "f_elo": "Map Elo", "f_round": "Round model (atk/def by map)",
    "f_pelo": "Player Elo (current roster)", "f_form": "Player form (Rating 2.0)",
    "f_fkd": "Opening duels (FK−FD)", "f_pick": "Map pick advantage", "f_selo": "Series Elo",
    "f_region": "Region strength", "f_round_econ": "Economy-adjusted round model", "f_pistol": "Pistol rounds",
    "f_mom": "Momentum", "f_rest": "Rest days", "f_load": "Recent workload", "f_chem": "Roster chemistry",
    "f_exp": "Tier-one experience",
}


def local_logo(team_id, url):
    """Download a team logo once into site/logos/ so the site never hotlinks vlr's CDN."""
    if not url or not isinstance(url, str):
        return None
    ext = Path(url.split("?")[0]).suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        ext = ".png"
    dest = ROOT / "site" / "logos" / f"{team_id}{ext}"
    if not dest.exists():
        import time
        import requests
        from .scrape import UA
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            time.sleep(0.5)
            if resp.status_code != 200 or not resp.content:
                return None
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
        except requests.RequestException:
            return None
    return f"logos/{dest.name}"


def to_utc_iso(ts) -> str:
    """vlr.gg's data-utc-ts attribute is actually US Eastern wall-clock time."""
    return pd.Timestamp(ts).tz_localize("America/New_York").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def r(x, n=4):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


# --------------------------------------------------------------------------- metrics

def metrics(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    bins = []
    edges = np.linspace(0, 1, 11)
    # fold to favourite perspective for calibration so bins are populated symmetrically
    pf = np.where(p >= 0.5, p, 1 - p)
    yf = np.where(p >= 0.5, y, 1 - y)
    for lo, hi in zip(edges[5:-1], edges[6:]):
        sel = (pf >= lo) & (pf < hi if hi < 1 else pf <= hi)
        if sel.sum():
            bins.append({"lo": r(lo, 2), "hi": r(hi, 2), "n": int(sel.sum()), "pred": r(pf[sel].mean()), "obs": r(yf[sel].mean())})
    return {
        "n": int(len(p)),
        "log_loss": r(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "brier": r(np.mean((p - y) ** 2)),
        "accuracy": r(np.mean((p >= 0.5) == (y == 1))),
        "calibration": bins,
    }


def backtest(maps_df, series_df):
    ev_m = maps_df[(maps_df["date"] >= EVAL_START) & maps_df["p_meta"].notna()].copy()
    pick_only = np.where(ev_m["f_pick"] == 1, 0.56, np.where(ev_m["f_pick"] == -1, 0.44, 0.5))
    map_models = {
        "Coin flip": np.full(len(ev_m), 0.5),
        "Pick-side only": pick_only,
        "Map Elo": ev_m["p_melo"], "Round model": ev_m["p_round"], "Player Elo": ev_m["p_pelo"],
        "Stacked ensemble": ev_m["p_meta"],
    }
    ev_s = series_df[(series_df["date"] >= EVAL_START)].copy()
    ser_pre = ev_s[ev_s["p_meta_preveto"].notna()]
    ser_models = {
        "Coin flip": (ser_pre, np.full(len(ser_pre), 0.5)),
        "Series Elo": (ser_pre, ser_pre["p_selo"]),
        "Map Elo (as series)": (ser_pre, ser_pre["p_melo_base"]),
        "Player Elo": (ser_pre, ser_pre["p_pelo"]),
        "Ensemble + veto sim (pre-veto)": (ser_pre, ser_pre["p_meta_preveto"]),
    }
    post = ev_s[ev_s["p_meta_postveto"].notna()]
    rng = np.random.default_rng(0)

    def boot(d, col, n=2000):
        p = np.clip(d[col].to_numpy(), 1e-6, 1 - 1e-6)
        y = d["win1"].to_numpy()
        ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
        acc = ((p >= .5) == (y == 1)).astype(float)
        idx = rng.integers(0, len(p), size=(n, len(p)))
        return {"ll": [r(np.percentile(ll[idx].mean(1), 2.5)), r(np.percentile(ll[idx].mean(1), 97.5))],
                "acc": [r(np.percentile(acc[idx].mean(1), 2.5)), r(np.percentile(acc[idx].mean(1), 97.5))]}

    ci = {"Ensemble + veto sim (pre-veto)": boot(ser_pre, "p_meta_preveto"), "Series Elo": boot(ser_pre, "p_selo"),
          "Player Elo": boot(ser_pre, "p_pelo"), "Map Elo (as series)": boot(ser_pre, "p_melo_base")}
    # paired improvement of the ensemble over Series Elo
    pe = np.clip(ser_pre["p_meta_preveto"].to_numpy(), 1e-6, 1 - 1e-6)
    ps = np.clip(ser_pre["p_selo"].to_numpy(), 1e-6, 1 - 1e-6)
    yy = ser_pre["win1"].to_numpy()
    dl = (-(yy * np.log(pe) + (1 - yy) * np.log(1 - pe))) - (-(yy * np.log(ps) + (1 - yy) * np.log(1 - ps)))
    idx = rng.integers(0, len(dl), size=(2000, len(dl)))
    paired = {"mean": r(dl.mean()), "lo": r(np.percentile(dl[idx].mean(1), 2.5)), "hi": r(np.percentile(dl[idx].mean(1), 97.5))}
    windows = {}
    for name, lo, hi in (("validation", EVAL_START, HOLDOUT_START), ("holdout", HOLDOUT_START, pd.Timestamp("2100-01-01"))):
        w = ser_pre[(ser_pre["date"] >= lo) & (ser_pre["date"] < hi)]
        windows[name] = {"from": lo.strftime("%Y-%m-%d"),
                         "models": {k: metrics(w[c], w["win1"]) | {"calibration": None} for k, c in
                                    (("Ensemble + veto sim (pre-veto)", "p_meta_preveto"), ("Series Elo", "p_selo"),
                                     ("Player Elo", "p_pelo"))}}
    out = {
        "eval_start": EVAL_START.strftime("%Y-%m-%d"),
        "maps": {k: metrics(v, ev_m["win1"]) for k, v in map_models.items()},
        "series": {k: metrics(p, d["win1"]) for k, (d, p) in ser_models.items()},
        "series_postveto": metrics(post["p_meta_postveto"], post["win1"]) if len(post) else None,
    }
    # by tier / monthly
    by_tier = {}
    for tier, g in ser_pre.groupby("tier"):
        by_tier[tier] = {"n": len(g), "accuracy": r(np.mean((g["p_meta_preveto"] >= .5) == (g["win1"] == 1))),
                         "elo_accuracy": r(np.mean((g["p_selo"] >= .5) == (g["win1"] == 1)))}
    out["by_tier"] = by_tier
    out["ci"] = ci
    out["paired_vs_elo"] = paired
    out["windows"] = windows
    monthly = []
    ser_pre = ser_pre.assign(month=ser_pre["date"].dt.to_period("Q").astype(str))
    for mth, g in ser_pre.groupby("month"):
        row = {"period": mth, "n": len(g)}
        for name, col in (("ensemble", "p_meta_preveto"), ("series_elo", "p_selo"), ("player_elo", "p_pelo")):
            p = np.clip(g[col].to_numpy(), 1e-6, 1 - 1e-6)
            y = g["win1"].to_numpy()
            row[name] = r(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
            row[name + "_acc"] = r(np.mean((p >= .5) == (y == 1)))
        monthly.append(row)
    out["over_time"] = monthly
    return out, ser_pre


# --------------------------------------------------------------------------- main

def main():
    data = load()
    st, maps_df, series_df, cand_df = run(data)
    now = data.matches["date"].max()
    OUT.mkdir(parents=True, exist_ok=True)
    teams_meta = data.teams

    # ---- active teams & pairwise tables
    active = [t for t, d in st.last_played.items() if (now - d).days <= ACTIVE_DAYS]
    upcoming = data.upcoming[data.upcoming["date"] >= now - pd.Timedelta(days=1)]
    for t in set(upcoming["team1_id"]) | set(upcoming["team2_id"]):
        if t not in active:
            active.append(t)
    active = sorted(active)
    pool = st.pool
    coef = st.meta.coef_.ravel()

    def meta_p(f, pick):
        x = np.array([f[k] if k != "f_pick" else pick for k in META_FEATURES])
        return float(1 / (1 + math.exp(-x @ coef)))

    feats = {}
    pair = {}
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            la, lb = st.lineups.get(a, []), st.lineups.get(b, [])
            for mp in pool:
                f = base_features(st, a, b, la, lb, mp)
                feats[(a, b, mp)] = f
                pair[(a, b, mp)] = (meta_p(f, 0), meta_p(f, 1), meta_p(f, -1))

    def pmap(a, b, mp, pick):
        """P(a wins map) with pick in {+1 a picked, -1 b picked, 0 decider}."""
        if (a, b, mp) in pair:
            return pair[(a, b, mp)][{0: 0, 1: 1, -1: 2}[pick]]
        return 1 - pair[(b, a, mp)][{0: 0, 1: 2, -1: 1}[pick]]

    rng = np.random.default_rng(3)
    ser_cache = {}

    def p_series(a, b, bo, n=250):
        key = (a, b, bo)
        if key not in ser_cache:
            sim = st.veto.simulate(pool, lambda mp, pk: pmap(a, b, mp, pk), st.habits, a, b, bo, n=n, rng=rng)
            ser_cache[key] = sim["p_series"]
            ser_cache[(b, a, bo)] = 1 - sim["p_series"]
        return ser_cache[key]

    # ---- per-team map strength vs the field & power index
    strength = {}
    for a in active:
        per_map = {mp: float(np.mean([pmap(a, b, mp, 0) for b in active if b != a])) for mp in pool}
        strength[a] = per_map
    power = {a: float(np.mean(list(strength[a].values()))) for a in active}

    # ---- historical team stats (last 365 days)
    since = now - pd.Timedelta(days=365)
    mrec = data.maps[data.maps["date"] >= since]
    rrec = data.rounds[data.rounds["date"] >= since]
    team_hist = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for row in mrec.itertuples(index=False):
        for t, won, pk in ((row.team1_id, row.win1, row.picked_by == 1), (row.team2_id, 1 - row.win1, row.picked_by == 2)):
            h = team_hist[t][row.map]
            h["played"] += 1
            h["wins"] += won
            h["picks"] += pk
    for row in rrec.itertuples(index=False):
        team_hist[row.attacker][row.map]["atk_n"] += 1
        team_hist[row.attacker][row.map]["atk_w"] += row.atk_win
        team_hist[row.defender][row.map]["def_n"] += 1
        team_hist[row.defender][row.map]["def_w"] += 1 - row.atk_win
    bans = defaultdict(Counter)
    vetoes_n = Counter()
    for row in data.matches[data.matches["date"] >= since].itertuples(index=False):
        acts = parse_veto(row.veto, row.team1_tag, row.team2_tag)
        for who, act, mp in acts:
            if act == "ban":
                bans[row.team1_id if who == 1 else row.team2_id][mp] += 1
        if acts:
            vetoes_n[row.team1_id] += 1
            vetoes_n[row.team2_id] += 1

    # recent form: last 10 series
    ser_hist = defaultdict(list)
    for row in data.matches.itertuples(index=False):
        for t, o, s1, s2 in ((row.team1_id, row.team2_id, row.score1, row.score2), (row.team2_id, row.team1_id, row.score2, row.score1)):
            ser_hist[t].append({"date": row.date.strftime("%Y-%m-%d"), "opp": int(o), "score": f"{s1}-{s2}",
                                "won": int(s1 > s2), "event": row.event, "match_id": int(row.match_id)})

    pl = data.players
    pname = pl.groupby("player_id")["player"].last().to_dict()
    recent_pl = pl[pl["date"] >= now - pd.Timedelta(days=120)]
    agents = recent_pl.groupby("player_id")["agent"].agg(lambda s: [a for a, _ in Counter(s.dropna()).most_common(3)]).to_dict()

    hist_by_team = defaultdict(list)
    for d, t, rating in st.melo.history:
        hist_by_team[t].append((d, rating))

    extras = insights.team_extras(data, maps_df, active)
    teams_out = []
    ranked = sorted(active, key=lambda t: -power[t])
    for rank, t in enumerate(ranked, 1):
        tm = teams_meta.loc[t] if t in teams_meta.index else None
        rp = st.rm.team_params(t)
        maps_info = []
        for mp in pool:
            h = team_hist[t][mp]
            maps_info.append({
                "map": mp, "strength": r(strength[t][mp]),
                "elo_offset": r(st.melo.o[(t, mp)], 1),
                "played": int(h["played"]), "wins": int(h["wins"]), "picks": int(h["picks"]),
                "bans": int(bans[t][mp]), "vetoes": int(vetoes_n[t]),
                "atk_rw": r(h["atk_w"] / h["atk_n"]) if h["atk_n"] else None,
                "def_rw": r(h["def_w"] / h["def_n"]) if h["def_n"] else None,
                "atk_rating": r(rp["atk"] + rp["atk_map"].get(mp, 0)) if rp else None,
                "def_rating": r(rp["def"] + rp["def_map"].get(mp, 0)) if rp else None,
            })
        roster = []
        for p in st.lineups.get(t, []):
            roster.append({
                "id": int(p), "name": pname.get(p, str(p)), "elo": r(st.pelo.r.get(p, st.pelo.rookie), 0),
                "maps": int(st.pelo.n.get(p, 0)), "rating": r(st.form.get(p, "rating"), 3),
                "acs": r(st.form.get(p, "acs"), 0), "kast": r(st.form.get(p, "kast"), 1),
                "adr": r(st.form.get(p, "adr"), 0), "fkd": r(st.form.get(p, "fkd"), 2),
                "agents": agents.get(p, []),
            })
        hist = hist_by_team.get(t, [])
        # one point per match day
        daily = {}
        for d, v in hist:
            daily[d.strftime("%Y-%m-%d")] = v
        teams_out.append({
            "id": int(t), "name": tm["name"] if tm is not None else str(t), "tag": tm["tag"] if tm is not None else None,
            "logo": local_logo(t, tm["logo"]) if tm is not None else None,
            "region": tm["region"] if tm is not None and isinstance(tm["region"], str) else "International",
            "rank": rank, "power": r(power[t]), "map_elo": r(st.melo.get(t), 0), "series_elo": r(st.selo.get(t), 0),
            "player_elo": r(st.pelo.team(st.lineups.get(t, [])), 0),
            "atk": r(rp["atk"]) if rp else None, "def": r(rp["def"]) if rp else None,
            "form": {"rating": r(st.form.team(st.lineups.get(t, []), "rating"), 3),
                     "fkd": r(st.form.team(st.lineups.get(t, []), "fkd"), 2)},
            "maps": maps_info, "roster": roster,
            "recent": ser_hist[t][-12:][::-1],
            "elo_history": [[k, round(v)] for k, v in sorted(daily.items())],
            **extras.get(int(t), {}),
            "rest_days": r((now - st.last_played[t]).total_seconds() / 86400, 1) if t in st.last_played else None,
            "chemistry": r(st.chem.get(st.lineups.get(t, [])), 3),
            "last_played": st.last_played[t].strftime("%Y-%m-%d") if t in st.last_played else None,
        })

    # ---- upcoming predictions
    up_out = []
    for u in upcoming.itertuples(index=False):
        a, b = int(u.team1_id), int(u.team2_id)
        bo = int(u.best_of) if u.best_of in (1, 3, 5) else 3
        sim = st.veto.simulate(pool, lambda mp, pk: pmap(a, b, mp, pk), st.habits, a, b, bo, n=2000,
                               rng=np.random.default_rng(u.match_id))
        # factor breakdown: average contribution (coef * feature) over pool maps, neutral pick
        contrib = {k: 0.0 for k in META_FEATURES if k != "f_pick"}
        for mp in pool:
            f = feats.get((a, b, mp)) or {k: -v for k, v in feats[(b, a, mp)].items()}
            for k in contrib:
                contrib[k] += coef[META_FEATURES.index(k)] * f[k] / len(pool)
        h2h = [x for x in ser_hist[a] if x["opp"] == b][-5:][::-1]
        # what each component model says on its own (neutral picks, series via the same format)
        la, lb = st.lineups.get(a, []), st.lineups.get(b, [])
        comp = {
            "Map Elo": series_win_prob([st.melo.predict(a, b)] * bo, bo),
            "Round model": float(np.mean([series_win_prob([st.rm.predict(a, b, mp)] * bo, bo) for mp in pool])),
            "Roster Elo": series_win_prob([st.pelo.predict(la, lb)] * bo, bo),
            "Series Elo": st.selo.predict(a, b),
        }
        up_out.append({
            "match_id": int(u.match_id), "date": to_utc_iso(u.date), "event": u.event,
            "event_id": int(u.event_id), "series": u.series, "best_of": bo, "status": u.status,
            "t1": a, "t2": b, "p1": r(sim["p_series"]),
            "p1_elo": r(st.selo.predict(a, b)),
            "scores": {k: r(v) for k, v in sorted(sim["scores"].items(), key=lambda kv: -kv[1])},
            "maps": [{k: (r(v) if isinstance(v, float) else v) for k, v in m.items()} for m in sim["maps"]],
            "top_vetoes": [{"maps": v["maps"], "prob": r(v["prob"])} for v in sim["top_vetoes"]],
            "factors": [{"key": k, "label": FEATURE_LABELS[k], "logit": r(v)} for k, v in contrib.items()],
            "h2h": h2h,
            "components": [{"model": k, "p1": r(v)} for k, v in comp.items()],
        })

    # ---- tournament simulation (Champions 2026)
    em = data.matches[data.matches["event_id"] == FOCUS_EVENT]
    import sqlite3
    from .scrape import DB_PATH
    con = sqlite3.connect(DB_PATH)
    ev_all = pd.read_sql_query("SELECT * FROM matches WHERE event_id=?", con, params=(FOCUS_EVENT,))
    con.close()
    from .tournament import classify
    groups = defaultdict(set)
    for row in ev_all.itertuples(index=False):
        kind, grp = classify(row.series or "")
        if kind == "opening" and grp:
            groups[grp].update(int(x) for x in (row.team1_id, row.team2_id) if x == x and x is not None)
    sp = series_df.set_index("match_id")
    focus_results = []
    for row in em.sort_values("date").itertuples(index=False):
        if row.match_id in sp.index:
            x = sp.loc[row.match_id]
            focus_results.append({"match_id": int(row.match_id), "date": row.date.strftime("%Y-%m-%d"), "series": row.series,
                                  "t1": int(row.team1_id), "t2": int(row.team2_id), "score": f"{int(row.score1)}-{int(row.score2)}",
                                  "win1": int(row.score1 > row.score2), "p1": r(x["p_meta_preveto"]), "p1_elo": r(x["p_selo"])})
    tour, bracket, placements = (simulate_tournament(ev_all, lambda a, b, bo: p_series(a, b, bo), n_sims=20000, detail=True)
                                 if len(ev_all) else ([], [], {}))
    # the probability we gave each already-known pairing (pre-match for finished series)
    up_by_id = {u["match_id"]: u for u in up_out}
    res_by_id = {x["match_id"]: x for x in focus_results}
    for b in bracket:
        mid = b.get("match_id")
        if mid in res_by_id:
            b["p1"] = res_by_id[mid]["p1"]
        elif mid in up_by_id:
            b["p1"] = up_by_id[mid]["p1"]

    # ---- backtest
    bt, ser_pre = backtest(maps_df, series_df)
    recent = ser_pre.sort_values("date").tail(80)[::-1]
    name_of = {t: (teams_meta.loc[t]["name"] if t in teams_meta.index else str(t)) for t in set(series_df["t1"]) | set(series_df["t2"])}
    bt["recent"] = [{"date": x.date.strftime("%Y-%m-%d"), "event": x.event, "t1": name_of[x.t1], "t2": name_of[x.t2],
                     "p1": r(x.p_meta_preveto), "p1_elo": r(x.p_selo), "score": f"{x.score1}-{x.score2}", "win1": int(x.win1)}
                    for x in recent.itertuples(index=False)]
    # feature importance: coefficient * std of feature
    X = maps_df[META_FEATURES]
    bt["coefficients"] = [{"key": k, "label": FEATURE_LABELS[k], "coef": r(c), "impact": r(c * X[k].std())}
                          for k, c in zip(META_FEATURES, coef)]
    bt["veto_weights"] = {k: r(v, 3) for k, v in st.veto.w.items()}
    bt["veto_formats"] = {str(k): v for k, v in st.veto.formats.items()}
    # map side balance from the round model
    gp = st.rm.global_params()
    rs = data.rounds[data.rounds["date"] >= since]
    side = rs.groupby("map")["atk_win"].agg(["mean", "count"]).reset_index()
    bt["map_sides"] = [{"map": m.map, "atk_rw": r(m.mean), "rounds": int(m.count)} for m in side.itertuples(index=False)
                       if m.map in pool]

    # ---- pairwise tables for the in-browser matchup tool
    idx = {t: i for i, t in enumerate(active)}
    ptab = {}
    for (a, b, mp), v in pair.items():
        ptab.setdefault(f"{idx[a]}-{idx[b]}", {})[mp] = [r(x, 3) for x in v]
    habits = {int(t): {act: [[r(st.habits.chosen[(t, act, mp)], 3), r(st.habits.avail[(t, act, mp)], 3)] for mp in pool]
                       for act in ("ban", "pick")} for t in active}

    reg_daily = {}
    for d, regs in st.melo.reg_history:
        reg_daily[d.strftime("%Y-%m-%d")] = {k: round(v, 1) for k, v in regs.items()}
    bt["region_history"] = [[k, v] for k, v in sorted(reg_daily.items())]
    bt["health"] = health()
    write("insights.json", {
        "meta": insights.meta_trends(data), "agents": insights.agent_meta(data),
        "economy": insights.economy_curves(data), "pistol": insights.pistol_stats(data),
        "league_econ": insights.league_econ(data),
        "agent_stats": insights.agent_stats(data, maps_df), "comps": insights.comp_stats(data, maps_df),
        "context": insights.context_stats(maps_df, st),
    })
    write("players.json", insights.players_table(data, st, teams_meta))
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "data_through": now.strftime("%Y-%m-%d"),
        "features": META_FEATURES, "holdout_start": HOLDOUT_START.strftime("%Y-%m-%d"),
        "health_ok": bt["health"]["ok"],
        "counts": {"series": int(len(data.matches)), "maps": int(len(data.maps)), "rounds": int(len(data.rounds)),
                   "player_maps": int(len(data.players)), "events": int(data.matches["event_id"].nunique()),
                   "teams": int(len(active))},
        "pool": pool, "focus_event": FOCUS_EVENT,
        "focus_event_name": ev_all["series"].iloc[0] if False else "Valorant Champions 2026",
    }
    write("meta.json", meta)
    write("teams.json", teams_out)
    write("upcoming.json", up_out)
    write("tournament.json", {"teams": tour, "groups": {k: sorted(v) for k, v in sorted(groups.items())},
                              "results": focus_results, "sims": 20000, "bracket": bracket, "placements": placements,
                              "places": ["1st", "2nd", "3rd", "4th", "5th–6th", "7th–8th", "9th–12th", "13th–16th"]})
    write("backtest.json", bt)
    write("matchup.json", {"teams": active, "pool": pool, "pairs": ptab, "habits": habits,
                           "veto_weights": st.veto.w, "habit_prior": st.habits.prior, "formats": {str(k): v for k, v in st.veto.formats.items()}})
    commit()
    print(f"wrote site data: {len(teams_out)} teams, {len(up_out)} upcoming, {len(tour)} tournament teams")
    print(json.dumps({k: {m: v[m] for m in ("log_loss", "accuracy", "n")} for k, v in bt["series"].items()}, indent=1))
    print(json.dumps({k: {m: v[m] for m in ("log_loss", "accuracy", "n")} for k, v in bt["maps"].items()}, indent=1))


def clean(o):
    """Make an object strict-JSON safe: NaN -> None, numpy scalars -> Python."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        o = o.item()
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


_PENDING: list[str] = []


def write(name, obj):
    """Stage to a temp file; commit() swaps everything in at once, so the site never sees a half-written update."""
    with open(OUT / f"{name}.tmp", "w") as f:
        json.dump(clean(obj), f, separators=(",", ":"), allow_nan=False, default=str)
    _PENDING.append(name)


def commit():
    import os
    # meta.json last: the site treats a new meta.generated_at as "everything else is ready"
    for name in sorted(_PENDING, key=lambda n: n == "meta.json"):
        os.replace(OUT / f"{name}.tmp", OUT / name)
    _PENDING.clear()


if __name__ == "__main__":
    main()
