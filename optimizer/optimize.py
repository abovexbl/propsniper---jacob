#!/usr/bin/env python3
"""
optimize.py — continuous-improvement loop over PropSniper configs, windowed.

For every rule it estimates the edge (Bayesian, shrunk) over each time window
(7d / 14d / 30d / 90d / all) and proposes an ACTION + fractional-Kelly stake per
window. The dashboard can then show and sort by any timeframe. Config-mutating
actions remain advisory and human-gated behind /audit.

Policy (per window, from posterior over edge mu and p_bleeding = P(mu < 0)):
    n < --min-n            -> EXPLORE   (too little data; small probe stake)
    p_bleeding >= 0.95     -> DISABLE
    0.75 <= p_bleeding     -> SHRINK
    p_bleeding <= 0.25 and post_mean > 0 -> PROMOTE
    otherwise              -> HOLD

Usage:
    python optimizer/optimize.py --bets data/data-panel-export-LATEST.csv --configs live/
    python optimizer/optimize.py --bets optimizer/sample_bets.csv --configs configs/sample_live
"""
from __future__ import annotations
import argparse
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bandit          # noqa: E402
import pslib           # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def decide(post: bandit.Posterior, min_n: int) -> tuple[str, str]:
    if post.n < min_n:
        return "EXPLORE", f"only {post.n} bets (< {min_n}); gather more before trusting the edge"
    if post.p_bleeding >= 0.95:
        return "DISABLE", f"{post.p_bleeding:.0%} confident edge < 0 over {post.n} bets"
    if post.p_bleeding >= 0.75:
        return "SHRINK", f"{post.p_bleeding:.0%} chance of negative edge; cut exposure"
    if post.p_bleeding <= 0.25 and post.post_mean > 0:
        return "PROMOTE", f"{1 - post.p_bleeding:.0%} confident positive edge (+{post.post_mean:.1%} ROI)"
    return "HOLD", f"edge {post.post_mean:+.1%}, p(bleed)={post.p_bleeding:.0%} — too uncertain to move"


def window_entry(rid, returns, sigma, args, rng) -> dict:
    post = bandit.update_posterior(rid, returns, sigma, prior_sd=args.prior_sd)
    action, rationale = decide(post, args.min_n)
    frac = bandit.kelly_fraction(post, args.kelly, args.max_fraction)
    if action == "DISABLE":
        frac = 0.0
    elif action == "EXPLORE":
        frac = min(max(frac, args.explore_floor), args.max_fraction)
    elif action == "SHRINK":
        frac *= 0.5
    return {
        **post.to_dict(),
        "action": action,
        "rationale": rationale,
        "thompson_sample": round(bandit.thompson_sample(post, rng), 6),
        "recommended_stake_fraction": round(frac, 6),
        "recommended_stake_abs": round(frac * args.bankroll, 2),
    }


def main():
    ap = argparse.ArgumentParser(description="Windowed continuous optimizer (Bayesian bandit + fractional Kelly)")
    ap.add_argument("--bets", required=True)
    ap.add_argument("--configs", required=True)
    ap.add_argument("--bankroll", type=float, default=10000.0)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--kelly", type=float, default=0.25)
    ap.add_argument("--max-fraction", type=float, default=0.05)
    ap.add_argument("--explore-floor", type=float, default=0.005)
    ap.add_argument("--prior-sd", type=float, default=0.5)
    ap.add_argument("--default-window", default="30d", choices=pslib.WINDOW_LABELS)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(REPO / "optimizer" / "proposals.json"))
    args = ap.parse_args()

    if not Path(args.bets).exists():
        print(f"ERROR: bets file not found: {args.bets}", file=sys.stderr)
        sys.exit(2)

    bets = pslib.load_bets(args.bets)
    if not bets:
        print("ERROR: no usable bets loaded", file=sys.stderr)
        sys.exit(2)
    markets = pslib.load_rule_markets(args.configs)
    deployed = set(markets)
    rng = random.Random(args.seed)

    # Reference 'now' = latest bet (so windows track recent activity).
    dated = [b["date"] for b in bets if b["date"]]
    now = max(dated) if dated else datetime.now()

    by_rule = defaultdict(list)            # rule -> [(date, roi)]
    for b in bets:
        by_rule[b["rule_id"]].append((b["date"], b["roi"]))

    sigma = bandit.empirical_sigma([b["roi"] for b in bets])

    proposals = []
    for rid in sorted(by_rule):
        windows = {}
        for label, days in pslib.WINDOWS:
            if days is None:
                rets = [r for _, r in by_rule[rid]]
            else:
                cutoff = now - timedelta(days=days)
                rets = [r for d, r in by_rule[rid] if d is not None and d >= cutoff]
            windows[label] = window_entry(rid, rets, sigma, args, rng)
        mkt = markets.get(rid, {})
        proposals.append({
            "rule_id": rid,
            "label": pslib.friendly_label(rid, mkt.get("league"), mkt.get("market")),
            "deployed": rid in deployed,
            "orphan": rid not in deployed,
            "league": mkt.get("league"),
            "market": mkt.get("market"),
            "windows": windows,
        })

    # Sort by the default window's Thompson sample (exploration priority).
    proposals.sort(key=lambda p: p["windows"][args.default_window]["thompson_sample"], reverse=True)

    summary_by_window = {}
    for label, _ in pslib.WINDOWS:
        counts = defaultdict(int)
        for p in proposals:
            counts[p["windows"][label]["action"]] += 1
        summary_by_window[label] = {
            "rules_scored": len(proposals),
            "orphans": sum(1 for p in proposals if p["orphan"]),
            "actions": dict(sorted(counts.items())),
        }

    payload = {
        "tool": "optimizer",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "windows": pslib.WINDOW_LABELS,
        "default_window": args.default_window,
        "params": {"bankroll": args.bankroll, "min_n": args.min_n, "kelly_mult": args.kelly,
                   "max_fraction": args.max_fraction, "prior_sd": args.prior_sd, "sigma": round(sigma, 6)},
        "summary_by_window": summary_by_window,
        "proposals": proposals,
        "note": "Advisory. Config-mutating actions are human-gated and must re-pass /audit before deploy.",
    }
    Path(args.out).write_text(json.dumps(payload, indent=2))

    # Human report (default window)
    w = args.default_window
    print(f"\n{'='*96}\nPropSniper Optimizer — proposed actions [{w} window]\n{'='*96}")
    print(f"sigma={sigma:.3f} bankroll={args.bankroll:,.0f} kelly={args.kelly} cap={args.max_fraction:.0%}")
    print(f"{'rule_id':<30} {'n':>4} {'edge':>7} {'p_bleed':>8} {'action':<9} {'stake':>9}  rationale")
    print("-" * 96)
    for p in proposals:
        e = p["windows"][w]
        tag = "" if p["deployed"] else "  [ORPHAN]"
        print(f"{p['rule_id'][:30]:<30} {e['n']:>4} {e['post_mean']:>+6.1%} {e['p_bleeding']:>7.0%} "
              f"{e['action']:<9} {e['recommended_stake_abs']:>9,.0f}  {e['rationale']}{tag}")
    print(f"\n{w} actions: {summary_by_window[w]['actions']}   wrote {args.out}\n{'='*96}\n")


if __name__ == "__main__":
    main()
