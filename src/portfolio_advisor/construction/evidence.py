"""Read-only enrichment of reviewed screening results with schema-v3 evidence."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from portfolio_advisor.database.migrations.shortlist_classification import (
    ClassificationCorrectionBinding,
    ShortlistClassificationCorrectionError,
    active_classification_correction,
    load_entry_classifications,
)

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
        classification_correction = active_classification_correction(connection)
        result = tuple(
            _load_one(
                connection,
                screening.provenance.snapshot_id,
                item,
                classification_correction,
            )
            for item in screening.candidates
            if item.eligible and item.rank is not None
        )
    except (sqlite3.DatabaseError, ShortlistClassificationCorrectionError) as error:
        raise ConstructionEvidenceError("construction evidence schema is incompatible") from error
    finally:
        if "connection" in locals():
            connection.close()
    return result


def _load_one(
    connection: sqlite3.Connection,
    snapshot_id: int,
    ranked: RankedInstrument,
    classification_correction: ClassificationCorrectionBinding | None,
) -> RankedConstructionInstrument:
    if ranked.rank is None:
        raise ConstructionEvidenceError("screening result is not a ranked eligible instrument")
    membership = connection.execute(
        """SELECT e.shortlist_entry_id, e.instrument_id, i.isin, i.canonical_name
           FROM shortlist_entry AS e
           JOIN instrument AS i ON i.instrument_id=e.instrument_id
           WHERE e.shortlist_snapshot_id=? AND e.shortlist_entry_id=?""",
        (snapshot_id, ranked.lineage.shortlist_entry_id),
    ).fetchone()
    if membership is None:
        raise ConstructionEvidenceError("ranked instrument has no exact shortlist membership")
    classifications = load_entry_classifications(
        connection,
        ranked.lineage.shortlist_entry_id,
        apply_corrections=classification_correction is not None,
        correction_binding=classification_correction,
    )
    if not classifications:
        raise ConstructionEvidenceError("ranked instrument has no source classification")
    if (
        int(membership["instrument_id"]) != ranked.instrument_id
        or str(membership["isin"]) != ranked.isin
        or tuple(row.source_occurrence_id for row in classifications)
        != ranked.lineage.source_occurrence_ids
    ):
        raise ConstructionEvidenceError("ranked instrument lineage conflicts with shortlist evidence")
    original_categories = {
        (row.currency, row.original_asset_class, row.original_sub_asset_class)
        for row in classifications
    }
    effective_categories = {
        (row.currency, row.effective_asset_class, row.effective_sub_asset_class)
        for row in classifications
    }
    conflict = any(row.conflict_status != "SOURCE_REPORTED" for row in classifications)
    if len(original_categories) == 1:
        _, original_asset_class, original_sub_asset_class = next(iter(original_categories))
    else:
        original_asset_class = original_sub_asset_class = None
    if len(effective_categories) == 1:
        currency, asset_class, sub_asset_class = next(iter(effective_categories))
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
        canonical_name=str(membership["canonical_name"]),
        rank=ranked.rank,
        screening_eligible=True,
        currency=currency or "",
        asset_class=asset_class,
        sub_asset_class=sub_asset_class,
        original_asset_class=original_asset_class,
        original_sub_asset_class=original_sub_asset_class,
        classification_correction_id=(
            classification_correction.correction_id
            if classification_correction is not None
            else None
        ),
        classification_correction_set_fingerprint=(
            classification_correction.correction_set_fingerprint
            if classification_correction is not None
            else None
        ),
        category_conflict=conflict,
        shortlist_snapshot_id=snapshot_id,
        shortlist_entry_id=ranked.lineage.shortlist_entry_id,
        source_occurrence_ids=ranked.lineage.source_occurrence_ids,
        nav=NavReadinessEvidence(
            observation_dates=tuple(date.fromisoformat(value) for value in nav_dates),
            quality="ADMITTED_AND_VALIDATED" if admitted else "UNAVAILABLE",
        ),
    )
