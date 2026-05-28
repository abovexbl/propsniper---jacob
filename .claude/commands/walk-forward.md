---
description: Walk-forward Brier stability check against OddsPapi exports.
---

Usage: `/walk-forward <sport>` (e.g., `/walk-forward mlb`, `/walk-forward wnba`)

Run:
```
python validation/walk_forward.py \
    --summary data/oddspapi_<sport>_summary_main_csv.gz \
    --fixtures data/oddspapi_<sport>_fixtures.csv
```

Interpret the output:
- `STABLE` cells: book is safe to keep in comp stack. Rank moves ≤2 between halves.
- `UNSTABLE`: book's Brier rank moves significantly between halves. Consider removing from stack.
- `LOW_N`: insufficient sample. Need more fixtures before trusting the ranking.
- `MISSING`: book didn't have outcomes in one half. Coverage issue.

If a book is UNSTABLE on a market where it's currently `required:true`, raise an
immediate alert — that's a production reliability bug. Either drop required:true
or swap the book for the next-most-stable comp candidate.

For more granular analysis, also try:
- `--split 0.6` — train on first 60%, test on last 40%
- `--min-n-per-book 100` — stricter sample requirement
- `--rank-threshold 1` — stricter stability (only ±1 rank move is STABLE)
