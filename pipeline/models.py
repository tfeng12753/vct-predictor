"""Rating models. Every model is strictly online: it is asked for a prediction
using only information available before a series starts, then updated with the
result. That lets the same code produce honest backtests and live predictions."""
from __future__ import annotations

import math
from collections import defaultdict
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.linear_model import LogisticRegression

LN10_400 = math.log(10) / 400


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def logit(p, eps=1e-6):
    p = np.clip(p, eps, 1 - eps)
    return np.log(p / (1 - p))


# --------------------------------------------------------------------------- round -> map

@lru_cache(maxsize=200_000)
def _map_win_from_start(pa: float, pd_: float, a_starts_atk: bool) -> float:
    """Exact P(team A wins a map) given per-round win probs.

    pa  = P(A wins a round while attacking), pd_ = P(A wins a round while defending).
    Regulation is 24 rounds with a side swap after 12; first to 13. At 12-12 the
    teams play overtime pairs (one round on each side) until someone leads by 2.
    """
    # dist[(a, b)] over scores after each round of regulation
    dist = {(0, 0): 1.0}
    win = 0.0
    for r in range(24):
        first_half = r < 12
        a_attacking = first_half == a_starts_atk
        p = pa if a_attacking else pd_
        nxt: dict = defaultdict(float)
        for (a, b), pr in dist.items():
            for da, db, q in ((1, 0, p), (0, 1, 1 - p)):
                na, nb = a + da, b + db
                if na == 13:
                    win += pr * q
                elif nb == 13:
                    continue
                else:
                    nxt[(na, nb)] += pr * q
        dist = nxt
    tie = dist.get((12, 12), 0.0)
    both = pa * pd_
    neither = (1 - pa) * (1 - pd_)
    ot = both / (both + neither) if both + neither > 0 else 0.5
    return win + tie * ot


def map_win_prob(pa: float, pd_: float) -> float:
    """Average over who starts on attack (decided by knife-less side choice we can't see)."""
    pa, pd_ = round(float(pa), 4), round(float(pd_), 4)
    return 0.5 * (_map_win_from_start(pa, pd_, True) + _map_win_from_start(pa, pd_, False))


def series_win_prob(map_probs: list[float], best_of: int) -> float:
    """P(A wins series) when maps are played in the given order (independent maps)."""
    need = best_of // 2 + 1
    dist = {(0, 0): 1.0}
    win = 0.0
    for p in map_probs[:best_of]:
        nxt: dict = defaultdict(float)
        for (a, b), pr in dist.items():
            if a + 1 == need:
                win += pr * p
            else:
                nxt[(a + 1, b)] += pr * p
            if b + 1 < need:
                nxt[(a, b + 1)] += pr * (1 - p)
        dist = nxt
    return win


def series_score_dist(map_probs: list[float], best_of: int) -> dict[str, float]:
    need = best_of // 2 + 1
    dist = {(0, 0): 1.0}
    final: dict = defaultdict(float)
    for p in map_probs[:best_of]:
        nxt: dict = defaultdict(float)
        for (a, b), pr in dist.items():
            for (na, nb), q in (((a + 1, b), p), ((a, b + 1), 1 - p)):
                if na == need or nb == need:
                    final[f"{na}-{nb}"] += pr * q
                else:
                    nxt[(na, nb)] += pr * q
        dist = nxt
    return dict(final)


# --------------------------------------------------------------------------- Elo family

class SeriesElo:
    """Plain team Elo on series results with a margin-of-victory multiplier."""

    def __init__(self, k=40.0, init=1500.0, new_team=1450.0, season_carry=0.75):
        self.k, self.init, self.new_team, self.carry = k, init, new_team, season_carry
        self.r: dict[int, float] = {}
        self.year = None

    def get(self, t):
        return self.r.get(t, self.new_team if self.r else self.init)

    def new_year(self, year):
        if self.year is not None and year != self.year:
            for t in self.r:
                self.r[t] = self.init + self.carry * (self.r[t] - self.init)
        self.year = year

    def predict(self, a, b):
        return float(sigmoid(LN10_400 * (self.get(a) - self.get(b))))

    def update(self, a, b, maps_a, maps_b):
        e = self.predict(a, b)
        s = 1.0 if maps_a > maps_b else 0.0
        mov = 1.0 + 0.5 * (abs(maps_a - maps_b) - 1)
        d = self.k * mov * (s - e)
        ra, rb = self.get(a), self.get(b)
        self.r[a], self.r[b] = ra + d, rb - d


