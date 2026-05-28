---
description: Per-rule P&L health check from PropSniper bet exports.
---

Run: `python pnl/feedback_loop.py --bets data/data-panel-export-LATEST.csv --configs live/`

This joins the user's PropSniper bet export to currently-deployed rule IDs and
flags any rule meeting ALL of:
- n_bets ≥ 30
- 14-day ROI < 0
- 30-day ROI < 0

These rules are bleeding through variance and methodology — investigate.

The output is a list of rule IDs to review, not auto-disable. Disabling is a
judgment call (variance vs broken methodology); the script raises the flag.

Common next steps:
- Re-run `/walk-forward <sport>` for the rule's market — has the comp stack drifted?
- Check `/audit` — has the rule been edited recently?
- Compare to community Brier in the PropSniper data panel — agrees or disagrees?
