"""Provider-neutral provenance migration, corruption, and read-only audit tests."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.canonical import canonical_json
from portfolio_advisor.database.migrations.reference_rate_provenance import (
    ReferenceRateProvenanceMigrationError,
    _prepare_ecb_bundle,
    build_reference_rate_provenance_candidate,
    migrate_reference_rate_provenance_v2,
)
from portfolio_advisor.database.schema.v3 import (
    _REFERENCE_RATE_SCHEMA_SQL,
    _REFERENCE_RATE_SCHEMA_SQL_V1,
    LEGACY_REFERENCE_RATE_FEATURE_FINGERPRINT,
    LEGACY_REFERENCE_RATE_FEATURE_REVISION,
    REFERENCE_RATE_FEATURE_FINGERPRINT,
    REFERENCE_RATE_FEATURE_ID,
    connect,
    detect_reference_rate_feature_state,
    initialize_schema,
    validate_schema,
    validate_schema_for_reference_rate_migration,
)
from portfolio_advisor.reference_rates import (
    ProviderRevisionTransitionContract,
    internal_evidence_identity,
)
from portfolio_advisor.reference_rates.provenance import (
    ReferenceRateProvenanceValidationError,
    _validate_revision_chains,
    validate_reference_rate_database,
)
from tests.ecb_estr_support import write_evidence


class _InjectedFailure(BaseException):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_v1_database(
    path: Path,
    *,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
) -> None:
    prepared = _prepare_ecb_bundle(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt_path=receipt_path,
    )
    with connect(path) as connection:
        initialize_schema(connection)
        for index in (
            "reference_rate_observation_availability",
            "reference_rate_observation_provider_revision",
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
            connection.execute(f'DROP TABLE "{table}"')
        connection.execute(
            "DELETE FROM schema_feature_contract WHERE feature_id=?",
            (REFERENCE_RATE_FEATURE_ID,),
        )
        for statement in _REFERENCE_RATE_SCHEMA_SQL_V1.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            """INSERT INTO schema_feature_contract VALUES (?, ?, ?)""",
            (
                REFERENCE_RATE_FEATURE_ID,
                LEGACY_REFERENCE_RATE_FEATURE_REVISION,
                LEGACY_REFERENCE_RATE_FEATURE_FINGERPRINT,
            ),
        )
        definition = prepared.definition
        connection.execute(
            """INSERT INTO reference_rate_definition VALUES
               (1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                definition.benchmark_id,
                definition.benchmark_name,
                definition.currency_code,
                definition.administrator,
                definition.series_identifier,
                definition.rate_units,
                definition.day_count_convention,
                definition.compounding_convention,
                definition.definition_version,
                prepared.old_definition_fingerprint,
            ),
        )
        source = prepared.source
        connection.execute(
            """INSERT INTO reference_rate_source VALUES
               (1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
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
               (1, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                manifest.retrieval_timestamp,
                manifest.request_url,
                canonical_json(dict(manifest.request_parameters)),
                manifest.response_content_type,
                manifest.http_status,
                manifest.raw_artifact_reference,
                manifest.raw_artifact_sha256,
                manifest.provider_dataset_version,
                manifest.import_status,
                manifest.dataset_fingerprint,
            ),
        )
        id_by_new_fingerprint: dict[str, int] = {}
        for observation_id, (item, old_fingerprint) in enumerate(
            zip(
                prepared.observations,
                prepared.old_observation_fingerprints,
                strict=True,
            ),
            start=1,
        ):
            predecessor_id = (
                id_by_new_fingerprint[item.supersedes_observation_fingerprint]
                if item.supersedes_observation_fingerprint is not None
                else None
            )
            connection.execute(
                """INSERT INTO reference_rate_observation VALUES
                   (?, 1, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation_id,
                    item.observation_date.isoformat(),
                    item.provider_publication_date.isoformat()
                    if item.provider_publication_date is not None
                    else None,
                    item.rate_decimal,
                    item.provider_revision_id,
                    item.revision_sequence,
                    predecessor_id,
                    int(item.is_current),
                    item.quality_status,
                    old_fingerprint,
                ),
            )
            id_by_new_fingerprint[item.fingerprint] = observation_id
        connection.commit()
        assert detect_reference_rate_feature_state(connection) == "V1"
        validate_schema_for_reference_rate_migration(connection)


