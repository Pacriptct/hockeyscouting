"""python -m scout [--email] [--only NAME] [-v]
python -m scout --probe URL [--no-render]"""

import argparse
import logging
import os

from . import digest
from .pipeline import load_config, run


def main():
    ap = argparse.ArgumentParser(description="Pull prospects from every source and update the dashboard")
    ap.add_argument("--email", action="store_true", help="send the daily digest email after the run")
    ap.add_argument("--only", help="only run sources whose name contains this")
    ap.add_argument("--probe", metavar="URL", help="show what can be read from a stats page, then exit")
    ap.add_argument("--no-render", action="store_true", help="with --probe: don't run the page's JavaScript")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    if args.probe:
        from .sources.league_site import probe
        probe(args.probe, render=not args.no_render)
        return

    cfg = load_config()
    if args.only:
        cfg["sources"] = [s for s in cfg["sources"] if args.only.lower() in s["name"].lower()]
    payload = run(cfg)
    print(f"{payload['count']} prospects on the board, {payload['new_count']} new today")
    if not os.environ.get("GITHUB_ACTIONS"):
        print("(saved to site/data/prospects.local.json - the dashboard shows it when run locally)")
    if args.email:
        digest.send(payload, int(cfg.get("digest_size", 40)))


if __name__ == "__main__":
    main()
