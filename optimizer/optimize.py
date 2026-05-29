#!/usr/bin/env python3
"""
optimize.py — the continuous-improvement loop over PropSniper configs.

Ingests settled bet history, estimates each rule's edge (Bayesian, shrunk),
and proposes an ACTION per rule under an explore/exploit policy. Every action
that would mutate a config is advisory only — the apply step is human-gated and
must re-pass `/audit` (per CLAUDE.md and ORCHESTRATION.md).

This is one turn of the loop. Run it on a schedule against fresh bet data and the
proposals track reality. See OPTIMIZER.md for the full design.

Policy (per rule, from posterior over edge mu and p_bleeding = P(mu < 0)):
    n < --min-n            -> EXPLORE  (too little data; keep a small probe stake)
    p_bleeding >= 0.95     -> DISABLE  (strong evidence of negative edge)
    0.75 <= p_bleeding     -> SHRINK   (likely bleeding; cut stake)
    p_bleeding <= 0.25 and post_mean > 0 -> PROMOTE  (confident edge; size up to cap)
    otherwise              -> HOLD     (genuinely uncertain; leave as-is)

Usage:
    python optimizer/optimize.py --bets data/data-panel-export-LATEST.csv --configs live/
    python optimizer/optimize.py --bets optimizer/sample_bets.csv --configs configs/sample_live --bankroll 10000
"""
from __future__ import annotations
import argparse
import csv
import glob
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bandit  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# Reuse the bet-export column flexibility from pnl/feedback_loop.py
COMMON = {
    "ruleId": ["ruleId", "rule_id", "RuleID", "Rule", "rule"],
    "stake":  ["stake", "Stake", "wager", "Wager"],
    "profit": ["profit", "Profit", "netProfit", "net_profit"],
}


def resolve(row: dict, key: str) -> str | None:
    for c in COMMON[key]:
        if c in row:
            return c
    return None


def load_returns(path: str) -> dict[str, list[float]]:
    """Return {rule_id: [unit_roi, ...]} where unit_roi = profit / stake."""
    by_rule: dict[str, list[float]] = defaultdict(list)
    with open(path) as fh:
        reader = csv.DictReader(fh)
        first = next(reader, None)
        if not first:
            return {}
        cr, cs, cp = resolve(first, "ruleId"), resolve(first, "stake"), resolve(first, "profit")
        if not (cr and cs and cp):
            print(f"ERROR: bets CSV needs rule/stake/profit columns. Saw {list(first.keys())}", file=sys.stderr)
            return {}
        for row in [first] + list(reader):
            try:
                stake = float(row.get(cs) or 0)
                profit = float(row.get(cp) or 0)
                rid = (row.get(cr) or "").strip()
            except (TypeError, ValueError):
                continue
            if rid and stake > 0:
                by_rule[rid].append(profit / stake)
    return by_rule


def load_deployed_ids(config_dir: str) -> set[str]:
    ids = set()
    for path in glob.glob(str(Path(config_dir) / "devig_*.json")):
        try:
            data = json.load(open(path))
        except (OSError, json.JSONDecodeError):
            continue
        block = data.get("filters", data) if data.get("type") else data
        for rule in block.get("rules", []) or []:
            if rule.get("id"):
                ids.add(rule["id"])
    return ids


def decide(post: bandit.Posterior, min_n: int) -> tuple[str, str]:
    """Return (action, rationale)."""
    if post.n < min_n:
        return "EXPLORE", f"only {post.n} bets (< {min_n}); gather more before trusting the edge"
    if post.p_bleeding >= 0.95:
        return "DISABLE", f"{post.p_bleeding:.0%} confident edge < 0 over {post.n} bets"
    if post.p_bleeding >= 0.75:
        return "SHRINK", f"{post.p_bleeding:.0%} chance of negative edge; cut exposure"
    if post.p_bleeding <= 0.25 and post.post_mean > 0:
        return "PROMOTE", f"{1 - post.p_bleeding:.0%} confident positive edge (+{post.post_mean:.1%} ROI)"
    return "HOLD", f"edge {post.post_mean:+.1%}, p(bleed)={post.p_bleeding:.0%} — too uncertain to move"


