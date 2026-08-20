from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import cast

from portfolio_advisor.history.portfolio_nav_methodology import (
    ACTIVATION_STATE,
    METHODOLOGY_STATUS,
    build_portfolio_nav_methodology_audit,
    write_portfolio_nav_methodology_audit,
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
            ("2025/01/02", "Alpha", "EUR fund", "LU0594300682", 50.0, "EUR", "", 0.0, 0.0, 0.0, 0.0, 0.0),
            ("2025/01/02", "Alpha", "USD fund", "LU1295422338", 50.0, "USD", "", 0.0, 0.0, 0.0, 0.0, 0.0),
            ("2025/02/03", "Alpha", "EUR fund", "LU0594300682", 60.0, "EUR", "", 0.0, 0.0, 0.0, 0.0, 0.0),
            ("2025/02/03", "Alpha", "USD fund", "LU1295422338", 40.0, "USD", "", 0.0, 0.0, 0.0, 0.0, 0.0),
        ],
    )
    connection.commit()
    connection.close()


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    database = tmp_path / "model.sqlite"
    _database(database)
    strict = tmp_path / "strict.json"
    strict.write_text(json.dumps({"dataset": {"official_eligible_windows": 1, "rejected_windows": 1}}), encoding="utf-8")
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("decision_date", "label_status"))
        writer.writeheader()
        writer.writerows(({"decision_date": "2025-01-02", "label_status": "NO_LOCAL_HISTORY"}, {"decision_date": "2025-02-03", "label_status": "STRICT_BACKTEST_REJECTED"}))
    return database, tmp_path / "store.sqlite", strict, labels


def test_methodology_blocks_mixed_currency_and_undefined_rebalance_semantics(tmp_path: Path) -> None:
    database, store, strict, labels = _inputs(tmp_path)

    payload = build_portfolio_nav_methodology_audit(
        database_path=database,
        nav_store_path=store,
        strict_validation_path=strict,
        label_store_path=labels,
    )

    assert payload["validation_status"] == METHODOLOGY_STATUS
    assert payload["activation_state"] == ACTIVATION_STATE
    blockers = cast(list[str], payload["approval_blockers"])
    assert "FX_METHODOLOGY_REQUIRED" in blockers
    assert "REBALANCE_EFFECTIVE_TIMESTAMP_UNRESOLVED" in blockers
    feasibility = cast(dict[str, object], payload["historical_feasibility"])
    assert feasibility["THEORETICALLY_CONSTRUCTIBLE"] == 0
    assert feasibility["BLOCKED_STRICT_ELIGIBILITY"] == 1
    assert feasibility["BLOCKED_SEMANTICS"] == 1
    assert feasibility["accounting_reconciles"] is True
    alternatives = cast(list[dict[str, object]], payload["evaluated_alternatives"])
    for alternative in alternatives:
        statuses = cast(dict[str, str], alternative["assumption_statuses"])
        assert set(statuses.values()) <= {"UNKNOWN", "NOT_REQUIRED"}


def test_methodology_audit_is_byte_deterministic_and_does_not_modify_database(tmp_path: Path) -> None:
    database, store, strict, labels = _inputs(tmp_path)
    before = database.read_bytes()
    first = build_portfolio_nav_methodology_audit(database_path=database, nav_store_path=store, strict_validation_path=strict, label_store_path=labels)
    second = build_portfolio_nav_methodology_audit(database_path=database, nav_store_path=store, strict_validation_path=strict, label_store_path=labels)
    first_path, second_path = tmp_path / "first.json", tmp_path / "second.json"
    write_portfolio_nav_methodology_audit(first_path, first)
    write_portfolio_nav_methodology_audit(second_path, second)

    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    assert database.read_bytes() == before
