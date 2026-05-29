#!/usr/bin/env python3
"""
integrations/propsniper.py — the data plane between PropSniper and the machine.

INBOUND (PropSniper -> machine -> website):
  - bet history (data-panel export) -> normalized bets -> optimizer (the learning machine)
  - community/consensus panel        -> normalized markets -> community-vs-personal join

OUTBOUND (machine -> PropSniper):
  - optimizer proposals -> a flat action list (rule, action, stake) the operator applies

The real PropSniper feed is still being wired, so every loader degrades gracefully:
if a source file is missing it reports `not_connected` instead of failing. Point
`integrations/sources.json` at your real exports when they're ready and the whole
pipeline picks them up — no code change.
"""
from __future__ import annotations
import csv
import io
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import pslib  # noqa: E402

DEFAULT_SOURCES = REPO / "integrations" / "sources.json"

# Column aliases for a PropSniper data-panel community/consensus export (CSV).
_COMMUNITY_ALIASES = {
    "league":  ["league", "League", "sport"],
    "market":  ["market", "Market", "marketName", "bet_type"],
    "edge":    ["community_edge", "edge", "avgEV", "ev", "community_roi"],
    "brier":   ["community_brier", "brier", "brierScore"],
    "n":       ["community_n", "n", "sample", "count", "bets"],
    "hit":     ["hit_rate", "hitRate", "win_rate", "winRate"],
}


def load_sources(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("PROPSNIPER_SOURCES", DEFAULT_SOURCES))
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else REPO / p


def _pick(row: dict, names: list[str]):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def normalize_community(path: str) -> dict | None:
    """Read a community/consensus source (JSON in our shape, or a flexible CSV)
    and return {source, as_of, markets:[{league,market,community_edge,community_brier,community_n,hit_rate}]}."""
    fp = _resolve(path)
    if not fp.exists():
        return None
    text = fp.read_text()
    # JSON already in our shape?
    if fp.suffix == ".json":
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "markets" in data:
                return data
        except json.JSONDecodeError:
            return None
    # Otherwise parse as CSV with alias mapping.
    markets = []
    for row in csv.DictReader(io.StringIO(text)):
        lg, mk = _pick(row, _COMMUNITY_ALIASES["league"]), _pick(row, _COMMUNITY_ALIASES["market"])
        if not mk:
            continue
        def num(key, default=0.0):
            v = _pick(row, _COMMUNITY_ALIASES[key])
            try:
                return float(v)
            except (TypeError, ValueError):
                return default
        markets.append({"league": lg, "market": mk, "community_edge": num("edge"),
                        "community_brier": num("brier"), "community_n": int(num("n")),
                        "hit_rate": num("hit")})
    return {"source": fp.name, "as_of": None, "markets": markets} if markets else None


def export_action_list(proposals: list[dict], window: str, out_path: str) -> int:
    """OUTBOUND: write a flat CSV the operator can apply back in PropSniper.
    Columns: rule_id, label, action, recommended_stake_abs, edge, p_bleeding."""
    rows = []
    for p in proposals:
        e = p.get("windows", {}).get(window)
        if not e:
            continue
        rows.append({"rule_id": p["rule_id"], "label": p.get("label", ""),
                     "action": e["action"], "recommended_stake_abs": e["recommended_stake_abs"],
                     "edge": e["post_mean"], "p_bleeding": e["p_bleeding"]})
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["rule_id", "label", "action", "recommended_stake_abs", "edge", "p_bleeding"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _status_for(path_str: str | None, kind: str) -> dict:
    """Report connection status for one source without failing."""
    if not path_str:
        return {"connected": False, "state": "not_configured", "path": None}
    fp = _resolve(path_str)
    if not fp.exists():
        return {"connected": False, "state": "not_connected", "path": path_str}
    info = {"connected": True, "path": path_str}
    try:
        if kind == "bets":
            bets = pslib.load_bets(str(fp))
            dates = [b["date"] for b in bets if b["date"]]
            info.update({"rows": len(bets),
                         "latest": max(dates).strftime("%Y-%m-%d") if dates else None})
        elif kind == "community":
            c = normalize_community(str(fp))
            info.update({"rows": len(c["markets"]) if c else 0})
        # sample files are named distinctly; flag them so the UI can say "sample"
        info["state"] = "sample" if ("sample" in fp.name or fp.name == "community_latest.json") else "live"
    except Exception as e:  # never let status checking crash the machine
        info.update({"connected": False, "state": "error", "note": str(e)})
    return info


def sync_status(sources: dict) -> dict:
    """Summarize what's wired, for the feed's data_sources block + the website."""
    return {
        "propsniper_bets": _status_for((sources.get("propsniper_bets") or {}).get("path"), "bets"),
        "propsniper_community": _status_for((sources.get("propsniper_community") or {}).get("path"), "community"),
        "oddspapi": _status_for((sources.get("oddspapi") or {}).get("path"), "raw"),
    }


if __name__ == "__main__":
    srcs = load_sources()
    print(json.dumps(sync_status(srcs), indent=2))
