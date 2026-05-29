#!/usr/bin/env python3
"""
pslib.py — shared loaders for the PropSniper machine pipeline.

Centralizes config + bet-history loading so optimize.py / run_machine.py /
community don't each reimplement it. Pure stdlib. The standalone audit/overview/
walk-forward tools keep their own loaders (so they remain self-contained drop-ins);
this lib backs the newer feed pipeline.
"""
from __future__ import annotations
import csv
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

# Time windows used across the pipeline. (label, days) — None days == all-time.
WINDOWS: list[tuple[str, int | None]] = [
    ("7d", 7), ("14d", 14), ("30d", 30), ("90d", 90), ("all", None)
]
WINDOW_LABELS = [w[0] for w in WINDOWS]

# Rule-ID venue prefix -> display name, for clean human-readable labels.
VENUE_NAMES = {
    "cae": "Caesars", "czr": "Caesars", "dk": "DraftKings", "fd": "FanDuel",
    "flf": "Fliff", "kal": "Kalshi", "reb": "Rebet", "mgm": "BetMGM",
    "fan": "Fanatics", "br": "BetRivers", "hr": "Hardrock", "bet": "BetMGM",
}


def venue_name(rule_id: str) -> str:
    tok = (rule_id or "").split("_")[0].lower()
    return VENUE_NAMES.get(tok, tok.upper() if tok else "?")


def friendly_label(rule_id: str, league: str | None = None, market: str | None = None) -> str:
    """e.g. ('cae_mlb_total_runs_f5','MLB','Total Runs 1st 5 Innings')
    -> 'Caesars MLB Total Runs 1st 5 Innings'. One clean line; raw id kept separately."""
    parts = [venue_name(rule_id)]
    if league:
        parts.append(league)
    if market:
        parts.append(market)
    return " ".join(p for p in parts if p)


_BET_COLUMNS = {
    "ruleId": ["ruleId", "rule_id", "RuleID", "Rule", "rule"],
    "stake":  ["stake", "Stake", "wager", "Wager"],
    "profit": ["profit", "Profit", "netProfit", "net_profit"],
    "date":   ["datePlaced", "date_placed", "Date", "date", "createdAt", "created_at"],
}

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    "%m/%d/%Y", "%Y-%m-%dT%H:%M:%SZ",
)


def parse_date(s: str) -> datetime | None:
    if not s:
        return None
    clean = s.split("+")[0].split(".")[0].strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(clean, fmt.replace("Z", ""))
        except ValueError:
            continue
    return None


def _resolve(row: dict, key: str) -> str | None:
    for c in _BET_COLUMNS[key]:
        if c in row:
            return c
    return None


def load_bets(path: str) -> list[dict]:
    """Load a bet-history CSV into [{rule_id, stake, profit, roi, date}]. roi = profit/stake."""
    out: list[dict] = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        first = next(reader, None)
        if not first:
            return out
        cr, cs, cp = _resolve(first, "ruleId"), _resolve(first, "stake"), _resolve(first, "profit")
        cd = _resolve(first, "date")
        if not (cr and cs and cp):
            print(f"WARN: bets CSV missing rule/stake/profit columns ({list(first.keys())})", file=sys.stderr)
            return out
        for row in [first] + list(reader):
            try:
                stake = float(row.get(cs) or 0)
                profit = float(row.get(cp) or 0)
                rid = (row.get(cr) or "").strip()
            except (TypeError, ValueError):
                continue
            if rid and stake > 0:
                out.append({"rule_id": rid, "stake": stake, "profit": profit,
                            "roi": profit / stake, "date": parse_date(row.get(cd, "")) if cd else None})
    return out


def _filters_block(data: dict) -> dict:
    return data.get("filters", data) if data.get("type") else data


def load_rule_markets(config_dir: str) -> dict[str, dict]:
    """Map {rule_id: {league, market, deployed: True}} from devig_*.json configs."""
    out: dict[str, dict] = {}
    for path in glob.glob(str(Path(config_dir) / "devig_*.json")):
        try:
            data = json.load(open(path))
        except (OSError, json.JSONDecodeError):
            continue
        for rule in _filters_block(data).get("rules", []) or []:
            rid = rule.get("id")
            if not rid:
                continue
            mi = (rule.get("market_inclusion") or [{}])[0]
            out[rid] = {
                "league": mi.get("league") or (rule.get("leagues") or [None])[0],
                "market": mi.get("market"),
                "deployed": True,
            }
    return out


def deployed_ids(config_dir: str) -> set[str]:
    return set(load_rule_markets(config_dir).keys())
