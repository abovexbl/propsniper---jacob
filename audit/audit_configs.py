#!/usr/bin/env python3
"""
audit_configs.py — PropSniper config schema audit.

Implements the 34-check audit from Operations Handoff Guide §9 plus the
two-filter coverage check from §5. Run before every deployment.

Usage:
    python audit_configs.py path/to/configs/*.json
    python audit_configs.py --dir path/to/configs/

Exit codes:
    0 — clean
    1 — non-fatal warnings only
    2 — schema errors (block deploy)
    3 — self-reference or duplicate-ID violations (block deploy)

Audit checks implemented:
    Top-level (1-5):    type, version, filters keys, v=3, devig in allowed set
    Rule schema (10-22): key set, ID uniqueness, leagues, bet_timing, time_types,
                         market_inclusion, filter keys, max EV cap, market sub-blocks
    Comparison (25-31): requiredComparisons, comparison field set, valid books,
                         self-reference forbidden
    Cross-file (32-34): global ID uniqueness, coverage spec, J/MO differentiation hint
"""
from __future__ import annotations
import json
import sys
import argparse
import glob
from pathlib import Path
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Reference data (from Builders Guide §7-9)
# --------------------------------------------------------------------------

VENUE_PREFIX_MAP = {
    "cae_":   "Caesars",
    "dk_":    "DraftKings",
    "fd_":    "FanDuel",
    "flf_":   "Fliff",
    "kal_":   "Kalshi",
    "kal_rd_": "Kalshi RD",
    "reb_":   "Rebet",
}

VALID_LEAGUES = {"MLB", "NBA", "WNBA", "NHL", "NFL", "Tennis", "EuroLeague", "UFC", "MMA"}

# Books that can appear as venue or comp. Case-sensitive (Builders Guide §9).
VALID_BOOKS_AS_VENUE = {
    "FanDuel", "DraftKings", "BetMGM", "Caesars", "Hardrock", "BetRivers",
    "Fanatics", "FanaticsMarkets", "Bovada", "BetOnline", "BetParx", "BallyBet",
    "theScore", "Fliff", "Rebet", "Kalshi",  # Kalshi can be venue per Builders Guide §3.3
}
VALID_BOOKS_COMP_ONLY = {
    "Pinnacle", "BookMaker", "Circa", "Bet105",  # sharp / specialty
    "Polymarket", "PolymarketUS", "Prophet", "4cx", "NoVigApp",  # exchanges/aggregators
}
VALID_BOOKS = VALID_BOOKS_AS_VENUE | VALID_BOOKS_COMP_ONLY

VALID_DEVIG_MODES = {"worstcase", "worst"}

VALID_TIME_TYPES = {"full", "period"}
VALID_BET_TIMING = {"live"}  # only "live" supported per Builders Guide §4.1

REQUIRED_RULE_KEYS = {"id", "leagues", "bet_timing", "time_types", "market_inclusion", "filters"}
REQUIRED_FILTER_KEYS = {"minEV", "maxEV", "minOdds", "maxOdds", "minHoursAway", "maxHoursAway", "leagues", "markets"}
REQUIRED_MARKET_SUB_BLOCKS = {"main", "team", "player"}
REQUIRED_COMPARISON_FIELDS = {"book", "minEV", "maxVig", "required", "weight", "minLiquidity"}
FORBIDDEN_COMPARISON_FIELDS = {"devig", "maxDifference"}

# --------------------------------------------------------------------------
# Finding model
# --------------------------------------------------------------------------

SEVERITY_ERROR = "ERROR"     # blocks deploy
SEVERITY_WARN = "WARN"       # non-fatal, deploy can proceed
SEVERITY_INFO = "INFO"       # informational

@dataclass
class Finding:
    severity: str
    check: str
    file: str
    rule_id: str | None
    message: str

    def fmt(self) -> str:
        loc = f"{Path(self.file).name}"
        if self.rule_id:
            loc += f"::{self.rule_id}"
        return f"  [{self.severity:5}] {self.check:30} {loc}: {self.message}"


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    files_checked: int = 0
    rules_checked: int = 0

    def add(self, severity: str, check: str, file: str, rule_id: str | None, message: str):
        self.findings.append(Finding(severity, check, file, rule_id, message))

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_ERROR]

    def warns(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_WARN]

    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_INFO]

    def exit_code(self) -> int:
        self_ref_or_dup = any(
            f.severity == SEVERITY_ERROR and f.check in {"self_reference", "duplicate_id"}
            for f in self.findings
        )
        if self_ref_or_dup:
            return 3
        if self.errors():
            return 2
        if self.warns():
            return 1
        return 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def venue_for_rule_id(rule_id: str) -> str | None:
    """Resolve venue prefix from rule ID. Returns canonical venue book name."""
    # Try longest prefix first (e.g., kal_rd_ before kal_)
    for prefix in sorted(VENUE_PREFIX_MAP.keys(), key=len, reverse=True):
        if rule_id.startswith(prefix):
            return VENUE_PREFIX_MAP[prefix]
    return None


