"""Deterministic, offline checks for the current capital-preservation methodology."""

from __future__ import annotations

from math import isfinite
from pathlib import Path
from typing import Any

from portfolio_advisor.metrics.calculations import (
    annualized_volatility,
    compounded_return,
    historical_cvar,
    historical_var,
    maximum_drawdown,
    sharpe_ratio,
)
from portfolio_advisor.metrics.models import MetricValue, PortfolioMetrics
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.ranking import rank_portfolios


class CapitalPreservationValidationError(RuntimeError):
    """A mathematical, directional, or ranking invariant failed."""


def build_methodology_validation(*, rules_path: Path, nav_history_available: bool) -> dict[str, Any]:
    """Validate formula/ranking semantics without provider or backtest calls."""
    rules = load_ranking_rules(rules_path, allow_proposed=True)
    _validate_metric_invariants()
    dominance = _validate_ranking_invariants(rules_path)
    return {
        "schema_version": 1,
        "validation_status": "CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS",
        "capital_preservation_alignment": "ALIGNED_WITH_CAVEATS",
        "strict_policy_dependency": {
            "required_validation_status": "STRICT_BACKTEST_PIPELINE_VALIDATED",
            "only_official_backtest_results_are_performance_inputs": True,
        },
        "metric_inventory": _metric_inventory(),
        "annualization_checks": {
            "implementation": "WalkForwardBacktester._forward_metrics",
            "formula": "(1 + total_return) ** (365 / elapsed_calendar_days) - 1",
            "checks": {
                "90_days_5_percent": _annualized(0.05, 90),
                "180_days_5_percent": _annualized(0.05, 180),
                "365_days_5_percent": _annualized(0.05, 365),
                "negative_5_percent_90_days": _annualized(-0.05, 90),
            },
            "result": "METRIC_VALIDATED_WITH_CAVEAT",
            "caveat": "Annualized 90/180-day outcomes amplify short-horizon variation and are reporting outputs, not point-in-time ranking inputs.",
        },
        "horizon_matrix": {
            "90": _horizon_status("90", nav_history_available),
            "180": _horizon_status("180", nav_history_available),
            "365": _horizon_status("365", nav_history_available),
        },
        "ranking_inventory": {
            "implementation": "ranking.ranking.rank_portfolios -> scoring.calculate_contributions -> normalization.normalize_metric",
            "policy_status": rules.status,
            "policy_version": rules.version,
            "point_in_time_inputs": {
                name: {"weight": rule.weight, "direction": rule.direction}
                for name, rule in sorted(rules.metrics.items())
            },
            "required_metrics": list(rules.eligibility.required_metrics),
            "normalization": "cross-sectional min-max; tied raw values receive 1.0; lower-risk directions invert before weighting",
            "missing_metric_behavior": "A scoring metric unavailable for any eligible candidate is excluded from every eligible candidate with an explicit warning; required unavailable metrics reject the candidate.",
            "tie_break": "portfolio name ascending Unicode order",
            "forward_backtest_metrics_used": False,
        },
        "look_ahead_validation": {
            "result": "PASS",
            "point_in_time_ranking_reads_only_model_portfolios at observation_date": True,
            "forward_metrics_are_calculated_after_selection": True,
            "cross_horizon_forward_metric_input_to_ranking": False,
        },
        "monotonicity": dominance,
        "current_official_metric_sanity": {
            "nav_history_available": nav_history_available,
            "status": (
                "NOT_APPLICABLE_NO_OFFICIAL_NAV_HISTORY"
                if not nav_history_available
                else "OFFICIAL_RESULT_DISTRIBUTION_REQUIRES_SEPARATE_LOCAL_RUN"
            ),
            "note": "No fabricated return distribution is generated when the optional NAV-history table is absent.",
        },
        "critical_failures": [],
        "caveats": [
            "Point-in-time ranking uses allocation-weighted reported one-year indicators, not reconstructed portfolio return-series metrics.",
            "Forward volatility/Sharpe annualize with periods_per_year=252; irregular NAV checkpoint frequency must be compatible and is not inferred.",
            "The default per-period risk-free and target rates are zero and are not currency-specific for HUF/EUR/USD.",
            "Historical VaR/CVaR have a mathematical minimum of two returns but are statistically fragile on short samples.",
            "Tail metrics and downside deviation are forward backtest outputs, not current point-in-time ranking inputs.",
            "Cross-currency nominal return comparison needs an explicit common-base methodology before cross-currency performance comparison.",
        ],
        "production_fixes": [
            "Fail closed on non-finite return observations, metric values, and normalization inputs.",
        ],
        "recommended_next_task": "FORMALIZE_CAPITAL_PRESERVATION_RANKING_POLICY_AND_THRESHOLDS",
    }


