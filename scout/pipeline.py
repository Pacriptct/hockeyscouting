"""Daily run: pull every source, rank, merge with history, write the site data."""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import date, timedelta
from pathlib import Path

import yaml

from .models import Player
from .scoring import current_season, score_all
from .sources import NEEDS_URL, SOURCES

log = logging.getLogger("scout")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"
DROP_AFTER_DAYS = 45
LOCAL_FILE = "prospects.local.json"  # forget players we haven't seen on any source in this long


def load_config(path: Path | None = None) -> dict:
    with (path or ROOT / "config.yaml").open() as f:
        return yaml.safe_load(f)


def _filled(p: Player) -> int:
    return sum(1 for v in p.to_dict().values() if v not in (None, "", [], {}))


def dedupe(players: list[Player]) -> list[Player]:
    """Same kid from two sources (EP + league site): keep the fuller record,
    fill its blanks from the other one."""
    by_key: dict[str, Player] = {}
    for p in players:
        k = p.key
        if k not in by_key:
            by_key[k] = p
            continue
        a, b = sorted([by_key[k], p], key=_filled, reverse=True)
        for f in ("birth_year", "dob", "shoots", "height", "weight", "hometown", "nationality",
                  "committed", "email", "profile_url", "position", "pos_raw"):
            if not getattr(a, f) and getattr(b, f):
                setattr(a, f, getattr(b, f))
        a.socials = {**b.socials, **a.socials}
        if b.source not in a.source:
            a.source = f"{a.source}, {b.source}"
        by_key[k] = a
    return list(by_key.values())


def collect(cfg: dict, ctx: dict) -> tuple[list[Player], list[dict]]:
    players: list[Player] = []
    report = []
    for src in cfg.get("sources", []):
        if not src.get("enabled", True):
            continue
        fn = SOURCES.get(src.get("type"))
        entry = {"name": src["name"], "type": src.get("type"), "count": 0, "ok": False, "error": "",
                 "setup": False}
        if src.get("type") in NEEDS_URL and not src.get("url"):
            entry.update(setup=True, error="Needs a stats page URL in config.yaml")
        elif not fn:
            entry["error"] = f"unknown source type {src.get('type')}"
        else:
            try:
                got = fn(src, ctx)
                players += got
                entry.update(count=len(got), ok=True)
                log.info("%-28s %5d players", src["name"], len(got))
            except Exception as e:  # one bad site shouldn't kill the run
                entry["error"] = f"{type(e).__name__}: {e}"
                log.warning("%-28s FAILED %s", src["name"], entry["error"])
                log.debug(traceback.format_exc())
        report.append(entry)
    return players, report


def merge_history(players: list[Player], old: dict, today: str) -> list[dict]:
    rows = []
    seen = set()
    for p in players:
        d = p.to_dict()
        prev = old.get(d["id"])
        d["first_seen"] = prev["first_seen"] if prev else today
        d["last_seen"] = today
        d["new"] = d["first_seen"] == today
        rows.append(d)
        seen.add(d["id"])
    # keep guys who dropped off a source for a while (site down, etc.)
    cutoff = (date.fromisoformat(today) - timedelta(days=DROP_AFTER_DAYS)).isoformat()
    for pid, prev in old.items():
        if pid not in seen and prev.get("last_seen", "") >= cutoff:
            prev = dict(prev, new=False)
            rows.append(prev)
    rows.sort(key=lambda d: d.get("score", 0), reverse=True)
    return rows


def run(cfg: dict | None = None, today: date | None = None, out_dir: Path = DATA) -> dict:
    cfg = cfg or load_config()
    today = today or date.today()
    season = cfg.get("season")
    if not season or season == "auto":
        season = current_season(today)
    ctx = {"season": season, "root": str(ROOT), "today": today.isoformat()}

    raw, report = collect(cfg, ctx)
    players = score_all(dedupe(raw), cfg)

    out_dir.mkdir(parents=True, exist_ok=True)
    # Only the GitHub run writes the real file. Runs on my own computer go to
    # an ignored file so they never clash with the daily update on git pull.
    official = out_dir / "prospects.json"
    pfile = official if os.environ.get("GITHUB_ACTIONS") else out_dir / LOCAL_FILE
    old = {}
    src = pfile if pfile.exists() else official
    if src.exists():
        old = {d["id"]: d for d in json.loads(src.read_text()).get("players", [])}

    any_ok = any(r["ok"] and r["count"] for r in report)
    if not any_ok and old:
        log.warning("every source came back empty, keeping yesterday's list")
        rows = [dict(d, new=False) for d in old.values()]
    else:
        rows = merge_history(players, old, today.isoformat())
    rows = rows[: int(cfg.get("max_results", 10000))]

    payload = {
        "generated": today.isoformat(),
        "season": season,
        "program": cfg.get("program", {}),
        "count": len(rows),
        "new_count": sum(1 for r in rows if r.get("new")),
        "sources": report,
        "players": rows,
    }
    pfile.write_text(json.dumps(payload, separators=(",", ":")))
    return payload
