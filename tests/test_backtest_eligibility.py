from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from portfolio_advisor.backtesting.eligibility import (
    BACKTEST_ELIGIBLE,
    BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT,
    BacktestEligibilityError,
    StrictCoverageEligibilityGate,
)
from portfolio_advisor.backtesting.service import WalkForwardBacktester
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.repository import HistoricalPortfolioRepository

ROOT = Path(__file__).resolve().parents[1]


def _rules(path: Path) -> Path:
    rules = path / "rules.yaml"
    rules.write_text(
        (ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml")
        .read_text(encoding="utf-8")
        .replace('version: "1.0.1"', 'version: "strict-gate-test"', 1)
        .replace("status: proposed", "status: approved", 1)
        .replace("minimum_metric_coverage: 0.70", "minimum_metric_coverage: 1", 1),
        encoding="utf-8",
    )
    return rules


def _database(path: Path, *, unresolved_isin: str | None = None) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        '''CREATE TABLE model_portfolios (
            "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
            "Allocation (%)" REAL, "Currency" TEXT, "Currency Risk" TEXT,
            "1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL,
            "Downside Risk" REAL, "Maximum Drawdown" REAL
        )'''
    )
    second_isin = unresolved_isin or "RESOLVED2"
    for value in ("2025/01/01", "2025/04/01"):
        connection.executemany(
            'INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (value, "Alpha", "Fund", "RESOLVED1", 50.0, "HUF", "Hedged", 0.02, 0.5, 0.01, 0.01, -0.02),
                (value, "Alpha", "Fund", second_isin, 50.0, "HUF", "Hedged", 0.02, 0.5, 0.01, 0.01, -0.02),
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
        ],
    )
    connection.commit()
    connection.close()


