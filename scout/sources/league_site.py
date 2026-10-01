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
    "goalsagainstaverage": "gaa", "goalsagainstave": "gaa", "savepercentage": "sv_pct", "savepct": "sv_pct",
    "birthdate": "dob", "dateofbirth": "dob", "birthday": "dob", "yearofbirth": "birth_year",
    "birthyear": "birth_year", "playerposition": "pos_raw", "position": "pos_raw",
    "positionabbreviation": "pos_raw", "pos": "pos_raw", "positions": "pos_raw",
    "teamsname": "team", "teamstitle": "team", "teamscommitted": "committed",
    "committedto": "committed", "teamscommittedto": "committed", "hometown": "hometown",
    "birthplace": "hometown", "shoots": "shoots", "catches": "shoots",
})
# single letters are too risky as JSON keys ("a", "p" show up everywhere)
for _bad in ("p", "a", "g", "x", "by", "bd", "sh", "pm", "pct", "url", "link", "home", "city"):
    JSON_KEYS.pop(_bad, None)
JSON_KEYS.update({"gp": "gp", "pts": "pts", "pim": "pim", "gaa": "gaa"})
FLATTEN_INTO = {"row", "player", "stats", "stat", "data", "attributes", "regularstats", "statistics", "person"}
# one-letter stat keys are only trusted inside a stats block ("stats": {"g": 3, "a": 1})
for _p in ("row", "stats", "stat", "regularstats", "statistics"):
    JSON_KEYS.update({_p + "g": "g", _p + "a": "a", _p + "pm": "plus_minus", _p + "p": "pts"})


SCALAR_WRAPPERS = ("title", "value", "text", "label", "display", "name", "fullName", "abbreviation")


WRAPPER_EXTRAS = {"id", "href", "url", "link", "slug", "type", "abbreviation", "abbr", "logo", "image", "key"}


def _unwrap(v):
    """{"title": "John Smith", "id": 4} -> "John Smith" when it's just a wrapped value."""
    if isinstance(v, dict) and len(v) <= 4:
        for k in SCALAR_WRAPPERS:
            if k in v and not isinstance(v[k], (dict, list)):
                extra = {_k(x) for x in v} - {_k(k)}
                if extra <= WRAPPER_EXTRAS:
                    return v[k]
    return v


def _columnar_rows(node: dict) -> list[dict] | None:
    """{"name": [...], "gp": [...], ...} -> [{"name":..., "gp":...}, ...]"""
    cols = {k: v for k, v in node.items() if isinstance(v, list) and len(v) >= 2}
    if len(cols) < 3:
        return None
    lengths = {}
    for k, v in cols.items():
        lengths.setdefault(len(v), []).append(k)
    n, keys = max(lengths.items(), key=lambda x: len(x[1]))
    if len(keys) < 3:
        return None
    rows = []
    for i in range(n):
        row = {}
        for k in keys:
            v = _unwrap(cols[k][i])
            if isinstance(v, list):
                v = v[0] if v else None
            row[k] = _unwrap(v)
        rows.append(row)
    return rows


def _flatten(d: dict, prefix: str = "", depth: int = 0) -> dict:
    out = {}
    for k, v in d.items():
        v = _unwrap(v)
        if isinstance(v, dict) and depth < 2:
            sub = _flatten(v, _k(k), depth + 1)
            for sk, sv in sub.items():
                out.setdefault(sk, sv)
            if _k(k) in FLATTEN_INTO:
                for sk, sv in _flatten(v, "", depth + 1).items():
                    out.setdefault(sk, sv)
        elif isinstance(v, list) and v and depth < 2:
            first = _unwrap(v[0])
            if isinstance(first, dict):
                # "teams": [{...}] -> teamsname, teamscommittedto, ...
                for sk, sv in _flatten(first, prefix + _k(k), depth + 1).items():
                    out.setdefault(sk, sv)
            elif not isinstance(first, list):
                out.setdefault(prefix + _k(k), first)
        elif not isinstance(v, (list, dict)):
            out.setdefault(prefix + _k(k), v)
    return out


