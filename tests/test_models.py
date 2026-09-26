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