def _coverage(
    path: Path,
    *,
    status: str,
    missing: list[str],
    unusable: list[str],
    unresolved_isin: str = "HU0000554795",
) -> None:
    path.write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "observation_date": "2025-01-01",
                        "portfolio_name": "Alpha",
                        "horizon": 90,
                        "required_start": "2025-01-01",
                        "required_end": "2025-04-01",
                        "required_isins": ["RESOLVED1", unresolved_isin],
                        "missing_isins": missing,
                        "unusable_isins": unusable,
                        "status": status,
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _terminal_resolution(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "isin": "HU0000554795",
                "resolution_status": "BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE",
                "research_closed": True,
                "backtest_admission": {
                    "nav_equivalent": False,
                    "backtest_return_series_approved": False,
                    "usable_for_backtest": False,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _history(database: Path) -> HistoricalPortfolioRepository:
    return HistoricalPortfolioRepository(ModelPortfolioRepository(database))


def test_strict_gate_rejects_terminal_constituent_and_emits_diagnostics(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    coverage = tmp_path / "coverage.json"
    resolution = tmp_path / "resolution.json"
    _database(database, unresolved_isin="HU0000554795")
    _coverage(coverage, status="UNUSABLE_SOURCE", missing=[], unusable=["HU0000554795"])
    _terminal_resolution(resolution)
    gate = StrictCoverageEligibilityGate.from_artifacts(coverage, (resolution,))

    backtester = WalkForwardBacktester(_history(database), _rules(tmp_path), eligibility_gate=gate)
    period = backtester.run(
        horizon_days=90,
        evaluation_dates=[date(2025, 1, 1)],
        diagnostics_for_rejected=True,
    ).periods[0]

    assert period.result_type == "DIAGNOSTICS_ONLY"
    assert period.realized_forward_metrics is None
    assert period.baseline_results == ()
    assert period.eligibility is not None
    assert period.eligibility.status == BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT
    assert period.eligibility.unresolved_weight == 50.0
    assert period.eligibility.blocking_constituents[0].category == "TERMINAL_UNRESOLVABLE"
    assert period.diagnostics is not None
    assert period.diagnostics.official_return_available is False
    assert period.diagnostics.official_risk_metrics_available is False
    assert period.diagnostics.ranking_eligible is False
    assert period.diagnostics.selection_eligible is False
    assert {item.isin for item in period.diagnostics.constituent_weights} == {
        "RESOLVED1",
        "HU0000554795",
    }


def test_strict_gate_allows_fully_resolved_window(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    coverage = tmp_path / "coverage.json"
    _database(database, unresolved_isin="HU0000554795")
    _coverage(coverage, status="COMPLETE", missing=[], unusable=[])
    gate = StrictCoverageEligibilityGate.from_artifacts(coverage)
    decision = gate.evaluate(
        history=_history(database),
        portfolio_name="Alpha",
        window=_history(database).forward_window(date(2025, 1, 1), 90),
    )

    assert decision.eligible is True
    assert decision.status == BACKTEST_ELIGIBLE
    assert decision.resolvable_weight == 100.0
    assert decision.unresolved_weight == 0.0


def test_reconciliation_and_temporary_gaps_also_block(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    coverage = tmp_path / "coverage.json"
    _database(database, unresolved_isin="HU0000554795")
    history = _history(database)
    window = history.forward_window(date(2025, 1, 1), 90)

    _coverage(coverage, status="RECONCILIATION_REQUIRED", missing=[], unusable=["HU0000554795"])
    reconciliation = StrictCoverageEligibilityGate.from_artifacts(coverage).evaluate(
        history=history, portfolio_name="Alpha", window=window
    )
    assert reconciliation.eligible is False
    assert reconciliation.blocking_constituents[0].category == "RECONCILIATION_REQUIRED"

    _coverage(coverage, status="MISSING_END", missing=["HU0000554795"], unusable=[])
    temporary = StrictCoverageEligibilityGate.from_artifacts(coverage).evaluate(
        history=history, portfolio_name="Alpha", window=window
    )
    assert temporary.eligible is False
    assert temporary.blocking_constituents[0].category == "TEMPORARY_DATA_GAP"


def test_at0000605324_reconciliation_requirement_blocks_without_source_shortcut(
    tmp_path: Path,
) -> None:
    database = tmp_path / "history.sqlite"
    coverage = tmp_path / "coverage.json"
    _database(database, unresolved_isin="AT0000605324")
    _coverage(
        coverage,
        status="RECONCILIATION_REQUIRED",
        missing=[],
        unusable=["AT0000605324"],
        unresolved_isin="AT0000605324",
    )
    decision = StrictCoverageEligibilityGate.from_artifacts(coverage).evaluate(
        history=_history(database),
        portfolio_name="Alpha",
        window=_history(database).forward_window(date(2025, 1, 1), 90),
    )

    assert decision.eligible is False
    assert decision.blocking_constituents[0].isin == "AT0000605324"
    assert decision.blocking_constituents[0].category == "RECONCILIATION_REQUIRED"


def test_missing_or_inconsistent_coverage_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    coverage = tmp_path / "coverage.json"
    _database(database, unresolved_isin="HU0000554795")
    _coverage(coverage, status="COMPLETE", missing=[], unusable=["HU0000554795"])
    gate = StrictCoverageEligibilityGate.from_artifacts(coverage)

    with pytest.raises(BacktestEligibilityError, match="complete coverage"):
        gate.evaluate(
            history=_history(database),
            portfolio_name="Alpha",
            window=_history(database).forward_window(date(2025, 1, 1), 90),
        )


def test_default_strict_gate_reconciles_to_retained_policy_simulation() -> None:
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
    gate = StrictCoverageEligibilityGate.from_default_artifacts()
    decisions = [
        gate.evaluate(
            history=history,
            portfolio_name=row["portfolio_name"],
            window=history.forward_window(date.fromisoformat(row["observation_date"]), row["horizon"]),
        )
        for row in coverage["windows"]
    ]
    strict = policy["policy_simulation_summaries"]["STRICT_REJECT_WINDOW"]

    assert len(decisions) == policy["current_dataset"]["total_actual_windows"]
    assert sum(item.eligible for item in decisions) == strict["eligible_windows"]
    assert sum(not item.eligible for item in decisions) == strict["rejected_windows"]
