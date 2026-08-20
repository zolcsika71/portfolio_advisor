from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import cast

from portfolio_advisor.history.official_portfolio_performance_research import (
    DIRECT_SOURCE_VALIDATED,
    DirectPortfolioSourceCandidate,
    build_research_artifact,
    build_search_targets,
    classify_candidate,
)


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        '''CREATE TABLE model_portfolios (
            "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
            "Allocation (%)" REAL, "Currency" TEXT, "Currency Risk" TEXT,
            "1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL,
            "Downside Risk" REAL, "Maximum Drawdown" REAL
        )'''
    )
    connection.executemany(
        'INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            ("2025/01/02", "PB Alpha EUR", "Fund", "LU0594300682", 100.0, "EUR", "", 0.0, 0.0, 0.0, 0.0, 0.0),
            ("2025/02/03", "PB Alpha EUR", "Fund", "LU0594300682", 100.0, "EUR", "", 0.0, 0.0, 0.0, 0.0, 0.0),
        ],
    )
    connection.commit()
    connection.close()


def _candidate(**changes: object) -> DirectPortfolioSourceCandidate:
    values: dict[str, object] = {
        "portfolio_id": "PB Alpha EUR", "portfolio_name": "PB Alpha EUR",
        "source_authority": "Erste Bank Hungary", "source_url_or_reference": "https://www.erstebank.hu/example",
        "authority_domain": "erstebank.hu", "identity_exact": True,
        "historical_period_start": date(2024, 1, 1), "historical_period_end": date(2025, 1, 1),
        "value_type": "PORTFOLIO_NAV", "currency": "EUR", "reproducible_retained_file": True,
        "local_path": "data/portfolio_performance/raw/erste/export.csv", "sha256": "a" * 64,
        "portfolio_level": True,
    }
    values.update(changes)
    return DirectPortfolioSourceCandidate(**values)  # type: ignore[arg-type]


def test_exact_targets_preserve_workbook_provenance_and_order(tmp_path: Path) -> None:
    database = tmp_path / "model.sqlite"
    workbooks = tmp_path / "processed"
    workbooks.mkdir()
    _database(database)
    (workbooks / "PB_Modell_Portfoliok_20250102.xls").touch()
    (workbooks / "PB_Modell_Portfoliok_20250203.xls").touch()

    targets = build_search_targets(database_path=database, processed_workbook_dir=workbooks)

    assert [item.portfolio_id for item in targets] == ["PB Alpha EUR"]
    assert targets[0].source_workbook_names == (
        "PB_Modell_Portfoliok_20250102.xls", "PB_Modell_Portfoliok_20250203.xls"
    )


def test_candidate_admission_requires_exact_authoritative_portfolio_semantics() -> None:
    assert classify_candidate(_candidate()) == DIRECT_SOURCE_VALIDATED
    assert classify_candidate(_candidate(identity_exact=False)) == "AUTHORITATIVE_SOURCE_FOUND_IDENTITY_UNRESOLVED"
    assert classify_candidate(_candidate(authority_domain="example.com")) == "SOURCE_UNAVAILABLE"
    assert classify_candidate(_candidate(value_type=None)) == "AUTHORITATIVE_SOURCE_FOUND_SEMANTICS_INSUFFICIENT"
    assert classify_candidate(_candidate(portfolio_level=False)) == "AUTHORITATIVE_SOURCE_FOUND_IDENTITY_UNRESOLVED"


def test_negative_research_is_deterministic_and_does_not_claim_portfolio_labels(tmp_path: Path) -> None:
    database = tmp_path / "model.sqlite"
    workbooks = tmp_path / "processed"
    workbooks.mkdir()
    _database(database)
    (workbooks / "PB_Modell_Portfoliok_20250102.xls").touch()
    (workbooks / "PB_Modell_Portfoliok_20250203.xls").touch()
    targets = build_search_targets(database_path=database, processed_workbook_dir=workbooks)

    first = build_research_artifact(
        targets=targets, source_families_searched=("erstebank.hu",), query_families=("PB Alpha EUR",), candidates=(), local_direct_source_found=False
    )
    second = build_research_artifact(
        targets=targets, source_families_searched=("erstebank.hu",), query_families=("PB Alpha EUR",), candidates=(), local_direct_source_found=False
    )

    assert first == second
    assert first["search_status"] == "DIRECT_OFFICIAL_PORTFOLIO_PERFORMANCE_SOURCE_NOT_FOUND"
    freeze_interaction = cast(dict[str, object], first["freeze_interaction"])
    assert freeze_interaction["synthetic_reconstruction_activated"] is False
