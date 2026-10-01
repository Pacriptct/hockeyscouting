"""Pull players from a league's own stats page, whatever it's built on.

League sites are all different (SportsEngine, HockeyTech, GameSheet, custom
.cfm pages...), so this tries everything on the page:

  1. any <table> with player stats in it
  2. any JSON the page loads in the background (with `render: true`) that
     looks like a list of players - this is how most JavaScript stats
     widgets get their data
  3. "Next" page links, up to `max_pages`

Config:
  url:        stats page, or a list of them (one per division, etc.)
  goalie_url: optional, separate goalie stats page(s)
  render:     true = load the page in a headless browser first (needed for
              JavaScript stats widgets; also turns on the JSON capture)
  max_pages:  how many "Next" pages to follow (default 25)
  league / level / team: used when the page doesn't say
"""

from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .. import http
from ..columns import ALIASES, row_to_player
from ..models import Player
from .html_table import parse_tables

log = logging.getLogger("scout")

NEXT_TEXT = re.compile(r"^\s*(next|next page|›|»|>|>>)\s*$", re.I)
LIMIT_PARAMS = ("limit", "per_page", "perpage", "pagesize", "page_size", "rows", "count", "max", "num")


# ---------------------------------------------------------------- JSON feeds

def _k(s: str) -> str:
    return re.sub(r"[^a-z0-9%+/]", "", str(s).lower())


JSON_KEYS = {}
for _field, _names in ALIASES.items():
    for _n in _names:
        JSON_KEYS.setdefault(_k(_n), _field)
JSON_KEYS.update({
    "fullname": "name", "playername": "name", "displayname": "name", "playerfullname": "name",
    "firstname": "first", "lastname": "last", "playerfirstname": "first", "playerlastname": "last",
    "teamname": "team", "teamtitle": "team", "teamfullname": "team", "teamcode": "team",
    "gamesplayed": "gp", "goals": "g", "assists": "a", "points": "pts", "totalpoints": "pts",
    "penaltyminutes": "pim", "plusminus": "plus_minus",
    "goalsagainstaverage": "gaa", "savepercentage": "sv_pct", "savepct": "sv_pct",
    "birthdate": "dob", "dateofbirth": "dob", "birthday": "dob", "yearofbirth": "birth_year",
    "birthyear": "birth_year", "playerposition": "pos_raw", "position": "pos_raw",
    "positionabbreviation": "pos_raw", "pos": "pos_raw", "hometown": "hometown",
    "birthplace": "hometown", "shoots": "shoots", "catches": "shoots",
})
# single letters are too risky as JSON keys ("a", "p" show up everywhere)
for _bad in ("p", "a", "g", "x", "by", "bd", "sh", "pm", "pct", "url", "link", "home", "city"):
    JSON_KEYS.pop(_bad, None)
JSON_KEYS.update({"gp": "gp", "pts": "pts", "pim": "pim", "gaa": "gaa"})
FLATTEN_INTO = {"row", "player", "stats", "stat", "data", "attributes", "regularstats", "statistics", "person"}


def _flatten(d: dict, prefix: str = "", depth: int = 0) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and depth < 2:
            sub = _flatten(v, _k(k), depth + 1)
            for sk, sv in sub.items():
                out.setdefault(sk, sv)
            if _k(k) in FLATTEN_INTO:
                for sk, sv in _flatten(v, "", depth + 1).items():
                    out.setdefault(sk, sv)
        elif not isinstance(v, (list, dict)):
            out.setdefault(prefix + _k(k), v)
    return out


def json_row_values(item: dict) -> dict:
    flat = _flatten(item)
    values = {}
    for k, v in flat.items():
        f = JSON_KEYS.get(k)
        if f and f not in values and v not in (None, ""):
            values[f] = str(v)
    # "goals"/"assists" can only be trusted when the row also looks like stats
    return values


STANDINGS_KEYS = {"wins", "losses", "w", "l", "otl", "sol", "ties", "gf", "ga", "streak", "rank"}


def _looks_like_players(rows: list[dict], raw: list[dict] | None = None) -> bool:
    if len(rows) < 2:
        return False
    if raw:
        standings = sum(1 for r in raw[:30] if len(STANDINGS_KEYS & set(_flatten(r))) >= 2)
        if standings >= len(raw[:30]) / 2:
            return False
    hits = 0
    for r in rows[:30]:
        has_name = "name" in r or ("first" in r and "last" in r)
        has_stat = bool({"gp", "pts", "sv_pct", "gaa", "birth_year", "dob"} & r.keys())
        hits += has_name and has_stat
    return hits / min(len(rows), 30) >= 0.6


