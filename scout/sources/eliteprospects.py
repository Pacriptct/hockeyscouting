"""Elite Prospects, through their official API.

Needs an API key (https://www.eliteprospects.com/api) in the EP_API_KEY
env var / repo secret. EP doesn't allow scraping the website itself, so
without a key this source is skipped. Use CSV exports in imports/ instead.

Config:
  league: EP league slug, same as in the EP URL
          (eliteprospects.com/league/<slug>)
"""

from __future__ import annotations

import os

from .. import http
from ..columns import row_to_player
from ..models import Player

API = "https://api.eliteprospects.com/v1"


def _g(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


def _measure(v, imperial_key="imperial"):
    if isinstance(v, dict):
        return str(v.get(imperial_key) or v.get("metrics") or "")
    return str(v or "")


def item_to_player(item: dict, cfg: dict) -> Player | None:
    pl = item.get("player") or {}
    stats = item.get("regularStats") or item.get("stats") or {}
    pos = pl.get("position") or ""
    goalie = str(pos).upper().startswith("G")
    values = {
        "name": pl.get("name") or f"{pl.get('firstName', '')} {pl.get('lastName', '')}",
        "team": _g(item, "team", "name", default=""),
        "pos_raw": pos,
        "dob": pl.get("dateOfBirth") or "",
        "birth_year": pl.get("yearOfBirth") or "",
        "shoots": pl.get("shoots") or pl.get("catches") or "",
        "height": _measure(pl.get("height")),
        "weight": _measure(pl.get("weight")),
        "hometown": pl.get("placeOfBirth") or "",
        "nationality": _g(pl, "nationality", "name", default=""),
        "gp": stats.get("GP"),
        "g": stats.get("G"),
        "a": stats.get("A"),
        "pts": stats.get("PTS") or stats.get("TP"),
        "pim": stats.get("PIM"),
        "plus_minus": stats.get("PM"),
        "gaa": stats.get("GAA"),
        "sv_pct": stats.get("SVP") or stats.get("SVS"),
    }
    p = row_to_player(values, league=_g(item, "league", "name", default=cfg.get("league", "")),
                      level=cfg.get("level", "Other"), source=cfg["name"],
                      source_url=f"https://www.eliteprospects.com/league/{cfg['league']}", goalie=goalie)
    if not p:
        return None
    p.profile_url = _g(pl, "links", "eliteprospectsUrl", default="") or (
        f"https://www.eliteprospects.com/player/{pl['id']}" if pl.get("id") else "")
    commit = pl.get("commitment") or _g(pl, "nextTeam", "name", default="")
    if commit:
        p.committed = str(commit if not isinstance(commit, dict) else commit.get("name", ""))
    return p


def fetch(cfg: dict, ctx: dict) -> list[Player]:
    key = os.environ.get("EP_API_KEY", "")
    if not key:
        raise RuntimeError("EP_API_KEY not set, skipping Elite Prospects")
    out: list[Player] = []
    offset, limit = 0, 100
    while True:
        r = http.get(f"{API}/player-stats", params={
            "apiKey": key, "league": cfg["league"], "season": ctx["season"],
            "limit": limit, "offset": offset,
        })
        body = r.json()
        data = body.get("data") or []
        for item in data:
            p = item_to_player(item, cfg)
            if p:
                out.append(p)
        total = _g(body, "_meta", "totalRecords", default=0) or 0
        offset += limit
        if not data or offset >= total:
            break
    return out
