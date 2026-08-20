from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.backtesting import service as backtesting_service
from portfolio_advisor.backtesting.models import (
    BacktestDiagnostics,
    BacktestEligibility,
    BacktestPeriodResult,
    BacktestResultAdmissionError,
    BacktestResultType,
    ConstituentDiagnostic,
    ForwardMetrics,
    UnresolvedConstituent,
    require_official_backtest_result,
)
from portfolio_advisor.backtesting.service import WalkForwardBacktester
from portfolio_advisor.backtesting.validation import validate_strict_pipeline
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.models import ForwardWindow
from portfolio_advisor.history.repository import HistoricalPortfolioRepository

ROOT = Path(__file__).resolve().parents[1]


class _Gate:
    def __init__(self, decisions: dict[str, BacktestEligibility]) -> None:
        self.decisions = decisions

    def evaluate(
        self,
        *,
        history: HistoricalPortfolioRepository,
        portfolio_name: str,
        window: ForwardWindow,
    ) -> BacktestEligibility:
        return self.decisions[portfolio_name]


def _rules(path: Path) -> Path:
    rules = path / "rules.yaml"
    rules.write_text(
        (ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml")
        .read_text(encoding="utf-8")
        .replace('version: "1.0.1"', 'version: "strict-e2e"', 1)
        .replace("status: proposed", "status: approved", 1)
        .replace("minimum_metric_coverage: 0.70", "minimum_metric_coverage: 1", 1),
        encoding="utf-8",
    )
    return rules


def _database(path: Path, *, beta_selected: bool = False) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        '''CREATE TABLE model_portfolios (
            "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
            "Allocation (%)" REAL, "Currency" TEXT, "Currency Risk" TEXT,
            "1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL,
            "Downside Risk" REAL, "Maximum Drawdown" REAL
        )'''
    )
    alpha_volatility, beta_volatility = ((0.08, 0.01) if beta_selected else (0.01, 0.08))
    for snapshot in ("2025/01/01", "2025/04/01"):
        connection.executemany(
            'INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (snapshot, "Alpha", "Fund", "AA0000000001", 100.0, "HUF", "Hedged", 0.02, 0.5, alpha_volatility, 0.01, -0.02),
                (snapshot, "Beta", "Fund", "HU0000554795", 100.0, "HUF", "Hedged", 0.02, 0.5, beta_volatility, 0.01, -0.02),
            ],
        )
    connection.execute(
        'CREATE TABLE portfolio_nav_history ("Date" TEXT, "Portfolio Name" TEXT, "Net Asset Value" REAL)'
    )
    connection.executemany(
        'INSERT INTO portfolio_nav_history VALUES (?, ?, ?)',
        [
            ("2025/01/01", "Alpha", 100.0),
            ("2025/02/01", "Alpha", 105.0),
            ("2025/03/02", "Alpha", 110.0),
            ("2025/04/01", "Alpha", 115.0),
            ("2025/01/01", "Beta", 100.0),
            ("2025/02/01", "Beta", 300.0),
            ("2025/03/02", "Beta", 400.0),
            ("2025/04/01", "Beta", 500.0),
        ],
    )
    connection.commit()
    connection.close()


def _eligibility(*, eligible: bool, isin: str = "HU0000554795", weight: float = 100.0) -> BacktestEligibility:
    return BacktestEligibility(
        eligible=eligible,
        status="BACKTEST_ELIGIBLE" if eligible else "BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT",
        policy_id="STRICT_REJECT_WINDOW",
        coverage_status="COMPLETE" if eligible else "UNUSABLE_SOURCE",
        resolvable_weight=100.0 if eligible else 100.0 - weight,
        unresolved_weight=0.0 if eligible else weight,
        blocking_constituents=(
            ()
            if eligible
            else (UnresolvedConstituent(isin, "TERMINAL_UNRESOLVABLE", weight),)
        ),
        constituent_weights=(ConstituentDiagnostic(isin, weight, "Fund", "HUF"),),
        diagnostics_allowed=not eligible,
    )


def _backtester(tmp_path: Path, *, beta_selected: bool, beta_eligible: bool) -> WalkForwardBacktester:
    database = tmp_path / "history.sqlite"
    _database(database, beta_selected=beta_selected)
    history = HistoricalPortfolioRepository(ModelPortfolioRepository(database))
    return WalkForwardBacktester(
        history,
        _rules(tmp_path),
        eligibility_gate=_Gate({"Alpha": _eligibility(eligible=True), "Beta": _eligibility(eligible=beta_eligible)}),
    )


