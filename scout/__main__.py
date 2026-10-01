"""python -m scout [--email] [--verbose]"""

import argparse
import logging

from . import digest
from .pipeline import load_config, run


def main():
    ap = argparse.ArgumentParser(description="Pull prospects from every source and update the dashboard")
    ap.add_argument("--email", action="store_true", help="send the daily digest email after the run")
    ap.add_argument("--only", help="only run sources whose name contains this")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    cfg = load_config()
    if args.only:
        cfg["sources"] = [s for s in cfg["sources"] if args.only.lower() in s["name"].lower()]
    payload = run(cfg)
    print(f"{payload['count']} prospects on the board, {payload['new_count']} new today")
    if args.email:
        digest.send(payload, int(cfg.get("digest_size", 40)))


if __name__ == "__main__":
    main()
