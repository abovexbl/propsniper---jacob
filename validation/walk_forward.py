#!/usr/bin/env python3
"""
walk_forward.py — temporal-split Brier stability check for comp book rankings.

The Operations Handoff Guide §5 (priority 2 of comp book selection) calls for
"walk-forward stability: STABLE (positive delta in both H1 and H2)." This script
implements that check explicitly.

Input: an OddsPapi export CSV (gzipped) for a sport, with columns including
  fixtureId, bookmaker, marketId, marketName, marketType, outcomeId,
  closePrice, closeTime, settlement, playerId
Plus a fixtures.csv with fixtureId, startTime.

Pipeline (per Operations Handoff Guide §4):
  1. Load and join. Filter to is_live = closeTime > startTime.
  2. Filter to settlement in {WIN, LOSE}.
  3. Pair outcomes by (fixtureId, marketId, bookmaker, playerId).
  4. Filter to 0.99 <= sum_implied <= 1.5.
  5. Multiplicative devig: fair_p = p / (p_A + p_B).
  6. Brier = mean((fair_p - outcome)**2) per (marketName, bookmaker).
  7. Split fixtures temporally 50/50 (or configurable). Recompute Brier per half.
  8. For each (market, book) cell, report:
       - n_total, n_h1, n_h2
       - brier_total, brier_h1, brier_h2
       - rank_h1, rank_h2, rank_delta
       - STABLE / UNSTABLE flag (rank moves by <= 2 positions AND both halves have n_book >= 50)

Output: per-cell stability table. Books that fail walk-forward should NOT be stack-eligible.

Usage:
    python walk_forward.py \\
        --summary path/to/oddspapi_mlb_summary_main_csv.gz \\
        --fixtures path/to/oddspapi_mlb_fixtures.csv \\
        --min-n-per-book 50

This script intentionally does not depend on pandas/numpy — uses only stdlib
so it can run in any Python 3.10+ environment without setup.
"""
from __future__ import annotations
import argparse
import csv
import gzip
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def open_maybe_gzip(path: str):
    """Open .gz or plain file transparently as text."""
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


def load_fixtures(path: str) -> dict[str, str]:
    """Returns {fixtureId: startTime_iso}."""
    out = {}
    with open_maybe_gzip(path) as fh:
        for row in csv.DictReader(fh):
            fid = row.get("fixtureId") or row.get("fixture_id")
            start = row.get("startTime") or row.get("start_time")
            if fid and start:
                out[fid] = start
    return out


@dataclass
class Outcome:
    fixture_id: str
    market_id: str
    market_name: str
    bookmaker: str
    player_id: str
    outcome_id: str
    close_price: float
    close_time: str
    settlement: str  # WIN / LOSE / PUSH / ""


def load_outcomes(path: str) -> Iterable[Outcome]:
    """Yield Outcome rows from a summary CSV (gz or plain)."""
    with open_maybe_gzip(path) as fh:
        for row in csv.DictReader(fh):
            try:
                price = float(row.get("closePrice") or row.get("close_price") or "")
            except (TypeError, ValueError):
                continue
            yield Outcome(
                fixture_id=row.get("fixtureId") or row.get("fixture_id", ""),
                market_id=row.get("marketId") or row.get("market_id", ""),
                market_name=row.get("marketName") or row.get("market_name", ""),
                bookmaker=(row.get("bookmaker") or "").strip(),
                player_id=str(row.get("playerId") or row.get("player_id") or "0"),
                outcome_id=str(row.get("outcomeId") or row.get("outcome_id") or ""),
                close_price=price,
                close_time=row.get("closeTime") or row.get("close_time", ""),
                settlement=(row.get("settlement") or "").strip().upper(),
            )


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def american_to_implied(odds: float) -> float:
    if odds == 0:
        return float("nan")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def filter_live(outcomes: Iterable[Outcome], fixtures: dict[str, str]) -> list[Outcome]:
    """Step 1: is_live = closeTime > startTime."""
    out = []
    for o in outcomes:
        start = fixtures.get(o.fixture_id)
        if not start:
            continue
        if o.close_time > start:  # ISO strings compare correctly
            out.append(o)
    return out


