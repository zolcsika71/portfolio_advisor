"""Typed, serializable results for deterministic walk-forward backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

type BacktestResultType = Literal[
    "OFFICIAL_BACKTEST", "BACKTEST_REJECTED", "DIAGNOSTICS_ONLY"
]


class BacktestResultAdmissionError(ValueError):
    """A non-official or malformed period was offered to a performance consumer."""


@dataclass(frozen=True, slots=True)
class ForwardMetrics:
    """Metrics recomputed solely from NAV observations after an evaluation date."""

    total_return: float | None
    annualized_return: float | None
    annualized_volatility: float | None
    maximum_drawdown: float | None
    downside_deviation: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    historical_var: float | None
    historical_cvar: float | None
    return_observation_count: int
    reported_metrics: tuple[str, ...] = field(default_factory=tuple)
    unavailable_metrics: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class BaselineResult:
    """One point-in-time comparison strategy and its forward outcome."""

    strategy: str
    portfolio_names: tuple[str, ...]
    forward_metrics: ForwardMetrics | None
    incomplete_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConstituentDiagnostic:
    """One original portfolio constituent, retained without modification."""

    isin: str
    weight: float
    asset_class: str | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class UnresolvedConstituent:
    """A constituent that blocks an official backtest under the strict policy."""

    isin: str
    category: str
    weight: float
    resolution_reference: str | None = None


@dataclass(frozen=True, slots=True)
class BacktestEligibility:
    """Machine-readable strict-policy decision made before NAV/metric use."""

    eligible: bool
    status: str
    policy_id: str
    coverage_status: str
    resolvable_weight: float
    unresolved_weight: float
    blocking_constituents: tuple[UnresolvedConstituent, ...]
    constituent_weights: tuple[ConstituentDiagnostic, ...]
    diagnostics_allowed: bool


@dataclass(frozen=True, slots=True)
class BacktestDiagnostics:
    """Non-performance diagnostics for a strictly rejected backtest window."""

    result_type: Literal["DIAGNOSTICS_ONLY"]
    policy_id: str
    portfolio_name: str
    window_start: date
    window_end: date
    horizon_days: int
    coverage_status: str
    constituent_count: int
    resolvable_weight: float
    unresolved_weight: float
    unresolved_constituents: tuple[UnresolvedConstituent, ...]
    constituent_weights: tuple[ConstituentDiagnostic, ...]
    official_return_available: bool = False
    official_risk_metrics_available: bool = False
    ranking_eligible: bool = False
    selection_eligible: bool = False

    def __post_init__(self) -> None:
        if self.result_type != "DIAGNOSTICS_ONLY":
            raise BacktestResultAdmissionError("diagnostics must have result_type DIAGNOSTICS_ONLY")
        if (
            self.official_return_available
            or self.official_risk_metrics_available
            or self.ranking_eligible
            or self.selection_eligible
        ):
            raise BacktestResultAdmissionError(
                "diagnostics-only records cannot be admitted to financial or selection consumers"
            )


@dataclass(frozen=True, slots=True)
class BacktestPeriodResult:
    """One ranking decision and its strictly later evaluation outcome."""

    evaluation_date: date
    horizon_days: int
    candidate_count: int
    selected_portfolio: str | None
    selected_rank: int | None
    selected_score: float | None
    rule_set_version: str
    proposed_rules_explicitly_enabled: bool
    realized_forward_metrics: ForwardMetrics | None
    baseline_results: tuple[BaselineResult, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    incomplete_period_reason: str | None = None
    result_type: BacktestResultType = "OFFICIAL_BACKTEST"
    eligibility: BacktestEligibility | None = None
    diagnostics: BacktestDiagnostics | None = None

    def __post_init__(self) -> None:
        if self.result_type == "OFFICIAL_BACKTEST":
            if self.diagnostics is not None:
                raise BacktestResultAdmissionError(
                    "official backtest results cannot carry diagnostics-only payloads"
                )
            if self.eligibility is not None and not self.eligibility.eligible:
                raise BacktestResultAdmissionError(
                    "official backtest results require an eligible strict-policy decision"
                )
            return
        if self.realized_forward_metrics is not None or self.baseline_results:
            raise BacktestResultAdmissionError(
                "rejected or diagnostics-only results cannot carry official performance outcomes"
            )
        if any(
            value is not None
            for value in (self.selected_portfolio, self.selected_rank, self.selected_score)
        ):
            raise BacktestResultAdmissionError(
                "rejected or diagnostics-only results cannot carry ranking or winner-selection output"
            )
        if self.result_type == "DIAGNOSTICS_ONLY":
            if self.diagnostics is None:
                raise BacktestResultAdmissionError("diagnostics-only result is missing its diagnostics payload")
            return
        if self.result_type == "BACKTEST_REJECTED" and self.diagnostics is not None:
            raise BacktestResultAdmissionError(
                "BACKTEST_REJECTED must not carry diagnostics unless diagnostics mode was requested"
            )
        if self.result_type != "BACKTEST_REJECTED":
            raise BacktestResultAdmissionError(f"unknown backtest result type: {self.result_type!r}")


def require_official_backtest_result(result: BacktestPeriodResult) -> BacktestPeriodResult:
    """Fail closed before any performance ranking, scoring, or comparison consumer."""
    if result.result_type != "OFFICIAL_BACKTEST":
        raise BacktestResultAdmissionError(
            f"only OFFICIAL_BACKTEST results may enter performance consumers, got {result.result_type}"
        )
    if result.diagnostics is not None or (
        result.eligibility is not None and not result.eligibility.eligible
    ):
        raise BacktestResultAdmissionError("official result has inconsistent strict-policy admission")
    return result


@dataclass(frozen=True, slots=True)
class BacktestAggregate:
    """Summary statistics calculated only from complete forward outcomes."""

    evaluation_period_count: int
    complete_period_count: int
    incomplete_period_count: int
    average_realized_return: float | None
    median_realized_return: float | None
    average_maximum_drawdown: float | None
    worst_maximum_drawdown: float | None
    average_downside_deviation: float | None
    hit_rate_vs_baselines: dict[str, float | None]
    selection_frequency: dict[str, int]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """A complete deterministic walk-forward run for one horizon."""

    horizon_days: int
    periods: tuple[BacktestPeriodResult, ...]
    aggregate: BacktestAggregate
