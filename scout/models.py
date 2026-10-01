"""Player record shared by every source."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Optional


def norm_name(name: str) -> str:
    """Lowercase, strip accents/punctuation so 'Jean-Luc O'Neil' == 'jean luc oneil'."""
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    name = name.lower().replace("'", "")
    name = re.sub(r"[^a-z ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def split_name(name: str) -> tuple[str, str]:
    name = (name or "").strip()
    if "," in name:  # "Last, First"
        last, first = [p.strip() for p in name.split(",", 1)]
        return first, last
    parts = name.split()
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


@dataclass
class Player:
    name: str
    team: str = ""
    league: str = ""
    level: str = "Other"
    source: str = ""
    position: str = ""          # F, D, G (raw position kept in pos_raw)
    pos_raw: str = ""
    birth_year: Optional[int] = None
    dob: str = ""
    shoots: str = ""
    height: str = ""
    weight: str = ""
    hometown: str = ""
    nationality: str = ""
    committed: str = ""
    gp: Optional[int] = None
    g: Optional[int] = None
    a: Optional[int] = None
    pts: Optional[int] = None
    pim: Optional[int] = None
    plus_minus: Optional[int] = None
    gaa: Optional[float] = None
    sv_pct: Optional[float] = None
    profile_url: str = ""
    source_url: str = ""
    email: str = ""
    socials: dict = field(default_factory=dict)
    # filled in by the pipeline
    score: float = 0.0
    production_pct: Optional[float] = None
    notes: list = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""

    @property
    def ppg(self) -> Optional[float]:
        if self.gp and self.pts is not None:
            return round(self.pts / self.gp, 2)
        return None

    @property
    def is_goalie(self) -> bool:
        return self.position == "G"

    @property
    def key(self) -> str:
        """Stable id: same name + birth year = same kid, even across leagues."""
        base = norm_name(self.name)
        if self.birth_year:
            base += f"|{self.birth_year}"
        else:
            base += f"|{norm_name(self.team)}"
        return hashlib.sha1(base.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.key
        d["ppg"] = self.ppg
        first, last = split_name(self.name)
        d["first_name"], d["last_name"] = first, last
        return d


def norm_position(raw: str) -> str:
    r = (raw or "").strip().upper()
    if not r:
        return ""
    if r.startswith("G"):
        return "G"
    if r.startswith("D") or r in {"LD", "RD"}:
        return "D"
    return "F"


def to_int(v) -> Optional[int]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in {"", "-", "--", "—"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def to_float(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").rstrip("%")
    if s in {"", "-", "--", "—"}:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f


def parse_birth_year(v) -> Optional[int]:
    if v is None:
        return None
    m = re.search(r"(19|20)\d{2}", str(v))
    if m:
        return int(m.group(0))
    m = re.fullmatch(r"'?(\d{2})", str(v).strip())  # '06
    if m:
        return 2000 + int(m.group(1))
    return None
