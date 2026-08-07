from __future__ import annotations

from math import sqrt

import pytest

from portfolio_advisor.metrics.calculations import (
    annualized_volatility,
    compounded_return,
    downside_deviation,
    historical_cvar,
    historical_var,
    maximum_drawdown,
    sharpe_ratio,
    sortino_ratio,
)


def test_compounded_return_and_missing_behavior() -> None:
    assert compounded_return([0.10, -0.10]) == pytest.approx(-0.01)
    assert compounded_return([-0.10, -0.10]) == pytest.approx(-0.19)
    assert compounded_return([]) is None
    assert compounded_return([0.01, None]) is None


def test_annualized_volatility_handles_single_missing_and_zero_series() -> None:
    assert annualized_volatility([0.01], 12) is None
    assert annualized_volatility([0.01, None], 12) is None
    assert annualized_volatility([0.01, 0.01], 12) == 0.0
    assert annualized_volatility([0.0, 0.02], 1) == pytest.approx(sqrt(0.0002))


def test_drawdown_and_downside_edge_cases() -> None:
    assert maximum_drawdown([]) is None
    assert maximum_drawdown([0.10, -0.20, 0.05]) == pytest.approx(-0.20)
    assert maximum_drawdown([0.01, 0.02]) == 0.0
    assert downside_deviation([0.01, 0.02], 0.0, 1) == 0.0
    assert downside_deviation([-0.10, 0.10], 0.0, 1) == pytest.approx(sqrt(0.005))


def test_sharpe_and_sortino_handle_zero_denominators() -> None:
    assert sharpe_ratio([0.01, 0.01], 0.0, 12) is None
    assert sortino_ratio([0.01, 0.02], 0.0, 12) is None
    assert sharpe_ratio([-0.01, 0.01], 0.0, 1) == 0.0


def test_historical_var_and_cvar_are_deterministic() -> None:
    returns = [-0.10, -0.05, 0.02, 0.03]
    assert historical_var(returns, 0.75) == pytest.approx(0.0625)
    assert historical_cvar(returns, 0.75) == pytest.approx(0.10)
    assert historical_var([0.01], 0.95) is None
    assert historical_var([0.01, 0.02], 0.95) == 0.0


@pytest.mark.parametrize("confidence", [0.0, 1.0, -0.1, 1.1])
def test_var_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence_level"):
        historical_var([0.01, -0.02], confidence)
