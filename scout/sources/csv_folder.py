"""Pull in every .csv in a folder (EP exports, showcase lists, coach lists...).

Headers just need to be recognizable: Name (or First/Last), Team, League,
Pos, YOB/DOB, GP, G, A, PTS, GAA, SV%, Email, Instagram, etc. A `Level`
column overrides the source level for that row.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..columns import map_headers, row_to_player
from ..models import Player


def read_csv(path: Path, cfg: dict) -> list[Player]:
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        if not headers:
            return out
        low = [h.strip().lower() for h in headers]
        level_idx = low.index("level") if "level" in low else None
        cols = map_headers(headers)
        for row in reader:
            if not any(c.strip() for c in row):
                continue
            values = {f: row[i] for i, f in cols.items() if i < len(row)}
            level = cfg.get("level", "Other")
            if level_idx is not None and level_idx < len(row) and row[level_idx].strip():
                level = row[level_idx].strip()
            goalie = (values.get("pos_raw") or "").strip().upper().startswith("G")
            p = row_to_player(values, level=level, source=f"{cfg['name']}: {path.name}",
                              goalie=goalie)
            if p:
                out.append(p)
    return out


def fetch(cfg: dict, ctx: dict) -> list[Player]:
    folder = Path(ctx["root"]) / cfg.get("path", "imports")
    out: list[Player] = []
    for path in sorted(folder.glob("*.csv")):
        out += read_csv(path, cfg)
    return out
