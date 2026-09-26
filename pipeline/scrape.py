"""Scrape VCT tier-one match data from vlr.gg into SQLite.

Polite by design: one request per ~1.1s, a descriptive User-Agent, and an on-disk
HTML cache so completed matches are only ever downloaded once. robots.txt only
disallows /search/auto and /rr/, neither of which we touch.

Usage:
    python -m pipeline.scrape            # crawl all configured years
    python -m pipeline.scrape --refresh  # also re-fetch event lists / upcoming matches
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "html"
DB_PATH = ROOT / "data" / "vct.db"
BASE = "https://www.vlr.gg"
UA = "Mozilla/5.0 (compatible; vct-predictor/0.1; personal research project)"
YEARS = [2023, 2024, 2025, 2026]
DELAY = 1.1

_session = requests.Session()
_session.headers["User-Agent"] = UA
_last = 0.0
REQUESTS = 0  # network requests made this run (cache hits excluded)


def fetch(path: str, cache_name: str | None = None, force: bool = False) -> str:
    """GET a vlr.gg path, caching to disk when cache_name is given."""
    global _last, REQUESTS
    f = CACHE / cache_name if cache_name else None
    if f and f.exists() and not force:
        return f.read_text()
    wait = DELAY - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        try:
            r = _session.get(BASE + path, timeout=30)
            _last = time.time()
            REQUESTS += 1
            if r.status_code == 200:
                break
            if r.status_code == 404:
                raise FileNotFoundError(path)
        except requests.RequestException:
            pass
        time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"failed to fetch {path}")
    if f:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(r.text)
    return r.text


def txt(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.replace("%", "").replace("+", "").replace("–", "-").strip()
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY, year INTEGER, name TEXT, tier TEXT, region TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    match_id INTEGER PRIMARY KEY, event_id INTEGER, status TEXT, date TEXT,
    stage TEXT, series TEXT, best_of INTEGER, patch TEXT,
    team1_id INTEGER, team2_id INTEGER, team1 TEXT, team2 TEXT,
    team1_tag TEXT, team2_tag TEXT, score1 INTEGER, score2 INTEGER, veto TEXT
);
CREATE TABLE IF NOT EXISTS maps (
    game_id INTEGER PRIMARY KEY, match_id INTEGER, map_order INTEGER, map TEXT,
    picked_by INTEGER, duration TEXT,
    score1 INTEGER, score2 INTEGER,
    t1_atk INTEGER, t1_def INTEGER, t2_atk INTEGER, t2_def INTEGER, t1_ot INTEGER, t2_ot INTEGER
);
CREATE TABLE IF NOT EXISTS rounds (
    game_id INTEGER, round_num INTEGER, winner INTEGER, winner_side TEXT, win_type TEXT,
    PRIMARY KEY (game_id, round_num)
);
CREATE TABLE IF NOT EXISTS player_maps (
    game_id INTEGER, match_id INTEGER, team INTEGER, player_id INTEGER, player TEXT, agent TEXT,
    rating REAL, acs REAL, kills REAL, deaths REAL, assists REAL, kast REAL, adr REAL,
    hs REAL, fk REAL, fd REAL,
    rating_atk REAL, rating_def REAL, acs_atk REAL, acs_def REAL,
    PRIMARY KEY (game_id, player_id)
);
CREATE TABLE IF NOT EXISTS econ_rounds (
    game_id INTEGER, round_num INTEGER, t1_loadout REAL, t2_loadout REAL,
    t1_bank REAL, t2_bank REAL, t1_buy TEXT, t2_buy TEXT,
    PRIMARY KEY (game_id, round_num)
);
CREATE TABLE IF NOT EXISTS econ_done (match_id INTEGER PRIMARY KEY, n_rounds INTEGER);
CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY, name TEXT, tag TEXT, logo TEXT, region TEXT
);
"""

