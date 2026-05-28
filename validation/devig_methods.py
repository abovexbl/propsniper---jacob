#!/usr/bin/env python3
"""
devig_methods.py — implementations of the four standard devig methods.

PropSniper ships with multiplicative-only devig. Jacob's configs use "worst" /
"worstcase" which are the most conservative multiplicative variants. This module
provides the same primitive plus Additive, Power, and Shin (per Crazy Ninja Mike
and standard betting literature), so a user can A/B test which method best
predicts outcomes on their historical data.

All functions accept American odds and return fair_p for each side (summing to 1.0).

Methods:
    multiplicative(odds_list) — standard. Equal share of vig per side.
    additive(odds_list)       — subtract equal absolute prob from each. Equiv to Shin for 2-way.
    power(odds_list, k)       — raise to power. Better for favorite-longshot bias.
    shin(odds_list)           — iterative Shin (1993). Models insider-trading bias.
    worst(odds_list)          — Jacob's "worst" mode. Treats max(implied_p) as fair.
    worstcase(odds_list)      — Jacob's "worstcase" mode. Highest-vig multiplicative.

Reference: Hyun Song Shin (1993), CrazyNinjaMike Devigger, Bet Hero — Devigging Methods.
"""
from __future__ import annotations
import math


def american_to_implied(odds: float) -> float:
    """American odds → implied probability (with vig)."""
    if odds == 0:
        raise ValueError("American odds cannot be 0")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def implied_to_american(p: float) -> float:
    """Fair probability → American odds. p must be in (0,1)."""
    if not 0 < p < 1:
        raise ValueError(f"p must be in (0,1), got {p}")
    if p >= 0.5:
        return -100.0 * p / (1 - p)
    return 100.0 * (1 - p) / p - 100.0


def implied_to_decimal(p: float) -> float:
    """Fair probability → decimal odds."""
    return 1.0 / p


def multiplicative(odds: list[float]) -> list[float]:
    """Multiplicative devig: fair_p_i = implied_p_i / sum(implied_p).

    Standard method. Equal share of vig per side. Best for 2-way symmetric markets.
    Used by OddsJam, Unabated, PropSniper default.
    """
    ps = [american_to_implied(o) for o in odds]
    total = sum(ps)
    return [p / total for p in ps]


def additive(odds: list[float]) -> list[float]:
    """Additive devig: subtract equal absolute probability mass from each side.

    For 2-way markets this is equivalent to Shin. Different from Shin in 3+ way markets.
    """
    ps = [american_to_implied(o) for o in odds]
    total = sum(ps)
    excess = total - 1.0  # the vig in probability units
    per_side = excess / len(ps)
    fair = [p - per_side for p in ps]
    # If subtraction drove any side negative (extreme longshots), fall back to multiplicative
    if any(f <= 0 for f in fair):
        return multiplicative(odds)
    # Renormalize for floating point
    s = sum(fair)
    return [f / s for f in fair]


def power(odds: list[float], k: float | None = None) -> list[float]:
    """Power devig: raise implied probs to power k chosen so they sum to 1.

    Finds k via bisection. Better than multiplicative for favorite-longshot bias.
    Always stays in [0,1] so no negative-prob blowup.
    """
    ps = [american_to_implied(o) for o in odds]
    if k is not None:
        powered = [p ** k for p in ps]
        s = sum(powered)
        return [x / s for x in powered]

    # Find k via bisection. k in (0, 2] should cover normal cases.
    lo, hi = 0.01, 5.0
    for _ in range(100):
        mid = (lo + hi) / 2
        s = sum(p ** mid for p in ps)
        if abs(s - 1.0) < 1e-9:
            break
        if s > 1.0:
            # Need to shrink probs further → bigger exponent
            lo = mid
        else:
            hi = mid
    powered = [p ** mid for p in ps]
    s = sum(powered)
    return [x / s for x in powered]


