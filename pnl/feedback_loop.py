#!/usr/bin/env python3
"""
feedback_loop.py — join PropSniper bet history to deployed rule IDs and flag
rules that are bleeding.

The Operations Handoff Guide §9.1 step 6 says: "Flag any rule with negative
personal ROI on n >= 30." This script automates that step.

Input:
  --bets:     PropSniper data-panel-export CSV. Expected columns include:
              ruleId (or rule_id), stake, profit, datePlaced (or date_placed)
              Column naming may vary; the script tries common variants.
  --configs:  directory of devig_*.json so we can sanity-check that flagged
              rule IDs actually exist in deployed configs.

Output:
  Three reports:
    1. Per-rule rolling P&L (7d / 14d / 30d / all-time)
    2. Rules flagged BLEEDING (n>=30, 14d_ROI<0, 30d_ROI<0)
    3. Rules MISSING from deployed configs (the bets fired but the rule is gone)
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
import glob
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


COMMON_COLUMNS = {
    "ruleId":    ["ruleId", "rule_id", "RuleID", "Rule", "rule"],
    "stake":     ["stake", "Stake", "wager", "Wager"],
    "profit":    ["profit", "Profit", "netProfit", "net_profit"],
    "date":      ["datePlaced", "date_placed", "Date", "date", "createdAt", "created_at"],
    "settled":   ["settled", "Settled", "isSettled", "status"],
}


def resolve_col(row: dict, key: str) -> str | None:
    """Find the matching column name in a row dict."""
    for candidate in COMMON_COLUMNS[key]:
        if candidate in row:
            return candidate
    return None


def parse_date(s: str) -> datetime | None:
    if not s:
        return None
    # Try a few common formats
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(s.split("+")[0].split(".")[0], fmt.replace(".%f", ""))
        except ValueError:
            continue
    return None


def load_bets(path: str) -> list[dict]:
    """Load bet history CSV into a list of normalized records."""
    bets = []
    with open(path) as fh:
        reader = csv.DictReader(fh)
        first = next(reader, None)
        if not first:
            return []
        col_rule = resolve_col(first, "ruleId")
        col_stake = resolve_col(first, "stake")
        col_profit = resolve_col(first, "profit")
        col_date = resolve_col(first, "date")

        if not col_rule:
            print(f"WARN: no rule ID column found. Expected one of {COMMON_COLUMNS['ruleId']}. Available: {list(first.keys())}", file=sys.stderr)
            return []

        # Process first row plus remaining
        for row in [first] + list(reader):
            try:
                stake = float(row.get(col_stake, 0) or 0) if col_stake else 0
                profit = float(row.get(col_profit, 0) or 0) if col_profit else 0
                rule_id = row.get(col_rule, "").strip()
                date_str = row.get(col_date, "") if col_date else ""
                date_obj = parse_date(date_str)
                if rule_id:
                    bets.append({
                        "rule_id": rule_id,
                        "stake": stake,
                        "profit": profit,
                        "date": date_obj,
                    })
            except (TypeError, ValueError):
                continue
    return bets


def load_deployed_rule_ids(config_dir: str) -> set[str]:
    """Pull all rule IDs from devig_*.json files in a directory."""
    rule_ids = set()
    for path in glob.glob(str(Path(config_dir) / "devig_*.json")):
        try:
            data = json.load(open(path))
        except (OSError, json.JSONDecodeError):
            continue
        filters_block = data.get("filters", data) if data.get("type") else data
        for rule in filters_block.get("rules", []) or []:
            if rule.get("id"):
                rule_ids.add(rule["id"])
    return rule_ids


def compute_rule_pnl(bets: list[dict], now: datetime | None = None) -> dict[str, dict]:
    """Per-rule rolling P&L. Returns {rule_id: {n, stake, profit, roi_7d, roi_14d, roi_30d, roi_all}}."""
    if now is None:
        now = max((b["date"] for b in bets if b["date"]), default=datetime.now())
    cutoffs = {
        "7d": now - timedelta(days=7),
        "14d": now - timedelta(days=14),
        "30d": now - timedelta(days=30),
    }
    agg = defaultdict(lambda: {
        "n_all": 0, "stake_all": 0.0, "profit_all": 0.0,
        "n_7d": 0,  "stake_7d": 0.0,  "profit_7d": 0.0,
        "n_14d": 0, "stake_14d": 0.0, "profit_14d": 0.0,
        "n_30d": 0, "stake_30d": 0.0, "profit_30d": 0.0,
    })
    for b in bets:
        r = agg[b["rule_id"]]
        r["n_all"] += 1
        r["stake_all"] += b["stake"]
        r["profit_all"] += b["profit"]
        d = b["date"]
        if not d:
            continue
        for window in ("7d", "14d", "30d"):
            if d >= cutoffs[window]:
                r[f"n_{window}"] += 1
                r[f"stake_{window}"] += b["stake"]
                r[f"profit_{window}"] += b["profit"]

    out = {}
    for rid, r in agg.items():
        rec = dict(r)
        for w in ("all", "7d", "14d", "30d"):
            stake = rec[f"stake_{w}"]
            rec[f"roi_{w}"] = (rec[f"profit_{w}"] / stake) if stake > 0 else None
        out[rid] = rec
    return out


def print_bleeding_report(pnl: dict[str, dict], deployed: set[str], min_n: int = 30):
    """Flag rules with n>=30 AND 14d_ROI<0 AND 30d_ROI<0."""
    bleeders = []
    for rid, r in pnl.items():
        if r["n_30d"] >= min_n and r.get("roi_30d", 0) is not None and r["roi_30d"] < 0 \
           and r.get("roi_14d", 0) is not None and r["roi_14d"] < 0:
            bleeders.append(rid)

    print(f"\n{'='*80}")
    print(f"BLEEDING RULES (n>={min_n}, 14d_ROI<0 AND 30d_ROI<0)")
    print(f"{'='*80}")
    if not bleeders:
        print("None. Nothing currently meets the bleeding criteria.")
    else:
        print(f"\n{'rule_id':<40} {'n_30d':>7} {'roi_14d':>10} {'roi_30d':>10} {'profit_30d':>12} {'deployed?':>10}")
        for rid in sorted(bleeders, key=lambda x: pnl[x]["roi_30d"]):
            r = pnl[rid]
            dep = "yes" if rid in deployed else "ORPHAN"
            print(f"{rid[:40]:<40} {r['n_30d']:>7} {r['roi_14d']:>10.2%} {r['roi_30d']:>10.2%} {r['profit_30d']:>12.2f} {dep:>10}")


def print_orphan_report(pnl: dict[str, dict], deployed: set[str]):
    """Rules in bet history but not in any deployed config."""
    orphans = sorted(rid for rid in pnl if rid not in deployed)
    if not orphans:
        return
    print(f"\n{'='*80}")
    print(f"ORPHANED RULES (in bet history but not in deployed configs)")
    print(f"{'='*80}")
    print(f"{'rule_id':<40} {'n_all':>7} {'profit_all':>12}")
    for rid in orphans:
        r = pnl[rid]
        print(f"{rid[:40]:<40} {r['n_all']:>7} {r['profit_all']:>12.2f}")


def print_full_table(pnl: dict[str, dict], deployed: set[str]):
    print(f"\n{'='*120}")
    print(f"All rules — rolling P&L")
    print(f"{'='*120}")
    print(f"{'rule_id':<40} {'n_all':>6} {'n_30d':>6} "
          f"{'roi_30d':>9} {'roi_14d':>9} {'roi_7d':>9} {'profit_all':>12}")
    for rid in sorted(pnl, key=lambda x: pnl[x].get("roi_30d") or -999):
        r = pnl[rid]
        def fmt_pct(v): return f"{v:>9.2%}" if v is not None else f"{'—':>9}"
        print(f"{rid[:40]:<40} {r['n_all']:>6} {r['n_30d']:>6} "
              f"{fmt_pct(r.get('roi_30d'))} {fmt_pct(r.get('roi_14d'))} {fmt_pct(r.get('roi_7d'))} "
              f"{r['profit_all']:>12.2f}")


def main():
    ap = argparse.ArgumentParser(description="Per-rule P&L feedback from PropSniper bet exports")
    ap.add_argument("--bets", required=True, help="data-panel-export CSV")
    ap.add_argument("--configs", required=True, help="directory of devig_*.json configs")
    ap.add_argument("--min-n", type=int, default=30, help="minimum bets for bleeding flag")
    ap.add_argument("--full", action="store_true", help="print full per-rule table")
    args = ap.parse_args()

    if not Path(args.bets).exists():
        print(f"ERROR: bets file not found: {args.bets}", file=sys.stderr)
        sys.exit(2)

    bets = load_bets(args.bets)
    if not bets:
        print(f"ERROR: no bets loaded from {args.bets}", file=sys.stderr)
        sys.exit(2)
    print(f"Loaded {len(bets)} bets covering {len({b['rule_id'] for b in bets})} unique rule IDs", file=sys.stderr)

    deployed = load_deployed_rule_ids(args.configs)
    print(f"Found {len(deployed)} rule IDs in deployed configs", file=sys.stderr)

    pnl = compute_rule_pnl(bets)
    print_bleeding_report(pnl, deployed, args.min_n)
    print_orphan_report(pnl, deployed)
    if args.full:
        print_full_table(pnl, deployed)


if __name__ == "__main__":
    main()