def book_matches(a: str, b: str) -> bool:
    """Case-insensitive book name match, robust to spacing."""
    norm = lambda s: s.lower().replace(" ", "").replace("_", "")
    return norm(a) == norm(b)


def all_comparisons(rule: dict) -> list[tuple[str, dict]]:
    """Yield (market_sub_block, comparison_dict) tuples for every comp in a rule."""
    out = []
    for sub_name, sub_block in (rule.get("filters", {}).get("markets") or {}).items():
        for comp in sub_block.get("comparisons", []) or []:
            out.append((sub_name, comp))
    return out


# --------------------------------------------------------------------------
# Per-file checks
# --------------------------------------------------------------------------

def detect_schema(data: dict) -> str:
    """Detect which config schema variant we have.

    Returns one of:
      'wrapped' — Builders Guide §3.1 form with type/version/filters wrapper
      'flat'    — Jacob's deployed v2 form: rules/v/devig at top level
      'unknown' — neither — treat as schema error
    """
    if data.get("type") == "propsniper/full-auto-filters" and "filters" in data:
        return "wrapped"
    if isinstance(data.get("rules"), list) and data.get("v") == 3:
        return "flat"
    return "unknown"


def get_filters_block(data: dict, schema: str) -> dict:
    """Return the dict that contains v/devig/rules/side_filters regardless of schema."""
    return data["filters"] if schema == "wrapped" else data


def check_top_level(data: dict, file: str, report: AuditReport) -> str:
    """Checks 1-5 — top-level structure. Returns detected schema variant."""
    schema = detect_schema(data)
    if schema == "unknown":
        report.add(SEVERITY_ERROR, "schema_unknown", file, None,
                   "neither wrapped (type/version/filters) nor flat (v/devig/rules) — cannot parse")
        return schema

    if schema == "flat":
        # Flat is the deployed reality; note it as info, not error.
        report.add(SEVERITY_INFO, "schema_flat", file, None,
                   "using flat schema (no type/version/filters wrapper). Builders Guide §3.1 specifies wrapped form.")
    else:
        if data.get("version") != 1:
            report.add(SEVERITY_ERROR, "top_version", file, None,
                       f"version must be 1, got {data.get('version')!r}")

    f = get_filters_block(data, schema)
    if f.get("v") != 3:
        report.add(SEVERITY_ERROR, "filters_v", file, None,
                   f"v must be 3 (schema version), got {f.get('v')!r}")
    if f.get("devig") not in VALID_DEVIG_MODES:
        report.add(SEVERITY_ERROR, "filters_devig", file, None,
                   f"devig must be one of {VALID_DEVIG_MODES}, got {f.get('devig')!r}")
    if "rules" not in f or not isinstance(f["rules"], list):
        report.add(SEVERITY_ERROR, "filters_rules", file, None, "rules must be an array")
    if "side_filters" in f and not isinstance(f["side_filters"], dict):
        report.add(SEVERITY_ERROR, "filters_side_filters", file, None, "side_filters must be an object")
    return schema


