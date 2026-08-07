"""Aggregate reported constituent indicators into transparent portfolio proxies."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from portfolio_advisor.database.repository import HoldingObservation

from .models import MetricValue, PortfolioMetrics


def _weighted_metric(
    holdings: list[HoldingObservation], attribute: str, label: str
) -> MetricValue:
    total = sum(holding.allocation or 0.0 for holding in holdings)
    observed = [
        holding
        for holding in holdings
        if holding.allocation is not None and getattr(holding, attribute) is not None
    ]
    covered = sum(holding.allocation or 0.0 for holding in observed)
    coverage = covered / total if total > 0.0 else 0.0
    if not observed or covered <= 0.0:
        return MetricValue(None, coverage, False, f"{label} is absent for all allocated holdings")
    value = sum((holding.allocation or 0.0) * getattr(holding, attribute) for holding in observed) / covered
    warning = None
    if coverage < 1.0:
        warning = f"{label} covers {coverage:.1%} of allocation; missing holdings are excluded"
    return MetricValue(float(value), coverage, True, warning)


def _allocation_indicator(
    holdings: list[HoldingObservation], predicate: Callable[[HoldingObservation], bool], label: str
) -> MetricValue:
    total = sum(holding.allocation or 0.0 for holding in holdings)
    if total <= 0.0:
        return MetricValue(None, 0.0, False, "Allocation total must be positive")
    known = [holding for holding in holdings if holding.allocation is not None]
    value = sum((holding.allocation or 0.0) for holding in known if predicate(holding)) / total
    return MetricValue(value, 1.0, True, None)


def _currency_concentration(holdings: list[HoldingObservation]) -> MetricValue:
    """Return the Herfindahl concentration of known currency allocations.

    A value of one means one reported currency; smaller values imply more
    dispersed currencies. It is descriptive only because an investor base
    currency is not present in the database.
    """
    total = sum(holding.allocation or 0.0 for holding in holdings)
    known = [
        holding
        for holding in holdings
        if holding.allocation is not None and holding.currency is not None
    ]
    covered = sum(holding.allocation or 0.0 for holding in known)
    coverage = covered / total if total > 0.0 else 0.0
    if covered <= 0.0:
        return MetricValue(None, coverage, False, "Currency is absent for all allocated holdings")
    allocations: dict[str, float] = defaultdict(float)
    for holding in known:
        allocations[holding.currency or ""] += holding.allocation or 0.0
    value = sum((allocation / covered) ** 2 for allocation in allocations.values())
    warning = None if coverage == 1.0 else f"Currency covers {coverage:.1%} of allocation"
    return MetricValue(value, coverage, True, warning)


def calculate_portfolio_metrics(holdings: list[HoldingObservation]) -> PortfolioMetrics:
    """Calculate only metrics supported by latest-date database columns.

    Reported one-year volatility, drawdown, Sharpe, return, and downside risk
    are allocation-weighted *indicators*, not reconstructed portfolio series
    metrics: covariance, historical daily returns, fees, and liquidity fields
    are absent from the schema. VaR, CVaR, Sortino, and recomputed drawdown are
    therefore reported as unavailable.
    """
    if not holdings:
        raise ValueError("A portfolio requires at least one holding")
    names = {holding.portfolio_name for holding in holdings}
    if len(names) != 1:
        raise ValueError("Holdings must belong to exactly one portfolio")
    total = sum(holding.allocation or 0.0 for holding in holdings)
    return_1y = _weighted_metric(holdings, "return_1y", "1-year return")
    volatility = _weighted_metric(holdings, "volatility_1y", "reported annualized volatility")
    drawdown = _weighted_metric(holdings, "maximum_drawdown", "reported maximum drawdown")
    downside = _weighted_metric(holdings, "downside_risk", "reported downside risk")
    sharpe = _weighted_metric(holdings, "sharpe_ratio_1y", "reported 1-year Sharpe ratio")
    unhedged = _allocation_indicator(
        holdings,
        lambda item: (item.currency_risk or "").casefold() == "unhedged",
        "unhedged allocation",
    )
    currency_concentration = _currency_concentration(holdings)
    metric_values = (
        return_1y,
        volatility,
        drawdown,
        downside,
        sharpe,
        unhedged,
        currency_concentration,
    )
    warnings = tuple(value.warning for value in metric_values if value.warning)
    unavailable = [
        "historical_var: periodic return history is not stored",
        "historical_cvar: periodic return history is not stored",
        "sortino_ratio: periodic return history and target return are not stored",
        "cost_indicators: no fee/cost column exists",
        "liquidity_indicators: no liquidity column exists",
        "currency_mismatch: investor base currency is not supplied; unhedged allocation and currency concentration are reported instead",
    ]
    if not downside.available:
        unavailable.append("downside_deviation: reported Downside Risk is absent for all allocated holdings")
    return PortfolioMetrics(
        portfolio_name=next(iter(names)),
        allocation_total=total,
        return_1y=return_1y,
        annualized_volatility=volatility,
        maximum_drawdown=drawdown,
        downside_deviation=downside,
        sharpe_ratio=sharpe,
        unhedged_allocation=unhedged,
        currency_concentration=currency_concentration,
        unavailable_metrics=tuple(unavailable),
        warnings=warnings,
    )


def calculate_all_portfolio_metrics(
    holdings: Iterable[HoldingObservation],
) -> list[PortfolioMetrics]:
    """Group source rows by name and calculate candidates in stable name order."""
    groups: dict[str, list[HoldingObservation]] = defaultdict(list)
    for holding in holdings:
        groups[holding.portfolio_name].append(holding)
    return [calculate_portfolio_metrics(groups[name]) for name in sorted(groups)]
