"""Descriptive analytics for the site's Meta, Players and team/match pages.

Everything here describes what happened (rates, curves, compositions); forecasting
lives in engine.py. Functions return plain dicts ready for JSON."""
from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from .data import Data, parse_veto

ROLES = {
    "Duelist": ["Jett", "Raze", "Reyna", "Phoenix", "Yoru", "Neon", "Iso", "Waylay"],
    "Initiator": ["Sova", "Breach", "Skye", "Kayo", "KAY/O", "Fade", "Gekko", "Tejo"],
    "Controller": ["Brimstone", "Viper", "Omen", "Astra", "Harbor", "Clove"],
    "Sentinel": ["Sage", "Cypher", "Killjoy", "Chamber", "Deadlock", "Vyse", "Veto"],
}
ROLE_OF = {a: r for r, agents in ROLES.items() for a in agents}


def r4(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 4)


# --------------------------------------------------------------------------- meta

def meta_trends(data: Data) -> dict:
    """Per quarter: how often each map is played, banned, and how attack-sided it is."""
    maps = data.maps.assign(q=data.maps["date"].dt.to_period("Q").astype(str))
    rounds = data.rounds.assign(q=data.rounds["date"].dt.to_period("Q").astype(str))
    matches = data.matches.assign(q=data.matches["date"].dt.to_period("Q").astype(str))
    played = maps.groupby(["q", "map"]).size().unstack(fill_value=0)
    share = played.div(played.sum(1), axis=0)
    atk = rounds.groupby(["q", "map"])["atk_win"].agg(["mean", "count"])
    bans, vetoes = defaultdict(Counter), Counter()
    for r in matches.itertuples(index=False):
        acts = parse_veto(r.veto, r.team1_tag, r.team2_tag)
        if not acts:
            continue
        vetoes[r.q] += 1
        for _, act, mp in acts:
            if act == "ban":
                bans[r.q][mp] += 1
    out = []
    for q in sorted(played.index):
        for mp in played.columns:
            n = int(played.loc[q, mp])
            a = atk.loc[(q, mp)] if (q, mp) in atk.index else None
            out.append({"q": q, "map": mp, "maps": n, "play_share": r4(share.loc[q, mp]),
                        "ban_rate": r4(bans[q][mp] / vetoes[q]) if vetoes[q] else None,
                        "atk_rw": r4(a["mean"]) if a is not None and a["count"] >= 60 else None})
    return {"rows": out, "quarters": sorted(played.index)}


def agent_meta(data: Data, days=150) -> dict:
    """Agent pick rate per map (share of team-maps that fielded the agent) over a recent window."""
    now = data.maps["date"].max()
    pl = data.players[data.players["date"] >= now - pd.Timedelta(days=days)]
    team_maps = pl.groupby("map")["game_id"].nunique() * 2
    counts = pl.groupby(["map", "agent"]).size()
    agents = pl["agent"].value_counts()
    agents = [a for a in agents.index if agents[a] >= 20]
    rows = []
    for (mp, ag), c in counts.items():
        if ag in agents:
            rows.append({"map": mp, "agent": ag, "rate": r4(c / team_maps[mp]), "n": int(c)})
    overall = {a: r4(agents_n / (pl["game_id"].nunique() * 2)) for a, agents_n in pl["agent"].value_counts().items() if a in agents}
    return {"days": days, "agents": agents, "maps": sorted(team_maps.index.tolist()), "rows": rows,
            "overall": overall, "roles": {a: ROLE_OF.get(a, "Unassigned") for a in agents}}


def economy_curves(data: Data) -> dict | None:
    """How loadout and buy type translate into round wins (attacker perspective)."""
    r = data.rounds[data.rounds["has_econ"] & ~data.rounds["round_num"].isin([1, 13])]
    if len(r) < 5000:
        return None
    diff = (r["atk_loadout"] - r["def_loadout"]) / 1000
    bins = np.arange(-24, 25, 3)
    cut = pd.cut(diff, bins)
    g = r.groupby(cut, observed=True)["atk_win"].agg(["mean", "count"])
    curve = [{"lo": float(iv.left), "hi": float(iv.right), "mid": float(iv.mid), "atk_rw": r4(row["mean"]), "n": int(row["count"])}
             for iv, row in g.iterrows() if row["count"] >= 80]
    order = ["eco", "semi-eco", "semi-buy", "full"]
    buys = r.groupby(["atk_buy", "def_buy"])["atk_win"].agg(["mean", "count"]).reset_index()
    matrix = [{"atk": a, "def": d, "atk_rw": r4(m), "n": int(n)} for a, d, m, n in buys.itertuples(index=False)
              if a in order and d in order and n >= 50]
    return {"curve": curve, "matrix": matrix, "order": order, "rounds": int(len(r))}


