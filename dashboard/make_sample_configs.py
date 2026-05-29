#!/usr/bin/env python3
"""
make_sample_configs.py — emit synthetic devig_*.json configs for demoing the
dashboard pipeline end-to-end WITHOUT shipping Jacob's real (sensitive) configs.

The generated configs deliberately contain a spread of violations so every
dashboard card lights up:
  - a self_reference   (DraftKings rule listing DraftKings as a comp)  -> exit 3
  - a duplicate_id     (same rule ID in two files)                     -> exit 3
  - a j/mo collision   (Caesars J and MO have identical comp stacks)   -> WARN
  - a clean rule       (passes everything)

These are SAMPLE data. Point the real pipeline at your gitignored live/ dir.

Usage:
    python dashboard/make_sample_configs.py            # -> configs/sample_live/
    python dashboard/make_sample_configs.py --out DIR
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def comp(book: str, required: bool = False, max_vig: float = 0.06) -> dict:
    return {"book": book, "minEV": 0, "maxVig": max_vig,
            "required": required, "weight": 1, "minLiquidity": 0}


def make_rule(rule_id: str, league: str, market: str, stack: list[dict],
              required_comparisons: int = 2, min_ev: float = 5) -> dict:
    """Build a rule with identical main/team/player sub-blocks (audit requires this)."""
    sub_block = {"requiredComparisons": required_comparisons, "comparisons": stack}
    markets = {"main": sub_block, "team": sub_block, "player": sub_block}
    return {
        "id": rule_id,
        "leagues": [league],
        "bet_timing": ["live"],
        "time_types": ["full", "period"],
        "market_inclusion": [{"league": league, "market": market}],
        "filters": {
            "minEV": min_ev, "maxEV": 100, "minOdds": -300, "maxOdds": 300,
            "minHoursAway": 0, "maxHoursAway": 72,
            "leagues": [league], "markets": markets,
        },
    }


def flat_config(rules: list[dict], devig: str = "worstcase") -> dict:
    return {"v": 3, "devig": devig, "rules": rules}


def main():
    ap = argparse.ArgumentParser(description="Generate sample PropSniper configs for the dashboard demo")
    ap.add_argument("--out", default=None, help="output dir (default: ../configs/sample_live relative to this script)")
    args = ap.parse_args()

    out = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / "configs" / "sample_live"
    out.mkdir(parents=True, exist_ok=True)

    # Shared stack used at Caesars for both J and MO -> identical-stack collision (WARN)
    caesars_stack = [comp("Pinnacle", required=True), comp("BetMGM"), comp("Circa")]

    devig_J_caesars = flat_config([
        make_rule("cae_mlb_total_runs_f5", "MLB", "Total Runs 1st 5 Innings",
                  [comp("Pinnacle", required=True), comp("BetMGM"), comp("Circa")]),
        make_rule("cae_nba_player_points", "NBA", "Player Points",
                  [comp("Pinnacle", required=True), comp("Circa")]),
    ])

    devig_MO_caesars = flat_config([
        # SAME cell + SAME stack as J -> j_mo_no_differentiation WARN
        make_rule("cae_mlb_total_runs_f5_m", "MLB", "Total Runs 1st 5 Innings",
                  [comp("Pinnacle", required=True), comp("BetMGM"), comp("Circa")]),
    ])

    devig_J_draftkings = flat_config([
        # SELF-REFERENCE: dk_ rule lists DraftKings as a comp book -> ERROR (exit 3)
        make_rule("dk_nba_player_points", "NBA", "Player Points",
                  [comp("Pinnacle", required=True), comp("DraftKings"), comp("Circa")]),
        # DUPLICATE ID: reuses an ID already present in devig_J_caesars.json -> ERROR (exit 3)
        make_rule("cae_mlb_total_runs_f5", "MLB", "Total Runs 1st 5 Innings",
                  [comp("Pinnacle", required=True), comp("BetMGM")]),
    ])

    files = {
        "devig_J_caesars.json": devig_J_caesars,
        "devig_MO_caesars.json": devig_MO_caesars,
        "devig_J_draftkings.json": devig_J_draftkings,
    }
    for name, data in files.items():
        (out / name).write_text(json.dumps(data, indent=2))
        print(f"wrote {out / name}")

    print(f"\n{len(files)} sample config files in {out}")
    print("Run: python audit/audit_configs.py --dir", out, "--format json")


if __name__ == "__main__":
    main()
