"""Run the scraper's database queries against the current schema (no network).

Guards the scheduled updater: a schema change once made a query ambiguous and every
refresh failed until someone noticed."""
import sqlite3

import pipeline.scrape as S


def make_db(tmp_path):
    con = sqlite3.connect(tmp_path / "t.db")
    con.executescript(S.SCHEMA)
    con.execute("ALTER TABLE events ADD COLUMN status TEXT")
    con.execute("ALTER TABLE events ADD COLUMN country TEXT")
    con.execute("INSERT INTO events VALUES (1, 2026, 'VCT 2026: Americas Stage 2', 'league', 'Americas', 'completed', 'us')")
    con.execute("INSERT INTO events VALUES (2, 2026, 'Challengers 2026: X', 't2', NULL, 'completed', NULL)")
    for mid, ev in ((734308, 1), (999, 2)):
        con.execute("INSERT INTO matches (match_id, event_id, status, date) VALUES (?, ?, 'completed', '2026-09-06 13:00:00')", (mid, ev))
    return con


def test_crawl_economy_queries(tmp_path, monkeypatch, fixture_html):
    con = make_db(tmp_path)
    fetched = []

    def fake_fetch(path, cache_name=None, force=False):
        fetched.append(path)
        return fixture_html("econ-2026-gf")

    monkeypatch.setattr(S, "fetch", fake_fetch)
    S.crawl_economy(con)
    assert fetched == ["/734308/?game=all&tab=economy"]   # tier-2 match skipped
    assert con.execute("SELECT COUNT(*) FROM econ_rounds").fetchone()[0] == 110
    S.crawl_economy(con)                                    # second pass: nothing left to do
    assert len(fetched) == 1
