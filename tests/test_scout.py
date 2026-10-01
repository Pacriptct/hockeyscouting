import json
from datetime import date
from pathlib import Path

from scout import pipeline
from scout.columns import map_headers
from scout.models import Player
from scout.scoring import current_season, score_all
from scout.sources import csv_folder, eliteprospects, hockeytech, html_table

FIX = Path(__file__).parent / "fixtures"

CFG = {
    "birth_years": {2005: 1.0, 2006: 1.0, 2007: 0.85},
    "keep_if_committed_to": ["ACHA"],
    "min_games": 5,
    "level_weights": {"Tier II": 1.0, "Tier III Top": 0.8, "Prep": 0.75, "Other": 0.5},
}


def test_headers_p_means_position_when_pts_present():
    cols = map_headers(["Player", "P", "GP", "PTS"])
    assert cols == {0: "name", 1: "pos_raw", 2: "gp", 3: "pts"}
    assert map_headers(["Name", "GP", "G", "A", "P"])[4] == "pts"


def test_html_table_parses_skaters_and_goalies():
    html = (FIX / "skaters.html").read_text()
    ps = html_table.parse_tables(html, base_url="https://league.test/stats", league="EHL",
                                 level="Tier III Top", source="EHL")
    names = [p.name for p in ps]
    assert "Totals" not in names
    assert names[:2] == ["Jake Miller", "Ryan O'Neil"]
    jake = ps[0]
    assert (jake.gp, jake.g, jake.a, jake.pts, jake.birth_year) == (20, 15, 20, 35, 2005)
    assert jake.profile_url == "https://league.test/player/101"
    wall = next(p for p in ps if p.name == "Matt Wall")
    assert wall.position == "G" and wall.sv_pct == 0.928 and wall.gaa == 2.10
    sieve = next(p for p in ps if p.name == "Nick Sieve")
    assert sieve.sv_pct == 0.881


def test_hockeytech_rows():
    data = hockeytech._loads((FIX / "hockeytech_players.json").read_text())
    rows = hockeytech.rows_from_statview(data)
    cfg = {"name": "NAHL", "league": "NAHL", "level": "Tier II",
           "player_url": "https://nahl.com/player/{id}"}
    ps = [hockeytech.row_to(r, False, cfg) for r in rows]
    assert ps[0].name == "Alex Carter" and ps[0].birth_year == 2005 and ps[0].position == "F"
    assert ps[0].profile_url == "https://nahl.com/player/5001"
    assert ps[1].position == "D" and ps[1].pts == 8


def test_hockeytech_season_pick():
    seasons = [{"season_id": "70", "season_name": "2026 Playoffs", "playoff": "1"},
               {"season_id": "69", "season_name": "2026-27 Pre-Season", "playoff": "0"},
               {"season_id": "68", "season_name": "2026-27 Regular Season", "playoff": "0"}]
    assert hockeytech.pick_season(seasons) == "68"


def test_eliteprospects_items():
    body = json.loads((FIX / "ep_player_stats.json").read_text())
    cfg = {"name": "EP - Prep", "league": "ushs-prep", "level": "Prep"}
    ps = [eliteprospects.item_to_player(i, cfg) for i in body["data"]]
    luke = ps[0]
    assert luke.birth_year == 2007 and luke.pts == 30 and luke.height == "6'0\""
    assert luke.profile_url.endswith("luke-prepper")
    assert ps[1].committed == "Adrian College (NCAA)"


def test_csv_import(tmp_path):
    ps = csv_folder.read_csv(FIX / "import.csv", {"name": "Imports", "level": "Other"})
    kyle, dan = ps
    assert kyle.name == "Kyle Showcase" and kyle.level == "Tier III Top" and kyle.birth_year == 2005
    assert kyle.email == "kyle@example.com" and kyle.socials["instagram"] == "@kyleshow"
    assert dan.position == "G"


def test_scoring_filters_and_ranks():
    html = (FIX / "skaters.html").read_text()
    ps = html_table.parse_tables(html, league="EHL", level="Tier III Top", source="EHL")
    ranked = score_all(ps, CFG)
    names = [p.name for p in ranked]
    assert "Tom Old" not in names           # 2003, too old
    assert names[0] == "Jake Miller"
    low = next(p for p in ranked if p.name == "Sam Low")
    assert low.score < ranked[0].score
    newguy = next(p for p in ranked if p.name == "Chris Newguy")
    assert any("GP" in n for n in newguy.notes)


def test_committed_players_dropped_unless_acha():
    a = Player(name="A", birth_year=2006, committed="Adrian College (NCAA)")
    b = Player(name="B", birth_year=2006, committed="Some Univ (ACHA D1)")
    kept = [p.name for p in score_all([a, b], CFG)]
    assert kept == ["B"]


def test_current_season():
    assert current_season(date(2026, 10, 1)) == "2026-2027"
    assert current_season(date(2027, 3, 1)) == "2026-2027"


def test_pipeline_tracks_new_and_dedupes(tmp_path, monkeypatch):
    html = (FIX / "skaters.html").read_text()

    def fake_league(src, ctx):
        return html_table.parse_tables(html, league="EHL", level="Tier III Top", source=src["name"])

    def fake_ep(src, ctx):
        # same kid as the league site, with extra info
        return [Player(name="Jake Miller", birth_year=2005, team="Boston Junior Rangers",
                       league="EHL", level="Tier III Top", source="EP", height="5'11\"")]

    monkeypatch.setitem(pipeline.SOURCES, "fake_league", fake_league)
    monkeypatch.setitem(pipeline.SOURCES, "fake_ep", fake_ep)
    cfg = dict(CFG, sources=[{"name": "League", "type": "fake_league"},
                             {"name": "EP", "type": "fake_ep"},
                             {"name": "Broken", "type": "nope"}])

    day1 = pipeline.run(cfg, today=date(2026, 10, 1), out_dir=tmp_path)
    jakes = [p for p in day1["players"] if p["name"] == "Jake Miller"]
    assert len(jakes) == 1 and jakes[0]["height"] == "5'11\""
    assert day1["new_count"] == day1["count"]
    assert not next(s for s in day1["sources"] if s["name"] == "Broken")["ok"]

    day2 = pipeline.run(cfg, today=date(2026, 10, 2), out_dir=tmp_path)
    assert day2["new_count"] == 0
    assert all(p["first_seen"] == "2026-10-01" for p in day2["players"])


def test_digest_lists_only_new():
    from scout import digest
    payload = {"generated": "2026-10-01", "count": 2, "sources": [{"name": "X", "ok": False, "error": "boom"}],
               "players": [
                   {"name": "A B", "new": True, "score": 50, "position": "F", "gp": 10, "g": 5, "a": 5, "pts": 10, "ppg": 1.0, "notes": []},
                   {"name": "C D", "new": False, "score": 40, "position": "G", "gp": 5, "gaa": 2.5, "sv_pct": 0.91, "notes": []}]}
    subject, text, body = digest.build(payload, 10, "https://example.test/")
    assert subject.startswith("1 new") and "A B" in text and "C D" not in text
    assert "boom" in text and "example.test" in body
