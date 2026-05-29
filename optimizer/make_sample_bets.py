#!/usr/bin/env python3
"""
make_sample_bets.py — synthetic settled-bet history for demoing the optimizer.

Spans ~150 days so the 7d / 14d / 30d / 90d / all windows each tell a different
story, and some rules deliberately drift over time (hot then cold, or improving)
so the window selector is meaningful. SAMPLE data only — real exports live in the
gitignored data/ dir.

Usage:
    python optimizer/make_sample_bets.py            # -> optimizer/sample_bets.csv
"""
from __future__ import annotations
import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

NET_AT_MINUS_110 = 100.0 / 110.0  # ~0.9091 net odds on a win at -110
ASOF = datetime(2026, 5, 29, 12, 0, 0)
HORIZON_DAYS = 150

# rule_id, league, market, n_bets, p_old, p_recent, recent_window_days, base_stake
# EV at -110 is positive iff p > ~0.524. Volumes are high enough that even the
# 7d window clears min-n, so days/weeks/months all carry meaningful signal.
PROFILE = [
    # winner overall but COOLING in the last 2 weeks -> windows diverge
    ("cae_mlb_total_runs_f5",    "MLB", "Total Runs 1st 5 Innings", 1300, 0.575, 0.495, 14, 100),
    # consistent loser everywhere -> DISABLE in every window
    ("dk_nba_player_points",     "NBA", "Player Points",             980, 0.452, 0.448, 21, 100),
    # marginal, basically a coin flip -> HOLD
    ("cae_mlb_total_runs_f5_m",  "MLB", "Total Runs 1st 5 Innings",  720, 0.527, 0.523, 14, 100),
    # IMPROVING: was losing, now winning -> recent windows turn positive
    ("cae_nba_player_points",    "NBA", "Player Points",             520, 0.488, 0.585, 14, 100),
    # steady strong winner -> PROMOTE across windows
    ("fd_mlb_strikeouts",        "MLB", "Pitcher Strikeouts",       1040, 0.560, 0.566, 21, 150),
    # recently turned cold sharply -> 7d/14d bad, longer windows ok
    ("cae_nhl_shots",            "NHL", "Shots on Goal",             600, 0.548, 0.470, 14, 75),
    # orphan: bets exist, rule not in deployed configs -> flagged
    ("fd_nhl_shots_on_goal",     "NHL", "Shots on Goal",             300, 0.470, 0.470, 21, 100),
]


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic settled bets (windowed) for the optimizer demo")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "sample_bets.csv"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows = []
    for rid, league, market, n, p_old, p_recent, recent_days, base_stake in PROFILE:
        for _ in range(n):
            age_days = rng.uniform(0, HORIZON_DAYS)
            p = p_recent if age_days <= recent_days else p_old
            won = rng.random() < p
            stake = round(base_stake * rng.uniform(0.8, 1.2), 2)
            profit = round(stake * NET_AT_MINUS_110, 2) if won else -stake
            placed = ASOF - timedelta(days=age_days, hours=rng.uniform(0, 23))
            rows.append({
                "ruleId": rid, "league": league, "market": market,
                "stake": f"{stake:.2f}", "profit": f"{profit:.2f}",
                "datePlaced": placed.strftime("%Y-%m-%dT%H:%M:%S"), "settled": "true",
            })
    rng.shuffle(rows)

    out = Path(args.out)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ruleId", "league", "market", "stake", "profit", "datePlaced", "settled"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} bets across {len(PROFILE)} rules over {HORIZON_DAYS}d -> {out}")


if __name__ == "__main__":
    main()
