#!/usr/bin/env python3
"""
serve_real.py — PRIVATE dashboard over your REAL PropSniper reports.

Reads your engine's report CSVs (C:\\Users\\<you>\\Propsniper by default), builds
the account -> venue -> market feed, and serves the drill-down dashboard bound to
127.0.0.1 ONLY, behind an optional server-side password. Nothing is published or
committed — real betting data never leaves this machine.

Usage:
    set PROPSNIPER_DASH_PW=your-password
    python serve_real.py                          # base = ~/Propsniper
    python serve_real.py --base "C:/Users/above/Propsniper" --port 8800

Then open the printed http://127.0.0.1:PORT/ (user: propsniper).
Re-run (or it auto-rebuilds each poll if you wire a scheduler) to refresh.
"""
from __future__ import annotations
import argparse
import base64
import functools
import hmac
import http.server
import json
import os
import shutil
import socketserver
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "integrations"))
import propsniper_reports as pr   # CSV-report fallback  # noqa: E402
import propsniper_db as pdb        # engine.db (export history)  # noqa: E402
import propsniper_remote as premote  # LIVE local API  # noqa: E402

DASHBOARD_SRC = REPO / "assets" / "real_dashboard.html"
AUTH_USER = "propsniper"


def build_feed(base, exclude):
    """Source priority: LIVE local API > engine.db (export) > report CSVs."""
    dbp = Path(base) / "engine" / "engine.db"
    try:
        if premote.available():
            return premote.load_remote_feed(exclude=exclude, db_path=str(dbp) if dbp.exists() else None)
    except Exception as e:
        sys.stderr.write(f"live API unavailable, falling back: {e}\n")
    if dbp.exists():
        return pdb.load_db_feed(dbp, exclude)
    return pr.load_real_feed(base, exclude)


def make_handler(directory, password, base, exclude):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=directory, **k)

        def _authed(self):
            if not password:
                return True
            h = self.headers.get("Authorization", "")
            if not h.startswith("Basic "):
                return False
            try:
                u, _, p = base64.b64decode(h[6:]).decode("utf-8", "replace").partition(":")
            except Exception:
                return False
            return hmac.compare_digest(u, AUTH_USER) and hmac.compare_digest(p, password)

        def _challenge(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="PropSniper Private"')
            self.end_headers()
            self.wfile.write(b"Authentication required.")

        def _rebuild_feed(self):
            # Rebuild the feed on every request for /feed.json so the dashboard
            # reflects the latest reports without a restart.
            feed = build_feed(base, exclude)
            (Path(directory) / "feed.json").write_text(json.dumps(feed, indent=2))

        def do_GET(self):
            if not self._authed():
                return self._challenge()
            if self.path.split("?")[0].endswith("/feed.json"):
                try:
                    self._rebuild_feed()
                except Exception as e:
                    sys.stderr.write(f"feed rebuild error: {e}\n")
            return super().do_GET()

        def do_HEAD(self):
            if not self._authed():
                return self._challenge()
            return super().do_HEAD()

        def log_message(self, *a):
            pass

    return H


def main():
    ap = argparse.ArgumentParser(description="Private real-data PropSniper dashboard (localhost only)")
    ap.add_argument("--base", default=str(pr.DEFAULT_BASE), help="PropSniper reports base dir")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--out-dir", default=str(REPO / "private"))
    ap.add_argument("--password", default=os.environ.get("PROPSNIPER_DASH_PW"))
    ap.add_argument("--exclude", default=None,
                    help="comma-sep account substrings to hide (e.g. 'rebet'); empty string shows all. Default hides rebet,mgm.")
    args = ap.parse_args()

    base = Path(args.base)
    exclude = None if args.exclude is None else [x.strip() for x in args.exclude.split(",") if x.strip()]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    feed = build_feed(base, exclude)
    (out / "feed.json").write_text(json.dumps(feed, indent=2))
    shutil.copyfile(DASHBOARD_SRC, out / "index.html")

    p = feed["portfolio"]
    print("=" * 70)
    print(f"PRIVATE real-data dashboard — base: {base}")
    print(f"  accounts={p['accounts']} ({p['winners']} up / {p['losers']} down)  "
          f"profit=${p['profit']:,.0f}  roi={(p['roi'] or 0)*100:.1f}%  bets={p['n']}")
    auth = f"password ON (user '{AUTH_USER}')" if args.password else "NO password (loopback only)"
    print(f"  serving http://127.0.0.1:{args.port}/   [{auth}]   Ctrl+C to stop")
    if not args.password:
        print("  tip: set PROPSNIPER_DASH_PW to require a password.")
    print("=" * 70)

    handler = make_handler(str(out), args.password, base, exclude)
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