def check_rule_schema(rule: dict, file: str, report: AuditReport):
    """Checks 10-22 — rule-level schema."""
    rid = rule.get("id", "<no-id>")

    # Check 10: required keys present
    rule_keys = set(rule.keys())
    missing = REQUIRED_RULE_KEYS - rule_keys
    extra = rule_keys - REQUIRED_RULE_KEYS
    if missing:
        report.add(SEVERITY_ERROR, "rule_keys_missing", file, rid, f"missing keys: {sorted(missing)}")
    if extra:
        report.add(SEVERITY_WARN, "rule_keys_extra", file, rid, f"unexpected keys: {sorted(extra)}")

    # Check 13: leagues is single-element array with valid league
    leagues = rule.get("leagues", [])
    if not isinstance(leagues, list) or len(leagues) != 1:
        report.add(SEVERITY_ERROR, "rule_leagues_shape", file, rid,
                   f"leagues must be array with exactly 1 element, got {leagues!r}")
    elif leagues[0] not in VALID_LEAGUES:
        report.add(SEVERITY_ERROR, "rule_leagues_value", file, rid,
                   f"unknown league {leagues[0]!r}, expected one of {sorted(VALID_LEAGUES)}")

    # Check 14: bet_timing exactly ["live"]
    if rule.get("bet_timing") != ["live"]:
        report.add(SEVERITY_ERROR, "rule_bet_timing", file, rid,
                   f"bet_timing must be ['live'], got {rule.get('bet_timing')!r}")

    # Check 15: time_types exactly ["full", "period"]
    tt = rule.get("time_types", [])
    if set(tt) != VALID_TIME_TYPES or len(tt) != 2:
        report.add(SEVERITY_ERROR, "rule_time_types", file, rid,
                   f"time_types must be ['full', 'period'], got {tt!r}")

    # Check 17: market_inclusion present + well-formed
    mi = rule.get("market_inclusion", [])
    if not isinstance(mi, list) or len(mi) != 1 or not isinstance(mi[0], dict):
        report.add(SEVERITY_ERROR, "rule_market_inclusion", file, rid,
                   f"market_inclusion must be array with 1 object, got {mi!r}")
    elif "league" not in mi[0] or "market" not in mi[0]:
        report.add(SEVERITY_ERROR, "rule_market_inclusion_fields", file, rid,
                   f"market_inclusion[0] must have 'league' and 'market', got {mi[0]!r}")

    # Filter-level checks
    f = rule.get("filters", {})
    if not isinstance(f, dict):
        report.add(SEVERITY_ERROR, "rule_filters_type", file, rid, "filters must be an object")
        return

    # Check 18: filter required keys
    fkeys = set(f.keys())
    fmissing = REQUIRED_FILTER_KEYS - fkeys
    if fmissing:
        report.add(SEVERITY_ERROR, "filter_keys_missing", file, rid, f"missing filter keys: {sorted(fmissing)}")

    # Check 19: filter.leagues matches rule.leagues
    if f.get("leagues") != rule.get("leagues"):
        report.add(SEVERITY_ERROR, "filter_leagues_match", file, rid,
                   f"filter.leagues {f.get('leagues')!r} != rule.leagues {rule.get('leagues')!r}")

    # Check 20: maxEV equals 100
    if f.get("maxEV") != 100:
        report.add(SEVERITY_WARN, "filter_maxev_cap", file, rid,
                   f"maxEV should be 100 per spec, got {f.get('maxEV')!r}")

    # minEV in [0,100]
    minev = f.get("minEV")
    if not (isinstance(minev, (int, float)) and 0 <= minev <= 100):
        report.add(SEVERITY_ERROR, "filter_minev_range", file, rid, f"minEV out of [0,100]: {minev!r}")

    # minHoursAway / maxHoursAway sanity
    if f.get("minHoursAway") != 0:
        report.add(SEVERITY_WARN, "filter_minhoursaway", file, rid,
                   f"minHoursAway should be 0 per spec, got {f.get('minHoursAway')!r}")
    if f.get("maxHoursAway", 0) < 24:
        report.add(SEVERITY_INFO, "filter_maxhoursaway_tight", file, rid,
                   f"maxHoursAway = {f.get('maxHoursAway')!r} hours (tight window — may be intentional)")

    # Check 23: markets sub-blocks are main/team/player
    markets = f.get("markets", {})
    if set(markets.keys()) != REQUIRED_MARKET_SUB_BLOCKS:
        report.add(SEVERITY_ERROR, "markets_sub_blocks", file, rid,
                   f"markets must have keys {REQUIRED_MARKET_SUB_BLOCKS}, got {set(markets.keys())}")
        return

    # Check 23 cont.: main/team/player must be identical (Builders Guide §2 CRITICAL)
    main = markets.get("main")
    if markets.get("team") != main or markets.get("player") != main:
        report.add(SEVERITY_ERROR, "markets_identical_subblocks", file, rid,
                   "markets.main / markets.team / markets.player must be identical objects")

    # Check 25: requiredComparisons present + valid
    for sub_name, sub_block in markets.items():
        rc = sub_block.get("requiredComparisons")
        if rc is None:
            report.add(SEVERITY_ERROR, "missing_required_comparisons", file, rid,
                       f"markets.{sub_name} missing requiredComparisons")
            continue
        n_comps = len(sub_block.get("comparisons", []))
        if rc == 1:
            report.add(SEVERITY_ERROR, "rc_equals_one", file, rid,
                       f"markets.{sub_name}.requiredComparisons=1 is FORBIDDEN (Builders Guide §14)")
        if rc > n_comps:
            report.add(SEVERITY_ERROR, "rc_exceeds_stack", file, rid,
                       f"markets.{sub_name}.requiredComparisons={rc} > {n_comps} books in stack")
        # Builders Guide §14: rc must be at least 67% of stack. Use rational math:
        # rc/n >= 2/3  ⟺  3*rc >= 2*n. Anything below that is a real consensus failure.
        if n_comps >= 3 and 3 * rc < 2 * n_comps:
            report.add(SEVERITY_WARN, "rc_below_consensus_threshold", file, rid,
                       f"markets.{sub_name}: requiredComparisons={rc}/{n_comps} ({rc/n_comps:.0%}) below 67% consensus threshold")

    # Check 28: comparison field set
    for sub_name, comp in all_comparisons(rule):
        comp_keys = set(comp.keys())
        c_missing = REQUIRED_COMPARISON_FIELDS - comp_keys
        c_forbidden = comp_keys & FORBIDDEN_COMPARISON_FIELDS
        c_extra = comp_keys - REQUIRED_COMPARISON_FIELDS - FORBIDDEN_COMPARISON_FIELDS
        if c_missing:
            report.add(SEVERITY_ERROR, "comp_fields_missing", file, rid,
                       f"{sub_name}/{comp.get('book')}: missing {sorted(c_missing)}")
        if c_forbidden:
            report.add(SEVERITY_ERROR, "comp_fields_forbidden", file, rid,
                       f"{sub_name}/{comp.get('book')}: forbidden fields {sorted(c_forbidden)}")
        if c_extra:
            report.add(SEVERITY_WARN, "comp_fields_extra", file, rid,
                       f"{sub_name}/{comp.get('book')}: unexpected fields {sorted(c_extra)}")

        # Sanity on individual fields
        if "maxVig" in comp and not (0 <= comp["maxVig"] <= 1):
            report.add(SEVERITY_ERROR, "comp_maxvig_range", file, rid,
                       f"{sub_name}/{comp.get('book')}: maxVig out of [0,1]: {comp['maxVig']!r}")

        # Check 29: book name is valid
        book = comp.get("book")
        if book and book not in VALID_BOOKS:
            report.add(SEVERITY_WARN, "comp_book_unknown", file, rid,
                       f"{sub_name}: unknown book {book!r} (may be ok if new, verify case-sensitive name)")