def filter_settled(outcomes: Iterable[Outcome]) -> list[Outcome]:
    """Step 2: settlement in {WIN, LOSE}."""
    return [o for o in outcomes if o.settlement in {"WIN", "LOSE"}]


def pair_and_devig(outcomes: list[Outcome]) -> list[tuple[Outcome, float]]:
    """Steps 3-5: pair outcomes by (fixture, market, book, player), devig multiplicatively.

    Returns list of (outcome, fair_p) for each row that paired and passed sanity checks.
    """
    groups = defaultdict(list)
    for o in outcomes:
        key = (o.fixture_id, o.market_id, o.bookmaker, o.player_id)
        groups[key].append(o)

    out = []
    for key, rows in groups.items():
        if len(rows) != 2:
            continue  # need exactly 2 sides
        a, b = rows
        pa = american_to_implied(a.close_price)
        pb = american_to_implied(b.close_price)
        if pa != pa or pb != pb:  # NaN check
            continue
        s = pa + pb
        if not (0.99 <= s <= 1.5):
            continue
        fair_a = pa / s
        fair_b = pb / s
        out.append((a, fair_a))
        out.append((b, fair_b))
    return out


def split_by_fixture_time(
    pairs: list[tuple[Outcome, float]],
    fixtures: dict[str, str],
    split_quantile: float = 0.5,
) -> tuple[set[str], set[str]]:
    """Return (h1_fixture_ids, h2_fixture_ids) split by startTime quantile."""
    fids = {p[0].fixture_id for p in pairs}
    sorted_fids = sorted(fids, key=lambda fid: fixtures.get(fid, ""))
    cut = int(len(sorted_fids) * split_quantile)
    h1 = set(sorted_fids[:cut])
    h2 = set(sorted_fids[cut:])
    return h1, h2


def compute_brier_per_cell(
    pairs: list[tuple[Outcome, float]],
    fixture_subset: set[str] | None = None,
) -> dict[tuple[str, str], tuple[float, int]]:
    """Return {(market_name, bookmaker): (brier, n)}."""
    by_cell = defaultdict(list)
    for o, fair_p in pairs:
        if fixture_subset is not None and o.fixture_id not in fixture_subset:
            continue
        outcome = 1.0 if o.settlement == "WIN" else 0.0
        by_cell[(o.market_name, o.bookmaker)].append((fair_p - outcome) ** 2)
    out = {}
    for cell, errs in by_cell.items():
        out[cell] = (sum(errs) / len(errs), len(errs))
    return out


