"""Pull player rows out of any HTML stats table on a page."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..columns import map_headers, row_to_player
from ..models import Player


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
