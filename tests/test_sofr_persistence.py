"""Transactional, multi-benchmark, and corruption tests for SOFR evidence."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations.sofr import (
    benchmark_stored_row_fingerprint,
    build_sofr_evidence_candidate,
)
from portfolio_advisor.database.schema.v3 import connect, initialize_schema
from portfolio_advisor.reference_rates.ecb_estr import (
    import_ecb_estr_evidence,
    validate_ecb_estr_database,
)
from portfolio_advisor.reference_rates.provenance import (
    ReferenceRateProvenanceValidationError,
    validate_reference_rate_database,
)
from portfolio_advisor.reference_rates.sofr import (
    SofrError,
    import_sofr_evidence,
    validate_sofr_database,
)
from tests.ecb_estr_support import csv_bytes as ecb_csv_bytes
from tests.ecb_estr_support import row as ecb_row
from tests.ecb_estr_support import write_evidence as write_ecb_evidence
from tests.sofr_support import construction_policy, write_evidence


class _InjectedFailure(BaseException):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _database(path: Path) -> None:
    with connect(path) as connection:
        initialize_schema(connection)
        connection.commit()


def test_offline_import_is_atomic_exact_and_byte_identical_on_replay(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sofr.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    first = import_sofr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert first.inserted_rows == 2105
    assert first.reused is False
    before = _sha256(target)
    second = import_sofr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert second.inserted_rows == 0
    assert second.reused is True
    assert _sha256(target) == before
    first_audit = validate_sofr_database(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    second_audit = validate_sofr_database(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert first_audit == second_audit
    assert first_audit["reference_rate_runtime_admission"] == (
        "USD_SOFR_BENCHMARK_SCOPED"
    )
    assert _sha256(target) == before
    assert first_audit["reference_rate_row_counts"] == {
        "reference_rate_definition": 1,
        "reference_rate_source": 1,
        "reference_rate_import_manifest": 1,
        "reference_rate_observation": 2102,
    }


def test_candidate_builder_preserves_source_schema_and_exact_estr_rows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _database(source)
    start = date(2019, 10, 1)
    ecb_rows = tuple(
        ecb_row(
            observation_date=(current := start + timedelta(days=index)).isoformat(),
            valid_from=(current + timedelta(days=1)).isoformat() + "T06:05:24Z",
        )
        for index in range(1771)
    )
    ecb_raw, ecb_receipt, _ = write_ecb_evidence(
        tmp_path,
        raw=ecb_csv_bytes(ecb_rows),
    )
    import_ecb_estr_evidence(
        target=source,
        repository_root=tmp_path,
        raw_artifact=ecb_raw,
        receipt_path=ecb_receipt,
        policy=construction_policy(),
    )
    source_hash = _sha256(source)
    estr_before = benchmark_stored_row_fingerprint(source, "ESTR")
    sofr_raw, sofr_receipt, _ = write_evidence(tmp_path)
    result = build_sofr_evidence_candidate(
        source=source,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=sofr_raw,
        receipt_path=sofr_receipt,
        policy=construction_policy(),
    )
    assert _sha256(source) == source_hash == result.source_sha256
    assert result.source_logical_fingerprint == result.candidate_logical_fingerprint
    assert result.estr_before_fingerprint == result.estr_after_fingerprint == estr_before
    assert result.candidate_reference_rate_counts == (
        ("reference_rate_definition", 2),
        ("reference_rate_import_manifest", 2),
        ("reference_rate_observation", 3873),
        ("reference_rate_source", 2),
    )
    assert _sha256(candidate) == result.candidate_sha256
    assert validate_reference_rate_database(
        target=candidate,
        repository_root=tmp_path,
        require_sofr=True,
    )["reference_rate_runtime_admission"] == "EUR_ESTR_AND_USD_SOFR"


@pytest.mark.parametrize(
    "stage",
    (
        "after_definition",
        "after_source",
        "after_manifest",
        "after_first_observation",
        "after_middle_observation",
        "after_observations",
        "before_commit",
    ),
)
def test_import_rolls_back_every_injected_failure(tmp_path: Path, stage: str) -> None:
    target = tmp_path / f"rollback-{stage}.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    before = _sha256(target)

    def fail(current: str) -> None:
        if current == stage:
            raise _InjectedFailure(current)

    with pytest.raises(_InjectedFailure):
        import_sofr_evidence(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
            failure_hook=fail,
        )
    assert _sha256(target) == before
    with sqlite3.connect(target) as connection:
        assert all(
            int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            == 0
            for table in (
                "reference_rate_definition",
                "reference_rate_source",
                "reference_rate_import_manifest",
                "reference_rate_observation",
            )
        )


def test_estr_and_sofr_coexist_with_scoped_and_complete_validation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "multi.sqlite"
    _database(target)
    ecb_raw, ecb_receipt, _ = write_ecb_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=ecb_raw,
        receipt_path=ecb_receipt,
        policy=construction_policy(),
    )
    with sqlite3.connect(target) as connection:
        ecb_before = tuple(
            connection.execute(
                """SELECT observation_date, rate_decimal, observation_fingerprint
                   FROM reference_rate_observation ORDER BY observation_date"""
            )
        )
    sofr_raw, sofr_receipt, _ = write_evidence(tmp_path)
    import_sofr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=sofr_raw,
        receipt_path=sofr_receipt,
        policy=construction_policy(),
    )
    with sqlite3.connect(target) as connection:
        ecb_id = int(
            connection.execute(
                "SELECT reference_rate_definition_id FROM reference_rate_definition WHERE benchmark_id='ESTR'"
            ).fetchone()[0]
        )
        ecb_after = tuple(
            connection.execute(
                """SELECT observation_date, rate_decimal, observation_fingerprint
                   FROM reference_rate_observation WHERE reference_rate_definition_id=?
                   ORDER BY observation_date""",
                (ecb_id,),
            )
        )
    assert ecb_after == ecb_before
    assert validate_ecb_estr_database(
        target=target,
        repository_root=tmp_path,
        raw_artifact=ecb_raw,
        receipt_path=ecb_receipt,
        policy=construction_policy(),
    )["status"] == "PASS"
    assert validate_sofr_database(
        target=target,
        repository_root=tmp_path,
        raw_artifact=sofr_raw,
        receipt_path=sofr_receipt,
        policy=construction_policy(),
    )["status"] == "PASS"
    complete = validate_reference_rate_database(
        target=target,
        repository_root=tmp_path,
        require_sofr=True,
    )
    assert complete["admitted_benchmark_ids"] == ["ESTR", "SOFR"]
    assert complete["reference_rate_runtime_admission"] == "EUR_ESTR_AND_USD_SOFR"


def test_conflicting_artifact_and_partial_schema_fail_without_writes(tmp_path: Path) -> None:
    target = tmp_path / "conflict.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_sofr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    before = _sha256(target)
    changed = raw.read_bytes().replace(b'"percentRate":2.18', b'"percentRate":2.19', 1)
    changed_raw, changed_receipt, _ = write_evidence(tmp_path, raw=changed)
    with pytest.raises(SofrError, match="differs from retained"):
        import_sofr_evidence(
            target=target,
            repository_root=tmp_path,
            raw_artifact=changed_raw,
            receipt_path=changed_receipt,
            policy=construction_policy(),
        )
    assert _sha256(target) == before

    partial = tmp_path / "partial.sqlite"
    _database(partial)
    with sqlite3.connect(partial) as connection:
        connection.execute("DROP INDEX reference_rate_observation_availability")
    partial_before = _sha256(partial)
    with pytest.raises(SofrError):
        import_sofr_evidence(
            target=partial,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
    assert _sha256(partial) == partial_before


def test_cross_benchmark_contamination_and_manifest_tampering_are_rejected(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tamper.sqlite"
    _database(target)
    ecb_raw, ecb_receipt, _ = write_ecb_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=ecb_raw,
        receipt_path=ecb_receipt,
        policy=construction_policy(),
    )
    sofr_raw, sofr_receipt, _ = write_evidence(tmp_path)
    import_sofr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=sofr_raw,
        receipt_path=sofr_receipt,
        policy=construction_policy(),
    )
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        ecb_source = int(
            connection.execute(
                """SELECT s.reference_rate_source_id FROM reference_rate_source s
                   JOIN reference_rate_definition d USING(reference_rate_definition_id)
                   WHERE d.benchmark_id='ESTR'"""
            ).fetchone()[0]
        )
        connection.execute(
            """UPDATE reference_rate_observation SET reference_rate_source_id=?
               WHERE reference_rate_observation_id=(
                   SELECT o.reference_rate_observation_id
                   FROM reference_rate_observation o
                   JOIN reference_rate_definition d USING(reference_rate_definition_id)
                   WHERE d.benchmark_id='SOFR' LIMIT 1
               )""",
            (ecb_source,),
        )
    with pytest.raises(ReferenceRateProvenanceValidationError):
        validate_reference_rate_database(
            target=target,
            repository_root=tmp_path,
            require_sofr=True,
        )


def test_raw_receipt_and_database_tampering_are_detected(tmp_path: Path) -> None:
    target = tmp_path / "tamper-artifact.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_sofr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    original = raw.read_bytes()
    raw.write_bytes(original + b" ")
    with pytest.raises(SofrError, match="byte count|SHA-256"):
        validate_sofr_database(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
    raw.write_bytes(original)
    receipt_bytes = receipt.read_bytes()
    receipt.write_bytes(b"{}")
    with pytest.raises(SofrError):
        validate_sofr_database(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
    receipt.write_bytes(receipt_bytes)
    with sqlite3.connect(target) as connection:
        connection.execute(
            """UPDATE reference_rate_import_manifest SET dataset_fingerprint=?
               WHERE reference_rate_definition_id=(
                   SELECT reference_rate_definition_id FROM reference_rate_definition
                   WHERE benchmark_id='SOFR'
               )""",
            ("0" * 64,),
        )
    with pytest.raises(SofrError):
        validate_sofr_database(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
