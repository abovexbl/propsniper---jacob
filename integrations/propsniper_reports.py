#!/usr/bin/env python3
"""
propsniper_reports.py — ingest the REAL PropSniper engine reports into one feed.

Your PropSniper machine already produces aggregated reports (per-account P&L, by
market, daily, working-vs-bleeding cells, book Brier). This reads them directly
instead of forcing raw bets through the synthetic optimizer, and emits a single
structured feed for the account -> venue -> market dashboard.

Reads (defensively — missing files/cols are tolerated):
  reports/bets_summary.csv            per-account P&L (all + 14d)
  reports/bets_by_market.csv          account x league x market P&L
  reports/bets_daily.csv              daily P&L per account
  reports/bets_recent.csv             individual recent bets
  reports/cells_working_vs_bleeding.csv  per-cell WORKING/BLEEDING verdict
  reports/accounts_list.csv           account metadata (balance, health, kelly)
  engine/reports/own_pnl.csv          account x market P&L + flag
  engine/reports/book_brier.csv       book Brier ranking per market

REAL DATA: never published or committed. Served only to the private dashboard.
"""
from __future__ import annotations
import csv
import io
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE = Path.home() / "Propsniper"

_VENUE_KW = [
    ("czr", "Caesars"), ("cesars", "Caesars"), ("caesar", "Caesars"),
    ("draftking", "DraftKings"), ("dk", "DraftKings"), ("fanduel", "FanDuel"),
    ("fd", "FanDuel"), ("fliff", "Fliff"), ("kalshi", "Kalshi"), ("rebet", "Rebet"),
    ("betmgm", "BetMGM"), ("mgm", "BetMGM"), ("underdog", "Underdog"),
    ("bovada", "Bovada"), ("hardrock", "Hardrock"),
]
_PERSON_KW = [("dad", "Dad"), ("mom", "Mom")]


