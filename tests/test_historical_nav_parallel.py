from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations.historical_nav_parallel import (
    HistoricalNavIntegrationError,
    integrate_historical_nav,
)
from portfolio_advisor.database.schema.v3 import (
    connect,
    initialize_schema,
    insert_instrument,
)


def _target(path: Path) -> None:
    with connect(path) as connection:
        initialize_schema(connection)
        insert_instrument(connection, "US0378331005", "Apple")


def _source(path: Path, isin: str = "US0378331005", value: float = 10.0) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE asset_nav_observations ("ISIN" TEXT,"Date" TEXT,"Value" REAL,"Currency" TEXT,"Value Type" TEXT,"Source Provider" TEXT,"Source Identifier" TEXT,"Provenance Reference" TEXT,"Quality Status" TEXT)')
        connection.execute('INSERT INTO asset_nav_observations VALUES (?,?,?,?,?,?,?,?,?)', (isin, "2024-01-01", value, "USD", "NAV", "TEST", "id", "proof", "VALIDATED"))


def test_nav_dry_run_preserves_target_and_resolves_canonical_isin(tmp_path: Path) -> None:
    target, source = tmp_path / "target.sqlite", tmp_path / "source.sqlite"
    _target(target); _source(source)
    result = integrate_historical_nav(nav_source=source, target=target, apply=False)
    assert result.admitted_count == result.target_count == 1 and result.unresolved_isins == ()
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM instrument_nav_observation").fetchone()[0] == 0


def test_unknown_and_invalid_nav_evidence_fail_closed(tmp_path: Path) -> None:
    target, source = tmp_path / "target.sqlite", tmp_path / "source.sqlite"
    _target(target); _source(source, "US5949181045")
    assert integrate_historical_nav(nav_source=source, target=target, apply=False).unresolved_isins == ("US5949181045",)
    source.unlink(); _source(source, value=-1.0)
    with pytest.raises(HistoricalNavIntegrationError):
        integrate_historical_nav(nav_source=source, target=target, apply=False)
