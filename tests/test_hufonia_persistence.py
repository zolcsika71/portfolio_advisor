"""Transactional, multi-benchmark, and corruption tests for HUFONIA evidence."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations.hufonia import (
    benchmark_stored_row_fingerprint,
    build_hufonia_evidence_candidate,
)
from portfolio_advisor.database.schema.v3 import connect, initialize_schema
from portfolio_advisor.reference_rates.ecb_estr import import_ecb_estr_evidence
from portfolio_advisor.reference_rates.hufonia import (
    HufoniaError,
    import_hufonia_evidence,
    validate_hufonia_database,
)
from portfolio_advisor.reference_rates.provenance import (
    ReferenceRateProvenanceValidationError,
    validate_reference_rate_database,
)
from portfolio_advisor.reference_rates.sofr import import_sofr_evidence
from tests.ecb_estr_support import csv_bytes as ecb_csv_bytes
from tests.ecb_estr_support import row as ecb_row
from tests.ecb_estr_support import write_evidence as write_ecb_evidence
from tests.hufonia_support import (
    construction_policy,
    install_synthetic_workbook,
    write_evidence,
)
from tests.sofr_support import write_evidence as write_sofr_evidence


class _InjectedFailure(BaseException):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _database(path: Path) -> None:
    with connect(path) as connection:
        initialize_schema(connection)
        connection.commit()


def _populate_phase_c(path: Path, root: Path) -> tuple[Path, Path, Path, Path]:
    start = date(2019, 10, 1)
    ecb_rows = tuple(
        ecb_row(
            observation_date=(current := start + timedelta(days=index)).isoformat(),
            valid_from=(current + timedelta(days=1)).isoformat() + "T06:05:24Z",
        )
        for index in range(1771)
    )
    ecb_raw, ecb_receipt, _ = write_ecb_evidence(
        root,
        raw=ecb_csv_bytes(ecb_rows),
    )
    import_ecb_estr_evidence(
        target=path,
        repository_root=root,
        raw_artifact=ecb_raw,
        receipt_path=ecb_receipt,
        policy=construction_policy(),
    )
    sofr_raw, sofr_receipt, _ = write_sofr_evidence(root)
    import_sofr_evidence(
        target=path,
        repository_root=root,
        raw_artifact=sofr_raw,
        receipt_path=sofr_receipt,
        policy=construction_policy(),
    )
    return ecb_raw, ecb_receipt, sofr_raw, sofr_receipt


def test_offline_import_is_atomic_exact_and_byte_identical_on_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    target = tmp_path / "hufonia.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    first = import_hufonia_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert first.inserted_rows == 6234
    assert first.reused is False
    before = _sha256(target)
    second = import_hufonia_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert second.inserted_rows == 0
    assert second.reused is True
    assert _sha256(target) == before
    first_audit = validate_hufonia_database(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    second_audit = validate_hufonia_database(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert first_audit == second_audit
    assert _sha256(target) == before
    assert first_audit["reference_rate_row_counts"] == {
        "reference_rate_definition": 1,
        "reference_rate_source": 1,
        "reference_rate_import_manifest": 1,
        "reference_rate_observation": 6231,
    }


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
def test_import_rolls_back_every_injected_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    install_synthetic_workbook(monkeypatch)
    target = tmp_path / f"rollback-{stage}.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    before = _sha256(target)

    def fail(current: str) -> None:
        if current == stage:
            raise _InjectedFailure(current)

    with pytest.raises(_InjectedFailure):
        import_hufonia_evidence(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
            failure_hook=fail,
        )
    assert _sha256(target) == before
    with sqlite3.connect(target) as connection:
        assert sum(
            int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in (
                "reference_rate_definition",
                "reference_rate_source",
                "reference_rate_import_manifest",
                "reference_rate_observation",
            )
        ) == 0


def test_phase_d_candidate_preserves_estr_sofr_and_validates_all_three_bundles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    source = tmp_path / "source.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _database(source)
    _populate_phase_c(source, tmp_path)
    source_hash = _sha256(source)
    estr_before = benchmark_stored_row_fingerprint(source, "ESTR")
    sofr_before = benchmark_stored_row_fingerprint(source, "SOFR")
    raw, receipt, _ = write_evidence(tmp_path)
    result = build_hufonia_evidence_candidate(
        source=source,
        candidate=candidate,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    assert _sha256(source) == source_hash == result.source_sha256
    assert result.source_logical_fingerprint == result.candidate_logical_fingerprint
    assert result.estr_before_fingerprint == result.estr_after_fingerprint == estr_before
    assert result.sofr_before_fingerprint == result.sofr_after_fingerprint == sofr_before
    assert result.candidate_reference_rate_counts == (
        ("reference_rate_definition", 3),
        ("reference_rate_import_manifest", 3),
        ("reference_rate_observation", 10104),
        ("reference_rate_source", 3),
    )
    assert _sha256(candidate) == result.candidate_sha256
    complete = validate_reference_rate_database(
        target=candidate,
        repository_root=tmp_path,
        require_hufonia=True,
    )
    assert complete["admitted_benchmark_ids"] == ["ESTR", "HUFONIA", "SOFR"]
    assert complete["reference_rate_runtime_admission"] == (
        "EUR_ESTR_USD_SOFR_AND_HUF_HUFONIA"
    )


def test_complete_phase_d_scope_rejects_estr_only_and_estr_sofr(
    tmp_path: Path,
) -> None:
    target = tmp_path / "incomplete-scope.sqlite"
    _database(target)
    ecb_raw, ecb_receipt, _ = write_ecb_evidence(tmp_path)
    import_ecb_estr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=ecb_raw,
        receipt_path=ecb_receipt,
        policy=construction_policy(),
    )
    with pytest.raises(ReferenceRateProvenanceValidationError, match="admission set"):
        validate_reference_rate_database(
            target=target,
            repository_root=tmp_path,
            require_hufonia=True,
        )
    sofr_raw, sofr_receipt, _ = write_sofr_evidence(tmp_path)
    import_sofr_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=sofr_raw,
        receipt_path=sofr_receipt,
        policy=construction_policy(),
    )
    with pytest.raises(ReferenceRateProvenanceValidationError, match="admission set"):
        validate_reference_rate_database(
            target=target,
            repository_root=tmp_path,
            require_hufonia=True,
        )


def test_conflict_partial_schema_and_cross_source_contamination_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    target = tmp_path / "conflict.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_hufonia_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    before = _sha256(target)
    changed_raw, changed_receipt, _ = write_evidence(
        tmp_path,
        raw=b"DIFFERENT_SYNTHETIC_MNB_HUFONIA_BIFF8_FIXTURE_V1",
    )
    with pytest.raises(HufoniaError, match="differs from retained"):
        import_hufonia_evidence(
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
    with pytest.raises(HufoniaError):
        import_hufonia_evidence(
            target=partial,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
    assert _sha256(partial) == partial_before

    multi = tmp_path / "contamination.sqlite"
    _database(multi)
    _populate_phase_c(multi, tmp_path / "multi-evidence")
    multi_raw, multi_receipt, _ = write_evidence(tmp_path / "multi-evidence")
    import_hufonia_evidence(
        target=multi,
        repository_root=tmp_path / "multi-evidence",
        raw_artifact=multi_raw,
        receipt_path=multi_receipt,
        policy=construction_policy(),
    )
    with sqlite3.connect(multi) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        sofr_source = int(
            connection.execute(
                """SELECT s.reference_rate_source_id FROM reference_rate_source s
                   JOIN reference_rate_definition d USING(reference_rate_definition_id)
                   WHERE d.benchmark_id='SOFR'"""
            ).fetchone()[0]
        )
        connection.execute(
            """UPDATE reference_rate_observation SET reference_rate_source_id=?
               WHERE reference_rate_observation_id=(
                   SELECT o.reference_rate_observation_id
                   FROM reference_rate_observation o
                   JOIN reference_rate_definition d USING(reference_rate_definition_id)
                   WHERE d.benchmark_id='HUFONIA' LIMIT 1
               )""",
            (sofr_source,),
        )
    with pytest.raises(ReferenceRateProvenanceValidationError):
        validate_reference_rate_database(
            target=multi,
            repository_root=tmp_path / "multi-evidence",
            require_hufonia=True,
        )


def test_manifest_and_database_tampering_are_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_synthetic_workbook(monkeypatch)
    target = tmp_path / "tamper.sqlite"
    _database(target)
    raw, receipt, _ = write_evidence(tmp_path)
    import_hufonia_evidence(
        target=target,
        repository_root=tmp_path,
        raw_artifact=raw,
        receipt_path=receipt,
        policy=construction_policy(),
    )
    with sqlite3.connect(target) as connection:
        connection.execute(
            """UPDATE reference_rate_import_manifest SET dataset_fingerprint=?
               WHERE reference_rate_definition_id=(
                   SELECT reference_rate_definition_id FROM reference_rate_definition
                   WHERE benchmark_id='HUFONIA'
               )""",
            ("0" * 64,),
        )
    with pytest.raises(HufoniaError):
        validate_hufonia_database(
            target=target,
            repository_root=tmp_path,
            raw_artifact=raw,
            receipt_path=receipt,
            policy=construction_policy(),
        )
