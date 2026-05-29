#!/usr/bin/env python3
"""
propsniper_db.py — tap the LIVE engine.db (the machine's datastore) directly.

The reports/*.csv files are stale snapshots; engine.db is what the PropSniper
machine ingests into on every run. This reads `settled_bets` (+ `accounts`) and
computes the same feed shape the dashboard expects — so the dashboard reflects
whatever the machine last ingested, not a frozen export.

Realized P&L = settled bets (status win/loss). Windows are relative to the latest
settled date in the DB (so "1D" tracks the most recent trading day present).
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from propsniper_reports import person_of, venue_of, EXCLUDE_DEFAULT  # reuse helpers  # noqa: E402

DEFAULT_DB = Path.home() / "Propsniper" / "engine" / "engine.db"


def _verdict(n: int, roi: float) -> str:
    if n < 20:
        return "NOISE_N<20"
    if roi <= -0.02:
        return "BLEEDING"
    if roi >= 0.02:
        return "WORKING"
    return "FLAT"


def load_db_feed(db_path=None, exclude=None) -> dict:
    db = Path(db_path) if db_path else DEFAULT_DB
    excl = [e.lower() for e in (EXCLUDE_DEFAULT if exclude is None else exclude)]
    def excluded(name):
        a = (name or "").lower()
        return any(e in a for e in excl)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    venue_by_name = {}
    for r in cur.execute("SELECT name, sportsbook FROM accounts"):
        venue_by_name[r["name"]] = r["sportsbook"]

    rows = cur.execute(
        "SELECT date(placed_utc) d, placed_utc, placed_epoch, account_name, sportsbook, league, market, "
        "selection, odds_american, ev, stake, status, profit "
        "FROM settled_bets"
    ).fetchall()
    con.close()

    settled = [r for r in rows if (r["status"] or "").lower() in ("win", "loss", "push")
               and not excluded(r["account_name"])]
    if not settled:
        return {"source": "engine.db", "base": str(db), "generated_at": _now(),
                "portfolio": {}, "accounts": [], "markets": [], "pnl_timeseries": [],
                "recent": [], "book_brier": [], "counts": {"accounts": 0}}

    dates = [r["d"] for r in settled if r["d"]]
    maxd = max(dates)
    cut14 = (date.fromisoformat(maxd) - timedelta(days=13)).isoformat()

    def f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    # per-account and per-account-market aggregation
    acc = {}          # name -> agg
    accmkt = {}       # (name, league, market) -> agg
    daily = {}        # date -> profit
    for r in settled:
        name = r["account_name"]; pr = f(r["profit"]); st = f(r["stake"]); ev = f(r["ev"])
        a = acc.setdefault(name, {"n": 0, "profit": 0.0, "stake": 0.0, "ev": 0.0,
                                  "n14": 0, "profit14": 0.0, "stake14": 0.0})
        a["n"] += 1; a["profit"] += pr; a["stake"] += st; a["ev"] += ev
        if r["d"] and r["d"] >= cut14:
            a["n14"] += 1; a["profit14"] += pr; a["stake14"] += st
        k = (name, r["league"], r["market"])
        m = accmkt.setdefault(k, {"n": 0, "profit": 0.0, "stake": 0.0, "ev": 0.0})
        m["n"] += 1; m["profit"] += pr; m["stake"] += st; m["ev"] += ev
        if r["d"]:
            daily[r["d"]] = daily.get(r["d"], 0.0) + pr

    markets_by_acct = {}
    for (name, lg, mk), m in accmkt.items():
        roi = (m["profit"] / m["stake"]) if m["stake"] else 0.0
        markets_by_acct.setdefault(name, []).append({
            "league": lg, "market": mk, "n": m["n"], "profit": round(m["profit"], 2),
            "stake": round(m["stake"], 2), "roi": round(roi, 4),
            "avg_ev": round(m["ev"] / m["n"], 2) if m["n"] else 0,
            "verdict": _verdict(m["n"], roi)})

    accounts = []
    for name, a in acc.items():
        mkts = sorted(markets_by_acct.get(name, []), key=lambda x: x["profit"], reverse=True)
        roi = (a["profit"] / a["stake"]) if a["stake"] else 0.0
        accounts.append({
            "account": name, "person": person_of(name),
            "venue": venue_by_name.get(name) or venue_of(name),
            "sportsbook": venue_by_name.get(name) or venue_of(name),
            "n_all": a["n"], "profit_all": round(a["profit"], 2), "stake_all": round(a["stake"], 2),
            "roi_all": round(roi, 4), "avg_ev": round(a["ev"] / a["n"], 2) if a["n"] else 0,
            "n_14d": a["n14"], "profit_14d": round(a["profit14"], 2),
            "roi_14d": round(a["profit14"] / a["stake14"], 4) if a["stake14"] else 0.0,
            "balance": None, "health": None,
            "working": sum(1 for x in mkts if x["verdict"] == "WORKING"),
            "bleeding": sum(1 for x in mkts if x["verdict"] == "BLEEDING"),
            "markets": mkts})
    accounts.sort(key=lambda x: x["profit_all"], reverse=True)

    tp = sum(a["profit_all"] for a in accounts); tw = sum(a["stake_all"] for a in accounts)
    tn = sum(a["n_all"] for a in accounts)
    portfolio = {"profit": round(tp, 2), "wagered": round(tw, 2), "n": tn,
                 "roi": round(tp / tw, 4) if tw else None,
                 "profit_14d": round(sum(a["profit_14d"] for a in accounts), 2),
                 "accounts": len(accounts),
                 "winners": sum(1 for a in accounts if a["profit_all"] > 0),
                 "losers": sum(1 for a in accounts if a["profit_all"] < 0),
                 "data_through": maxd}

    pnl_timeseries, cum = [], 0.0
    for d in sorted(daily):
        cum += daily[d]
        pnl_timeseries.append({"date": d, "profit": round(daily[d], 2), "cumulative": round(cum, 2)})

    # cross-account market rollup
    roll = {}
    for a in accounts:
        for m in a["markets"]:
            r = roll.setdefault((m["league"], m["market"]),
                                {"league": m["league"], "market": m["market"], "profit": 0.0, "stake": 0.0, "n": 0})
            r["profit"] += m["profit"]; r["stake"] += m["stake"]; r["n"] += m["n"]
    markets = sorted(roll.values(), key=lambda x: x["profit"], reverse=True)
    for r in markets:
        r["roi"] = round(r["profit"] / r["stake"], 4) if r["stake"] else None
        r["profit"] = round(r["profit"], 2); r["stake"] = round(r["stake"], 2)

    recent = sorted([r for r in rows if not excluded(r["account_name"])],
                    key=lambda r: r["placed_epoch"] or 0, reverse=True)[:300]
    recent_rows = [{"date": (r["placed_utc"] or "")[:10], "time": (r["placed_utc"] or "")[11:19],
                    "account": r["account_name"], "book": r["sportsbook"], "league": r["league"],
                    "market": r["market"], "selection": r["selection"], "odds": r["odds_american"],
                    "ev": f(r["ev"]), "stake": f(r["stake"]), "status": (r["status"] or "").lower(),
                    "profit": f(r["profit"])} for r in recent]

    return {"source": "engine.db (live)", "base": str(db), "generated_at": _now(),
            "portfolio": portfolio, "accounts": accounts, "markets": markets,
            "pnl_timeseries": pnl_timeseries, "recent": recent_rows, "book_brier": [],
            "counts": {"accounts": len(accounts), "markets": len(markets),
                       "recent": len(recent_rows), "data_through": maxd}}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    f = load_db_feed(sys.argv[1] if len(sys.argv) > 1 else None)
    p = f["portfolio"]
    print(f"data_through={p.get('data_through')} accounts={p.get('accounts')} "
          f"profit=${p.get('profit')} roi={p.get('roi')} 14d=${p.get('profit_14d')}")
    print("daily tail:", f["pnl_timeseries"][-3:])