def find_player_lists(data) -> list[list[dict]]:
    """Walk any JSON and return every list that looks like player rows."""
    found = []

    def walk(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            if len(dicts) >= 2:
                rows = [json_row_values(x) for x in dicts]
                if _looks_like_players(rows, dicts):
                    found.append(rows)
                    return
            for x in node:
                walk(x, depth + 1)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v, depth + 1)

    walk(data)
    return found


def loads_loose(text: str):
    """JSON, JSONP `callback({...})`, or `({...})`."""
    t = text.strip()
    if not t or t[0] not in "[{(" and not re.match(r"^[\w$.]+\s*\(", t):
        return None
    m = re.match(r"^[\w$.]*\s*\((.*)\)\s*;?\s*$", t, re.S)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except ValueError:
        return None


def players_from_json(data, *, cfg: dict, source_url: str, goalie_hint=False) -> list[Player]:
    out = []
    for rows in find_player_lists(data):
        for values in rows:
            pos = (values.get("pos_raw") or "").upper()
            goalie = goalie_hint or pos.startswith("G") or (
                ("sv_pct" in values or "gaa" in values) and "pts" not in values)
            p = row_to_player(values, league=cfg.get("league", ""), level=cfg.get("level", "Other"),
                              team=cfg.get("team", ""), source=cfg["name"], source_url=source_url,
                              goalie=goalie)
            if p:
                out.append(p)
    return out


def widen(url: str) -> str | None:
    """If a feed URL has a page-size param, ask for everything at once."""
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    changed = False
    new = []
    for k, v in q:
        if k.lower() in LIMIT_PARAMS and v.isdigit() and int(v) < 1000:
            new.append((k, "2000"))
            changed = True
        else:
            new.append((k, v))
    if not changed:
        return None
    return urlunsplit(parts._replace(query=urlencode(new)))


# ---------------------------------------------------------------- pages