def rank_by_brier(brier_per_cell: dict[tuple[str, str], tuple[float, int]]) -> dict[str, list[tuple[str, float, int]]]:
    """Per market_name, rank books ascending by Brier. Returns {market: [(book, brier, n), ...]}."""
    by_market = defaultdict(list)
    for (market, book), (brier, n) in brier_per_cell.items():
        by_market[market].append((book, brier, n))
    for m in by_market:
        by_market[m].sort(key=lambda x: x[1])
    return dict(by_market)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def print_stability_report(
    rank_full: dict[str, list[tuple[str, float, int]]],
    rank_h1: dict[str, list[tuple[str, float, int]]],
    rank_h2: dict[str, list[tuple[str, float, int]]],
    min_n_per_book: int = 50,
    stable_rank_threshold: int = 2,
):
    """Print per-market table showing rank in each half + STABLE flag."""
    markets = sorted(rank_full.keys())
    if not markets:
        print("No markets with sufficient data.")
        return

    print(f"\n{'='*100}")
    print(f"Walk-Forward Stability Report")
    print(f"{'='*100}")
    print(f"Min n per book: {min_n_per_book}")
    print(f"STABLE = rank moves by <= {stable_rank_threshold} positions between halves AND both halves have n>={min_n_per_book}")
    print()

    summary_stable = 0
    summary_unstable = 0

    for market in markets:
        rows = rank_full[market]
        h1_map = {b: (br, n, idx) for idx, (b, br, n) in enumerate(rank_h1.get(market, []))}
        h2_map = {b: (br, n, idx) for idx, (b, br, n) in enumerate(rank_h2.get(market, []))}

        # Display top 8 by full-period Brier
        print(f"\n--- {market} ---")
        print(f"{'rank':>4} {'book':<15} {'brier':>8} {'n':>6} | {'h1_rank':>7} {'h1_brier':>9} {'h1_n':>6} | {'h2_rank':>7} {'h2_brier':>9} {'h2_n':>6} | {'flag':<10}")
        for idx, (book, brier, n) in enumerate(rows[:8]):
            h1 = h1_map.get(book, (None, 0, None))
            h2 = h2_map.get(book, (None, 0, None))

            h1_rank_str = str(h1[2] + 1) if h1[2] is not None else "—"
            h2_rank_str = str(h2[2] + 1) if h2[2] is not None else "—"
            h1_brier_str = f"{h1[0]:.4f}" if h1[0] is not None else "—"
            h2_brier_str = f"{h2[0]:.4f}" if h2[0] is not None else "—"

            if h1[2] is None or h2[2] is None:
                flag = "MISSING"
                summary_unstable += 1
            elif h1[1] < min_n_per_book or h2[1] < min_n_per_book:
                flag = "LOW_N"
                summary_unstable += 1
            elif abs(h1[2] - h2[2]) > stable_rank_threshold:
                flag = "UNSTABLE"
                summary_unstable += 1
            else:
                flag = "STABLE"
                summary_stable += 1

            print(f"{idx+1:>4} {book:<15} {brier:>8.4f} {n:>6} | "
                  f"{h1_rank_str:>7} {h1_brier_str:>9} {h1[1]:>6} | "
                  f"{h2_rank_str:>7} {h2_brier_str:>9} {h2[1]:>6} | {flag:<10}")

    print(f"\n{'='*100}")
    print(f"Summary: {summary_stable} stable cells, {summary_unstable} unstable/missing/low-n")
    print(f"{'='*100}\n")


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Walk-forward stability check for PropSniper comp books")
    ap.add_argument("--summary", required=True, help="path to oddspapi_{sport}_summary_*.csv[.gz]")
    ap.add_argument("--fixtures", required=True, help="path to oddspapi_{sport}_fixtures.csv")
    ap.add_argument("--split", type=float, default=0.5, help="temporal split point (0-1, default 0.5)")
    ap.add_argument("--min-n-per-book", type=int, default=50, help="minimum outcomes per book per half")
    ap.add_argument("--rank-threshold", type=int, default=2, help="STABLE if abs(rank_h1 - rank_h2) <= this")
    args = ap.parse_args()

    if not Path(args.fixtures).exists():
        print(f"ERROR: fixtures file not found: {args.fixtures}", file=sys.stderr)
        sys.exit(2)
    if not Path(args.summary).exists():
        print(f"ERROR: summary file not found: {args.summary}", file=sys.stderr)
        sys.exit(2)

    print(f"Loading fixtures from {args.fixtures}...", file=sys.stderr)
    fixtures = load_fixtures(args.fixtures)
    print(f"  loaded {len(fixtures)} fixtures", file=sys.stderr)

    print(f"Loading outcomes from {args.summary}...", file=sys.stderr)
    outcomes = list(load_outcomes(args.summary))
    print(f"  loaded {len(outcomes)} raw outcomes", file=sys.stderr)

    live = filter_live(outcomes, fixtures)
    print(f"  {len(live)} after is_live filter", file=sys.stderr)

    settled = filter_settled(live)
    print(f"  {len(settled)} after settlement filter (WIN/LOSE only)", file=sys.stderr)

    pairs = pair_and_devig(settled)
    print(f"  {len(pairs)} after pairing + multiplicative devig + sanity", file=sys.stderr)

    h1, h2 = split_by_fixture_time(pairs, fixtures, args.split)
    print(f"  H1: {len(h1)} fixtures, H2: {len(h2)} fixtures", file=sys.stderr)

    brier_full = compute_brier_per_cell(pairs)
    brier_h1 = compute_brier_per_cell(pairs, h1)
    brier_h2 = compute_brier_per_cell(pairs, h2)

    rank_full = rank_by_brier(brier_full)
    rank_h1 = rank_by_brier(brier_h1)
    rank_h2 = rank_by_brier(brier_h2)

    print_stability_report(rank_full, rank_h1, rank_h2, args.min_n_per_book, args.rank_threshold)


if __name__ == "__main__":
    main()