def pistol_stats(data: Data) -> dict:
    """What a pistol round is worth: conversion into the next round, the half and the map."""
    r = data.rounds
    key = r.set_index(["game_id", "round_num"])
    out = {}
    pist = r[r["round_num"].isin([1, 13])]
    nxt = key["winner"].reindex(list(zip(pist["game_id"], pist["round_num"] + 1)))
    same = (nxt.to_numpy() == pist["winner"].to_numpy())
    out["next_round"] = r4(np.nanmean(np.where(pd.isna(nxt.to_numpy()), np.nan, same)))
    # won the half the pistol opened
    half = []
    for (gid, start), g in r[r["round_num"].between(1, 24)].assign(
            h=lambda x: np.where(x.round_num <= 12, 1, 13)).groupby(["game_id", "h"]):
        g = g.sort_values("round_num")
        if len(g) < 6 or g.iloc[0]["round_num"] != start:
            continue
        pw = g.iloc[0]["winner"]
        w = (g["winner"] == pw).sum()
        half.append(1.0 if w * 2 > len(g) else (0.5 if w * 2 == len(g) else 0.0))
    out["half"] = r4(np.mean(half)) if half else None
    maps = data.maps.set_index("game_id")
    both = pist.groupby("game_id")["winner"].agg(lambda s: s.iloc[0] if len(s) == 2 and s.nunique() == 1 else 0)
    both = both[both > 0]
    mw = maps.loc[both.index, "win1"].map({1: 1, 0: 2})
    out["map_if_both"] = r4((mw == both).mean()) if len(both) else None
    out["n_maps"] = int(len(both))
    return out


# --------------------------------------------------------------------------- players

def players_table(data: Data, st, teams_meta, days=180) -> list[dict]:
    now = data.maps["date"].max()
    pl = data.players[data.players["date"] >= now - pd.Timedelta(days=days)]
    g = pl.groupby("player_id")
    agg = g.agg(maps=("game_id", "nunique"), rating=("rating", "mean"), acs=("acs", "mean"), kast=("kast", "mean"),
                adr=("adr", "mean"), hs=("hs", "mean"), fk=("fk", "mean"), fd=("fd", "mean"),
                kills=("kills", "sum"), deaths=("deaths", "sum"), name=("player", "last"), team_id=("team_id", "last"))
    agg = agg[agg["maps"] >= 8]
    out = []
    for pid, a in agg.iterrows():
        ags = Counter(pl.loc[pl["player_id"] == pid, "agent"].dropna())
        roles = Counter(ROLE_OF.get(x, "Unassigned") for x in pl.loc[pl["player_id"] == pid, "agent"].dropna())
        tid = int(a["team_id"])
        region = teams_meta.loc[tid]["region"] if tid in teams_meta.index else None
        out.append({
            "id": int(pid), "name": a["name"], "team_id": tid,
            "team": teams_meta.loc[tid]["name"] if tid in teams_meta.index else "",
            "region": region if isinstance(region, str) else "International",
            "maps": int(a["maps"]), "rating": r4(a["rating"]), "acs": r4(a["acs"]), "kast": r4(a["kast"]),
            "adr": r4(a["adr"]), "hs": r4(a["hs"]), "fkd": r4(a["fk"] - a["fd"]), "kd": r4(a["kills"] / max(a["deaths"], 1)),
            "elo": r4(st.pelo.r.get(pid)), "agents": [x for x, _ in ags.most_common(3)],
            "role": roles.most_common(1)[0][0] if roles else "Unassigned",
        })
    out.sort(key=lambda x: -(x["rating"] or 0))
    return out


# --------------------------------------------------------------------------- teams

