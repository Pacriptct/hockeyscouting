"""Leagues whose stats run on HockeyTech (lscluster.hockeytech.com).

Config:
  client_code: league code (e.g. "nahl")
  key:         public API key from the league's stats page source
  season_id:   optional, otherwise the newest regular season is used
  league_id:   optional, defaults to 1
"""

from __future__ import annotations

import json
import os
import re

from .. import http
from ..columns import row_to_player
from ..models import Player

FEED = "https://lscluster.hockeytech.com/feed/index.php"


def _loads(text: str):
    """Feed sometimes comes back wrapped in (...) like JSONP."""
    t = text.strip()
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1]
    return json.loads(t)


def pick_season(seasons: list[dict]) -> str | None:
    for s in seasons:
        name = (s.get("season_name") or s.get("name") or "").lower()
        if str(s.get("playoff", "0")) == "1" or re.search(r"playoff|pre-?season|exhibition|showcase", name):
            continue
        return str(s.get("season_id") or s.get("id"))
    return None


def rows_from_statview(data) -> list[dict]:
    """statviewfeed: [{"sections":[{"data":[{"row":{...},"prop":{...}}]}]}]"""
    rows = []
    blocks = data if isinstance(data, list) else [data]
    for b in blocks:
        for sec in (b or {}).get("sections", []):
            for item in sec.get("data", []):
                row = dict(item.get("row") or {})
                prop = item.get("prop") or {}
                pid = (prop.get("name") or {}).get("playerLink") or row.get("player_id")
                if pid:
                    row["_player_id"] = str(pid)
                rows.append(row)
    return rows


def row_to(row: dict, goalie: bool, cfg: dict) -> Player | None:
    values = {
        "name": row.get("name") or f"{row.get('first_name', '')} {row.get('last_name', '')}",
        "team": row.get("team_name") or row.get("team_code") or "",
        "pos_raw": "G" if goalie else row.get("position", ""),
        "dob": row.get("birthdate") or row.get("birthdate_year") or "",
        "hometown": row.get("hometown") or "",
        "shoots": row.get("shoots") or row.get("catches") or "",
        "gp": row.get("games_played"),
        "g": row.get("goals"),
        "a": row.get("assists"),
        "pts": row.get("points"),
        "pim": row.get("penalty_minutes"),
        "plus_minus": row.get("plus_minus"),
        "gaa": row.get("goals_against_average") or row.get("gaa"),
        "sv_pct": row.get("save_percentage") or row.get("savepct"),
    }
    p = row_to_player(values, league=cfg.get("league", ""), level=cfg.get("level", "Other"),
                      source=cfg["name"], source_url=cfg.get("site", ""), goalie=goalie)
    if p and row.get("_player_id") and cfg.get("player_url"):
        p.profile_url = cfg["player_url"].format(id=row["_player_id"])
    return p


def fetch(cfg: dict, ctx: dict) -> list[Player]:
    key = cfg.get("key") or os.environ.get(f"HT_KEY_{cfg['client_code'].upper()}", "")
    if not key:
        raise RuntimeError(f"no HockeyTech key for {cfg['name']} (set `key` in config.yaml)")
    base = {"key": key, "client_code": cfg["client_code"], "fmt": "json", "lang": "en"}

    season_id = cfg.get("season_id")
    if not season_id:
        r = http.get(FEED, params={**base, "feed": "modulekit", "view": "seasons"})
        seasons = _loads(r.text).get("SiteKit", {}).get("Seasons", [])
        season_id = pick_season(seasons)
    if not season_id:
        raise RuntimeError(f"couldn't figure out season for {cfg['name']}")

    out: list[Player] = []
    for position, goalie in (("skaters", False), ("goalies", True)):
        params = {
            **base, "feed": "statviewfeed", "view": "players", "season": season_id,
            "team": "all", "position": position, "rookies": 0, "statsType": "standard",
            "league_id": cfg.get("league_id", 1), "division": -1, "first": 0,
            "limit": 2000, "sort": "points" if not goalie else "gaa",
            "qualified": "all",
        }
        r = http.get(FEED, params=params)
        for row in rows_from_statview(_loads(r.text)):
            p = row_to(row, goalie, cfg)
            if p:
                out.append(p)
    return out


def _sitekit(params: dict, view: str, **extra):
    r = http.get(FEED, params={**params, "feed": "modulekit", "view": view, **extra})
    return _loads(r.text).get("SiteKit", {})


def enrich_from_rosters(players: list[Player], feed_url: str) -> None:
    """The stats feed on a lot of HockeyTech sites leaves out birthdates and
    only gives team codes. Pull each team's roster and fill those in."""
    from urllib.parse import parse_qsl, urlsplit

    from ..models import norm_name, parse_birth_year

    q = dict(parse_qsl(urlsplit(feed_url).query))
    if not (q.get("key") and q.get("client_code") and q.get("season")):
        return
    if all(p.birth_year for p in players):
        return
    base = {"key": q["key"], "client_code": q["client_code"], "fmt": "json", "lang": "en"}
    season = q["season"]
    teams = _sitekit(base, "teamsbyseason", season_id=season).get("Teamsbyseason", [])
    by_name: dict[str, dict] = {}
    for t in teams:
        tid = t.get("id") or t.get("team_id")
        if not tid:
            continue
        team_name = t.get("name") or t.get("team_name") or ""
        for r in _sitekit(base, "roster", season_id=season, team_id=tid).get("Roster", []):
            if not isinstance(r, dict):
                continue
            name = r.get("name") or f"{r.get('first_name', '')} {r.get('last_name', '')}"
            r = dict(r, _team=team_name, _code=t.get("code", ""))
            by_name[norm_name(name)] = r
    for p in players:
        r = by_name.get(norm_name(p.name))
        if not r:
            continue
        p.dob = p.dob or r.get("birthdate") or r.get("rawbirthdate") or ""
        p.birth_year = p.birth_year or parse_birth_year(r.get("birthdate_year") or p.dob)
        p.hometown = p.hometown or r.get("hometown") or r.get("birthplace") or ""
        p.height = p.height or r.get("height") or r.get("height_hyphenated") or ""
        p.weight = p.weight or str(r.get("weight") or "")
        p.shoots = p.shoots or r.get("shoots") or r.get("catches") or ""
        if r["_team"] and (not p.team or p.team == r["_code"] or len(p.team) <= 4):
            p.team = r["_team"]
