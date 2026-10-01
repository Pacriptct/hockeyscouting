from . import csv_folder, eliteprospects, hockeytech, html_table

SOURCES = {
    "html_table": html_table.fetch,
    "hockeytech": hockeytech.fetch,
    "eliteprospects": eliteprospects.fetch,
    "csv_folder": csv_folder.fetch,
}
