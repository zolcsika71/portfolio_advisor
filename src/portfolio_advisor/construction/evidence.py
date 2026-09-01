"""Read-only enrichment of reviewed screening results with schema-v3 evidence."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .models import (
    CapitalConservationShortlist,
    NavReadinessEvidence,
    RankedConstructionInstrument,
    RankedInstrument,
)


class ConstructionEvidenceError(RuntimeError):
    """Exact shortlist or NAV evidence cannot be proven read-only."""


def load_construction_instrument_evidence(
    database_path: Path,
    screening: CapitalConservationShortlist,
) -> tuple[RankedConstructionInstrument, ...]:
    """Bind every eligible reviewed rank to its exact membership, categories, and NAV dates."""
    if not database_path.is_file():
        raise ConstructionEvidenceError("schema-v3 database is missing")
    try:
        connection = sqlite3.connect(
            f"file:{database_path.resolve()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ConstructionEvidenceError("SQLite integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ConstructionEvidenceError("SQLite foreign_key_check failed")
        result = tuple(
            _load_one(connection, screening.provenance.snapshot_id, item)
            for item in screening.candidates
            if item.eligible and item.rank is not None
        )
    except sqlite3.DatabaseError as error:
        raise ConstructionEvidenceError("construction evidence schema is incompatible") from error
    finally:
        if "connection" in locals():
            connection.close()
    return result


def _load_one(
    connection: sqlite3.Connection,
    snapshot_id: int,
    ranked: RankedInstrument,
) -> RankedConstructionInstrument:
    if ranked.rank is None:
        raise ConstructionEvidenceError("screening result is not a ranked eligible instrument")
    rows = connection.execute(
        """SELECT e.shortlist_entry_id, e.instrument_id, i.isin, i.canonical_name,
                  o.shortlist_entry_source_occurrence_id, o.observed_currency_code,
                  o.observed_asset_class, o.observed_sub_asset_class, o.conflict_status
           FROM shortlist_entry e
           JOIN instrument i ON i.instrument_id=e.instrument_id
           JOIN shortlist_entry_lineage l ON l.shortlist_entry_id=e.shortlist_entry_id
           JOIN shortlist_entry_source_occurrence o
             ON o.shortlist_entry_source_occurrence_id=l.source_occurrence_id
           WHERE e.shortlist_snapshot_id=? AND e.shortlist_entry_id=?
           ORDER BY o.shortlist_entry_source_occurrence_id""",
        (snapshot_id, ranked.lineage.shortlist_entry_id),
    ).fetchall()
    if not rows:
        raise ConstructionEvidenceError("ranked instrument has no exact shortlist membership")
    if (
        any(int(row["instrument_id"]) != ranked.instrument_id for row in rows)
        or any(str(row["isin"]) != ranked.isin for row in rows)
        or tuple(int(row["shortlist_entry_source_occurrence_id"]) for row in rows)
        != ranked.lineage.source_occurrence_ids
    ):
        raise ConstructionEvidenceError("ranked instrument lineage conflicts with shortlist evidence")
    categories = {
        (
            _text(row["observed_currency_code"]),
            _text(row["observed_asset_class"]),
            _text(row["observed_sub_asset_class"]),
        )
        for row in rows
    }
    conflict = any(str(row["conflict_status"]) != "SOURCE_REPORTED" for row in rows)
    if len(categories) == 1:
        currency, asset_class, sub_asset_class = next(iter(categories))
    else:
        currency = asset_class = sub_asset_class = None
        conflict = True
    nav_rows = connection.execute(
        """SELECT observation_date, currency_code, quality_status
           FROM instrument_nav_observation WHERE instrument_id=?
           ORDER BY observation_date, source_provider, source_identifier""",
        (ranked.instrument_id,),
    ).fetchall()
    nav_dates = tuple(str(row["observation_date"]) for row in nav_rows)
    admitted = bool(nav_rows) and len(set(nav_dates)) == len(nav_dates) and all(
        str(row["quality_status"]) == "VALIDATED"
        and currency is not None
        and str(row["currency_code"]) == currency
        for row in nav_rows
    )
    return RankedConstructionInstrument(
        instrument_id=ranked.instrument_id,
        isin=ranked.isin,
        canonical_name=ranked.canonical_name,
        rank=ranked.rank,
        screening_eligible=True,
        currency=currency or "",
        asset_class=asset_class,
        sub_asset_class=sub_asset_class,
        category_conflict=conflict,
        shortlist_snapshot_id=snapshot_id,
        shortlist_entry_id=ranked.lineage.shortlist_entry_id,
        source_occurrence_ids=ranked.lineage.source_occurrence_ids,
        nav=NavReadinessEvidence(
            observation_dates=tuple(date.fromisoformat(value) for value in nav_dates),
            quality="ADMITTED_AND_VALIDATED" if admitted else "UNAVAILABLE",
        ),
    )


def _text(value: object) -> str | None:
    result = str(value).strip() if value is not None else ""
    return result or None