def _num(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return default


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def venue_of(account: str) -> str:
    a = (account or "").lower()
    for kw, name in _VENUE_KW:
        if kw in a:
            return name
    return "Other"


def person_of(account: str) -> str:
    a = (account or "").lower()
    for kw, name in _PERSON_KW:
        if kw in a:
            return name
    return "Jacob"  # default / unlabeled accounts are Jacob's


def clean_name(raw: str, sportsbook: str | None = None) -> str:
    """Uniform account display name: '<Person> <Sportsbook>'.
    'DraftKings'->'Jacob DraftKings', 'MOM CESARS'->'Mom Caesars',
    'DAD FD'->'Dad FanDuel', 'Moms account'(Kalshi)->'Mom Kalshi'."""
    return f"{person_of(raw)} {sportsbook or venue_of(raw)}".strip()


# Accounts to hide by default (substring match, case-insensitive). Edit to re-include.
EXCLUDE_DEFAULT = ["rebet", "mgm"]


def load_real_feed(base=None, exclude=None) -> dict:
    base = Path(base) if base else DEFAULT_BASE
    rep, eng = base / "reports", base / "engine" / "reports"
    excl = [e.lower() for e in (EXCLUDE_DEFAULT if exclude is None else exclude)]
    def excluded(name):
        a = (name or "").lower()
        return any(e in a for e in excl)

    summary = _read(rep / "bets_summary.csv")
    by_market = _read(rep / "bets_by_market.csv")
    daily = _read(rep / "bets_daily.csv")
    recent = _read(rep / "bets_recent.csv")
    cells = _read(rep / "cells_working_vs_bleeding.csv")
    acct_meta = {r.get("name"): r for r in _read(rep / "accounts_list.csv")}
    brier = _read(eng / "book_brier.csv")

    # verdict lookup by (account, league, market)
    verdict = {}
    for c in cells:
        verdict[(c.get("Account"), c.get("League"), c.get("Market"))] = c.get("verdict")

    # markets grouped by account
    markets_by_acct: dict[str, list] = {}
    for m in by_market:
        acct = m.get("Account")
        if excluded(acct):
            continue
        v = verdict.get((acct, m.get("League"), m.get("Market")))
        markets_by_acct.setdefault(acct, []).append({
            "league": m.get("League"), "market": m.get("Market"),
            "n": int(_num(m.get("n"))), "profit": round(_num(m.get("profit")), 2),
            "stake": round(_num(m.get("stake")), 2), "roi": round(_num(m.get("roi_pct")) / 100, 4),
            "avg_ev": round(_num(m.get("avg_ev")), 2),
            "verdict": (v or ("WORKING" if _num(m.get("profit")) >= 0 else "BLEEDING")).upper(),
        })

    accounts = []
    for s in summary:
        name = s.get("Account")
        if excluded(name):
            continue
        mkts = sorted(markets_by_acct.get(name, []), key=lambda x: x["profit"], reverse=True)
        meta = acct_meta.get(name, {})
        accounts.append({
            "account": name, "person": person_of(name), "venue": venue_of(name),
            "n_all": int(_num(s.get("n_all"))), "profit_all": round(_num(s.get("profit_all")), 2),
            "stake_all": round(_num(s.get("stake_all")), 2), "roi_all": round(_num(s.get("roi_pct_all")) / 100, 4),
            "avg_ev": round(_num(s.get("avg_ev_all")), 2),
            "n_14d": int(_num(s.get("n_14d"))), "profit_14d": round(_num(s.get("profit_14d")), 2),
            "roi_14d": round(_num(s.get("roi_pct_14d")) / 100, 4),
            "balance": round(_num(meta.get("balance")), 2) if meta.get("balance") else None,
            "health": meta.get("health_status") or None,
            "sportsbook": meta.get("sportsbook") or venue_of(name),
            "working": sum(1 for x in mkts if x["verdict"] == "WORKING"),
            "bleeding": sum(1 for x in mkts if x["verdict"] == "BLEEDING"),
            "markets": mkts,
        })
    accounts.sort(key=lambda a: a["profit_all"], reverse=True)

    # portfolio totals
    tp = sum(a["profit_all"] for a in accounts)
    tw = sum(a["stake_all"] for a in accounts)
    tn = sum(a["n_all"] for a in accounts)
    portfolio = {"profit": round(tp, 2), "wagered": round(tw, 2), "n": tn,
                 "roi": round(tp / tw, 4) if tw else None,
                 "profit_14d": round(sum(a["profit_14d"] for a in accounts), 2),
                 "accounts": len(accounts),
                 "winners": sum(1 for a in accounts if a["profit_all"] > 0),
                 "losers": sum(1 for a in accounts if a["profit_all"] < 0)}

    # daily cumulative across all (non-excluded) accounts
    by_day: dict[str, float] = {}
    for d in daily:
        if excluded(d.get("Account")):
            continue
        by_day[d.get("date")] = by_day.get(d.get("date"), 0.0) + _num(d.get("profit"))
    pnl_timeseries, cum = [], 0.0
    for day in sorted(k for k in by_day if k):
        cum += by_day[day]
        pnl_timeseries.append({"date": day, "profit": round(by_day[day], 2), "cumulative": round(cum, 2)})

    # cross-account market rollup (excluded accounts omitted)
    mkt_roll: dict[tuple, dict] = {}
    for m in by_market:
        if excluded(m.get("Account")):
            continue
        key = (m.get("League"), m.get("Market"))
        r = mkt_roll.setdefault(key, {"league": key[0], "market": key[1], "profit": 0.0, "stake": 0.0, "n": 0})
        r["profit"] += _num(m.get("profit")); r["stake"] += _num(m.get("stake")); r["n"] += int(_num(m.get("n")))
    markets = sorted(mkt_roll.values(), key=lambda x: x["profit"], reverse=True)
    for r in markets:
        r["roi"] = round(r["profit"] / r["stake"], 4) if r["stake"] else None
        r["profit"] = round(r["profit"], 2); r["stake"] = round(r["stake"], 2)

    recent_rows = [{
        "date": r.get("Date"), "time": r.get("Time"), "account": r.get("Account"),
        "book": r.get("Sportsbook"), "league": r.get("League"), "market": r.get("Market"),
        "selection": r.get("Selection") or r.get("Participant"), "odds": r.get("Odds"),
        "ev": _num(r.get("EV %")), "stake": _num(r.get("Stake")),
        "status": (r.get("Status") or "").lower(), "profit": _num(r.get("Profit")),
    } for r in recent if not excluded(r.get("Account"))][:300]

    brier_rows = sorted([{
        "league": b.get("league"), "market": b.get("market"), "book": b.get("book"),
        "brier": _num(b.get("brier")), "n": int(_num(b.get("n"))),
        "coverage": _num(b.get("coverage")), "rank": int(_num(b.get("rank"))),
    } for b in brier], key=lambda x: (x["league"] or "", x["market"] or "", x["rank"]))[:200]

    return {
        "source": "real", "base": str(base),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "portfolio": portfolio,
        "accounts": accounts,
        "markets": markets,
        "pnl_timeseries": pnl_timeseries,
        "recent": recent_rows,
        "book_brier": brier_rows,
        "counts": {"accounts": len(accounts), "markets": len(markets),
                   "recent": len(recent_rows), "brier": len(brier_rows)},
    }


if __name__ == "__main__":
    import json
    import sys
    f = load_real_feed(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"accounts={f['counts']['accounts']} markets={f['counts']['markets']} "
          f"portfolio profit={f['portfolio']['profit']} roi={f['portfolio']['roi']}")
