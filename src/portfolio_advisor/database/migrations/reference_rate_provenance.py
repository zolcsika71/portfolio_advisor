"""Candidate-only migration to provider-neutral reference-rate provenance v2."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.migrations.reference_rate import (
    pre_reference_rate_logical_fingerprint,
    reference_rate_schema_contract,
)
from portfolio_advisor.database.schema.v3 import (
    _REFERENCE_RATE_SCHEMA_SQL,
    LEGACY_REFERENCE_RATE_FEATURE_FINGERPRINT,
    LEGACY_REFERENCE_RATE_FEATURE_REVISION,
    REFERENCE_RATE_FEATURE_FINGERPRINT,
    REFERENCE_RATE_FEATURE_ID,
    REFERENCE_RATE_FEATURE_REVISION,
    SchemaVersionError,
    detect_reference_rate_feature_state,
    detect_schema_version,
    validate_schema,
    validate_schema_for_reference_rate_migration,
)
from portfolio_advisor.reference_rates.contracts import (
    ReferenceRateDefinition,
    ReferenceRateImportManifest,
    ReferenceRateObservation,
    ReferenceRateSource,
)
from portfolio_advisor.reference_rates.ecb_estr import (
    EcbEstrAcquisitionReceipt,
    ParsedEcbEstrDataset,
    _manifest,
    _observation_contracts,
    _require_existing_bundle,
    ecb_estr_definition,
    ecb_estr_source,
    load_ecb_estr_receipt,
    verified_ecb_estr_artifact,
)

REFERENCE_RATE_PROVENANCE_MIGRATION_REVISION = (
    "MILESTONE_11C_PHASE_C0_REFERENCE_RATE_PROVENANCE_V2"
)
LEGACY_REFERENCE_RATE_SCHEMA_CONTRACT_FINGERPRINT = (
    "1d9cb07e1bee4bed81ebe6a58a293ea544249498f736899069452ae167b59d61"
)


class ReferenceRateProvenanceMigrationError(RuntimeError):
    """The provenance migration or its preservation proof failed closed."""


@dataclass(frozen=True, slots=True)
class ReferenceRateProvenanceMigrationResult:
    """Deterministic report for one in-place candidate migration or v2 no-op."""

    migration_revision: str
    reused: bool
    before_sha256: str
    after_sha256: str
    before_schema_contract_fingerprint: str
    after_schema_contract_fingerprint: str
    old_feature_contract_fingerprint: str
    new_feature_contract_fingerprint: str
    old_definition_fingerprint: str
    new_definition_fingerprint: str
    source_fingerprint: str
    old_manifest_fingerprint: str
    new_manifest_fingerprint: str
    old_observation_set_fingerprint: str
    new_observation_set_fingerprint: str
    dataset_fingerprint: str
    internal_evidence_identity: str
    base_logical_fingerprint: str
    reference_rate_row_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "after_schema_contract_fingerprint": self.after_schema_contract_fingerprint,
            "after_sha256": self.after_sha256,
            "base_logical_fingerprint": self.base_logical_fingerprint,
            "before_schema_contract_fingerprint": self.before_schema_contract_fingerprint,
            "before_sha256": self.before_sha256,
            "dataset_fingerprint": self.dataset_fingerprint,
            "internal_evidence_identity": self.internal_evidence_identity,
            "migration_revision": self.migration_revision,
            "new_definition_fingerprint": self.new_definition_fingerprint,
            "new_feature_contract_fingerprint": self.new_feature_contract_fingerprint,
            "new_manifest_fingerprint": self.new_manifest_fingerprint,
            "new_observation_set_fingerprint": self.new_observation_set_fingerprint,
            "old_definition_fingerprint": self.old_definition_fingerprint,
            "old_feature_contract_fingerprint": self.old_feature_contract_fingerprint,
            "old_manifest_fingerprint": self.old_manifest_fingerprint,
            "old_observation_set_fingerprint": self.old_observation_set_fingerprint,
            "reference_rate_row_counts": dict(self.reference_rate_row_counts),
            "reused": self.reused,
            "source_fingerprint": self.source_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class _PreparedEcbBundle:
    receipt: EcbEstrAcquisitionReceipt
    dataset: ParsedEcbEstrDataset
    definition: ReferenceRateDefinition
    source: ReferenceRateSource
    manifest: ReferenceRateImportManifest
    observations: tuple[ReferenceRateObservation, ...]
    old_definition_fingerprint: str
    old_manifest_fingerprint: str
    old_observation_fingerprints: tuple[str, ...]


def build_reference_rate_provenance_candidate(
    *,
    source: Path,
    candidate: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    failure_hook: Callable[[str], None] | None = None,
) -> ReferenceRateProvenanceMigrationResult:
    """Copy an exact v1 installation, then migrate only the disposable candidate."""
    if source.is_symlink() or candidate.is_symlink():
        raise ReferenceRateProvenanceMigrationError(
            "provenance migration paths must not contain symlinks"
        )
    source = source.resolve()
    candidate = candidate.resolve()
    if not source.is_file():
        raise ReferenceRateProvenanceMigrationError(
            "provenance migration source must be a regular SQLite file"
        )
    if candidate == source:
        raise ReferenceRateProvenanceMigrationError(
            "candidate must differ from the installed source"
        )
    if candidate.exists() or candidate.is_symlink():
        raise ReferenceRateProvenanceMigrationError("candidate target already exists")
    try:
        _require_no_sidecars(source)
        source_sha256 = _sha256(source)
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA query_only=ON")
            if detect_schema_version(connection) != 3:
                raise ReferenceRateProvenanceMigrationError(
                    "source is not recognized schema v3"
                )
            validate_schema_for_reference_rate_migration(connection)
            if detect_reference_rate_feature_state(connection) != "V1":
                raise ReferenceRateProvenanceMigrationError(
                    "candidate build requires the exact installed v1 reference-rate feature"
                )
            prepared = _prepare_ecb_bundle(
                repository_root=repository_root,
                raw_artifact=raw_artifact,
                receipt_path=receipt_path,
            )
            _validate_v1_ecb_bundle(connection, prepared)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(candidate) as candidate_connection:
                connection.backup(candidate_connection)
        _require_no_sidecars(source)
        if _sha256(source) != source_sha256:
            raise ReferenceRateProvenanceMigrationError(
                "installed source changed during candidate copy"
            )
        result = migrate_reference_rate_provenance_v2(
            target=candidate,
            repository_root=repository_root,
            raw_artifact=raw_artifact,
            receipt_path=receipt_path,
            failure_hook=failure_hook,
        )
        if _sha256(source) != source_sha256:
            raise ReferenceRateProvenanceMigrationError(
                "installed source changed during candidate construction"
            )
        return result
    except BaseException:
        if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
            candidate.unlink()
        raise


def migrate_reference_rate_provenance_v2(
    *,
    target: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    failure_hook: Callable[[str], None] | None = None,
) -> ReferenceRateProvenanceMigrationResult:
    """Migrate an exact candidate v1 feature transactionally; exact v2 is a no-op."""
    if target.is_symlink():
        raise ReferenceRateProvenanceMigrationError(
            "provenance migration target path must not contain symlinks"
        )
    target = target.resolve()
    if not target.is_file():
        raise ReferenceRateProvenanceMigrationError(
            "provenance migration target must be a regular SQLite file"
        )
    _require_no_sidecars(target)
    prepared = _prepare_ecb_bundle(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt_path=receipt_path,
    )
    before_sha256 = _sha256(target)
    base_before, base_counts = pre_reference_rate_logical_fingerprint(target)
    del base_counts
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            state = detect_reference_rate_feature_state(connection)
        except SchemaVersionError as error:
            raise ReferenceRateProvenanceMigrationError(str(error)) from error
        if state == "V2":
            validate_schema(connection)
            _require_existing_bundle(
                connection,
                definition=prepared.definition,
                source=prepared.source,
                manifest=prepared.manifest,
                observations=prepared.observations,
            )
            before_contract = reference_rate_schema_contract(connection)
            result = _result(
                target=target,
                prepared=prepared,
                reused=True,
                before_sha256=before_sha256,
                before_schema_contract_fingerprint=canonical_fingerprint(before_contract),
                base_logical_fingerprint=base_before,
                connection=connection,
            )
            if result.after_sha256 != before_sha256:
                raise ReferenceRateProvenanceMigrationError(
                    "exact v2 migration replay changed database bytes"
                )
            return result
        if state != "V1":
            raise ReferenceRateProvenanceMigrationError(
                "migration accepts only exact reference-rate provenance v1 or v2"
            )
        validate_schema_for_reference_rate_migration(connection)
        _validate_v1_ecb_bundle(connection, prepared)
        before_contract_fingerprint = canonical_fingerprint(
            reference_rate_schema_contract(connection)
        )
        if (
            before_contract_fingerprint
            != LEGACY_REFERENCE_RATE_SCHEMA_CONTRACT_FINGERPRINT
        ):
            raise ReferenceRateProvenanceMigrationError(
                "legacy schema-contract fingerprint differs from the reviewed v1 contract"
            )
        _rebuild_v2(connection, prepared, failure_hook=failure_hook)
        validate_schema(connection)
        _require_existing_bundle(
            connection,
            definition=prepared.definition,
            source=prepared.source,
            manifest=prepared.manifest,
            observations=prepared.observations,
        )
        _validate_preservation_projection(connection, prepared)
    base_after, _ = pre_reference_rate_logical_fingerprint(target)
    if base_after != base_before:
        raise ReferenceRateProvenanceMigrationError(
            "migration changed pre-existing non-reference-rate logical data"
        )
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        result = _result(
            target=target,
            prepared=prepared,
            reused=False,
            before_sha256=before_sha256,
            before_schema_contract_fingerprint=before_contract_fingerprint,
            base_logical_fingerprint=base_before,
            connection=connection,
        )
    return result


def _prepare_ecb_bundle(
    *, repository_root: Path, raw_artifact: Path, receipt_path: Path
) -> _PreparedEcbBundle:
    if any(
        path.is_symlink()
        for path in (repository_root, raw_artifact, receipt_path)
    ):
        raise ReferenceRateProvenanceMigrationError(
            "retained ECB provenance paths must not contain symlinks"
        )
    root = repository_root.resolve()
    raw = raw_artifact.resolve()
    receipt_file = receipt_path.resolve()
    receipt = load_ecb_estr_receipt(receipt_file)
    expected_raw = (root / PurePosixPath(receipt.raw_artifact_reference)).resolve()
    if raw != expected_raw or receipt_file != expected_raw.with_suffix(".receipt.json"):
        raise ReferenceRateProvenanceMigrationError(
            "retained ECB raw artifact and receipt paths differ from provenance"
        )
    _, dataset = verified_ecb_estr_artifact(
        repository_root=root,
        raw_artifact=raw,
        receipt=receipt,
    )
    definition = ecb_estr_definition()
    source = ecb_estr_source()
    manifest = _manifest(receipt, source, dataset)
    observations = _observation_contracts(dataset, source, manifest)
    old_definition_payload = definition.canonical_payload()
    old_definition_payload["contract_schema_version"] = 1
    old_definition_fingerprint = canonical_fingerprint(old_definition_payload)
    old_manifest_payload = {
        "dataset_fingerprint": manifest.dataset_fingerprint,
        "http_status": manifest.http_status,
        "import_status": manifest.import_status,
        "provider_dataset_version": manifest.provider_dataset_version,
        "raw_artifact_reference": manifest.raw_artifact_reference,
        "raw_artifact_sha256": manifest.raw_artifact_sha256,
        "request_parameters": dict(manifest.request_parameters),
        "request_url": manifest.request_url,
        "response_content_type": manifest.response_content_type,
        "retrieval_timestamp": manifest.retrieval_timestamp,
        "source_contract_fingerprint": manifest.source_contract_fingerprint,
    }
    old_manifest_fingerprint = canonical_fingerprint(old_manifest_payload)
    predecessor_by_date: dict[str, str] = {}
    old_observation_fingerprints: list[str] = []
    for item in observations:
        key = item.observation_date.isoformat()
        payload = {
            "benchmark_id": item.benchmark_id,
            "import_manifest_fingerprint": old_manifest_fingerprint,
            "observation_date": key,
            "provider_revision_id": item.provider_revision_id,
            "publication_date": (
                item.provider_publication_date.isoformat()
                if item.provider_publication_date is not None
                else None
            ),
            "quality_status": item.quality_status,
            "rate_decimal": item.rate_decimal,
            "revision_sequence": item.revision_sequence,
            "source_contract_fingerprint": item.source_contract_fingerprint,
            "supersedes_observation_fingerprint": predecessor_by_date.get(key),
        }
        fingerprint = canonical_fingerprint(payload)
        predecessor_by_date[key] = fingerprint
        old_observation_fingerprints.append(fingerprint)
    return _PreparedEcbBundle(
        receipt=receipt,
        dataset=dataset,
        definition=definition,
        source=source,
        manifest=manifest,
        observations=observations,
        old_definition_fingerprint=old_definition_fingerprint,
        old_manifest_fingerprint=old_manifest_fingerprint,
        old_observation_fingerprints=tuple(old_observation_fingerprints),
    )


def _validate_v1_ecb_bundle(
    connection: sqlite3.Connection, prepared: _PreparedEcbBundle
) -> None:
    definition_rows = connection.execute(
        "SELECT * FROM reference_rate_definition ORDER BY reference_rate_definition_id"
    ).fetchall()
    source_rows = connection.execute(
        "SELECT * FROM reference_rate_source ORDER BY reference_rate_source_id"
    ).fetchall()
    manifest_rows = connection.execute(
        "SELECT * FROM reference_rate_import_manifest ORDER BY reference_rate_import_manifest_id"
    ).fetchall()
    observation_rows = connection.execute(
        """SELECT * FROM reference_rate_observation
           ORDER BY observation_date, revision_sequence, reference_rate_observation_id"""
    ).fetchall()
    if (
        len(definition_rows) != 1
        or len(source_rows) != 1
        or len(manifest_rows) != 1
        or len(observation_rows) != len(prepared.observations)
    ):
        raise ReferenceRateProvenanceMigrationError(
            "legacy database does not contain exactly one complete ECB bundle"
        )
    definition = definition_rows[0]
    expected_definition = (
        1,
        prepared.definition.benchmark_id,
        prepared.definition.benchmark_name,
        prepared.definition.currency_code,
        prepared.definition.administrator,
        prepared.definition.series_identifier,
        prepared.definition.rate_units,
        prepared.definition.day_count_convention,
        prepared.definition.compounding_convention,
        prepared.definition.definition_version,
        prepared.old_definition_fingerprint,
    )
    if tuple(definition)[1:] != expected_definition:
        raise ReferenceRateProvenanceMigrationError(
            "legacy ECB definition differs from retained evidence contract"
        )
    source = source_rows[0]
    expected_source = (
        int(definition["reference_rate_definition_id"]),
        prepared.source.source_code,
        prepared.source.source_organization,
        prepared.source.official_page_url,
        prepared.source.machine_readable_url,
        prepared.source.response_format,
        prepared.source.source_role,
        prepared.source.authentication_requirement,
        prepared.source.automated_use_status,
        prepared.source.licensing_reference,
        prepared.source.raw_retention_status,
        prepared.source.fingerprint,
    )
    if tuple(source)[1:] != expected_source:
        raise ReferenceRateProvenanceMigrationError(
            "legacy ECB source differs from retained evidence contract"
        )
    manifest = manifest_rows[0]
    expected_manifest = (
        int(source["reference_rate_source_id"]),
        int(definition["reference_rate_definition_id"]),
        prepared.manifest.retrieval_timestamp,
        prepared.manifest.request_url,
        canonical_json(dict(prepared.manifest.request_parameters)),
        prepared.manifest.response_content_type,
        prepared.manifest.http_status,
        prepared.manifest.raw_artifact_reference,
        prepared.manifest.raw_artifact_sha256,
        prepared.manifest.provider_dataset_version,
        prepared.manifest.import_status,
        prepared.manifest.dataset_fingerprint,
    )
    if tuple(manifest)[1:] != expected_manifest:
        raise ReferenceRateProvenanceMigrationError(
            "legacy ECB manifest differs from retained evidence contract"
        )
    id_by_fingerprint: dict[str, int] = {}
    for row, item, old_fingerprint in zip(
        observation_rows,
        prepared.observations,
        prepared.old_observation_fingerprints,
        strict=True,
    ):
        predecessor = (
            id_by_fingerprint[item.supersedes_observation_fingerprint]
            if item.supersedes_observation_fingerprint is not None
            else None
        )
        expected = (
            int(definition["reference_rate_definition_id"]),
            int(source["reference_rate_source_id"]),
            int(manifest["reference_rate_import_manifest_id"]),
            item.observation_date.isoformat(),
            (
                item.provider_publication_date.isoformat()
                if item.provider_publication_date is not None
                else None
            ),
            item.rate_decimal,
            item.provider_revision_id,
            item.revision_sequence,
            predecessor,
            int(item.is_current),
            item.quality_status,
            old_fingerprint,
        )
        if tuple(row)[1:] != expected:
            raise ReferenceRateProvenanceMigrationError(
                "legacy ECB observation differs from retained raw evidence"
            )
        id_by_fingerprint[item.fingerprint] = int(row["reference_rate_observation_id"])


def _rebuild_v2(
    connection: sqlite3.Connection,
    prepared: _PreparedEcbBundle,
    *,
    failure_hook: Callable[[str], None] | None,
) -> None:
    definition_id = int(
        connection.execute("SELECT reference_rate_definition_id FROM reference_rate_definition").fetchone()[0]
    )
    source_id = int(
        connection.execute("SELECT reference_rate_source_id FROM reference_rate_source").fetchone()[0]
    )
    manifest_id = int(
        connection.execute(
            "SELECT reference_rate_import_manifest_id FROM reference_rate_import_manifest"
        ).fetchone()[0]
    )
    old_observation_rows = connection.execute(
        """SELECT reference_rate_observation_id, supersedes_observation_id
           FROM reference_rate_observation
           ORDER BY observation_date, revision_sequence, reference_rate_observation_id"""
    ).fetchall()
    if connection.in_transaction:
        connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
        raise ReferenceRateProvenanceMigrationError(
            "SQLite foreign keys could not be disabled for guarded reconstruction"
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        for index in (
            "reference_rate_observation_current",
            "reference_rate_observation_date",
        ):
            connection.execute(f'DROP INDEX "{index}"')
        for table in (
            "reference_rate_observation",
            "reference_rate_import_manifest",
            "reference_rate_source",
            "reference_rate_definition",
        ):
            connection.execute(f'ALTER TABLE "{table}" RENAME TO "{table}__v1"')
        _call_hook(failure_hook, "after_legacy_rename")
        for statement in _REFERENCE_RATE_SCHEMA_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)
        _call_hook(failure_hook, "after_v2_schema")
        item = prepared.definition
        connection.execute(
            """INSERT INTO reference_rate_definition VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                definition_id,
                item.contract_schema_version,
                item.benchmark_id,
                item.benchmark_name,
                item.currency_code,
                item.administrator,
                item.series_identifier,
                item.rate_units,
                item.day_count_convention,
                item.compounding_convention,
                item.definition_version,
                item.fingerprint,
            ),
        )
        source = prepared.source
        connection.execute(
            """INSERT INTO reference_rate_source VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_id,
                definition_id,
                source.source_code,
                source.source_organization,
                source.official_page_url,
                source.machine_readable_url,
                source.response_format,
                source.source_role,
                source.authentication_requirement,
                source.automated_use_status,
                source.licensing_reference,
                source.raw_retention_status,
                source.fingerprint,
            ),
        )
        manifest = prepared.manifest
        connection.execute(
            """INSERT INTO reference_rate_import_manifest VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                manifest_id,
                manifest.provenance_contract_version,
                source_id,
                definition_id,
                manifest.retrieval_timestamp,
                manifest.request_url,
                canonical_json(dict(manifest.request_parameters)),
                manifest.response_content_type,
                manifest.http_status,
                manifest.raw_artifact_reference,
                manifest.raw_artifact_sha256,
                manifest.provider_dataset_version,
                manifest.provider_dataset_version_source_field,
                manifest.internal_evidence_identity_scheme,
                manifest.internal_evidence_identity,
                manifest.import_status,
                manifest.dataset_fingerprint,
                manifest.fingerprint,
            ),
        )
        _call_hook(failure_hook, "after_v2_manifest")
        for old_row, observation in zip(
            old_observation_rows, prepared.observations, strict=True
        ):
            connection.execute(
                """INSERT INTO reference_rate_observation VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(old_row["reference_rate_observation_id"]),
                    observation.provenance_contract_version,
                    definition_id,
                    source_id,
                    manifest_id,
                    observation.observation_date.isoformat(),
                    (
                        observation.provider_publication_date.isoformat()
                        if observation.provider_publication_date is not None
                        else None
                    ),
                    observation.rate_decimal,
                    observation.provider_revision_id,
                    observation.provider_revision_id_source_field,
                    observation.provider_revision_indicator,
                    observation.provider_revision_indicator_source_field,
                    observation.provider_revision_status,
                    observation.provider_revision_contract_id,
                    observation.provider_revision_contract_version,
                    observation.provider_revision_contract_revision_indicator_value,
                    observation.provider_revision_contract_authoritative_reference,
                    observation.provider_revision_contract_fingerprint,
                    observation.provider_publication_value,
                    observation.provider_publication_value_kind,
                    observation.provider_publication_source_field,
                    observation.availability_basis,
                    observation.availability_boundary_utc,
                    observation.availability_derivation_rule_id,
                    observation.availability_derivation_rule_version,
                    observation.availability_policy_reference,
                    observation.availability_calendar_id,
                    observation.availability_calendar_version,
                    observation.availability_calendar_fingerprint,
                    observation.revision_sequence,
                    old_row["supersedes_observation_id"],
                    int(observation.is_current),
                    observation.quality_status,
                    observation.fingerprint,
                ),
            )
        _call_hook(failure_hook, "after_v2_observations")
        for table in (
            "reference_rate_observation__v1",
            "reference_rate_import_manifest__v1",
            "reference_rate_source__v1",
            "reference_rate_definition__v1",
        ):
            connection.execute(f'DROP TABLE "{table}"')
        cursor = connection.execute(
            """UPDATE schema_feature_contract
               SET revision=?, contract_fingerprint=?
               WHERE feature_id=? AND revision=? AND contract_fingerprint=?""",
            (
                REFERENCE_RATE_FEATURE_REVISION,
                REFERENCE_RATE_FEATURE_FINGERPRINT,
                REFERENCE_RATE_FEATURE_ID,
                LEGACY_REFERENCE_RATE_FEATURE_REVISION,
                LEGACY_REFERENCE_RATE_FEATURE_FINGERPRINT,
            ),
        )
        if cursor.rowcount != 1:
            raise ReferenceRateProvenanceMigrationError(
                "legacy feature marker changed during migration"
            )
        _call_hook(failure_hook, "before_integrity_checks")
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != ("ok",) or violations:
            raise ReferenceRateProvenanceMigrationError(
                "candidate integrity or foreign-key validation failed before commit"
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise ReferenceRateProvenanceMigrationError(
            "SQLite foreign keys were not restored after reconstruction"
        )


def _validate_preservation_projection(
    connection: sqlite3.Connection, prepared: _PreparedEcbBundle
) -> None:
    counts = _reference_counts(connection)
    if counts != {
        "reference_rate_definition": 1,
        "reference_rate_import_manifest": 1,
        "reference_rate_observation": len(prepared.observations),
        "reference_rate_source": 1,
    }:
        raise ReferenceRateProvenanceMigrationError(
            "reference-rate row counts changed during provenance migration"
        )
    rows = connection.execute(
        """SELECT observation_date, provider_publication_date, rate_decimal,
                  provider_revision_id, revision_sequence, supersedes_observation_id,
                  is_current, quality_status, provider_revision_indicator,
                  provider_revision_indicator_source_field
           FROM reference_rate_observation
           ORDER BY observation_date, revision_sequence, reference_rate_observation_id"""
    ).fetchall()
    for row, observation in zip(rows, prepared.observations, strict=True):
        expected = (
            observation.observation_date.isoformat(),
            (
                observation.provider_publication_date.isoformat()
                if observation.provider_publication_date is not None
                else None
            ),
            observation.rate_decimal,
            observation.provider_revision_id,
            observation.revision_sequence,
            row["supersedes_observation_id"],
            int(observation.is_current),
            observation.quality_status,
            observation.provider_revision_indicator,
            observation.provider_revision_indicator_source_field,
        )
        if tuple(row) != expected:
            raise ReferenceRateProvenanceMigrationError(
                "migrated ECB observation projection differs from retained evidence"
            )


def _result(
    *,
    target: Path,
    prepared: _PreparedEcbBundle,
    reused: bool,
    before_sha256: str,
    before_schema_contract_fingerprint: str,
    base_logical_fingerprint: str,
    connection: sqlite3.Connection,
) -> ReferenceRateProvenanceMigrationResult:
    validate_schema(connection)
    integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != ("ok",) or violations:
        raise ReferenceRateProvenanceMigrationError(
            "migrated target integrity or foreign-key validation failed"
        )
    after_contract = reference_rate_schema_contract(connection)
    return ReferenceRateProvenanceMigrationResult(
        migration_revision=REFERENCE_RATE_PROVENANCE_MIGRATION_REVISION,
        reused=reused,
        before_sha256=before_sha256,
        after_sha256=_sha256(target),
        before_schema_contract_fingerprint=before_schema_contract_fingerprint,
        after_schema_contract_fingerprint=canonical_fingerprint(after_contract),
        old_feature_contract_fingerprint=LEGACY_REFERENCE_RATE_FEATURE_FINGERPRINT,
        new_feature_contract_fingerprint=REFERENCE_RATE_FEATURE_FINGERPRINT,
        old_definition_fingerprint=prepared.old_definition_fingerprint,
        new_definition_fingerprint=prepared.definition.fingerprint,
        source_fingerprint=prepared.source.fingerprint,
        old_manifest_fingerprint=prepared.old_manifest_fingerprint,
        new_manifest_fingerprint=prepared.manifest.fingerprint,
        old_observation_set_fingerprint=canonical_fingerprint(
            list(prepared.old_observation_fingerprints)
        ),
        new_observation_set_fingerprint=canonical_fingerprint(
            [item.fingerprint for item in prepared.observations]
        ),
        dataset_fingerprint=prepared.dataset.fingerprint,
        internal_evidence_identity=prepared.manifest.internal_evidence_identity,
        base_logical_fingerprint=base_logical_fingerprint,
        reference_rate_row_counts=tuple(sorted(_reference_counts(connection).items())),
    )


def _reference_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
        for table in (
            "reference_rate_definition",
            "reference_rate_import_manifest",
            "reference_rate_observation",
            "reference_rate_source",
        )
    }


def _require_no_sidecars(target: Path) -> None:
    sidecars = tuple(
        path
        for path in (
            Path(f"{target}-journal"),
            Path(f"{target}-shm"),
            Path(f"{target}-wal"),
        )
        if path.exists()
    )
    if sidecars:
        raise ReferenceRateProvenanceMigrationError(
            "active SQLite sidecars prohibit provenance migration"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _call_hook(hook: Callable[[str], None] | None, stage: str) -> None:
    if hook is not None:
        hook(stage)
