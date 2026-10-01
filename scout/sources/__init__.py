from . import csv_folder, hockeytech, league_site

SOURCES = {
    "league_site": league_site.fetch,
    "html_table": league_site.fetch,
    "hockeytech": hockeytech.fetch,
    "csv_folder": csv_folder.fetch,
}

# source types that need a `url` before they can run
NEEDS_URL = {"league_site", "html_table"}
