import pytest

from pipeline.models import map_win_prob, series_score_dist, series_win_prob


def test_even_rounds_even_map():
    assert map_win_prob(0.5, 0.5) == pytest.approx(0.5, abs=1e-9)


@pytest.mark.parametrize("pa,pd", [(0.55, 0.48), (0.4, 0.62), (0.51, 0.51), (0.7, 0.3)])
def test_symmetry(pa, pd):
    # B attacks when A defends: B's attack win prob is 1-pd, its defence win prob is 1-pa
    assert map_win_prob(pa, pd) + map_win_prob(1 - pd, 1 - pa) == pytest.approx(1.0, abs=1e-3)


def test_monotone_and_compounding():
    ps = [map_win_prob(p, p) for p in (0.45, 0.5, 0.55, 0.6)]
    assert ps == sorted(ps)
    assert map_win_prob(0.55, 0.55) == pytest.approx(0.70, abs=0.01)


def test_series():
    assert series_win_prob([0.6] * 3, 3) == pytest.approx(0.648)
    assert series_win_prob([0.5] * 5, 5) == pytest.approx(0.5)
    d = series_score_dist([0.6, 0.55, 0.7], 3)
    assert sum(d.values()) == pytest.approx(1.0)
    assert set(d) == {"2-0", "2-1", "1-2", "0-2"}
    assert d["2-0"] + d["2-1"] == pytest.approx(series_win_prob([0.6, 0.55, 0.7], 3))


def test_elo_uncertainty_boost():
    from pipeline.models import MapElo, PlayerElo
    flat, boost = MapElo(k=16, k_map=0), MapElo(k=16, k_map=0, k_new=1.0)
    for m in (flat, boost):
        m.update(1, 2, "Ascent", 13, 5)
    # disabled by default; enabled, a brand-new team moves twice as far on its first map
    assert boost.r[1] - boost.init == pytest.approx(2 * (flat.r[1] - flat.init))
    assert boost.k_of(1) < 2 * boost.k and boost.k_of(99) == pytest.approx(2 * boost.k)
    # a full roster change restores the uncertainty
    boost.n[1] = 100
    boost.roster_change(1, 1.0)
    assert boost.n[1] == 0
    p, q = PlayerElo(k=20, k_ind=0), PlayerElo(k=20, k_ind=0, k_new=1.0)
    for m in (p, q):
        m.update([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 13, 7)
    assert q.r[1] - q.rookie == pytest.approx(2 * (p.r[1] - p.rookie))


def test_stack_recency_weights():
    import numpy as np
    import pandas as pd
    from pipeline.engine import fit_meta
    rng = np.random.default_rng(0)
    n = 4000
    dates = pd.date_range("2024-01-01", periods=n, freq="4h")
    x = rng.normal(size=n)
    # the signal flips sign halfway through: an equally weighted fit averages it away, a recency-weighted one follows it
    beta = np.where(np.arange(n) < n // 2, -1.0, 1.0)
    df = pd.DataFrame({"date": dates, "x": x, "win1": (rng.random(n) < 1 / (1 + np.exp(-beta * x))).astype(int)})
    flat = fit_meta(df, ["x"]).coef_[0][0]
    recent = fit_meta(df, ["x"], halflife=60).coef_[0][0]
    assert abs(flat) < 0.3 and recent > 0.6
