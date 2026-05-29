#!/usr/bin/env python3
"""
make_sample_community.py — synthetic community/consensus metrics for the demo.

Stands in for the real PropSniper community / OddsPapi consensus feed so the
"Community vs Personal" panel renders without a live source. Keyed by
(league, market) to match deployed rules. SAMPLE data only.

Real source: replace community/community_latest.json with a fetch from the actual
PropSniper community panel / OddsPapi export (same shape). See run_machine.py.

Usage:
    python community/make_sample_community.py   # -> community/community_latest.json
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

# league, market, community_edge (ROI-equiv), community_brier, n, hit_rate
MARKETS = [
    ("MLB", "Total Runs 1st 5 Innings", 0.018, 0.244, 5120, 0.529),
    ("NBA", "Player Points",            -0.004, 0.251, 8430, 0.498),
    ("MLB", "Pitcher Strikeouts",        0.041, 0.238, 3960, 0.547),
    ("NHL", "Shots on Goal",             0.009, 0.248, 2870, 0.515),
]


def main():
    ap = argparse.ArgumentParser(description="Generate sample community consensus metrics")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "community_latest.json"))
    args = ap.parse_args()

    payload = {
        "source": "sample",
        "as_of": "2026-05-29T12:00:00Z",
        "markets": [
            {"league": lg, "market": mk, "community_edge": ce,
             "community_brier": br, "community_n": n, "hit_rate": hr}
            for (lg, mk, ce, br, n, hr) in MARKETS
        ],
    }
    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2))
    print(f"wrote {len(MARKETS)} market consensus rows -> {out}")


if __name__ == "__main__":
    main()