def shin(odds: list[float], max_iter: int = 100, tol: float = 1e-10) -> list[float]:
    """Shin devig (1993). Iteratively solves for insider-information parameter z.

    Models the vig as resulting from informed bettors. For 2-way markets this is
    equivalent to additive. For 3+ way markets this differs and is generally better
    than multiplicative.

    Solves for z in (0,1) such that fair_p_i = (sqrt(z^2 + 4(1-z) * imp_p_i^2 / sum_imp) - z) / (2(1-z))
    and sum(fair_p_i) = 1.
    """
    ps = [american_to_implied(o) for o in odds]
    s = sum(ps)
    if s <= 1.0:
        # No vig to remove; just normalize
        return [p / s for p in ps]

    def fair_with_z(z: float) -> list[float]:
        return [
            (math.sqrt(z * z + 4 * (1 - z) * (p * p) / s) - z) / (2 * (1 - z))
            for p in ps
        ]

    # Bisect z in (0, 1)
    lo, hi = 1e-9, 1 - 1e-9
    for _ in range(max_iter):
        z = (lo + hi) / 2
        f = fair_with_z(z)
        total = sum(f)
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = z
        else:
            hi = z
    f = fair_with_z(z)
    # Numerical clean-up
    total = sum(f)
    return [x / total for x in f]


def worst(odds: list[float]) -> list[float]:
    """Jacob's "worst" mode: use MAX implied probability across comp books as the fair value.

    This is the most conservative possible devig — it assumes the highest-vig book
    is right. Used by Rebet J file. Implied_p_max maps to fair_p_max via 1-x for other side.
    Only well-defined for 2-way markets.
    """
    if len(odds) != 2:
        raise ValueError("worst() defined only for 2-way markets")
    ps = [american_to_implied(o) for o in odds]
    # Take the larger implied probability as fair_p for that side, fair_p for the other = 1 - fair_p
    larger_idx = 0 if ps[0] >= ps[1] else 1
    fair = [0.0, 0.0]
    fair[larger_idx] = ps[larger_idx]
    fair[1 - larger_idx] = 1.0 - ps[larger_idx]
    return fair


def worstcase(odds: list[float]) -> list[float]:
    """Jacob's "worstcase" mode: multiplicative devig using max-vig formulation.

    For 2-way markets this is equivalent to standard multiplicative (since both sides
    share the vig equally). Listed separately for explicit support and clarity.

    For 3+ way markets, this would diverge from multiplicative, but Jacob's deployed
    configs are all 2-way.
    """
    return multiplicative(odds)


METHODS = {
    "multiplicative": multiplicative,
    "additive": additive,
    "power": power,
    "shin": shin,
    "worst": worst,
    "worstcase": worstcase,
}


def devig(odds: list[float], method: str = "multiplicative") -> list[float]:
    """Generic dispatch."""
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}. Options: {sorted(METHODS)}")
    return METHODS[method](odds)


def compare_all(odds: list[float]) -> dict[str, list[float]]:
    """Run all methods and return fair probabilities under each."""
    out = {}
    for name, fn in METHODS.items():
        try:
            out[name] = fn(odds)
        except Exception as e:
            out[name] = f"N/A ({e})"
    return out


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Compare devig methods on a single market")
    ap.add_argument("odds", nargs="+", type=float, help="American odds, e.g. -110 -110")
    ap.add_argument("--method", choices=sorted(METHODS), help="show one method only")
    args = ap.parse_args()

    if args.method:
        fair = devig(args.odds, args.method)
        for o, p in zip(args.odds, fair):
            print(f"  {o:+.0f}  fair_p={p:.4f}  fair_odds={implied_to_american(p):+.1f}  decimal={implied_to_decimal(p):.4f}")
    else:
        results = compare_all(args.odds)
        cols = [f"{o:+.0f}" for o in args.odds]
        print(f"{'method':<16} {'  '.join(f'{c:>10}' for c in cols)}")
        print("-" * (16 + 12 * len(cols)))
        for name, fair in results.items():
            if isinstance(fair, str):
                print(f"{name:<16} {fair}")
                continue
            row = " ".join(f"{p:>10.4f}" for p in fair)
            print(f"{name:<16} {row}")
        # Show implied (with vig) for reference
        ps = [american_to_implied(o) for o in args.odds]
        print(f"{'(raw implied)':<16} {' '.join(f'{p:>10.4f}' for p in ps)}  → sum={sum(ps):.4f}")


if __name__ == "__main__":
    _cli()