class MapElo:
    """Map-level team Elo plus a shrunken per-(team, map) offset.

    * Margin of victory: K scales with log round differential (FiveThirtyEight style),
      damped when the favourite wins big to avoid autocorrelation.
    * Map offsets learn slower and decay toward 0 so thin samples do not dominate.
    * Season carry-over and a roster-turnover regression toward the mean.
    """

    def __init__(self, k=24.0, k_map=14.0, map_decay=0.985, init=1500.0, new_team=1440.0,
                 season_carry=0.8, roster_regress=0.35, k_region=10.0):
        self.k, self.k_map, self.map_decay, self.k_region = k, k_map, map_decay, k_region
        # Region offsets: regions mostly play themselves, so without an explicit term a
        # region's average level is only pinned down by the odd international. Offsets
        # are learned from cross-region maps only and cancel within a region.
        self.region_of: dict[int, str] = {}
        self.reg: dict[str, float] = defaultdict(float)
        self.reg_history: list[tuple] = []
        self.prior = None  # optional fn(team) -> starting rating
        self.init, self.new_team, self.carry, self.roster_regress = init, new_team, season_carry, roster_regress
        self.r: dict[int, float] = {}
        self.o: dict[tuple[int, str], float] = defaultdict(float)
        self.year = None
        self.history: list[tuple] = []

    def _new(self, t):
        if self.prior:
            v = self.prior(t)
            if v is not None:
                return v
        return self.new_team if self.r else self.init

    def get(self, t):
        r = self.r.get(t)
        return self._new(t) if r is None else r

    def new_year(self, year):
        if self.year is not None and year != self.year:
            for t in self.r:
                self.r[t] = self.init + self.carry * (self.r[t] - self.init)
            for key in self.o:
                self.o[key] *= 0.7
        self.year = year

    def roster_change(self, t, frac_new):
        if frac_new > 0 and t in self.r:
            keep = 1 - self.roster_regress * frac_new
            self.r[t] = self.init + keep * (self.r[t] - self.init)

    def region_diff(self, a, b):
        ra, rb = self.region_of.get(a), self.region_of.get(b)
        if not ra or not rb or ra == rb:
            return 0.0
        return self.reg[ra] - self.reg[rb]

    def diff(self, a, b, mp=None):
        d = self.get(a) - self.get(b) + self.region_diff(a, b)
        if mp:
            d += self.o[(a, mp)] - self.o[(b, mp)]
        return d

    def predict(self, a, b, mp=None):
        return float(sigmoid(LN10_400 * self.diff(a, b, mp)))

    def update(self, a, b, mp, rounds_a, rounds_b, date=None):
        d = self.diff(a, b, mp)
        e = float(sigmoid(LN10_400 * d))
        s = 1.0 if rounds_a > rounds_b else 0.0
        rd = abs(rounds_a - rounds_b)
        mov = math.log1p(rd) / math.log1p(6)
        winner_d = d if s == 1 else -d
        mov *= 2.2 / (max(winner_d, 0) * 0.001 + 2.2)
        delta = self.k * mov * (s - e)
        ra, rb = self.get(a), self.get(b)
        self.r[a], self.r[b] = ra + delta, rb - delta
        for t, sign in ((a, 1), (b, -1)):
            self.o[(t, mp)] = self.o[(t, mp)] * self.map_decay + sign * self.k_map * (s - e)
        ra, rb = self.region_of.get(a), self.region_of.get(b)
        if ra and rb and ra != rb:
            self.reg[ra] += self.k_region * (s - e)
            self.reg[rb] -= self.k_region * (s - e)
            if date is not None:
                self.reg_history.append((date, dict(self.reg)))
        if date is not None:
            self.history.append((date, a, self.r[a]))
            self.history.append((date, b, self.r[b]))


