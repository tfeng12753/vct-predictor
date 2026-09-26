"""Parser tests against real vlr.gg pages saved as fixtures (2023 and 2026 layouts)."""
from pipeline.data import parse_veto
from pipeline.scrape import parse_economy, parse_match


def check_match(m):
    assert m["status"] == "completed"
    w1 = sum(mp["score1"] > mp["score2"] for mp in m["maps"])
    w2 = sum(mp["score2"] > mp["score1"] for mp in m["maps"])
    assert (m["score1"], m["score2"]) == (w1, w2)
    for mp in m["maps"]:
        rounds = [r for r in m["rounds"] if r["game_id"] == mp["game_id"]]
        # every round is parsed and the side split adds up to the map score
        assert len(rounds) == mp["score1"] + mp["score2"]
        assert sum(r["winner"] == 1 for r in rounds) == mp["score1"]
        side1 = mp["t1_atk"] + mp["t1_def"] + (mp["t1_ot"] or 0)
        assert side1 == mp["score1"]
        players = [p for p in m["players"] if p["game_id"] == mp["game_id"]]
        assert len(players) == 10
        assert {p["team"] for p in players} == {1, 2}
        assert all(0 < p["rating"] < 4 for p in players)


def test_parse_2026_grand_final(fixture_html):
    m = parse_match(734308, fixture_html("match-2026-gf"))
    assert (m["team1"], m["team2"]) == ("100 Thieves", "LOUD")
    assert (m["score1"], m["score2"]) == (3, 2)
    assert m["best_of"] == 5 and m["patch"] == "13.04"
    assert [mp["map"] for mp in m["maps"]] == ["Split", "Sunset", "Ascent", "Summit", "Haven"]
    assert [mp["picked_by"] for mp in m["maps"]] == [1, 2, 1, 2, 0]
    check_match(m)


def test_parse_2023_grand_final(fixture_html):
    m = parse_match(189055, fixture_html("match-2023-gf"))
    assert (m["team1"], m["team2"]) == ("LOUD", "NRG")
    check_match(m)


def test_veto_attribution(fixture_html):
    m = parse_match(734308, fixture_html("match-2026-gf"))
    acts = parse_veto(m["veto"], *m["tags"])
    assert acts[0] == (1, "ban", "Abyss")
    assert acts[-1] == (0, "remains", "Haven")
    assert [a for a in acts if a[1] == "pick"] == [(1, "pick", "Split"), (2, "pick", "Sunset"), (1, "pick", "Ascent"), (2, "pick", "Summit")]
    assert parse_veto("XYZ ban Lotus", "100T", "LOUD") == []


def test_economy(fixture_html):
    rows = parse_economy(fixture_html("econ-2026-gf"))
    assert len(rows) == 110  # 20 + 24 + 26 + 14 + 26 rounds
    assert all(r["t1_loadout"] and r["t2_loadout"] for r in rows)
    pistols = [r for r in rows if r["round_num"] in (1, 13)]
    assert all(r["t1_loadout"] < 6000 and r["t2_loadout"] < 6000 for r in pistols)
    assert {r["t1_buy"] for r in rows} <= {"eco", "semi-eco", "semi-buy", "full"}
