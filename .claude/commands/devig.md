---
description: Compare devig methods on a single market (Multiplicative, Additive, Power, Shin, worst, worstcase).
---

Usage: `/devig <odds_A> <odds_B>` (e.g., `/devig -110 -110`, `/devig -300 +250`)

Run: `python validation/devig_methods.py -- <odds_A> <odds_B>`

Report the fair probabilities under each method side-by-side.

Things to highlight in the response:
- For odds near pick'em (-110/-110), all methods converge — choice doesn't matter
- For favorite/dog skews (-300/+250 or steeper), Multiplicative vs Power/Shin diverge by 1-2% probability — meaningful at EV floors of 5-10%
- "worst" (Jacob's Rebet mode) is the most conservative — use only when you need maximum safety margin
- "worstcase" (Jacob's default in 8/9 files) equals Multiplicative for 2-way markets

If the user asks "which should I use," recommend:
- **Multiplicative** for 2-way symmetric markets (most cases)
- **Shin** or **Power** for skewed markets (heavy favorite or dog)
- **Worst** only when you want explicit safety margin (e.g., new market with no historical Brier validation)