def team_extras(data: Data, maps_df: pd.DataFrame, team_ids, days=365) -> dict:
    """Per team: agent comps per map, economy profile, and results vs expectation."""
    now = data.maps["date"].max()
    since = now - pd.Timedelta(days=days)
    pl = data.players[data.players["date"] >= now - pd.Timedelta(days=180)]
    comps = defaultdict(lambda: defaultdict(Counter))
    for (gid, tid), g in pl.groupby(["game_id", "team_id"]):
        ags = tuple(sorted(g["agent"].dropna()))
        if len(ags) == 5:
            comps[tid][g["map"].iloc[0]][ags] += 1
    r = data.rounds[data.rounds["date"] >= since]
    rows = []
    for side_team, side in (("attacker", "atk"), ("defender", "def")):
        won = r["atk_win"] if side == "atk" else 1 - r["atk_win"]
        own = r[f"{side}_buy"]
        opp = r["def_buy" if side == "atk" else "atk_buy"]
        rows.append(pd.DataFrame({"team": r[side_team], "won": won, "pistol": r["round_num"].isin([1, 13]),
                                  "own": own, "opp": opp, "econ": r["has_econ"]}))
    tr = pd.concat(rows)
    out = {}
    md = maps_df[["date", "t1", "t2", "win1", "p_melo"]]
    for t in team_ids:
        x = tr[tr["team"] == t]
        e = x[x["econ"] & ~x["pistol"]]
        full = e[(e["own"] == "full") & (e["opp"] == "full")]
        saving = ("eco", "semi-eco")
        eco = e[e["own"].isin(saving) & (e["opp"] == "full")]
        anti = e[(e["own"] == "full") & e["opp"].isin(saving)]
        pist = x[x["pistol"]]
        # cumulative results vs pre-match expectation (Map Elo), last 60 maps
        mt = md[(md["t1"] == t) | (md["t2"] == t)].tail(60)
        exp = np.where(mt["t1"] == t, mt["p_melo"], 1 - mt["p_melo"])
        act = np.where(mt["t1"] == t, mt["win1"], 1 - mt["win1"])
        out[int(t)] = {
            "comps": {mp: [{"agents": list(c), "n": n} for c, n in cs.most_common(2)] for mp, cs in comps[t].items()},
            "econ": {
                "pistol": r4(pist["won"].mean()) if len(pist) >= 10 else None, "pistol_n": int(len(pist)),
                "full_buy": r4(full["won"].mean()) if len(full) >= 40 else None, "full_buy_n": int(len(full)),
                "eco_win": r4(eco["won"].mean()) if len(eco) >= 15 else None, "eco_n": int(len(eco)),
                "anti_eco": r4(anti["won"].mean()) if len(anti) >= 15 else None, "anti_n": int(len(anti)),
            },
            "vs_expectation": [[d.strftime("%Y-%m-%d"), r4(v)] for d, v in zip(mt["date"], np.cumsum(act - exp))],
        }
    return out


def league_econ(data: Data, days=365) -> dict:
    """League-wide reference values for the team economy profile."""
    now = data.maps["date"].max()
    r = data.rounds[(data.rounds["date"] >= now - pd.Timedelta(days=days)) & data.rounds["has_econ"]
                    & ~data.rounds["round_num"].isin([1, 13])]
    if not len(r):
        return {}
    saving = ["eco", "semi-eco"]
    eco = pd.concat([1 - r[r["def_buy"].isin(saving) & (r["atk_buy"] == "full")]["atk_win"],
                     r[r["atk_buy"].isin(saving) & (r["def_buy"] == "full")]["atk_win"]])
    return {"full_buy": 0.5, "eco_win": r4(eco.mean()), "anti_eco": r4(1 - eco.mean()), "pistol": 0.5}


# --------------------------------------------------------------------------- agents vs expectation

def _team_maps(data: Data, maps_df: pd.DataFrame, days: int | None):
    """One row per team per map: the five agents fielded, the result, and the model's pre-match expectation."""
    md = maps_df[["game_id", "date", "t1", "t2", "win1", "p_melo", "map"]]
    if days:
        md = md[md["date"] >= md["date"].max() - pd.Timedelta(days=days)]
    pl = data.players[data.players["game_id"].isin(md["game_id"])]
    comps = pl.groupby(["game_id", "team"])["agent"].agg(lambda s: tuple(sorted(a for a in s if isinstance(a, str))))
    rows = []
    for g in md.itertuples(index=False):
        for team_no, won, exp in ((1, g.win1, g.p_melo), (2, 1 - g.win1, 1 - g.p_melo)):
            comp = comps.get((g.game_id, team_no))
            if comp and len(comp) == 5:
                rows.append((g.game_id, g.date, g.map, comp, won, exp))
    return pd.DataFrame(rows, columns=["game_id", "date", "map", "comp", "won", "exp"])


