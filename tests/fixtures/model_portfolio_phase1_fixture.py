"""Deterministic synthetic evidence for consolidation Phase 1 tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

from portfolio_advisor.database.migrations.model_portfolio_dry_run import (
    ReconciledSourceRow,
    _populate,
)
from portfolio_advisor.database.model_portfolio_phase1 import (
    PARSER_VERSION,
    ModelOccurrenceBinding,
    SourceAuthorityEpoch,
    WorkbookAdmissionRequest,
    normalize_parsed_fields,
    source_dataset_fingerprint,
)
from portfolio_advisor.database.repository import HoldingObservation
from portfolio_advisor.database.schema.v3 import connect, initialize_schema

FIXTURE_DATE = date(2024, 9, 17)
SOURCE_HASH = "a" * 64
SHEET_NAME = "modell portfóliók"
PORTFOLIOS_AND_ROWS = (
    ("PB Konzervatív USD", 33),
    ("PB Konzervatív USD", 35),
    ("PB Kiegyensúlyozott USD", 87),
    ("PB Kiegyensúlyozott USD", 91),
)


def create_phase1_databases(
    root: Path,
) -> tuple[Path, Path, WorkbookAdmissionRequest]:
    """Create equivalent flat/v3 stores and one exact normalization request."""
    legacy = root / "legacy.sqlite"
    analytical = root / "analytical.sqlite"
    _create_legacy(legacy)
    rows = tuple(
        _source_row(portfolio, source_row)
        for portfolio, source_row in PORTFOLIOS_AND_ROWS
    )
    with connect(analytical) as connection:
        initialize_schema(connection)
        _populate(connection, rows)
        _insert_unrelated_non_model_occurrence(connection)
        request = _request(connection)
    return legacy, analytical, request


def _create_legacy(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE model_portfolios ("
            '"Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT, '
            '"Allocation (%)" REAL, "Asset Class" TEXT, "Currency" TEXT, '
            '"Currency Risk" TEXT, "1 Year" REAL, "1Y Sharpe Ratio" REAL, '
            '"1Y Volatility" REAL, "Downside Risk" REAL, "Maximum Drawdown" REAL)'
        )
        connection.executemany(
            "INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    FIXTURE_DATE.strftime("%Y/%m/%d"),
                    portfolio,
                    "Synthetic retained duplicate",
                    "IE00B7KFL990",
                    50.0,
                    "Bond",
                    "USD",
                    "Unhedged",
                    0.08,
                    0.7,
                    0.05,
                    0.04,
                    -0.1,
                )
                for portfolio, _ in PORTFOLIOS_AND_ROWS
            ],
        )


def _source_row(portfolio: str, source_row: int) -> ReconciledSourceRow:
    holding = HoldingObservation(
        portfolio_name=portfolio,
        product="Synthetic retained duplicate",
        isin="IE00B7KFL990",
        allocation=50.0,
        currency="USD",
        currency_risk="Unhedged",
        return_1y=0.08,
        sharpe_ratio_1y=0.7,
        volatility_1y=0.05,
        downside_risk=0.04,
        maximum_drawdown=-0.1,
        asset_class="Bond",
    )
    source_values = {
        "Portfolio Name": portfolio,
        "Product": "Synthetic retained duplicate",
        "ISIN": "IE00B7KFL990",
        "Allocation (%)": 50.0,
        "Asset Class": "Bond",
        "Sub-Asset Class": "Global",
        "Currency": "USD",
        "Currency Risk": "Unhedged",
        "Sustainability": "1: ESG-Minimum Standard",
        "YTD": "0",
        "1 Year": 0.08,
        "3 Years": 0.12,
        "5 Years": None,
        "1Y Sharpe Ratio": 0.7,
        "3Y Sharpe Ratio": 0.9,
        "5Y Sharpe Ratio": None,
        "1Y Volatility": 0.05,
        "3Y Volatility": 0.07,
        "Downside Risk": 0.04,
        "Information Ratio": -0.2,
        "Maximum Drawdown": -0.1,
    }
    return ReconciledSourceRow(
        observation_date=FIXTURE_DATE,
        holding=holding,
        source={
            "file": "synthetic_model_20240917.xls",
            "file_sha256": SOURCE_HASH,
            "sheet": SHEET_NAME,
            "snapshot_date": FIXTURE_DATE.isoformat(),
            "source_row": source_row,
            "isin": holding.isin,
            "product_name": holding.product,
            "normalized_product_name": "synthetic retained duplicate",
            "portfolio_name": portfolio,
            "currency": holding.currency,
            "asset_class": holding.asset_class,
            "sub_asset_class": "Global",
            "allocation": holding.allocation,
            "source_values": source_values,
        },
    )


def _insert_unrelated_non_model_occurrence(connection: sqlite3.Connection) -> None:
    """Add a row that a model-only adapter must never expose."""
    portfolio_id = int(
        connection.execute(
            "INSERT INTO portfolio(portfolio_name,portfolio_type) "
            "VALUES('Synthetic custom portfolio','CUSTOM') RETURNING portfolio_id"
        ).fetchone()[0]
    )
    source_sheet_id, instrument_id = connection.execute(
        """SELECT occurrence.source_sheet_id, occurrence.instrument_id
           FROM portfolio_holding_source_occurrence AS occurrence
           ORDER BY occurrence.portfolio_holding_source_occurrence_id LIMIT 1"""
    ).fetchone()
    snapshot_id = int(
        connection.execute(
            """INSERT INTO portfolio_snapshot(portfolio_id,snapshot_date,source_sheet_id)
               VALUES(?, '2099-01-01', ?) RETURNING portfolio_snapshot_id""",
            (portfolio_id, source_sheet_id),
        ).fetchone()[0]
    )
    payload = json.dumps(
        {"scope": "synthetic non-model adapter exclusion"},
        sort_keys=True,
        separators=(",", ":"),
    )
    connection.execute(
        """INSERT INTO portfolio_holding_source_occurrence(
               portfolio_snapshot_id, instrument_id, source_sheet_id,
               source_row_number, reported_weight, observed_product_name,
               observed_currency_code, observed_currency_risk,
               observed_asset_class, observed_sub_asset_class,
               source_payload_json, source_payload_sha256, source_semantics_status
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            snapshot_id,
            instrument_id,
            source_sheet_id,
            999,
            100.0,
            "Synthetic non-model holding",
            "USD",
            "Unhedged",
            "Custom",
            "Excluded",
            payload,
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "SOURCE_REPORTED",
        ),
    )


