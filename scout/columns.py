"""Turn a row with whatever headers a site uses into a Player."""

from __future__ import annotations

import re

from .models import (
    Player,
    norm_position,
    parse_birth_year,
    to_float,
    to_int,
)

# header (lowercased, stripped of punctuation) -> Player field
ALIASES = {
    "name": ["name", "player", "playername", "player name", "skater", "goalie", "goaltender"],
    "first": ["first", "firstname", "first name", "fname"],
    "last": ["last", "lastname", "last name", "lname"],
    "team": ["team", "teamname", "team name", "club", "tm"],
    "league": ["league", "lg"],
    "pos_raw": ["pos", "position", "p"],
    "birth_year": ["yob", "birth year", "birthyear", "by", "year of birth", "born"],
    "dob": ["dob", "birthdate", "birth date", "date of birth", "bd"],
    "shoots": ["shoots", "catches", "s/c", "s c", "shoots/catches"],
    "height": ["height", "ht"],
    "weight": ["weight", "wt"],
    "hometown": ["hometown", "home town", "birthplace", "birth place", "home", "city"],
    "nationality": ["nationality", "nat", "country"],
    "committed": ["commitment", "committed", "commit", "college"],
    "gp": ["gp", "games", "games played", "g p"],
    "g": ["g", "goals"],
    "a": ["a", "assists", "ast"],
    "pts": ["pts", "points", "tp", "p"],
    "pim": ["pim", "penalty minutes"],
    "plus_minus": ["+/-", "plusminus", "plus minus", "pm"],
    "gaa": ["gaa", "goals against average"],
    "sv_pct": ["sv%", "sv %", "svpct", "save %", "save pct", "save percentage", "pct"],
    "email": ["email", "e mail"],
    "profile_url": ["url", "link", "profile", "ep link", "profile url"],
    "instagram": ["instagram", "ig"],
    "twitter": ["twitter", "x"],
}


def clean_header(h: str) -> str:
    h = (h or "").strip().lower()
    if h in {"+/-", "sv%"}:
        return h
    h = h.replace("%", " %").replace("_", " ")
    h = re.sub(r"[^a-z0-9%+/ ]+", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def map_headers(headers: list[str], goalie_table: bool = False) -> dict[int, str]:
    """Column index -> field. 'P' means points on skater tables, position otherwise."""
    lookup = {}
    for field_name, names in ALIASES.items():
        for n in names:
            lookup.setdefault(clean_header(n), field_name)

    cleaned = [clean_header(h) for h in headers]
    has_pts = any(c in {"pts", "points", "tp"} for c in cleaned)

    out: dict[int, str] = {}
    used: set[str] = set()
    for i, c in enumerate(cleaned):
        if c == "p":
            f = "pos_raw" if has_pts or goalie_table else "pts"
        elif c == "g" and goalie_table:
            continue  # goalie tables use G for goals against or games, skip
        else:
            f = lookup.get(c)
        if f and f not in used:
            out[i] = f
            used.add(f)
    return out


def _shoots(v) -> str:
    """Only L / R - anything else is some other column that got mislabeled."""
    v = (v or "").strip().upper()
    return v[0] if v[:1] in ("L", "R") and (len(v) == 1 or v in ("LEFT", "RIGHT")) else ""


def fix_case(name: str) -> str:
    """VINCENZO CAPANO -> Vincenzo Capano, O'NEIL -> O'Neil, MCDONALD -> McDonald."""
    def word(w: str) -> str:
        out = []
        for x in re.split(r"([-'])", w.lower()):
            x = x.capitalize()
            if re.match(r"^Mc[a-z]", x):
                x = "Mc" + x[2:].capitalize()
            out.append(x)
        return "".join(out)
    return " ".join(word(w) for w in name.split())


def row_to_player(values: dict, *, team="", league="", level="Other",
                  source="", source_url="", goalie=False) -> Player | None:
    """values: field -> raw string."""
    name = (values.get("name") or "").strip()
    if not name and (values.get("first") or values.get("last")):
        name = f"{values.get('first', '').strip()} {values.get('last', '').strip()}".strip()
    name = re.sub(r"\s*\(.*?\)\s*", " ", name)        # drop "(C)", "(A)", "(R)"
    name = re.sub(r"^\s*#?\d+\s+", "", name)          # drop leading jersey number
    name = re.sub(r"\s+", " ", name).strip(" *")
    if not name or name.lower() in {"totals", "total", "team totals"}:
        return None
    if "," in name:  # "Last, First"
        last, first = [x.strip() for x in name.split(",", 1)]
        name = f"{first} {last}".strip()
    if name.isupper() or name.islower():
        name = fix_case(name)

    pos_raw = (values.get("pos_raw") or "").strip()
    if goalie and not pos_raw:
        pos_raw = "G"

    dob = (values.get("dob") or "").strip()
    by = parse_birth_year(values.get("birth_year")) or parse_birth_year(dob)

    socials = {}
    if values.get("instagram"):
        socials["instagram"] = values["instagram"].strip()
    if values.get("twitter"):
        socials["x"] = values["twitter"].strip()

    sv = to_float(values.get("sv_pct"))
    if sv is not None and sv > 1.5:  # 91.5 -> .915
        sv = round(sv / 100, 3)

    return Player(
        name=name,
        team=(values.get("team") or team or "").strip(),
        league=(values.get("league") or league or "").strip(),
        level=level,
        source=source,
        source_url=source_url,
        pos_raw=pos_raw,
        position=norm_position(pos_raw),
        birth_year=by,
        dob=dob,
        shoots=_shoots(values.get("shoots")),
        height=(values.get("height") or "").strip(),
        weight=(values.get("weight") or "").strip(),
        hometown=(values.get("hometown") or "").strip(),
        nationality=(values.get("nationality") or "").strip(),
        committed=(values.get("committed") or "").strip(),
        gp=to_int(values.get("gp")),
        g=None if goalie else to_int(values.get("g")),
        a=None if goalie else to_int(values.get("a")),
        pts=None if goalie else to_int(values.get("pts")),
        pim=to_int(values.get("pim")),
        plus_minus=to_int(values.get("plus_minus")),
        gaa=to_float(values.get("gaa")),
        sv_pct=sv,
        email=(values.get("email") or "").strip(),
        profile_url=(values.get("profile_url") or "").strip(),
        socials=socials,
    )
