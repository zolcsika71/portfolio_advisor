from __future__ import annotations

import pytest

from portfolio_advisor.database.repository import HoldingObservation
from portfolio_advisor.metrics.portfolio import calculate_portfolio_metrics


def _holding(**overrides: object) -> HoldingObservation:
    values: dict[str, object] = {
        "portfolio_name": "Conservative",
        "product": "Fund",
        "isin": "X",
        "allocation": 50.0,
        "currency": "HUF",
        "currency_risk": "Hedged",
        "return_1y": 0.02,
        "sharpe_ratio_1y": 0.5,
        "volatility_1y": 0.04,
        "downside_risk": 0.03,
        "maximum_drawdown": -0.05,
    }
    values.update(overrides)
    return HoldingObservation(**values)  # type: ignore[arg-type]


def test_weighted_reported_indicators_and_currency_allocations() -> None:
    metrics = calculate_portfolio_metrics(
        [_holding(), _holding(allocation=50.0, return_1y=0.06, maximum_drawdown=-0.01, currency="EUR", currency_risk="Unhedged")]
    )
    assert metrics.allocation_total == 100.0
    assert metrics.return_1y.value == pytest.approx(0.04)
    assert metrics.maximum_drawdown.value == pytest.approx(-0.03)
    assert metrics.unhedged_allocation.value == pytest.approx(0.5)
    assert metrics.currency_concentration.value == pytest.approx(0.5)


def test_missing_indicator_is_explicitly_covered_and_warned() -> None:
    metrics = calculate_portfolio_metrics([_holding(), _holding(maximum_drawdown=None)])
    assert metrics.maximum_drawdown.available
    assert metrics.maximum_drawdown.coverage == pytest.approx(0.5)
    assert metrics.maximum_drawdown.warning is not None


def test_empty_and_mixed_portfolio_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        calculate_portfolio_metrics([])
    with pytest.raises(ValueError, match="exactly one"):
        calculate_portfolio_metrics([_holding(), _holding(portfolio_name="Other")])
