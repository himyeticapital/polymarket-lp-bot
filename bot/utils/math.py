"""Mathematical utilities: Kelly criterion, Sharpe ratio, reward scoring."""

from __future__ import annotations

import math
import statistics


def kelly_criterion(
    edge: float, price: float, fraction: float = 0.25
) -> float:
    """Calculate position size using fractional Kelly criterion.

    Args:
        edge: Estimated edge (synth_prob - market_prob).
        price: Current market price (probability).
        fraction: Kelly fraction (0.25 = quarter-Kelly for safety).

    Returns:
        Fraction of bankroll to bet (0.0 if no edge).
    """
    if price <= 0 or price >= 1 or edge <= 0:
        return 0.0
    b = (1.0 / price) - 1.0  # Odds ratio
    p = price + edge  # Estimated true probability
    q = 1.0 - p
    if p <= 0 or p >= 1:
        return 0.0
    kelly = (b * p - q) / b
    return max(0.0, kelly * fraction)


def reward_score(
    max_spread: float, actual_spread: float, size: float
) -> float:
    """Calculate liquidity reward score using quadratic formula.

    S(v,s) = ((v - s) / v)^2 * b

    Args:
        max_spread: max_incentive_spread for the market.
        actual_spread: Order's spread from adjusted midpoint.
        size: Order size.

    Returns:
        Reward score (0.0 if outside max spread).
    """
    if max_spread <= 0 or actual_spread >= max_spread or actual_spread < 0:
        return 0.0
    return ((max_spread - actual_spread) / max_spread) ** 2 * size


def sharpe_ratio(returns: list[float], annualization_factor: float = 1.0) -> float:
    """Calculate Sharpe ratio from a list of trade returns.

    Args:
        returns: List of individual trade P&L values.
        annualization_factor: sqrt(trades_per_day) for daily Sharpe.

    Returns:
        Sharpe ratio (0.0 if insufficient data or zero variance).
    """
    if len(returns) < 2:
        return 0.0
    mean = statistics.mean(returns)
    std = statistics.stdev(returns)
    if std == 0:
        return 0.0
    return (mean / std) * annualization_factor


def win_rate(wins: int, losses: int) -> float:
    """Calculate win rate as a percentage."""
    total = wins + losses
    if total == 0:
        return 0.0
    return wins / total


def runway_pct(balance: float, avg_daily_loss: float) -> float:
    """Calculate runway as percentage.

    How long until bankrupt at worst-case daily loss rate.
    Returns percentage (0-100+).
    """
    if avg_daily_loss <= 0:
        return 100.0
    days = balance / avg_daily_loss
    return min(days * 100 / 30, 100.0)  # Normalize to 30-day horizon


def bayesian_posterior(
    prior: float, likelihood: float, evidence: float
) -> float:
    """Calculate Bayesian posterior probability.

    P(H|E) = P(E|H) * P(H) / P(E)

    Args:
        prior: P(H) — prior probability of hypothesis.
        likelihood: P(E|H) — probability of evidence given hypothesis.
        evidence: P(E) — total probability of evidence.

    Returns:
        Posterior probability P(H|E).
    """
    if evidence <= 0:
        return prior
    return (likelihood * prior) / evidence


def round_to_tick(price: float, tick: float = 0.01) -> float:
    """Round price to nearest valid tick size."""
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 10)


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value to [min_val, max_val] range."""
    return max(min_val, min(value, max_val))
