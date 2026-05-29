# PropSniper Operations Toolkit

A stdlib-only Python toolkit for auditing, validating, and managing PropSniper devig configs.
Designed to drop into a Claude Code project and run via slash commands.

Built from the methodology in:
- `PropSniper Operations Handoff Guide` (v1.0)
- `PropSniper Config Builder's Guide`
- `OddsPapi Complete Operations Guide`

## What this gives you

| Tool | Purpose | Run via |
|------|---------|---------|
| `audit/audit_configs.py` | 34-check schema audit. Catches self-references, duplicate IDs, schema drift, J/MO collisions. | `/audit` |
| `configs/overview.py` | Flatten N config files into one table + summary stats + book-frequency. | `/overview` |
| `configs/randomize_stack.py` | Jitter-audit identical comp stacks across accounts; rotate ranks 2/3 for differentiation. | `/jitter` / `/rotate` |
| `validation/devig_methods.py` | Library + CLI for Multiplicative / Additive / Power / Shin / worst / worstcase devig. | `/devig` |
| `validation/walk_forward.py` | Temporal-split Brier stability check against OddsPapi exports. | `/walk-forward` |
| `pnl/feedback_loop.py` | Joins PropSniper bet-history exports to deployed rule IDs, flags negative-ROI rules. | `/pnl-check` |
| `optimizer/optimize.py` | Windowed Bayesian-bandit + fractional-Kelly optimizer. Per-rule PROMOTE/HOLD/EXPLORE/SHRINK/DISABLE across 7d/14d/30d/90d/all. | `/optimize` |

All scripts are stdlib-only (Python 3.10+). No pip install required.

## The machine + live dashboard

Beyond the standalone tools, the toolkit ships a closed loop that turns bet data
into a live, monitored dashboard. See `OPTIMIZER.md` (algorithm) and
`ORCHESTRATION.md` (cross-platform design).

| Piece | Purpose |
|-------|---------|
| `run_machine.py` | One pulse: runs audit + windowed optimizer + community join → `docs/feed.json` (schema v2). `--publish` commits+pushes so the site goes live. |
| `docs/index.html` | Live-polling dashboard: window selector (days/weeks/months), sortable optimizer + community-vs-personal tables, trend sparklines, audit findings. GitHub-Pages ready. |
| `serve_local.py` | Private, **password-gated**, localhost-only dashboard over your REAL data. Nothing published or committed. See `REMOTE_ACCESS.md`. |
| `community/` | Community/consensus join (personal edge vs community per market → beating/inline/below). |
| `pslib.py` | Shared loaders (configs, bets, time windows) for the feed pipeline. |

```bash
# Demo end to end on synthetic data (nothing sensitive):
python configs/make_sample_configs.py
python optimizer/make_sample_bets.py
python community/make_sample_community.py
python run_machine.py                 # -> docs/feed.json
python serve_local.py                 # private dashboard at http://127.0.0.1:8799/

# Real data (stays on your machine, never committed):
set PROPSNIPER_DASH_PW=your-password
python serve_local.py --configs live/ --bets data/data-panel-export-LATEST.csv
```

Sensitive inputs (`live/`, `data/`) and generated artifacts (`configs/sample_live/`,
`optimizer/sample_bets.csv`, `community/community_latest.json`, `private/`) are
gitignored. The committed `docs/feed.json` carries only sample data.

## Install

Clone the repo, then either:

**Option A — drop into an existing Claude Code project:**

```bash
git clone https://github.com/YOUR_USER/propsniper-toolkit.git
cd YOUR_PROJECT
cp -r propsniper-toolkit/.claude/commands/* .claude/commands/
cp -r propsniper-toolkit/{audit,configs,validation,pnl} ./
```

**Option B — use as a standalone Claude Code project:**

```bash
git clone https://github.com/YOUR_USER/propsniper-toolkit.git
cd propsniper-toolkit
# Drop your devig_*.json configs into ./configs/live/
# Drop your OddsPapi exports into ./data/
claude
```

## Daily workflow

```
$ /audit              # before deploying any config change
$ /jitter             # check stacks aren't identical across accounts
$ /overview           # see what you've actually got deployed
$ /pnl-check          # nightly — which rules are bleeding?
$ /walk-forward mlb   # weekly or monthly — are your rankings stable?
$ /devig -110 -110    # ad-hoc — quick devig comparison
```

## Exit codes

Scripts use stable exit codes so they can wrap into CI:

| Code | Meaning |
|------|---------|
| 0    | Clean — safe to deploy |
| 1    | Warnings only — review but not blocking |
| 2    | Schema errors — block deploy |
| 3    | Blocking violations (self-reference, duplicate ID) — must fix |

## Methodology references

Each tool's docstring cites the section of Jacob's PDFs it implements. The audit
checks map 1:1 to Operations Handoff §9's 34-check list.

## Limitations

- `walk_forward.py` and `feedback_loop.py` require OddsPapi + PropSniper exports that
  this repo does not ship. You bring your own data.
- Devig method comparison is computational — does not yet wire into walk-forward.
  Combining the two (e.g., "does Power devig beat Multiplicative on Jacob's MLB
  Run Line data?") is a future tool.
- The 34 audit checks are conservative. Add project-specific checks in
  `audit/audit_configs.py` by following the pattern of existing check functions.
