# Wittenberg Hockey Recruiting

Recruiting board for Wittenberg men's hockey (ACHA D2, moving to NCAA D3).
Every morning it pulls players straight from the league websites (EHL,
NAHL, NCDC, NA3HL, USPHL, 18U AAA leagues, Canadian Jr A...), ranks them, puts them on a
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
- **Instagram**: each player has an IG handle box. Don't know it yet?
  *Google his IG*, *Search on Instagram* and *Team's IG* (players get
  tagged in team posts) help find it. Then **Copy DM & open chat** copies
  the DM, opens his DMs in the Instagram app (ig.me link) and marks him
  DM'd with today's date. Paste and send.
- **Follow-ups**: tick *Follow-ups due* on the board to see guys you DM'd
  5+ days ago who haven't been moved to *Replied*. *Copy follow-up* sends
  the second message and logs it.
- **Copy email / Open in mail app**: same idea for email.
- **Status**: Not reviewed → Checked → Reached out → Talking → Visit → Pass.
- **My pipeline**: everyone you've marked, by status.
- **Templates**: edit the DM, follow-up, email, coach name/email/phone.
- **Export CSV**: whatever's currently filtered, with your statuses + notes.

Statuses and notes are saved in the browser. Use *Templates → Download
backup* now and then, and *Restore backup* to move them to another device.

## Setup

1. **Turn on Pages**: repo Settings → Pages → Source: **GitHub Actions**.
2. **Add secrets** (Settings → Secrets and variables → Actions):

   | Secret | What |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | your Gmail address |
   | `SMTP_PASS` | a Gmail **app password** (Google account → Security → App passwords) |
   | `DIGEST_TO` | where the daily email goes (comma-separate for more than one) |

3. **Fill in league URLs** (see below), then **run it**: Actions tab →
   *Daily prospect pull* → *Run workflow*. After that it runs every morning
   on its own.
4. Fill in coach name/email/phone in `config.yaml` (or in the dashboard's
   Templates tab).

## Sources

Every league is listed in `config.yaml` with its level (Tier I/II/III,
18U AAA, Canadian Jr A). Each one just needs the URL of that league's
**full player stats page**. Leagues without a URL show as *Needs URL* on
the Sources tab; leagues whose URL broke show as *Failed* there and in the
email. Everything else keeps running either way.

The scraper loads the page in a real browser and reads players from:
- any stats table on the page
- the data the page loads in the background (how most JavaScript stats
  widgets work: HockeyTech, GameSheet, etc.), asking for the full list
  instead of one page at a time when it can
- "Next" page links

### Adding / fixing a league URL

1. Go to the league's site and open the full player stats (all players,
   not the top-10 leaders box). Copy the address.
2. Actions tab → **Probe a stats page** → *Run workflow* → paste the URL.
3. Open the run's log. It lists the tables and data feeds it found and a
   sample of players. If the sample looks right, the URL works.
4. Put the URL in `config.yaml` under that league and push.

If the league has separate skater and goalie pages, use `url` for skaters
and `goalie_url` for goalies. Divisions on separate pages: make `url` a
list. SportsEngine / GameSheet stats URLs change every season, so redo this
each fall.

If Probe says **HockeyTech feed!** with a `client_code` and `key`, you
can switch that league to `type: hockeytech` (example in `config.yaml`),
which pulls the whole league in one request.

### Other sources

- **`csv_folder`**: every CSV in `imports/`. Use it for prep rosters,
  showcase lists, lists from other coaches. See `imports/README.md`.

Locally:

```
pip install -r requirements-dev.txt
python -m playwright install chromium
python -m scout --probe "https://league-site/stats"   # test a URL
python -m scout --only "EHL" -v                       # run one league
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
python -m scout                 # full run, writes site/data/prospects.local.json
cd site && python -m http.server # dashboard at http://localhost:8000
```
