"""Scrape any stats page that shows players in an HTML table.

Works on most league sites as long as the stats are in a <table>. If the
table is built by JavaScript, set `render: true` and the page gets loaded in
a headless browser first.

Config:
  url:        one URL or a list (e.g. one per page / division / team)
  goalie_url: optional, same thing for goalie stats
  render:     true to run JavaScript before parsing
  league / level / team: filled in when the table doesn't have them
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http
from ..columns import map_headers, row_to_player
from ..models import Player


def _as_list(v) -> list[str]:
    if not v:
        return []
    return v if isinstance(v, list) else [v]


def fetch_html(url: str, render: bool) -> str:
    if render:
        return http.render(url)
    return http.get(url).text


def _header_cells(table) -> list[str]:
    thead = table.find("thead")
    row = None
    if thead:
        rows = thead.find_all("tr")
        row = rows[-1] if rows else None
    if row is None:
        row = table.find("tr")
    if row is None:
        return []
    return [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]


def parse_tables(html: str, *, base_url: str = "", league="", level="Other",
                 team="", source="", force_goalie=False) -> list[Player]:
    soup = BeautifulSoup(html, "lxml")
    players: list[Player] = []
    for table in soup.find_all("table"):
        headers = _header_cells(table)
        if not headers:
            continue
        low = {h.strip().lower() for h in headers}
        goalie = force_goalie or bool(low & {"gaa", "sv%", "sv %", "save %", "svpct"})
        cols = map_headers(headers, goalie_table=goalie)
        fields = set(cols.values())
        if not ({"name", "last"} & fields):
            continue
        if not ({"gp", "pts", "sv_pct", "gaa", "pos_raw", "birth_year", "dob"} & fields):
            continue

        body_rows = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
        for tr in body_rows:
            cells = tr.find_all(["td", "th"])
            if len(cells) < len(cols) or len(cells) < 3:
                continue
            values = {}
            link = ""
            for i, f in cols.items():
                if i >= len(cells):
                    continue
                values[f] = cells[i].get_text(" ", strip=True)
                if f in ("name", "last") and not link:
                    a = cells[i].find("a", href=True)
                    if a:
                        link = urljoin(base_url, a["href"])
            p = row_to_player(values, team=team, league=league, level=level,
                              source=source, source_url=base_url, goalie=goalie)
            if p:
                if link and not p.profile_url:
                    p.profile_url = link
                players.append(p)
    return players


def fetch(cfg: dict, ctx: dict) -> list[Player]:
    out: list[Player] = []
    render = bool(cfg.get("render"))
    common = dict(league=cfg.get("league", ""), level=cfg.get("level", "Other"),
                  team=cfg.get("team", ""), source=cfg["name"])
    for url in _as_list(cfg.get("url")):
        out += parse_tables(fetch_html(url, render), base_url=url, **common)
    for url in _as_list(cfg.get("goalie_url")):
        out += parse_tables(fetch_html(url, render), base_url=url, force_goalie=True, **common)
    return out
