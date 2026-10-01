"""Rank players: production vs. their own league, level of the league, age."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from .models import Player

UNKNOWN_AGE_WEIGHT = 0.6
NO_STATS_PRODUCTION = 0.5


def current_season(today: date | None = None) -> str:
    today = today or date.today()
    start = today.year if today.month >= 8 else today.year - 1
    return f"{start}-{start + 1}"


def _percentiles(values: list[float]) -> list[float]:
    """Fraction of the group each value beats (ties split)."""
    n = len(values)
    if n == 1:
        return [0.5]
    out = []
    for v in values:
        below = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v) - 1
        out.append((below + equal / 2) / (n - 1))
    return out


def production(players: list[Player], min_games: int) -> None:
    """Set production_pct on each player, ranked inside league + position group."""
    groups: dict[tuple, list[Player]] = defaultdict(list)
    for p in players:
        if not p.gp or p.gp < min_games:
            continue
        if p.is_goalie:
            if p.sv_pct is None and p.gaa is None:
                continue
            groups[(p.league, "G")].append(p)
        elif p.pts is not None:
            groups[(p.league, "D" if p.position == "D" else "F")].append(p)

    for (_, grp), members in groups.items():
        if grp == "G":
            sv = _percentiles([m.sv_pct or 0 for m in members])
            gaa = _percentiles([-(m.gaa if m.gaa is not None else 9) for m in members])
            for m, a, b in zip(members, sv, gaa):
                m.production_pct = round((a + b) / 2, 3)
        else:
            pct = _percentiles([m.ppg or 0 for m in members])
            for m, v in zip(members, pct):
                m.production_pct = round(v, 3)


def is_committed_elsewhere(p: Player, keep_terms: list[str]) -> bool:
    c = (p.committed or "").strip()
    if not c or c.lower() in {"-", "none", "uncommitted", "n/a", "false", "0", "no", "null"}:
        return False
    return not any(t.lower() in c.lower() for t in keep_terms)


def score_all(players: list[Player], cfg: dict) -> list[Player]:
    min_games = int(cfg.get("min_games", 5))
    years = {int(k): float(v) for k, v in (cfg.get("birth_years") or {}).items()}
    levels = cfg.get("level_weights") or {}
    keep_terms = cfg.get("keep_if_committed_to") or []

    production(players, min_games)

    kept = []
    for p in players:
        p.notes = []
        if is_committed_elsewhere(p, keep_terms):
            continue
        if p.birth_year:
            age_w = years.get(p.birth_year, 0.0)
            if age_w <= 0:
                continue
        else:
            age_w = UNKNOWN_AGE_WEIGHT
            p.notes.append("No birth year listed - check age")

        level_w = float(levels.get(p.level, levels.get("Other", 0.5)))
        prod = p.production_pct if p.production_pct is not None else NO_STATS_PRODUCTION
        if p.production_pct is None:
            if p.gp is not None and p.gp < min_games:
                p.notes.append(f"Only {p.gp} GP so far")
            else:
                p.notes.append("No stats to rank yet")

        p.score = round(100 * level_w * age_w * (0.35 + 0.65 * prod), 1)

        if p.production_pct is not None and p.production_pct >= 0.9:
            what = "save numbers" if p.is_goalie else "PPG"
            p.notes.append(f"Top 10% {what} in {p.league}")
        elif p.production_pct is not None and p.production_pct >= 0.75:
            what = "save numbers" if p.is_goalie else "PPG"
            p.notes.append(f"Top 25% {what} in {p.league}")
        if p.birth_year and years and p.birth_year == min(years):
            p.notes.append("Final junior year - needs a spot next fall")
        if p.committed and p.committed.lower() not in {"false", "0", "no", "null", "none"}:
            p.notes.append(f"Listed commitment: {p.committed}")
        kept.append(p)

    kept.sort(key=lambda p: p.score, reverse=True)
    return kept
