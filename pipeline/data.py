"""Load the scraped SQLite database into tidy pandas frames."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

import pandas as pd

from .scrape import DB_PATH


@dataclass
class Data:
    matches: pd.DataFrame   # one row per completed series
    maps: pd.DataFrame      # one row per completed map, with match context
    rounds: pd.DataFrame    # one row per round, attacker/defender resolved
    players: pd.DataFrame   # one row per player per map
    upcoming: pd.DataFrame  # scheduled series with both teams known
    teams: pd.DataFrame
    events: pd.DataFrame
    t2_maps: pd.DataFrame | None = None      # tier-2 (Challengers/Ascension) maps, for rating priors only
    t2_players: pd.DataFrame | None = None


def load(db_path=DB_PATH) -> Data:
    con = sqlite3.connect(db_path)
    q = lambda sql: pd.read_sql_query(sql, con)
    events = q("SELECT * FROM events")
    teams = q("SELECT * FROM teams").set_index("team_id")
    ecols = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    country = "e.country" if "country" in ecols else "NULL"
    matches = q(f"""SELECT m.*, e.name AS event, e.tier, e.region AS event_region, e.year, {country} AS event_country
                   FROM matches m JOIN events e USING(event_id)""")
    matches["date"] = pd.to_datetime(matches["date"])
    t2 = matches[matches["tier"] == "t2"]
    matches = matches[matches["tier"] != "t2"]
    both = matches["team1_id"].notna() & matches["team2_id"].notna()
    upcoming = matches[both & (matches["status"] != "completed")].sort_values("date").reset_index(drop=True)
    matches = matches[both & (matches["status"] == "completed") & matches["score1"].notna()]
    matches = matches[(matches["score1"] + matches["score2"]) > 0]
    matches = matches.sort_values(["date", "match_id"]).reset_index(drop=True)
    for c in ("team1_id", "team2_id"):
        matches[c] = matches[c].astype(int)
        upcoming[c] = upcoming[c].astype(int)
    matches[["score1", "score2"]] = matches[["score1", "score2"]].astype(int)
    # best-of can be missing on a few pages; infer it from the winning score (2 -> Bo3, 3 -> Bo5, 1 -> Bo1)
    inferred = (matches[["score1", "score2"]].max(axis=1) * 2 - 1).clip(1, 5)
    matches["best_of"] = matches["best_of"].fillna(inferred).astype(int)
    upcoming["best_of"] = upcoming["best_of"].fillna(3).astype(int)
    matches["international"] = matches["tier"].isin(["masters", "champions"]).astype(int)

    maps = q("SELECT * FROM maps")
    maps = maps.merge(matches[["match_id", "date", "team1_id", "team2_id", "event_id", "tier", "best_of",
                               "international", "veto", "team1_tag", "team2_tag"]], on="match_id")
    maps = maps[(maps["score1"] != maps["score2"])].sort_values(["date", "match_id", "map_order"]).reset_index(drop=True)
    maps["win1"] = (maps["score1"] > maps["score2"]).astype(int)

    rounds = q("SELECT * FROM rounds")
    rounds = rounds.merge(maps[["game_id", "match_id", "date", "map", "team1_id", "team2_id"]], on="game_id")
    rounds = rounds[rounds["winner_side"].isin(["atk", "def"])].copy()
    atk_is_1 = ((rounds["winner"] == 1) & (rounds["winner_side"] == "atk")) | \
               ((rounds["winner"] == 2) & (rounds["winner_side"] == "def"))
    rounds["attacker"] = rounds["team1_id"].where(atk_is_1, rounds["team2_id"])
    rounds["defender"] = rounds["team2_id"].where(atk_is_1, rounds["team1_id"])
    rounds["atk_win"] = (rounds["winner_side"] == "atk").astype(int)
    # economy (from the match economy tab), oriented attacker vs defender
    try:
        econ = q("SELECT * FROM econ_rounds")
    except Exception:
        econ = pd.DataFrame(columns=["game_id", "round_num", "t1_loadout", "t2_loadout", "t1_buy", "t2_buy"])
    rounds = rounds.merge(econ[["game_id", "round_num", "t1_loadout", "t2_loadout", "t1_buy", "t2_buy"]],
                          on=["game_id", "round_num"], how="left")
    atk1 = rounds["attacker"] == rounds["team1_id"]
    rounds["atk_loadout"] = rounds["t1_loadout"].where(atk1, rounds["t2_loadout"])
    rounds["def_loadout"] = rounds["t2_loadout"].where(atk1, rounds["t1_loadout"])
    rounds["atk_buy"] = rounds["t1_buy"].where(atk1, rounds["t2_buy"])
    rounds["def_buy"] = rounds["t2_buy"].where(atk1, rounds["t1_buy"])
    rounds["has_econ"] = rounds["atk_loadout"].notna() & rounds["def_loadout"].notna()
    rounds = rounds.drop(columns=["t1_loadout", "t2_loadout", "t1_buy", "t2_buy"])
    rounds = rounds.sort_values(["date", "game_id", "round_num"]).reset_index(drop=True)

    players = q("SELECT * FROM player_maps")
    players_all = players
    players = players.merge(maps[["game_id", "date", "team1_id", "team2_id", "map", "win1"]], on="game_id")
    players["team_id"] = players["team1_id"].where(players["team"] == 1, players["team2_id"])
    players["won"] = (players["win1"] == (players["team"] == 1)).astype(int)
    players = players.sort_values(["date", "game_id"]).reset_index(drop=True)
    t2 = t2[t2["status"].eq("completed") & t2["team1_id"].notna() & t2["team2_id"].notna()]
    t2_maps = q("SELECT * FROM maps").merge(t2[["match_id", "date", "team1_id", "team2_id"]], on="match_id")
    t2_maps = t2_maps[t2_maps["score1"] != t2_maps["score2"]].sort_values(["date", "match_id", "map_order"]).reset_index(drop=True)
    t2_maps["win1"] = (t2_maps["score1"] > t2_maps["score2"]).astype(int)
    t2_maps[["team1_id", "team2_id"]] = t2_maps[["team1_id", "team2_id"]].astype(int)
    t2_players = players_all[players_all["game_id"].isin(t2_maps["game_id"])]
    con.close()
    return Data(matches, maps, rounds, players, upcoming, teams, events, t2_maps, t2_players)


VETO_RE = re.compile(r"^(.+?) (ban|pick) (.+)$")


def parse_veto(veto: str, tag1: str | None, tag2: str | None) -> list[tuple[int, str, str]]:
    """'100T ban Abyss; LOUD pick Sunset; Haven remains' -> [(1,'ban','Abyss'), (2,'pick','Sunset'), (0,'remains','Haven')].

    Returns [] when the string cannot be attributed to the two teams.
    """
    out = []
    if not veto:
        return out
    for part in [p.strip() for p in veto.split(";") if p.strip()]:
        if part.endswith(" remains"):
            out.append((0, "remains", part[: -len(" remains")].strip()))
            continue
        mm = VETO_RE.match(part)
        if not mm:
            return []
        who, act, mp = mm.groups()
        if tag1 and who.lower() == tag1.lower():
            out.append((1, act, mp))
        elif tag2 and who.lower() == tag2.lower():
            out.append((2, act, mp))
        else:
            return []
    return out
