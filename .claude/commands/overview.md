---
description: Show a flat-table overview of all deployed rules across the 9 config files.
---

Run `python configs/overview.py --dir live/` for the full table + summary.

Common follow-ups the user may want:
- `--book-frequency` — how often each book appears, and as required:true
- `--cells-only` — one row per (user, venue, league, market) cell
- `--summary-only` — just EV floor + league distribution
- `--format csv > rules.csv` — export to CSV for spreadsheet analysis

After printing, surface anything interesting:
- Books that appear often but never as required (BetParx in Jacob's setup is the canonical example — high participation, low confidence)
- EV floor outliers (the kalshi_jacob `f18` rule on Tennis ML is intentional)
- League imbalances (heavy MLB tilt suggests seasonal positioning)
