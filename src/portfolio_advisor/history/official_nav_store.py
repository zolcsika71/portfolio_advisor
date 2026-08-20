"""Small, source-neutral local store for validated asset NAV observations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path


class OfficialNavStoreError(RuntimeError):
    """Validated source evidence cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class OfficialNavObservation:
    isin: str
    observation_date: date
    value: float
    currency: str
    value_type: str
    source_provider: str
    source_identifier: str
    provenance_reference: str
    quality_status: str = "VALIDATED"

    def __post_init__(self) -> None:
        if len(self.isin) != 12 or not self.isin.isalnum() or self.isin != self.isin.upper():
            raise OfficialNavStoreError("observation has an invalid ISIN")
        if not isfinite(self.value) or self.value <= 0.0:
            raise OfficialNavStoreError("observation value must be finite and positive")
        if not self.currency or not self.value_type or not self.source_provider:
            raise OfficialNavStoreError("observation identity and semantics are required")
        if self.value_type != "NAV":
            raise OfficialNavStoreError("only validated NAV observations are admissible")
        if not self.source_identifier or not self.provenance_reference:
            raise OfficialNavStoreError("observation provenance is required")


@dataclass(frozen=True, slots=True)
class OfficialNavCoverage:
    """Observed source interval for one persisted ISIN/provider pair."""

    isin: str
    source_provider: str
    observation_count: int
    first_observation: date
    last_observation: date


@dataclass(frozen=True, slots=True)
class OfficialNavStoreSummary:
    """Deterministic aggregate diagnostics for the local evidence store."""

    acquired_isin_count: int
    observation_count: int
    provider_observation_counts: tuple[tuple[str, int], ...]


class OfficialNavStore:
    """Idempotent SQLite persistence separate from portfolio snapshot data."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def persist(self, observations: tuple[OfficialNavObservation, ...]) -> int:
        if not observations:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            inserted = 0
            for item in observations:
                existing = connection.execute(
                    'SELECT "Value", "Currency", "Source Identifier", "Provenance Reference", "Quality Status" '
                    'FROM asset_nav_observations WHERE "ISIN" = ? AND "Date" = ? '
                    'AND "Source Provider" = ? AND "Value Type" = ?',
                    (item.isin, item.observation_date.isoformat(), item.source_provider, item.value_type),
                ).fetchone()
                values = (
                    item.value, item.currency, item.source_identifier,
                    item.provenance_reference, item.quality_status,
                )
                if existing is not None:
                    if tuple(existing) != values:
                        raise OfficialNavStoreError("conflicting reimport for canonical NAV observation")
                    continue
                connection.execute(
                    'INSERT INTO asset_nav_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        item.isin, item.observation_date.isoformat(), item.value, item.currency,
                        item.value_type, item.source_provider, item.source_identifier,
                        item.provenance_reference, item.quality_status,
                    ),
                )
                inserted += 1
        return inserted

    def observations(self, isin: str) -> tuple[OfficialNavObservation, ...]:
        if not self.path.is_file():
            return ()
        with sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True) as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                'SELECT "ISIN", "Date", "Value", "Currency", "Value Type", "Source Provider", '
                '"Source Identifier", "Provenance Reference", "Quality Status" FROM asset_nav_observations '
                'WHERE "ISIN" = ? ORDER BY "Date", "Source Provider", "Value Type"', (isin,)
            ).fetchall()
        return tuple(
            OfficialNavObservation(
                isin=row[0], observation_date=date.fromisoformat(row[1]), value=float(row[2]),
                currency=row[3], value_type=row[4], source_provider=row[5], source_identifier=row[6],
                provenance_reference=row[7], quality_status=row[8],
            )
            for row in rows
        )

    def identities(self) -> tuple[tuple[str, str, str, str], ...]:
        """Return retained ISIN/provider/currency semantics in stable order."""
        if not self.path.is_file():
            return ()
        with sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                'SELECT DISTINCT "ISIN", "Source Provider", "Currency", "Value Type" '
                'FROM asset_nav_observations '
                'ORDER BY "ISIN", "Source Provider", "Currency", "Value Type"'
            ).fetchall()
        return tuple((str(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows)

    def coverage(self, isin: str, source_provider: str) -> OfficialNavCoverage | None:
        """Return the exact retained observation bounds for a provider and ISIN."""
        if not self.path.is_file():
            return None
        with sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True) as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                'SELECT count(*), min("Date"), max("Date") FROM asset_nav_observations '
                'WHERE "ISIN" = ? AND "Source Provider" = ? AND "Value Type" = ?',
                (isin, source_provider, "NAV"),
            ).fetchone()
        if row is None or int(row[0]) == 0:
            return None
        return OfficialNavCoverage(
            isin=isin,
            source_provider=source_provider,
            observation_count=int(row[0]),
            first_observation=date.fromisoformat(str(row[1])),
            last_observation=date.fromisoformat(str(row[2])),
        )

    def summary(self) -> OfficialNavStoreSummary:
        """Return aggregate local evidence counts without performing network I/O."""
        if not self.path.is_file():
            return OfficialNavStoreSummary(0, 0, ())
        with sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True) as connection:
            self._ensure_schema(connection)
            total = int(connection.execute('SELECT count(*) FROM asset_nav_observations').fetchone()[0])
            isins = int(connection.execute('SELECT count(DISTINCT "ISIN") FROM asset_nav_observations').fetchone()[0])
            providers = tuple(
                (str(row[0]), int(row[1]))
                for row in connection.execute(
                    'SELECT "Source Provider", count(*) FROM asset_nav_observations '
                    'GROUP BY "Source Provider" ORDER BY "Source Provider"'
                ).fetchall()
            )
        return OfficialNavStoreSummary(isins, total, providers)

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            '''CREATE TABLE IF NOT EXISTS asset_nav_observations (
                "ISIN" TEXT NOT NULL, "Date" TEXT NOT NULL, "Value" REAL NOT NULL,
                "Currency" TEXT NOT NULL, "Value Type" TEXT NOT NULL,
                "Source Provider" TEXT NOT NULL, "Source Identifier" TEXT NOT NULL,
                "Provenance Reference" TEXT NOT NULL, "Quality Status" TEXT NOT NULL,
                PRIMARY KEY ("ISIN", "Date", "Source Provider", "Value Type")
            )'''
        )
