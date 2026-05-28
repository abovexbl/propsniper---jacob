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

All scripts are stdlib-only (Python 3.10+). No pip install required.

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
