#!/usr/bin/env python3
"""
run_machine.py — one pulse of "the machine" (feed schema v2).

Runs the audit + the windowed optimizer, joins personal performance against
community consensus, and writes a single combined feed for the live dashboard.
Optionally commits + pushes it so the change goes live.

Feed (docs/feed.json):
    { schema_version, generated_at, source, windows, default_window,
      audit{...}, optimizer{...windowed...}, community{ comparison[] } }

⚠️ PRIVACY: the feed contains rule IDs, edges, and stakes. Publishing to a PUBLIC
repo exposes your operation. Default targets sample data. Only point
--configs/--bets at real live/ + data/ if the publish target is PRIVATE.

Usage:
    python run_machine.py                                    # sample data -> docs/feed.json
    python run_machine.py --configs live/ --bets data/x.csv  # real (private only)
    python run_machine.py --publish                          # also git commit+push
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import pslib  # noqa: E402

AUDIT = REPO / "audit" / "audit_configs.py"
OPTIMIZE = REPO / "optimizer" / "optimize.py"
COMMUNITY_FILE = REPO / "community" / "community_latest.json"
SCHEMA_VERSION = 2

HEADLINE = {
    "self_reference": "Self-Ref Violations",
    "j_mo_no_differentiation": "Stack Collisions",
    "duplicate_id": "Duplicate Rule IDs",
    "duplicate_id_in_file": "Duplicate Rule IDs",
}
BEAT_THRESHOLD = 0.01  # personal edge must exceed community by 1% ROI to "beat"


def run_json(cmd: list[str]) -> dict:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if not p.stdout.strip():
        sys.stderr.write(p.stderr)
        raise SystemExit(f"no JSON from: {' '.join(cmd)} (exit {p.returncode})")
    return json.loads(p.stdout)


def get_audit(config_dir: str) -> dict:
    audit = run_json([sys.executable, str(AUDIT), "--dir", config_dir, "--format", "json"])
    counts = audit.get("counts_by_check", {})
    cards = {label: 0 for label in dict.fromkeys(HEADLINE.values())}
    for check, label in HEADLINE.items():
        cards[label] += counts.get(check, 0)
    audit["cards"] = cards
    return audit


def get_optimizer(config_dir: str, bets: str, bankroll: float) -> dict | None:
    if not Path(bets).exists():
        return None
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "proposals.json")
        r = subprocess.run([sys.executable, str(OPTIMIZE), "--bets", bets, "--configs", config_dir,
                            "--bankroll", str(bankroll), "--out", out], capture_output=True, text=True)
        if not Path(out).exists():
            sys.stderr.write(r.stderr)
            return None
        return json.loads(Path(out).read_text())


def build_community(optimizer: dict | None, window: str) -> dict:
    """Join personal performance (chosen window) against community consensus by market."""
    if not COMMUNITY_FILE.exists():
        return {"status": "not_connected",
                "note": "Drop community/community_latest.json (run community/make_sample_community.py for a demo).",
                "comparison": []}
    try:
        cdata = json.loads(COMMUNITY_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "error", "note": f"could not read community file: {e}", "comparison": []}

    by_market = {(m.get("league"), m.get("market")): m for m in cdata.get("markets", [])}
    comparison = []
    counts = {"beating": 0, "inline": 0, "below": 0}
    for p in (optimizer or {}).get("proposals", []):
        c = by_market.get((p.get("league"), p.get("market")))
        if not c:
            continue
        e = p["windows"][window]
        personal_edge = e["post_mean"]
        delta = personal_edge - c["community_edge"]
        verdict = "beating" if delta >= BEAT_THRESHOLD else ("below" if delta <= -BEAT_THRESHOLD else "inline")
        counts[verdict] += 1
        comparison.append({
            "rule_id": p["rule_id"], "label": p.get("label"),
            "league": p.get("league"), "market": p.get("market"),
            "deployed": p["deployed"], "orphan": p["orphan"],
            "personal_edge": round(personal_edge, 6), "personal_n": e["n"],
            "personal_p_bleeding": e["p_bleeding"],
            "community_edge": c["community_edge"], "community_brier": c["community_brier"],
            "community_n": c["community_n"], "community_hit_rate": c["hit_rate"],
            "delta_edge": round(delta, 6), "verdict": verdict,
        })
    comparison.sort(key=lambda x: x["delta_edge"], reverse=True)
    return {"status": "connected", "source": cdata.get("source"), "as_of": cdata.get("as_of"),
            "window": window, "summary": counts, "comparison": comparison}


def main():
    ap = argparse.ArgumentParser(description="Run the machine and emit the live dashboard feed (v2)")
    ap.add_argument("--configs", default="configs/sample_live")
    ap.add_argument("--bets", default="optimizer/sample_bets.csv")
    ap.add_argument("--bankroll", type=float, default=10000.0)
    ap.add_argument("--out-dir", default=str(REPO / "docs"))
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()

    audit = get_audit(args.configs)
    optimizer = get_optimizer(args.configs, args.bets, args.bankroll)
    default_window = (optimizer or {}).get("default_window", "30d")
    community = build_community(optimizer, default_window)

    feed = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"configs": args.configs, "bets": args.bets, "community": community.get("source")},
        "windows": pslib.WINDOW_LABELS,
        "default_window": default_window,
        "audit": audit,
        "optimizer": optimizer,
        "community": community,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "feed.json").write_text(json.dumps(feed, indent=2))
    (out_dir / "feed.js").write_text(
        "// AUTO-GENERATED by run_machine.py\nwindow.MACHINE_FEED = " + json.dumps(feed, indent=2) + ";\n")

    acts = (optimizer or {}).get("summary_by_window", {}).get(default_window, {}).get("actions", {})
    print(f"[{feed['generated_at']}] audit={audit.get('verdict')} cards={audit['cards']}")
    print(f"  optimizer[{default_window}]={acts}  community={community.get('status')} {community.get('summary','')}")
    print(f"  wrote {out_dir/'feed.json'}")

    if args.publish:
        rel = [str((out_dir / f).relative_to(REPO)) for f in ("feed.json", "feed.js")]
        subprocess.run(["git", "-C", str(REPO), "add", *rel], check=True)
        msg = f"machine pulse {feed['generated_at']} — {audit.get('verdict')}"
        r = subprocess.run(["git", "-C", str(REPO), "commit", "-m", msg], capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" in (r.stdout + r.stderr):
            print("no feed change to publish")
            return
        subprocess.run(["git", "-C", str(REPO), "push", "mine", "HEAD:main"], check=True)
        print(f"published feed (commit: {msg})")


if __name__ == "__main__":
    main()
