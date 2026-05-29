#!/usr/bin/env python3
"""
publish_live.py — push a PUBLIC snapshot of the real dashboard to GitHub Pages.

Reads the live engine.db, writes docs/live/{index.html, feed.json}, and (with
--publish) commits + pushes so it's served at:
    https://abovexbl.github.io/propsniper---jacob/live/

NOTE: this makes your real P&L PUBLIC (and lands in git history). That's the
intent here — share with friends. rebet/mgm stay excluded by default.
The dashboard polls feed.json; re-run this to refresh the public numbers.

Usage:
    python publish_live.py                 # build docs/live/ only
    python publish_live.py --publish       # build + git commit + push
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import serve_real  # reuse build_feed (prefers engine.db)  # noqa: E402

OUT = REPO / "docs" / "live"
DASH = REPO / "assets" / "real_dashboard.html"


def main():
    ap = argparse.ArgumentParser(description="Publish the real dashboard publicly (GitHub Pages)")
    ap.add_argument("--base", default=str(Path.home() / "Propsniper"))
    ap.add_argument("--exclude", default=None, help="comma-sep account substrings to hide; default rebet,mgm")
    ap.add_argument("--publish", action="store_true", help="git commit + push docs/live")
    args = ap.parse_args()

    exclude = None if args.exclude is None else [x.strip() for x in args.exclude.split(",") if x.strip()]
    feed = serve_real.build_feed(Path(args.base), exclude)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "feed.json").write_text(json.dumps(feed, indent=2))
    shutil.copyfile(DASH, OUT / "index.html")

    p = feed["portfolio"]
    print(f"built docs/live/  —  through {p.get('data_through')}  "
          f"profit=${p.get('profit'):,.0f}  roi={(p.get('roi') or 0)*100:.1f}%  accounts={p.get('accounts')}")

    if args.publish:
        subprocess.run(["git", "-C", str(REPO), "add", "docs/live/index.html", "docs/live/feed.json"], check=True)
        msg = f"public dashboard snapshot — through {p.get('data_through')} (${p.get('profit'):,.0f})"
        r = subprocess.run(["git", "-C", str(REPO), "commit", "-m", msg], capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" in (r.stdout + r.stderr):
            print("no change to publish")
            return
        subprocess.run(["git", "-C", str(REPO), "push", "mine", "HEAD:main"], check=True)
        print("published. URL (after Pages is enabled): https://abovexbl.github.io/propsniper---jacob/live/")


if __name__ == "__main__":
    main()