REGION_PAT = [("Americas", "Americas"), ("EMEA", "EMEA"), ("Pacific", "Pacific"), ("China", "China")]


def event_meta(name: str) -> tuple[str, str]:
    """(tier, region) from an event name. International events get region 'International'."""
    n = name.lower()
    if "champions 20" in n and "tour" not in n or "valorant champions" in n:
        return "champions", "International"
    if "masters" in n or "lock//in" in n:
        return "masters", "International"
    if "last chance" in n or "champions china qualifier" in n:
        return "lcq", next((r for k, r in REGION_PAT if k.lower() in n), "International")
    for k, r in REGION_PAT:
        if k.lower() in n:
            return ("kickoff" if "kickoff" in n else "league"), r
    return "other", "International"


# --------------------------------------------------------------------------- parsing

def parse_year_events(year: int, force: bool) -> list[tuple[int, str, str]]:
    html = fetch(f"/vct-{year}", f"vct-{year}.html", force=force)
    s = BeautifulSoup(html, "lxml")
    out = []
    for a in s.select("a.event-item"):
        eid = int(a["href"].split("/")[2])
        title = txt(a.select_one(".event-item-title"))
        status = txt(a.select_one(".event-item-desc-item-status")).lower() or "unknown"
        fl = a.select_one(".mod-location i.flag")
        country = next((c[4:] for c in (fl.get("class", []) if fl else []) if c.startswith("mod-")), None)
        out.append((eid, title, status, country))
    return out


def parse_event_matches(event_id: int, force: bool) -> list[dict]:
    html = fetch(f"/event/matches/{event_id}/?series_id=all", f"event-{event_id}.html", force=force)
    s = BeautifulSoup(html, "lxml")
    out = []
    for a in s.select("a.match-item"):
        mid = int(a["href"].split("/")[1])
        status = txt(a.select_one(".ml-status")).lower()
        names = [txt(x) for x in a.select(".match-item-vs-team-name")]
        out.append({"match_id": mid, "status": status, "tbd": any(n.upper() in ("TBD", "") for n in names[:2]) or len(names) < 2})
    return out


def _side_vals(cell) -> dict:
    """ovw-cell -> {'both': x, 't': y, 'ct': z}"""
    vals = {}
    if cell is None:
        return vals
    for sp in cell.select("span.side"):
        cls = sp.get("class", [])
        for k in ("both", "t", "ct"):
            if f"mod-{k}" in cls:
                vals[k] = num(sp.get_text(strip=True))
    return vals