def _request(connection: sqlite3.Connection) -> WorkbookAdmissionRequest:
    connection.row_factory = sqlite3.Row
    records = connection.execute(
        """SELECT occurrence.portfolio_holding_source_occurrence_id,
                  occurrence.source_row_number, occurrence.source_payload_sha256,
                  portfolio.portfolio_name
           FROM portfolio_holding_source_occurrence AS occurrence
           JOIN portfolio_snapshot AS snapshot
             ON snapshot.portfolio_snapshot_id=occurrence.portfolio_snapshot_id
           JOIN portfolio AS portfolio ON portfolio.portfolio_id=snapshot.portfolio_id
           WHERE portfolio.portfolio_type='MODEL'
           ORDER BY occurrence.source_row_number"""
    ).fetchall()
    parsed = normalize_parsed_fields(
        {
            "Sustainability": "1: ESG-Minimum Standard",
            "YTD": 0.0,
            "3 Years": 0.12,
            "5 Years": None,
            "3Y Sharpe Ratio": 0.9,
            "5Y Sharpe Ratio": None,
            "3Y Volatility": 0.07,
            "Information Ratio": -0.2,
        }
    )
    items = tuple(
        ModelOccurrenceBinding(
            source_occurrence_id=int(row[0]),
            source_file_sha256=SOURCE_HASH,
            source_sheet_name=SHEET_NAME,
            source_row_number=int(row[1]),
            snapshot_date=FIXTURE_DATE.isoformat(),
            portfolio_name=str(row[3]),
            isin="IE00B7KFL990",
            source_payload_sha256=str(row[2]),
            parsed_fields=parsed,
        )
        for row in records
    )
    dataset_fingerprint = source_dataset_fingerprint(connection)
    return WorkbookAdmissionRequest(
        admission_id="SYNTHETIC_MODEL_WORKBOOK_2024_09_17",
        batch_id="SYNTHETIC_MODEL_BATCH_1",
        authority=SourceAuthorityEpoch(
            epoch_id="SYNTHETIC_PHASE1_EPOCH",
            baseline_source_sha256="b" * 64,
            baseline_dataset_fingerprint=dataset_fingerprint,
            parser_version=PARSER_VERSION,
            ranking_policy_sha256="c" * 64,
            authorization_reference="synthetic-test-authorization",
        ),
        filename="synthetic_model_20240917.xls",
        source_file_sha256=SOURCE_HASH,
        snapshot_date=FIXTURE_DATE.isoformat(),
        source_sheet_name=SHEET_NAME,
        header_signature=hashlib.sha256(
            json.dumps(tuple(range(21))).encode("ascii")
        ).hexdigest(),
        expected_source_dataset_fingerprint=dataset_fingerprint,
        authorization_reference="synthetic-test-admission",
        items=items,
    )