def test_rejected_window_stops_before_nav_and_every_official_metric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backtester = _backtester(tmp_path, beta_selected=True, beta_eligible=False)

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("official NAV or metric path was invoked for a rejected window")

    monkeypatch.setattr(backtester.history, "nav_series", fail)
    monkeypatch.setattr(backtester, "_forward_metrics", fail)
    for name in (
        "compounded_return",
        "annualized_volatility",
        "maximum_drawdown",
        "sharpe_ratio",
        "historical_var",
        "historical_cvar",
    ):
        monkeypatch.setattr(backtesting_service, name, fail)

    result = backtester.run(
        horizon_days=90,
        evaluation_dates=[date(2025, 1, 1)],
        diagnostics_for_rejected=True,
    )
    period = result.periods[0]
    assert period.result_type == "DIAGNOSTICS_ONLY"
    assert period.realized_forward_metrics is None
    assert period.baseline_results == ()
    assert period.selected_portfolio is None
    assert period.selected_rank is None
    assert period.selected_score is None
    assert period.diagnostics is not None
    assert period.diagnostics.unresolved_constituents[0].isin == "HU0000554795"
    assert result.aggregate.complete_period_count == 0
    assert result.aggregate.selection_frequency == {}


def test_eligible_gate_preserves_underlying_official_metrics_and_selection(tmp_path: Path) -> None:
    backtester = _backtester(tmp_path, beta_selected=False, beta_eligible=False)
    result = backtester.run(horizon_days=90, evaluation_dates=[date(2025, 1, 1)])
    period = result.periods[0]
    window = backtester.history.forward_window(date(2025, 1, 1), 90)
    series = backtester.history.nav_series("Alpha", window)
    assert series is not None

    assert period.result_type == "OFFICIAL_BACKTEST"
    assert period.selected_portfolio == "Alpha"
    assert period.realized_forward_metrics == backtester._forward_metrics(series)
    assert period.realized_forward_metrics is not None
    assert period.realized_forward_metrics.total_return == pytest.approx(0.15)
    assert result.aggregate.selection_frequency == {"Alpha": 1}


def test_non_official_results_are_structurally_barred_from_performance_consumers() -> None:
    diagnostics = BacktestDiagnostics(
        result_type="DIAGNOSTICS_ONLY",
        policy_id="STRICT_REJECT_WINDOW",
        portfolio_name="Beta",
        window_start=date(2025, 1, 1),
        window_end=date(2025, 4, 1),
        horizon_days=90,
        coverage_status="UNUSABLE_SOURCE",
        constituent_count=1,
        resolvable_weight=0.0,
        unresolved_weight=100.0,
        unresolved_constituents=(),
        constituent_weights=(),
    )
    rejected = BacktestPeriodResult(
        evaluation_date=date(2025, 1, 1),
        horizon_days=90,
        candidate_count=1,
        selected_portfolio=None,
        selected_rank=None,
        selected_score=None,
        rule_set_version="test",
        proposed_rules_explicitly_enabled=False,
        realized_forward_metrics=None,
        baseline_results=(),
        result_type="DIAGNOSTICS_ONLY",
        diagnostics=diagnostics,
    )
    with pytest.raises(BacktestResultAdmissionError, match="OFFICIAL_BACKTEST"):
        require_official_backtest_result(rejected)
    with pytest.raises(BacktestResultAdmissionError, match="cannot be admitted"):
        replace(diagnostics, official_return_available=True)
    with pytest.raises(BacktestResultAdmissionError, match="cannot carry official performance"):
        replace(rejected, realized_forward_metrics=_metrics())
    with pytest.raises(BacktestResultAdmissionError, match="unknown backtest result type"):
        replace(rejected, result_type=cast(BacktestResultType, "SPOOFED"))


