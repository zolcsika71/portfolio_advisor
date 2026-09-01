"""Transactional ECB €STR persistence, idempotency, candidate, and audit tests."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations import build_ecb_estr_evidence_candidate
from portfolio_advisor.database.migrations.reference_rate import (
    ReferenceRateMigrationError,
    pre_reference_rate_logical_fingerprint,
    reference_rate_schema_contract,
)
from portfolio_advisor.database.schema.v3 import connect, initialize_schema
from portfolio_advisor.reference_rates import (
    EcbEstrError,
    import_ecb_estr_evidence,
    validate_ecb_estr_database,
)
from tests.ecb_estr_support import construction_policy, csv_bytes, row, write_evidence


class _InjectedFailure(BaseException):
    pass


def _database(path: Path) -> None:
    with connect(path) as connection:
        initialize_schema(connection)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        return {
            table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in (
                "reference_rate_definition",
                "reference_rate_source",
                "reference_rate_import_manifest",
                "reference_rate_observation",
            )
        }


def test_initial_import_is_exact_and_repeat_is_byte_identical_noop(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    first = import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert first.reused is False
    assert first.inserted_rows == 5
    assert first.observation_count == 2
    assert _counts(target) == {
        "reference_rate_definition": 1,
        "reference_rate_source": 1,
        "reference_rate_import_manifest": 1,
        "reference_rate_observation": 2,
    }
    with sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT rate_decimal FROM reference_rate_observation ORDER BY observation_date"
        ).fetchall() == [("2.186",), ("2.185",)]
    before = _sha256(target)
    second = import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert second.reused is True
    assert second.inserted_rows == 0
    assert second.dataset_fingerprint == first.dataset_fingerprint
    assert _sha256(target) == before


def test_conflicting_snapshot_fails_closed_without_database_change(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    conflicting_raw, conflicting_receipt, _ = write_evidence(
        tmp_path,
        raw=csv_bytes((row(value="2.190"),)),
        last_modified="Tue, 01 Sep 2026 07:05:24 GMT",
    )
    before = _sha256(target)
    with pytest.raises(EcbEstrError, match="conflict|missing or extra"):
        import_ecb_estr_evidence(
            target=target,
            repository_root=tmp_path,
            raw_artifact=conflicting_raw,
            receipt_path=conflicting_receipt,
            policy=construction_policy(),
        )
    assert _sha256(target) == before
    assert _counts(target)["reference_rate_observation"] == 2


def test_import_rejects_provider_version_unavailable_at_retrieval(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(
        tmp_path,
        retrieval_timestamp="2026-09-01T05:00:00+00:00",
        last_modified="Tue, 01 Sep 2026 04:00:00 GMT",
    )
    with pytest.raises(EcbEstrError, match="unavailable at retrieval"):
        import_ecb_estr_evidence(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
    assert sum(_counts(target).values()) == 0


@pytest.mark.parametrize(
    "failure_stage",
    [
        "after_definition",
        "after_source",
        "after_manifest",
        "after_first_observation",
        "after_middle_observation",
        "after_observations",
        "before_commit",
    ],
)
def test_injected_failure_rolls_back_all_four_tables(
    tmp_path: Path, failure_stage: str
) -> None:
    target = tmp_path / f"{failure_stage}.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path / failure_stage)

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise _InjectedFailure(stage)

    with pytest.raises(_InjectedFailure):
        import_ecb_estr_evidence(
            target=target,
            repository_root=tmp_path / failure_stage,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
            failure_hook=fail,
        )
    assert sum(_counts(target).values()) == 0


def test_read_only_validator_is_deterministic_and_detects_tampering(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    first = validate_ecb_estr_database(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    second = validate_ecb_estr_database(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert first == second
    assert first["status"] == "PASS"
    assert first["evidence_status"] == "EUR_ESTR_ADMITTED_VALIDATED"
    assert first["reference_rate_runtime_admission"] == "NO_GO"
    assert first["milestone_11_runtime"] == "IMPLEMENTED_BLOCKED_BY_DATA"
    constructed_counts = first["constructed_portfolio_row_counts"]
    assert isinstance(constructed_counts, dict)
    assert not any(constructed_counts.values())
    with connect(target) as connection:
        connection.execute(
            "UPDATE reference_rate_observation SET rate_decimal='9.999' WHERE observation_date='2026-08-31'"
        )
        connection.commit()
    with pytest.raises(EcbEstrError, match="fingerprint|conflict"):
        validate_ecb_estr_database(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )


def test_validator_rejects_non_integer_revision_sequence(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    with connect(target) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE reference_rate_observation SET revision_sequence=1.5 "
            "WHERE observation_date='2026-08-31'"
        )
        connection.commit()
    with pytest.raises(EcbEstrError, match="exact SQLite integer"):
        validate_ecb_estr_database(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )


def test_validator_rejects_raw_artifact_tampering_and_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    raw.write_bytes(raw.read_bytes() + b"\n")
    with pytest.raises(EcbEstrError, match="byte count|SHA-256"):
        validate_ecb_estr_database(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )


def test_validator_rejects_symlinked_raw_artifact(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    moved = raw.with_name("preserved.csv")
    raw.rename(moved)
    raw.symlink_to(moved)
    with pytest.raises(EcbEstrError, match="symlink"):
        validate_ecb_estr_database(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )


def test_copy_on_write_candidate_preserves_schema_and_all_preexisting_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _database(source)
    raw, receipt, _ = write_evidence(tmp_path)
    source_sha = _sha256(source)
    source_logical, source_counts = pre_reference_rate_logical_fingerprint(source)
    with connect(source) as connection:
        source_contract = reference_rate_schema_contract(connection)
    result = build_ecb_estr_evidence_candidate(
        source=source,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert result.source_sha256 == source_sha
    assert result.source_logical_fingerprint == source_logical
    assert result.candidate_base_logical_fingerprint == source_logical
    assert result.base_table_counts == source_counts
    assert _sha256(source) == source_sha
    assert result.candidate_sha256 == _sha256(candidate)
    with connect(candidate) as connection:
        assert reference_rate_schema_contract(connection) == source_contract
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert _counts(candidate)["reference_rate_observation"] == 2


def test_import_and_validator_reject_tampered_phase_a_schema_contract(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    with connect(target) as connection:
        connection.execute("DROP INDEX reference_rate_observation_date")
        connection.commit()
    with pytest.raises(EcbEstrError, match="reviewed Phase A contract"):
        import_ecb_estr_evidence(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
    assert sum(_counts(target).values()) == 0

    validated_target = tmp_path / "validated-target.sqlite"
    _database(validated_target)
    import_ecb_estr_evidence(
        target=validated_target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    with connect(validated_target) as connection:
        connection.execute("DROP INDEX reference_rate_observation_current")
        connection.commit()
    with pytest.raises(EcbEstrError, match="reviewed Phase A contract"):
        validate_ecb_estr_database(
            target=validated_target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )


def test_candidate_rejects_nonempty_phase_a_source(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    _database(source)
    raw, receipt, _ = write_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=source,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    with pytest.raises(ReferenceRateMigrationError, match="zero evidence rows"):
        build_ecb_estr_evidence_candidate(
            source=source,
            candidate=tmp_path / "candidate.sqlite",
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
    assert not (tmp_path / "candidate.sqlite").exists()
