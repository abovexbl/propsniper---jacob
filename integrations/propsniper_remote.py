#!/usr/bin/env python3
"""
propsniper_remote.py — LIVE personal data from PropSniper's LOCAL backend.

No export, no Wireshark, no cloud scraping. The desktop app's own endpoints
(127.0.0.1:8080, Bearer .backend_token) serve everything in real time:
    /v1/remote/dashboard      -> portfolio stats incl today_profit / today_roi
    /v1/remote/daily-profit   -> [{day, profit, cumulative_profit}] (live)
    /v1/remote/bets/active    -> live/active bets w/ outcomes
    /v1/accounts              -> per-account profit / balance / health / bets

Per-MARKET history isn't on the local API (no settled-history route), so we
enrich each account's market breakdown from engine.db (the export) when present.
Headline KPIs, today's P&L, the daily chart, and per-account numbers are LIVE.
"""
from __future__ import annotations
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from propsniper_reports import person_of, venue_of, clean_name, EXCLUDE_DEFAULT  # noqa: E402
import propsniper_db as pdb  # for per-market enrichment from engine.db  # noqa: E402

TOKEN_FILE = Path.home() / "AppData" / "Roaming" / "com.propsniper" / ".backend_token"
BASE = "http://127.0.0.1:8080"
TZ = "America/New_York"


def _get(path: str, tok: str):
    req = urllib.request.Request(BASE + path,
                                 headers={"Authorization": f"Bearer {tok}", "Origin": "http://localhost:8080"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def available(token_file=None) -> bool:
    tf = Path(token_file) if token_file else TOKEN_FILE
    if not tf.exists():
        return False
    try:
        _get("/v1/health", tf.read_text().strip())
        return True
    except Exception:
        return False


def load_remote_feed(token_file=None, exclude=None, db_path=None) -> dict:
    tf = Path(token_file) if token_file else TOKEN_FILE
    tok = tf.read_text().strip()
    excl = [e.lower() for e in (EXCLUDE_DEFAULT if exclude is None else exclude)]
    def excluded(name):
        a = (name or "").lower()
        return any(e in a for e in excl)

    tzq = urllib.parse.quote(TZ)
    stats = _get(f"/v1/remote/dashboard?timezone={tzq}", tok).get("stats", {})
    daily = _get(f"/v1/remote/daily-profit?timezone={tzq}", tok).get("daily_profit", [])
    accts = _get("/v1/accounts", tok).get("accounts", [])
    active = _get("/v1/remote/bets/active", tok).get("bets", [])

    # per-market breakdown from engine.db (export history), keyed by account name
    markets_by_acct = {}
    try:
        dbfeed = pdb.load_db_feed(db_path, exclude=exclude)
        for a in dbfeed.get("accounts", []):
            markets_by_acct[a["account"]] = a.get("markets", [])
    except Exception:
        pass

    # accounts — LIVE profit/balance from /v1/accounts, markets enriched from db
    accounts = []
    for a in accts:
        name = a.get("name")
        if excluded(name):
            continue
        mkts = markets_by_acct.get(name, [])
        profit = round(_num(a.get("profit")), 2)
        stake = sum(m["stake"] for m in mkts) if mkts else 0.0
        accounts.append({
            "account": clean_name(name, a.get("sportsbook")), "account_raw": name,
            "person": person_of(name),
            "venue": a.get("sportsbook") or venue_of(name),
            "sportsbook": a.get("sportsbook") or venue_of(name),
            "n_all": int(_num(a.get("num_bets"))),
            "profit_all": profit, "stake_all": round(stake, 2),
            "roi_all": round(profit / stake, 4) if stake else None,
            "n_14d": 0, "profit_14d": 0.0, "roi_14d": None,
            "balance": round(_num(a.get("balance")), 2),
            "health": a.get("health_status"),
            "working": sum(1 for m in mkts if m.get("verdict") == "WORKING"),
            "bleeding": sum(1 for m in mkts if m.get("verdict") == "BLEEDING"),
            "markets": mkts,
        })
    accounts.sort(key=lambda x: x["profit_all"], reverse=True)

    total_profit = round(_num(stats.get("total_profit")), 2)
    total_staked = round(_num(stats.get("total_staked")), 2)
    portfolio = {
        "profit": total_profit, "wagered": total_staked,
        "roi": round(total_profit / total_staked, 4) if total_staked else None,
        "n": int(_num(stats.get("graded_bets"))),
        "accounts": len(accounts),
        "winners": sum(1 for a in accounts if a["profit_all"] > 0),
        "losers": sum(1 for a in accounts if a["profit_all"] < 0),
        # LIVE today + open exposure
        "today_profit": round(_num(stats.get("today_profit")), 2),
        "today_roi": round(_num(stats.get("today_roi")) / 100, 4),
        "today_wagered": round(_num(stats.get("today_wagered")), 2),
        "open_bets": int(_num(stats.get("open_bets"))),
        "live_bets": int(_num(stats.get("live_bets"))),
        "wins": int(_num(stats.get("wins"))), "losses": int(_num(stats.get("losses"))),
        "data_through": "live",
    }

    pnl_timeseries = [{"date": d.get("day"), "profit": round(_num(d.get("profit")), 2),
                       "cumulative": round(_num(d.get("cumulative_profit")), 2)} for d in daily if d.get("day")]

    # cross-account market rollup (from enriched markets)
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

    clean_map = {a.get("name"): clean_name(a.get("name"), a.get("sportsbook")) for a in accts}
    recent = [{"date": _ts(b.get("created_at_ts"))[:10], "time": _ts(b.get("created_at_ts"))[11:19],
               "account": clean_map.get(b.get("account_name"), clean_name(b.get("account_name"), b.get("sportsbook"))),
               "book": b.get("sportsbook"), "league": b.get("league"),
               "market": b.get("market"), "selection": b.get("selection") or b.get("participant"),
               "odds": b.get("odds"), "ev": _num(b.get("ev")), "stake": _num(b.get("amount")),
               "status": (b.get("result") or b.get("outcome") or "open").lower(), "profit": _num(b.get("payout"))}
              for b in active if not excluded(b.get("account_name"))]

    return {"source": "engine local API (live)", "base": BASE,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "portfolio": portfolio, "accounts": accounts, "markets": markets,
            "pnl_timeseries": pnl_timeseries, "recent": recent, "book_brier": [],
            "counts": {"accounts": len(accounts), "markets": len(markets), "recent": len(recent)}}


def _ts(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


if __name__ == "__main__":
    f = load_remote_feed()
    p = f["portfolio"]
    print(f"LIVE  total=${p['profit']:,} roi={p['roi']}  TODAY=${p['today_profit']:,} (roi {p['today_roi']}) "
          f"open={p['open_bets']}  accounts={p['accounts']} daily_pts={len(f['pnl_timeseries'])} recent={len(f['recent'])}")
