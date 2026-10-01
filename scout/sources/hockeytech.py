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