def agent_stats(data: Data, maps_df: pd.DataFrame, days=365, min_n=40) -> dict:
    """Per agent: pick rate trend by quarter, and win rate against model expectation (team strength removed)."""
    tm = _team_maps(data, maps_df, None)
    tm["q"] = tm["date"].dt.to_period("Q").astype(str)
    ex = tm.explode("comp").rename(columns={"comp": "agent"})
    per_q = ex.groupby(["q", "agent"]).size().unstack(fill_value=0)
    share = per_q.div(tm.groupby("q").size(), axis=0)          # share of team-maps fielding the agent
    recent = ex[ex["date"] >= ex["date"].max() - pd.Timedelta(days=days)]
    out = []
    for ag, g in recent.groupby("agent"):
        if len(g) < min_n:
            continue
        resid = g["won"] - g["exp"]
        se = resid.std(ddof=1) / np.sqrt(len(g))
        out.append({"agent": ag, "role": ROLE_OF.get(ag, "Unassigned"), "n": int(len(g)),
                    "win": r4(g["won"].mean()), "expected": r4(g["exp"].mean()),
                    "vs_exp": r4(resid.mean()), "lo": r4(resid.mean() - 1.96 * se), "hi": r4(resid.mean() + 1.96 * se),
                    "trend": [[q, r4(share.loc[q, ag]) if ag in share.columns else 0] for q in share.index[-10:]]})
    out.sort(key=lambda d: -d["n"])
    return {"days": days, "agents": out, "quarters": list(share.index[-10:])}


def comp_stats(data: Data, maps_df: pd.DataFrame, days=365, min_n=12) -> list[dict]:
    """Most-played full compositions per map, with results against expectation."""
    tm = _team_maps(data, maps_df, days)
    out = []
    for (mp, comp), g in tm.groupby(["map", "comp"]):
        if len(g) < min_n:
            continue
        resid = g["won"] - g["exp"]
        se = resid.std(ddof=1) / np.sqrt(len(g))
        out.append({"map": mp, "agents": list(comp), "n": int(len(g)), "win": r4(g["won"].mean()),
                    "vs_exp": r4(resid.mean()), "lo": r4(resid.mean() - 1.96 * se), "hi": r4(resid.mean() + 1.96 * se)})
    out.sort(key=lambda d: (d["map"], -d["n"]))
    return out


def context_stats(maps_df: pd.DataFrame, st) -> dict:
    """Descriptive checks behind the context signals: home advantage, and upsets on fresh patches."""
    out = {}
    intl = maps_df[maps_df["intl"] == 1]
    h = intl[intl["f_home"] != 0]
    if len(h):
        won = np.where(h["f_home"] > 0, h["win1"], 1 - h["win1"])
        exp = np.where(h["f_home"] > 0, h["p_melo"], 1 - h["p_melo"])
        resid = won - exp
        se = resid.std(ddof=1) / np.sqrt(len(resid))
        out["home"] = {"n": int(len(h)), "win": r4(won.mean()), "expected": r4(exp.mean()),
                       "vs_exp": r4(resid.mean()), "lo": r4(resid.mean() - 1.96 * se), "hi": r4(resid.mean() + 1.96 * se)}
    fav = maps_df.assign(fav_won=np.where(maps_df["p_melo"] >= .5, maps_df["win1"], 1 - maps_df["win1"]),
                         fav_p=np.where(maps_df["p_melo"] >= .5, maps_df["p_melo"], 1 - maps_df["p_melo"]))
    buckets = []
    for label, sel in (("First 5 days of a patch", fav["patch_age"] < 5), ("Rest of the patch", fav["patch_age"] >= 5),
                       ("First 3 weeks of an act", fav["act_age"] < 21), ("Later in the act", fav["act_age"] >= 21),
                       ("International events", fav["intl"] == 1), ("Regional leagues", fav["intl"] == 0)):
        g = fav[sel]
        if len(g) >= 50:
            resid = g["fav_won"] - g["fav_p"]
            se = resid.std(ddof=1) / np.sqrt(len(g))
            buckets.append({"label": label, "n": int(len(g)), "fav_won": r4(g["fav_won"].mean()), "fav_expected": r4(g["fav_p"].mean()),
                            "vs_exp": r4(resid.mean()), "lo": r4(resid.mean() - 1.96 * se), "hi": r4(resid.mean() + 1.96 * se)})
    out["favourites"] = buckets
    return out
