from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import cast

from portfolio_advisor.history.nav_acquisition import (
    EXCLUDED_ISINS,
    build_historical_nav_acquisition_targets,
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
            ("2025/01/02", "Alpha", "Valid Fund", "LU0594300682", 50.0, "EUR", "", 0.0, 0.0, 0.0, 0.0, 0.0),
            ("2025/01/02", "Alpha", "Terminal", "HU0000554795", 50.0, "HUF", "", 0.0, 0.0, 0.0, 0.0, 0.0),
        ],
    )
    connection.commit()
    connection.close()


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    database = tmp_path / "model.sqlite"
    _database(database)
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("decision_date", "portfolio_name", "horizon_days", "label_status", "label_start_date", "label_end_date"),
        )
        writer.writeheader()
        writer.writerows(
            [
                {"decision_date": "2025-01-02", "portfolio_name": "Alpha", "horizon_days": "90", "label_status": "NO_LOCAL_HISTORY", "label_start_date": "2025-01-02", "label_end_date": "2025-04-02"},
                {"decision_date": "2025-01-02", "portfolio_name": "Alpha", "horizon_days": "180", "label_status": "STRICT_BACKTEST_REJECTED", "label_start_date": "2025-01-02", "label_end_date": "2025-07-01"},
            ]
        )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"windows": [{"observation_date": "2025-01-02", "portfolio_name": "Alpha", "horizon": 90, "status": "COMPLETE", "source_used_by_isin": {"LU0594300682": "erste_market", "HU0000554795": "oekb"}}]}), encoding="utf-8")
    return labels, coverage, database


def test_inventory_includes_only_strict_complete_no_local_history_assets_and_is_deterministic(tmp_path: Path) -> None:
    labels, coverage, database = _inputs(tmp_path)

    first = build_historical_nav_acquisition_targets(label_store_path=labels, coverage_path=coverage, database_path=database)
    second = build_historical_nav_acquisition_targets(label_store_path=labels, coverage_path=coverage, database_path=database)

    assert first == second
    assert first["target_count"] == 1
    assert first["excluded_special_isins"] == sorted(EXCLUDED_ISINS)
    targets = cast(list[dict[str, object]], first["targets"])
    target = targets[0]
    assert target["isin"] == "LU0594300682"
    assert target["required_start_date"] == "2025-01-02"
    assert target["required_end_date"] == "2025-04-02"
    assert target["recoverable_label_count"] == 1
    assert target["preferred_existing_provider"] == "erste_market"