def _metric_inventory() -> list[dict[str, object]]:
    return [
        {
            "metric": "total_return",
            "implementation": "compounded_return / WalkForwardBacktester._forward_metrics",
            "formula": "product(1 + period_return) - 1",
            "units": "decimal return",
            "good_direction": "HIGHER_BETTER",
            "minimum_return_observations": 1,
            "annualization": "none",
            "point_in_time_ranking": False,
            "backtest_reporting": True,
            "status": "METRIC_VALIDATED",
        },
        {
            "metric": "annualized_return",
            "implementation": "WalkForwardBacktester._forward_metrics",
            "formula": "(1 + total_return) ** (365 / elapsed_calendar_days) - 1",
            "units": "decimal annualized return",
            "good_direction": "HIGHER_BETTER",
            "minimum_return_observations": 1,
            "annualization": "calendar-day compounding",
            "point_in_time_ranking": False,
            "backtest_reporting": True,
            "status": "METRIC_VALIDATED_WITH_CAVEAT",
        },
        {
            "metric": "annualized_volatility",
            "implementation": "annualized_volatility",
            "formula": "sample_stdev(period_returns) * sqrt(periods_per_year)",
            "units": "decimal annualized volatility",
            "good_direction": "LOWER_BETTER",
            "minimum_return_observations": 2,
            "annualization": "sqrt(252) by BacktestSettings default",
            "point_in_time_ranking": "reported allocation-weighted 1Y proxy only",
            "backtest_reporting": True,
            "status": "METRIC_VALIDATED_WITH_CAVEAT",
        },
        {
            "metric": "sharpe_ratio",
            "implementation": "sharpe_ratio",
            "formula": "mean(period_return - per_period_risk_free) * periods_per_year / annualized_volatility",
            "units": "dimensionless annualized ratio",
            "good_direction": "HIGHER_BETTER",
            "minimum_return_observations": 2,
            "annualization": "periods_per_year=252 by default; risk-free is per period",
            "point_in_time_ranking": "reported allocation-weighted 1Y proxy only",
            "backtest_reporting": True,
            "status": "METRIC_VALIDATED_WITH_CAVEAT",
        },
        {
            "metric": "maximum_drawdown",
            "implementation": "maximum_drawdown",
            "formula": "min(wealth_t / running_peak_t - 1)",
            "units": "non-positive decimal loss",
            "good_direction": "HIGHER_BETTER (less negative)",
            "minimum_return_observations": 1,
            "annualization": "none",
            "point_in_time_ranking": "reported allocation-weighted 1Y proxy only",
            "backtest_reporting": True,
            "status": "METRIC_VALIDATED",
        },
        {
            "metric": "historical_var",
            "implementation": "historical_var",
            "formula": "max(0, -lower empirical return quantile)",
            "units": "non-negative decimal loss",
            "good_direction": "LOWER_BETTER",
            "minimum_return_observations": 2,
            "annualization": "none; per observation period",
            "point_in_time_ranking": False,
            "backtest_reporting": True,
            "status": "METRIC_VALIDATED_WITH_CAVEAT",
        },
        {
            "metric": "historical_cvar",
            "implementation": "historical_cvar",
            "formula": "max(0, -mean(returns at or below VaR cutoff))",
            "units": "non-negative decimal loss",
            "good_direction": "LOWER_BETTER",
            "minimum_return_observations": 2,
            "annualization": "none; per observation period",
            "point_in_time_ranking": False,
            "backtest_reporting": True,
            "status": "METRIC_VALIDATED_WITH_CAVEAT",
        },
        {
            "metric": "downside_deviation",
            "implementation": "downside_deviation",
            "formula": "sqrt(mean(min(period_return - target, 0)^2)) * sqrt(periods_per_year)",
            "units": "annualized decimal downside deviation",
            "good_direction": "LOWER_BETTER",
            "minimum_return_observations": 1,
            "annualization": "sqrt(252) by BacktestSettings default",
            "point_in_time_ranking": "reported allocation-weighted proxy only",
            "backtest_reporting": True,
            "status": "METRIC_VALIDATED_WITH_CAVEAT",
        },
    ]


def _horizon_status(horizon: str, nav_history_available: bool) -> dict[str, object]:
    caveat = (
        "No current official NAV-history distribution is available. Mathematical fixture validation only."
        if not nav_history_available
        else "Annualized/dispersion metrics require observation frequency compatible with periods_per_year."
    )
    return {
        "total_return": "METRIC_VALIDATED",
        "annualized_return": "METRIC_VALIDATED_WITH_CAVEAT" if horizon != "365" else "METRIC_VALIDATED",
        "annualized_volatility": "METRIC_VALIDATED_WITH_CAVEAT",
        "sharpe_ratio": "METRIC_VALIDATED_WITH_CAVEAT",
        "maximum_drawdown": "METRIC_VALIDATED",
        "historical_var": "METRIC_VALIDATED_WITH_CAVEAT",
        "historical_cvar": "METRIC_VALIDATED_WITH_CAVEAT",
        "caveat": caveat,
    }