def parse_match(match_id: int, html: str) -> dict:
    s = BeautifulSoup(html, "lxml")
    m: dict = {"match_id": match_id}
    sup = s.select_one(".match-header-super")
    ev = sup.select_one("a.match-header-event") if sup else None
    m["event_id"] = int(ev["href"].split("/")[2]) if ev else None
    m["series"] = txt(ev.select_one(".match-header-event-series")) if ev else ""
    ts = s.select_one(".match-header-date .moment-tz-convert[data-utc-ts]")
    m["date"] = ts["data-utc-ts"] if ts else None
    patch = re.search(r"Patch\s+([\d.]+)", txt(s.select_one(".match-header-date")))
    m["patch"] = patch.group(1) if patch else None

    links = s.select(".match-header-link")
    ids, names = [], []
    for ln in links[:2]:
        href = ln.get("href", "")
        mm = re.match(r"/team/(\d+)", href)
        ids.append(int(mm.group(1)) if mm else None)
        names.append(txt(ln.select_one(".wf-title-med")))
    while len(ids) < 2:
        ids.append(None)
        names.append("TBD")
    m["team1_id"], m["team2_id"] = ids
    m["team1"], m["team2"] = names
    logos = [ln.select_one("img") for ln in links[:2]]
    m["logos"] = [("https:" + i["src"]) if i and i.get("src", "").startswith("//") else None for i in logos]

    sc = s.select(".match-header-vs-score .js-spoiler span:not(.match-header-vs-score-colon)")
    scores = [num(x.get_text(strip=True)) for x in sc if x.get_text(strip=True).isdigit()]
    m["score1"], m["score2"] = (int(scores[0]), int(scores[1])) if len(scores) >= 2 else (None, None)
    notes = [txt(x) for x in s.select(".match-header-vs-note")]
    bo = next((re.search(r"Bo(\d)", n) for n in notes if re.search(r"Bo(\d)", n)), None)
    m["best_of"] = int(bo.group(1)) if bo else None
    status = " ".join(notes).lower()
    m["status"] = "completed" if "final" in status else ("live" if "live" in status else "upcoming")
    m["veto"] = txt(s.select_one(".match-header-note"))
    hs = [int(x) for x in re.findall(r"\d+", txt(s.select_one(".match-header-vs-score .js-spoiler")))]
    if len(hs) >= 2:
        m["score1"], m["score2"] = hs[0], hs[1]

    # team tags as used in player tables / veto text
    m["tags"] = [None, None]
    m["maps"], m["rounds"], m["players"] = [], [], []
    order = 0
    for g in s.select(".vm-stats-game"):
        gid = g.get("data-game-id")
        if not gid or gid == "all":
            continue
        hdr = g.select_one(".vm-stats-game-header")
        if hdr is None:
            continue
        mapname_el = hdr.select_one(".map span")
        if mapname_el is None:
            continue
        mapname = mapname_el.find(string=True, recursive=False)
        mapname = (mapname or "").strip()
        if not mapname or mapname.upper() == "TBD":
            continue
        pick = hdr.select_one(".picked")
        picked_by = 1 if pick and "mod-1" in pick.get("class", []) else (2 if pick and "mod-2" in pick.get("class", []) else 0)
        teams = hdr.select(".team")
        sc1 = num(txt(teams[0].select_one(".score")))
        sc2 = num(txt(teams[1].select_one(".score")))
        if sc1 is None or sc2 is None:
            continue

        def sides(t):
            return (num(txt(t.select_one(".mod-t"))), num(txt(t.select_one(".mod-ct"))), num(txt(t.select_one(".mod-ot"))))

        a1, d1, o1 = sides(teams[0])
        a2, d2, o2 = sides(teams[1])
        order += 1
        game_id = int(gid)
        m["maps"].append({
            "game_id": game_id, "match_id": match_id, "map_order": order, "map": mapname,
            "picked_by": picked_by, "duration": txt(hdr.select_one(".map-duration")),
            "score1": int(sc1), "score2": int(sc2),
            "t1_atk": a1, "t1_def": d1, "t2_atk": a2, "t2_def": d2, "t1_ot": o1, "t2_ot": o2,
        })

        for col in g.select(".vlr-rounds-row-col[title]"):
            rn = num(txt(col.select_one(".rnd-num")))
            sqs = col.select(".rnd-sq")
            if rn is None or len(sqs) < 2:
                continue
            winner, side, wtype = None, None, None
            for i, sq in enumerate(sqs[:2]):
                cls = sq.get("class", [])
                if "mod-win" in cls:
                    winner = i + 1
                    side = "atk" if "mod-t" in cls else ("def" if "mod-ct" in cls else None)
                    img = sq.select_one("img")
                    if img and img.get("src"):
                        wtype = Path(img["src"]).stem
            if winner:
                m["rounds"].append({"game_id": game_id, "round_num": int(rn), "winner": winner,
                                    "winner_side": side, "win_type": wtype})

        for ti, tbl in enumerate(g.select(".ovw-table")[:2]):
            for row in tbl.select(".ovw-row:not(.mod-head)"):
                a = row.select_one(".ovw-player a[href^='/player/']")
                if not a:
                    continue
                pid = int(a["href"].split("/")[2])
                tag = txt(row.select_one(".ovw-player-tag"))
                if tag and not m["tags"][ti]:
                    m["tags"][ti] = tag
                ag = row.select_one(".ovw-agents img")
                cell = {c.get("data-col"): c for c in row.select(".ovw-cell[data-col]")}
                kda = {k.get("data-col"): _side_vals(k) for k in row.select(".ovw-kda-stat[data-col]")}
                r = _side_vals(cell.get("rating2"))
                acs = _side_vals(cell.get("acs"))
                m["players"].append({
                    "game_id": game_id, "match_id": match_id, "team": ti + 1, "player_id": pid,
                    "player": txt(row.select_one(".ovw-player-name")),
                    "agent": ag.get("title") if ag else None,
                    "rating": r.get("both"), "acs": acs.get("both"),
                    "kills": kda.get("kills", {}).get("both"), "deaths": kda.get("deaths", {}).get("both"),
                    "assists": kda.get("assists", {}).get("both"),
                    "kast": _side_vals(cell.get("kast")).get("both"), "adr": _side_vals(cell.get("adr")).get("both"),
                    "hs": _side_vals(cell.get("hsp")).get("both"), "fk": _side_vals(cell.get("fb")).get("both"),
                    "fd": _side_vals(cell.get("fd")).get("both"),
                    "rating_atk": r.get("t"), "rating_def": r.get("ct"),
                    "acs_atk": acs.get("t"), "acs_def": acs.get("ct"),
                })
    if m["status"] == "completed" and m["maps"]:
        w1 = sum(mp["score1"] > mp["score2"] for mp in m["maps"])
        w2 = sum(mp["score2"] > mp["score1"] for mp in m["maps"])
        if m["score1"] is None or (m["score1"], m["score2"]) != (w1, w2):
            m["score1"], m["score2"] = w1, w2
    return m


