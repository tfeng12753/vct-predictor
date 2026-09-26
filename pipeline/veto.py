"""Map veto model.

Each ban/pick is treated as a conditional-logit choice among the maps still available:

    ban score(m)  = a_ban  * -logit P(team wins m) + b_ban  * log(team's decayed ban rate of m)
    pick score(m) = a_pick *  logit P(team wins m) + b_pick * log(team's decayed pick rate of m)

a_* measures how "rationally" teams veto by their modelled edge; b_* how much they
follow habit/comfort (which also captures information our model misses, e.g. scrims).
The four weights are fitted by maximum likelihood on historical vetoes.
"""
from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np
from scipy.optimize import minimize

from .models import logit, series_win_prob

# Gauss-Hermite nodes/weights for E[f(Z)], Z ~ N(0, 1)
_x, _w = np.polynomial.hermite_e.hermegauss(9)
GH = tuple(zip(_x.tolist(), (_w / _w.sum()).tolist()))


class VetoHabits:
    """Decayed per-team ban/pick rates, normalised by how often each map was available."""

    def __init__(self, decay=0.93, prior=0.5):
        self.decay, self.prior = decay, prior
        self.chosen = defaultdict(float)     # (team, act, map)
        self.avail = defaultdict(float)      # (team, act, map)

    def rate(self, team, act, mp, n_avail):
        base = 1.0 / max(n_avail, 1)
        c, a = self.chosen[(team, act, mp)], self.avail[(team, act, mp)]
        return (c + self.prior * base * 2) / (a + self.prior * 2)

    def rates(self, team, act, maps):
        return np.array([self.rate(team, act, m, len(maps)) for m in maps])

    def update(self, team, act, available, chosen):
        for m in available:
            self.chosen[(team, act, m)] *= self.decay
            self.avail[(team, act, m)] = self.avail[(team, act, m)] * self.decay + 1.0
        self.chosen[(team, act, chosen)] += 1.0


class VetoModel:
    def __init__(self):
        self.w = {"a_ban": 1.0, "b_ban": 1.0, "a_pick": 1.0, "b_pick": 1.0}
        self.formats: dict[int, list[tuple[int, str]]] = {}

    # ---- fitting
    def fit(self, situations):
        """situations: list of (act, logit_p_own[array], log_rate[array], chosen_idx)."""
        def nll(x, act):
            a, b = x
            tot = 0.0
            for s_act, lp, lr, ci in situations:
                if s_act != act:
                    continue
                z = (a * (-lp if act == "ban" else lp)) + b * lr
                z = z - z.max()
                tot -= z[ci] - np.log(np.exp(z).sum())
            return tot

        for act in ("ban", "pick"):
            res = minimize(nll, x0=[1.0, 1.0], args=(act,), method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-3})
            self.w[f"a_{act}"], self.w[f"b_{act}"] = map(float, res.x)
        n = {act: sum(1 for s in situations if s[0] == act) for act in ("ban", "pick")}
        self.fit_n = n

    def learn_formats(self, sequences: dict[int, list[list[tuple[int, str]]]]):
        """Most common (actor, action) pattern per best-of, actor 1 = team acting first."""
        for bo, seqs in sequences.items():
            pats = Counter(tuple(s) for s in seqs)
            if pats:
                self.formats[bo] = list(pats.most_common(1)[0][0])
        self.formats.setdefault(3, [(1, "ban"), (2, "ban"), (1, "pick"), (2, "pick"), (1, "ban"), (2, "ban")])
        self.formats.setdefault(5, [(1, "ban"), (2, "ban"), (1, "pick"), (2, "pick"), (1, "pick"), (2, "pick")])
        self.formats.setdefault(1, [(1, "ban"), (2, "ban"), (1, "ban"), (2, "ban"), (1, "ban"), (2, "ban")])

    # ---- simulation
    def simulate(self, pool, prob, habits, t1, t2, best_of, n=300, rng=None, sigma=0.0):
        """Monte Carlo over vetoes.

        sigma: spread of a shared per-series "form on the day" shift added to every map's log-odds.
        Maps within a series are correlated; treating them as independent over-amplifies a map
        edge into a series edge. sigma=0 recovers independent maps.

        prob(m, pick) -> P(team1 wins map m) where pick is +1 (team1 pick), -1 (team2) or 0 (decider).
        Returns dict with series win prob, score distribution, and per-map play/pick stats.
        """
        rng = rng or np.random.default_rng(0)
        fmt = self.formats.get(best_of, self.formats[3])
        base = {m: prob(m, 0) for m in pool}
        p_series = 0.0
        score = defaultdict(float)
        played = Counter()
        picked = Counter()
        orders = Counter()
        need = best_of // 2 + 1
        for i in range(n):
            first = t1 if i % 2 == 0 else t2
            second = t2 if first == t1 else t1
            avail = list(pool)
            seq = []
            for actor_pos, act in fmt:
                if len(avail) <= 1:
                    break
                team = first if actor_pos == 1 else second
                p_own = np.array([base[m] if team == t1 else 1 - base[m] for m in avail])
                lr = np.log(habits.rates(team, act, avail))
                a, b = self.w[f"a_{act}"], self.w[f"b_{act}"]
                z = a * (-logit(p_own) if act == "ban" else logit(p_own)) + b * lr
                pz = np.exp(z - z.max())
                pz /= pz.sum()
                j = rng.choice(len(avail), p=pz)
                m = avail.pop(j)
                if act == "pick":
                    seq.append((m, 1 if team == t1 else -1))
            if len(seq) < best_of and avail:
                # decider(s): remaining maps
                seq.append((avail[0], 0))
            seq = seq[:best_of]
            ps0 = [prob(m, pk) for m, pk in seq]
            # integrate the shared shift exactly (Gauss-Hermite): no extra Monte Carlo noise, and the
            # veto sampling stream is identical for every sigma, so comparisons across sigma are paired
            for node, wt in (GH if sigma else ((0.0, 1.0),)):
                shift = sigma * node
                ps = [float(1 / (1 + np.exp(-(logit(p) + shift)))) for p in ps0] if shift else ps0
                p_series += wt * series_win_prob(ps, best_of)
                dist = {(0, 0): 1.0}
                for p in ps:
                    nxt = defaultdict(float)
                    for (x, y), pr in dist.items():
                        for (nx, ny), q in (((x + 1, y), p), ((x, y + 1), 1 - p)):
                            if nx == need or ny == need:
                                score[f"{nx}-{ny}"] += wt * pr * q / n
                            else:
                                nxt[(nx, ny)] += pr * q
                    dist = nxt
            for k, (m, pk) in enumerate(seq):
                played[m] += 1
                if pk:
                    picked[(m, pk)] += 1
            orders[tuple(m for m, _ in seq)] += 1
        maps = []
        for m in pool:
            maps.append({
                "map": m, "play_rate": played[m] / n, "p_neutral": base[m],
                "p_t1_pick": prob(m, 1), "p_t2_pick": prob(m, -1),
                "t1_pick_rate": picked[(m, 1)] / n, "t2_pick_rate": picked[(m, -1)] / n,
            })
        maps.sort(key=lambda d: -d["play_rate"])
        top = [{"maps": list(k), "prob": v / n} for k, v in orders.most_common(5)]
        return {"p_series": p_series / n, "scores": dict(score), "maps": maps, "top_vetoes": top}
