# Wittenberg Hockey Recruiting

Recruiting board for Wittenberg men's hockey (ACHA D2, moving to NCAA D3).
Every morning it pulls players from Elite Prospects and league stats sites
(EHL, NAHL, NA3HL, USPHL, prep, 18U AAA...), ranks them, puts them on a
dashboard and emails a list of the new names. From the dashboard I check
each guy out, mark him, and copy a ready-to-send email or DM.

## How it works

```
GitHub Actions (daily)
  └─ python -m scout
       ├─ pulls every source in config.yaml
       ├─ merges duplicates (same name + birth year)
       ├─ drops guys who are too old or already committed
       ├─ ranks the rest
       ├─ writes site/data/prospects.json
       └─ emails the new names
  └─ publishes site/ to GitHub Pages
```

**Score (0-100)** = league level × birth year weight × production.
Production is where a player's points per game ranks inside his own league
and position (forwards vs. forwards, D vs. D). Goalies are ranked on SV% and
GAA. All the weights are in `config.yaml`.

## Dashboard

- **Board**: everyone, sorted by score. Filter by position, level, league,
  birth year, status, or "new today". Click a player for stats, links
  (profile, Elite Prospects, Instagram/X/Google searches, team staff) and
  the outreach buttons.
- **Copy email / Copy DM / Open in mail app**: fills in the template with
  his name, team, league and position.
- **Status**: Not reviewed → Checked → Reached out → Talking → Visit → Pass.
- **My pipeline**: everyone you've marked, by status.
- **Templates**: edit the email + DM, coach name/email/phone.
- **Export CSV**: whatever's currently filtered, with your statuses + notes.

Statuses and notes are saved in the browser. Use *Templates → Download
backup* now and then, and *Restore backup* to move them to another device.

## Setup

1. **Turn on Pages**: repo Settings → Pages → Source: **GitHub Actions**.
2. **Add secrets** (Settings → Secrets and variables → Actions):

   | Secret | What |
   |---|---|
   | `EP_API_KEY` | Elite Prospects API key (optional, see below) |
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | your Gmail address |
   | `SMTP_PASS` | a Gmail **app password** (Google account → Security → App passwords) |
   | `DIGEST_TO` | where the daily email goes (comma-separate for more than one) |

3. **Run it**: Actions tab → *Daily prospect pull* → *Run workflow*. After
   that it runs every morning on its own.
4. Fill in coach name/email/phone in `config.yaml` (or in the dashboard's
   Templates tab).

## Sources

All in `config.yaml`. Each has `enabled: true/false`. If one site is down or
changes its layout, that source fails on its own and shows up red on the
**Sources** tab and in the email; everything else still runs.

- **`eliteprospects`**: official EP API. EP doesn't allow scraping their
  site, so this needs an API key. `league` is the slug from the EP URL
  (`eliteprospects.com/league/<slug>`), double check these against EP.
  No key? Skip it and use CSV exports (below).
- **`html_table`**: any league page that shows stats in a table (EHL,
  USPHL, NA3HL, prep leagues, 18U leagues...). Set `url` to the stats page
  (or a list of URLs, e.g. one per division), `goalie_url` for goalies, and
  `render: true` if the table only appears after the page's JavaScript runs.
- **`hockeytech`**: leagues on HockeyTech (lscluster.hockeytech.com). Needs
  the league's public `key` and `client_code`, found by opening the league's
  stats page, viewing source / network tab and searching for `key=`.
- **`csv_folder`**: every CSV in `imports/`. Good for EP exports, showcase
  rosters, lists from other coaches. See `imports/README.md`.

### Adding a league

Copy a block in `config.yaml`, change the name/url/league/level, push. To
test just that source locally:

```
pip install -r requirements-dev.txt
python -m playwright install chromium
python -m scout --only "EHL" -v
```

## Tuning

- `birth_years`: who we're recruiting and how much each year counts.
  Update every summer (bump everything +1).
- `level_weights`: how strong each level is.
- `keep_if_committed_to`: committed players get dropped unless the
  commitment mentions one of these (default keeps ACHA commits).
- `min_games`: GP needed before production counts.

## Recruiting rules

Once Wittenberg is officially NCAA D3 (including the provisional years),
contact with prospects falls under NCAA D3 recruiting rules. Check with
compliance on contact timing and what you can say about the move before
sending messages under the NCAA banner.

## Local dev

```
pip install -r requirements-dev.txt
pytest
python -m scout                 # full run, writes site/data/prospects.json
cd site && python -m http.server # dashboard at http://localhost:8000
```
