"""Deterministic return-series formulas used when a return history is supplied.

The source SQLite database does not contain periodic return series, so these
formulas are deliberately separate from latest-date portfolio aggregation.
All return inputs are decimal returns (for example, ``0.01`` for one percent).
"""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite, sqrt
from statistics import fmean, stdev


def _clean_returns(returns: Sequence[float | None], minimum: int) -> list[float] | None:
    """Return finite observations or ``None`` when a series is incomplete/short."""
    if any(value is None for value in returns):
        return None
    values = [float(value) for value in returns if value is not None]
    if len(values) < minimum or any(not isfinite(value) or value < -1.0 for value in values):
        return None
    return values


def compounded_return(returns: Sequence[float | None]) -> float | None:
    """Calculate ``product(1 + r_t) - 1``; empty or missing series return ``None``."""
    values = _clean_returns(returns, minimum=1)
    if values is None:
        return None
    wealth = 1.0
    for value in values:
        wealth *= 1.0 + value
        if not isfinite(wealth):
            return None
    return wealth - 1.0


def annualized_volatility(
    returns: Sequence[float | None], periods_per_year: int
) -> float | None:
    """Sample standard deviation times ``sqrt(periods_per_year)``.

    At least two non-missing observations and a positive annualization count
    are required. A zero-volatility series returns ``0.0``.
    """
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    values = _clean_returns(returns, minimum=2)
    return None if values is None else stdev(values) * sqrt(periods_per_year)


def maximum_drawdown(returns: Sequence[float | None]) -> float | None:
    """Calculate the minimum peak-to-trough wealth loss from a return series.

    The returned value is non-positive. A single return is valid; missing or
    empty sequences are unavailable.
    """
    values = _clean_returns(returns, minimum=1)
    if values is None:
        return None
    wealth = peak = 1.0
    drawdown = 0.0
    for value in values:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        drawdown = min(drawdown, wealth / peak - 1.0)
    return drawdown


def downside_deviation(
    returns: Sequence[float | None], target_return: float, periods_per_year: int
) -> float | None:
    """Annualized root-mean-square shortfall below a per-period target.

    Uses all observations in the denominator, so an all-positive series is
    exactly zero. It requires at least one complete observation.
    """
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    values = _clean_returns(returns, minimum=1)
    if values is None:
        return None
    squared_shortfalls = [min(value - target_return, 0.0) ** 2 for value in values]
    return sqrt(fmean(squared_shortfalls)) * sqrt(periods_per_year)


def sharpe_ratio(
    returns: Sequence[float | None], risk_free_return: float, periods_per_year: int
) -> float | None:
    """Annualized mean excess return divided by annualized sample volatility.

    ``risk_free_return`` is per period. Zero volatility returns ``None`` rather
    than an artificial infinite ratio.
    """
    volatility = annualized_volatility(returns, periods_per_year)
    values = _clean_returns(returns, minimum=2)
    if volatility is None or values is None or volatility == 0.0:
        return None
    annualized_excess = fmean(value - risk_free_return for value in values) * periods_per_year
    return annualized_excess / volatility


def sortino_ratio(
    returns: Sequence[float | None], target_return: float, periods_per_year: int
) -> float | None:
    """Annualized mean excess return divided by annualized downside deviation."""
    downside = downside_deviation(returns, target_return, periods_per_year)
    values = _clean_returns(returns, minimum=1)
    if downside is None or values is None or downside == 0.0:
        return None
    annualized_excess = fmean(value - target_return for value in values) * periods_per_year
    return annualized_excess / downside


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    """Linear-interpolated empirical quantile without a third-party dependency."""
    position = probability * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] + fraction * (sorted_values[upper] - sorted_values[lower])


def historical_var(returns: Sequence[float | None], confidence_level: float) -> float | None:
    """Historical VaR as a non-negative loss at the lower return quantile.

    At least two complete observations are required. ``confidence_level`` must
    lie strictly between zero and one.
    """
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be strictly between 0 and 1")
    values = _clean_returns(returns, minimum=2)
    if values is None:
        return None
    return max(0.0, -_quantile(sorted(values), 1.0 - confidence_level))


def historical_cvar(returns: Sequence[float | None], confidence_level: float) -> float | None:
    """Historical CVaR: mean non-negative loss in the VaR tail, inclusive."""
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be strictly between 0 and 1")
    values = _clean_returns(returns, minimum=2)
    if values is None:
        return None
    cutoff = _quantile(sorted(values), 1.0 - confidence_level)
    tail = [value for value in values if value <= cutoff]
    return max(0.0, -fmean(tail))
