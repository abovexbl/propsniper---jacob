#!/usr/bin/env python3
"""
randomize_stack.py — per-account or per-deploy comp stack randomization.

Defends against limiting by ensuring that identical (venue × market) cells
deployed at different accounts don't have identical comp stacks. Per Operations
Handoff Guide §5 ("J vs MO differentiation").

Two modes:
  - --rotate     : given a candidate pool of 5 books per cell, rotate ranks
                   2 and 3 between deploys. Preserves rank-1 (required:true)
                   which is the high-confidence pick.
  - --jitter     : given configs for multiple accounts at the same venue,
                   verify they have non-identical stacks. Print which cells
                   have identical stacks (limiting risk) and propose swaps.

Input: a candidate pool YAML/JSON like:

    {
      "cae_mlb_total_runs_1st_5_innings": {
        "rank1": "Pinnacle",
        "alternates": ["BetParx", "BetMGM", "BetRivers", "Hardrock"]
      },
      ...
    }

Output: rewritten devig_*.json configs with rotated stack composition.

Usage:
    python randomize_stack.py --jitter --dir from-jacob/
    python randomize_stack.py --rotate --pool candidates.json --account J --out devig_J_caesars.json
"""
from __future__ import annotations
import argparse
import json
import glob
import random
import sys
from pathlib import Path
from collections import defaultdict


def load_config(path: str) -> dict:
    return json.load(open(path))


def get_filters(data: dict) -> dict:
    return data.get("filters", data) if data.get("type") else data


def cell_key(rule: dict) -> tuple:
    """Unique cell signature: (league, market). Comp stacks for the same cell
    deployed at the same venue across accounts should differ."""
    mi = (rule.get("market_inclusion") or [{}])[0]
    return (mi.get("league"), mi.get("market"))


def main_stack(rule: dict) -> tuple[str, ...]:
    comps = rule.get("filters", {}).get("markets", {}).get("main", {}).get("comparisons", [])
    return tuple(c.get("book") for c in comps)


def required_book(rule: dict) -> str | None:
    comps = rule.get("filters", {}).get("markets", {}).get("main", {}).get("comparisons", [])
    for c in comps:
        if c.get("required"):
            return c.get("book")
    return None


# --------------------------------------------------------------------------
# Jitter audit: find identical-stack cells across files at same venue
# --------------------------------------------------------------------------

def venue_of_file(path: str) -> str:
    """Crude venue extraction from filename (devig_USER_VENUE_v2.json)."""
    name = Path(path).name.lower()
    for v in ("caesars", "draftkings", "fanduel", "fliff", "kalshi", "rebet"):
        if v in name:
            return v
    return "?"


def jitter_audit(files: list[str]) -> int:
    """Find cells where multiple accounts at the same venue have identical stacks."""
    # venue -> cell -> {file: stack}
    by_venue_cell = defaultdict(lambda: defaultdict(dict))
    for path in files:
        venue = venue_of_file(path)
        try:
            data = load_config(path)
        except (OSError, json.JSONDecodeError) as e:
            print(f"WARN: skipping {path}: {e}", file=sys.stderr)
            continue
        for rule in get_filters(data).get("rules", []) or []:
            cell = cell_key(rule)
            stack = main_stack(rule)
            by_venue_cell[venue][cell][Path(path).name] = stack

    n_collisions = 0
    print(f"\n{'='*80}")
    print("Stack Differentiation Audit")
    print(f"{'='*80}\n")

    for venue, cells in sorted(by_venue_cell.items()):
        venue_collisions = []
        for cell, file_to_stack in cells.items():
            if len(file_to_stack) < 2:
                continue
            unique_stacks = set(file_to_stack.values())
            if len(unique_stacks) == 1:
                venue_collisions.append((cell, list(file_to_stack)))

        if venue_collisions:
            print(f"\n{venue.upper()} — {len(venue_collisions)} cells with identical stacks across accounts:")
            for cell, file_list in venue_collisions:
                print(f"  {cell[0] or '?':<10} {cell[1] or '?':<40} across: {', '.join(file_list)}")
                n_collisions += 1

    print(f"\nTotal limiting-risk collisions: {n_collisions}")
    print(f"{'='*80}\n")
    return n_collisions


