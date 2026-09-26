import pandas as pd
import pytest

from pipeline.tournament import simulate


def event(results=None):
    rows, mid = [], 1
    teams = iter(range(1, 17))
    for g in "ABCD":
        for _ in range(2):
            a, b = next(teams), next(teams)
            rows.append(dict(match_id=mid, series=f"Group Stage: Opening ({g})", team1_id=a, team2_id=b,
                             status="upcoming", score1=None, score2=None))
            mid += 1
        for k in ("Winner's", "Elimination", "Decider"):
            rows.append(dict(match_id=mid, series=f"Group Stage: {k} ({g})", team1_id=None, team2_id=None,
                             status="upcoming", score1=None, score2=None))
            mid += 1
    df = pd.DataFrame(rows)
    for mid, s1, s2 in results or []:
        df.loc[df.match_id == mid, ["status", "score1", "score2"]] = ["completed", s1, s2]
    return df


def test_probabilities_are_consistent():
    out = simulate(event(), lambda a, b, bo: 0.5 + (b - a) * 0.02, n_sims=4000)
    tot = lambda k: sum(r[k] for r in out)
    assert tot("champion") == pytest.approx(1, abs=1e-9)
    assert tot("final") == pytest.approx(2, abs=1e-9)
    assert tot("top4") == pytest.approx(4, abs=1e-9)
    assert tot("playoffs") == pytest.approx(8, abs=1e-9)
    for r in out:
        assert r["champion"] <= r["final"] <= r["top4"] <= r["top6"] <= r["playoffs"]
    # team 1 is the strongest under this p_series
    assert out[0]["team_id"] == 1


def test_completed_results_are_locked_in():
    # team 2 beat team 1 in group A's opening: team 1 can no longer win the group
    out = {r["team_id"]: r for r in simulate(event([(1, 0, 2)]), lambda a, b, bo: 0.9 if a < b else 0.1, n_sims=2000)}
    assert out[1]["group_1st"] == 0


def test_bracket_and_placements_are_consistent():
    teams, bracket, places = simulate(event(), lambda a, b, bo: 0.5 + (b - a) * 0.02, n_sims=3000, detail=True)
    slots = {b["slot"]: b for b in bracket}
    assert len(bracket) == 34 and {"A-o1", "uqf1", "lf", "gf"} <= set(slots)
    for b in bracket:  # each slot is played by exactly two teams and won by one, in every simulation
        assert sum(t["plays"] for t in b["teams"]) == pytest.approx(2, abs=1e-6) or len(b["teams"]) == 4
        assert all(t["wins"] <= t["plays"] for t in b["teams"])
    for t, dist in places.items():
        assert sum(dist.values()) == pytest.approx(1, abs=1e-3)  # values are rounded to 4 decimals
    champ = {t["team_id"]: t["champion"] for t in teams}
    assert all(places[t]["1st"] == pytest.approx(champ[t], abs=1e-4) for t in champ)
