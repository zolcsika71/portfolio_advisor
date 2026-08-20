from __future__ import annotations

from datetime import date, timedelta
from math import sqrt
from pathlib import Path

import pytest

from portfolio_advisor.backtesting.service import (
    BacktestSettings,
    WalkForwardBacktester,
)
from portfolio_advisor.history.models import NavObservation, NavSeries
from portfolio_advisor.metrics.calculations import (
    annualized_volatility,
    compounded_return,
    historical_cvar,
    historical_var,
    maximum_drawdown,
    sharpe_ratio,
)
from portfolio_advisor.metrics.methodology_validation import (
    build_methodology_validation,
)
from portfolio_advisor.metrics.models import MetricValue
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.normalization import normalize_metric

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1.01])
def test_nonfinite_or_impossible_returns_fail_closed(value: float) -> None:
    assert compounded_return([value]) is None
    assert annualized_volatility([0.01, value], 252) is None
    assert sharpe_ratio([0.01, value], 0.0, 252) is None
    assert historical_var([0.01, value], 0.95) is None
    assert historical_cvar([0.01, value], 0.95) is None


def test_drawdown_and_tail_metrics_respect_path_and_loss_directions() -> None:
    smooth = maximum_drawdown([0.03, 0.03, 0.03])
    crash = maximum_drawdown([0.20, -0.40, 0.50])
    assert smooth is not None and crash is not None
    assert smooth == 0.0
    assert crash == pytest.approx(-0.40)
    assert crash < smooth

    returns = [-0.20, -0.05, 0.01, 0.02, 0.03]
    var = historical_var(returns, 0.80)
    cvar = historical_cvar(returns, 0.80)
    assert var is not None and cvar is not None
    assert var >= 0.0
    assert cvar >= var


@pytest.mark.parametrize("horizon", [90, 180, 365])
def test_actual_forward_metric_annualization_uses_elapsed_calendar_days(horizon: int) -> None:
    start = date(2025, 1, 1)
    end = start + timedelta(days=horizon)
    series = NavSeries(
        "Alpha",
        (
            NavObservation(start, "Alpha", 100.0),
            NavObservation(start + timedelta(days=horizon // 2), "Alpha", 102.0),
            NavObservation(end, "Alpha", 105.0),
        ),
    )
    backtester = WalkForwardBacktester.__new__(WalkForwardBacktester)
    backtester.settings = BacktestSettings()
    metrics = backtester._forward_metrics(series)

    assert metrics.total_return == pytest.approx(0.05)
    assert metrics.annualized_return == pytest.approx((1.05 ** (365 / horizon)) - 1.0)


def test_volatility_and_sharpe_are_directional_and_small_sample_safe() -> None:
    low_dispersion = annualized_volatility([0.01, 0.02, 0.01], 1)
    high_dispersion = annualized_volatility([-0.10, 0.12, 0.01], 1)
    assert low_dispersion is not None and high_dispersion is not None
    assert high_dispersion > low_dispersion
    assert annualized_volatility([0.01], 252) is None
    assert sharpe_ratio([0.01, 0.01], 0.0, 252) is None
    assert sharpe_ratio([-0.01, 0.01], 0.0, 1) == pytest.approx(0.0)


def test_metric_values_and_normalization_reject_nonfinite_inputs() -> None:
    with pytest.raises(ValueError, match="finite"):
        MetricValue(float("nan"), 1.0, True)
    with pytest.raises(ValueError, match="coverage"):
        MetricValue(None, float("nan"), False)
    with pytest.raises(ValueError, match="finite"):
        normalize_metric({"Safe": 0.01, "Spoof": float("inf")}, "LOWER_BETTER")


def test_proposed_policy_scores_less_negative_drawdown_as_better() -> None:
    rules = load_ranking_rules(RULES, allow_proposed=True)
    assert rules.version == "1.0.1"
    assert rules.metrics["maximum_drawdown"].direction == "HIGHER_BETTER"

    audit = build_methodology_validation(rules_path=RULES, nav_history_available=False)
    assert audit["validation_status"] == "CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS"
    assert audit["capital_preservation_alignment"] == "ALIGNED_WITH_CAVEATS"
    assert audit["monotonicity"]["capital_preservation_dominance"] == "PASS"
    assert audit["monotonicity"]["catastrophic_drawdown_high_return"]["winner"] == "Safe"


def test_sample_standard_deviation_annualization_is_exact() -> None:
    assert annualized_volatility([0.0, 0.02], 1) == pytest.approx(sqrt(0.0002))
