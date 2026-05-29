#!/usr/bin/env python3
"""
serve_local.py — Option A: a PRIVATE, local-only live dashboard over your REAL data.

Runs the machine (audit + optimizer) against your configs/bets, writes the feed to
a gitignored `private/` dir, drops a copy of the dashboard next to it, and serves it
bound to 127.0.0.1 only. Nothing is published; nothing is committed; only this
machine can reach it.

This is the safe home for real betting data: it never leaves your computer.

Usage (real data):
    python serve_local.py --configs live/ --bets data/data-panel-export-LATEST.csv

Usage (sample demo):
    python serve_local.py

Then open the printed http://127.0.0.1:PORT/ URL. Ctrl+C to stop.
"""
from __future__ import annotations
import argparse
import functools
import http.server
import json
import os
import shutil
import socketserver
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import run_machine  # reuse get_audit / get_optimizer  # noqa: E402

DASHBOARD_SRC = REPO / "docs" / "index.html"


def build_feed(configs: str, bets: str, bankroll: float) -> dict:
    audit = run_machine.get_audit(configs)
    optimizer = run_machine.get_optimizer(configs, bets, bankroll)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"configs": configs, "bets": bets},
        "audit": audit,
        "optimizer": optimizer,
        "community": {"status": "not_connected",
                      "note": "Connect the PropSniper community / OddsPapi consensus source to populate this panel.",
                      "metrics": []},
    }


def main():
    ap = argparse.ArgumentParser(description="Private local-only live dashboard (Option A)")
    ap.add_argument("--configs", default="configs/sample_live", help="config dir (use live/ for real data)")
    ap.add_argument("--bets", default="optimizer/sample_bets.csv", help="bet export CSV (use data/... for real)")
    ap.add_argument("--bankroll", type=float, default=10000.0)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--out-dir", default=str(REPO / "private"))
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    is_real = args.configs.replace("\\", "/").startswith("live") or args.bets.replace("\\", "/").startswith("data")
    if is_real:
        print("=" * 70)
        print("PRIVATE MODE — real data. Served to 127.0.0.1 only; never committed.")
        print(f"  output dir: {out}  (gitignored)")
        print("=" * 70)

    feed = build_feed(args.configs, args.bets, args.bankroll)
    (out / "feed.json").write_text(json.dumps(feed, indent=2))
    shutil.copyfile(DASHBOARD_SRC, out / "index.html")

    a = feed["audit"]
    acts = (feed.get("optimizer") or {}).get("summary", {}).get("actions", {})
    print(f"feed built: audit={a.get('verdict')} cards={a.get('cards')} optimizer={acts}")
    print(f"serving {out} at http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    print("Refresh the page (or it polls every 60s) after re-running to see updates.")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    # Bind to loopback ONLY — not reachable from the network.
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