def check_self_reference(rule: dict, file: str, report: AuditReport):
    """Check 31 — venue cannot be its own comp book. Critical per Builders Guide §9.3."""
    rid = rule.get("id", "<no-id>")
    venue = venue_for_rule_id(rid)
    if not venue:
        report.add(SEVERITY_WARN, "venue_unresolved", file, rid,
                   f"could not resolve venue from rule ID prefix (known prefixes: {sorted(VENUE_PREFIX_MAP)})")
        return
    seen = set()
    for sub_name, comp in all_comparisons(rule):
        book = comp.get("book", "")
        if book_matches(book, venue) and book not in seen:
            report.add(SEVERITY_ERROR, "self_reference", file, rid,
                       f"venue {venue!r} appears as comp book in markets.{sub_name} (CIRCULAR)")
            seen.add(book)


# --------------------------------------------------------------------------
# Cross-file checks
# --------------------------------------------------------------------------

def check_global_id_uniqueness(rules_by_file: dict[str, list[dict]], report: AuditReport):
    """Check 11/32 — rule IDs must be globally unique across all files."""
    id_to_files = defaultdict(list)
    for file, rules in rules_by_file.items():
        for rule in rules:
            rid = rule.get("id")
            if rid:
                id_to_files[rid].append(file)
    for rid, files in id_to_files.items():
        if len(files) > 1:
            report.add(SEVERITY_ERROR, "duplicate_id", files[0], rid,
                       f"rule ID appears in {len(files)} files: {[Path(f).name for f in files]}")


def check_per_file_uniqueness(rules: list[dict], file: str, report: AuditReport):
    """Check 11 — IDs unique within a file."""
    counts = Counter(r.get("id") for r in rules if r.get("id"))
    for rid, n in counts.items():
        if n > 1:
            report.add(SEVERITY_ERROR, "duplicate_id_in_file", file, rid,
                       f"appears {n} times in same file")


