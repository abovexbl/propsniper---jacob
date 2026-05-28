# Project Context — PropSniper Operations Toolkit

This is Jacob's operations toolkit for managing PropSniper devig configs.

## What's running here

Jacob runs a live in-game +EV sports betting operation across multiple family accounts
and venues. The full methodology lives in three PDFs (referenced in `docs/`):

- `PropSniper Operations Handoff Guide` (Brier methodology + audit checks)
- `PropSniper Config Builder's Guide` (JSON schema + EV floors + books)
- `OddsPapi Complete Operations Guide` (data pipeline)

## Directory layout

```
audit/        — schema audits (run before EVERY deploy)
validation/   — Brier + devig-method math
configs/      — config inspection / mutation tools
pnl/          — rule-level P&L feedback
.claude/      — Claude Code slash commands
docs/         — methodology references (your PDFs go here)
data/         — OddsPapi exports + PropSniper bet history (gitignored)
live/         — currently-deployed devig_*.json files (gitignored — sensitive)
```

## Workflow Claude should use

When asked to do anything involving config changes:

1. **Always audit first.** Run `audit_configs.py --dir live/` and read the verdict.
   Block the user from deploying if exit code != 0 unless they explicitly override.
2. **Before changing comp stacks:** check `validation/walk_forward.py` output — if
   a book is UNSTABLE across H1/H2, don't put it in the stack.
3. **After changing any rule:** re-run audit. Diff against the prior version.
4. **Never edit configs directly without re-running audit.** This is the #1 rule.

## Patterns to enforce

- Rule IDs must be globally unique across all 9 files
- Venue X cannot appear as a comp book in any rule deployed at X (self-reference forbidden)
- `requiredComparisons` must be >= 67% of stack size
- `markets.main`, `markets.team`, `markets.player` must be identical objects per rule
- 8 required `filters` keys: minEV, maxEV, minOdds, maxOdds, minHoursAway, maxHoursAway, leagues, markets
- Each comparison object must have exactly 6 fields: book, minEV, maxVig, required, weight, minLiquidity
- Forbidden comparison fields: `devig`, `maxDifference`

## Conventions

- Python is stdlib-only. No pip dependencies.
- Scripts print to stdout in human-readable form; structured output via `--format csv`.
- Exit codes follow the convention in README.md.
- All scripts accept `--help` and have a docstring at the top.

## Common requests Claude should be ready for

- "audit the configs" → `python audit/audit_configs.py --dir live/`
- "what cells are deployed at Caesars?" → `python configs/overview.py --dir live/ --cells-only | grep -i caesars`
- "rotate ranks 2 and 3 for the MO account" → `python configs/randomize_stack.py --rotate --pool POOL --config live/devig_MO_caesars.json --account MO --rank2-offset 1 --rank3-offset 3 --out new.json`
- "is Pinnacle stable on MLB Run Line?" → `python validation/walk_forward.py --summary data/oddspapi_mlb_summary_main_csv.gz --fixtures data/oddspapi_mlb_fixtures.csv | grep -A2 "Run Line"`
- "which rules are bleeding?" → `python pnl/feedback_loop.py --bets data/data-panel-export-LATEST.csv --configs live/`
- "compare devig methods for -300/+250" → `python validation/devig_methods.py -- -300 +250`

## What NOT to do

- Don't push to GitHub if `live/` contains real bet history or per-account credentials.
  `.gitignore` already excludes `live/`, `data/`, `*.csv`, `*.csv.gz`, and `*.db`.
- Don't disable audit checks. If a check is firing falsely, fix the check, don't bypass it.
- Don't deploy a config that hasn't passed audit. The audit catches real bugs every time.
