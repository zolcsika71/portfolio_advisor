"""Deterministic synthetic dual-sheet workbooks for Phase 3B.1 tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from portfolio_advisor.database.model_portfolio_phase1 import source_dataset_fingerprint
from portfolio_advisor.database.model_portfolio_phase3b import (
    MODEL_HEADERS,
    MODEL_ROLE,
    MODEL_SHEET_NAME,
    SHORTLIST_HEADERS,
    SHORTLIST_ROLE,
    SHORTLIST_SHEET_NAME,
    WORKBOOK_FORMAT,
    SyntheticWorkbookAdmissionRequest,
    shortlist_dataset_fingerprint,
)


def write_synthetic_workbook(
    root: Path,
    *,
    snapshot_date: str,
    suffix: str = "",
    changed_product: bool = False,
    invalid_shortlist: bool = False,
) -> Path:
    """Write a portable synthetic envelope; it is deliberately not an Excel file."""
    product = (
        "Synthetic duplicate changed" if changed_product else "Synthetic duplicate"
    )
    model_rows = [
        _row(
            2,
            MODEL_HEADERS,
            {
                "Portfolio Name": "PB Synthetic Balanced",
                "Product": product,
                "ISIN": "IE00B7KFL990",
                "Allocation (%)": 50.0,
                "Asset Class": "Bond",
                "Sub-Asset Class": "Global",
                "Currency": "USD",
                "Currency Risk": "Unhedged",
                "Sustainability": "1: ESG-Minimum Standard",
                "YTD": 0.0,
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
            },
        ),
        _row(
            3,
            MODEL_HEADERS,
            {
                "Portfolio Name": "PB Synthetic Balanced",
                "Product": product,
                "ISIN": "IE00B7KFL990",
                "Allocation (%)": 50.0,
                "Asset Class": "Bond",
                "Sub-Asset Class": "Global",
                "Currency": "USD",
                "Currency Risk": "Unhedged",
                "Sustainability": "1: ESG-Minimum Standard",
                "YTD": 0.0,
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
            },
        ),
    ]
    shortlist_rows = [
        _row(
            2,
            SHORTLIST_HEADERS,
            {
                "Product": "Synthetic shortlist one",
                "ISIN": "IE00B7KFL990",
                "Asset Class": "Bond",
                "Sub-Asset Class": "Global",
                "Product Type": "Fund",
                "Currency": "USD",
                "Currency Risk": "Unhedged",
                "Sustainability": "1: ESG-Minimum Standard",
                "YTD": 0.0,
                "1yr": 0.08,
                "3yr": 0.12,
                "5yr": None,
                "1Y Sharpe": 0.7,
                "3Y Sharpe": 0.9,
                "5Y Sharpe": None,
                "1Y Vol.": 0.05,
                "3Y Vol.": 0.07,
                "Down. risk": 0.04,
                "Info. ratio": -0.2,
                "Max. drawd.": -0.1,
            },
        ),
        _row(
            3,
            SHORTLIST_HEADERS,
            {
                "Product": "Synthetic shortlist two",
                "ISIN": "IE00B84J9L26" if not invalid_shortlist else "INVALID",
                "Asset Class": "Equity",
                "Sub-Asset Class": "Global",
                "Product Type": "Fund",
                "Currency": "EUR",
                "Currency Risk": "Hedged",
                "Sustainability": None,
                "YTD": 0.0,
                "1yr": 0.05,
                "3yr": 0.1,
                "5yr": 0.2,
                "1Y Sharpe": 0.5,
                "3Y Sharpe": 0.8,
                "5Y Sharpe": 1.0,
                "1Y Vol.": 0.08,
                "3Y Vol.": 0.1,
                "Down. risk": 0.06,
                "Info. ratio": 0.1,
                "Max. drawd.": -0.15,
            },
        ),
    ]
    payload = {
        "format": WORKBOOK_FORMAT,
        "snapshot_date": snapshot_date,
        "sheets": [
            {
                "role": MODEL_ROLE,
                "name": MODEL_SHEET_NAME,
                "headers": list(MODEL_HEADERS),
                "rows": model_rows,
            },
            {
                "role": SHORTLIST_ROLE,
                "name": SHORTLIST_SHEET_NAME,
                "headers": list(SHORTLIST_HEADERS),
                "rows": shortlist_rows,
            },
        ],
    }
    path = root / f"synthetic-workbook-{snapshot_date}{suffix}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def request_for(
    database: Path,
    workbook: Path,
    *,
    admission_id: str,
    authority_id: str = "SYNTHETIC_PHASE3B1_AUTHORITY",
    authorization_reference: str = "user-approved-synthetic-phase3b1",
) -> SyntheticWorkbookAdmissionRequest:
    """Bind a request to the current temporary database state and predecessor."""
    with sqlite3.connect(database) as connection:
        predecessor = connection.execute(
            "SELECT admission_id FROM model_workbook_writer_admission ORDER BY admission_sequence DESC LIMIT 1"
        ).fetchone()
        model_before = source_dataset_fingerprint(connection)
        shortlist_before = shortlist_dataset_fingerprint(connection)
    return SyntheticWorkbookAdmissionRequest(
        admission_id=admission_id,
        authority_id=authority_id,
        authorization_reference=authorization_reference,
        workbook_path=workbook,
        retention_root=database.parent / "phase3b-retained-evidence",
        expected_workbook_sha256=_sha256(workbook),
        expected_predecessor_admission_id=None
        if predecessor is None
        else str(predecessor[0]),
        expected_model_fingerprint_before=model_before,
        expected_shortlist_fingerprint_before=shortlist_before,
    )


def _row(
    source_row: int, headers: tuple[str, ...], values: dict[str, object]
) -> dict[str, object]:
    assert tuple(values) == headers
    raw_values = dict(values)
    if raw_values.get("YTD") == 0.0:
        raw_values["YTD"] = "0"
    return {
        "source_row": source_row,
        "raw_values": raw_values,
        "values": values,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
