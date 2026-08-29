"""Synthetic tests for the isolated one-time TBSZ current-standings database."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from portfolio_advisor.tbsz.current_standings import (
    CURRENT_STANDINGS_SCHEMA_VERSION,
    EXPECTED_SOURCE_FILENAMES,
    CurrentStandingsError,
    OutputAlreadyExistsError,
    create_current_standings_database,
)


def _position(name: str, currency: str, amount: str, huf_value: str | None = None) -> dict[str, str]:
    value: dict[str, str] = {
        "provider_name": name,
        "market_currency": currency,
        "market_value": amount,
    }
    if huf_value is not None:
        value["reporting_currency"] = "HUF"
        value["reporting_value"] = huf_value
    return value


def _document(filename: str, account: str, view_type: str, *, positions: list[dict[str, str]] | None = None, cash: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "source_filename": filename,
        "account_label": account,
        "view_type": view_type,
        "manual_confirmed": True,
        "positions": positions or [],
        "cash": cash or [],
    }


def _write_source_set(tmp_path: Path) -> tuple[Path, Path, Path]:
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    for filename in EXPECTED_SOURCE_FILENAMES:
        (source_directory / filename).write_bytes(f"synthetic {filename}".encode())
    positions_2024 = [
        _position("Asset 01", "HUF", "3200545"),
        _position("Asset 02", "EUR", "5581.58", "2037443"),
        _position("Asset 03", "HUF", "1998028"),
        _position("Asset 04", "HUF", "1883735"),
        _position("Asset 05", "USD", "5073.62", "1596870"),
        _position("Asset 06", "USD", "3929.69", "1236832"),
        _position("Asset 07", "HUF", "1025000"),
        _position("Asset 08", "HUF", "599658"),
        _position("Asset 09", "USD", "1359.63", "427930"),
        _position("Asset 10", "EUR", "1171.42", "427605"),
        _position("Asset 11", "EUR", "983.84", "359132"),
    ]
    positions_2019 = [
        _position("Asset 12", "HUF", "3359405"),
        _position("Asset 04", "HUF", "1885938"),
    ]
    positions_2025 = [
        _position("Asset 04", "HUF", "635315"),
        _position("Asset 13", "HUF", "137674"),
        _position("Asset 01", "HUF", "109127"),
        _position("Asset 11", "EUR", "80.20", "29276"),
    ]
    cash_2024 = [
        {"currency": "HUF", "balance": "2180991"},
        {"currency": "USD", "balance": "7219.58"},
        {"currency": "EUR", "balance": "2679.13"},
    ]
    cash_2025 = [
        {"currency": "HUF", "balance": "222538"},
        {"currency": "USD", "balance": "491"},
        {"currency": "EUR", "balance": "0.15"},
    ]
    manifest = {
        "schema_version": 1,
        "documents": [
            _document(EXPECTED_SOURCE_FILENAMES[0], "TBSZ 2024", "POSITIONS", positions=positions_2024),
            _document(EXPECTED_SOURCE_FILENAMES[1], "TBSZ 2024", "CASH", cash=cash_2024),
            _document(EXPECTED_SOURCE_FILENAMES[2], "TBSZ 2024(2019)", "POSITIONS", positions=positions_2019),
            _document(EXPECTED_SOURCE_FILENAMES[3], "TBSZ 2024(2019)", "CASH"),
            _document(EXPECTED_SOURCE_FILENAMES[4], "TBSZ 2025", "POSITIONS", positions=positions_2025),
            _document(EXPECTED_SOURCE_FILENAMES[5], "TBSZ 2025", "CASH", cash=cash_2025),
            _document(EXPECTED_SOURCE_FILENAMES[6], "TBSZ 2025", "POSITIONS", positions=positions_2025),
            _document(EXPECTED_SOURCE_FILENAMES[7], "TBSZ 2025", "CASH", cash=cash_2025),
        ],
    }
    confirmations_path = tmp_path / "manual_confirmations.json"
    confirmations_path.write_text(json.dumps(manifest), encoding="utf-8")
    return source_directory, confirmations_path, tmp_path / "tbsz_current_portfolio.sqlite"


def _create(tmp_path: Path):  # type: ignore[no-untyped-def]
    source_directory, confirmations_path, output_path = _write_source_set(tmp_path)
    result = create_current_standings_database(
        source_directory=source_directory,
        confirmations_path=confirmations_path,
        output_path=output_path,
    )
    return source_directory, confirmations_path, output_path, result


def test_creates_expected_isolated_current_standings_database(tmp_path: Path) -> None:
    _, _, output_path, result = _create(tmp_path)

    assert result.account_count == 3
    assert result.source_document_count == 8
    assert result.instrument_count == 13
    assert result.position_count == 17
    assert result.cash_count == 6
    assert result.backup_path is None
    with sqlite3.connect(output_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (CURRENT_STANDINGS_SCHEMA_VERSION,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT account_label FROM accounts ORDER BY account_id").fetchall() == [
            ("TBSZ 2024",),
            ("TBSZ 2024 (2019)",),
            ("TBSZ 2025",),
        ]
        assert connection.execute("SELECT count(*) FROM current_holdings").fetchone() == (23,)
        assert connection.execute(
            "SELECT count(*) FROM source_documents WHERE length(source_sha256) != 64"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT record_type, count(*) FROM current_holdings GROUP BY record_type ORDER BY record_type"
        ).fetchall() == [("ASSET", 17), ("CASH", 6)]
        assert connection.execute(
            "SELECT DISTINCT currency FROM current_holdings WHERE record_type = 'ASSET' ORDER BY currency"
        ).fetchall() == [("EUR",), ("HUF",), ("USD",)]
        assert connection.execute(
            "SELECT DISTINCT currency FROM current_holdings WHERE record_type = 'CASH' ORDER BY currency"
        ).fetchall() == [("EUR",), ("HUF",), ("USD",)]
        assert connection.execute(
            "SELECT count(*) FROM current_holdings WHERE tbsz_name = 'TBSZ 2024 (2019)' AND record_type = 'CASH'"
        ).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM instruments WHERE isin IS NOT NULL").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM position_snapshots WHERE quantity IS NOT NULL OR unit_price IS NOT NULL OR roi_percent IS NOT NULL OR source_date IS NOT NULL"
        ).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM cash_snapshots WHERE source_date IS NOT NULL").fetchone() == (0,)
        table_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not {"recommendations", "target_allocations", "transactions"} & table_names


def test_missing_required_pdf_and_unapproved_account_fail_closed(tmp_path: Path) -> None:
    source_directory, confirmations_path, output_path = _write_source_set(tmp_path)
    (source_directory / EXPECTED_SOURCE_FILENAMES[0]).unlink()
    with pytest.raises(CurrentStandingsError, match="REQUIRED_SOURCE_PDF_MISSING"):
        create_current_standings_database(
            source_directory=source_directory,
            confirmations_path=confirmations_path,
            output_path=output_path,
        )
    assert not output_path.exists()

    (source_directory / EXPECTED_SOURCE_FILENAMES[0]).write_bytes(b"synthetic replacement")
    manifest = json.loads(confirmations_path.read_text(encoding="utf-8"))
    manifest["documents"][0]["account_label"] = "Normal"
    confirmations_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CurrentStandingsError, match="Normal/Norm"):
        create_current_standings_database(
            source_directory=source_directory,
            confirmations_path=confirmations_path,
            output_path=output_path,
        )
    assert not output_path.exists()


def test_existing_output_is_refused_and_force_backs_up_before_replacement(tmp_path: Path) -> None:
    source_directory, confirmations_path, output_path, _ = _create(tmp_path)
    original_bytes = output_path.read_bytes()

    with pytest.raises(OutputAlreadyExistsError, match="OUTPUT_ALREADY_EXISTS"):
        create_current_standings_database(
            source_directory=source_directory,
            confirmations_path=confirmations_path,
            output_path=output_path,
        )
    assert output_path.read_bytes() == original_bytes

    result = create_current_standings_database(
        source_directory=source_directory,
        confirmations_path=confirmations_path,
        output_path=output_path,
        force=True,
    )
    assert result.backup_path is not None
    assert result.backup_path.parent == output_path.parent / "backups"
    assert result.backup_path.is_file()
    with sqlite3.connect(result.backup_path) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("PRAGMA foreign_key_check").fetchall() == []
