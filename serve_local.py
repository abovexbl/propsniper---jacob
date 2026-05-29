#!/usr/bin/env python3
"""
serve_local.py — Option A: a PRIVATE, local-only live dashboard over your REAL data,
with a real (server-side) password.

Runs the machine (audit + optimizer) against your configs/bets, writes the feed to
a gitignored `private/` dir, drops a copy of the dashboard next to it, and serves it
bound to 127.0.0.1 only. Nothing is published; nothing is committed; only this
machine can reach it.

Password: if you set one (via --password or the PROPSNIPER_DASH_PW env var), the
server enforces HTTP Basic Auth SERVER-SIDE — it challenges before serving any
byte, including the feed. This is real auth (unlike a client-side JS password on a
static site). Over loopback, plain HTTP Basic Auth is fine. For remote/hosted
access you need HTTPS + a proper host (see ORCHESTRATION.md / Path 2).

Usage (real data, password-gated):
    set PROPSNIPER_DASH_PW=your-password           (Windows)  / export on *nix
    python serve_local.py --configs live/ --bets data/data-panel-export-LATEST.csv

Usage (sample demo, no password):
    python serve_local.py

Then open the printed http://127.0.0.1:PORT/ URL. Ctrl+C to stop.
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
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import run_machine  # reuse get_audit / get_optimizer  # noqa: E402

DASHBOARD_SRC = REPO / "docs" / "index.html"
AUTH_USER = "propsniper"


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


def make_handler(directory: str, password: str | None):
    """Return a request-handler class that serves `directory`, gated by Basic Auth
    if `password` is set. Auth is checked SERVER-SIDE before any content is sent."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=directory, **k)

        def _authed(self) -> bool:
            if not password:
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
                user, _, pw = decoded.partition(":")
            except Exception:
                return False
            # constant-time comparison to avoid timing leaks
            return hmac.compare_digest(user, AUTH_USER) and hmac.compare_digest(pw, password)

        def _challenge(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="PropSniper Command Center"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Authentication required.")

        def do_GET(self):
            if not self._authed():
                return self._challenge()
            return super().do_GET()

        def do_HEAD(self):
            if not self._authed():
                return self._challenge()
            return super().do_HEAD()

        def log_message(self, fmt, *args):
            pass  # quiet

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Private local-only live dashboard with server-side password (Option A)")
    ap.add_argument("--configs", default="configs/sample_live", help="config dir (use live/ for real data)")
    ap.add_argument("--bets", default="optimizer/sample_bets.csv", help="bet export CSV (use data/... for real)")
    ap.add_argument("--bankroll", type=float, default=10000.0)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--out-dir", default=str(REPO / "private"))
    ap.add_argument("--password", default=os.environ.get("PROPSNIPER_DASH_PW"),
                    help="dashboard password (or set PROPSNIPER_DASH_PW). Username is 'propsniper'.")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    is_real = args.configs.replace("\\", "/").startswith("live") or args.bets.replace("\\", "/").startswith("data")
    if is_real:
        print("=" * 70)
        print("PRIVATE MODE — real data. Served to 127.0.0.1 only; never committed.")
        print(f"  output dir: {out}  (gitignored)")
        if not args.password:
            print("  WARNING: no password set. Loopback-only, but consider PROPSNIPER_DASH_PW.")
        print("=" * 70)

    feed = build_feed(args.configs, args.bets, args.bankroll)
    (out / "feed.json").write_text(json.dumps(feed, indent=2))
    shutil.copyfile(DASHBOARD_SRC, out / "index.html")

    a = feed["audit"]
    acts = (feed.get("optimizer") or {}).get("summary", {}).get("actions", {})
    print(f"feed built: audit={a.get('verdict')} cards={a.get('cards')} optimizer={acts}")
    auth_state = f"password ON (user '{AUTH_USER}')" if args.password else "no password (loopback only)"
    print(f"serving {out} at http://127.0.0.1:{args.port}/  [{auth_state}]  (Ctrl+C to stop)")

    handler = make_handler(str(out), args.password)
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