def _bank(t: str) -> float | None:
    t = t.strip().lower()
    try:
        return float(t[:-1]) * 1000 if t.endswith("k") else float(t)
    except ValueError:
        return None


BUY = {"": "eco", "$": "semi-eco", "$$": "semi-buy", "$$$": "full"}


def parse_economy(html: str) -> list[dict]:
    """Per-round loadout value, remaining bank and buy class for both teams, per map."""
    s = BeautifulSoup(html, "lxml")
    out = []
    for g in s.select(".vm-stats-game"):
        gid = g.get("data-game-id")
        if not gid or gid == "all":
            continue
        for tbl in g.select("table.mod-econ"):
            for td in tbl.find_all("td")[1:]:
                rn = num(txt(td.select_one(".round-num")))
                sq = td.select(".rnd-sq")
                banks = td.select(".bank")
                if rn is None or len(sq) < 2:
                    continue
                out.append({
                    "game_id": int(gid), "round_num": int(rn),
                    "t1_loadout": num(sq[0].get("title")), "t2_loadout": num(sq[1].get("title")),
                    "t1_bank": _bank(txt(banks[0])) if len(banks) > 0 else None,
                    "t2_bank": _bank(txt(banks[1])) if len(banks) > 1 else None,
                    "t1_buy": BUY.get(txt(sq[0]), txt(sq[0])), "t2_buy": BUY.get(txt(sq[1]), txt(sq[1])),
                })
    return out


T2_MIN_YEAR = 2024