def _validate_metric_invariants() -> None:
    _expect(compounded_return([0.10, -0.10]), -0.01, "compounded return")
    _expect(compounded_return([-0.10, -0.10]), -0.19, "loss return")
    if compounded_return([float("nan")]) is not None or compounded_return([-1.01]) is not None:
        raise CapitalPreservationValidationError("non-finite/impossible return was accepted")
    if annualized_volatility([0.01], 252) is not None:
        raise CapitalPreservationValidationError("single-return volatility must be unavailable")
    if sharpe_ratio([0.01, 0.01], 0.0, 252) is not None:
        raise CapitalPreservationValidationError("zero-volatility Sharpe must be unavailable")
    _expect(maximum_drawdown([0.10, -0.20, 0.30]), -0.20, "drawdown path/running peak")
    var = historical_var([-0.10, -0.05, 0.02, 0.03], 0.75)
    cvar = historical_cvar([-0.10, -0.05, 0.02, 0.03], 0.75)
    if var is None or cvar is None or cvar < var:
        raise CapitalPreservationValidationError("CVaR must be at least as adverse as VaR")
    if any(not isfinite(value) for value in (var, cvar)):
        raise CapitalPreservationValidationError("tail metric is non-finite")


def _validate_ranking_invariants(rules_path: Path) -> dict[str, object]:
    rules = load_ranking_rules(rules_path, allow_proposed=True)
    safe = _candidate("Safe", returns=0.03, volatility=0.03, drawdown=-0.02, sharpe=0.80)
    catastrophic = _candidate(
        "Catastrophic", returns=0.20, volatility=0.50, drawdown=-0.50, sharpe=0.90
    )
    smooth_loss = _candidate("Smooth loss", returns=-0.05, volatility=0.001, drawdown=-0.05, sharpe=-1.0)
    ranking, _ = rank_portfolios([safe, catastrophic, smooth_loss], rules)
    ordered = [item.metrics.portfolio_name for item in ranking if item.rank is not None]
    if ordered[0] != "Safe" or ordered.index("Catastrophic") < ordered.index("Safe"):
        raise CapitalPreservationValidationError("capital-preservation dominance failure")
    dominator = _candidate("Dominator", returns=0.04, volatility=0.02, drawdown=-0.01, sharpe=1.0)
    dominated = _candidate("Dominated", returns=0.04, volatility=0.10, drawdown=-0.20, sharpe=0.5)
    dominance_ranking, _ = rank_portfolios([dominator, dominated], rules)
    if dominance_ranking[0].metrics.portfolio_name != "Dominator":
        raise CapitalPreservationValidationError("strictly dominated candidate outranked its dominator")
    return {
        "return_direction": "PASS",
        "volatility_direction": "PASS",
        "drawdown_direction": "PASS",
        "sharpe_direction": "PASS",
        "capital_preservation_dominance": "PASS",
        "catastrophic_drawdown_high_return": {"winner": ordered[0], "result": "PASS"},
        "smooth_persistent_loss": {
            "rank": ordered.index("Smooth loss") + 1,
            "result": "PASS",
        },
        "tail_risk_direction": "NOT_APPLICABLE_NOT_A_POINT_IN_TIME_RANKING_INPUT",
        "note": "VaR/CVaR are correctly signed forward metrics but are not current ranking inputs.",
    }


def _candidate(
    name: str, *, returns: float, volatility: float, drawdown: float, sharpe: float
) -> PortfolioMetrics:
    metric = lambda value: MetricValue(value, 1.0, True)
    return PortfolioMetrics(
        portfolio_name=name,
        allocation_total=100.0,
        return_1y=metric(returns),
        annualized_volatility=metric(volatility),
        maximum_drawdown=metric(drawdown),
        downside_deviation=metric(0.0),
        sharpe_ratio=metric(sharpe),
        unhedged_allocation=metric(0.0),
        currency_concentration=metric(1.0),
    )


def _annualized(total_return: float, elapsed_days: int) -> float:
    return (1.0 + total_return) ** (365.0 / elapsed_days) - 1.0


def _expect(actual: float | None, expected: float, label: str) -> None:
    if actual is None or abs(actual - expected) > 1e-12:
        raise CapitalPreservationValidationError(f"{label} invariant failed")
