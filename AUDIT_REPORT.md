# PropSniper Toolkit — Code Audit Report

**Scope:** all 6 Python tools + 6 slash commands + docs in `propsniper-toolkit`.
**Method:** full line-by-line read. Findings are concrete (file + behavior + fix), graded by severity.
**Date:** 2026-05-29

Severity key: **BLOCKER** (wrong results / data loss) · **HIGH** (correctness gap that bites with real data) · **MEDIUM** (inconsistency / fragility) · **LOW** (polish).

---

## Summary

The toolkit is genuinely well-built: stdlib-only, typed dataclasses, stable CI exit codes, rational-math consensus checks, honest docstrings that cite methodology. The audit engine is the strongest piece. The issues below are mostly **edge-case correctness** and **two different venue-resolution schemes that disagree** — plus the one structural gap that blocks the dashboard: **no machine-readable output**.

| # | Severity | File | Issue |
|---|----------|------|-------|
| 1 | BLOCKER | `configs/randomize_stack.py` | `/rotate` silently discards per-book tuning (minEV/maxVig/weight/minLiquidity reset to defaults) |
| 2 | HIGH | `audit/audit_configs.py` | Kalshi RD self-reference false-negative (venue "Kalshi RD" ≠ comp "Kalshi") |
| 3 | HIGH | `audit/audit_configs.py` | No `minEV ≤ maxEV` cross-field check; no min/maxOdds ordering check |
| 4 | HIGH | `configs/overview.py` | `--cells-only` venue token includes `.json` for non-`_v2` filenames |
| 5 | HIGH | `validation/walk_forward.py` | Implementation measures **rank stability**, but cited methodology (§5) defines STABLE as **positive Brier delta in both halves** — different test |
| 6 | MEDIUM | `audit` vs `randomize_stack` | Two venue resolvers with different coverage; audit's J/MO check misses Fliff/Kalshi/Rebet/BetMGM |
| 7 | MEDIUM | `configs/randomize_stack.py` | `k in rid` substring matching can rotate the wrong rule |
| 8 | MEDIUM | `validation/walk_forward.py` | Books posting >2 price updates per market silently dropped (pair requires exactly 2) |
| 9 | MEDIUM | `validation/walk_forward.py` | ISO time comparison via string `>`; breaks across mixed TZ/format |
| 10 | LOW | (whole repo) | No `--format json`, no tests, no CI workflow despite CI-ready exit codes |

---

## BLOCKER

### 1. `/rotate` resets per-book tuning to defaults — `randomize_stack.py:178-192`
`rotate_stacks()` rebuilds every comparison from scratch:
```python
new_comps.append({"book": book, "minEV": 0, "maxVig": 0.1,
                  "required": i == 0, "weight": 1, "minLiquidity": 0})
```
Any rule that was carefully tuned (e.g. `maxVig: 0.04` on a sharp book, a non-zero `minLiquidity`, custom `weight`) loses that tuning the moment you rotate ranks 2/3. The README sells rotation as "preserves rank-1" — but it preserves only the *book*, not its thresholds. It also forces `requiredComparisons: 2` regardless of stack size.
**Fix:** carry forward the existing comparison object for a book when present; only swap `book` identity and re-flag `required`. Fall back to defaults only for genuinely new books, and `log` to stderr when defaults are applied.

---

## HIGH

### 2. Kalshi RD self-reference false-negative — `audit_configs.py:40-48, 351-365`
`VENUE_PREFIX_MAP` maps `kal_rd_ → "Kalshi RD"`, but `"Kalshi RD"` is **not** in `VALID_BOOKS`, and `book_matches("Kalshi", "Kalshi RD")` normalizes to `"kalshi"` vs `"kalshird"` → **no match**. So a rule with ID `kal_rd_...` that lists `Kalshi` as a comp book — the exact self-reference the check exists to catch — passes clean.
**Fix:** treat `Kalshi RD` as an alias of `Kalshi` for self-reference (map the RD venue to the base book for the comparison), or add `Kalshi RD` to the book vocabulary and alias table.

### 3. Missing cross-field range checks — `audit_configs.py:272-288`
`minEV` is range-checked `[0,100]` and `maxEV` warned if `≠100`, but there is **no check that `minEV ≤ maxEV`**, and `minOdds`/`maxOdds` (required keys) get **no value or ordering validation at all**. A config with `minEV: 60, maxEV: 50` or `minOdds: +200, maxOdds: -200` deploys clean.
**Fix:** add `filter_ev_ordering` (ERROR if `minEV > maxEV`) and `filter_odds_ordering` (ERROR if `minOdds > maxOdds` in implied-prob space).

