"""Typed, optional persistence for direct official portfolio performance only."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from math import isfinite
from pathlib import Path

ALLOWED_VALUE_TYPES = frozenset(
    {
        "PORTFOLIO_NAV",
        "PORTFOLIO_INDEX_VALUE",
        "OFFICIAL_PORTFOLIO_PRICE",
        "OFFICIAL_TOTAL_RETURN_INDEX",
    }
)


class OfficialPortfolioPerformanceError(RuntimeError):
    """A direct official portfolio series cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class OfficialPortfolioPerformanceObservation:
    portfolio_id: str
    observation_date: date
    value: float
    currency: str
    value_type: str
    source_provider: str
    source_identifier: str
    provenance_reference: str
    quality_status: str = "VALIDATED"

    def __post_init__(self) -> None:
        if not self.portfolio_id or not self.currency or not self.source_provider:
            raise OfficialPortfolioPerformanceError("portfolio identity, currency, and provider are required")
        if self.value_type not in ALLOWED_VALUE_TYPES:
            raise OfficialPortfolioPerformanceError("unsupported direct portfolio value semantics")
        if not isfinite(self.value) or self.value <= 0.0:
            raise OfficialPortfolioPerformanceError("portfolio observation must be finite and positive")
        if not self.source_identifier or not self.provenance_reference:
            raise OfficialPortfolioPerformanceError("direct portfolio observation provenance is required")


class OfficialPortfolioPerformanceStore:
    """Idempotent canonical storage distinct from constituent NAV evidence."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def persist(self, observations: tuple[OfficialPortfolioPerformanceObservation, ...]) -> int:
        if not observations:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            inserted = 0
            for item in observations:
                existing = connection.execute(
                    'SELECT "Value", "Currency", "Source Identifier", "Provenance Reference", "Quality Status" '
                    'FROM official_portfolio_performance WHERE "Portfolio ID" = ? AND "Date" = ? '
                    'AND "Source Provider" = ? AND "Value Type" = ?',
                    (item.portfolio_id, item.observation_date.isoformat(), item.source_provider, item.value_type),
                ).fetchone()
                values = (
                    item.value,
                    item.currency,
                    item.source_identifier,
                    item.provenance_reference,
                    item.quality_status,
                )
                if existing is not None:
                    if tuple(existing) != values:
                        raise OfficialPortfolioPerformanceError("conflicting direct portfolio observation")
                    continue
                connection.execute(
                    'INSERT INTO official_portfolio_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        item.portfolio_id,
                        item.observation_date.isoformat(),
                        item.value,
                        item.currency,
                        item.value_type,
                        item.source_provider,
                        item.source_identifier,
                        item.provenance_reference,
                        item.quality_status,
                    ),
                )
                inserted += 1
        return inserted

    def observations(self, portfolio_id: str) -> tuple[OfficialPortfolioPerformanceObservation, ...]:
        if not self.path.is_file():
            return ()
        with sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                'SELECT "Portfolio ID", "Date", "Value", "Currency", "Value Type", "Source Provider", '
                '"Source Identifier", "Provenance Reference", "Quality Status" '
                'FROM official_portfolio_performance WHERE "Portfolio ID" = ? '
                'ORDER BY "Date", "Source Provider", "Value Type"',
                (portfolio_id,),
            ).fetchall()
        return tuple(
            OfficialPortfolioPerformanceObservation(
                portfolio_id=str(row[0]),
                observation_date=date.fromisoformat(str(row[1])),
                value=float(row[2]),
                currency=str(row[3]),
                value_type=str(row[4]),
                source_provider=str(row[5]),
                source_identifier=str(row[6]),
                provenance_reference=str(row[7]),
                quality_status=str(row[8]),
            )
            for row in rows
        )

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            '''CREATE TABLE IF NOT EXISTS official_portfolio_performance (
                "Portfolio ID" TEXT NOT NULL, "Date" TEXT NOT NULL, "Value" REAL NOT NULL,
                "Currency" TEXT NOT NULL, "Value Type" TEXT NOT NULL,
                "Source Provider" TEXT NOT NULL, "Source Identifier" TEXT NOT NULL,
                "Provenance Reference" TEXT NOT NULL, "Quality Status" TEXT NOT NULL,
                PRIMARY KEY ("Portfolio ID", "Date", "Source Provider", "Value Type")
            )'''
        )