def crawl_tier2(con: sqlite3.Connection, refresh: bool = False) -> None:
    """Challengers / Ascension (vlr tier 61) events from T2_MIN_YEAR on. Stored with events.tier='t2'
    so they can inform ratings without ever being scored as tier-one results."""
    known_status = dict(con.execute("SELECT event_id, status FROM events"))
    events = []
    for page in range(1, 15):
        html = fetch(f"/events/?tier=61&page={page}", f"t2-list-{page}.html", force=refresh and page <= 2)
        items = BeautifulSoup(html, "lxml").select("a.event-item")
        if not items:
            break
        years = []
        for a in items:
            title = txt(a.select_one(".event-item-title"))
            y = re.search(r"20(\d\d)", title) or re.search(r"\b(2\d)\b", title)
            year = 2000 + int(y.group(1)) if y else None
            years.append(year)
            status = txt(a.select_one(".event-item-desc-item-status")).lower()
            if year and year >= T2_MIN_YEAR:
                events.append((int(a["href"].split("/")[2]), year, title, status))
        if all(y and y < T2_MIN_YEAR for y in years if y):
            break
    print(f"tier 2: {len(events)} events since {T2_MIN_YEAR}", flush=True)
    done = {r[0] for r in con.execute("SELECT match_id FROM matches WHERE status='completed'")}
    for eid, year, title, status in events:
        con.execute("INSERT OR REPLACE INTO events (event_id, year, name, tier, region, status) VALUES (?,?,?,?,?,?)",
                    (eid, year, title, "t2", None, status))
        force = refresh and not (status == "completed" and known_status.get(eid) == "completed")
        try:
            items = parse_event_matches(eid, force=force)
        except (FileNotFoundError, RuntimeError):
            continue
        new = [it for it in items if "completed" in it["status"] and it["match_id"] not in done]
        if new:
            print(f"  [t2 {year}] {title}: {len(new)} new matches", flush=True)
        for it in new:
            try:
                html = fetch(f"/{it['match_id']}/", f"match-{it['match_id']}.html")
            except (FileNotFoundError, RuntimeError):
                continue
            m = parse_match(it["match_id"], html)
            m["event_id"] = eid
            store(con, m, None)
            con.commit()  # short transactions: the scheduled updater shares this database


def crawl_economy(con: sqlite3.Connection) -> None:
    """Fetch the economy tab for every completed match not yet processed."""
    todo = [r[0] for r in con.execute(
        "SELECT m.match_id FROM matches m JOIN events e USING(event_id) WHERE m.status='completed' AND e.tier != 't2' "
        "AND m.match_id NOT IN (SELECT match_id FROM econ_done) "
        "ORDER BY m.date DESC")]
    print(f"economy: {len(todo)} matches to fetch", flush=True)
    for i, mid in enumerate(todo):
        try:
            html = fetch(f"/{mid}/?game=all&tab=economy", f"econ-{mid}.html")
        except (FileNotFoundError, RuntimeError) as e:
            print("  skip econ", mid, e, flush=True)
            continue
        rows = parse_economy(html)
        con.executemany("INSERT OR REPLACE INTO econ_rounds VALUES (:game_id,:round_num,:t1_loadout,:t2_loadout,"
                        ":t1_bank,:t2_bank,:t1_buy,:t2_buy)", rows)
        con.execute("INSERT OR REPLACE INTO econ_done VALUES (?,?)", (mid, len(rows)))
        if i % 25 == 0:
            con.commit()
            print(f"  economy {i}/{len(todo)}", flush=True)
    con.commit()


# --------------------------------------------------------------------------- storage