TEAM_PRIORITY = ["teamname", "teamsname", "teams", "teamtitle", "teamstitle", "teamfullname", "team",
                 "teamcode", "teamsabbreviation", "teamabbreviation"]


def json_row_values(item: dict) -> dict:
    flat = _flatten(item)
    values = {}
    for k, v in flat.items():
        f = JSON_KEYS.get(k)
        if f and f not in values and v not in (None, ""):
            values[f] = str(v)
    # full team names beat 3-letter codes
    for k in TEAM_PRIORITY:
        v = flat.get(k)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            values["team"] = str(v)
            break
    # "goals"/"assists" can only be trusted when the row also looks like stats
    return values


# keys that only show up on a player (goalie rows have wins/losses too)
PLAYER_KEYS = {"playerid", "playername", "firstname", "lastname", "jersey", "birthdate", "dob",
               "position", "positions", "hometown", "playerfirstname", "playerlastname"}
STANDINGS_KEYS = {"wins", "losses", "w", "l", "otl", "sol", "ties", "gf", "ga", "streak", "rank"}


def _looks_like_players(rows: list[dict], raw: list[dict] | None = None) -> bool:
    if len(rows) < 2:
        return False
    if raw:
        standings = sum(1 for r in raw[:30]
                        if len(STANDINGS_KEYS & set(_flatten(r))) >= 2 and not (PLAYER_KEYS & set(_flatten(r))))
        if standings >= len(raw[:30]) / 2:
            return False
    hits = 0
    for r in rows[:30]:
        has_name = "name" in r or ("first" in r and "last" in r)
        has_stat = bool({"gp", "pts", "sv_pct", "gaa", "birth_year", "dob"} & r.keys())
        hits += has_name and has_stat
    return hits / min(len(rows), 30) >= 0.6


def find_player_lists(data, raw_out: list | None = None) -> list[list[dict]]:
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
                    if raw_out is not None:
                        raw_out.append(dicts[0])
                    return
            for x in node:
                walk(x, depth + 1)
        elif isinstance(node, dict):
            col_rows = _columnar_rows(node)
            if col_rows:
                rows = [json_row_values(x) for x in col_rows]
                if _looks_like_players(rows, col_rows):
                    found.append(rows)
                    if raw_out is not None:
                        raw_out.append(col_rows[0])
                    return
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
    # signed URLs break if we touch them
    if any(re.search(r"sig|signature|hash|token|md5", k, re.I) for k, _ in q):
        return None
    changed = False
    new = []
    for k, v in q:
        if k.lower() in LIMIT_PARAMS and v.isdigit() and 0 < int(v) < 1000:
            new.append((k, "2000"))
            changed = True
        elif k.lower() in OFFSET_PARAMS and v.isdigit():
            new.append((k, "0"))  # widened request should start from the top
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


OFFSET_PARAMS = ("offset", "start", "first", "skip")
PAGE_PARAMS = ("page", "pagenumber", "page_number", "pageno")
MAX_FEED_PAGES = 150


