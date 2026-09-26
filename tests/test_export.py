"""Sanity checks on the JSON the site reads (skipped until pipeline.export has run)."""
import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "site" / "data"
pytestmark = pytest.mark.skipif(not (DATA / "meta.json").exists(), reason="run pipeline.export first")


def load(name):
    # json.loads rejects nothing by default; parse_constant makes NaN/Infinity fail loudly
    def bad(c):
        raise ValueError(f"{name} contains {c}")
    return json.loads((DATA / name).read_text(), parse_constant=bad)


def test_json_is_strict_and_probabilities_valid():
    up = load("upcoming.json")
    for u in up:
        assert 0 <= u["p1"] <= 1
        assert sum(u["scores"].values()) == pytest.approx(1, abs=0.01)
        assert all(0 <= m["p_neutral"] <= 1 for m in u["maps"])
    t = load("tournament.json")
    if t["teams"]:
        assert sum(x["champion"] for x in t["teams"]) == pytest.approx(1, abs=0.001)
    teams = load("teams.json")
    assert len({x["id"] for x in teams}) == len(teams)
    assert all(0 < x["power"] < 1 for x in teams)
    load("backtest.json")
    load("matchup.json")
