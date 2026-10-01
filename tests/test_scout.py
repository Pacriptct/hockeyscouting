import json
from datetime import date
from pathlib import Path

from scout import pipeline
from scout.columns import map_headers
from scout.models import Player
from scout.scoring import current_season, score_all
from scout.sources import csv_folder, hockeytech, html_table, league_site

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


def test_feed_detection_nested_and_jsonp():
    text = """cb({"payload": {"players": [
      {"player": {"firstName": "Joe", "lastName": "Smith", "birthDate": "2005-02-01", "position": "C"},
       "team": {"name": "Rochester Jr Americans"}, "stats": {"gamesPlayed": 10, "goals": 5, "assists": 7, "points": 12}},
      {"player": {"firstName": "Al", "lastName": "Jones", "birthDate": "2006-05-05", "position": "D"},
       "team": {"name": "Rochester Jr Americans"}, "stats": {"gamesPlayed": 10, "goals": 1, "assists": 3, "points": 4}},
      {"player": {"firstName": "Gus", "lastName": "Net", "birthDate": "2006-01-01", "position": "G"},
       "team": {"name": "Rochester Jr Americans"}, "stats": {"gamesPlayed": 8, "goalsAgainstAverage": 2.5, "savePercentage": 0.915}}
    ], "meta": {"count": 3}}});"""
    data = league_site.loads_loose(text)
    ps = league_site.players_from_json(data, cfg={"name": "X", "league": "EHL", "level": "Tier II"},
                                       source_url="u")
    joe, al, gus = ps
    assert joe.name == "Joe Smith" and joe.team == "Rochester Jr Americans"
    assert (joe.gp, joe.g, joe.a, joe.pts, joe.birth_year) == (10, 5, 7, 12, 2005)
    assert al.position == "D"
    assert gus.is_goalie and gus.sv_pct == 0.915 and gus.pts is None


def test_feed_detection_hockeytech_shape():
    data = hockeytech._loads((FIX / "hockeytech_players.json").read_text())
    rows = [{"row": {"name": f"P{i}", "games_played": "5", "points": str(i)}} for i in range(3)]
    ps = league_site.players_from_json(data + [{"sections": [{"data": rows}]}],
                                       cfg={"name": "X"}, source_url="u")
    names = {p.name for p in ps}
    assert {"Alex Carter", "Ben Diaz", "P0"} <= names


def test_non_player_json_ignored():
    data = {"teams": [{"name": "A", "wins": 3}, {"name": "B", "wins": 2}, {"name": "C", "wins": 1}],
            "news": [{"title": "x"}] * 5}
    assert league_site.players_from_json(data, cfg={"name": "X"}, source_url="u") == []


def test_widen_and_next_link():
    assert "limit=2000" in league_site.widen("https://f.test/feed?view=players&limit=20&key=k")
    assert "offset=0" in league_site.widen("https://f.test/feed?limit=20&offset=20")
    assert league_site.widen("https://f.test/feed?view=players") is None
    html = '<a href="?page=1">1</a><a href="?page=2">Next</a>'
    assert league_site.next_link(html, "https://l.test/stats") == "https://l.test/stats?page=2"
    assert league_site.next_link('<a href="#">Next</a>', "https://l.test/") is None


def test_league_site_follows_pages(monkeypatch):
    page1 = (FIX / "skaters.html").read_text().replace("</body>", '<a href="/stats?page=2">Next »</a></body>')
    page1 = page1.replace('Next »', 'Next')
    page2 = """<table><tr><th>Player</th><th>Team</th><th>GP</th><th>PTS</th></tr>
               <tr><td>Page Two</td><td>T</td><td>9</td><td>9</td></tr>
               <tr><td>Jake Miller</td><td>T</td><td>21</td><td>36</td></tr></table>"""
    pages = {"https://l.test/stats": page1, "https://l.test/stats?page=2": page2}
    monkeypatch.setattr(league_site, "load", lambda url, render: (pages[url], []))
    ps = league_site.fetch({"name": "EHL", "url": "https://l.test/stats", "league": "EHL"}, {})
    names = [p.name for p in ps]
    assert "Page Two" in names
    assert names.count("Jake Miller") == 1
    assert next(p for p in ps if p.name == "Jake Miller").gp == 21