class PlayerElo:
    """Roster-aware Elo: every player carries a rating; a lineup's strength is the mean.

    Result credit is shared by the lineup, plus a small zero-sum individual term from
    Rating 2.0 relative to the lobby average, so standout players rise faster than
    teammates. Transfers carry a player's rating to their new team automatically.
    """

    def __init__(self, k=20.0, k_ind=18.0, init=1500.0, rookie=1450.0, season_carry=0.85):
        self.k, self.k_ind, self.init, self.rookie, self.carry = k, k_ind, init, rookie, season_carry
        self.r: dict[int, float] = {}
        self.n: dict[int, int] = defaultdict(int)
        self.year = None
        self.prior = None  # optional fn(player) -> starting rating (e.g. from tier-2 results)

    def get(self, p):
        r = self.r.get(p)
        if r is None:
            return self.prior(p) if self.prior else self.rookie
        return r

    def new_year(self, year):
        if self.year is not None and year != self.year:
            for p in self.r:
                self.r[p] = self.init + self.carry * (self.r[p] - self.init)
        self.year = year

    def team(self, lineup):
        if not lineup:
            return self.rookie
        return float(np.mean([self.get(p) for p in lineup]))

    def predict(self, la, lb):
        return float(sigmoid(LN10_400 * (self.team(la) - self.team(lb))))

    def update(self, la, lb, rounds_a, rounds_b, perf: dict[int, float] | None = None):
        e = self.predict(la, lb)
        s = 1.0 if rounds_a > rounds_b else 0.0
        mov = math.log1p(abs(rounds_a - rounds_b)) / math.log1p(6)
        delta = self.k * mov * (s - e)
        lobby = np.mean([v for v in (perf or {}).values() if v is not None]) if perf else None
        for lineup, sign in ((la, 1), (lb, -1)):
            for p in lineup:
                ind = 0.0
                if perf and lobby is not None and perf.get(p) is not None:
                    ind = self.k_ind * (perf[p] - lobby)
                self.r[p] = self.get(p) + sign * delta + ind
                self.n[p] += 1


class PlayerForm:
    """Shrunken exponentially weighted averages of per-map player stats."""

    STATS = ("rating", "acs", "kast", "adr", "fkd")
    PRIOR = {"rating": 1.0, "acs": 200.0, "kast": 71.0, "adr": 132.0, "fkd": 0.0}

    def __init__(self, halflife_maps=12.0, prior_maps=6.0):
        self.decay = 0.5 ** (1 / halflife_maps)
        self.prior_w = prior_maps
        self.sum: dict[tuple[int, str], float] = defaultdict(float)
        self.w: dict[int, float] = defaultdict(float)

    def get(self, p, stat):
        w = self.w.get(p, 0.0)
        return (self.sum[(p, stat)] + self.prior_w * self.PRIOR[stat]) / (w + self.prior_w)

    def team(self, lineup, stat):
        if not lineup:
            return self.PRIOR[stat]
        return float(np.mean([self.get(p, stat) for p in lineup]))

    def update(self, row):
        p = row["player_id"]
        vals = {"rating": row["rating"], "acs": row["acs"], "kast": row["kast"], "adr": row["adr"],
                "fkd": (row["fk"] or 0) - (row["fd"] or 0) if row["fk"] is not None else None}
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals.values()):
            return
        for s in self.STATS:
            self.sum[(p, s)] = self.sum[(p, s)] * self.decay + vals[s]
        self.w[p] = self.w[p] * self.decay + 1.0


# --------------------------------------------------------------------------- round model