# --------------------------------------------------------------------------
# Rotate: given candidate pools per cell, build differentiated stacks
# --------------------------------------------------------------------------

def rotate_stacks(
    config: dict,
    candidate_pool: dict[str, dict],
    account: str,
    rank2_offset: int = 0,
    rank3_offset: int = 1,
    seed: int | None = None,
) -> dict:
    """Rewrite a config's comp stacks using candidate pools.

    For each rule whose ID is in candidate_pool:
      - rank-1 = pool["rank1"] (preserved, marked required:true)
      - rank-2 = pool["alternates"][rank2_offset]
      - rank-3 = pool["alternates"][rank3_offset]

    Different accounts (J, MO) pass different offsets so they get different mid-stacks.

    Suggested defaults:
      J  account: rank2_offset=0, rank3_offset=1 (top-of-pool)
      MO account: rank2_offset=1, rank3_offset=3 (skip-1, skip-3)

    Mutates a deep copy of config.
    """
    if seed is not None:
        random.seed(seed)
    out = json.loads(json.dumps(config))  # deep copy

    rules = get_filters(out).get("rules", []) or []
    n_rewritten = 0
    for rule in rules:
        rid = rule.get("id")
        # Match by stripped rule ID (no account suffix, no _v2)
        match_key = None
        for k in candidate_pool:
            if rid and (rid.startswith(k) or k in rid):
                match_key = k
                break
        if not match_key:
            continue

        pool = candidate_pool[match_key]
        rank1 = pool["rank1"]
        alts = pool["alternates"]
        if rank2_offset >= len(alts) or rank3_offset >= len(alts):
            print(f"WARN: pool {match_key!r} too small for offsets {rank2_offset}/{rank3_offset}", file=sys.stderr)
            continue
        rank2 = alts[rank2_offset]
        rank3 = alts[rank3_offset]

        new_books = [rank1, rank2, rank3]
        new_comps = []
        for i, book in enumerate(new_books):
            new_comps.append({
                "book": book,
                "minEV": 0,
                "maxVig": 0.1,
                "required": i == 0,
                "weight": 1,
                "minLiquidity": 0,
            })

        for sub in ("main", "team", "player"):
            rule["filters"]["markets"][sub]["comparisons"] = list(new_comps)
            rule["filters"]["markets"][sub]["requiredComparisons"] = 2

        n_rewritten += 1

    print(f"Rewrote {n_rewritten}/{len(rules)} rules for account {account}", file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Comp stack randomizer / jitter audit")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--jitter", action="store_true", help="audit identical-stack collisions")
    g.add_argument("--rotate", action="store_true", help="apply candidate pool to rotate stacks")

    ap.add_argument("--dir", help="directory of devig_*.json (jitter mode)")
    ap.add_argument("--pool", help="candidate pool JSON file (rotate mode)")
    ap.add_argument("--config", help="input config file (rotate mode)")
    ap.add_argument("--account", default="J", help="account label (J/MO)")
    ap.add_argument("--rank2-offset", type=int, default=0)
    ap.add_argument("--rank3-offset", type=int, default=1)
    ap.add_argument("--out", help="output path (rotate mode)")
    ap.add_argument("--seed", type=int)
    args = ap.parse_args()

    if args.jitter:
        if not args.dir:
            print("ERROR: --jitter requires --dir", file=sys.stderr)
            sys.exit(2)
        files = sorted(glob.glob(str(Path(args.dir) / "devig_*.json")))
        sys.exit(0 if jitter_audit(files) == 0 else 1)
    else:
        if not (args.pool and args.config and args.out):
            print("ERROR: --rotate requires --pool, --config, --out", file=sys.stderr)
            sys.exit(2)
        config = load_config(args.config)
        pool = json.load(open(args.pool))
        result = rotate_stacks(config, pool, args.account, args.rank2_offset, args.rank3_offset, args.seed)
        with open(args.out, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
