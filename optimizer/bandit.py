#!/usr/bin/env python3
"""
bandit.py — Bayesian edge estimation + Thompson sampling + fractional Kelly.

The math core of the continuous optimizer. Pure stdlib. See OPTIMIZER.md for the
why; this file is the how.

Model (per rule r):
  Each settled bet i contributes a unit return x_i = profit_i / stake_i
    (win -> +net_odds, loss -> -1). The rule's edge is the mean unit ROI mu_r.
  Likelihood:  x_i ~ Normal(mu_r, sigma^2)   (sigma estimated empirical-Bayes, global)
  Prior:       mu_r ~ Normal(prior_mean, prior_sd^2)   (weak, centered at 0 = no edge)
  Posterior (conjugate Normal-Normal): Normal(post_mean, post_sd^2)

Why this shape:
  - Shrinkage falls out of the prior: small-n rules get pulled toward 0 edge, so we
    don't chase noise. This is exactly the "shrink position size when uncertain"
    behavior fractional-Kelly practitioners approximate by hand.
  - P(edge < 0) is a clean, interpretable "how sure are we this rule is bleeding".
  - Thompson sampling from the posterior gives explore/exploit for free and tolerates
    delayed feedback (bets settle later) — the property that makes it fit betting.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict

_SQRT2 = math.sqrt(2.0)


def normal_cdf(x: float, mu: float = 0.0, sd: float = 1.0) -> float:
    """P(X <= x) for X ~ Normal(mu, sd^2)."""
    if sd <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sd * _SQRT2)))


@dataclass
class Posterior:
    rule_id: str
    n: int
    observed_roi: float   # xbar, raw mean unit ROI
    post_mean: float      # shrunk posterior mean edge
    post_sd: float        # posterior uncertainty on the mean
    p_bleeding: float     # P(edge < 0)
    sigma: float          # assumed per-bet ROI sd (global, empirical-Bayes)

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}


def empirical_sigma(all_returns: list[float], floor: float = 0.5) -> float:
    """Global per-bet ROI standard deviation (empirical-Bayes estimate of sigma).

    Using one pooled sigma across rules stabilizes per-rule posteriors when any
    single rule has few bets. Floored so a quiet early dataset can't collapse it.
    """
    n = len(all_returns)
    if n < 2:
        return max(floor, 1.0)
    m = sum(all_returns) / n
    var = sum((x - m) ** 2 for x in all_returns) / (n - 1)
    return max(math.sqrt(var), floor)


def update_posterior(
    rule_id: str,
    returns: list[float],
    sigma: float,
    prior_mean: float = 0.0,
    prior_sd: float = 0.5,
) -> Posterior:
    """Conjugate Normal-Normal update. sigma treated as known (plug-in)."""
    n = len(returns)
    xbar = sum(returns) / n if n else 0.0
    prior_prec = 1.0 / (prior_sd * prior_sd)
    lik_prec = n / (sigma * sigma) if n else 0.0
    post_prec = prior_prec + lik_prec
    post_var = 1.0 / post_prec
    post_mean = (prior_mean * prior_prec + (n * xbar / (sigma * sigma) if n else 0.0)) * post_var
    post_sd = math.sqrt(post_var)
    p_bleed = normal_cdf(0.0, post_mean, post_sd)
    return Posterior(rule_id, n, xbar, post_mean, post_sd, p_bleed, sigma)


def thompson_sample(post: Posterior, rng) -> float:
    """One draw of the rule's edge from its posterior. Drives explore/exploit ranking."""
    return rng.gauss(post.post_mean, post.post_sd)


def kelly_fraction(post: Posterior, kelly_mult: float = 0.25, max_fraction: float = 0.05) -> float:
    """Fractional-Kelly stake as a fraction of bankroll.

    For approximately-Gaussian returns the growth-optimal fraction is ~ mean/variance.
    We use the SHRUNK posterior mean (so uncertainty already pulls the stake down),
    then apply a fractional multiplier (quarter-Kelly default) and a hard cap.
    """
    if post.post_mean <= 0:
        return 0.0
    f_full = post.post_mean / (post.sigma * post.sigma)
    return max(0.0, min(kelly_mult * f_full, max_fraction))
