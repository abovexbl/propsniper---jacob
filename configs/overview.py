#!/usr/bin/env python3
"""
overview.py — flatten N PropSniper config files into a single inspectable table.

Use this to answer questions like:
  - "Which (venue, league, market) cells am I actually deployed on?"
  - "What EV floors am I using across accounts? Is my J/MO differentiation working?"
  - "Which comp books appear most often, and how often as required:true?"
  - "Which rules use side_filters? Which restrict odds range?"

Usage:
    python overview.py --dir from-jacob/
    python overview.py --dir from-jacob/ --format csv > rules.csv
    python overview.py --dir from-jacob/ --book-frequency
    python overview.py --dir from-jacob/ --cells-only
"""
from __future__ import annotations
import json
import argparse
import glob
import sys
import csv
from collections import Counter, defaultdict
from pathlib import Path


def load_rules(files: list[str]) -> list[dict]:
    """Flatten N config files into a list of rule records with provenance.

    Supports both schema variants:
      - flat:    { "v": 3, "devig": "worst", "rules": [...] }
      - wrapped: { "type":..., "filters": { "v":3, "rules":[...] } }
    """
    out = []
    for path in files:
        try:
            data = json.load(open(path))
        except (OSError, json.JSONDecodeError) as e:
            print(f"WARN: skipping {path}: {e}", file=sys.stderr)
            continue

        filters_block = data.get("filters", data) if data.get("type") else data
        rules = filters_block.get("rules", []) or []
        devig_mode = filters_block.get("devig")

        for rule in rules:
            mi = (rule.get("market_inclusion") or [{}])[0]
            f = rule.get("filters", {})
            markets = f.get("markets", {}).get("main", {})
            comps = markets.get("comparisons", []) or []
            books = [c.get("book") for c in comps]
            required_books = [c.get("book") for c in comps if c.get("required")]
            out.append({
                "file": Path(path).name,
                "rule_id": rule.get("id"),
                "league": mi.get("league") or (rule.get("leagues") or [None])[0],
                "market": mi.get("market"),
                "minEV": f.get("minEV"),
                "maxEV": f.get("maxEV"),
                "minOdds": f.get("minOdds"),
                "maxOdds": f.get("maxOdds"),
                "minHoursAway": f.get("minHoursAway"),
                "maxHoursAway": f.get("maxHoursAway"),
                "devig_mode": devig_mode,
                "stack": tuple(books),
                "required_book": required_books[0] if required_books else None,
                "stack_size": len(books),
                "required_count": markets.get("requiredComparisons"),
            })
    return out


def print_table(rules: list[dict]):
    cols = ["file", "rule_id", "league", "market", "minEV", "minOdds", "maxOdds",
            "maxHoursAway", "required_book", "stack"]
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rules), default=0)) for c in cols}
    # Cap stack column width
    widths["stack"] = min(widths["stack"], 60)
    widths["rule_id"] = min(widths["rule_id"], 38)
    widths["file"] = min(widths["file"], 32)

    sep = " | "
    header = sep.join(f"{c:<{widths[c]}}" for c in cols)
    print(header)
    print("-" * len(header))
    for r in rules:
        row = []
        for c in cols:
            v = str(r.get(c, "") or "")
            if c == "stack":
                v = ",".join(b or "?" for b in r["stack"])
            row.append(f"{v[:widths[c]]:<{widths[c]}}")
        print(sep.join(row))


def print_csv(rules: list[dict]):
    cols = ["file", "rule_id", "league", "market", "minEV", "maxEV", "minOdds", "maxOdds",
            "minHoursAway", "maxHoursAway", "devig_mode", "stack", "required_book",
            "stack_size", "required_count"]
    w = csv.DictWriter(sys.stdout, fieldnames=cols)
    w.writeheader()
    for r in rules:
        row = dict(r)
        row["stack"] = ",".join(b or "?" for b in r["stack"])
        w.writerow(row)


