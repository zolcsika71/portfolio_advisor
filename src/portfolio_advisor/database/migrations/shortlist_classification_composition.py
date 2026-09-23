"""Composable, provenance-bound effective shortlist classification corrections."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json

from . import shortlist_classification as legacy
from .shortlist_parallel import INTEGRATION_VERSION
from .shortlist_zero_null import sha256_file

CONTRACT_VERSION = 2
FEATURE_REVISION = 2
REPLACEMENT_FROM = "?"
REPLACEMENT_TO = "ő"
SCOPE = "SHORTLIST_EFFECTIVE_SUB_ASSET_CLASS"

FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "composition": "ORDERED_EXPLICIT_ADMISSIONS",
        "contract_version": CONTRACT_VERSION,
        "legacy_feature_fingerprint": legacy.FEATURE_FINGERPRINT,
        "replacement_from": REPLACEMENT_FROM,
        "replacement_to": REPLACEMENT_TO,
        "revision": FEATURE_REVISION,
        "scope": SCOPE,
        "source_header": legacy.SOURCE_HEADER,
    }
)


@dataclass(frozen=True, slots=True)
class ClassificationCompositionRequest:
    correction_id: str
    dataset_fingerprint: str
    initial_target_sha256: str
    authorization_reference: str
    reason: str
    expected_prior_label_counts: tuple[tuple[str, int], ...]


_TABLE_SCHEMA_SQL = """
CREATE TABLE shortlist_classification_composition_admission (
    correction_id TEXT PRIMARY KEY CHECK(length(trim(correction_id)) > 0),
    contract_version INTEGER NOT NULL CHECK(contract_version = 2),
    application_order INTEGER NOT NULL UNIQUE CHECK(application_order >= 2),
    dataset_fingerprint TEXT NOT NULL CHECK(length(dataset_fingerprint) = 64),
    integration_version TEXT NOT NULL,
    initial_target_sha256 TEXT NOT NULL CHECK(length(initial_target_sha256) = 64),
    authorization_reference TEXT NOT NULL CHECK(length(trim(authorization_reference)) > 0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    scope TEXT NOT NULL CHECK(scope = 'SHORTLIST_EFFECTIVE_SUB_ASSET_CLASS'),
    source_header TEXT NOT NULL CHECK(source_header = 'Aleszközosztály'),
    replacement_from TEXT NOT NULL CHECK(replacement_from = '?'),
    replacement_to TEXT NOT NULL CHECK(replacement_to = 'ő'),
    expected_prior_label_counts_json TEXT NOT NULL,
    prior_composition_fingerprint TEXT NOT NULL CHECK(length(prior_composition_fingerprint) = 64),
    correction_set_fingerprint TEXT NOT NULL UNIQUE CHECK(length(correction_set_fingerprint) = 64),
    composed_correction_set_fingerprint TEXT NOT NULL UNIQUE CHECK(length(composed_correction_set_fingerprint) = 64),
    item_count INTEGER NOT NULL CHECK(item_count > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE shortlist_classification_composition_item (
    correction_id TEXT NOT NULL REFERENCES shortlist_classification_composition_admission(correction_id),
    application_order INTEGER NOT NULL CHECK(application_order >= 2),
    source_key TEXT NOT NULL,
    source_file_sha256 TEXT NOT NULL CHECK(length(source_file_sha256) = 64),
    source_sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    source_header TEXT NOT NULL CHECK(source_header = 'Aleszközosztály'),
    snapshot_date TEXT NOT NULL,
    isin TEXT NOT NULL,
    asset_class TEXT NOT NULL CHECK(length(trim(asset_class)) > 0),
    expected_original_sub_asset_class TEXT NOT NULL,
    expected_prior_effective_sub_asset_class TEXT NOT NULL CHECK(instr(expected_prior_effective_sub_asset_class, '?') > 0),
    effective_sub_asset_class TEXT NOT NULL CHECK(instr(effective_sub_asset_class, '?') = 0),
    PRIMARY KEY(correction_id, source_key),
    UNIQUE(application_order, source_key)
);
CREATE TRIGGER shortlist_classification_composition_admission_immutable_update
BEFORE UPDATE ON shortlist_classification_composition_admission
BEGIN SELECT RAISE(ABORT, 'shortlist classification composition admissions are immutable'); END;
CREATE TRIGGER shortlist_classification_composition_admission_immutable_delete
BEFORE DELETE ON shortlist_classification_composition_admission
BEGIN SELECT RAISE(ABORT, 'shortlist classification composition admissions are immutable'); END;
CREATE TRIGGER shortlist_classification_composition_item_immutable_update
BEFORE UPDATE ON shortlist_classification_composition_item
BEGIN SELECT RAISE(ABORT, 'shortlist classification composition items are immutable'); END;
CREATE TRIGGER shortlist_classification_composition_item_immutable_delete
BEFORE DELETE ON shortlist_classification_composition_item
BEGIN SELECT RAISE(ABORT, 'shortlist classification composition items are immutable'); END;
"""

_VIEW_SCHEMA_SQL = """
CREATE VIEW v_shortlist_classification_correction_stage AS
WITH RECURSIVE
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
           composed.effective_asset_class,
           composed.original_sub_asset_class,
           CASE WHEN item.source_key IS NULL
                THEN composed.effective_sub_asset_class
                ELSE item.effective_sub_asset_class END,
           composed.conflict_status,
           CASE WHEN item.source_key IS NULL
                THEN composed.correction_id ELSE item.correction_id END,
           CASE WHEN item.source_key IS NULL
                THEN composed.correction_set_fingerprint
                ELSE admission.composed_correction_set_fingerprint END,
           admission.application_order
    FROM composed
    JOIN shortlist_classification_composition_admission AS admission
      ON admission.application_order = composed.application_order + 1
    LEFT JOIN shortlist_classification_composition_item AS item
      ON item.correction_id = admission.correction_id
     AND item.application_order = admission.application_order
     AND item.source_file_sha256 = composed.source_file_sha256
     AND item.source_sheet_name = composed.source_sheet_name
     AND item.source_row_number = composed.source_row_number
     AND item.snapshot_date = (
         SELECT snapshot_date FROM shortlist_snapshot
         WHERE shortlist_snapshot_id = composed.shortlist_snapshot_id
     )
     AND item.isin = composed.isin
     AND item.asset_class = composed.original_asset_class
     AND item.expected_original_sub_asset_class = composed.original_sub_asset_class
     AND item.expected_prior_effective_sub_asset_class = composed.effective_sub_asset_class
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
    SELECT max(candidate.application_order)
    FROM v_shortlist_classification_correction_stage AS candidate
    WHERE candidate.shortlist_entry_source_occurrence_id =
          stage.shortlist_entry_source_occurrence_id
);
"""

_NEW_OBJECT_NAMES = frozenset(
    {
        "shortlist_classification_composition_admission",
        "shortlist_classification_composition_item",
        "shortlist_classification_composition_admission_immutable_update",
        "shortlist_classification_composition_admission_immutable_delete",
        "shortlist_classification_composition_item_immutable_update",
        "shortlist_classification_composition_item_immutable_delete",
        "v_shortlist_classification_correction_stage",
        "v_effective_shortlist_classification",
    }
)


def admit_composed_classification_correction(
    path: Path, request: ClassificationCompositionRequest
) -> legacy.ClassificationCorrectionResult:
    """Append the authorized effective-label replacement atomically."""
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
        existing = _existing_admission(connection, request.correction_id)
        if existing is not None:
            result = _validate_replay(connection, request, existing)
            if sha256_file(path) != before_sha256:
                raise legacy.ShortlistClassificationCorrectionError(
                    "exact replay unexpectedly changed the database"
                )
            return result
        legacy.validate_classification_corrections(connection)
        if before_sha256 != request.initial_target_sha256:
            raise legacy.ShortlistClassificationCorrectionError(
                "initial target SHA-256 does not match authorization"
            )
        prior_order = _maximum_application_order(connection)
        application_order = prior_order + 1
        items = _discover_items(
            connection,
            request.dataset_fingerprint,
            prior_order,
            request.expected_prior_label_counts,
        )
        correction_set_fingerprint = canonical_fingerprint(items)
        prior_binding = _prefix_bindings(connection)[-1]
        composed_fingerprint = _composed_fingerprint(
            (
                *_individual_bindings(connection),
                (request.correction_id, correction_set_fingerprint),
            )
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            _install_composition_schema(connection)
            connection.execute(
                """INSERT INTO shortlist_classification_composition_admission(
                       correction_id, contract_version, application_order,
                       dataset_fingerprint, integration_version,
                       initial_target_sha256, authorization_reference, reason,
                       scope, source_header, replacement_from, replacement_to,
                       expected_prior_label_counts_json,
                       prior_composition_fingerprint, correction_set_fingerprint,
                       composed_correction_set_fingerprint, item_count
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request.correction_id,
                    CONTRACT_VERSION,
                    application_order,
                    request.dataset_fingerprint,
                    INTEGRATION_VERSION,
                    request.initial_target_sha256,
                    request.authorization_reference,
                    request.reason,
                    SCOPE,
                    legacy.SOURCE_HEADER,
                    REPLACEMENT_FROM,
                    REPLACEMENT_TO,
                    _label_counts_json(request.expected_prior_label_counts),
                    prior_binding.correction_set_fingerprint,
                    correction_set_fingerprint,
                    composed_fingerprint,
                    len(items),
                ),
            )
            connection.executemany(
                """INSERT INTO shortlist_classification_composition_item(
                       correction_id, source_key, source_file_sha256,
                       source_sheet_name, source_row_number, source_header,
                       snapshot_date, isin, asset_class,
                       expected_original_sub_asset_class,
                       expected_prior_effective_sub_asset_class,
                       effective_sub_asset_class, application_order
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ((request.correction_id, *item) for item in items),
            )
            validate_composed_classification_corrections(connection)
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


def validate_composed_classification_corrections(
    connection: sqlite3.Connection,
) -> None:
    """Validate legacy evidence plus every ordered composition admission."""
    legacy._validate_sqlite_health(connection)
    _validate_schema(connection)
    manifest = legacy._manifest(connection)
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
        or str(legacy_admission["dataset_fingerprint"])
        != str(manifest["dataset_fingerprint"])
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
    legacy_items = legacy._discover_items(
        connection, str(manifest["dataset_fingerprint"])
    )
    if (
        legacy._stored_items(connection, str(legacy_admission["correction_id"]))
        != legacy_items
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification correction item bindings do not match evidence"
        )
    if int(legacy_admission["item_count"]) != len(legacy_items) or str(
        legacy_admission["correction_set_fingerprint"]
    ) != canonical_fingerprint(legacy_items):
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification correction fingerprint is stale"
        )

    admissions = connection.execute(
        "SELECT * FROM shortlist_classification_composition_admission "
        "ORDER BY application_order"
    ).fetchall()
    if not admissions:
        raise legacy.ShortlistClassificationCorrectionError(
            "composition schema has no admission"
        )
    individual = [
        (
            str(legacy_admission["correction_id"]),
            str(legacy_admission["correction_set_fingerprint"]),
        )
    ]
    prior_fingerprint = individual[0][1]
    for expected_order, admission in enumerate(admissions, start=2):
        if (
            int(admission["contract_version"]) != CONTRACT_VERSION
            or int(admission["application_order"]) != expected_order
            or str(admission["dataset_fingerprint"])
            != str(manifest["dataset_fingerprint"])
            or str(admission["integration_version"]) != INTEGRATION_VERSION
            or str(admission["scope"]) != SCOPE
            or str(admission["source_header"]) != legacy.SOURCE_HEADER
            or str(admission["replacement_from"]) != REPLACEMENT_FROM
            or str(admission["replacement_to"]) != REPLACEMENT_TO
            or str(admission["prior_composition_fingerprint"]) != prior_fingerprint
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                "classification composition admission binding is stale"
            )
        expected_counts = _parse_label_counts(
            str(admission["expected_prior_label_counts_json"])
        )
        items = _discover_items(
            connection,
            str(manifest["dataset_fingerprint"]),
            expected_order - 1,
            expected_counts,
        )
        stored = _stored_items(connection, str(admission["correction_id"]))
        item_fingerprint = canonical_fingerprint(items)
        if stored != items:
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
        prior_fingerprint = _composed_fingerprint(tuple(individual))
        if str(admission["composed_correction_set_fingerprint"]) != prior_fingerprint:
            raise legacy.ShortlistClassificationCorrectionError(
                "composed classification fingerprint mismatch"
            )

    occurrence_count = int(
        connection.execute(
            "SELECT count(*) FROM shortlist_entry_source_occurrence"
        ).fetchone()[0]
    )
    projected = connection.execute(
        """SELECT count(*),
                  sum(CASE WHEN instr(effective_sub_asset_class, '?') > 0
                           THEN 1 ELSE 0 END)
           FROM v_effective_shortlist_classification"""
    ).fetchone()
    if projected is None or (int(projected[0]), int(projected[1] or 0)) != (
        occurrence_count,
        0,
    ):
        raise legacy.ShortlistClassificationCorrectionError(
            "effective classification composition projection is incomplete"
        )


def active_composed_classification_correction(
    connection: sqlite3.Connection,
) -> legacy.ClassificationCorrectionBinding:
    """Return the aggregate binding for all validated admissions."""
    bindings = _prefix_bindings(connection)
    if len(bindings) < 2:
        raise legacy.ShortlistClassificationCorrectionError(
            "composed classification binding is unavailable"
        )
    return bindings[-1]


def resolve_classification_binding_order(
    connection: sqlite3.Connection,
    binding: legacy.ClassificationCorrectionBinding,
    *,
    validated_state: legacy.ClassificationCorrectionBinding | None = None,
) -> int:
    """Resolve current or historical correction provenance to its exact stage."""
    if validated_state is None:
        validate_composed_classification_corrections(connection)
    else:
        legacy.ensure_classification_binding_current(connection, validated_state)
    for order, candidate in enumerate(_prefix_bindings(connection), start=1):
        if (
            candidate.correction_id == binding.correction_id
            and candidate.correction_set_fingerprint
            == binding.correction_set_fingerprint
        ):
            if validated_state is not None:
                legacy.ensure_classification_binding_current(
                    connection, validated_state
                )
            return order
    raise legacy.ShortlistClassificationCorrectionError(
        "persisted classification correction binding is stale"
    )


def _discover_items(
    connection: sqlite3.Connection,
    expected_dataset_fingerprint: str,
    prior_order: int,
    expected_prior_label_counts: tuple[tuple[str, int], ...],
) -> tuple[tuple[object, ...], ...]:
    manifest = legacy._manifest(connection)
    if str(manifest["dataset_fingerprint"]) != expected_dataset_fingerprint:
        raise legacy.ShortlistClassificationCorrectionError(
            "shortlist dataset fingerprint does not match authorization"
        )
    has_stage_view = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' "
        "AND name='v_shortlist_classification_correction_stage'"
    ).fetchone()
    if has_stage_view is None:
        if prior_order != 1:
            raise legacy.ShortlistClassificationCorrectionError(
                "classification composition stage is unavailable"
            )
        source = "v_effective_shortlist_classification AS effective"
        predicate = ""
        parameters: tuple[object, ...] = ()
    else:
        source = "v_shortlist_classification_correction_stage AS effective"
        predicate = "AND effective.application_order=?"
        parameters = (prior_order,)
    rows = connection.execute(
        f"""SELECT effective.source_file_sha256,
                   effective.source_sheet_name, effective.source_row_number,
                   occurrence.source_payload_json, snapshot.snapshot_date,
                   effective.isin, effective.original_asset_class,
                   effective.original_sub_asset_class,
                   effective.effective_sub_asset_class
            FROM {source}
            JOIN shortlist_entry_source_occurrence AS occurrence
              ON occurrence.shortlist_entry_source_occurrence_id=
                 effective.shortlist_entry_source_occurrence_id
            JOIN shortlist_snapshot AS snapshot
              ON snapshot.shortlist_snapshot_id=effective.shortlist_snapshot_id
            WHERE instr(effective.effective_sub_asset_class, ?) > 0
              {predicate}
            ORDER BY effective.source_file_sha256,
                     effective.source_sheet_name,
                     effective.source_row_number,
                     effective.isin""",
        (REPLACEMENT_FROM, *parameters),
    ).fetchall()
    actual_counts = Counter(str(row["effective_sub_asset_class"]) for row in rows)
    if actual_counts != Counter(dict(expected_prior_label_counts)):
        raise legacy.ShortlistClassificationCorrectionError(
            "effective classification inventory does not match authorization"
        )
    items: list[tuple[object, ...]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["source_payload_json"]))
        except json.JSONDecodeError as error:
            raise legacy.ShortlistClassificationCorrectionError(
                "shortlist source payload is malformed"
            ) from error
        if not isinstance(payload, dict) or (
            payload.get(legacy.SOURCE_HEADER) != row["original_sub_asset_class"]
            or payload.get(legacy.ASSET_CLASS_HEADER) != row["original_asset_class"]
            or payload.get("ISIN") != row["isin"]
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                "normalized shortlist classification does not match raw source evidence"
            )
        prior = str(row["effective_sub_asset_class"])
        effective = prior.replace(REPLACEMENT_FROM, REPLACEMENT_TO)
        if prior == effective:
            raise legacy.ShortlistClassificationCorrectionError(
                "authorized classification replacement is empty"
            )
        source_key = (
            f"SHORTLIST_CLASSIFICATION:{row['source_file_sha256']}:"
            f"{row['source_sheet_name']}:{row['source_row_number']}:"
            f"{legacy.SOURCE_HEADER}"
        )
        items.append(
            (
                source_key,
                str(row["source_file_sha256"]),
                str(row["source_sheet_name"]),
                int(row["source_row_number"]),
                legacy.SOURCE_HEADER,
                str(row["snapshot_date"]),
                str(row["isin"]),
                str(row["original_asset_class"]),
                str(row["original_sub_asset_class"]),
                prior,
                effective,
                prior_order + 1,
            )
        )
    if not items:
        raise legacy.ShortlistClassificationCorrectionError(
            "authorized classification composition set is empty"
        )
    return tuple(sorted(items))


def _stored_items(
    connection: sqlite3.Connection, correction_id: str
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            """SELECT source_key, source_file_sha256, source_sheet_name,
                      source_row_number, source_header, snapshot_date, isin,
                      asset_class, expected_original_sub_asset_class,
                      expected_prior_effective_sub_asset_class,
                      effective_sub_asset_class, application_order
               FROM shortlist_classification_composition_item
               WHERE correction_id=? ORDER BY source_key""",
            (correction_id,),
        )
    )


def _validate_replay(
    connection: sqlite3.Connection,
    request: ClassificationCompositionRequest,
    existing: sqlite3.Row,
) -> legacy.ClassificationCorrectionResult:
    validate_composed_classification_corrections(connection)
    expected = {
        "contract_version": CONTRACT_VERSION,
        "dataset_fingerprint": request.dataset_fingerprint,
        "integration_version": INTEGRATION_VERSION,
        "initial_target_sha256": request.initial_target_sha256,
        "authorization_reference": request.authorization_reference,
        "reason": request.reason,
        "scope": SCOPE,
        "source_header": legacy.SOURCE_HEADER,
        "replacement_from": REPLACEMENT_FROM,
        "replacement_to": REPLACEMENT_TO,
        "expected_prior_label_counts_json": _label_counts_json(
            request.expected_prior_label_counts
        ),
    }
    if any(str(existing[key]) != str(value) for key, value in expected.items()):
        raise legacy.ShortlistClassificationCorrectionError(
            "classification composition replay bindings do not exactly match"
        )
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
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='shortlist_classification_composition_admission'"
        ).fetchone()
        is None
    ):
        return None
    rows = connection.execute(
        "SELECT * FROM shortlist_classification_composition_admission "
        "WHERE correction_id=?",
        (correction_id,),
    ).fetchall()
    if len(rows) > 1:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification composition identity is ambiguous"
        )
    return rows[0] if rows else None


def _maximum_application_order(connection: sqlite3.Connection) -> int:
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='shortlist_classification_composition_admission'"
        ).fetchone()
        is None
    ):
        return 1
    return int(
        connection.execute(
            "SELECT coalesce(max(application_order), 1) "
            "FROM shortlist_classification_composition_admission"
        ).fetchone()[0]
    )


def _individual_bindings(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    legacy_row = connection.execute(
        "SELECT correction_id, correction_set_fingerprint "
        "FROM shortlist_classification_correction_admission"
    ).fetchone()
    if legacy_row is None:
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification admission is missing"
        )
    result = [(str(legacy_row[0]), str(legacy_row[1]))]
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='shortlist_classification_composition_admission'"
        ).fetchone()
        is not None
    ):
        result.extend(
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT correction_id, correction_set_fingerprint "
                "FROM shortlist_classification_composition_admission "
                "ORDER BY application_order"
            )
        )
    return tuple(result)


def _prefix_bindings(
    connection: sqlite3.Connection,
) -> tuple[legacy.ClassificationCorrectionBinding, ...]:
    individual = _individual_bindings(connection)
    result: list[legacy.ClassificationCorrectionBinding] = []
    for length in range(1, len(individual) + 1):
        prefix = individual[:length]
        result.append(
            legacy.ClassificationCorrectionBinding(
                correction_id="+".join(item[0] for item in prefix),
                correction_set_fingerprint=(
                    prefix[0][1] if length == 1 else _composed_fingerprint(prefix)
                ),
                application_order=length,
            )
        )
    return tuple(result)


def _composed_fingerprint(bindings: tuple[tuple[str, str], ...]) -> str:
    return canonical_fingerprint(
        {
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


def _validate_request(request: ClassificationCompositionRequest) -> None:
    if not request.correction_id.strip():
        raise legacy.ShortlistClassificationCorrectionError(
            "classification correction identity is required"
        )
    for name, value in (
        ("dataset fingerprint", request.dataset_fingerprint),
        ("initial target SHA-256", request.initial_target_sha256),
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
    labels = request.expected_prior_label_counts
    if not labels or len({label for label, _count in labels}) != len(labels):
        raise legacy.ShortlistClassificationCorrectionError(
            "expected prior classification inventory is empty or ambiguous"
        )
    for label, count in labels:
        if (
            not label
            or REPLACEMENT_FROM not in label
            or label.replace(REPLACEMENT_FROM, REPLACEMENT_TO) == label
            or count <= 0
        ):
            raise legacy.ShortlistClassificationCorrectionError(
                "expected prior classification inventory is invalid"
            )


def _label_counts_json(values: tuple[tuple[str, int], ...]) -> str:
    return canonical_json(
        [
            {
                "effective": label.replace(REPLACEMENT_FROM, REPLACEMENT_TO),
                "prior": label,
                "row_count": count,
            }
            for label, count in sorted(values)
        ]
    )


def _parse_label_counts(value: str) -> tuple[tuple[str, int], ...]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise legacy.ShortlistClassificationCorrectionError(
            "expected prior classification inventory is malformed"
        ) from error
    if not isinstance(payload, list):
        raise legacy.ShortlistClassificationCorrectionError(
            "expected prior classification inventory is malformed"
        )
    result: list[tuple[str, int]] = []
    for item in payload:
        if not isinstance(item, dict) or set(item) != {
            "effective",
            "prior",
            "row_count",
        }:
            raise legacy.ShortlistClassificationCorrectionError(
                "expected prior classification inventory is malformed"
            )
        prior = str(item["prior"])
        count = int(item["row_count"])
        if str(item["effective"]) != prior.replace(REPLACEMENT_FROM, REPLACEMENT_TO):
            raise legacy.ShortlistClassificationCorrectionError(
                "expected classification replacement is inconsistent"
            )
        result.append((prior, count))
    parsed = tuple(sorted(result))
    _validate_request(
        ClassificationCompositionRequest(
            correction_id="validation",
            dataset_fingerprint="0" * 64,
            initial_target_sha256="0" * 64,
            authorization_reference="validation",
            reason="validation",
            expected_prior_label_counts=parsed,
        )
    )
    if _label_counts_json(parsed) != value:
        raise legacy.ShortlistClassificationCorrectionError(
            "expected prior classification inventory is not canonical"
        )
    return parsed


def _install_composition_schema(connection: sqlite3.Connection) -> None:
    marker = connection.execute(
        "SELECT revision, contract_fingerprint FROM schema_feature_contract "
        "WHERE feature_id=?",
        (legacy.FEATURE_ID,),
    ).fetchall()
    if [tuple(row) for row in marker] != [
        (legacy.FEATURE_REVISION, legacy.FEATURE_FINGERPRINT)
    ]:
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification feature marker is missing or stale"
        )
    _execute_sql(connection, _TABLE_SCHEMA_SQL)
    connection.execute("DROP VIEW v_effective_shortlist_classification")
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
    if _schema_objects(connection, legacy_names) != expected_legacy:
        raise legacy.ShortlistClassificationCorrectionError(
            "legacy classification correction schema is damaged or incompatible"
        )
    if _schema_objects(connection, _NEW_OBJECT_NAMES) != _expected_new_schema_objects():
        raise legacy.ShortlistClassificationCorrectionError(
            "classification composition schema is damaged or incompatible"
        )
    marker = connection.execute(
        "SELECT revision, contract_fingerprint FROM schema_feature_contract "
        "WHERE feature_id=?",
        (legacy.FEATURE_ID,),
    ).fetchall()
    if [tuple(row) for row in marker] != [(FEATURE_REVISION, FEATURE_FINGERPRINT)]:
        raise legacy.ShortlistClassificationCorrectionError(
            "classification composition marker is missing or stale"
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
            "classification composition schema SQL is incomplete"
        )


def _schema_objects(
    connection: sqlite3.Connection, names: frozenset[str]
) -> tuple[tuple[str, ...], ...]:
    placeholders = ",".join("?" for _ in names)
    rows = connection.execute(
        f"""SELECT type, name, tbl_name, sql FROM sqlite_master
            WHERE name IN ({placeholders}) ORDER BY type, name""",
        tuple(sorted(names)),
    ).fetchall()
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]), " ".join(str(row[3]).split()))
        for row in rows
    )


def _expected_new_schema_objects() -> tuple[tuple[str, ...], ...]:
    with sqlite3.connect(":memory:") as scratch:
        _execute_sql(scratch, _TABLE_SCHEMA_SQL)
        _execute_sql(scratch, _VIEW_SCHEMA_SQL)
        return _schema_objects(scratch, _NEW_OBJECT_NAMES)
