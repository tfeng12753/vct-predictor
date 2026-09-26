"""Additional online signals. Each tracker exposes a pre-match value per team (or
lineup) and an update() called after the series, mirroring the rating models."""
from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations

import numpy as np
import pandas as pd


class PistolTracker:
    """Decayed pistol-round (rounds 1 and 13) win rate, shrunk toward 50%."""

    def __init__(self, decay=0.97, prior=8.0):
        self.decay, self.prior = decay, prior
        self.w = defaultdict(float)
        self.n = defaultdict(float)

    def rate(self, t):
        return (self.w[t] + 0.5 * self.prior) / (self.n[t] + self.prior)

    def logit(self, t):
        p = self.rate(t)
        return math.log(p / (1 - p))

    def update(self, pistols: list[tuple[int, int]]):
        """pistols: [(winner_team, loser_team), ...] for one map."""
        for w, l in pistols:
            for t, won in ((w, 1.0), (l, 0.0)):
                self.w[t] = self.w[t] * self.decay + won
                self.n[t] = self.n[t] * self.decay + 1.0


class Momentum:
    """EWMA of (result - pre-match expectation): is a team beating its rating lately?"""

    def __init__(self, alpha=0.15):
        self.alpha = alpha
        self.v = defaultdict(float)

    def get(self, t):
        return self.v[t]

    def update(self, t, residual):
        self.v[t] = (1 - self.alpha) * self.v[t] + self.alpha * residual


class Schedule:
    """Rest days and recent workload."""

    def __init__(self):
        self.dates: dict[int, list] = defaultdict(list)   # one entry per map played

    def rest_days(self, t, now):
        d = self.dates.get(t)
        if not d:
            return 60.0
        return min(60.0, (now - d[-1]).total_seconds() / 86400)

    def load(self, t, now, days=14):
        d = self.dates.get(t, [])
        cut = now - pd.Timedelta(days=days)
        return sum(1 for x in d[-40:] if x >= cut)

    def update(self, t, date, n_maps):
        self.dates[t].extend([date] * n_maps)


class Chemistry:
    """Maps the current lineup has played together, averaged over the 10 player pairs."""

    def __init__(self):
        self.pair = defaultdict(float)

    def get(self, lineup):
        if not lineup or len(lineup) < 2:
            return 0.0
        pairs = list(combinations(sorted(lineup), 2))
        return float(np.mean([math.log1p(self.pair[p]) for p in pairs]))

    def update(self, lineup):
        for p in combinations(sorted(lineup), 2):
            self.pair[p] += 1.0


class Experience:
    """Decayed count of tier-one maps a team has played."""

    def __init__(self, decay=0.99):
        self.decay = decay
        self.n = defaultdict(float)

    def get(self, t):
        return math.log1p(self.n[t])

    def update(self, t):
        self.n[t] = self.n[t] * self.decay + 1.0


# --------------------------------------------------------------------------- agent & map signals

class MapComfort:
    """How much the current five have played a specific map (decayed per player)."""

    def __init__(self, decay=0.985):
        self.decay = decay
        self.n = defaultdict(float)          # (player, map) -> decayed maps

    def get(self, lineup, mp):
        if not lineup:
            return 0.0
        return float(np.mean([math.log1p(self.n[(p, mp)]) for p in lineup]))

    def update(self, lineup, mp, all_maps):
        for p in lineup:
            for m in all_maps:
                if (p, m) in self.n:
                    self.n[(p, m)] *= self.decay
            self.n[(p, mp)] += 1.0


class AgentPool:
    """Per-player decayed agent usage: pool depth and per-map, per-agent comfort."""

    def __init__(self, decay=0.97):
        self.decay = decay
        self.use = defaultdict(lambda: defaultdict(float))   # player -> agent -> weight

    def depth(self, p):
        """Effective number of agents (exp of entropy)."""
        u = self.use.get(p)
        if not u:
            return 1.0
        w = np.array(list(u.values()))
        w = w / w.sum()
        return float(math.exp(-(w * np.log(w + 1e-12)).sum()))

    def team_depth(self, lineup):
        return float(np.mean([self.depth(p) for p in lineup])) if lineup else 1.0

    def update(self, p, agent):
        u = self.use[p]
        for a in list(u):
            u[a] *= self.decay
        if agent:
            u[agent] += 1.0


class MetaTracker:
    """League agent pick rates per map (decayed) and each team's latest comp per map."""

    def __init__(self, decay=0.995):
        self.decay = decay
        self.league = defaultdict(lambda: defaultdict(float))  # map -> agent -> weight
        self.total = defaultdict(float)                         # map -> team-maps
        self.last_comp = {}                                     # (team, map) -> tuple(agents)

    def rate(self, mp, agent):
        return self.league[mp][agent] / self.total[mp] if self.total[mp] else 0.0

    def alignment(self, team, mp):
        """Mean league pick rate of the agents in the team's latest comp on this map (0.5 if unknown)."""
        comp = self.last_comp.get((team, mp))
        if not comp or not self.total[mp]:
            return 0.5
        return float(np.mean([self.rate(mp, a) for a in comp]))

    def update(self, team, mp, comp):
        if len(comp) != 5:
            return
        lg = self.league[mp]
        for a in list(lg):
            lg[a] *= self.decay
        self.total[mp] = self.total[mp] * self.decay + 1.0
        for a in comp:
            lg[a] += 1.0
        self.last_comp[(team, mp)] = tuple(sorted(comp))


class MapForm:
    """Player rating on a specific map relative to their overall level (shrunk)."""

    def __init__(self, prior=8.0):
        self.prior = prior
        self.sum = defaultdict(float)
        self.n = defaultdict(float)
        self.all_sum = defaultdict(float)
        self.all_n = defaultdict(float)

    def get(self, lineup, mp):
        if not lineup:
            return 0.0
        out = []
        for p in lineup:
            overall = self.all_sum[p] / self.all_n[p] if self.all_n[p] else 1.0
            on_map = (self.sum[(p, mp)] + self.prior * overall) / (self.n[(p, mp)] + self.prior)
            out.append(on_map - overall)
        return float(np.mean(out))

    def update(self, p, mp, rating):
        if rating is None or rating != rating:
            return
        self.sum[(p, mp)] += rating
        self.n[(p, mp)] += 1
        self.all_sum[p] += rating
        self.all_n[p] += 1


# --------------------------------------------------------------------------- context

COUNTRY_REGION = {
    **{c: "Americas" for c in ("us", "ca", "br", "cl", "mx", "ar", "co", "pe")},
    **{c: "EMEA" for c in ("eu", "es", "fr", "gb", "uk", "de", "tr", "it", "pl", "se", "dk", "pt", "nl", "sa", "ae", "ru")},
    **{c: "Pacific" for c in ("jp", "kr", "th", "sg", "id", "ph", "my", "vn", "in", "au", "tw", "hk")},
    "cn": "China",
}


class PatchClock:
    """Days since a patch (and its major version, i.e. the act) first appeared in pro play."""

    def __init__(self):
        self.first = {}

    def _age(self, key, date):
        if key not in self.first:
            self.first[key] = date
        return (date - self.first[key]).total_seconds() / 86400

    def age(self, patch, date):
        return self._age(str(patch), date) if patch else 60.0

    def act_age(self, patch, date):
        return self._age("major:" + str(patch).split(".")[0], date) if patch else 60.0