def main():
    ap = argparse.ArgumentParser(description="Continuous config optimizer (Bayesian bandit + fractional Kelly)")
    ap.add_argument("--bets", required=True, help="settled bet-history CSV (rule/stake/profit)")
    ap.add_argument("--configs", required=True, help="directory of deployed devig_*.json")
    ap.add_argument("--bankroll", type=float, default=10000.0, help="bankroll for absolute stake sizing")
    ap.add_argument("--min-n", type=int, default=30, help="min bets before a rule's edge is trusted")
    ap.add_argument("--kelly", type=float, default=0.25, help="fractional-Kelly multiplier (0.25 = quarter)")
    ap.add_argument("--max-fraction", type=float, default=0.05, help="hard cap on bankroll fraction per rule")
    ap.add_argument("--explore-floor", type=float, default=0.005, help="probe stake fraction for EXPLORE rules")
    ap.add_argument("--prior-sd", type=float, default=0.5, help="prior sd on edge (smaller = more shrinkage)")
    ap.add_argument("--seed", type=int, default=7, help="RNG seed for reproducible Thompson samples")
    ap.add_argument("--out", default=str(REPO / "optimizer" / "proposals.json"))
    args = ap.parse_args()

    if not Path(args.bets).exists():
        print(f"ERROR: bets file not found: {args.bets}", file=sys.stderr)
        sys.exit(2)

    by_rule = load_returns(args.bets)
    if not by_rule:
        print("ERROR: no usable bets loaded", file=sys.stderr)
        sys.exit(2)
    deployed = load_deployed_ids(args.configs)
    rng = random.Random(args.seed)

    sigma = bandit.empirical_sigma([r for rs in by_rule.values() for r in rs])

    proposals = []
    for rid in sorted(by_rule):
        post = bandit.update_posterior(rid, by_rule[rid], sigma, prior_sd=args.prior_sd)
        action, rationale = decide(post, args.min_n)
        frac = bandit.kelly_fraction(post, args.kelly, args.max_fraction)
        if action == "DISABLE":
            frac = 0.0
        elif action == "EXPLORE":
            frac = min(max(frac, args.explore_floor), args.max_fraction)
        elif action == "SHRINK":
            frac *= 0.5
        proposals.append({
            **post.to_dict(),
            "deployed": rid in deployed,
            "orphan": rid not in deployed,
            "action": action,
            "rationale": rationale,
            "thompson_sample": round(bandit.thompson_sample(post, rng), 6),
            "recommended_stake_fraction": round(frac, 6),
            "recommended_stake_abs": round(frac * args.bankroll, 2),
        })

    # Rank exploration budget by Thompson sample (highest sampled edge first)
    proposals.sort(key=lambda p: p["thompson_sample"], reverse=True)

    counts = defaultdict(int)
    for p in proposals:
        counts[p["action"]] += 1

    payload = {
        "tool": "optimizer",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "params": {"bankroll": args.bankroll, "min_n": args.min_n, "kelly_mult": args.kelly,
                   "max_fraction": args.max_fraction, "prior_sd": args.prior_sd, "sigma": round(sigma, 6)},
        "summary": {"rules_scored": len(proposals), "actions": dict(sorted(counts.items())),
                    "orphans": sum(1 for p in proposals if p["orphan"])},
        "proposals": proposals,
        "note": "Advisory. Config-mutating actions are human-gated and must re-pass /audit before deploy.",
    }
    Path(args.out).write_text(json.dumps(payload, indent=2))

    # Human report
    print(f"\n{'='*92}")
    print("PropSniper Optimizer — proposed actions")
    print(f"{'='*92}")
    print(f"sigma={sigma:.3f}  bankroll={args.bankroll:,.0f}  kelly={args.kelly}  cap={args.max_fraction:.0%}")
    print(f"{'rule_id':<34} {'n':>4} {'edge':>7} {'p_bleed':>8} {'action':<9} {'stake':>9}  rationale")
    print("-" * 92)
    for p in proposals:
        tag = "" if p["deployed"] else "  [ORPHAN]"
        print(f"{p['rule_id'][:34]:<34} {p['n']:>4} {p['post_mean']:>+6.1%} {p['p_bleeding']:>7.0%} "
              f"{p['action']:<9} {p['recommended_stake_abs']:>9,.0f}  {p['rationale']}{tag}")
    print(f"\nactions: {dict(sorted(counts.items()))}   wrote {args.out}")
    print(f"{'='*92}\n")


if __name__ == "__main__":
    main()
