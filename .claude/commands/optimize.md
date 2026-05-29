---
description: Continuous optimizer — score every rule's edge from bet history and propose actions.
---

Run: `python optimizer/optimize.py --bets data/data-panel-export-LATEST.csv --configs live/`

This estimates each deployed rule's true edge (Bayesian, shrunk toward zero when
data is thin), then proposes one action per rule:

- **PROMOTE** — confident positive edge. Size up toward the Kelly cap.
- **HOLD** — edge real but uncertain. Leave the stake where it is.
- **EXPLORE** — too few bets (< `--min-n`, default 30). Keep a small probe stake and gather data.
- **SHRINK** — likely bleeding (75–95% confident edge < 0). Cut exposure.
- **DISABLE** — strongly bleeding (≥95% confident edge < 0). Stop the rule.
- `[ORPHAN]` tag — bets exist for a rule ID not in any deployed config. Investigate (renamed? deleted?).

Stakes are **fractional Kelly** (default quarter) on the shrunk posterior, hard-capped
by `--max-fraction`. Output is written to `optimizer/proposals.json` for the dashboard.

These proposals are **advisory**. Before applying any config-mutating action:
1. Re-run `/walk-forward <sport>` for the rule's market — is the comp stack still stable?
2. Produce the challenger config and run `/audit` on it — it must pass (exit 0/1) before deploy.
3. Get explicit human sign-off. The optimizer proposes; it never deploys.

Useful flags:
- `--bankroll N` — set bankroll for absolute stake sizing
- `--kelly 0.5` — half-Kelly (more aggressive); `0.25` is the safer default
- `--min-n 50` — require more data before trusting an edge
- `--prior-sd 0.3` — stronger shrinkage (more skeptical of small samples)

Demo with synthetic data:
```
python optimizer/make_sample_bets.py
python optimizer/optimize.py --bets optimizer/sample_bets.csv --configs configs/sample_live
```