def test_aggregate_and_selection_frequency_exclude_rejected_and_diagnostics(tmp_path: Path) -> None:
    backtester = _backtester(tmp_path, beta_selected=False, beta_eligible=True)
    official = BacktestPeriodResult(
        evaluation_date=date(2025, 1, 1),
        horizon_days=90,
        candidate_count=1,
        selected_portfolio="Alpha",
        selected_rank=1,
        selected_score=1.0,
        rule_set_version="test",
        proposed_rules_explicitly_enabled=False,
        realized_forward_metrics=_metrics(),
        baseline_results=(),
        eligibility=_eligibility(eligible=True),
    )
    diagnostic = BacktestPeriodResult(
        evaluation_date=date(2025, 2, 1),
        horizon_days=90,
        candidate_count=1,
        selected_portfolio=None,
        selected_rank=None,
        selected_score=None,
        rule_set_version="test",
        proposed_rules_explicitly_enabled=False,
        realized_forward_metrics=None,
        baseline_results=(),
        result_type="DIAGNOSTICS_ONLY",
        diagnostics=BacktestDiagnostics(
            result_type="DIAGNOSTICS_ONLY",
            policy_id="STRICT_REJECT_WINDOW",
            portfolio_name="Fake winner",
            window_start=date(2025, 2, 1),
            window_end=date(2025, 5, 2),
            horizon_days=90,
            coverage_status="UNUSABLE_SOURCE",
            constituent_count=1,
            resolvable_weight=0.0,
            unresolved_weight=100.0,
            unresolved_constituents=(),
            constituent_weights=(),
        ),
    )
    rejected = replace(diagnostic, result_type="BACKTEST_REJECTED", diagnostics=None)
    aggregate = backtester._aggregate((official, diagnostic, rejected))

    assert aggregate.complete_period_count == 1
    assert aggregate.incomplete_period_count == 2
    assert aggregate.average_realized_return == pytest.approx(_metrics().total_return)
    assert aggregate.selection_frequency == {"Alpha": 1}


def test_current_offline_artifacts_reconcile_all_windows_and_terminal_blockers() -> None:
    from portfolio_advisor.backtesting.eligibility import StrictCoverageEligibilityGate

    coverage = json.loads(
        (ROOT / "data/audit/backtest_window_coverage.json").read_text(encoding="utf-8")
    )
    policy = json.loads(
        (ROOT / "data/audit/backtest_missing_data_policy_analysis.json").read_text(
            encoding="utf-8"
        )
    )
    history = HistoricalPortfolioRepository(
        ModelPortfolioRepository(ROOT / "database/model_portfolio.sqlite")
    )
    result = validate_strict_pipeline(
        history=history,
        gate=StrictCoverageEligibilityGate.from_default_artifacts(),
        coverage_payload=coverage,
        policy_payload=policy,
    )

    assert result["validation_status"] == "STRICT_BACKTEST_PIPELINE_VALIDATED"
    assert result["dataset"]["official_eligible_windows"] + result["dataset"]["rejected_windows"] == result["dataset"]["total_windows"]
    assert result["hu0000554795"]["rejected_windows"] == result["hu0000554795"]["affected_windows"]
    assert result["at0000605324"]["reconciliation_required_rejection_associations"] > 0


def test_validation_fails_closed_when_policy_counts_do_not_match() -> None:
    from portfolio_advisor.backtesting.eligibility import StrictCoverageEligibilityGate
    from portfolio_advisor.backtesting.validation import StrictPipelineValidationError

    coverage = json.loads(
        (ROOT / "data/audit/backtest_window_coverage.json").read_text(encoding="utf-8")
    )
    policy = json.loads(
        (ROOT / "data/audit/backtest_missing_data_policy_analysis.json").read_text(
            encoding="utf-8"
        )
    )
    policy["policy_simulation_summaries"]["STRICT_REJECT_WINDOW"]["eligible_windows"] += 1
    history = HistoricalPortfolioRepository(
        ModelPortfolioRepository(ROOT / "database/model_portfolio.sqlite")
    )

    with pytest.raises(StrictPipelineValidationError, match="eligible windows mismatch"):
        validate_strict_pipeline(
            history=history,
            gate=StrictCoverageEligibilityGate.from_default_artifacts(),
            coverage_payload=coverage,
            policy_payload=policy,
        )


def _metrics() -> ForwardMetrics:
    return ForwardMetrics(
        total_return=0.1,
        annualized_return=0.2,
        annualized_volatility=0.3,
        maximum_drawdown=-0.1,
        downside_deviation=0.2,
        sharpe_ratio=1.0,
        sortino_ratio=1.2,
        historical_var=-0.2,
        historical_cvar=-0.3,
        return_observation_count=3,
    )