def next_link(html: str, base: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    a = soup.find("a", rel="next", href=True)
    if not a:
        for cand in soup.find_all("a", href=True):
            label = cand.get_text(" ", strip=True) or cand.get("aria-label", "") or cand.get("title", "")
            if NEXT_TEXT.match(label) or label.lower() in {"next page", "next »", "next ›"}:
                a = cand
                break
    if not a or a["href"].startswith(("#", "javascript")):
        return None
    return urljoin(base, a["href"])


def load(url: str, render: bool) -> tuple[str, list[tuple[str, object]]]:
    """Returns (html, [(feed_url, parsed_json), ...])."""
    if not render:
        return http.get(url).text, []

    from playwright.sync_api import sync_playwright

    feeds: list[tuple[str, object]] = []

    def on_response(resp):
        try:
            if resp.request.resource_type not in ("xhr", "fetch", "script", "document"):
                return
            ctype = resp.headers.get("content-type", "")
            if not any(t in ctype for t in ("json", "javascript", "text/plain", "text/html")):
                return
            data = loads_loose(resp.text())
            if data is not None:
                feeds.append((resp.url, data))
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(user_agent=http.UA)
        page.on("response", on_response)
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        html = page.content()
        # stats widgets often live in iframes
        for frame in page.frames[1:]:
            try:
                html += frame.content()
            except Exception:
                pass
        browser.close()
    return html, feeds


def scrape_url(url: str, cfg: dict, goalie=False) -> list[Player]:
    render = bool(cfg.get("render"))
    max_pages = int(cfg.get("max_pages", 25))
    common = dict(league=cfg.get("league", ""), level=cfg.get("level", "Other"),
                  team=cfg.get("team", ""), source=cfg["name"])
    out: list[Player] = []
    seen_pages = set()
    page_url = url
    for _ in range(max_pages):
        if page_url in seen_pages:
            break
        seen_pages.add(page_url)
        html, feeds = load(page_url, render)
        got = parse_tables(html, base_url=page_url, force_goalie=goalie, **common)

        for feed_url, data in feeds:
            from_feed = players_from_json(data, cfg=cfg, source_url=url, goalie_hint=goalie)
            if not from_feed:
                continue
            wide = widen(feed_url)
            if wide:
                try:
                    more = players_from_json(loads_loose(http.get(wide).text), cfg=cfg,
                                             source_url=url, goalie_hint=goalie)
                    if len(more) > len(from_feed):
                        from_feed = more
                except Exception as e:
                    log.debug("couldn't widen %s: %s", feed_url, e)
            got += from_feed

        if not got and page_url != url:
            break
        out += got
        nxt = next_link(html, page_url)
        if not nxt:
            break
        page_url = nxt
    return out


def best_per_player(players: list[Player]) -> list[Player]:
    """Tables + feeds + extra pages overlap. Same name on the same league site
    is the same kid (unless the birth years clash); keep the row with the most
    games and fill its blanks from the others."""
    from ..models import norm_name

    groups: dict[tuple, list[Player]] = {}
    for p in players:
        groups.setdefault((norm_name(p.name), p.is_goalie), []).append(p)
    out = []
    for members in groups.values():
        clusters: list[list[Player]] = []
        for p in members:
            for c in clusters:
                years = {x.birth_year for x in c if x.birth_year}
                if not p.birth_year or not years or p.birth_year in years:
                    c.append(p)
                    break
            else:
                clusters.append([p])
        for c in clusters:
            best = max(c, key=lambda x: x.gp or 0)
            for other in c:
                for f in ("birth_year", "dob", "profile_url", "hometown", "shoots", "position",
                          "pos_raw", "height", "weight"):
                    if not getattr(best, f) and getattr(other, f):
                        setattr(best, f, getattr(other, f))
            out.append(best)
    return out


def _as_list(v) -> list[str]:
    if not v:
        return []
    return v if isinstance(v, list) else [v]


def fetch(cfg: dict, ctx: dict) -> list[Player]:
    out: list[Player] = []
    for url in _as_list(cfg.get("url")):
        out += scrape_url(url, cfg)
    for url in _as_list(cfg.get("goalie_url")):
        out += scrape_url(url, cfg, goalie=True)
    return best_per_player(out)


def probe(url: str, render: bool = True) -> None:
    """Print what the scraper can see on a page. Used to test a URL before
    adding it to config.yaml."""
    cfg = {"name": "probe", "league": "", "level": "Other", "render": render, "max_pages": 1}
    html, feeds = load(url, render)
    print(f"Page: {url}  (render={render}, {len(html):,} chars of HTML)\n")

    soup = BeautifulSoup(html, "lxml")
    from .html_table import _header_cells
    tables = soup.find_all("table")
    print(f"Tables on page: {len(tables)}")
    for i, t in enumerate(tables):
        heads = _header_cells(t)
        if heads:
            print(f"  [{i}] {len(t.find_all('tr'))} rows | {' | '.join(heads[:14])}")
    from_tables = parse_tables(html, base_url=url, source="probe")
    print(f"  -> players read from tables: {len(from_tables)}\n")

    print(f"Background data feeds captured: {len(feeds)}")
    total_feed = 0
    for feed_url, data in feeds:
        ps = players_from_json(data, cfg=cfg, source_url=url)
        if ps:
            total_feed += len(ps)
            print(f"  PLAYERS x{len(ps)}: {feed_url[:160]}")
            if "hockeytech.com" in feed_url:
                q = dict(parse_qsl(urlsplit(feed_url).query))
                print(f"    HockeyTech feed! client_code={q.get('client_code')} key={q.get('key')}")
            if widen(feed_url):
                print("    (page size can be raised - full list will be pulled)")
    print(f"  -> players read from feeds: {total_feed}\n")

    nxt = next_link(html, url)
    print(f"Next page link: {nxt or 'none'}\n")

    sample = best_per_player(from_tables + [p for f in feeds for p in players_from_json(f[1], cfg=cfg, source_url=url)])
    sample.sort(key=lambda p: (p.pts or 0), reverse=True)
    print(f"Sample ({len(sample)} unique players):")
    for p in sample[:15]:
        print(f"  {p.name:<26} {p.position or '?':<2} {p.birth_year or '????'}  {p.team[:24]:<24} "
              f"GP {p.gp or '-':<3} PTS {p.pts if p.pts is not None else '-':<4} "
              f"SV% {p.sv_pct if p.sv_pct is not None else '-'}")
    if not sample:
        print("  nothing found. Try the league's full stats page (not the home page / leaders),"
              " or with render on.")
