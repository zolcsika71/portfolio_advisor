"""Synthetic governed evidence shared by Milestone 11B focused tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from portfolio_advisor.construction import (
    CapitalConservationShortlist,
    NavReadinessEvidence,
    RankedConstructionInstrument,
    RankedInstrument,
)
from portfolio_advisor.construction.models import ConstructionProvenance, SourceLineage
from portfolio_advisor.database.schema.v3 import connect, initialize_schema

ISINS = (
    "AT0000605324",
    "AT0000605332",
    "AT0000613617",
    "AT0000627476",
    "AT0000627484",
    "AT0000658976",
    "AT0000673165",
    "AT0000673181",
    "AT0000673249",
    "AT0000673306",
    "AT0000673314",
    "AT0000673322",
)
SNAPSHOT_DATE = date(2026, 1, 1)
SOURCE_SHA = "a" * 64
MANIFEST_SHA = "b" * 64


@dataclass(frozen=True, slots=True)
class SyntheticConstructionFixture:
    database_path: Path
    screening: CapitalConservationShortlist
    instruments: tuple[RankedConstructionInstrument, ...]


def build_fixture(
    tmp_path: Path,
    *,
    count: int = 10,
    currencies: tuple[str, ...] | None = None,
    groups: tuple[tuple[str | None, str | None], ...] | None = None,
    ranks: tuple[int, ...] | None = None,
    nav_dates: tuple[tuple[date, ...], ...] | None = None,
    category_conflicts: tuple[bool, ...] | None = None,
    nav_quality: tuple[str, ...] | None = None,
    proxy_flags: tuple[bool, ...] | None = None,
) -> SyntheticConstructionFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "constructed-fixture.sqlite"
    currencies = currencies or tuple("EUR" for _ in range(count))
    default_groups = tuple(
        (("BOND", "GOV"), ("BOND", "CORP"), ("CASHLIKE", "MONEY"))[index % 3]
        for index in range(count)
    )
    groups = groups or default_groups
    ranks = ranks or tuple(range(1, count + 1))
    dates = tuple(SNAPSHOT_DATE - timedelta(days=365 - index) for index in range(366))
    nav_dates = nav_dates or tuple(dates for _ in range(count))
    category_conflicts = category_conflicts or tuple(False for _ in range(count))
    nav_quality = nav_quality or tuple("ADMITTED_AND_VALIDATED" for _ in range(count))
    proxy_flags = proxy_flags or tuple(False for _ in range(count))
    candidates: list[RankedInstrument] = []
    enriched: list[RankedConstructionInstrument] = []
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute(
            """INSERT INTO source_file(
                   source_file_id, filename, sha256, source_type, source_date
               ) VALUES (1, 'synthetic.xls', ?, 'SHORTLIST_XLS', ?)""",
            (SOURCE_SHA, SNAPSHOT_DATE.isoformat()),
        )
        connection.execute(
            "INSERT INTO source_sheet(source_sheet_id, source_file_id, sheet_name) VALUES (1, 1, 'shortlist')"
        )
        connection.execute(
            """INSERT INTO shortlist_snapshot(
                   shortlist_snapshot_id, snapshot_date, source_sheet_id
               ) VALUES (1, ?, 1)""",
            (SNAPSHOT_DATE.isoformat(),),
        )
        for index in range(count):
            instrument_id = index + 1
            isin = ISINS[index]
            asset_class, sub_asset_class = groups[index]
            connection.execute(
                """INSERT INTO instrument(instrument_id, isin, canonical_name, base_currency_code)
                   VALUES (?, ?, ?, ?)""",
                (instrument_id, isin, f"Synthetic {index + 1}", currencies[index]),
            )
            connection.execute(
                """INSERT INTO shortlist_entry(
                       shortlist_entry_id, shortlist_snapshot_id, instrument_id,
                       source_row_number, status
                   ) VALUES (?, 1, ?, ?, 'SOURCE_REPORTED')""",
                (instrument_id, instrument_id, index + 2),
            )
            conflict_status = (
                "SOURCE_METADATA_CONFLICT" if category_conflicts[index] else "SOURCE_REPORTED"
            )
            connection.execute(
                """INSERT INTO shortlist_entry_source_occurrence(
                       shortlist_entry_source_occurrence_id, shortlist_snapshot_id,
                       instrument_id, source_sheet_id, source_row_number,
                       observed_product_name, observed_currency_code,
                       observed_asset_class, observed_sub_asset_class,
                       source_payload_json, conflict_status
                   ) VALUES (?, 1, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    instrument_id,
                    instrument_id,
                    index + 2,
                    f"Synthetic {index + 1}",
                    currencies[index],
                    asset_class,
                    sub_asset_class,
                    json.dumps({"ISIN": isin}, sort_keys=True),
                    conflict_status,
                ),
            )
            connection.execute(
                "INSERT INTO shortlist_entry_lineage(shortlist_entry_id, source_occurrence_id) VALUES (?, ?)",
                (instrument_id, instrument_id),
            )
            lineage = SourceLineage(instrument_id, (instrument_id,), (index + 2,))
            candidates.append(
                RankedInstrument(
                    instrument_id=instrument_id,
                    isin=isin,
                    canonical_name=f"Synthetic {index + 1}",
                    eligible=True,
                    rejection_reasons=(),
                    rank=ranks[index],
                    total_score=1.0 - index / 100,
                    feature_values=(),
                    weighted_contributions=(),
                    lineage=lineage,
                )
            )
            enriched.append(
                RankedConstructionInstrument(
                    instrument_id=instrument_id,
                    isin=isin,
                    canonical_name=f"Synthetic {index + 1}",
                    rank=ranks[index],
                    screening_eligible=True,
                    currency=currencies[index],
                    asset_class=asset_class,
                    sub_asset_class=sub_asset_class,
                    category_conflict=category_conflicts[index],
                    shortlist_snapshot_id=1,
                    shortlist_entry_id=instrument_id,
                    source_occurrence_ids=(instrument_id,),
                    nav=NavReadinessEvidence(
                        observation_dates=nav_dates[index],
                        quality=nav_quality[index],
                        proxy_instrument_used=proxy_flags[index],
                    ),
                )
            )
        connection.execute(
            """INSERT INTO shortlist_stage_manifest(
                   singleton, integration_version, workbook_fingerprints_json,
                   header_signature, source_occurrence_count, snapshot_count,
                   membership_count, lineage_count, instrument_count, alias_count,
                   metric_observation_count, multi_occurrence_count,
                   conflict_occurrence_count, dataset_fingerprint, completion_status
               ) VALUES (1, 'MILESTONE_9_SHORTLIST_V1', '{}', 'synthetic', ?, 1, ?, ?, ?,
                         0, 0, 0, ?, ?, 'COMPLETE')""",
            (
                count,
                count,
                count,
                count,
                sum(category_conflicts),
                MANIFEST_SHA,
            ),
        )
        connection.commit()
    provenance = ConstructionProvenance(
        objective="capital_conservation",
        strategy="CAPITAL_DEFENSIVE",
        construction_capability="INTERMEDIATE_INSTRUMENT_SCREENING_NOT_PORTFOLIO_CONSTRUCTION",
        policy_id="CAPITAL_PRESERVATION_RANKING_POLICY",
        policy_version="1.0.1",
        policy_fingerprint="c" * 64,
        registry_fingerprint="d" * 64,
        capability_states=(),
        snapshot_id=1,
        snapshot_date=SNAPSHOT_DATE,
        source_file="synthetic.xls",
        source_file_sha256=SOURCE_SHA,
        source_sheet_id=1,
        source_sheet_name="shortlist",
        shortlist_manifest_fingerprint=MANIFEST_SHA,
        shortlist_integration_version="MILESTONE_9_SHORTLIST_V1",
    )
    screening = CapitalConservationShortlist(
        provenance=provenance,
        candidates=tuple(candidates),
        constructed=tuple(candidates),
        ranking_warnings=(),
    )
    return SyntheticConstructionFixture(target, screening, tuple(enriched))
