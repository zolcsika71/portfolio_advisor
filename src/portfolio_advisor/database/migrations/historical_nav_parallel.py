"""Read-only-source historical NAV integration for the parallel schema-v3 store."""

from __future__ import annotations

import hashlib
import math
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from portfolio_advisor.audit.milestone_4 import is_valid_isin
from portfolio_advisor.database.migrations.model_portfolio_dry_run import _sha256
from portfolio_advisor.database.schema.v3 import (
    connect,
    transaction,
    upgrade_schema_v3_nav_extension,
    validate_schema,
)

NAV_INTEGRATION_VERSION = "MILESTONE_8_NAV_V1"


class HistoricalNavIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NavIntegrationResult:
    source_fingerprint: str
    source_count: int
    source_isin_count: int
    admitted_count: int
    unresolved_isins: tuple[str, ...]
    target_count: int
    target_isin_count: int
    dataset_fingerprint: str


def integrate_historical_nav(*, nav_source: Path, target: Path, apply: bool) -> NavIntegrationResult:
    """Copy-on-write integration. ``apply=False`` always uses a temporary target."""
    source_fingerprint = _sha256(nav_source)
    rows = _read_source(nav_source)
    destination_dir = target.parent if apply else Path(tempfile.mkdtemp(prefix="portfolio-advisor-m8-"))
    destination_dir.mkdir(parents=True, exist_ok=True)
    candidate = destination_dir / (f".{target.name}.m8.sqlite" if apply else "nav-dry-run.sqlite")
    shutil.copy2(target, candidate)
    try:
        result = _integrate(rows, source_fingerprint, candidate)
        if apply:
            candidate.replace(target)
        return result
    except BaseException:
        if candidate.exists():
            candidate.unlink()
        raise


def validate_historical_nav(*, nav_source: Path, target: Path) -> NavIntegrationResult:
    """Read-only exact source/target reconciliation."""
    rows = _read_source(nav_source)
    fingerprint = _sha256(nav_source)
    with sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        validate_schema(connection)
        target_rows = connection.execute(
            "SELECT i.isin, observation_date, nav_value, currency_code, value_type, source_provider, source_identifier, provenance_reference, quality_status FROM instrument_nav_observation n JOIN instrument i ON i.instrument_id=n.instrument_id ORDER BY 1,2,6,5,7"
        ).fetchall()
    admitted, unresolved = _admitted(rows, {str(row[0]) for row in target_rows})
    expected = sorted(admitted)
    actual = [tuple(row) for row in target_rows]
    if expected != actual:
        raise HistoricalNavIntegrationError("target NAV observations do not exactly reconcile to admitted source evidence")
    return NavIntegrationResult(fingerprint, len(rows), len({r[0] for r in rows}), len(admitted), tuple(sorted(unresolved)), len(actual), len({r[0] for r in actual}), _fingerprint(actual))


def _read_source(path: Path) -> list[tuple[Any, ...]]:
    if not path.is_file():
        raise HistoricalNavIntegrationError("official NAV source is missing")
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(asset_nav_observations)")}
        required = {"ISIN", "Date", "Value", "Currency", "Value Type", "Source Provider", "Source Identifier", "Provenance Reference", "Quality Status"}
        if columns != required:
            raise HistoricalNavIntegrationError("official NAV source schema is incompatible")
        rows = connection.execute('SELECT "ISIN", "Date", "Value", "Currency", "Value Type", "Source Provider", "Source Identifier", "Provenance Reference", "Quality Status" FROM asset_nav_observations ORDER BY 1,2,6,5').fetchall()
    for row in rows:
        if not is_valid_isin(str(row[0])) or not math.isfinite(float(row[2])) or float(row[2]) <= 0 or len(str(row[3])) != 3 or str(row[4]) != "NAV":
            raise HistoricalNavIntegrationError("invalid NAV identity, value, currency, or semantics")
    return [tuple(row) for row in rows]


def _integrate(rows: list[tuple[Any, ...]], source_fingerprint: str, path: Path) -> NavIntegrationResult:
    with connect(path) as connection:
        upgrade_schema_v3_nav_extension(connection)
        canonical = {str(row[1]): int(row[0]) for row in connection.execute("SELECT instrument_id, isin FROM instrument")}
        admitted, unresolved = _admitted(rows, set(canonical))
        with transaction(connection):
            for row in admitted:
                isin, observed, value, currency, value_type, provider, identifier, provenance, quality = row
                values = (canonical[str(isin)], observed, value, currency, value_type, provider, identifier, provenance, quality, source_fingerprint)
                existing = connection.execute("SELECT nav_value,currency_code,provenance_reference,quality_status,source_fingerprint FROM instrument_nav_observation WHERE instrument_id=? AND observation_date=? AND source_provider=? AND value_type=? AND source_identifier=?", (canonical[str(isin)], observed, provider, value_type, identifier)).fetchone()
                if existing is not None and tuple(existing) != (value, currency, provenance, quality, source_fingerprint):
                    raise HistoricalNavIntegrationError("conflicting NAV replay")
                if existing is None:
                    connection.execute("INSERT INTO instrument_nav_observation (instrument_id,observation_date,nav_value,currency_code,value_type,source_provider,source_identifier,provenance_reference,quality_status,source_fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?)", values)
        target = connection.execute("SELECT count(*), count(DISTINCT instrument_id) FROM instrument_nav_observation").fetchone()
    return NavIntegrationResult(source_fingerprint, len(rows), len({r[0] for r in rows}), len(admitted), tuple(sorted(unresolved)), int(target[0]), int(target[1]), _fingerprint(admitted))


def _admitted(rows: list[tuple[Any, ...]], canonical: set[str]) -> tuple[list[tuple[Any, ...]], set[str]]:
    unresolved = {str(row[0]) for row in rows if str(row[0]) not in canonical}
    return [row for row in rows if str(row[0]) in canonical], unresolved


def _fingerprint(rows: list[tuple[Any, ...]]) -> str:
    return hashlib.sha256(repr(rows).encode()).hexdigest()
