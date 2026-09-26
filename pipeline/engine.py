"""Chronological engine: builds leakage-free features, trains the stacked meta
model walk-forward, replays vetoes, and backtests every model."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .data import Data, parse_veto
from .models import (LN10_400, MapElo, PlayerElo, PlayerForm, RoundModel, SeriesElo, logit,
                     series_win_prob, sigmoid)
from .signals import (COUNTRY_REGION, AgentPool, Chemistry, Experience, MapComfort, MapForm, MetaTracker, Momentum,
                      PatchClock, PistolTracker, Schedule)
from .veto import VetoHabits, VetoModel

EVAL_START = pd.Timestamp("2024-07-01")
# Signals and the stacking learner are chosen on [EVAL_START, HOLDOUT_START); the holdout
# after that is never used for any modelling decision and is reported separately.
HOLDOUT_START = pd.Timestamp("2025-07-01")
# Series Elo (f_selo) is computed but left out of the stack: it is collinear with Map/Roster
# Elo, took a negative weight, and dropping it left backtest log loss unchanged.
META_FEATURES = ["f_elo", "f_round", "f_pelo", "f_form", "f_fkd", "f_pick"]
# Every signal the engine computes; META_FEATURES is the subset selected on the validation window.
ALL_FEATURES = ["f_elo", "f_round", "f_round_econ", "f_pelo", "f_form", "f_fkd", "f_pick", "f_region", "f_selo",
                "f_pistol", "f_mom", "f_rest", "f_load", "f_chem", "f_exp",
                # agents & maps
                "f_mapcomf", "f_depth", "f_meta", "f_mapform",
                # context
                "f_home", "f_elo_newpatch", "f_elo_newact", "f_elo_intl", "f_elo_elim"]
ELIM_WORDS = ("elimination", "decider", "lower", "grand final", "last chance")


@dataclass
class State:
    selo: SeriesElo
    melo: MapElo
    pelo: PlayerElo
    form: PlayerForm
    rm: RoundModel
    lineups: dict
    pool: list
    last_played: dict
    pistol: PistolTracker = field(default_factory=PistolTracker)
    mom: Momentum = field(default_factory=Momentum)
    sched: Schedule = field(default_factory=Schedule)
    chem: Chemistry = field(default_factory=Chemistry)
    exp: Experience = field(default_factory=Experience)
    rm_econ: RoundModel | None = None
    mapcomf: MapComfort = field(default_factory=MapComfort)
    agents: AgentPool = field(default_factory=AgentPool)
    metat: MetaTracker = field(default_factory=MetaTracker)
    mapform: MapForm = field(default_factory=MapForm)
    patches: PatchClock = field(default_factory=PatchClock)
    ctx: dict = field(default_factory=dict)   # per-match context: patch_age, intl, elim, host
    all_maps: set = field(default_factory=set)
    now: pd.Timestamp | None = None
    habits: VetoHabits | None = None
    veto: VetoModel | None = None
    meta: LogisticRegression | None = None
    extras: dict = field(default_factory=dict)


def match_lineups(pl: pd.DataFrame) -> dict[int, list[int]]:
    out = {}
    for team, g in pl.groupby("team"):
        out[int(team)] = g["player_id"].value_counts().index[:5].tolist()
    return out


def context_features(st, a, b, elo):
    """Match context. Interactions let the stack learn whether favourites are more or less
    reliable in a setting; all are antisymmetric so mirrored training rows stay valid."""
    c = st.ctx
    host = c.get("host")
    ra, rb = st.melo.region_of.get(a), st.melo.region_of.get(b)
    home = (1.0 if host and ra == host else 0.0) - (1.0 if host and rb == host else 0.0)
    return {
        "f_home": home if c.get("intl") else 0.0,
        "f_elo_newpatch": elo * (1.0 if c.get("patch_age", 60) < 5 else 0.0),
        "f_elo_newact": elo * (1.0 if c.get("act_age", 60) < 21 else 0.0),
        "f_elo_intl": elo * c.get("intl", 0.0),
        "f_elo_elim": elo * c.get("elim", 0.0),
    }


def base_features(st: State, a, b, la, lb, mp):
    """Model-agnostic, pick-agnostic features from team a's perspective."""
    elo = LN10_400 * st.melo.diff(a, b, mp)
    return {
        # Map Elo including its region offsets. (A separate region feature was tested; on the
        # validation window it was no better, see pipeline/ablation.py.)
        "f_elo": elo,
        "f_round": float(logit(st.rm.predict(a, b, mp))),
        "f_pelo": LN10_400 * (st.pelo.team(la) - st.pelo.team(lb)),
        "f_form": 10 * (st.form.team(la, "rating") - st.form.team(lb, "rating")),
        "f_fkd": st.form.team(la, "fkd") - st.form.team(lb, "fkd"),
        "f_selo": LN10_400 * (st.selo.get(a) - st.selo.get(b)),
        "f_region": LN10_400 * st.melo.region_diff(a, b),
        "f_round_econ": float(logit(st.rm_econ.predict(a, b, mp))) if st.rm_econ is not None else 0.0,
        "f_pistol": st.pistol.logit(a) - st.pistol.logit(b),
        "f_mom": st.mom.get(a) - st.mom.get(b),
        "f_rest": (math.log1p(st.sched.rest_days(a, st.now)) - math.log1p(st.sched.rest_days(b, st.now))) if st.now is not None else 0.0,
        "f_load": (st.sched.load(a, st.now) - st.sched.load(b, st.now)) / 10 if st.now is not None else 0.0,
        "f_chem": st.chem.get(la) - st.chem.get(lb),
        "f_exp": st.exp.get(a) - st.exp.get(b),
        "f_mapcomf": st.mapcomf.get(la, mp) - st.mapcomf.get(lb, mp),
        "f_depth": st.agents.team_depth(la) - st.agents.team_depth(lb),
        "f_meta": 5 * (st.metat.alignment(a, mp) - st.metat.alignment(b, mp)),
        "f_mapform": 10 * (st.mapform.get(la, mp) - st.mapform.get(lb, mp)),
        **context_features(st, a, b, elo),
    }


