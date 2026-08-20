"""Walk-forward orchestration that delegates all ranking to Milestone 2."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import fmean, median

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.history.models import (
    SUPPORTED_HORIZON_DAYS,
    ForwardWindow,
    NavSeries,
)
from portfolio_advisor.history.repository import HistoricalPortfolioRepository
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
from portfolio_advisor.ranking.models import CandidateEvaluation

from .eligibility import (
    BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT,
    BacktestEligibilityGate,
    StrictCoverageEligibilityGate,
)
from .models import (
    BacktestAggregate,
    BacktestDiagnostics,
    BacktestEligibility,
    BacktestPeriodResult,
    BacktestResult,
    BacktestResultType,
    BaselineResult,
    ForwardMetrics,
    require_official_backtest_result,
)


@dataclass(frozen=True, slots=True)
class BacktestSettings:
    """Explicit assumptions for metrics recomputed from optional NAV history."""

    periods_per_year: int = 252
    risk_free_return_per_period: float = 0.0
    target_return_per_period: float = 0.0
    var_confidence_level: float = 0.95


def forward_metrics_from_nav_series(series: NavSeries, settings: BacktestSettings) -> ForwardMetrics:
    """Compute canonical forward metrics from one already admitted direct NAV series.

    This function contains the same metric path used by the strict walk-forward
    backtester.  It performs no source resolution, date substitution, or
    portfolio reconstruction; callers must already have exact-boundary data.
    """
    returns = [
        later.net_asset_value / earlier.net_asset_value - 1.0
        for earlier, later in zip(series.observations, series.observations[1:])
    ]
    total_return = compounded_return(returns)
    elapsed_days = (
        series.observations[-1].observation_date - series.observations[0].observation_date
    ).days
    annualized_return = (
        (1.0 + total_return) ** (365.0 / elapsed_days) - 1.0
        if total_return is not None and elapsed_days > 0
        else None
    )
    values = {
        "annualized_volatility": annualized_volatility(returns, settings.periods_per_year),
        "maximum_drawdown": maximum_drawdown(returns),
        "downside_deviation": downside_deviation(
            returns, settings.target_return_per_period, settings.periods_per_year
        ),
        "sharpe_ratio": sharpe_ratio(
            returns, settings.risk_free_return_per_period, settings.periods_per_year
        ),
        "sortino_ratio": sortino_ratio(
            returns, settings.target_return_per_period, settings.periods_per_year
        ),
        "historical_var": historical_var(returns, settings.var_confidence_level),
        "historical_cvar": historical_cvar(returns, settings.var_confidence_level),
    }
    unavailable = tuple(name for name, value in values.items() if value is None)
    return ForwardMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=values["annualized_volatility"],
        maximum_drawdown=values["maximum_drawdown"],
        downside_deviation=values["downside_deviation"],
        sharpe_ratio=values["sharpe_ratio"],
        sortino_ratio=values["sortino_ratio"],
        historical_var=values["historical_var"],
        historical_cvar=values["historical_cvar"],
        return_observation_count=len(returns),
        unavailable_metrics=unavailable,
    )


class WalkForwardBacktester:
    """Evaluate fixed historical dates without allowing future ranking inputs."""

    def __init__(
        self,
        history: HistoricalPortfolioRepository,
        rules_path: Path,
        settings: BacktestSettings | None = None,
        eligibility_gate: BacktestEligibilityGate | None = None,
    ) -> None:
        self.history = history
        self.advisor = CapitalPreservationAdvisor(history.model_repository, rules_path)
        self.settings = settings or BacktestSettings()
        # The default is intentionally strict and offline.  Tests and controlled
        # integrations can inject an equally explicit evidence gate.
        self.eligibility_gate = eligibility_gate or StrictCoverageEligibilityGate.from_default_artifacts()

    def run(
        self,
        *,
        horizon_days: int,
        evaluation_dates: Iterable[date] | None = None,
        allow_proposed_rules: bool = False,
        diagnostics_for_rejected: bool = False,
    ) -> BacktestResult:
        """Run deterministic point-in-time rankings over requested source dates."""
        if horizon_days not in SUPPORTED_HORIZON_DAYS:
            supported = ", ".join(str(value) for value in sorted(SUPPORTED_HORIZON_DAYS))
            raise ValueError(f"horizon_days must be one of: {supported}")
        known_dates = self.history.observation_dates()
        dates = known_dates if evaluation_dates is None else tuple(evaluation_dates)
        if not dates:
            return BacktestResult(horizon_days, (), self._aggregate(()))
        if any(item not in known_dates for item in dates):
            raise ValueError("evaluation_dates must be available model-portfolio observation dates")
        ordered_dates = tuple(sorted(set(dates)))
        periods = tuple(
            self._evaluate_period(
                date_value, horizon_days, allow_proposed_rules, diagnostics_for_rejected
            )
            for date_value in ordered_dates
        )
        return BacktestResult(horizon_days, periods, self._aggregate(periods))

    def _evaluate_period(
        self,
        evaluation_date: date,
        horizon_days: int,
        allow_proposed_rules: bool,
        diagnostics_for_rejected: bool,
    ) -> BacktestPeriodResult:
        window = self.history.forward_window(evaluation_date, horizon_days)
        advisor_result = self.advisor.evaluate(
            observation_date=evaluation_date,
            allow_proposed_rules=allow_proposed_rules,
        )
        selected = advisor_result.selected_portfolio
        if selected is None:
            reason = "No selection was produced by the point-in-time ranking policy"
            return BacktestPeriodResult(
                evaluation_date=evaluation_date,
                horizon_days=horizon_days,
                candidate_count=len(advisor_result.calculated_metrics),
                selected_portfolio=None,
                selected_rank=None,
                selected_score=None,
                rule_set_version=advisor_result.rule_set_version,
                proposed_rules_explicitly_enabled=advisor_result.proposed_rules_explicitly_enabled,
                realized_forward_metrics=None,
                baseline_results=(),
                warnings=advisor_result.warnings,
                incomplete_period_reason=reason,
            )
        eligible = tuple(item for item in advisor_result.ranking if item.eligible)
        eligibility = self.eligibility_gate.evaluate(
            history=self.history,
            portfolio_name=selected.metrics.portfolio_name,
            window=window,
        )
        if not eligibility.eligible:
            rejected_result_type: BacktestResultType = (
                "DIAGNOSTICS_ONLY" if diagnostics_for_rejected else "BACKTEST_REJECTED"
            )
            return BacktestPeriodResult(
                evaluation_date=evaluation_date,
                horizon_days=horizon_days,
                candidate_count=len(advisor_result.calculated_metrics),
                # The selected portfolio remains available only inside the
                # diagnostics payload.  A rejected window cannot expose a
                # performance-ranking or winner-selection result.
                selected_portfolio=None,
                selected_rank=None,
                selected_score=None,
                rule_set_version=advisor_result.rule_set_version,
                proposed_rules_explicitly_enabled=advisor_result.proposed_rules_explicitly_enabled,
                realized_forward_metrics=None,
                baseline_results=(),
                warnings=advisor_result.warnings,
                incomplete_period_reason=self._eligibility_reason(eligibility),
                result_type=rejected_result_type,
                eligibility=eligibility,
                diagnostics=(
                    self._diagnostics(selected.metrics.portfolio_name, window, eligibility)
                    if diagnostics_for_rejected
                    else None
                ),
            )
        selected_outcome = self._portfolio_outcome(
            selected.metrics.portfolio_name, window, eligibility
        )
        baselines = self._baselines(eligible, window)
        return BacktestPeriodResult(
            evaluation_date=evaluation_date,
            horizon_days=horizon_days,
            candidate_count=len(advisor_result.calculated_metrics),
            selected_portfolio=selected.metrics.portfolio_name,
            selected_rank=selected.rank,
            selected_score=selected.total_score,
            rule_set_version=advisor_result.rule_set_version,
            proposed_rules_explicitly_enabled=advisor_result.proposed_rules_explicitly_enabled,
            realized_forward_metrics=selected_outcome.forward_metrics,
            baseline_results=baselines,
            warnings=advisor_result.warnings,
            incomplete_period_reason=selected_outcome.incomplete_reason,
            eligibility=eligibility,
        )

    def _portfolio_outcome(
        self,
        portfolio_name: str,
        window: ForwardWindow,
        eligibility: BacktestEligibility | None = None,
    ) -> BaselineResult:
        decision = eligibility or self.eligibility_gate.evaluate(
            history=self.history, portfolio_name=portfolio_name, window=window
        )
        if not decision.eligible:
            return BaselineResult(
                strategy="selected_portfolio",
                portfolio_names=(portfolio_name,),
                forward_metrics=None,
                incomplete_reason=self._eligibility_reason(decision),
            )
        series = self.history.nav_series(portfolio_name, window)
        if series is None:
            return BaselineResult(
                strategy="selected_portfolio",
                portfolio_names=(portfolio_name,),
                forward_metrics=None,
                incomplete_reason=self._missing_nav_reason(),
            )
        return BaselineResult(
            strategy="selected_portfolio",
            portfolio_names=(portfolio_name,),
            forward_metrics=self._forward_metrics(series),
        )

    def _baselines(
        self, eligible: tuple[CandidateEvaluation, ...], window: ForwardWindow
    ) -> tuple[BaselineResult, ...]:
        if not eligible:
            return ()
        names = tuple(item.metrics.portfolio_name for item in eligible)
        equal_weight = self._equal_weight_outcome(names, window)
        lowest_volatility = self._lowest_metric_outcome(
            "lowest_volatility", eligible, "annualized_volatility", window, prefer_lower=True
        )
        lowest_drawdown = self._lowest_metric_outcome(
            "lowest_drawdown", eligible, "maximum_drawdown", window, prefer_lower=False
        )
        return (equal_weight, lowest_volatility, lowest_drawdown)

    def _equal_weight_outcome(self, names: tuple[str, ...], window: ForwardWindow) -> BaselineResult:
        decisions = tuple(
            self.eligibility_gate.evaluate(history=self.history, portfolio_name=name, window=window)
            for name in names
        )
        rejected = next((item for item in decisions if not item.eligible), None)
        if rejected is not None:
            return BaselineResult(
                strategy="equal_weight_eligible",
                portfolio_names=names,
                forward_metrics=None,
                incomplete_reason=self._eligibility_reason(rejected),
            )
        series = [self.history.nav_series(name, window) for name in names]
        if any(item is None for item in series):
            return BaselineResult(
                strategy="equal_weight_eligible",
                portfolio_names=names,
                forward_metrics=None,
                incomplete_reason=self._missing_nav_reason(),
            )
        complete_series = tuple(item for item in series if item is not None)
        dates = tuple(item.observation_date for item in complete_series[0].observations)
        if any(tuple(item.observation_date for item in current.observations) != dates for current in complete_series):
            return BaselineResult(
                strategy="equal_weight_eligible",
                portfolio_names=names,
                forward_metrics=None,
                incomplete_reason="Eligible portfolios have unaligned NAV timestamps; no interpolation was used",
            )
        composite = self._equal_weight_series(complete_series)
        return BaselineResult(
            strategy="equal_weight_eligible",
            portfolio_names=names,
            forward_metrics=self._forward_metrics(composite),
        )

    def _lowest_metric_outcome(
        self,
        strategy: str,
        eligible: tuple[CandidateEvaluation, ...],
        metric_name: str,
        window: ForwardWindow,
        *,
        prefer_lower: bool,
    ) -> BaselineResult:
        choices = [
            item
            for item in eligible
            if (metric := getattr(item.metrics, metric_name)).available and metric.value is not None
        ]
        if not choices:
            return BaselineResult(
                strategy=strategy,
                portfolio_names=(),
                forward_metrics=None,
                incomplete_reason=f"No eligible portfolio has {metric_name} available at evaluation date",
            )
        chosen = min(
            choices,
            key=lambda item: (
                (getattr(item.metrics, metric_name).value or 0.0)
                if prefer_lower
                else -(getattr(item.metrics, metric_name).value or 0.0),
                item.metrics.portfolio_name,
            ),
        )
        outcome = self._portfolio_outcome(chosen.metrics.portfolio_name, window)
        return BaselineResult(
            strategy=strategy,
            portfolio_names=outcome.portfolio_names,
            forward_metrics=outcome.forward_metrics,
            incomplete_reason=outcome.incomplete_reason,
        )

    def _forward_metrics(self, series: NavSeries) -> ForwardMetrics:
        return forward_metrics_from_nav_series(series, self.settings)

    @staticmethod
    def _equal_weight_series(series: Sequence[NavSeries]) -> NavSeries:
        reference = series[0]
        values = [1.0]
        for index in range(1, len(reference.observations)):
            period_returns = [
                item.observations[index].net_asset_value / item.observations[index - 1].net_asset_value - 1.0
                for item in series
            ]
            values.append(values[-1] * (1.0 + fmean(period_returns)))
        observations = tuple(
            type(item)(item.observation_date, "equal_weight_eligible", value)
            for item, value in zip(reference.observations, values, strict=True)
        )
        return NavSeries("equal_weight_eligible", observations)

    def _aggregate(self, periods: Sequence[BacktestPeriodResult]) -> BacktestAggregate:
        complete: list[tuple[BacktestPeriodResult, ForwardMetrics]] = []
        for item in periods:
            if item.result_type != "OFFICIAL_BACKTEST":
                continue
            official = require_official_backtest_result(item)
            metrics = official.realized_forward_metrics
            if metrics is not None and metrics.total_return is not None:
                complete.append((official, metrics))
        returns = [metrics.total_return for _, metrics in complete if metrics.total_return is not None]
        drawdowns = [
            metrics.maximum_drawdown
            for _, metrics in complete
            if metrics.maximum_drawdown is not None
        ]
        downside = [
            metrics.downside_deviation
            for _, metrics in complete
            if metrics.downside_deviation is not None
        ]
        hit_rates: dict[str, float | None] = {}
        for strategy in ("equal_weight_eligible", "lowest_volatility", "lowest_drawdown"):
            comparisons: list[tuple[float, float]] = []
            for item, metrics in complete:
                baseline = next(
                    (value for value in item.baseline_results if value.strategy == strategy), None
                )
                if baseline is None or baseline.forward_metrics is None:
                    continue
                baseline_return = baseline.forward_metrics.total_return
                if baseline_return is not None and metrics.total_return is not None:
                    comparisons.append((metrics.total_return, baseline_return))
            hit_rates[strategy] = (
                sum(selected >= baseline for selected, baseline in comparisons) / len(comparisons)
                if comparisons
                else None
            )
        frequency = Counter(
            item.selected_portfolio
            for item in periods
            if item.result_type == "OFFICIAL_BACKTEST" and item.selected_portfolio is not None
        )
        return BacktestAggregate(
            evaluation_period_count=len(periods),
            complete_period_count=len(complete),
            incomplete_period_count=len(periods) - len(complete),
            average_realized_return=fmean(returns) if returns else None,
            median_realized_return=median(returns) if returns else None,
            average_maximum_drawdown=fmean(drawdowns) if drawdowns else None,
            worst_maximum_drawdown=min(drawdowns) if drawdowns else None,
            average_downside_deviation=fmean(downside) if downside else None,
            hit_rate_vs_baselines=hit_rates,
            selection_frequency=dict(sorted(frequency.items())),
        )

    def _missing_nav_reason(self) -> str:
        if self.history.nav_history_available():
            return "NAV history lacks an exact evaluation-date or horizon-end observation"
        return "NAV history is not stored; recomputed forward metrics are unavailable"

    @staticmethod
    def _eligibility_reason(eligibility: BacktestEligibility) -> str:
        if eligibility.status != BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT:
            return f"Official backtest is unavailable: {eligibility.status}"
        isins = ", ".join(item.isin for item in eligibility.blocking_constituents)
        return (
            "Official backtest rejected by STRICT_REJECT_WINDOW because required constituent "
            f"history is unresolved: {isins}"
        )

    @staticmethod
    def _diagnostics(
        portfolio_name: str, window: ForwardWindow, eligibility: BacktestEligibility
    ) -> BacktestDiagnostics:
        """Create a non-performance record; no NAV or metric function is touched."""
        return BacktestDiagnostics(
            result_type="DIAGNOSTICS_ONLY",
            policy_id=eligibility.policy_id,
            portfolio_name=portfolio_name,
            window_start=window.evaluation_date,
            window_end=window.end_date,
            horizon_days=window.horizon_days,
            coverage_status=eligibility.coverage_status,
            constituent_count=len(eligibility.constituent_weights),
            resolvable_weight=eligibility.resolvable_weight,
            unresolved_weight=eligibility.unresolved_weight,
            unresolved_constituents=eligibility.blocking_constituents,
            constituent_weights=eligibility.constituent_weights,
        )