def test_source_without_url_is_marked_setup(tmp_path):
    cfg = dict(CFG, sources=[{"name": "NAHL", "type": "league_site", "url": ""}])
    out = pipeline.run(cfg, today=date(2026, 10, 1), out_dir=tmp_path)
    s = out["sources"][0]
    assert s["setup"] and not s["ok"]


def test_widen_leaves_signed_and_unlimited_urls_alone():
    signed = "https://api.t.test/get_skaters?auth_key=k&league_id=1&limit=20&auth_signature=abc"
    assert league_site.widen(signed) is None
    assert league_site.widen("https://f.test/feed?limit=0") is None


def test_name_case_fixed():
    from scout.columns import row_to_player
    assert row_to_player({"name": "VINCENZO CAPANO"}).name == "Vincenzo Capano"
    assert row_to_player({"name": "RYAN O'NEIL-MCDONALD"}).name == "Ryan O'Neil-McDonald"
    assert row_to_player({"name": "Smith, Joe"}).name == "Joe Smith"
    assert row_to_player({"name": "Joe DeAngelo"}).name == "Joe DeAngelo"


def test_hockeytech_roster_enrichment(monkeypatch):
    calls = []

    def fake_sitekit(base, view, **extra):
        calls.append(view)
        if view == "teamsbyseason":
            return {"Teamsbyseason": [{"id": "7", "name": "Camrose Kodiaks", "code": "CAM"}]}
        return {"Roster": [{"first_name": "Connor", "last_name": "Frost", "birthdate": "2006-04-04",
                            "hometown": "Red Deer, AB", "shoots": "L"}, "junk"]}

    monkeypatch.setattr(hockeytech, "_sitekit", fake_sitekit)
    p = Player(name="Connor Frost", team="CAM")
    hockeytech.enrich_from_rosters(
        [p], "https://lscluster.hockeytech.com/feed/index.php?feed=statviewfeed&season=48&key=k&client_code=ajhl")
    assert p.birth_year == 2006 and p.team == "Camrose Kodiaks" and p.hometown == "Red Deer, AB"


def test_columnar_feed():
    data = {"data": {
        "ids": [1, 2, 3],
        "name": [{"title": "Joe Smith", "id": 1}, {"title": "Al Jones", "id": 2}, {"title": "Bo Hill", "id": 3}],
        "team": [{"title": "Valley Jr Warriors"}, {"title": "Valley Jr Warriors"}, {"title": "Seahawks"}],
        "pos": ["F", "D", "F"],
        "gp": [10, 9, 8], "g": [5, 1, 2], "a": [6, 4, 2], "pts": [11, 5, 4],
        "birthdate": ["2006-01-02", "2007-03-04", "2008-05-06"],
    }}
    ps = league_site.players_from_json(data, cfg={"name": "EHL", "league": "EHL"}, source_url="u")
    joe = ps[0]
    assert [p.name for p in ps] == ["Joe Smith", "Al Jones", "Bo Hill"]
    assert (joe.team, joe.gp, joe.pts, joe.birth_year, joe.position) == ("Valley Jr Warriors", 10, 11, 2006, "F")


def test_gamesheet_record():
    # shape copied from gamesheetstats.com/api/players/standings
    rec = {"birthdate": "2006-08-05", "firstName": "ALEX", "height": "5' 9", "hometown": "Bensalem",
           "id": 8612973, "jersey": "11", "lastName": "HOOD", "photoUrl": None, "positions": ["forward"],
           "stats": {"a": 1, "g": 3, "gp": 3, "pim": 0, "pm": 0, "pts": 4, "ptspg": 1.33},
           "teams": [{"abbreviation": "PHI", "active": True, "committedTo": None, "id": 526642,
                      "title": "Philadelphia Little Flyers"}]}
    other = dict(rec, firstName="JOE", positions=["defense"], teams=[dict(rec["teams"][0], committedTo="Univ (ACHA)")])
    ps = league_site.players_from_json({"data": [rec, other]}, cfg={"name": "EHL", "league": "EHL"}, source_url="u")
    alex, joe = ps
    assert alex.name == "Alex Hood" and alex.position == "F" and alex.birth_year == 2006
    assert (alex.gp, alex.g, alex.a, alex.pts) == (3, 3, 1, 4)
    assert alex.team == "Philadelphia Little Flyers" and alex.hometown == "Bensalem"
    assert joe.position == "D" and joe.committed == "Univ (ACHA)"