# Tuned with pipeline/tune.py on 2024-01-01 .. EVAL_START (before any reported backtest).
PARAMS: dict = {
    "rm_econ": {"C": 0.05, "tau": 0.4, "halflife_days": 180, "map_scale": 0.35, "econ": True},
    "selo": {"k": 24, "season_carry": 0.6},
    "melo": {"k": 16, "k_map": 0, "roster_regress": 0.8, "k_region": 20},
    "pelo": {"k": 20, "k_ind": 0},
    "form": {},
    # tier-2 priors: a newcomer starts at (default + beta * tier-2 rating edge). 0 disables.
    "t2": {"beta_player": 0.0, "beta_team": 0.0, "min_maps": 10},
    # Shared per-series log-odds shift (maps within a series are correlated), by context. Tested with exact
    # Gauss-Hermite integration: international series improve in the same direction on validation and
    # holdout, but n~80 per window leaves the intervals across zero, so it stays off for now.
    "series": {"sigma_regional": 0.0, "sigma_intl": 0.0},
    "rm": {"C": 0.05, "tau": 0.4, "halflife_days": 180, "map_scale": 0.35},
}


def run(data: Data, verbose=True, params=None, components_only=False):
    p = {**PARAMS, **(params or {})}
    st = State(SeriesElo(**p["selo"]), MapElo(**p["melo"]), PlayerElo(**p["pelo"]), PlayerForm(**p["form"]),
               RoundModel(**p["rm"]), {}, [], {})
    st.rm.prepare(data.rounds)
    if data.rounds["has_econ"].any():
        st.rm_econ = RoundModel(**p["rm_econ"])
        st.rm_econ.prepare(data.rounds)
    # ---- tier-2 (Challengers/Ascension) ratings, advanced in date order alongside tier one
    t2p = p["t2"]
    t2_maps = data.t2_maps if data.t2_maps is not None else pd.DataFrame()
    use_t2 = len(t2_maps) and (t2p["beta_player"] or t2p["beta_team"])
    pelo2, melo2 = PlayerElo(k=20, k_ind=0, rookie=1500), MapElo(k=24, k_map=0, k_region=0, new_team=1500)
    t2_pl = {k: g for k, g in data.t2_players.groupby("game_id")} if use_t2 else {}
    t2_rows = list(t2_maps.itertuples(index=False)) if use_t2 else []
    t2_i = 0
    if use_t2:
        def p_prior(pid):
            if pelo2.n.get(pid, 0) < t2p["min_maps"]:
                return st.pelo.rookie
            return st.pelo.rookie + t2p["beta_player"] * (pelo2.r[pid] - 1500)

        def t_prior(tid):
            if tid not in melo2.r:
                return None
            return st.melo.new_team + t2p["beta_team"] * (melo2.r[tid] - 1500)
        if t2p["beta_player"]:
            st.pelo.prior = p_prior
        if t2p["beta_team"]:
            st.melo.prior = t_prior
    st.all_maps = set(data.maps["map"])
    pistol_rounds = data.rounds[data.rounds["round_num"].isin([1, 13])]
    pistols_by_game = defaultdict(list)
    for g, att, dfd, aw in zip(pistol_rounds["game_id"], pistol_rounds["attacker"], pistol_rounds["defender"], pistol_rounds["atk_win"]):
        pistols_by_game[g].append((att, dfd) if aw else (dfd, att))
    st.melo.region_of = {int(t): r for t, r in data.teams["region"].items() if isinstance(r, str)}
    maps_by_match = {k: g for k, g in data.maps.groupby("match_id")}
    pl_by_match = {k: g for k, g in data.players.groupby("match_id")}

    map_rows, cand_rows, series_rows = [], [], []
    veto_log = []  # (match_id, date, t1, t2, best_of, pool, actions)
    fit_week = None

    for m in data.matches.itertuples(index=False):
        week = (m.date - pd.Timedelta(days=m.date.weekday())).normalize()
        if week != fit_week:
            st.rm.fit(week)
            if st.rm_econ is not None:
                st.rm_econ.fit(week)
            fit_week = week
        st.now = m.date
        while t2_i < len(t2_rows) and t2_rows[t2_i].date < m.date:
            r2 = t2_rows[t2_i]
            t2_i += 1
            g2 = t2_pl.get(r2.game_id)
            if g2 is not None and len(g2) >= 8:
                pelo2.update(g2[g2["team"] == 1]["player_id"].tolist(), g2[g2["team"] == 2]["player_id"].tolist(), r2.score1, r2.score2)
            melo2.update(r2.team1_id, r2.team2_id, r2.map, r2.score1, r2.score2)
        series_l = (m.series or "").lower()
        st.ctx = {
            "patch_age": st.patches.age(m.patch, m.date),
            "act_age": st.patches.act_age(m.patch, m.date),
            "intl": 1.0 if m.tier in ("masters", "champions") else 0.0,
            "elim": 1.0 if any(w in series_l for w in ELIM_WORDS) else 0.0,
            "host": COUNTRY_REGION.get(m.event_country) if isinstance(m.event_country, str) else None,
        }
        for mod in (st.selo, st.melo, st.pelo):
            mod.new_year(m.year)
        a, b = m.team1_id, m.team2_id
        pl = pl_by_match.get(m.match_id)
        lu = match_lineups(pl) if pl is not None else {}
        la, lb = lu.get(1, st.lineups.get(a, [])), lu.get(2, st.lineups.get(b, []))
        for t, l in ((a, la), (b, lb)):
            prev = st.lineups.get(t)
            if prev and l:
                st.melo.roster_change(t, len(set(l) - set(prev)) / 5)

        mg = maps_by_match.get(m.match_id)
        played = list(mg["map"]) if mg is not None else []
        actions = parse_veto(m.veto, m.team1_tag, m.team2_tag)
        veto_pool = [mp for _, _, mp in actions]
        pool = veto_pool if len(veto_pool) >= 5 else sorted(set(st.pool) | set(played))
        pool = sorted(set(pool) | set(played))

        common = None
        for mp in pool:
            f = base_features(st, a, b, la, lb, mp)
            common = f
            cand_rows.append({"match_id": m.match_id, "date": m.date, "map": mp, **f})
        series_rows.append({
            "match_id": m.match_id, "date": m.date, "t1": a, "t2": b, "best_of": m.best_of,
            "win1": int(m.score1 > m.score2), "score1": m.score1, "score2": m.score2,
            "tier": m.tier, "event": m.event, "p_selo": st.selo.predict(a, b),
            "p_melo_base": st.melo.predict(a, b), "p_pelo": st.pelo.predict(la, lb),
        })
        if mg is not None:
            for r in mg.itertuples(index=False):
                f = base_features(st, a, b, la, lb, r.map)
                pick = 1 if r.picked_by == 1 else (-1 if r.picked_by == 2 else 0)
                map_rows.append({
                    "game_id": r.game_id, "match_id": m.match_id, "date": m.date, "map": r.map,
                    "map_order": r.map_order, "t1": a, "t2": b, "win1": r.win1, "tier": m.tier,
                    "f_pick": pick, **f,
                    "p_melo": st.melo.predict(a, b, r.map), "p_round": st.rm.predict(a, b, r.map),
                    "p_pelo": st.pelo.predict(la, lb),
                    "patch_age": st.ctx.get("patch_age"), "act_age": st.ctx.get("act_age"),
                    "intl": st.ctx.get("intl"), "patch": m.patch,
                })
        if actions:
            veto_log.append((m.match_id, m.date, a, b, m.best_of, pool, actions))

        # ---- updates (after all pre-match predictions were recorded)
        st.selo.update(a, b, m.score1, m.score2)
        if mg is not None:
            for r in mg.itertuples(index=False):
                pre = st.melo.predict(a, b, r.map)
                st.mom.update(a, r.win1 - pre)
                st.mom.update(b, (1 - r.win1) - (1 - pre))
                st.pistol.update(pistols_by_game.get(r.game_id, []))
                st.exp.update(a)
                st.exp.update(b)
                st.melo.update(a, b, r.map, r.score1, r.score2, date=m.date)
                gp = pl[pl["game_id"] == r.game_id] if pl is not None else None
                perf = {k: v for k, v in zip(gp["player_id"], gp["rating"]) if v == v} if gp is not None and len(gp) else None
                la_g = gp[gp["team"] == 1]["player_id"].tolist() if gp is not None and len(gp) else la
                lb_g = gp[gp["team"] == 2]["player_id"].tolist() if gp is not None and len(gp) else lb
                st.pelo.update(la_g, lb_g, r.score1, r.score2, perf)
                st.chem.update(la_g)
                st.chem.update(lb_g)
                st.mapcomf.update(la_g, r.map, st.all_maps)
                st.mapcomf.update(lb_g, r.map, st.all_maps)
                if gp is not None and len(gp):
                    for team_no, tid in ((1, a), (2, b)):
                        g_t = gp[gp["team"] == team_no]
                        st.metat.update(tid, r.map, [x for x in g_t["agent"] if isinstance(x, str)])
                        for pid, ag, rt in zip(g_t["player_id"], g_t["agent"], g_t["rating"]):
                            st.agents.update(pid, ag if isinstance(ag, str) else None)
                            st.mapform.update(pid, r.map, rt)
                if gp is not None:
                    for row in gp.to_dict("records"):
                        st.form.update(row)
        if la:
            st.lineups[a] = la
        if lb:
            st.lineups[b] = lb
        st.last_played[a] = st.last_played[b] = m.date
        n_maps = len(mg) if mg is not None else 0
        st.sched.update(a, m.date, n_maps)
        st.sched.update(b, m.date, n_maps)
        if len(veto_pool) >= 5:
            st.pool = sorted(set(veto_pool))
    st.rm.fit(pd.Timestamp.now().normalize() + pd.Timedelta(days=1))
    if st.rm_econ is not None:
        st.rm_econ.fit(pd.Timestamp.now().normalize() + pd.Timedelta(days=1))
    st.now = pd.Timestamp.now()

    maps_df = pd.DataFrame(map_rows)
    cand_df = pd.DataFrame(cand_rows)
    series_df = pd.DataFrame(series_rows)
    if verbose:
        print(f"features: {len(maps_df)} maps, {len(series_df)} series, {len(cand_df)} candidate map rows")
    if components_only:
        return st, maps_df, series_df, cand_df

    # ---- stacked meta model, walk-forward monthly
    cand_pred = {}  # (match_id, map) -> {pick: p}
    maps_df["p_meta"] = walk_forward(maps_df, META_FEATURES, cand_df=cand_df, cand_out=cand_pred)
    st.meta = fit_meta(maps_df)
    st.extras["meta_coef"] = dict(zip(META_FEATURES, st.meta.coef_.ravel().tolist()))

    # ---- veto: habits replay, fit weights on pre-eval data, then backtest series pre-veto
    fmt_seqs = defaultdict(list)
    for _, _, _, _, bo, _, actions in veto_log:
        acts = [(who, act) for who, act, _ in actions if act != "remains"]
        if not acts:
            continue
        first = acts[0][0]
        fmt_seqs[bo].append([(1 if w == first else 2, act) for w, act in acts])
    vm = VetoModel()
    vm.learn_formats(fmt_seqs)

    # neutral per-map probabilities for veto situations; pre-eval rows use the in-sample final meta
    # (only for fitting 4 veto weights), eval rows use the walk-forward predictions.
    cand_all = {}
    store_cand(st.meta, cand_df, cand_all, META_FEATURES)
    situations_pre, situations_all = [], []
    habits = VetoHabits()
    veto_by_match = {v[0]: v for v in veto_log}
    rng = np.random.default_rng(7)
    series_df["p_meta_postveto"] = np.nan
    series_df["p_meta_preveto"] = np.nan
    mp_by_match = {k: g for k, g in maps_df.groupby("match_id")}

    def replay(fit_weights_on_pre):
        nonlocal habits
        habits = VetoHabits()
        for idx, srow in series_df.iterrows():
            mid = srow["match_id"]
            v = veto_by_match.get(mid)
            in_eval = srow["date"] >= EVAL_START
            src = cand_pred if in_eval else cand_all
            if in_eval and not fit_weights_on_pre:
                pool = v[5] if v else sorted({k[1] for k in src if k[0] == mid})
                pool = [p for p in pool if (mid, p) in src]
                if len(pool) >= 3 and srow["best_of"] in (1, 3, 5):
                    probf = lambda mp, pk, mid=mid, src=src: src[(mid, mp)][pk]
                    sig = p["series"]["sigma_intl" if srow["tier"] in ("masters", "champions") else "sigma_regional"]
                    sim = vm.simulate(pool, probf, habits, srow["t1"], srow["t2"], int(srow["best_of"]), n=120, rng=rng, sigma=sig)
                    series_df.at[idx, "p_meta_preveto"] = sim["p_series"]
                if v is not None and srow["best_of"] in (1, 3, 5):
                    seq = [(mp, 1 if who == 1 else -1) for who, act, mp in v[6] if act == "pick"]
                    seq += [(mp, 0) for who, act, mp in v[6] if act == "remains"]
                    if len(seq) >= srow["best_of"] and all((mid, mp) in src for mp, _ in seq):
                        ps = [src[(mid, mp)][pk] for mp, pk in seq]
                        series_df.at[idx, "p_meta_postveto"] = series_win_prob(ps, int(srow["best_of"]))
            if v is None:
                continue
            _, _, t1, t2, bo, pool, actions = v
            avail = [p for p in pool]
            for who, act, mp in actions:
                if act == "remains" or mp not in avail:
                    continue
                team = t1 if who == 1 else t2
                if fit_weights_on_pre and all((mid, p) in src for p in avail) and len(avail) > 1:
                    p1 = np.array([src[(mid, p)][0] for p in avail])
                    p_own = p1 if who == 1 else 1 - p1
                    s = (act, logit(p_own), np.log(habits.rates(team, act, avail)), avail.index(mp))
                    situations_all.append(s)
                    if not in_eval:
                        situations_pre.append(s)
                habits.update(team, act, avail, mp)
                avail.remove(mp)

    replay(fit_weights_on_pre=True)
    vm.fit(situations_pre)
    if verbose:
        print("veto weights (pre-eval fit):", {k: round(v, 3) for k, v in vm.w.items()}, vm.fit_n)
    replay(fit_weights_on_pre=False)
    backtest_veto_w = dict(vm.w)
    vm.fit(situations_all)
    st.veto, st.habits = vm, habits
    st.extras["series_sigma"] = dict(p["series"])
    st.extras["veto_weights_backtest"] = backtest_veto_w
    st.extras["veto_fit_n"] = vm.fit_n
    return st, maps_df, series_df, cand_df


