"""Explicit, provenance-bound English asset/sub-asset pair mappings."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json

from . import shortlist_classification as legacy
from . import shortlist_classification_composition as composition
from .shortlist_parallel import INTEGRATION_VERSION
from .shortlist_zero_null import sha256_file

CONTRACT_VERSION = 3
FEATURE_REVISION = 3
SCOPE = "SHORTLIST_EFFECTIVE_ASSET_SUB_ASSET_PAIR"
SOURCE_SCOPE = "Eszközosztály+Aleszközosztály"

FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "composition": "ORDERED_EXPLICIT_PAIR_MAPPINGS",
        "contract_version": CONTRACT_VERSION,
        "prior_feature_fingerprint": composition.FEATURE_FINGERPRINT,
        "revision": FEATURE_REVISION,
        "scope": SCOPE,
        "source_scope": SOURCE_SCOPE,
    }
)


@dataclass(frozen=True, slots=True)
class PairMappingEntry:
    prior_asset_class: str
    prior_sub_asset_class: str
    effective_asset_class: str
    effective_sub_asset_class: str
    row_count: int
    snapshot_count: int
    first_snapshot_date: str
    last_snapshot_date: str
    reference_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "effective_asset_class": self.effective_asset_class,
            "effective_sub_asset_class": self.effective_sub_asset_class,
            "first_snapshot_date": self.first_snapshot_date,
            "last_snapshot_date": self.last_snapshot_date,
            "prior_asset_class": self.prior_asset_class,
            "prior_sub_asset_class": self.prior_sub_asset_class,
            "reference_status": self.reference_status,
            "row_count": self.row_count,
            "snapshot_count": self.snapshot_count,
        }


@dataclass(frozen=True, slots=True)
class PairMappingManifest:
    schema_version: int
    mapping_id: str
    authorization_reference: str
    dataset_fingerprint: str
    expected_occurrence_count: int
    expected_mapped_occurrence_count: int
    expected_prior_asset_class_count: int
    expected_prior_sub_asset_class_count: int
    expected_prior_pair_count: int
    expected_result_asset_class_count: int
    expected_result_sub_asset_class_count: int
    expected_result_pair_count: int
    expected_snapshot_count: int
    expected_unchanged_snapshot_count: int
    expected_changed_transitions: tuple[tuple[int, int, int], ...]
    entries: tuple[PairMappingEntry, ...]
    manifest_sha256: str

    @property
    def mapping_fingerprint(self) -> str:
        return canonical_fingerprint(
            [entry.to_dict() for entry in self.entries_in_canonical_order]
        )

    @property
    def entries_in_canonical_order(self) -> tuple[PairMappingEntry, ...]:
        return tuple(
            sorted(
                self.entries,
                key=lambda item: (
                    item.prior_asset_class,
                    item.prior_sub_asset_class,
                ),
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "authorization_reference": self.authorization_reference,
            "dataset_fingerprint": self.dataset_fingerprint,
            "entries": [entry.to_dict() for entry in self.entries_in_canonical_order],
            "expected_input": {
                "asset_class_count": self.expected_prior_asset_class_count,
                "mapped_occurrence_count": self.expected_mapped_occurrence_count,
                "occurrence_count": self.expected_occurrence_count,
                "pair_count": self.expected_prior_pair_count,
                "sub_asset_class_count": self.expected_prior_sub_asset_class_count,
            },
            "expected_output": {
                "asset_class_count": self.expected_result_asset_class_count,
                "occurrence_count": self.expected_occurrence_count,
                "pair_count": self.expected_result_pair_count,
                "sub_asset_class_count": self.expected_result_sub_asset_class_count,
            },
            "expected_snapshot_group_effects": {
                "changed_transitions": [
                    {
                        "after_group_count": after,
                        "before_group_count": before,
                        "snapshot_count": count,
                    }
                    for before, after, count in self.expected_changed_transitions
                ],
                "snapshot_count": self.expected_snapshot_count,
                "unchanged_snapshot_count": self.expected_unchanged_snapshot_count,
            },
            "mapping_id": self.mapping_id,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class ClassificationPairMappingRequest:
    correction_id: str
    dataset_fingerprint: str
    initial_target_sha256: str
    authorization_reference: str
    reason: str
    manifest: PairMappingManifest


@dataclass(slots=True)
class _InventoryState:
    rows: int = 0
    snapshots: set[str] = field(default_factory=set)


_TABLE_SCHEMA_SQL = """
CREATE TABLE shortlist_classification_pair_mapping_admission (
    correction_id TEXT PRIMARY KEY CHECK(length(trim(correction_id)) > 0),
    contract_version INTEGER NOT NULL CHECK(contract_version = 3),
    application_order INTEGER NOT NULL UNIQUE CHECK(application_order >= 3),
    dataset_fingerprint TEXT NOT NULL CHECK(length(dataset_fingerprint) = 64),
    integration_version TEXT NOT NULL,
    initial_target_sha256 TEXT NOT NULL CHECK(length(initial_target_sha256) = 64),
    authorization_reference TEXT NOT NULL CHECK(length(trim(authorization_reference)) > 0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    scope TEXT NOT NULL CHECK(scope = 'SHORTLIST_EFFECTIVE_ASSET_SUB_ASSET_PAIR'),
    source_scope TEXT NOT NULL CHECK(source_scope = 'Eszközosztály+Aleszközosztály'),
    mapping_id TEXT NOT NULL CHECK(length(trim(mapping_id)) > 0),
    mapping_manifest_sha256 TEXT NOT NULL CHECK(length(mapping_manifest_sha256) = 64),
    mapping_fingerprint TEXT NOT NULL CHECK(length(mapping_fingerprint) = 64),
    mapping_manifest_json TEXT NOT NULL,
    expected_occurrence_count INTEGER NOT NULL CHECK(expected_occurrence_count > 0),
    expected_mapped_occurrence_count INTEGER NOT NULL CHECK(expected_mapped_occurrence_count > 0),
    expected_prior_asset_class_count INTEGER NOT NULL CHECK(expected_prior_asset_class_count > 0),
    expected_prior_sub_asset_class_count INTEGER NOT NULL CHECK(expected_prior_sub_asset_class_count > 0),
    expected_prior_pair_count INTEGER NOT NULL CHECK(expected_prior_pair_count > 0),
    expected_result_asset_class_count INTEGER NOT NULL CHECK(expected_result_asset_class_count > 0),
    expected_result_sub_asset_class_count INTEGER NOT NULL CHECK(expected_result_sub_asset_class_count > 0),
    expected_result_pair_count INTEGER NOT NULL CHECK(expected_result_pair_count > 0),
    expected_snapshot_group_effects_json TEXT NOT NULL,
    prior_composition_fingerprint TEXT NOT NULL CHECK(length(prior_composition_fingerprint) = 64),
    correction_set_fingerprint TEXT NOT NULL UNIQUE CHECK(length(correction_set_fingerprint) = 64),
    composed_correction_set_fingerprint TEXT NOT NULL UNIQUE CHECK(length(composed_correction_set_fingerprint) = 64),
    item_count INTEGER NOT NULL CHECK(item_count > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE shortlist_classification_pair_mapping_item (
    correction_id TEXT NOT NULL REFERENCES shortlist_classification_pair_mapping_admission(correction_id),
    application_order INTEGER NOT NULL CHECK(application_order >= 3),
    source_key TEXT NOT NULL,
    source_file_sha256 TEXT NOT NULL CHECK(length(source_file_sha256) = 64),
    source_sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    source_scope TEXT NOT NULL CHECK(source_scope = 'Eszközosztály+Aleszközosztály'),
    snapshot_date TEXT NOT NULL,
    isin TEXT NOT NULL,
    expected_original_asset_class TEXT NOT NULL,
    expected_original_sub_asset_class TEXT NOT NULL,
    expected_prior_effective_asset_class TEXT NOT NULL,
    expected_prior_effective_sub_asset_class TEXT NOT NULL,
    effective_asset_class TEXT NOT NULL CHECK(length(trim(effective_asset_class)) > 0),
    effective_sub_asset_class TEXT NOT NULL CHECK(length(trim(effective_sub_asset_class)) > 0),
    PRIMARY KEY(correction_id, source_key),
    UNIQUE(application_order, source_key)
);
CREATE INDEX shortlist_classification_pair_mapping_item_resolution_idx
ON shortlist_classification_pair_mapping_item(
    correction_id,
    application_order,
    source_file_sha256,
    source_sheet_name,
    source_row_number,
    snapshot_date,
    isin,
    expected_original_asset_class,
    expected_original_sub_asset_class,
    expected_prior_effective_asset_class,
    expected_prior_effective_sub_asset_class
);
CREATE TRIGGER shortlist_classification_pair_mapping_admission_immutable_update
BEFORE UPDATE ON shortlist_classification_pair_mapping_admission
BEGIN SELECT RAISE(ABORT, 'shortlist classification pair mapping admissions are immutable'); END;
CREATE TRIGGER shortlist_classification_pair_mapping_admission_immutable_delete
BEFORE DELETE ON shortlist_classification_pair_mapping_admission
BEGIN SELECT RAISE(ABORT, 'shortlist classification pair mapping admissions are immutable'); END;
CREATE TRIGGER shortlist_classification_pair_mapping_item_immutable_update
BEFORE UPDATE ON shortlist_classification_pair_mapping_item
BEGIN SELECT RAISE(ABORT, 'shortlist classification pair mapping items are immutable'); END;
CREATE TRIGGER shortlist_classification_pair_mapping_item_immutable_delete
BEFORE DELETE ON shortlist_classification_pair_mapping_item
BEGIN SELECT RAISE(ABORT, 'shortlist classification pair mapping items are immutable'); END;
"""

_VIEW_SCHEMA_SQL = """
CREATE VIEW v_shortlist_classification_correction_stage AS
WITH RECURSIVE
admission_order AS (
    SELECT application_order, 'SUB_ASSET' AS admission_type
    FROM shortlist_classification_composition_admission
    UNION ALL
    SELECT application_order, 'PAIR' AS admission_type
    FROM shortlist_classification_pair_mapping_admission
),
base AS (
    SELECT occurrence.shortlist_entry_source_occurrence_id,
           occurrence.shortlist_snapshot_id,
           occurrence.instrument_id,
           source_file.sha256 AS source_file_sha256,
           source_sheet.sheet_name AS source_sheet_name,
           occurrence.source_row_number,
           instrument.isin,
           occurrence.observed_currency_code,
           occurrence.observed_asset_class AS original_asset_class,
           occurrence.observed_asset_class AS effective_asset_class,
           occurrence.observed_sub_asset_class AS original_sub_asset_class,
           CASE WHEN correction.source_key IS NULL
                THEN occurrence.observed_sub_asset_class
                ELSE correction.effective_sub_asset_class END AS effective_sub_asset_class,
           occurrence.conflict_status,
           correction.correction_id,
           admission.correction_set_fingerprint,
           1 AS application_order
    FROM shortlist_entry_source_occurrence AS occurrence
    JOIN shortlist_snapshot AS snapshot
      ON snapshot.shortlist_snapshot_id = occurrence.shortlist_snapshot_id
    JOIN source_sheet AS source_sheet
      ON source_sheet.source_sheet_id = occurrence.source_sheet_id
    JOIN source_file AS source_file
      ON source_file.source_file_id = source_sheet.source_file_id
    JOIN instrument AS instrument
      ON instrument.instrument_id = occurrence.instrument_id
    LEFT JOIN shortlist_classification_correction AS correction
      ON correction.source_file_sha256 = source_file.sha256
     AND correction.source_sheet_name = source_sheet.sheet_name
     AND correction.source_row_number = occurrence.source_row_number
     AND correction.snapshot_date = snapshot.snapshot_date
     AND correction.isin = instrument.isin
     AND correction.asset_class = occurrence.observed_asset_class
     AND correction.expected_original_sub_asset_class = occurrence.observed_sub_asset_class
    LEFT JOIN shortlist_classification_correction_admission AS admission
      ON admission.correction_id = correction.correction_id
),
composed AS (
    SELECT * FROM base
    UNION ALL
    SELECT composed.shortlist_entry_source_occurrence_id,
           composed.shortlist_snapshot_id,
           composed.instrument_id,
           composed.source_file_sha256,
           composed.source_sheet_name,
           composed.source_row_number,
           composed.isin,
           composed.observed_currency_code,
           composed.original_asset_class,
           CASE WHEN pair_item.source_key IS NULL
                THEN composed.effective_asset_class
                ELSE pair_item.effective_asset_class END,
           composed.original_sub_asset_class,
           CASE WHEN pair_item.source_key IS NOT NULL
                THEN pair_item.effective_sub_asset_class
                WHEN sub_item.source_key IS NOT NULL
                THEN sub_item.effective_sub_asset_class
                ELSE composed.effective_sub_asset_class END,
           composed.conflict_status,
           CASE WHEN pair_item.source_key IS NOT NULL THEN pair_item.correction_id
                WHEN sub_item.source_key IS NOT NULL THEN sub_item.correction_id
                ELSE composed.correction_id END,
           CASE WHEN pair_item.source_key IS NOT NULL
                THEN pair_admission.composed_correction_set_fingerprint
                WHEN sub_item.source_key IS NOT NULL
                THEN sub_admission.composed_correction_set_fingerprint
                ELSE composed.correction_set_fingerprint END,
           next.application_order
    FROM composed
    JOIN admission_order AS next
      ON next.application_order = composed.application_order + 1
    LEFT JOIN shortlist_classification_composition_admission AS sub_admission
      ON next.admission_type = 'SUB_ASSET'
     AND sub_admission.application_order = next.application_order
    LEFT JOIN shortlist_classification_composition_item AS sub_item
      ON sub_item.correction_id = sub_admission.correction_id
     AND sub_item.application_order = sub_admission.application_order
     AND sub_item.source_file_sha256 = composed.source_file_sha256
     AND sub_item.source_sheet_name = composed.source_sheet_name
     AND sub_item.source_row_number = composed.source_row_number
     AND sub_item.snapshot_date = (
         SELECT snapshot_date FROM shortlist_snapshot
         WHERE shortlist_snapshot_id = composed.shortlist_snapshot_id
     )
     AND sub_item.isin = composed.isin
     AND sub_item.asset_class = composed.original_asset_class
     AND sub_item.expected_original_sub_asset_class = composed.original_sub_asset_class
     AND sub_item.expected_prior_effective_sub_asset_class = composed.effective_sub_asset_class
    LEFT JOIN shortlist_classification_pair_mapping_admission AS pair_admission
      ON next.admission_type = 'PAIR'
     AND pair_admission.application_order = next.application_order
    LEFT JOIN shortlist_classification_pair_mapping_item AS pair_item
      ON pair_item.correction_id = pair_admission.correction_id
     AND pair_item.application_order = pair_admission.application_order
     AND pair_item.source_file_sha256 = composed.source_file_sha256
     AND pair_item.source_sheet_name = composed.source_sheet_name
     AND pair_item.source_row_number = composed.source_row_number
     AND pair_item.snapshot_date = (
         SELECT snapshot_date FROM shortlist_snapshot
         WHERE shortlist_snapshot_id = composed.shortlist_snapshot_id
     )
     AND pair_item.isin = composed.isin
     AND pair_item.expected_original_asset_class = composed.original_asset_class
     AND pair_item.expected_original_sub_asset_class = composed.original_sub_asset_class
     AND pair_item.expected_prior_effective_asset_class = composed.effective_asset_class
     AND pair_item.expected_prior_effective_sub_asset_class = composed.effective_sub_asset_class
)
SELECT * FROM composed;

CREATE VIEW v_effective_shortlist_classification AS
SELECT stage.shortlist_entry_source_occurrence_id,
       stage.shortlist_snapshot_id,
       stage.instrument_id,
       stage.source_file_sha256,
       stage.source_sheet_name,
       stage.source_row_number,
       stage.isin,
       stage.observed_currency_code,
       stage.original_asset_class,
       stage.effective_asset_class,
       stage.original_sub_asset_class,
       stage.effective_sub_asset_class,
       stage.conflict_status,
       stage.correction_id,
       stage.correction_set_fingerprint
FROM v_shortlist_classification_correction_stage AS stage
WHERE stage.application_order = (
    SELECT max(application_order)
    FROM (
        SELECT 1 AS application_order
        UNION ALL
        SELECT application_order
        FROM shortlist_classification_composition_admission
        UNION ALL
        SELECT application_order
        FROM shortlist_classification_pair_mapping_admission
    )
);
"""

_OBJECT_NAMES = frozenset(
    {
        "shortlist_classification_pair_mapping_admission",
        "shortlist_classification_pair_mapping_item",
        "shortlist_classification_pair_mapping_item_resolution_idx",
        "shortlist_classification_pair_mapping_admission_immutable_update",
        "shortlist_classification_pair_mapping_admission_immutable_delete",
        "shortlist_classification_pair_mapping_item_immutable_update",
        "shortlist_classification_pair_mapping_item_immutable_delete",
        "v_shortlist_classification_correction_stage",
        "v_effective_shortlist_classification",
    }
)


def load_pair_mapping_manifest(path: Path) -> PairMappingManifest:
    """Load and strictly validate one reviewable mapping manifest."""
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest must be a regular file"
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest is unreadable or malformed"
        ) from error
    return _parse_manifest_payload(payload, sha256_file(resolved))


def admit_pair_mapping_correction(
    path: Path, request: ClassificationPairMappingRequest
) -> legacy.ClassificationCorrectionResult:
    """Append one exact asset/sub-asset mapping admission atomically."""
    path = path.resolve()
    _validate_request(request)
    if not path.is_file() or path.is_symlink():
        raise legacy.ShortlistClassificationCorrectionError(
            "correction target must be a regular SQLite file"
        )
    before_sha256 = sha256_file(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        extension_installed = composition._pair_mapping_extension_present(connection)
        existing = _existing_admission(connection, request.correction_id)
        if existing is not None:
            result = _validate_replay(connection, request, existing)
            if sha256_file(path) != before_sha256:
                raise legacy.ShortlistClassificationCorrectionError(
                    "exact replay unexpectedly changed the database"
                )
            return result
        composition.validate_composed_classification_corrections(connection)
        if before_sha256 != request.initial_target_sha256:
            raise legacy.ShortlistClassificationCorrectionError(
                "initial target SHA-256 does not match authorization"
            )
        prior_order = composition.maximum_application_order(connection)
        application_order = prior_order + 1
        items = _discover_items(connection, prior_order, request.manifest)
        correction_set_fingerprint = canonical_fingerprint(items)
        prior_binding = composition._prefix_bindings(connection)[-1]
        composed_fingerprint = composed_pair_mapping_fingerprint(
            (
                *composition._individual_bindings(connection),
                (request.correction_id, correction_set_fingerprint),
            )
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not extension_installed:
                _install_schema(connection)
            connection.execute(
                """INSERT INTO shortlist_classification_pair_mapping_admission(
                       correction_id, contract_version, application_order,
                       dataset_fingerprint, integration_version,
                       initial_target_sha256, authorization_reference, reason,
                       scope, source_scope, mapping_id,
                       mapping_manifest_sha256, mapping_fingerprint,
                       mapping_manifest_json, expected_occurrence_count,
                       expected_mapped_occurrence_count,
                       expected_prior_asset_class_count,
                       expected_prior_sub_asset_class_count,
                       expected_prior_pair_count,
                       expected_result_asset_class_count,
                       expected_result_sub_asset_class_count,
                       expected_result_pair_count,
                       expected_snapshot_group_effects_json,
                       prior_composition_fingerprint,
                       correction_set_fingerprint,
                       composed_correction_set_fingerprint, item_count
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _admission_values(
                    request,
                    application_order,
                    prior_binding.correction_set_fingerprint,
                    correction_set_fingerprint,
                    composed_fingerprint,
                    len(items),
                ),
            )
            connection.executemany(
                """INSERT INTO shortlist_classification_pair_mapping_item(
                       correction_id, source_key, source_file_sha256,
                       source_sheet_name, source_row_number, source_scope,
                       snapshot_date, isin, expected_original_asset_class,
                       expected_original_sub_asset_class,
                       expected_prior_effective_asset_class,
                       expected_prior_effective_sub_asset_class,
                       effective_asset_class, effective_sub_asset_class,
                       application_order
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ((request.correction_id, *item) for item in items),
            )
            validate_pair_mapping_corrections(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return legacy.ClassificationCorrectionResult(
        correction_id=request.correction_id,
        dataset_fingerprint=request.dataset_fingerprint,
        correction_set_fingerprint=correction_set_fingerprint,
        item_count=len(items),
        replayed=False,
    )


def validate_pair_mapping_corrections(connection: sqlite3.Connection) -> None:
    """Validate v1/v2 history plus every ordered pair-mapping admission."""
    legacy._validate_sqlite_health(connection)
    _validate_schema(connection)
    manifest_row = legacy._manifest(connection)
    dataset_fingerprint = str(manifest_row["dataset_fingerprint"])
    individual = _validate_legacy_and_v2_records(connection, dataset_fingerprint)
    prior_fingerprint = composition._composed_fingerprint(tuple(individual))
    admissions = connection.execute(
        "SELECT * FROM shortlist_classification_pair_mapping_admission "
        "ORDER BY application_order"
    ).fetchall()
    if not admissions:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping schema has no admission"
        )
    latest_manifest: PairMappingManifest | None = None
    for expected_order, admission in enumerate(admissions, start=len(individual) + 1):
        stored_manifest = _parse_manifest_payload(
            json.loads(str(admission["mapping_manifest_json"])),
            str(admission["mapping_manifest_sha256"]),
        )
        expected_effects = _group_effects_json(stored_manifest)
        if (
            int(admission["contract_version"]) != CONTRACT_VERSION
            or int(admission["application_order"]) != expected_order
            or str(admission["dataset_fingerprint"]) != dataset_fingerprint
            or str(admission["integration_version"]) != INTEGRATION_VERSION
            or str(admission["scope"]) != SCOPE
            or str(admission["source_scope"]) != SOURCE_SCOPE
            or str(admission["mapping_id"]) != stored_manifest.mapping_id
            or str(admission["mapping_fingerprint"])
            != stored_manifest.mapping_fingerprint
            or str(admission["authorization_reference"])
            != stored_manifest.authorization_reference
            or str(admission["expected_snapshot_group_effects_json"])
            != expected_effects
            or str(admission["prior_composition_fingerprint"]) != prior_fingerprint
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                "classification pair mapping admission binding is stale"
            )
        _validate_admission_counts(admission, stored_manifest)
        items = _discover_items(connection, expected_order - 1, stored_manifest)
        stored = _stored_items(connection, str(admission["correction_id"]))
        item_fingerprint = canonical_fingerprint(items)
        if stored != items:
            raise legacy.ShortlistClassificationCorrectionError(
                "classification pair mapping items do not match evidence"
            )
        if (
            int(admission["item_count"]) != len(items)
            or str(admission["correction_set_fingerprint"]) != item_fingerprint
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                "classification pair mapping set fingerprint mismatch"
            )
        individual.append((str(admission["correction_id"]), item_fingerprint))
        prior_fingerprint = composed_pair_mapping_fingerprint(tuple(individual))
        if str(admission["composed_correction_set_fingerprint"]) != prior_fingerprint:
            raise legacy.ShortlistClassificationCorrectionError(
                "composed classification pair mapping fingerprint mismatch"
            )
        latest_manifest = stored_manifest

    if latest_manifest is None:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping admission is unavailable"
        )
    projected = connection.execute(
        """SELECT count(*), count(DISTINCT effective_asset_class),
                  count(DISTINCT effective_sub_asset_class),
                  count(*)
           FROM (
               SELECT effective_asset_class, effective_sub_asset_class,
                      count(*) AS rows_in_pair
               FROM v_effective_shortlist_classification
               WHERE effective_asset_class IS NOT NULL
                 AND effective_sub_asset_class IS NOT NULL
               GROUP BY effective_asset_class, effective_sub_asset_class
           )"""
    ).fetchone()
    if projected is None or tuple(int(value) for value in projected) != (
        latest_manifest.expected_result_pair_count,
        latest_manifest.expected_result_asset_class_count,
        latest_manifest.expected_result_sub_asset_class_count,
        latest_manifest.expected_result_pair_count,
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "effective classification pair projection is incomplete"
        )
    occurrence_count = int(
        connection.execute(
            "SELECT count(*) FROM v_effective_shortlist_classification"
        ).fetchone()[0]
    )
    if occurrence_count != latest_manifest.expected_occurrence_count:
        raise legacy.ShortlistClassificationCorrectionError(
            "effective classification occurrence count is stale"
        )
    null_counts = connection.execute(
        """SELECT
               sum(original_asset_class IS NULL), sum(effective_asset_class IS NULL),
               sum(original_sub_asset_class IS NULL), sum(effective_sub_asset_class IS NULL)
           FROM v_effective_shortlist_classification"""
    ).fetchone()
    if (
        null_counts is None
        or int(null_counts[0] or 0) != int(null_counts[1] or 0)
        or int(null_counts[2] or 0) != int(null_counts[3] or 0)
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping did not preserve NULL classifications"
        )


def composed_pair_mapping_fingerprint(
    bindings: tuple[tuple[str, str], ...],
) -> str:
    return canonical_fingerprint(
        {
            "contract_version": CONTRACT_VERSION,
            "ordered_admissions": [
                {
                    "application_order": order,
                    "correction_id": correction_id,
                    "correction_set_fingerprint": fingerprint,
                }
                for order, (correction_id, fingerprint) in enumerate(bindings, start=1)
            ],
            "scope": SCOPE,
        }
    )


def _discover_items(
    connection: sqlite3.Connection,
    prior_order: int,
    manifest: PairMappingManifest,
) -> tuple[tuple[object, ...], ...]:
    installed = legacy._manifest(connection)
    if str(installed["dataset_fingerprint"]) != manifest.dataset_fingerprint:
        raise legacy.ShortlistClassificationCorrectionError(
            "shortlist dataset fingerprint does not match pair mapping authorization"
        )
    rows = connection.execute(
        """SELECT effective.source_file_sha256,
                  effective.source_sheet_name, effective.source_row_number,
                  occurrence.source_payload_json, snapshot.snapshot_date,
                  effective.isin, effective.original_asset_class,
                  effective.original_sub_asset_class,
                  effective.effective_asset_class,
                  effective.effective_sub_asset_class
           FROM v_shortlist_classification_correction_stage AS effective
           JOIN shortlist_entry_source_occurrence AS occurrence
             ON occurrence.shortlist_entry_source_occurrence_id=
                effective.shortlist_entry_source_occurrence_id
           JOIN shortlist_snapshot AS snapshot
             ON snapshot.shortlist_snapshot_id=effective.shortlist_snapshot_id
           WHERE effective.application_order=?
           ORDER BY effective.source_file_sha256,
                    effective.source_sheet_name,
                    effective.source_row_number,
                    effective.isin""",
        (prior_order,),
    ).fetchall()
    if len(rows) != manifest.expected_occurrence_count:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification occurrence count does not match mapping authorization"
        )
    mapping = {
        (entry.prior_asset_class, entry.prior_sub_asset_class): entry
        for entry in manifest.entries
    }
    stats: dict[tuple[str, str], _InventoryState] = defaultdict(_InventoryState)
    before_by_snapshot: dict[str, set[tuple[object, object]]] = defaultdict(set)
    after_by_snapshot: dict[str, set[tuple[object, object]]] = defaultdict(set)
    mapped_count = 0
    items: list[tuple[object, ...]] = []
    output_rows: list[tuple[object, object]] = []
    for row in rows:
        prior_asset = row["effective_asset_class"]
        prior_sub = row["effective_sub_asset_class"]
        snapshot_date = str(row["snapshot_date"])
        prior_pair = (prior_asset, prior_sub)
        before_by_snapshot[snapshot_date]
        after_by_snapshot[snapshot_date]
        if prior_asset is None or prior_sub is None:
            output_rows.append(prior_pair)
            continue
        before_by_snapshot[snapshot_date].add(prior_pair)
        pair = (str(prior_asset), str(prior_sub))
        entry = mapping.get(pair)
        if entry is None:
            raise legacy.ShortlistClassificationCorrectionError(
                "effective classification pair is absent from authorization"
            )
        mapped_count += 1
        state = stats[pair]
        state.rows += 1
        state.snapshots.add(snapshot_date)
        effective_pair = (entry.effective_asset_class, entry.effective_sub_asset_class)
        after_by_snapshot[snapshot_date].add(effective_pair)
        output_rows.append(effective_pair)
        _validate_raw_evidence(row)
        source_key = (
            f"SHORTLIST_CLASSIFICATION_PAIR:{row['source_file_sha256']}:"
            f"{row['source_sheet_name']}:{row['source_row_number']}:{SOURCE_SCOPE}"
        )
        items.append(
            (
                source_key,
                str(row["source_file_sha256"]),
                str(row["source_sheet_name"]),
                int(row["source_row_number"]),
                SOURCE_SCOPE,
                snapshot_date,
                str(row["isin"]),
                str(row["original_asset_class"]),
                str(row["original_sub_asset_class"]),
                pair[0],
                pair[1],
                entry.effective_asset_class,
                entry.effective_sub_asset_class,
                prior_order + 1,
            )
        )
    if mapped_count != manifest.expected_mapped_occurrence_count:
        raise legacy.ShortlistClassificationCorrectionError(
            "mapped occurrence count does not match authorization"
        )
    _validate_mapping_inventory(stats, manifest)
    _validate_output_inventory(output_rows, manifest)
    _validate_group_effects(before_by_snapshot, after_by_snapshot, manifest)
    if not items:
        raise legacy.ShortlistClassificationCorrectionError(
            "authorized classification pair mapping set is empty"
        )
    return tuple(sorted(items))


def _validate_raw_evidence(row: sqlite3.Row) -> None:
    try:
        payload = json.loads(str(row["source_payload_json"]))
    except json.JSONDecodeError as error:
        raise legacy.ShortlistClassificationCorrectionError(
            "shortlist source payload is malformed"
        ) from error
    if not isinstance(payload, dict) or (
        payload.get(legacy.ASSET_CLASS_HEADER) != row["original_asset_class"]
        or payload.get(legacy.SOURCE_HEADER) != row["original_sub_asset_class"]
        or payload.get("ISIN") != row["isin"]
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "normalized shortlist classification does not match raw source evidence"
        )


def _validate_mapping_inventory(
    stats: dict[tuple[str, str], _InventoryState],
    manifest: PairMappingManifest,
) -> None:
    if set(stats) != {
        (entry.prior_asset_class, entry.prior_sub_asset_class)
        for entry in manifest.entries
    }:
        raise legacy.ShortlistClassificationCorrectionError(
            "effective classification pair inventory does not match authorization"
        )
    for entry in manifest.entries:
        state = stats[(entry.prior_asset_class, entry.prior_sub_asset_class)]
        if not state.snapshots:
            raise legacy.ShortlistClassificationCorrectionError(
                "classification pair snapshot inventory is empty"
            )
        actual = (
            state.rows,
            len(state.snapshots),
            min(state.snapshots),
            max(state.snapshots),
        )
        expected = (
            entry.row_count,
            entry.snapshot_count,
            entry.first_snapshot_date,
            entry.last_snapshot_date,
        )
        if actual != expected:
            raise legacy.ShortlistClassificationCorrectionError(
                "classification pair counts or coverage do not match authorization"
            )


def _validate_output_inventory(
    output_rows: list[tuple[object, object]], manifest: PairMappingManifest
) -> None:
    pairs = {
        (asset, sub)
        for asset, sub in output_rows
        if asset is not None and sub is not None
    }
    assets = {asset for asset, _sub in pairs}
    sub_assets = {sub for _asset, sub in pairs}
    if (
        len(assets),
        len(sub_assets),
        len(pairs),
    ) != (
        manifest.expected_result_asset_class_count,
        manifest.expected_result_sub_asset_class_count,
        manifest.expected_result_pair_count,
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "resulting classification pair inventory does not match authorization"
        )


def _validate_group_effects(
    before: dict[str, set[tuple[object, object]]],
    after: dict[str, set[tuple[object, object]]],
    manifest: PairMappingManifest,
) -> None:
    unchanged = 0
    changed: Counter[tuple[int, int]] = Counter()
    for snapshot_date, prior_groups in before.items():
        resulting_groups = after[snapshot_date]
        if len(prior_groups) == len(resulting_groups):
            unchanged += 1
        else:
            changed[(len(prior_groups), len(resulting_groups))] += 1
    expected = Counter(
        {
            (before_count, after_count): count
            for before_count, after_count, count in manifest.expected_changed_transitions
        }
    )
    if (
        len(before) != manifest.expected_snapshot_count
        or unchanged != manifest.expected_unchanged_snapshot_count
        or changed != expected
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "snapshot classification group effects do not match authorization"
        )


def _validate_legacy_and_v2_records(
    connection: sqlite3.Connection, dataset_fingerprint: str
) -> list[tuple[str, str]]:
    legacy_admissions = connection.execute(
        "SELECT * FROM shortlist_classification_correction_admission"
    ).fetchall()
    if len(legacy_admissions) != 1:
        raise legacy.ShortlistClassificationCorrectionError(
            "exactly one legacy classification admission is required"
        )
    legacy_admission = legacy_admissions[0]
    if (
        int(legacy_admission["contract_version"]) != legacy.CONTRACT_VERSION
        or str(legacy_admission["dataset_fingerprint"]) != dataset_fingerprint
        or str(legacy_admission["integration_version"]) != INTEGRATION_VERSION
        or str(legacy_admission["source_header"]) != legacy.SOURCE_HEADER
        or str(legacy_admission["original_sub_asset_class"])
        != legacy.ORIGINAL_SUB_ASSET_CLASS
        or str(legacy_admission["effective_sub_asset_class"])
        != legacy.EFFECTIVE_SUB_ASSET_CLASS
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification correction admission binding is stale"
        )
    legacy_items = legacy._discover_items(connection, dataset_fingerprint)
    if (
        legacy._stored_items(connection, str(legacy_admission["correction_id"]))
        != legacy_items
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification correction item bindings do not match evidence"
        )
    legacy_fingerprint = canonical_fingerprint(legacy_items)
    if (
        int(legacy_admission["item_count"]) != len(legacy_items)
        or str(legacy_admission["correction_set_fingerprint"]) != legacy_fingerprint
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification correction fingerprint is stale"
        )
    individual = [(str(legacy_admission["correction_id"]), legacy_fingerprint)]
    prior_fingerprint = legacy_fingerprint
    admissions = connection.execute(
        "SELECT * FROM shortlist_classification_composition_admission "
        "ORDER BY application_order"
    ).fetchall()
    if not admissions:
        raise legacy.ShortlistClassificationCorrectionError(
            "composition schema has no admission"
        )
    for expected_order, admission in enumerate(admissions, start=2):
        if (
            int(admission["contract_version"]) != composition.CONTRACT_VERSION
            or int(admission["application_order"]) != expected_order
            or str(admission["dataset_fingerprint"]) != dataset_fingerprint
            or str(admission["integration_version"]) != INTEGRATION_VERSION
            or str(admission["scope"]) != composition.SCOPE
            or str(admission["source_header"]) != legacy.SOURCE_HEADER
            or str(admission["replacement_from"]) != composition.REPLACEMENT_FROM
            or str(admission["replacement_to"]) != composition.REPLACEMENT_TO
            or str(admission["prior_composition_fingerprint"]) != prior_fingerprint
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                "classification composition admission binding is stale"
            )
        expected_counts = composition._parse_label_counts(
            str(admission["expected_prior_label_counts_json"])
        )
        items = composition._discover_items(
            connection, dataset_fingerprint, expected_order - 1, expected_counts
        )
        item_fingerprint = canonical_fingerprint(items)
        if (
            composition._stored_items(connection, str(admission["correction_id"]))
            != items
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                "classification composition item bindings do not match evidence"
            )
        if (
            int(admission["item_count"]) != len(items)
            or str(admission["correction_set_fingerprint"]) != item_fingerprint
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                "classification composition set fingerprint mismatch"
            )
        individual.append((str(admission["correction_id"]), item_fingerprint))
        prior_fingerprint = composition._composed_fingerprint(tuple(individual))
        if str(admission["composed_correction_set_fingerprint"]) != prior_fingerprint:
            raise legacy.ShortlistClassificationCorrectionError(
                "composed classification fingerprint mismatch"
            )
    return individual


def _stored_items(
    connection: sqlite3.Connection, correction_id: str
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            """SELECT source_key, source_file_sha256, source_sheet_name,
                      source_row_number, source_scope, snapshot_date, isin,
                      expected_original_asset_class,
                      expected_original_sub_asset_class,
                      expected_prior_effective_asset_class,
                      expected_prior_effective_sub_asset_class,
                      effective_asset_class, effective_sub_asset_class,
                      application_order
               FROM shortlist_classification_pair_mapping_item
               WHERE correction_id=? ORDER BY source_key""",
            (correction_id,),
        )
    )


def _validate_replay(
    connection: sqlite3.Connection,
    request: ClassificationPairMappingRequest,
    existing: sqlite3.Row,
) -> legacy.ClassificationCorrectionResult:
    validate_pair_mapping_corrections(connection)
    manifest = request.manifest
    expected = {
        "contract_version": CONTRACT_VERSION,
        "dataset_fingerprint": request.dataset_fingerprint,
        "integration_version": INTEGRATION_VERSION,
        "initial_target_sha256": request.initial_target_sha256,
        "authorization_reference": request.authorization_reference,
        "reason": request.reason,
        "scope": SCOPE,
        "source_scope": SOURCE_SCOPE,
        "mapping_id": manifest.mapping_id,
        "mapping_manifest_sha256": manifest.manifest_sha256,
        "mapping_fingerprint": manifest.mapping_fingerprint,
        "mapping_manifest_json": canonical_json(manifest.to_dict()),
        "expected_snapshot_group_effects_json": _group_effects_json(manifest),
    }
    if any(str(existing[key]) != str(value) for key, value in expected.items()):
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping replay bindings do not exactly match"
        )
    _validate_admission_counts(existing, manifest)
    return legacy.ClassificationCorrectionResult(
        correction_id=request.correction_id,
        dataset_fingerprint=request.dataset_fingerprint,
        correction_set_fingerprint=str(existing["correction_set_fingerprint"]),
        item_count=int(existing["item_count"]),
        replayed=True,
    )


def _existing_admission(
    connection: sqlite3.Connection, correction_id: str
) -> sqlite3.Row | None:
    if not composition._pair_mapping_extension_present(connection):
        return None
    rows = connection.execute(
        "SELECT * FROM shortlist_classification_pair_mapping_admission "
        "WHERE correction_id=?",
        (correction_id,),
    ).fetchall()
    if len(rows) > 1:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping identity is ambiguous"
        )
    return rows[0] if rows else None


def _validate_request(request: ClassificationPairMappingRequest) -> None:
    if not request.correction_id.strip():
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping identity is required"
        )
    for name, value in (
        ("dataset fingerprint", request.dataset_fingerprint),
        ("initial target SHA-256", request.initial_target_sha256),
        ("mapping manifest SHA-256", request.manifest.manifest_sha256),
    ):
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                f"{name} must be lowercase hexadecimal SHA-256"
            )
    if not request.authorization_reference.strip() or not request.reason.strip():
        raise legacy.ShortlistClassificationCorrectionError(
            "authorization reference and reason are required"
        )
    if request.dataset_fingerprint != request.manifest.dataset_fingerprint:
        raise legacy.ShortlistClassificationCorrectionError(
            "mapping manifest dataset fingerprint does not match request"
        )
    if request.authorization_reference != request.manifest.authorization_reference:
        raise legacy.ShortlistClassificationCorrectionError(
            "mapping manifest authorization reference does not match request"
        )


def _admission_values(
    request: ClassificationPairMappingRequest,
    application_order: int,
    prior_fingerprint: str,
    correction_set_fingerprint: str,
    composed_fingerprint: str,
    item_count: int,
) -> tuple[object, ...]:
    manifest = request.manifest
    return (
        request.correction_id,
        CONTRACT_VERSION,
        application_order,
        request.dataset_fingerprint,
        INTEGRATION_VERSION,
        request.initial_target_sha256,
        request.authorization_reference,
        request.reason,
        SCOPE,
        SOURCE_SCOPE,
        manifest.mapping_id,
        manifest.manifest_sha256,
        manifest.mapping_fingerprint,
        canonical_json(manifest.to_dict()),
        manifest.expected_occurrence_count,
        manifest.expected_mapped_occurrence_count,
        manifest.expected_prior_asset_class_count,
        manifest.expected_prior_sub_asset_class_count,
        manifest.expected_prior_pair_count,
        manifest.expected_result_asset_class_count,
        manifest.expected_result_sub_asset_class_count,
        manifest.expected_result_pair_count,
        _group_effects_json(manifest),
        prior_fingerprint,
        correction_set_fingerprint,
        composed_fingerprint,
        item_count,
    )


def _validate_admission_counts(
    admission: sqlite3.Row, manifest: PairMappingManifest
) -> None:
    expected = {
        "expected_occurrence_count": manifest.expected_occurrence_count,
        "expected_mapped_occurrence_count": manifest.expected_mapped_occurrence_count,
        "expected_prior_asset_class_count": manifest.expected_prior_asset_class_count,
        "expected_prior_sub_asset_class_count": manifest.expected_prior_sub_asset_class_count,
        "expected_prior_pair_count": manifest.expected_prior_pair_count,
        "expected_result_asset_class_count": manifest.expected_result_asset_class_count,
        "expected_result_sub_asset_class_count": manifest.expected_result_sub_asset_class_count,
        "expected_result_pair_count": manifest.expected_result_pair_count,
    }
    if any(int(admission[key]) != value for key, value in expected.items()):
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping counts are stale"
        )


def _group_effects_json(manifest: PairMappingManifest) -> str:
    return canonical_json(
        {
            "changed_transitions": [
                {
                    "after_group_count": after,
                    "before_group_count": before,
                    "snapshot_count": count,
                }
                for before, after, count in manifest.expected_changed_transitions
            ],
            "snapshot_count": manifest.expected_snapshot_count,
            "unchanged_snapshot_count": manifest.expected_unchanged_snapshot_count,
        }
    )


def _parse_manifest_payload(
    payload: object, manifest_sha256: str
) -> PairMappingManifest:
    if not isinstance(payload, dict) or set(payload) != {
        "authorization_reference",
        "dataset_fingerprint",
        "entries",
        "expected_input",
        "expected_output",
        "expected_snapshot_group_effects",
        "mapping_id",
        "schema_version",
    }:
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest structure is invalid"
        )
    expected_input = payload["expected_input"]
    expected_output = payload["expected_output"]
    effects = payload["expected_snapshot_group_effects"]
    entries_value = payload["entries"]
    if (
        not isinstance(expected_input, dict)
        or set(expected_input)
        != {
            "asset_class_count",
            "mapped_occurrence_count",
            "occurrence_count",
            "pair_count",
            "sub_asset_class_count",
        }
        or not isinstance(expected_output, dict)
        or set(expected_output)
        != {
            "asset_class_count",
            "occurrence_count",
            "pair_count",
            "sub_asset_class_count",
        }
        or not isinstance(effects, dict)
        or set(effects)
        != {"changed_transitions", "snapshot_count", "unchanged_snapshot_count"}
        or not isinstance(entries_value, list)
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest inventory is invalid"
        )
    entries: list[PairMappingEntry] = []
    entry_keys = {
        "effective_asset_class",
        "effective_sub_asset_class",
        "first_snapshot_date",
        "last_snapshot_date",
        "prior_asset_class",
        "prior_sub_asset_class",
        "reference_status",
        "row_count",
        "snapshot_count",
    }
    for value in entries_value:
        if not isinstance(value, dict) or set(value) != entry_keys:
            raise legacy.ShortlistClassificationCorrectionError(
                "pair mapping manifest entry is invalid"
            )
        entry = PairMappingEntry(
            prior_asset_class=_required_text(value["prior_asset_class"]),
            prior_sub_asset_class=_required_text(value["prior_sub_asset_class"]),
            effective_asset_class=_required_text(value["effective_asset_class"]),
            effective_sub_asset_class=_required_text(
                value["effective_sub_asset_class"]
            ),
            row_count=_positive_int(value["row_count"]),
            snapshot_count=_positive_int(value["snapshot_count"]),
            first_snapshot_date=_valid_date(value["first_snapshot_date"]),
            last_snapshot_date=_valid_date(value["last_snapshot_date"]),
            reference_status=_required_text(value["reference_status"]),
        )
        if entry.first_snapshot_date > entry.last_snapshot_date:
            raise legacy.ShortlistClassificationCorrectionError(
                "pair mapping manifest date range is invalid"
            )
        entries.append(entry)
    prior_pairs = {
        (entry.prior_asset_class, entry.prior_sub_asset_class) for entry in entries
    }
    if not entries or len(prior_pairs) != len(entries):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest prior pairs are empty or ambiguous"
        )
    changed_value = effects["changed_transitions"]
    if not isinstance(changed_value, list):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping snapshot effects are invalid"
        )
    transitions: list[tuple[int, int, int]] = []
    for value in changed_value:
        if not isinstance(value, dict) or set(value) != {
            "after_group_count",
            "before_group_count",
            "snapshot_count",
        }:
            raise legacy.ShortlistClassificationCorrectionError(
                "pair mapping snapshot transition is invalid"
            )
        transitions.append(
            (
                _positive_int(value["before_group_count"]),
                _positive_int(value["after_group_count"]),
                _positive_int(value["snapshot_count"]),
            )
        )
    if len(set(transitions)) != len(transitions):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping snapshot transitions are ambiguous"
        )
    manifest = PairMappingManifest(
        schema_version=_positive_int(payload["schema_version"]),
        mapping_id=_required_text(payload["mapping_id"]),
        authorization_reference=_required_text(payload["authorization_reference"]),
        dataset_fingerprint=_required_sha256(payload["dataset_fingerprint"]),
        expected_occurrence_count=_positive_int(expected_input["occurrence_count"]),
        expected_mapped_occurrence_count=_positive_int(
            expected_input["mapped_occurrence_count"]
        ),
        expected_prior_asset_class_count=_positive_int(
            expected_input["asset_class_count"]
        ),
        expected_prior_sub_asset_class_count=_positive_int(
            expected_input["sub_asset_class_count"]
        ),
        expected_prior_pair_count=_positive_int(expected_input["pair_count"]),
        expected_result_asset_class_count=_positive_int(
            expected_output["asset_class_count"]
        ),
        expected_result_sub_asset_class_count=_positive_int(
            expected_output["sub_asset_class_count"]
        ),
        expected_result_pair_count=_positive_int(expected_output["pair_count"]),
        expected_snapshot_count=_positive_int(effects["snapshot_count"]),
        expected_unchanged_snapshot_count=int(effects["unchanged_snapshot_count"]),
        expected_changed_transitions=tuple(sorted(transitions)),
        entries=tuple(
            sorted(
                entries,
                key=lambda item: (item.prior_asset_class, item.prior_sub_asset_class),
            )
        ),
        manifest_sha256=_required_sha256(manifest_sha256),
    )
    if manifest.schema_version != 1:
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest schema version is unsupported"
        )
    if int(expected_output["occurrence_count"]) != manifest.expected_occurrence_count:
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping output occurrence count is inconsistent"
        )
    if (
        sum(entry.row_count for entry in manifest.entries)
        != manifest.expected_mapped_occurrence_count
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping entry counts are inconsistent"
        )
    if len(manifest.entries) != manifest.expected_prior_pair_count:
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping prior pair count is inconsistent"
        )
    resulting_pairs = {
        (entry.effective_asset_class, entry.effective_sub_asset_class)
        for entry in manifest.entries
    }
    if (
        len({entry.prior_asset_class for entry in manifest.entries})
        != manifest.expected_prior_asset_class_count
        or len({entry.prior_sub_asset_class for entry in manifest.entries})
        != manifest.expected_prior_sub_asset_class_count
        or len({asset for asset, _sub in resulting_pairs})
        != manifest.expected_result_asset_class_count
        or len({sub for _asset, sub in resulting_pairs})
        != manifest.expected_result_sub_asset_class_count
        or len(resulting_pairs) != manifest.expected_result_pair_count
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping category counts are inconsistent"
        )
    if manifest.expected_unchanged_snapshot_count < 0 or (
        manifest.expected_unchanged_snapshot_count
        + sum(count for _before, _after, count in manifest.expected_changed_transitions)
        != manifest.expected_snapshot_count
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping snapshot counts are inconsistent"
        )
    return manifest


def _install_schema(connection: sqlite3.Connection) -> None:
    marker = connection.execute(
        "SELECT revision, contract_fingerprint FROM schema_feature_contract "
        "WHERE feature_id=?",
        (legacy.FEATURE_ID,),
    ).fetchall()
    if [tuple(row) for row in marker] != [
        (composition.FEATURE_REVISION, composition.FEATURE_FINGERPRINT)
    ]:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification composition marker is missing or stale"
        )
    _execute_sql(connection, _TABLE_SCHEMA_SQL)
    connection.execute("DROP VIEW v_effective_shortlist_classification")
    connection.execute("DROP VIEW v_shortlist_classification_correction_stage")
    _execute_sql(connection, _VIEW_SCHEMA_SQL)
    connection.execute(
        "UPDATE schema_feature_contract SET revision=?, contract_fingerprint=? "
        "WHERE feature_id=?",
        (FEATURE_REVISION, FEATURE_FINGERPRINT, legacy.FEATURE_ID),
    )


def _validate_schema(connection: sqlite3.Connection) -> None:
    legacy_names = legacy._SCHEMA_OBJECT_NAMES - {
        "v_effective_shortlist_classification"
    }
    expected_legacy = tuple(
        row
        for row in legacy._expected_schema_objects()
        if row[1] != "v_effective_shortlist_classification"
    )
    if composition._schema_objects(connection, legacy_names) != expected_legacy:
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification correction schema is damaged or incompatible"
        )
    v2_names = composition._NEW_OBJECT_NAMES - {
        "v_shortlist_classification_correction_stage",
        "v_effective_shortlist_classification",
    }
    expected_v2 = tuple(
        row for row in composition._expected_new_schema_objects() if row[1] in v2_names
    )
    if composition._schema_objects(connection, v2_names) != expected_v2:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification composition schema is damaged or incompatible"
        )
    if composition._schema_objects(connection, _OBJECT_NAMES) != _expected_objects():
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping schema is damaged or incompatible"
        )
    marker = connection.execute(
        "SELECT revision, contract_fingerprint FROM schema_feature_contract "
        "WHERE feature_id=?",
        (legacy.FEATURE_ID,),
    ).fetchall()
    if [tuple(row) for row in marker] != [(FEATURE_REVISION, FEATURE_FINGERPRINT)]:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping marker is missing or stale"
        )


def _execute_sql(connection: sqlite3.Connection, sql: str) -> None:
    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise legacy.ShortlistClassificationCorrectionError(
            "classification pair mapping schema SQL is incomplete"
        )


def _expected_objects() -> tuple[tuple[str, ...], ...]:
    with sqlite3.connect(":memory:") as scratch:
        _execute_sql(scratch, _TABLE_SCHEMA_SQL)
        _execute_sql(scratch, _VIEW_SCHEMA_SQL)
        return composition._schema_objects(scratch, _OBJECT_NAMES)


def _required_text(value: object) -> str:
    result = str(value).strip() if value is not None else ""
    if not result:
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest text value is empty"
        )
    return result


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest count is invalid"
        )
    if value <= 0:
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest count must be positive"
        )
    return value


def _valid_date(value: object) -> str:
    result = _required_text(value)
    try:
        date.fromisoformat(result)
    except ValueError as error:
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest date is invalid"
        ) from error
    return result


def _required_sha256(value: object) -> str:
    result = _required_text(value)
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "pair mapping manifest SHA-256 is invalid"
        )
    return result