def test_candidate_migration_preserves_ecb_and_replay_is_byte_identical(
    tmp_path: Path,
) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    source = tmp_path / "legacy.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _legacy_v1_database(
        source,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    source_hash = _sha256(source)
    result = build_reference_rate_provenance_candidate(
        source=source,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    assert _sha256(source) == source_hash
    assert result.reused is False
    assert result.dataset_fingerprint
    assert result.old_definition_fingerprint != result.new_definition_fingerprint
    assert result.source_fingerprint
    assert result.old_manifest_fingerprint != result.new_manifest_fingerprint
    assert dict(result.reference_rate_row_counts) == {
        "reference_rate_definition": 1,
        "reference_rate_import_manifest": 1,
        "reference_rate_observation": 2,
        "reference_rate_source": 1,
    }
    with connect(candidate) as connection:
        assert detect_reference_rate_feature_state(connection) == "V2"
        validate_schema(connection)
        marker = connection.execute(
            "SELECT contract_fingerprint FROM schema_feature_contract WHERE feature_id=?",
            (REFERENCE_RATE_FEATURE_ID,),
        ).fetchone()[0]
        assert marker == REFERENCE_RATE_FEATURE_FINGERPRINT
        assert connection.execute(
            "SELECT group_concat(rate_decimal, ',') FROM reference_rate_observation ORDER BY observation_date"
        ).fetchone()[0] == "2.186,2.185"
    audit = validate_reference_rate_database(target=candidate, repository_root=tmp_path)
    assert audit["status"] == "PASS"
    before_replay = _sha256(candidate)
    replay = migrate_reference_rate_provenance_v2(
        target=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    assert replay.reused is True
    assert replay.before_sha256 == replay.after_sha256 == before_replay
    assert _sha256(candidate) == before_replay


@pytest.mark.parametrize(
    "stage",
    (
        "after_legacy_rename",
        "after_v2_schema",
        "after_v2_manifest",
        "after_v2_observations",
        "before_integrity_checks",
    ),
)
def test_migration_rolls_back_every_injected_failure(tmp_path: Path, stage: str) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    target = tmp_path / f"rollback-{stage}.sqlite"
    _legacy_v1_database(
        target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    before = _sha256(target)

    def fail(current: str) -> None:
        if current == stage:
            raise _InjectedFailure(current)

    with pytest.raises(_InjectedFailure):
        migrate_reference_rate_provenance_v2(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            failure_hook=fail,
        )
    assert _sha256(target) == before
    with connect(target) as connection:
        assert detect_reference_rate_feature_state(connection) == "V1"
        validate_schema_for_reference_rate_migration(connection)


def test_partial_v1_and_v2_schemas_fail_closed(tmp_path: Path) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    legacy = tmp_path / "partial-v1.sqlite"
    _legacy_v1_database(
        legacy,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    with sqlite3.connect(legacy) as connection:
        connection.execute("DROP INDEX reference_rate_observation_date")
    with pytest.raises(ReferenceRateProvenanceMigrationError, match="partial|mixed|stale"):
        migrate_reference_rate_provenance_v2(
            target=legacy,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
        )

    clean_legacy = tmp_path / "clean-v1.sqlite"
    candidate = tmp_path / "v2.sqlite"
    _legacy_v1_database(
        clean_legacy,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    build_reference_rate_provenance_candidate(
        source=clean_legacy,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    with sqlite3.connect(candidate) as connection:
        connection.execute("DROP INDEX reference_rate_observation_availability")
    with pytest.raises(ReferenceRateProvenanceValidationError):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)


@pytest.mark.parametrize(
    ("label", "old", "new"),
    (
        (
            "missing-check",
            "revision_sequence INTEGER NOT NULL CHECK(revision_sequence > 0)",
            "revision_sequence INTEGER NOT NULL",
        ),
        (
            "missing-unique",
            "UNIQUE(reference_rate_definition_id, observation_date, revision_sequence)",
            "CHECK(1)",
        ),
        (
            "missing-foreign-key",
            "REFERENCES reference_rate_observation(reference_rate_observation_id) ON DELETE RESTRICT",
            "",
        ),
    ),
)
def test_v2_missing_constraints_fail_closed(
    tmp_path: Path, label: str, old: str, new: str
) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    legacy = tmp_path / f"legacy-{label}.sqlite"
    candidate = tmp_path / f"candidate-{label}.sqlite"
    _legacy_v1_database(
        legacy,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    build_reference_rate_provenance_candidate(
        source=legacy,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    with sqlite3.connect(candidate) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='table' AND name='reference_rate_observation'"
            ).fetchone()[0]
        )
        assert old in sql
        for index in (
            "reference_rate_observation_availability",
            "reference_rate_observation_provider_revision",
            "reference_rate_observation_current",
            "reference_rate_observation_date",
        ):
            connection.execute(f'DROP INDEX "{index}"')
        connection.execute(
            "ALTER TABLE reference_rate_observation RENAME TO reference_rate_observation_corrupt_source"
        )
        connection.execute(sql.replace(old, new, 1))
        connection.execute(
            "INSERT INTO reference_rate_observation SELECT * FROM reference_rate_observation_corrupt_source"
        )
        connection.execute("DROP TABLE reference_rate_observation_corrupt_source")
        for statement in _REFERENCE_RATE_SCHEMA_SQL.split(";"):
            if "INDEX reference_rate_observation" in statement:
                connection.execute(statement)
    with pytest.raises(ReferenceRateProvenanceValidationError):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)


@pytest.mark.parametrize("state", ("future-marker", "mixed-marker", "missing-table"))
def test_future_mixed_and_missing_v2_schema_states_fail_closed(
    tmp_path: Path, state: str
) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    legacy = tmp_path / f"legacy-{state}.sqlite"
    candidate = tmp_path / f"candidate-{state}.sqlite"
    _legacy_v1_database(
        legacy,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    build_reference_rate_provenance_candidate(
        source=legacy,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    with sqlite3.connect(candidate) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        if state == "future-marker":
            connection.execute(
                "UPDATE schema_feature_contract SET revision=99 WHERE feature_id=?",
                (REFERENCE_RATE_FEATURE_ID,),
            )
        elif state == "mixed-marker":
            connection.execute(
                "UPDATE schema_feature_contract SET revision=?, contract_fingerprint=? WHERE feature_id=?",
                (
                    LEGACY_REFERENCE_RATE_FEATURE_REVISION,
                    LEGACY_REFERENCE_RATE_FEATURE_FINGERPRINT,
                    REFERENCE_RATE_FEATURE_ID,
                ),
            )
        else:
            connection.execute("DROP TABLE reference_rate_observation")
    with pytest.raises(ReferenceRateProvenanceValidationError):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)


def test_validator_is_read_only_and_detects_artifact_manifest_and_fk_tampering(
    tmp_path: Path,
) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    legacy = tmp_path / "legacy.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _legacy_v1_database(
        legacy,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    build_reference_rate_provenance_candidate(
        source=legacy,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    before = _sha256(candidate)
    assert validate_reference_rate_database(
        target=candidate, repository_root=tmp_path
    ) == validate_reference_rate_database(target=candidate, repository_root=tmp_path)
    assert _sha256(candidate) == before

    original = raw.read_bytes()
    raw.write_bytes(original + b"tamper")
    with pytest.raises(ReferenceRateProvenanceValidationError, match="tampered"):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)
    raw.write_bytes(original)

    receipt_bytes = receipt.read_bytes()
    receipt.write_bytes(b"{}")
    with pytest.raises(ReferenceRateProvenanceValidationError):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)
    receipt.write_bytes(receipt_bytes)

    with sqlite3.connect(candidate) as connection:
        manifest_fingerprint = str(
            connection.execute(
                "SELECT manifest_fingerprint FROM reference_rate_import_manifest"
            ).fetchone()[0]
        )
        observation_fingerprint = str(
            connection.execute(
                "SELECT observation_fingerprint FROM reference_rate_observation WHERE reference_rate_observation_id=1"
            ).fetchone()[0]
        )
        connection.execute(
            "UPDATE reference_rate_import_manifest SET manifest_fingerprint=?",
            ("0" * 64,),
        )
    with pytest.raises(ReferenceRateProvenanceValidationError, match="manifest"):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)
    with sqlite3.connect(candidate) as connection:
        connection.execute(
            "UPDATE reference_rate_import_manifest SET manifest_fingerprint=?",
            (manifest_fingerprint,),
        )
        connection.execute(
            "UPDATE reference_rate_observation SET observation_fingerprint=? WHERE reference_rate_observation_id=1",
            ("0" * 64,),
        )
    with pytest.raises(ReferenceRateProvenanceValidationError, match="observation"):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)
    with sqlite3.connect(candidate) as connection:
        connection.execute(
            "UPDATE reference_rate_observation SET observation_fingerprint=? WHERE reference_rate_observation_id=1",
            (observation_fingerprint,),
        )

    with sqlite3.connect(candidate) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE reference_rate_observation SET reference_rate_source_id=999 WHERE reference_rate_observation_id=1"
        )
    with pytest.raises(ReferenceRateProvenanceValidationError):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)