def walk_forward(maps_df, features, learner="logistic", cand_df=None, cand_out=None, start=None):
    """Monthly walk-forward: each month is predicted by a stack fitted on all earlier maps."""
    out = pd.Series(np.nan, index=maps_df.index)
    blocks = pd.date_range(start or EVAL_START, maps_df["date"].max() + pd.offsets.MonthBegin(1), freq="MS")
    for i, start in enumerate(blocks[:-1]):
        end = blocks[i + 1]
        clf = fit_meta(maps_df[maps_df["date"] < start], features, learner)
        sel = (maps_df["date"] >= start) & (maps_df["date"] < end)
        if sel.any():
            out[sel] = clf.predict_proba(maps_df.loc[sel, features].to_numpy())[:, 1]
        if cand_df is not None:
            store_cand(clf, cand_df[(cand_df["date"] >= start) & (cand_df["date"] < end)], cand_out, features)
    return out


class SymmetricGBM:
    """Gradient boosting on mirrored rows, averaged over both orientations at predict time."""

    def __init__(self):
        from sklearn.ensemble import HistGradientBoostingClassifier
        self.m = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.05, max_leaf_nodes=8,
                                                min_samples_leaf=60, l2_regularization=1.0, random_state=0)

    def fit(self, X, y):
        self.m.fit(X, y)
        return self

    def predict_proba(self, X):
        p = 0.5 * (self.m.predict_proba(X)[:, 1] + 1 - self.m.predict_proba(-X)[:, 1])
        return np.c_[1 - p, p]


def fit_meta(df: pd.DataFrame, features=None, learner="logistic"):
    """Symmetric stack: rows are mirrored so team order carries no signal."""
    features = features or META_FEATURES
    X = df[features].to_numpy()
    y = df["win1"].to_numpy()
    Xs = np.vstack([X, -X])
    ys = np.concatenate([y, 1 - y])
    if learner == "gbm":
        return SymmetricGBM().fit(Xs, ys)
    clf = LogisticRegression(C=1.0, fit_intercept=False, max_iter=1000)
    clf.fit(Xs, ys)
    return clf


def store_cand(clf, cs: pd.DataFrame, out: dict, features=None):
    features = features or META_FEATURES
    if cs.empty:
        return
    for pk in (0, 1, -1):
        X = cs.assign(f_pick=pk)[features].to_numpy()
        p = clf.predict_proba(X)[:, 1]
        for mid, mp, pv in zip(cs["match_id"], cs["map"], p):
            out.setdefault((mid, mp), {})[pk] = float(pv)
