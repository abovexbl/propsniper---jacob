#!/usr/bin/env python3
"""
make_sample_bets.py — synthetic settled-bet history for demoing the optimizer.

Generates a data-panel-export-style CSV with rule IDs that match the sample
configs (configs/sample_live), engineered so the optimizer surfaces every action:
  - a confident WINNER  (-> PROMOTE)
  - a confident LOSER   (-> DISABLE)
  - a marginal rule     (-> HOLD)
  - a small-sample rule  (-> EXPLORE)
  - an ORPHAN rule (bets exist, not in any deployed config)

SAMPLE data only. Real bet exports stay in the gitignored data/ dir.

Usage:
    python optimizer/make_sample_bets.py            # -> optimizer/sample_bets.csv
"""
from __future__ import annotations
import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

# American -110 -> decimal 1.9091 -> net odds on a win
NET_AT_MINUS_110 = 100.0 / 110.0  # ~0.9091
STAKE = 100.0
ASOF = datetime(2026, 5, 29, 12, 0, 0)

# (rule_id, n_bets, true_win_prob) — EV at -110 is positive iff p > ~0.524
PROFILE = [
    ("cae_mlb_total_runs_f5",   250, 0.560),  # winner   -> PROMOTE
    ("dk_nba_player_points",    140, 0.450),  # loser    -> DISABLE
    ("cae_mlb_total_runs_f5_m",  90, 0.525),  # marginal -> HOLD
    ("cae_nba_player_points",    18, 0.540),  # small n  -> EXPLORE
    ("fd_nhl_shots_on_goal",     40, 0.470),  # orphan (not in sample configs)
]


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic settled bets for the optimizer demo")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "sample_bets.csv"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows = []
    for rule_id, n, p in PROFILE:
        for i in range(n):
            won = rng.random() < p
            profit = STAKE * NET_AT_MINUS_110 if won else -STAKE
            # spread placements over the last ~45 days, newest last
            placed = ASOF - timedelta(days=rng.uniform(0, 45), hours=rng.uniform(0, 23))
            rows.append({
                "ruleId": rule_id,
                "stake": f"{STAKE:.2f}",
                "profit": f"{profit:.2f}",
                "datePlaced": placed.strftime("%Y-%m-%dT%H:%M:%S"),
                "settled": "true",
            })
    rng.shuffle(rows)

    out = Path(args.out)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ruleId", "stake", "profit", "datePlaced", "settled"])
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} bets across {len(PROFILE)} rules -> {out}")


if __name__ == "__main__":
    main()