def _feed_pages(feed_url: str):
    """Yield URLs for the following pages of an offset/page-numbered feed."""
    parts = urlsplit(feed_url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    keys = {k.lower(): k for k, _ in q}
    vals = dict(q)
    size = None
    for name in LIMIT_PARAMS:
        if name in keys and vals[keys[name]].isdigit() and int(vals[keys[name]]) > 0:
            size = (keys[name], int(vals[keys[name]]))
            break
    off = next((keys[n] for n in OFFSET_PARAMS if n in keys and vals[keys[n]].isdigit()), None)
    pg = next((keys[n] for n in PAGE_PARAMS if n in keys and vals[keys[n]].isdigit()), None)
    if off and size:
        start = 0
        step = size[1]
        for i in range(MAX_FEED_PAGES):
            yield urlunsplit(parts._replace(query=urlencode([(k, str(start + i * step) if k == off else v)
                                                             for k, v in q])))
    elif pg:
        first = 0 if vals[pg] == "0" else 1
        for i in range(MAX_FEED_PAGES):
            yield urlunsplit(parts._replace(query=urlencode([(k, str(first + i) if k == pg else v)
                                                             for k, v in q])))


def page_through_feeds(page, feeds: list) -> list:
    """Stats widgets load players 20-50 at a time as you scroll. Ask for every
    page from inside the browser (so bot-protection cookies come along),
    slowly, until a page comes back with no players."""
    fetch_js = """async (u) => {
        const r = await fetch(u, {credentials: 'include'});
        return [r.status, await r.text()];
    }"""
    have = {u for u, _ in feeds}
    extra = []
    tried_bases = set()
    for feed_url, data in list(feeds):
        if not players_from_json(data, cfg={"name": "x"}, source_url=""):
            continue
        base = re.sub(r"(offset|start|first|skip|page\w*)=\d+", "", feed_url, flags=re.I)
        if base in tried_bases:
            continue
        tried_bases.add(base)
        log.debug("paging through %s", feed_url)
        for u in _feed_pages(feed_url):
            if u in have:
                continue
            status, text = 0, ""
            for attempt in range(3):
                try:
                    status, text = page.evaluate(fetch_js, u)
                except Exception:
                    status = 0
                if status == 200:
                    break
                page.wait_for_timeout(3000 * (attempt + 1))
            if status != 200:
                log.info("  blocked while paging (status %s) at %s", status, u[:120])
                break
            data = loads_loose(text)
            if data is None or not players_from_json(data, cfg={"name": "x"}, source_url=""):
                log.debug("paging %s ended at %s (no players in response: %s)", feed_url, u, text[:120])
                break
            have.add(u)
            extra.append((u, data))
            page.wait_for_timeout(1200)
    if extra:
        log.debug("pulled %d extra feed pages", len(extra))
    return extra


# what the last rendered page loaded, for --probe
LAST_LOAD: dict = {"requests": [], "frames": []}


def load(url: str, render: bool) -> tuple[str, list[tuple[str, object]]]:
    """Returns (html, [(feed_url, parsed_json), ...]).

    HTML fragments loaded in the background (common on SportsEngine-style
    sites, where the stats tab is fetched after the page loads) get appended
    to the html so their tables are read too."""
    if not render:
        return http.get(url).text, []

    from playwright.sync_api import sync_playwright

    feeds: list[tuple[str, object]] = []
    fragments: list[str] = []
    requests_seen: list[tuple] = []

    def on_response(resp):
        try:
            rtype = resp.request.resource_type
            ctype = resp.headers.get("content-type", "")
            if rtype in ("xhr", "fetch", "document", "script"):
                requests_seen.append((rtype, resp.status, ctype.split(";")[0], resp.url))
            if rtype not in ("xhr", "fetch", "script", "document"):
                return
            if not any(t in ctype for t in ("json", "javascript", "text/plain", "text/html")):
                return
            text = resp.text()
            data = loads_loose(text)
            if data is not None:
                feeds.append((resp.url, data))
            elif rtype in ("xhr", "fetch") and "<table" in text.lower():
                fragments.append(text)
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None,
                                    args=["--disable-blink-features=AutomationControlled"])
        # look like a normal Chrome of the same version (bot checks compare these)
        major = browser.version.split(".")[0]
        ua = re.sub(r"Chrome/[\d.]+", f"Chrome/{major}.0.0.0", http.UA)
        page = browser.new_page(user_agent=ua, viewport={"width": 1400, "height": 1000}, locale="en-US")
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.on("response", on_response)
        # some league sites never go quiet (ads, video) - read what's there
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception:
            log.debug("%s never finished loading, reading it anyway", url)
            page.wait_for_timeout(5000)
        # scroll down so lazy-loaded widgets / iframes actually load
        for _ in range(8):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(500)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        feeds += page_through_feeds(page, feeds)
        html = page.content()
        frames = []
        # stats widgets often live in iframes
        for frame in page.frames[1:]:
            frames.append(frame.url)
            try:
                html += frame.content()
            except Exception:
                pass
        browser.close()
    LAST_LOAD.update(requests=requests_seen, frames=frames)
    return html + "".join(fragments), feeds


