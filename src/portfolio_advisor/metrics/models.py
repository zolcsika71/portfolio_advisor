"""Typed metric values and candidate-level calculated metric sets."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite


@dataclass(frozen=True, slots=True)
class MetricValue:
    """A value plus data coverage and an explicit availability explanation."""

    value: float | None
    coverage: float
    available: bool
    warning: str | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.coverage) or not 0.0 <= self.coverage <= 1.0:
            raise ValueError("metric coverage must be finite and between zero and one")
        if self.available:
            if self.value is None or not isfinite(self.value):
                raise ValueError("available metric values must be finite")
        elif self.value is not None:
            raise ValueError("unavailable metric values must be None")


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
