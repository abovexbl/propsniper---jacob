---
description: Audit comp-stack collisions across accounts (limiting risk defense).
---

Run `python configs/randomize_stack.py --jitter --dir live/`.

This finds (venue, league, market) cells where two or more account configs
have identical comp stacks. Identical stacks = identical bet patterns =
faster account limiting from books.

If collisions found, propose a candidate pool per cell and run:
```
python configs/randomize_stack.py --rotate --pool POOL_FILE \
    --config live/devig_J_<venue>.json --account J \
    --rank2-offset 0 --rank3-offset 1 --out live/devig_J_<venue>_new.json

python configs/randomize_stack.py --rotate --pool POOL_FILE \
    --config live/devig_MO_<venue>.json --account MO \
    --rank2-offset 1 --rank3-offset 3 --out live/devig_MO_<venue>_new.json
```

This produces differentiated stacks: same rank-1 (best book) but different ranks 2 and 3.

Audit the rewritten files before deploying.