class RoundModel:
    """Time-decayed Bradley-Terry model of individual rounds.

    logit P(attacker wins) = mu + side_m + atk[A] + s*atk[A,m] - def[B] - s*def[B,m]

    Fitted with L2 (ridge) logistic regression; team-map columns are scaled by s<1
    so their effective penalty is 1/s^2 times larger (partial pooling toward the
    team's overall attack/defence). Converting to a map win probability uses the
    exact regulation + overtime DP above, so this model 'knows' that a small
    per-round edge compounds into a large map edge.
    """

    ECON_COLS = ("ldiff", "atk_eco", "def_eco", "atk_semi", "def_semi", "pistol")

    def __init__(self, halflife_days=150.0, C=0.5, map_scale=0.5, tau=1.0, econ=False):
        self.halflife, self.C, self.s, self.tau, self.econ = halflife_days, C, map_scale, tau, econ
        self.coef = None

    def prepare(self, rounds: pd.DataFrame):
        self.rounds = rounds
        self.teams = sorted(set(rounds["attacker"]) | set(rounds["defender"]))
        self.maps = sorted(rounds["map"].unique())
        self.ti = {t: i for i, t in enumerate(self.teams)}
        self.mi = {m: i for i, m in enumerate(self.maps)}
        nT, nM = len(self.teams), len(self.maps)
        self.off = {"atk": 0, "def": nT, "atkm": 2 * nT, "defm": 2 * nT + nT * nM, "side": 2 * nT + 2 * nT * nM}
        self.off["econ"] = self.off["side"] + nM
        self.ncol = self.off["econ"] + (len(self.ECON_COLS) if self.econ else 0)
        a = rounds["attacker"].map(self.ti).to_numpy()
        d = rounds["defender"].map(self.ti).to_numpy()
        m = rounds["map"].map(self.mi).to_numpy()
        n = len(rounds)
        rows = np.repeat(np.arange(n), 5)
        cols = np.stack([self.off["atk"] + a, self.off["def"] + d, self.off["atkm"] + a * nM + m,
                         self.off["defm"] + d * nM + m, self.off["side"] + m], axis=1).ravel()
        vals = np.tile([1.0, -1.0, self.s, -self.s, 1.0], n)
        X = sparse.csr_matrix((vals, (rows, cols)), shape=(n, self.ncol))
        if self.econ:
            # Shared economy covariates (scaled up so the ridge barely penalises them). The
            # baseline is a full buy vs full buy at equal loadout, so team coefficients
            # measure skill at equal economy, and predictions are made at that baseline.
            has = rounds["has_econ"].fillna(False).to_numpy()
            ld = ((rounds["atk_loadout"] - rounds["def_loadout"]) / 10000).fillna(0).to_numpy()
            ab, db = rounds["atk_buy"].fillna(""), rounds["def_buy"].fillna("")
            pistol = rounds["round_num"].isin([1, 13]).to_numpy()
            E = np.stack([ld, (ab == "eco") & ~pistol, (db == "eco") & ~pistol,
                          ab.isin(["semi-eco", "semi-buy"]), db.isin(["semi-eco", "semi-buy"]), pistol], axis=1).astype(float)
            E[~has] = 0.0
            X = sparse.hstack([X[:, : self.off["econ"]], sparse.csr_matrix(E * 5.0)]).tocsr()
        self.X = X
        self.y = rounds["atk_win"].to_numpy()
        self.t = rounds["date"].to_numpy()

    def fit(self, asof):
        mask = self.t < np.datetime64(asof)
        if mask.sum() < 2000:
            self.coef = None
            return
        age = (np.datetime64(asof) - self.t[mask]) / np.timedelta64(1, "D")
        w = 0.5 ** (age / self.halflife)
        clf = LogisticRegression(C=self.C, max_iter=400, solver="lbfgs")
        clf.fit(self.X[mask], self.y[mask], sample_weight=w / w.mean())
        self.coef = clf.coef_.ravel()
        self.mu = float(clf.intercept_[0])

    def _atk_logit(self, a, d, mp):
        if self.coef is None:
            return 0.0
        c, nM = self.coef, len(self.maps)
        z = self.mu
        mi = self.mi.get(mp)
        if mi is not None:
            z += c[self.off["side"] + mi]
        ai, di = self.ti.get(a), self.ti.get(d)
        team = 0.0
        if ai is not None:
            team += c[self.off["atk"] + ai] + (self.s * c[self.off["atkm"] + ai * nM + mi] if mi is not None else 0)
        if di is not None:
            team -= c[self.off["def"] + di] + (self.s * c[self.off["defm"] + di * nM + mi] if mi is not None else 0)
        # tau < 1 shrinks team edges: rounds are not independent (economy, momentum), so an iid
        # round model overstates how reliably a per-round edge converts into map wins.
        return z + self.tau * team

    def round_probs(self, a, b, mp):
        """(P(A wins round on attack), P(A wins round on defence))."""
        pa = float(sigmoid(self._atk_logit(a, b, mp)))
        pd_ = 1.0 - float(sigmoid(self._atk_logit(b, a, mp)))
        return pa, pd_

    def predict(self, a, b, mp):
        pa, pd_ = self.round_probs(a, b, mp)
        return map_win_prob(pa, pd_)

    def team_params(self, t):
        """Per-team attack/defence (overall and by map) in logit units, for export."""
        if self.coef is None or t not in self.ti:
            return None
        c, nM, i = self.coef, len(self.maps), self.ti[t]
        return {
            "atk": float(c[self.off["atk"] + i]),
            "def": float(c[self.off["def"] + i]),
            "atk_map": {m: float(self.s * c[self.off["atkm"] + i * nM + j]) for m, j in self.mi.items()},
            "def_map": {m: float(self.s * c[self.off["defm"] + i * nM + j]) for m, j in self.mi.items()},
        }

    def global_params(self):
        g = {"mu": self.mu, "side": {m: float(self.coef[self.off["side"] + j]) for m, j in self.mi.items()}}
        if self.econ and self.coef is not None:
            g["econ"] = {k: float(5.0 * self.coef[self.off["econ"] + i]) for i, k in enumerate(self.ECON_COLS)}
        return g