def store(con: sqlite3.Connection, m: dict, region: str | None) -> None:
    con.execute(
        "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (m["match_id"], m["event_id"], m["status"], m["date"], m.get("stage"), m["series"], m["best_of"],
         m["patch"], m["team1_id"], m["team2_id"], m["team1"], m["team2"], m["tags"][0], m["tags"][1],
         m["score1"], m["score2"], m["veto"]),
    )
    for (tid, name, tag, logo) in zip((m["team1_id"], m["team2_id"]), (m["team1"], m["team2"]), m["tags"], m["logos"]):
        if tid:
            con.execute(
                "INSERT INTO teams(team_id,name,tag,logo,region) VALUES (?,?,?,?,?) "
                "ON CONFLICT(team_id) DO UPDATE SET name=excluded.name, logo=COALESCE(excluded.logo,logo), "
                "tag=COALESCE(excluded.tag,tag), region=CASE WHEN excluded.region IS NOT NULL THEN excluded.region ELSE region END",
                (tid, name, tag, logo, region),
            )
    con.execute("DELETE FROM maps WHERE match_id=?", (m["match_id"],))
    con.execute("DELETE FROM player_maps WHERE match_id=?", (m["match_id"],))
    for mp in m["maps"]:
        con.execute("DELETE FROM rounds WHERE game_id=?", (mp["game_id"],))
        con.execute("INSERT OR REPLACE INTO maps VALUES (:game_id,:match_id,:map_order,:map,:picked_by,:duration,"
                    ":score1,:score2,:t1_atk,:t1_def,:t2_atk,:t2_def,:t1_ot,:t2_ot)", mp)
    con.executemany("INSERT OR REPLACE INTO rounds VALUES (:game_id,:round_num,:winner,:winner_side,:win_type)", m["rounds"])
    con.executemany(
        "INSERT OR REPLACE INTO player_maps VALUES (:game_id,:match_id,:team,:player_id,:player,:agent,:rating,:acs,"
        ":kills,:deaths,:assists,:kast,:adr,:hs,:fk,:fd,:rating_atk,:rating_def,:acs_atk,:acs_def)", m["players"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-fetch event lists and non-final matches")
    ap.add_argument("--years", type=int, nargs="*", default=YEARS)
    ap.add_argument("--no-economy", action="store_true", help="skip the per-round economy pages")
    ap.add_argument("--tier2", action="store_true", help="also crawl Challengers/Ascension events")
    ap.add_argument("--only-tier2", action="store_true", help="crawl only Challengers/Ascension events")
    args = ap.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=120)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    if "status" not in cols:
        con.execute("ALTER TABLE events ADD COLUMN status TEXT")
    if "country" not in cols:
        con.execute("ALTER TABLE events ADD COLUMN country TEXT")
    done = {r[0] for r in con.execute("SELECT match_id FROM matches WHERE status='completed'")}
    known_status = dict(con.execute("SELECT event_id, status FROM events"))

    for year in ([] if args.only_tier2 else args.years):
        events = parse_year_events(year, force=args.refresh)
        for eid, name, ev_status, country in events:
            tier, region = event_meta(name)
            con.execute("INSERT OR REPLACE INTO events (event_id, year, name, tier, region, status, country) VALUES (?,?,?,?,?,?,?)",
                        (eid, year, name, tier, region, ev_status, country))
            # Re-read an event's match list only while it can still change: not yet completed,
            # or completed since we last looked (one final read picks up the last results).
            ev_force = args.refresh and not (ev_status == "completed" and known_status.get(eid) == "completed")
            if args.refresh and not ev_force and (CACHE / f"event-{eid}.html").exists():
                continue
            try:
                items = parse_event_matches(eid, force=ev_force)
            except FileNotFoundError:
                continue
            print(f"[{year}] {name}: {len(items)} matches", flush=True)
            team_region = region if region != "International" else None
            for it in items:
                mid = it["match_id"]
                if mid in done and "completed" in it["status"]:
                    continue
                final = "completed" in it["status"]
                if not final and it["tbd"]:
                    continue  # nothing to learn from a TBD vs TBD page
                try:
                    html = fetch(f"/{mid}/", f"match-{mid}.html" if final else None, force=False)
                except (FileNotFoundError, RuntimeError) as e:
                    print("  skip", mid, e)
                    continue
                m = parse_match(mid, html)
                m["event_id"] = m["event_id"] or eid
                store(con, m, team_region)
            con.commit()
    if args.tier2 or args.only_tier2:
        crawl_tier2(con, refresh=args.refresh)
    if not args.no_economy and not args.only_tier2:
        crawl_economy(con)
    con.close()
    print(f"requests made: {REQUESTS}", flush=True)


if __name__ == "__main__":
    main()