GAMESHEET_SEASON = re.compile(r"gamesheetstats\.com/seasons/(\d+)")
GAMESHEET_API = "https://gamesheetstats.com/api/{kind}/standings/{season}"


def gamesheet_direct(url: str, cfg: dict, goalie: bool) -> list[Player]:
    """GameSheet stats pages show 20 players at a time behind bot protection,
    but the API behind them answers plain requests and will hand over a
    whole season at once."""
    m = GAMESHEET_SEASON.search(url)
    if not m:
        return []
    season = m.group(1)
    kind = "goalies" if goalie else "players"
    sort = "gaa" if goalie else "-pts"
    headers = {"Referer": f"https://gamesheetstats.com/seasons/{season}/{kind}",
               "Accept": "application/json"}
    out: list[Player] = []
    offset, limit = 0, 500
    for _ in range(40):
        try:
            r = http.session().get(GAMESHEET_API.format(kind=kind, season=season),
                                   params={"limit": limit, "offset": offset, "sort": sort},
                                   headers=headers, timeout=30)
        except Exception as e:
            log.info("  GameSheet API failed: %s", e)
            break
        if r.status_code != 200:
            log.info("  GameSheet API -> %s, falling back to the browser", r.status_code)
            break
        ps = players_from_json(loads_loose(r.text), cfg=cfg, source_url=url, goalie_hint=goalie)
        if not ps:
            break
        out += ps
        if len(ps) < limit:
            break
        offset += limit
    if out:
        log.info("  GameSheet season %s: %d %s", season, len(out), kind)
    return out


def scrape_url(url: str, cfg: dict, goalie=False) -> list[Player]:
    direct = gamesheet_direct(url, cfg, goalie)
    if direct:
        return direct
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
            if "hockeytech.com" in feed_url:
                from .hockeytech import enrich_from_rosters
                try:
                    enrich_from_rosters(from_feed, feed_url)
                except Exception as e:
                    log.debug("couldn't load HockeyTech rosters: %s", e)
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
                          "pos_raw", "height", "weight", "team", "committed", "email"):
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


def _shape(node, depth=0) -> str:
    """Short outline of a JSON structure, for probe output."""
    if depth > 3:
        return "..."
    if isinstance(node, dict):
        items = list(node.items())[:12]
        return "{" + ", ".join(f"{k}: {_shape(v, depth + 1)}" for k, v in items) + (", ..." if len(node) > 12 else "") + "}"
    if isinstance(node, list):
        return f"[{len(node)} x {_shape(node[0], depth + 1) if node else '-'}]"
    return type(node).__name__


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
            raw = []
            find_player_lists(data, raw)
            if raw:
                print("    first record: " + json.dumps(raw[0], default=str)[:700])
    for feed_url, data in feeds:
        if not players_from_json(data, cfg=cfg, source_url=url) and "challenge" not in feed_url:
            print(f"  no players recognized in: {feed_url[:160]}")
            print("    shape: " + _shape(data))
            print("    start: " + json.dumps(data, default=str)[:1200])
    print(f"  -> players read from feeds: {total_feed}\n")

    nxt = next_link(html, url)
    print(f"Next page link: {nxt or 'none'}\n")

    if render:
        print(f"Embedded frames: {len(LAST_LOAD['frames'])}")
        for f in LAST_LOAD["frames"]:
            print(f"  {f[:200]}")
        print()
        reqs = [r for r in LAST_LOAD["requests"] if r[0] in ("xhr", "fetch", "document")]
        print(f"Data requests the page made: {len(reqs)}")
        for rtype, status, ctype, rurl in reqs[:60]:
            print(f"  {rtype:<8} {status} {ctype:<24} {rurl[:170]}")
        print()

    links = []
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        if re.search(r"stat|leader|scor|skater|goalie|roster|player", label + " " + a["href"], re.I):
            full = urljoin(url, a["href"])
            if full not in [l[1] for l in links] and not full.startswith("javascript"):
                links.append((label[:40], full))
    print(f"Stats-looking links on the page: {len(links)}")
    for label, full in links[:40]:
        print(f"  {label:<40} {full[:160]}")
    print()

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
