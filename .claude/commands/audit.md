---
description: Run the 34-check schema audit on all deployed configs. Block deploy on errors.
---

Run `python audit/audit_configs.py --dir live/` and interpret the output.

If the audit returns exit code 0 or 1, report the verdict and any warnings.
If the audit returns exit code 2 or 3, list every error grouped by check type.

When errors exist, do NOT propose any config changes until they are fixed.
The most common errors and their fixes:

- `self_reference`: a rule deployed at venue X has X as a comp book. Remove X from comparisons. Pick the next-best book by Brier (run `/walk-forward` for guidance).
- `duplicate_id`: same rule ID appears in two files. Suffix the MO version with `_m` per Builders Guide §7.3.
- `rc_equals_one`: requiredComparisons=1 is forbidden. Increase to at least 2, or shrink stack to 2 books with rc=2.
- `markets_identical_subblocks`: markets.main, markets.team, markets.player diverged. Re-copy main into team and player verbatim.

After fixing errors, re-run audit until exit code is 0 before suggesting any deploy.
