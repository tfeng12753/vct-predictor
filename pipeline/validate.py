"""Data-health checks run on every export. A vlr.gg layout change usually shows up
here first (maps without rounds, missing player rows) rather than as a crash."""
from __future__ import annotations

import sqlite3

import pandas as pd

from .data import parse_veto
from .scrape import DB_PATH


def health(db_path=DB_PATH) -> dict:
    con = sqlite3.connect(db_path)
    q = lambda sql: pd.read_sql_query(sql, con)
    matches = q("SELECT m.* FROM matches m JOIN events e USING(event_id) WHERE m.status='completed' AND e.tier != 't2'")
    maps = q("SELECT * FROM maps")
    maps = maps[maps["match_id"].isin(matches["match_id"])]
    rounds = q("SELECT game_id, COUNT(*) AS n FROM rounds GROUP BY game_id")
    players = q("SELECT game_id, COUNT(*) AS n FROM player_maps GROUP BY game_id")
    try:
        econ = q("SELECT game_id, COUNT(*) AS n FROM econ_rounds GROUP BY game_id")
    except Exception:
        econ = pd.DataFrame(columns=["game_id", "n"])
    con.close()

    m = maps.merge(rounds, on="game_id", how="left").merge(players, on="game_id", how="left", suffixes=("_r", "_p"))
    m = m.merge(econ.rename(columns={"n": "n_e"}), on="game_id", how="left")
    total = m["score1"] + m["score2"]
    no_maps = set(matches["match_id"]) - set(maps["match_id"])
    wins = maps.assign(w1=maps.score1 > maps.score2, w2=maps.score2 > maps.score1).groupby("match_id")[["w1", "w2"]].sum()
    mm = matches.set_index("match_id").join(wins, how="inner")
    bad_series = int(((mm["score1"] != mm["w1"]) | (mm["score2"] != mm["w2"])).sum())
    veto_ok = matches.apply(lambda r: bool(parse_veto(r["veto"], r["team1_tag"], r["team2_tag"])), axis=1)

    checks = [
        ("Completed series with map data", 1 - len(no_maps) / max(len(matches), 1), 0.99),
        ("Series score matches map results", 1 - bad_series / max(len(mm), 1), 0.99),
        ("Maps with every round parsed", float((m["n_r"] == total).mean()), 0.97),
        ("Maps with all 10 player stat lines", float((m["n_p"] == 10).mean()), 0.97),
        # vlr.gg has no economy tab for roughly a fifth of series, so full coverage is impossible
        ("Rounds with economy data", float(m["n_e"].fillna(0).sum() / max(total.sum(), 1)), 0.75),
        ("Series with a readable map veto", float(veto_ok.mean()), 0.85),
    ]
    rows = [{"check": c, "value": round(v, 4), "threshold": t, "ok": bool(v >= t)} for c, v, t in checks]
    return {"checks": rows, "ok": all(r["ok"] for r in rows),
            "counts": {"series": int(len(matches)), "maps": int(len(maps)), "rounds": int(total.sum())}}


if __name__ == "__main__":
    import json
    print(json.dumps(health(), indent=1))