def print_book_frequency(rules: list[dict]):
    """How often does each book appear, and how often as required:true?"""
    appears = Counter()
    required = Counter()
    for r in rules:
        for b in r["stack"]:
            if b:
                appears[b] += 1
        if r.get("required_book"):
            required[r["required_book"]] += 1
    print(f"\n{'Book':<20} {'Appearances':>12} {'As required':>12} {'% required':>10}")
    print("-" * 60)
    for book, n in appears.most_common():
        req = required.get(book, 0)
        pct = (req / n * 100) if n else 0
        print(f"{book:<20} {n:>12} {req:>12} {pct:>9.1f}%")


def print_cells(rules: list[dict]):
    """One row per (venue-from-file, league, market) cell, showing all rule IDs."""
    cells = defaultdict(list)
    for r in rules:
        # Venue inferred from filename token (jacob/jac/dad/mom + book)
        fname = r["file"]
        parts = fname.replace("devig_", "").replace("_v2.json", "").split("_")
        if len(parts) >= 2:
            user, venue = parts[0], "_".join(parts[1:])
        else:
            user, venue = "?", "?"
        cells[(user, venue, r["league"], r["market"])].append(r["rule_id"])

    print(f"\n{'User':<8} {'Venue':<12} {'League':<10} {'Market':<35} {'Rules'}")
    print("-" * 100)
    for (user, venue, league, market), ids in sorted(cells.items()):
        print(f"{user:<8} {venue:<12} {(league or '?'):<10} {(market or '?'):<35} {','.join(ids)}")


def print_summary(rules: list[dict]):
    by_user = defaultdict(list)
    for r in rules:
        u = r["file"].replace("devig_", "").split("_")[0]
        by_user[u].append(r["minEV"])

    print(f"\nSummary: {len(rules)} rules across {len({r['file'] for r in rules})} files")
    print(f"\nEV floor distribution by user (filename token):")
    for u, floors in sorted(by_user.items()):
        floors = [x for x in floors if x is not None]
        avg = sum(floors) / len(floors) if floors else 0
        print(f"  {u:<8} n={len(floors):>3}  avg={avg:.1f}  range=[{min(floors)},{max(floors)}]")

    leagues = Counter(r["league"] for r in rules)
    print(f"\nLeague distribution:")
    for lg, n in leagues.most_common():
        print(f"  {lg or '?':<12} {n}")

    markets = Counter((r["league"], r["market"]) for r in rules)
    print(f"\nTop 10 (league, market) cells:")
    for (lg, mk), n in markets.most_common(10):
        print(f"  {(lg or '?'):<10} {(mk or '?'):<35} {n}")


def main():
    ap = argparse.ArgumentParser(description="Flatten PropSniper configs into one inspectable view")
    ap.add_argument("--dir", help="directory of devig_*.json files")
    ap.add_argument("paths", nargs="*", help="explicit config files")
    ap.add_argument("--format", choices=["table", "csv"], default="table")
    ap.add_argument("--book-frequency", action="store_true", help="show book usage frequency table")
    ap.add_argument("--cells-only", action="store_true", help="show (user, venue, league, market) cells")
    ap.add_argument("--summary-only", action="store_true", help="show only the summary stats")
    args = ap.parse_args()

    files = []
    if args.dir:
        files.extend(sorted(glob.glob(str(Path(args.dir) / "devig_*.json"))))
    for p in args.paths:
        files.extend(sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p])

    if not files:
        print("ERROR: no files. Pass --dir or paths.", file=sys.stderr)
        sys.exit(2)

    rules = load_rules(files)
    if not rules:
        print("ERROR: no rules parsed", file=sys.stderr)
        sys.exit(2)

    if args.book_frequency:
        print_book_frequency(rules)
    elif args.cells_only:
        print_cells(rules)
    elif args.summary_only:
        print_summary(rules)
    elif args.format == "csv":
        print_csv(rules)
    else:
        print_table(rules)
        print_summary(rules)


if __name__ == "__main__":
    main()
