"""Daily email with the new prospects."""

from __future__ import annotations

import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _stat_line(p: dict) -> str:
    if p.get("position") == "G":
        bits = [f"{p['gp']} GP" if p.get("gp") else "",
                f"{p['gaa']:.2f} GAA" if p.get("gaa") is not None else "",
                f"{p['sv_pct']:.3f} SV%" if p.get("sv_pct") is not None else ""]
    else:
        bits = [f"{p['gp']} GP" if p.get("gp") else "",
                f"{p['g']}-{p['a']}-{p['pts']}" if p.get("pts") is not None else "",
                f"{p['ppg']} PPG" if p.get("ppg") is not None else ""]
    return " | ".join(b for b in bits if b)


def build(payload: dict, size: int, site_url: str = "") -> tuple[str, str, str]:
    new = [p for p in payload["players"] if p.get("new")][:size]
    subject = f"{len(new)} new hockey prospects - {payload['generated']}"
    lines, rows = [], []
    for p in new:
        who = f"{p['name']} ({p.get('position') or '?'}, {p.get('birth_year') or '?'})"
        where = f"{p.get('team', '')} - {p.get('league', '')}"
        lines.append(f"[{p['score']}] {who} | {where} | {_stat_line(p)}")
        link = p.get("profile_url") or ""
        name = html.escape(p["name"])
        name_html = f'<a href="{html.escape(link)}">{name}</a>' if link else name
        rows.append(
            f"<tr><td>{p['score']}</td><td>{name_html}</td><td>{p.get('position') or ''}</td>"
            f"<td>{p.get('birth_year') or ''}</td><td>{html.escape(p.get('team', ''))}</td>"
            f"<td>{html.escape(p.get('league', ''))}</td><td>{_stat_line(p)}</td>"
            f"<td>{html.escape('; '.join(p.get('notes', [])))}</td></tr>"
        )
    failed = [s for s in payload.get("sources", []) if not s["ok"] and not s.get("setup")]
    fail_txt = "".join(f"\n  - {s['name']}: {s['error']}" for s in failed)
    footer = f"\nDashboard: {site_url}\n" if site_url else ""
    text = (f"{len(new)} new prospects today ({payload['count']} total on the board).\n\n"
            + "\n".join(lines) + footer
            + (f"\nSources that failed:{fail_txt}\n" if failed else ""))
    html_body = (
        f"<p><b>{len(new)}</b> new prospects today ({payload['count']} total on the board)."
        + (f' <a href="{html.escape(site_url)}">Open dashboard</a>' if site_url else "") + "</p>"
        '<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;font:13px sans-serif">'
        "<tr><th>Score</th><th>Name</th><th>Pos</th><th>YOB</th><th>Team</th><th>League</th>"
        "<th>Stats</th><th>Notes</th></tr>" + "".join(rows) + "</table>"
        + (f"<p>Sources that failed:<br>{'<br>'.join(html.escape(s['name'] + ': ' + s['error']) for s in failed)}</p>" if failed else "")
    )
    return subject, text, html_body


def send(payload: dict, size: int) -> bool:
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("DIGEST_TO")
    if not host or not to:
        print("SMTP_HOST / DIGEST_TO not set, skipping email")
        return False
    if not payload.get("new_count"):
        print("no new prospects today, skipping email")
        return False
    user = os.environ.get("SMTP_USER", "")
    subject, text, html_body = build(payload, size, os.environ.get("SITE_URL", ""))
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ.get("DIGEST_FROM", user)
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        if user:
            s.login(user, os.environ.get("SMTP_PASS", ""))
        s.sendmail(msg["From"], [a.strip() for a in to.split(",")], msg.as_string())
    print(f"sent digest to {to}")
    return True
