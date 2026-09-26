"""Monte Carlo simulation of a Champions-style event: four GSL groups, then an
8-team double-elimination playoff. Completed series and already-drawn pairings
are respected; everything else is sampled from pre-computed series probabilities.

Besides stage probabilities, the simulation records every bracket slot (who is likely
to play in it and who is likely to win it) and each team's finishing-place distribution."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

GROUPS = "ABCD"
# Upper quarterfinal seeding: group winners meet runners-up from another group.
# vlr.gg fills in the real pairings once groups finish; those override this.
UQF = [("A1", "C2"), ("B1", "D2"), ("C1", "A2"), ("D1", "B2")]

# bracket slots in display order: (slot id, label, stage, best-of)
SLOTS = [(f"{g}-{k}", lbl, f"Group {g}", 3) for g in GROUPS for k, lbl in
         (("o1", "Opening"), ("o2", "Opening"), ("w", "Winners' match"), ("e", "Elimination match"), ("d", "Decider"))] + [
    ("uqf1", "Upper quarterfinal", "Upper bracket", 3), ("uqf2", "Upper quarterfinal", "Upper bracket", 3),
    ("uqf3", "Upper quarterfinal", "Upper bracket", 3), ("uqf4", "Upper quarterfinal", "Upper bracket", 3),
    ("usf1", "Upper semifinal", "Upper bracket", 3), ("usf2", "Upper semifinal", "Upper bracket", 3),
    ("uf", "Upper final", "Upper bracket", 3),
    ("lr1a", "Lower round 1", "Lower bracket", 3), ("lr1b", "Lower round 1", "Lower bracket", 3),
    ("lr2a", "Lower round 2", "Lower bracket", 3), ("lr2b", "Lower round 2", "Lower bracket", 3),
    ("lr3", "Lower round 3", "Lower bracket", 3), ("lf", "Lower final", "Lower bracket", 5),
    ("gf", "Grand final", "Grand final", 5),
]
PLACES = ["1st", "2nd", "3rd", "4th", "5th–6th", "7th–8th", "9th–12th", "13th–16th"]


def classify(series: str) -> tuple[str, str | None]:
    s = series.lower()
    g = re.search(r"\(([a-d])\)", s)
    grp = g.group(1).upper() if g else None
    for key in ("opening", "winner", "elimination", "decider"):
        if key in s and grp:
            return key, grp
    for key, name in (("upper quarter", "uqf"), ("upper semi", "usf"), ("upper final", "uf"),
                      ("lower round 1", "lr1"), ("lower round 2", "lr2"), ("lower round 3", "lr3"),
                      ("lower final", "lf"), ("grand final", "gf")):
        if key in s:
            return name, None
    return "other", None


def real_slots(event_matches: pd.DataFrame) -> dict[str, object]:
    """Map vlr.gg matches onto bracket slot ids (by stage, in match-id order)."""
    em = event_matches.sort_values("match_id")
    by = defaultdict(list)
    for r in em.itertuples(index=False):
        by[classify(r.series or "")].append(r)
    out = {}
    for g in GROUPS:
        for i, k in enumerate(("o1", "o2")):
            if i < len(by[("opening", g)]):
                out[f"{g}-{k}"] = by[("opening", g)][i]
        for kind, k in (("winner", "w"), ("elimination", "e"), ("decider", "d")):
            if by[(kind, g)]:
                out[f"{g}-{k}"] = by[(kind, g)][0]
    for kind, ids in (("uqf", ["uqf1", "uqf2", "uqf3", "uqf4"]), ("usf", ["usf1", "usf2"]), ("uf", ["uf"]),
                      ("lr1", ["lr1a", "lr1b"]), ("lr2", ["lr2a", "lr2b"]), ("lr3", ["lr3"]), ("lf", ["lf"]), ("gf", ["gf"])):
        for sid, r in zip(ids, by[(kind, None)]):
            out[sid] = r
    return out


def _known(r):
    return r is not None and not pd.isna(r.team1_id) and not pd.isna(r.team2_id)


def simulate(event_matches: pd.DataFrame, p_series, n_sims=20000, seed=11, detail=False):
    """p_series(a, b, best_of) -> P(a beats b).

    Returns per-team stage probabilities; with detail=True returns
    (teams, bracket slots, placement distributions)."""
    rng = np.random.default_rng(seed)
    real = real_slots(event_matches)
    teams = set()
    for g in GROUPS:
        for k in ("o1", "o2"):
            r = real.get(f"{g}-{k}")
            if _known(r):
                teams.update([int(r.team1_id), int(r.team2_id)])

    cache = {}
    occ = defaultdict(Counter)     # slot -> team -> sims in which the team plays this slot
    wins = defaultdict(Counter)    # slot -> team -> sims in which the team wins it
    pairs = defaultdict(Counter)   # slot -> (a, b) pairing counts
    place = defaultdict(Counter)   # team -> place bucket -> count
    counts = defaultdict(lambda: defaultdict(int))

    def play(slot, a, b, bo):
        """Winner/loser of a slot, honouring a completed real result between the same teams."""
        r = real.get(slot)
        if r is not None and r.status == "completed" and _known(r) and {int(r.team1_id), int(r.team2_id)} == {a, b}:
            w = int(r.team1_id) if r.score1 > r.score2 else int(r.team2_id)
            res = (w, b if w == a else a)
        else:
            key = (a, b, bo)
            if key not in cache:
                cache[key] = p_series(a, b, bo)
            res = (a, b) if rng.random() < cache[key] else (b, a)
        occ[slot][a] += 1
        occ[slot][b] += 1
        wins[slot][res[0]] += 1
        pairs[slot][tuple(sorted((a, b)))] += 1
        return res

    for _ in range(n_sims):
        seed_of = {}
        for g in GROUPS:
            o = [real.get(f"{g}-o1"), real.get(f"{g}-o2")]
            if not all(_known(x) for x in o):
                continue
            w1, l1 = play(f"{g}-o1", int(o[0].team1_id), int(o[0].team2_id), 3)
            w2, l2 = play(f"{g}-o2", int(o[1].team1_id), int(o[1].team2_id), 3)
            ww, wl = play(f"{g}-w", w1, w2, 3)
            ew, el = play(f"{g}-e", l1, l2, 3)
            dw, dl = play(f"{g}-d", wl, ew, 3)
            seed_of[f"{g}1"], seed_of[f"{g}2"] = ww, dw
            counts[ww]["group_1st"] += 1
            for t in (ww, dw):
                counts[t]["playoffs"] += 1
            place[el]["13th–16th"] += 1
            place[dl]["9th–12th"] += 1
        if len(seed_of) < 8:
            continue
        real_uqf = [real.get(s) for s in ("uqf1", "uqf2", "uqf3", "uqf4")]
        if all(_known(r) and int(r.team1_id) in seed_of.values() and int(r.team2_id) in seed_of.values() for r in real_uqf):
            pairs_q = [(int(r.team1_id), int(r.team2_id)) for r in real_uqf]
        else:
            pairs_q = [(seed_of[x], seed_of[y]) for x, y in UQF]
        uw, ul = zip(*[play(f"uqf{i + 1}", a, b, 3) for i, (a, b) in enumerate(pairs_q)])
        for t in uw:
            counts[t]["upper_semis"] += 1
        s1w, s1l = play("usf1", uw[0], uw[1], 3)
        s2w, s2l = play("usf2", uw[2], uw[3], 3)
        l1w, l1l = play("lr1a", ul[0], ul[1], 3)
        l2w, l2l = play("lr1b", ul[2], ul[3], 3)
        # lower round 2 crosses the upper-semi losers to avoid immediate rematches
        r2a, r2al = play("lr2a", l1w, s2l, 3)
        r2b, r2bl = play("lr2b", l2w, s1l, 3)
        ufw, ufl = play("uf", s1w, s2w, 3)
        l3w, l3l = play("lr3", r2a, r2b, 3)
        lfw, lfl = play("lf", ufl, l3w, 5)
        gfw, gfl = play("gf", ufw, lfw, 5)
        for t in (s1w, s2w, r2a, r2b):
            counts[t]["top4"] += 1
        for t in (ufw, lfw):
            counts[t]["final"] += 1
        counts[gfw]["champion"] += 1
        for t in set(uw) | {l1w, l2w}:
            counts[t]["top6"] += 1
        for t, pl in ((gfw, "1st"), (gfl, "2nd"), (lfl, "3rd"), (l3l, "4th"), (r2al, "5th–6th"), (r2bl, "5th–6th"),
                      (l1l, "7th–8th"), (l2l, "7th–8th")):
            place[t][pl] += 1

    out = []
    for t in teams:
        c = counts[t]
        out.append({"team_id": int(t), **{k: c[k] / n_sims for k in
                    ("group_1st", "playoffs", "top6", "upper_semis", "top4", "final", "champion")}})
    out.sort(key=lambda d: -d["champion"])
    if not detail:
        return out

    bracket = []
    for sid, label, stage, bo in SLOTS:
        r = real.get(sid)
        top = sorted(occ[sid].items(), key=lambda kv: -kv[1])[:4]
        pair, pn = (pairs[sid].most_common(1) or [((None, None), 0)])[0]
        entry = {
            "slot": sid, "label": label, "stage": stage, "best_of": bo,
            "teams": [{"team_id": int(t), "plays": round(n / n_sims, 4), "wins": round(wins[sid][t] / n_sims, 4)} for t, n in top],
            "likely_pair": [int(x) for x in pair] if pair[0] is not None else None, "likely_pair_p": round(pn / n_sims, 4),
        }
        if r is not None:
            entry["match_id"] = int(r.match_id)
            if _known(r):
                entry["t1"], entry["t2"] = int(r.team1_id), int(r.team2_id)
            if r.status == "completed" and _known(r):
                entry["score"] = f"{int(r.score1)}-{int(r.score2)}"
                entry["winner"] = int(r.team1_id) if r.score1 > r.score2 else int(r.team2_id)
        bracket.append(entry)
    placements = {int(t): {pl: round(place[t][pl] / n_sims, 4) for pl in PLACES} for t in teams}
    return out, bracket, placements