def check_j_mo_differentiation(rules_by_file: dict[str, list[dict]], report: AuditReport):
    """Check 34 — for cells deployed at both J and MO at same venue, stacks should differ.

    Heuristic: group files by venue prefix (cae/dk/fd). Within each venue group,
    if two files have rules with similar venue+market signatures, their comp stacks
    should not be identical [0,1,2]/[0,1,2] (would be a differentiation failure).
    """
    venue_groups = defaultdict(list)
    for file in rules_by_file:
        name = Path(file).name.lower()
        for venue_token in ("caesars", "draftkings", "fanduel"):
            if venue_token in name:
                venue_groups[venue_token].append(file)
                break

    for venue, files in venue_groups.items():
        if len(files) < 2:
            continue
        # Build market -> per-file comp stack tuple
        cell_stacks = defaultdict(dict)
        for file in files:
            for rule in rules_by_file[file]:
                mi = rule.get("market_inclusion", [{}])[0]
                market_key = (mi.get("league"), mi.get("market"))
                comps = rule.get("filters", {}).get("markets", {}).get("main", {}).get("comparisons", [])
                stack = tuple(c.get("book") for c in comps)
                cell_stacks[market_key][file] = stack
        # Flag any market where all files have identical stacks AND multiple files exist
        for cell, file_stacks in cell_stacks.items():
            if len(file_stacks) >= 2 and len(set(file_stacks.values())) == 1:
                report.add(SEVERITY_WARN, "j_mo_no_differentiation", list(file_stacks)[0], None,
                           f"{venue} {cell}: identical comp stack across {len(file_stacks)} files (limiting risk)")


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def audit_file(path: str, report: AuditReport) -> list[dict]:
    """Audit one config file. Returns its rules for cross-file analysis."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        report.add(SEVERITY_ERROR, "json_parse", path, None, f"invalid JSON: {e}")
        return []
    except OSError as e:
        report.add(SEVERITY_ERROR, "file_read", path, None, f"cannot read file: {e}")
        return []

    report.files_checked += 1
    schema = check_top_level(data, path, report)
    if schema == "unknown":
        return []
    f = get_filters_block(data, schema)
    rules = f.get("rules", []) or []
    check_per_file_uniqueness(rules, path, report)
    for rule in rules:
        report.rules_checked += 1
        check_rule_schema(rule, path, report)
        check_self_reference(rule, path, report)
    return rules


def main():
    ap = argparse.ArgumentParser(description="PropSniper config audit (34-check schema validation)")
    ap.add_argument("paths", nargs="*", help="config JSON files or glob patterns")
    ap.add_argument("--dir", help="directory of devig_*.json files to audit")
    ap.add_argument("--quiet", action="store_true", help="only print summary")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args()

    files: list[str] = []
    if args.dir:
        files.extend(sorted(glob.glob(str(Path(args.dir) / "devig_*.json"))))
    for p in args.paths:
        files.extend(sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p])
    files = sorted(set(files))

    if not files:
        print("ERROR: no files to audit. Pass paths or --dir.", file=sys.stderr)
        sys.exit(2)

    report = AuditReport()
    rules_by_file: dict[str, list[dict]] = {}
    for f in files:
        rules_by_file[f] = audit_file(f, report)

    check_global_id_uniqueness(rules_by_file, report)
    check_j_mo_differentiation(rules_by_file, report)

    # Output
    print(f"\n{'='*72}")
    print(f"PropSniper Config Audit")
    print(f"{'='*72}")
    print(f"Files checked:  {report.files_checked}")
    print(f"Rules checked:  {report.rules_checked}")
    errors = report.errors()
    warns = report.warns()
    infos = report.infos()
    print(f"Errors:         {len(errors)}")
    print(f"Warnings:       {len(warns)}")
    print(f"Info:           {len(infos)}")
    print()

    if not args.quiet:
        by_check = defaultdict(list)
        for f in report.findings:
            by_check[f.check].append(f)
        for check in sorted(by_check):
            findings = by_check[check]
            sev = findings[0].severity
            print(f"\n{sev}: {check}  ({len(findings)} finding{'s' if len(findings)>1 else ''})")
            for finding in findings[:20]:
                print(finding.fmt())
            if len(findings) > 20:
                print(f"  ... +{len(findings)-20} more")

    print(f"\n{'='*72}")
    exit_code = report.exit_code()
    if args.strict and warns:
        exit_code = max(exit_code, 1)
    verdict = {0: "PASS", 1: "PASS (warnings)", 2: "FAIL (schema errors)", 3: "FAIL (blocking violations)"}[exit_code]
    print(f"Verdict: {verdict}")
    print(f"{'='*72}\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
