# PropSniper Continuous Optimizer — Engine Study & Algorithm

**Goal:** an algorithm that *continuously improves the deployed configs from real bet
data* — closing the loop the existing tools left open (`/pnl-check` flags bleeders,
`/walk-forward` checks stability, but nothing decides what to *do* and sizes it).

This doc surveys the candidate optimization engines, justifies the choice, specifies
the algorithm, and gives the roadmap. The working v1 is in `optimizer/`.

---

## 1. The problem, stated precisely

We run N betting rules. Each settled bet on rule *r* yields a unit return
`x = profit/stake`. We want to **maximize long-run risk-adjusted bankroll growth**
by deciding, per rule, continuously:

1. **Keep / cut / kill** — is this rule's edge real and positive?
2. **How much to stake** — bet size as a fraction of bankroll.
3. **Which comp books** — swap unstable books for stable ones.

Subject to hard constraints: every config change must **pass `/audit`** (feasibility)
and **preserve J/MO differentiation** (account-limiting risk). So this is *constrained,
sequential decision-making under uncertainty with delayed, noisy feedback* — bets settle
later, and a 5% edge is buried in ~95% per-bet variance.

That framing rules some engines in and others out.

## 2. Engines considered

| Engine | Fit here | Verdict |
|--------|----------|---------|
| **Grid / walk-forward parameter sweep** | Already partly present (`walk_forward.py`). Good for *validation*, bad as the driver — re-optimizing on every cell overfits the tail. | **Guardrail, not driver.** |
| **Bayesian optimization (GP)** | Great for expensive black-box tuning of a few continuous knobs (EV floors). Overkill for per-rule keep/kill; assumes smoothness we don't have across discrete book choices. | Optional, later, for EV-floor tuning. |
| **Genetic / evolutionary** | Flexible over discrete stacks, but sample-hungry and opaque — hard to justify a bet to a human, and we get few samples per rule. | No. |
| **Reinforcement learning (policy/Q)** | Theoretically the general answer, but needs far more data than a betting op generates and is hard to constrain safely. | Premature. |
| **Multi-armed bandit — Thompson sampling** | Built for explore/exploit under uncertainty, **tolerates delayed feedback**, Bayesian (gives uncertainty for free), logarithmic regret, simple to implement and audit. Empirically beats UCB in recommendation/ad settings. | **Driver.** |
| **Fractional Kelly** | The standard growth-optimal bet-sizing rule; fractional + uncertainty-shrunk is what professionals actually use. | **Staking layer.** |

**Chosen stack:** Thompson sampling over *Bayesian edge posteriors*, with *fractional
Kelly* staking, *walk-forward* as the overfitting guard, and the *audit* as a hard
feasibility constraint. Each piece is well-established (refs in §7) and maps onto a
tool we already have.

## 3. The algorithm

### 3a. Edge estimation (Bayesian, shrunk) — `bandit.update_posterior`
Model each rule's unit returns `x_i ~ Normal(mu, sigma^2)`; `mu` is the edge.
Prior `mu ~ Normal(0, prior_sd^2)` (centered on *no edge*). Conjugate Normal-Normal
posterior is closed-form. `sigma` is estimated **empirical-Bayes** as one pooled
per-bet ROI sd across all rules, so a rule with 12 bets borrows stability from the rest.

The prior does the heavy lifting: thin-data rules get **shrunk toward zero edge**, so we
never size up on noise. This *is* the "shrink position when uncertain" behavior fractional-
Kelly users approximate by hand — here it's principled.

### 3b. Decision policy — `optimize.decide`
From the posterior we read `p_bleeding = P(mu < 0)`:

```
n < min_n            -> EXPLORE   (probe stake, keep learning)
p_bleeding >= 0.95   -> DISABLE
p_bleeding >= 0.75   -> SHRINK
p_bleeding <= 0.25 and mean>0 -> PROMOTE
else                 -> HOLD
```

### 3c. Staking (fractional Kelly) — `bandit.kelly_fraction`
For ~Gaussian returns the growth-optimal fraction is `~ mean/variance`. We apply it to
the **shrunk** mean, multiply by a fractional-Kelly factor (quarter by default), and cap
hard. Result: stake rises with confidence *and* edge, and collapses to 0 when the
posterior mean is non-positive.

