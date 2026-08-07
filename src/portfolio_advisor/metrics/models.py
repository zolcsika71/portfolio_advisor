"""Typed metric values and candidate-level calculated metric sets."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MetricValue:
    """A value plus data coverage and an explicit availability explanation."""

    value: float | None
    coverage: float
    available: bool
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioMetrics:
    """Metrics calculated from one latest-date model-portfolio observation."""

    portfolio_name: str
    allocation_total: float
    return_1y: MetricValue
    annualized_volatility: MetricValue
    maximum_drawdown: MetricValue
    downside_deviation: MetricValue
    sharpe_ratio: MetricValue
    unhedged_allocation: MetricValue
    currency_concentration: MetricValue
    unavailable_metrics: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