def test_timetoscore_goalies_not_mistaken_for_standings():
    # shape from api.usphl.timetoscore.com/get_goalies
    g = {"teams": [{"team_id": "2456", "team_ab": "FM", "team_name": "Fresno Monsters", "position": "G"}],
         "player_name": "Sam Net", "player_id": "1", "team_name": "Fresno Monsters", "jersey": "30",
         "position": "G", "games_played": "1", "goals_against": "4", "saves": "18", "save_pct": "0.8182",
         "wins": "0", "losses": "1", "ot_losses": "0", "goals_against_ave": "4.000"}
    ps = league_site.players_from_json({"goalies": [g, dict(g, player_name="Al Pad")]},
                                       cfg={"name": "NCDC"}, source_url="u")
    assert len(ps) == 2
    assert ps[0].is_goalie and ps[0].sv_pct == 0.8182 and ps[0].gaa == 4.0 and ps[0].team == "Fresno Monsters"


def test_committed_flags():
    keep = score_all([Player(name="A", birth_year=2006, committed="False"),
                      Player(name="B", birth_year=2006, committed="True")], CFG)
    assert [p.name for p in keep] == ["A"]
    assert not keep[0].notes or "commitment" not in " ".join(keep[0].notes)


def test_unknown_position_codes_stay_blank():
    from scout.models import norm_position
    assert norm_position("A") == "" and norm_position("LW") == "F" and norm_position("forward") == "F"
    assert norm_position("defense") == "D" and norm_position("Goalie") == "G" and norm_position("C/RW") == "F"


def test_gamesheet_direct(monkeypatch):
    def rec(i):
        return {"firstName": f"P{chr(65 + i % 26)}{chr(65 + i // 26)}", "lastName": "SMITH",
                "birthdate": "2006-06-20", "positions": ["forward"],
                "stats": {"a": 1, "g": 1, "gp": 7, "pts": 2}, "teams": [{"id": 1, "title": "Team A"}]}
    calls = []

    class S:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append((url, dict(params)))
            off, lim = params["offset"], params["limit"]

            class R:
                status_code = 200
                text = json.dumps({"data": [rec(i) for i in range(off, min(off + lim, 620))]})
            return R()

    monkeypatch.setattr(league_site.http, "session", lambda: S())
    ps = league_site.fetch({"name": "EHL", "url": "https://gamesheetstats.com/seasons/15125/players"}, {})
    assert len(ps) == 620
    assert calls[0][0] == "https://gamesheetstats.com/api/players/standings/15125"
    assert calls[0][1]["offset"] == 0 and calls[1][1]["offset"] == 500
    assert ps[0].team == "Team A" and ps[0].position == "F"


def test_sh_column_is_not_shoots():
    cols = map_headers(["Name", "GP", "G", "A", "PTS", "SH", "S/C"])
    assert "shoots" not in [cols.get(5)]
    from scout.columns import row_to_player
    assert row_to_player({"name": "A B", "shoots": "19"}).shoots == ""
    assert row_to_player({"name": "A B", "shoots": "Left"}).shoots == "L"


def test_local_runs_write_ignored_file(tmp_path, monkeypatch):
    cfg = dict(CFG, sources=[])
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    pipeline.run(cfg, today=date(2026, 10, 1), out_dir=tmp_path)
    assert (tmp_path / "prospects.local.json").exists() and not (tmp_path / "prospects.json").exists()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    pipeline.run(cfg, today=date(2026, 10, 1), out_dir=tmp_path)
    assert (tmp_path / "prospects.json").exists()