### 3d. Exploration ordering (Thompson) — `bandit.thompson_sample`
Each cycle we draw `mu ~ posterior` per rule and rank by the draw. High-uncertainty rules
occasionally sample high and get exploration budget; confident winners dominate on the
mean. This is Thompson sampling — and because it only needs the posterior, **delayed bet
settlement doesn't break it.**

### 3e. The loop (one turn = one `optimize.py` run)
```
ingest settled bets ─► update posteriors ─► decide actions ─► size (frac-Kelly)
        ▲                                                          │
        │                                                          ▼
   schedule / new data                                   emit proposals.json
        ▲                                                          │
        │            ┌───────────────── HUMAN GATE ────────────────┘
        └── deploy ◄─┤ challenger config ─► /audit (must pass) ─► /walk-forward (stable?)
                     └ approve in chat
```
Steps left of the gate are automated. **The gate is mandatory** for any config mutation,
money movement, or book swap (per `CLAUDE.md` #1 rule and `ORCHESTRATION.md §6`). The
optimizer *proposes*; a human *deploys*.

## 4. Why these guardrails are not optional
- **Audit as constraint:** a proposal that fails `/audit` is infeasible, full stop. The
  optimizer's job is to find the *best feasible* config, not the best config.
- **Walk-forward before any book swap:** never promote a book into a stack unless it's
  STABLE across temporal halves — otherwise we'd chase a book that was hot in-sample.
- **Fractional, not full, Kelly:** full Kelly is correct only if you *know* your edge.
  We don't; we have a posterior. Quarter-Kelly on the shrunk mean is the honest size.

## 5. What v1 does / doesn't do
**Does:** ingest bet history, estimate shrunk per-rule edge, classify into 5 actions,
size with fractional Kelly, flag orphans, rank exploration by Thompson sample, emit a
JSON feed. Verified on synthetic data (every action fires correctly).

**Doesn't yet:** auto-generate the challenger config for a book swap (needs the
walk-forward ranking joined in — `randomize_stack.py` already does the rewrite, see
AUDIT_REPORT.md #1 first so it preserves tuning); model per-*book* edge within a stack
(currently per-rule); decay old bets (a half-life weighting so stale results matter less).

## 6. Roadmap
- [x] Bayesian edge + Thompson + fractional Kelly core (`optimizer/bandit.py`)
- [x] Decision loop + JSON proposal feed (`optimizer/optimize.py`, `/optimize`)
- [ ] Join `walk_forward.py --format json` so SWAP_BOOK proposals are auto-generated and pre-validated
- [ ] Time-decay weighting (exponential half-life) so the edge tracks regime change
- [ ] Per-book posteriors (Thompson over books *within* a stack, not just rules)
- [ ] Merge `proposals.json` into the dashboard feed as an "Actions" panel
- [ ] Champion/challenger shadow deploy: run a challenger config in observe-only mode, promote on accumulated posterior evidence

## 7. References (engine study)
- Agrawal & Goyal, *Analysis of Thompson Sampling for the Multi-armed Bandit Problem* — logarithmic regret, delayed-feedback tolerance. https://proceedings.mlr.press/v23/agrawal12.html
- *Batched Thompson Sampling for Multi-Armed Bandits* — practical batched updates (fits periodic bet settlement). https://arxiv.org/pdf/2108.06812
- *Thompson Sampling for Combinatorial Bandits / online feature selection* — choosing subsets (stacks of books). https://www.researchgate.net/publication/264081758
- Downey, *Why fractional Kelly? Simulations of bet size with uncertainty and downside risk* — fractional Kelly as the answer to not knowing your edge. https://matthewdowney.github.io/uncertainty-kelly-criterion-optimal-bet-size.html
- *Kelly Criterion: inputs, edge, and fractional Kelly* — quarter/half-Kelly practice. https://quantmatter.com/kelly-criterion-formula/

> Responsible-use note: this optimizes a legal, user-operated +EV betting toolkit. It is
> deliberately conservative — it shrinks uncertain edges, caps stakes, and never auto-
> deploys. Treat its output as a flag for human review, not an instruction.
