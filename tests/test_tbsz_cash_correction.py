"""Focused synthetic tests for screenshot-backed LTIA cash corrections."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.audit.milestone_4 import audit_ltia_reconciliation
from portfolio_advisor.database.audit import audit_named_database
from portfolio_advisor.tbsz.models import (
    CashCorrectionInput,
    ScreenshotArtifactInput,
    ScreenshotArtifactRole,
    SourceCashInput,
    SourceConflictError,
    SourceDocumentInput,
    TbszError,
)
from portfolio_advisor.tbsz.repository import (
    CURRENT_SCHEMA_VERSION,
    TbszPortfolioRepository,
)
from portfolio_advisor.tbsz.screenshot_evidence import retain_screenshot_artifact

_PNG = b"\x89PNG\r\n\x1a\nsynthetic-image-bytes"


def _repository(tmp_path: Path) -> TbszPortfolioRepository:
    repository = TbszPortfolioRepository(tmp_path / "tbsz.sqlite")
    repository.initialize()
    return repository


def _cash_document(
    *,
    filename: str = "cash.pdf",
    account: str = "TBSZ synthetic",
    source_date: date | None = None,
    cash: tuple[SourceCashInput, ...] = (),
    content: bytes = b"cash",
) -> SourceDocumentInput:
    return SourceDocumentInput(
        source_filename=filename,
        content_sha256=hashlib.sha256(content).hexdigest(),
        account_label=account,
        view_type="CASH",
        source_date=source_date,
        evidence_status="SYNTHETIC_PDF_EVIDENCE",
        cash=cash,
    )


def _artifacts(
    tmp_path: Path,
    *,
    suffix: bytes = b"a",
) -> tuple[Path, tuple[ScreenshotArtifactInput, ...]]:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(parents=True)
    incoming = tmp_path / "incoming"
    incoming.mkdir(parents=True)
    label = suffix.hex()
    primary_path = incoming / f"account-{label}.png"
    crop_path = incoming / f"crop-{label}.png"
    primary_path.write_bytes(_PNG + b"-primary-" + suffix)
    crop_path.write_bytes(_PNG + b"-crop-" + suffix)
    primary_hash = hashlib.sha256(primary_path.read_bytes()).hexdigest()
    crop_hash = hashlib.sha256(crop_path.read_bytes()).hexdigest()
    primary = retain_screenshot_artifact(
        primary_path,
        evidence_root=evidence_root,
        role=ScreenshotArtifactRole.PRIMARY_ACCOUNT_CONTEXT,
        expected_sha256=primary_hash,
    )
    crop = retain_screenshot_artifact(
        crop_path,
        evidence_root=evidence_root,
        role=ScreenshotArtifactRole.SUPPORTING_CROP,
        expected_sha256=crop_hash,
    )
    return evidence_root, (primary, crop)


def _correction(
    predecessor_snapshot_id: int,
    artifacts: tuple[ScreenshotArtifactInput, ...],
    *,
    correction_id: str = "cash-correction:synthetic:1",
    account: str = "TBSZ synthetic",
    currency: str = "HUF",
    balance: Decimal = Decimal(55013),
) -> CashCorrectionInput:
    return CashCorrectionInput(
        correction_id=correction_id,
        account_label=account,
        predecessor_snapshot_id=predecessor_snapshot_id,
        reason="Synthetic authorized correction of an empty cash observation",
        source_date=None,
        evidence_status="MANUALLY_CONFIRMED_FROM_ERSTE_SCREENSHOT",
        cash=(
            SourceCashInput(
                currency,
                balance,
                "MANUALLY_CONFIRMED_FROM_ERSTE_SCREENSHOT",
            ),
        ),
        artifacts=artifacts,
    )


def _counts(path: Path) -> tuple[int, int, int, int]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM source_snapshots), "
            "(SELECT count(*) FROM cash_snapshots), "
            "(SELECT count(*) FROM screenshot_artifacts), "
            "(SELECT count(*) FROM source_snapshot_supersessions)"
        ).fetchone()
    assert row is not None
    return (int(row[0]), int(row[1]), int(row[2]), int(row[3]))


def _downgrade_to_v2(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE source_snapshot_supersessions")
        connection.execute("DROP TABLE screenshot_artifacts")
        connection.execute(
            """CREATE TABLE source_snapshots_v2 (
                snapshot_id INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL REFERENCES tbsz_accounts(account_id),
                source_filename TEXT NOT NULL UNIQUE,
                content_sha256 TEXT NOT NULL,
                source_type TEXT NOT NULL CHECK(source_type = 'GEORGE_PDF'),
                view_type TEXT NOT NULL CHECK(view_type IN ('POSITIONS', 'CASH')),
                source_date TEXT NULL,
                ingested_at TEXT NOT NULL,
                evidence_status TEXT NOT NULL,
                evidence_fingerprint TEXT NOT NULL
            )"""
        )
        connection.execute("INSERT INTO source_snapshots_v2 SELECT * FROM source_snapshots")
        connection.execute("DROP TABLE source_snapshots")
        connection.execute("ALTER TABLE source_snapshots_v2 RENAME TO source_snapshots")
        connection.execute("PRAGMA user_version = 2")


def test_v2_to_v3_migration_preserves_rows_and_creates_verified_backup(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    predecessor, _ = repository.import_source_document(_cash_document())
    before = _counts(repository.path)
    _downgrade_to_v2(repository.path)

    backup = repository.initialize()

    assert backup is not None and backup.is_file()
    assert repository.schema_version() == CURRENT_SCHEMA_VERSION == 3
    assert _counts(repository.path) == before
    assert repository.current_cash_snapshot("TBSZ synthetic") == predecessor
    with sqlite3.connect(f"file:{backup.resolve()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_cash_correction_selects_replays_exactly_and_yields_to_future_pdf(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    predecessor, _ = repository.import_source_document(_cash_document())
    evidence_root, artifacts = _artifacts(tmp_path)
    correction = _correction(predecessor.snapshot_id, artifacts)

    successor, inserted = repository.admit_cash_correction(
        correction, evidence_root=evidence_root
    )
    counts_after_insert = _counts(repository.path)
    replay, replay_inserted = repository.admit_cash_correction(
        correction, evidence_root=evidence_root
    )

    assert inserted is True
    assert replay_inserted is False
    assert replay.snapshot_id == successor.snapshot_id
    assert _counts(repository.path) == counts_after_insert
    assert repository.current_cash_snapshot("TBSZ synthetic") == successor
    selected_cash = repository.cash_for_snapshot(successor.snapshot_id)
    assert len(selected_cash) == 1
    assert selected_cash[0].currency == "HUF"
    assert selected_cash[0].balance == Decimal(55013)

    with sqlite3.connect(
        f"file:{repository.path.resolve()}?mode=ro", uri=True
    ) as connection:
        source_type, source_date = connection.execute(
            "SELECT source_type, source_date FROM source_snapshots WHERE snapshot_id = ?",
            (successor.snapshot_id,),
        ).fetchone()
        artifact_rows = connection.execute(
            "SELECT artifact_role, content_sha256 FROM screenshot_artifacts "
            "WHERE snapshot_id = ? ORDER BY artifact_role",
            (successor.snapshot_id,),
        ).fetchall()
    assert source_type == "ERSTE_SCREENSHOT"
    assert source_date is None
    assert artifact_rows == sorted(
        (artifact.role.value, artifact.content_sha256) for artifact in artifacts
    )
    audit = audit_named_database(tmp_path, repository.path.name)
    assert audit.healthy is True
    assert audit.tbsz_invariant_violations == ()
    milestone_audit = audit_ltia_reconciliation(repository.path)
    assert milestone_audit["current_projection"]["selected_snapshot_ids"] == [
        successor.snapshot_id
    ]
    assert milestone_audit["current_projection"]["cash_by_currency"] == [
        {"currency": "HUF", "current_balance_records": 1}
    ]

    later, _ = repository.import_source_document(
        _cash_document(
            filename="later-dated.pdf",
            source_date=date(2026, 1, 2),
            cash=(SourceCashInput("EUR", Decimal(7)),),
            content=b"later",
        )
    )
    assert repository.current_cash_snapshot("TBSZ synthetic") == later
    assert repository.cash_for_snapshot(later.snapshot_id)[0].currency == "EUR"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: replace(value, account_label="TBSZ other"),
        lambda value: replace(
            value,
            cash=(SourceCashInput("EUR", Decimal(55013)),),
        ),
        lambda value: replace(
            value,
            cash=(SourceCashInput("HUF", Decimal(55014)),),
        ),
        lambda value: replace(value, predecessor_snapshot_id=value.predecessor_snapshot_id + 1),
    ),
)
def test_mismatched_replay_fails_without_partial_admission(
    tmp_path: Path,
    mutation: Callable[[CashCorrectionInput], CashCorrectionInput],
) -> None:
    repository = _repository(tmp_path)
    predecessor, _ = repository.import_source_document(_cash_document())
    repository.import_source_document(
        _cash_document(
            filename="other.pdf",
            account="TBSZ other",
            source_date=date(2026, 1, 1),
            content=b"other",
        )
    )
    evidence_root, artifacts = _artifacts(tmp_path)
    correction = _correction(predecessor.snapshot_id, artifacts)
    repository.admit_cash_correction(correction, evidence_root=evidence_root)
    before = _counts(repository.path)

    with pytest.raises(SourceConflictError, match="identity already exists"):
        repository.admit_cash_correction(
            mutation(correction),
            evidence_root=evidence_root,
        )

    assert _counts(repository.path) == before


def test_changed_artifact_hash_and_competing_correction_fail_without_writes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    predecessor, _ = repository.import_source_document(_cash_document())
    evidence_root, artifacts = _artifacts(tmp_path)
    correction = _correction(predecessor.snapshot_id, artifacts)
    repository.admit_cash_correction(correction, evidence_root=evidence_root)
    before = _counts(repository.path)
    primary, crop = correction.artifacts
    changed_hash = replace(primary, content_sha256="0" * 64)

    with pytest.raises(SourceConflictError, match="hash differs"):
        repository.admit_cash_correction(
            replace(correction, artifacts=(changed_hash, crop)),
            evidence_root=evidence_root,
        )
    assert _counts(repository.path) == before

    evidence_root_2, artifacts_2 = _artifacts(tmp_path / "competing", suffix=b"b")
    with pytest.raises(SourceConflictError, match="not the currently selected"):
        repository.admit_cash_correction(
            _correction(
                predecessor.snapshot_id,
                artifacts_2,
                correction_id="cash-correction:synthetic:competing",
            ),
            evidence_root=evidence_root_2,
        )
    assert _counts(repository.path) == before


def test_retention_rejects_symlink_escape_without_creating_outside_directory(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (evidence_root / "source").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "account.png"
    source.write_bytes(_PNG)

    with pytest.raises(TbszError, match="contains a symlink"):
        retain_screenshot_artifact(
            source,
            evidence_root=evidence_root,
            role=ScreenshotArtifactRole.PRIMARY_ACCOUNT_CONTEXT,
            expected_sha256=hashlib.sha256(_PNG).hexdigest(),
        )

    assert not (outside / "screenshots").exists()


def test_corrupt_cross_account_supersession_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    predecessor, _ = repository.import_source_document(_cash_document())
    repository.import_source_document(
        _cash_document(
            filename="other.pdf",
            account="TBSZ other",
            source_date=date(2026, 1, 1),
            content=b"other",
        )
    )
    evidence_root, artifacts = _artifacts(tmp_path)
    repository.admit_cash_correction(
        _correction(predecessor.snapshot_id, artifacts), evidence_root=evidence_root
    )
    with sqlite3.connect(repository.path) as connection:
        other_account_id = connection.execute(
            "SELECT account_id FROM tbsz_accounts WHERE label = 'TBSZ other'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE source_snapshot_supersessions SET account_id = ?",
            (other_account_id,),
        )

    with pytest.raises(TbszError, match="crosses its account or CASH scope"):
        repository.current_cash_snapshot("TBSZ synthetic")


def test_invalid_predecessor_and_corrupt_cycle_are_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    predecessor, _ = repository.import_source_document(_cash_document())
    evidence_root, artifacts = _artifacts(tmp_path)
    before = _counts(repository.path)
    with pytest.raises(SourceConflictError, match="does not exist"):
        repository.admit_cash_correction(
            _correction(999, artifacts), evidence_root=evidence_root
        )
    assert _counts(repository.path) == before

    first, _ = repository.admit_cash_correction(
        _correction(predecessor.snapshot_id, artifacts), evidence_root=evidence_root
    )
    evidence_root_2, artifacts_2 = _artifacts(tmp_path / "second", suffix=b"b")
    second, _ = repository.admit_cash_correction(
        _correction(
            first.snapshot_id,
            artifacts_2,
            correction_id="cash-correction:synthetic:2",
            balance=Decimal(55014),
        ),
        evidence_root=evidence_root_2,
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE source_snapshot_supersessions SET successor_snapshot_id = ? "
            "WHERE correction_id = ?",
            (predecessor.snapshot_id, "cash-correction:synthetic:2"),
        )
    with pytest.raises(TbszError, match="cycle"):
        repository.current_cash_snapshot("TBSZ synthetic")
    assert second.snapshot_id != predecessor.snapshot_id