### 4. `--cells-only` venue token keeps `.json` — `overview.py:131-137`
```python
parts = fname.replace("devig_", "").replace("_v2.json", "").split("_")
```
For files **without** `_v2` (e.g. `devig_MO_caesars.json`, the form used throughout `CLAUDE.md`), `_v2.json` doesn't match, so `.json` is never stripped. Result: `venue = "caesars.json"`. Cells then fail to group with their `_v2` siblings.
**Fix:** strip the suffix robustly: `Path(fname).stem` then `.removeprefix("devig_").removesuffix("_v2")`.

### 5. walk-forward STABLE definition diverges from cited methodology — `walk_forward.py:26, 250-261`
The docstring cites Operations Handoff §5: *"STABLE (positive delta in both H1 and H2)"* — i.e. the book's edge has the **right sign in each half**. The code instead flags STABLE when the book's **Brier rank moves ≤2 positions** between halves. Rank-stability and sign-of-delta are different tests; a book can be rank-stable while having a negative half. The tool claims §5 fidelity but implements a proxy.
**Fix:** either (a) implement the §5 test (compare book Brier to a baseline/market-consensus Brier per half, require positive edge both halves), or (b) update the docstring + `/walk-forward.md` to state plainly that this is a rank-stability heuristic, not the §5 delta test.

---

## MEDIUM

### 6. Two venue resolvers that disagree
- `audit_configs.py` resolves venue from **rule-ID prefix** (`cae_`, `dk_`, `kal_rd_`, …).
- `randomize_stack.py:75-81` and the audit's own `check_j_mo_differentiation:405` resolve venue from **filename token** (`caesars`, `draftkings`, `fanduel`).

The filename resolvers cover different sets: the audit's J/MO check only knows `caesars/draftkings/fanduel` (misses Fliff/Kalshi/Rebet/BetMGM); `randomize_stack` adds `fliff/kalshi/rebet` but misses `betmgm/hardrock`. So identical-stack collisions at those venues slip past whichever check.
**Fix:** single source of truth — one `venue.py` with the prefix map *and* the filename token map kept in sync, used by all three call sites.

### 7. Substring rule matching in rotate — `randomize_stack.py:162-167`
`if rid and (rid.startswith(k) or k in rid)` — `k in rid` means a pool key like `mlb_total` matches **any** rule whose ID contains that substring, potentially rotating rules you didn't intend.
**Fix:** match on `startswith` only, or require an exact normalized cell key.

### 8. Multi-update books dropped in pairing — `walk_forward.py:147-149`
`pair_and_devig` skips any `(fixture, market, book, player)` group that isn't **exactly 2** rows. A book that posts several in-game price updates produces >2 rows for that key and is silently discarded — undercounting exactly the most actively-priced (sharpest) books.
**Fix:** when >2, select the canonical pair (e.g. the two latest `closeTime` rows that form the 2-sided market) rather than dropping.

### 9. ISO time string comparison — `walk_forward.py:126, 173`
`o.close_time > start` and the H1/H2 sort rely on lexicographic ISO ordering. Correct only if every timestamp is the same fixed-width format and timezone. Mixed `Z` vs `+00:00`, or naive vs offset, silently mis-orders.
**Fix:** parse to `datetime` once at load; compare/sort on the parsed value.

---

## LOW / structural

### 10. No JSON output, no tests, no CI
- README advertises CI-friendly exit codes, but there's no workflow and **no `--format json`** on any tool — so nothing downstream (a dashboard, an alert bot) can consume results without scraping stdout. This is the single biggest blocker to the live dashboard (addressed in `dashboard/` — see below).
- No `tests/`. The audit engine in particular is pure functions over dicts and is highly testable; a dozen fixtures would lock in the 34 checks.
- `random` is imported in `randomize_stack.py` and seeded but never used for selection (rotation is deterministic by offset) — dead import.
- `feedback_loop.compute_rule_pnl` anchors "30d" to the **last bet date**, not today — correct for backtests, surprising for live "is anything bleeding right now?" Document it.

---

## What's good (keep)
- `AuditReport.exit_code()` precedence (self-ref/dup → 3, errors → 2, warns → 1) is clean and correct.
- Rational consensus math `3*rc >= 2*n` avoids float drift — nice touch.
- `devig_methods.additive` negative-mass fallback to multiplicative is the right call.
- Honest `Limitations` sections; docstrings cite the source PDF section. Maintain this.