def test_rejected_manifest_cannot_back_observations(tmp_path: Path) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    legacy = tmp_path / "legacy.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _legacy_v1_database(
        legacy,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    build_reference_rate_provenance_candidate(
        source=legacy,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    prepared = _prepare_ecb_bundle(
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    rejected = replace(prepared.manifest, import_status="VALIDATED_REJECTED")
    with sqlite3.connect(candidate) as connection:
        connection.execute(
            "UPDATE reference_rate_import_manifest SET import_status=?, manifest_fingerprint=?",
            (rejected.import_status, rejected.fingerprint),
        )
        for row_id, observation in enumerate(prepared.observations, start=1):
            changed = replace(
                observation,
                import_manifest_fingerprint=rejected.fingerprint,
            )
            connection.execute(
                "UPDATE reference_rate_observation SET observation_fingerprint=? WHERE reference_rate_observation_id=?",
                (changed.fingerprint, row_id),
            )
    with pytest.raises(ReferenceRateProvenanceValidationError, match="rejected"):
        validate_reference_rate_database(target=candidate, repository_root=tmp_path)


def test_symlink_inputs_and_partial_candidate_cleanup_fail_closed(tmp_path: Path) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    source = tmp_path / "legacy.sqlite"
    _legacy_v1_database(
        source,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    source_link = tmp_path / "legacy-link.sqlite"
    source_link.symlink_to(source)
    with pytest.raises(ReferenceRateProvenanceMigrationError, match="symlink"):
        build_reference_rate_provenance_candidate(
            source=source_link,
            candidate=tmp_path / "candidate.sqlite",
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
        )

    candidate = tmp_path / "valid.sqlite"
    build_reference_rate_provenance_candidate(
        source=source,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    target_link = tmp_path / "candidate-link.sqlite"
    target_link.symlink_to(candidate)
    with pytest.raises(ReferenceRateProvenanceValidationError, match="symlink"):
        validate_reference_rate_database(target=target_link, repository_root=tmp_path)

    raw_link = tmp_path / "raw-link.csv"
    raw_link.symlink_to(raw)
    with pytest.raises(ReferenceRateProvenanceMigrationError, match="symlink"):
        _prepare_ecb_bundle(
            repository_root=tmp_path,
            raw_artifact=raw_link,
            receipt_path=receipt,
        )

    incomplete = tmp_path / "incomplete.sqlite"
    incomplete.write_bytes(b"not sqlite")
    with pytest.raises(ReferenceRateProvenanceMigrationError, match="already exists"):
        build_reference_rate_provenance_candidate(
            source=source,
            candidate=incomplete,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
        )
    assert incomplete.read_bytes() == b"not sqlite"


def test_persisted_revision_chain_requires_exact_approved_contract(
    tmp_path: Path,
) -> None:
    raw, receipt, _ = write_evidence(tmp_path)
    prepared = _prepare_ecb_bundle(
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
    )
    first_manifest = prepared.manifest
    second_retrieval = "2026-09-02T12:00:00+00:00"
    second_identity = internal_evidence_identity(
        source_contract_fingerprint=first_manifest.source_contract_fingerprint,
        retrieval_timestamp=second_retrieval,
        request_url=first_manifest.request_url,
        request_parameters=first_manifest.request_parameters,
        response_content_type=first_manifest.response_content_type,
        http_status=first_manifest.http_status,
        raw_artifact_reference=first_manifest.raw_artifact_reference,
        raw_artifact_sha256=first_manifest.raw_artifact_sha256,
    )
    second_manifest = replace(
        first_manifest,
        retrieval_timestamp=second_retrieval,
        internal_evidence_identity=second_identity,
    )
    previous = replace(prepared.observations[0], is_current=False)
    contract = ProviderRevisionTransitionContract(
        contract_id="SYNTHETIC_PROVIDER_REVISION_RULE",
        contract_version="1.0.0",
        benchmark_id=previous.benchmark_id,
        source_contract_fingerprint=previous.source_contract_fingerprint,
        revision_indicator_source_field="OBS_STATUS",
        revision_indicator_value="R",
        authoritative_reference="https://example.test/revision-policy",
    )
    current = replace(
        previous,
        import_manifest_fingerprint=second_manifest.fingerprint,
        rate=previous.rate + Decimal("0.001"),
        provider_revision_id="provider-revision-2",
        provider_revision_id_source_field="REVISION_ID",
        provider_revision_indicator="R",
        provider_revision_indicator_source_field="OBS_STATUS",
        provider_revision_status="PROVIDER_EXPLICIT_REVISION",
        provider_revision_contract_id=contract.contract_id,
        provider_revision_contract_version=contract.contract_version,
        provider_revision_contract_revision_indicator_value=(
            contract.revision_indicator_value
        ),
        provider_revision_contract_authoritative_reference=contract.authoritative_reference,
        provider_revision_contract_fingerprint=contract.fingerprint,
        availability_boundary_utc="2026-09-02T06:05:24.000000Z",
        revision_sequence=2,
        supersedes_observation_fingerprint=previous.fingerprint,
        is_current=True,
    )
    observations = {1: previous, 2: current}
    manifests = {
        1: (1, 1, first_manifest),
        2: (1, 1, second_manifest),
    }
    with pytest.raises(ReferenceRateProvenanceValidationError, match="approved"):
        _validate_revision_chains(
            observations,
            manifests,
            approved_revision_contracts=(),
        )
    _validate_revision_chains(
        observations,
        manifests,
        approved_revision_contracts=(contract,),
    )
    same_manifest_current = replace(
        current,
        import_manifest_fingerprint=first_manifest.fingerprint,
        availability_boundary_utc="2026-09-01T10:00:00.000000Z",
    )
    with pytest.raises(ReferenceRateProvenanceValidationError, match="approved"):
        _validate_revision_chains(
            {1: previous, 2: same_manifest_current},
            {1: (1, 1, first_manifest)},
            approved_revision_contracts=(),
        )
    _validate_revision_chains(
        {1: previous, 2: same_manifest_current},
        {1: (1, 1, first_manifest)},
        approved_revision_contracts=(contract,),
    )
    wrong_source = replace(contract, source_contract_fingerprint="f" * 64)
    with pytest.raises(ReferenceRateProvenanceValidationError, match="approved"):
        _validate_revision_chains(
            observations,
            manifests,
            approved_revision_contracts=(wrong_source,),
        )
