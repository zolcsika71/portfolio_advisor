"""Synthetic-only tests for the local, advisory-only TBSZ workflow."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.tbsz.comparison import compare_tbsz_to_recommended_portfolio
from portfolio_advisor.tbsz.models import (
    ComparisonAction,
    IdentityStatus,
    ReconciliationStatus,
    SourceCashInput,
    SourceConflictError,
    SourceDocumentInput,
    SourcePositionInput,
    TbszError,
    TransactionAction,
)
from portfolio_advisor.tbsz.reconciliation import reconcile_position_snapshots
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository
from portfolio_advisor.tbsz.source_import import (
    ALREADY_IMPORTED_IDENTICAL,
    IMPORTED,
    SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION,
    import_george_pdf_directory,
)

_RULES = Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml")
_ISIN_A = "HU0000000001"
_ISIN_B = "HU0000000002"


def _repository(tmp_path: Path) -> TbszPortfolioRepository:
    repository = TbszPortfolioRepository(tmp_path / "tbsz.sqlite")
    repository.initialize()
    return repository


def _document(
    *,
    filename: str = "synthetic.pdf",
    account: str = "TBSZ synthetic",
    source_date: date | None = None,
    market_value: Decimal | None = Decimal(100),
    market_currency: str | None = "HUF",
    isin: str | None = _ISIN_A,
    provider_name: str = "Synthetic Fund A",
    view_type: str = "POSITIONS",
    content: str = "a",
) -> SourceDocumentInput:
    return SourceDocumentInput(
        source_filename=filename,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        account_label=account,
        view_type=view_type,
        source_date=source_date,
        evidence_status="SYNTHETIC_TEST_EVIDENCE",
        positions=(
            SourcePositionInput(
                provider_name=provider_name,
                market_value=market_value,
                market_currency=market_currency,
                isin=isin,
            ),
        )
        if view_type == "POSITIONS"
        else (),
        cash=(SourceCashInput("HUF", Decimal(10)),) if view_type == "CASH" else (),
    )


def _model_database(tmp_path: Path, allocations: dict[str, Decimal]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "model.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            '''CREATE TABLE model_portfolios (
                "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
                "Allocation (%)" REAL, "Asset Class" TEXT, "Currency" TEXT,
                "Currency Risk" TEXT, "1 Year" REAL, "1Y Sharpe Ratio" REAL,
                "1Y Volatility" REAL, "Downside Risk" REAL, "Maximum Drawdown" REAL
            )'''
        )
        for isin, allocation in allocations.items():
            connection.execute(
                'INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ("2026/01/01", "Target", f"Synthetic {isin}", isin, float(allocation), "Bond", "HUF", "HUF", 1.0, 1.0, 1.0, 1.0, 1.0),
            )
    return path


def test_schema_provenance_and_unknown_fields_are_retained_as_null(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source, inserted = repository.import_source_document(
        _document(isin=None, market_value=None, market_currency=None)
    )
    assert inserted is True
    assert source.source_filename == "synthetic.pdf"
    assert source.source_type == "GEORGE_PDF"
    assert source.source_date is None
    position = repository.positions_for_snapshot(source.snapshot_id)[0]
    assert position.instrument.isin is None
    assert position.quantity is None
    assert position.unit_price is None
    assert position.market_value is None
    assert position.market_currency is None
    cash_source, _ = repository.import_source_document(
        _document(filename="cash.pdf", view_type="CASH", source_date=date(2026, 1, 2), content="cash")
    )
    assert repository.cash_for_snapshot(cash_source.snapshot_id)[0].balance == Decimal(10)


@pytest.mark.parametrize("label", ("TBSZ Normal", "TBSZ Normál"))
def test_only_tbsz_accounts_are_admitted_and_normal_is_rejected(tmp_path: Path, label: str) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(TbszError, match="Normal/Normál"):
        repository.import_source_document(_document(account=label))
    repository.import_source_document(_document(account="TBSZ valid"))
    assert [account.label for account in repository.accounts()] == ["TBSZ valid"]


def test_source_import_is_idempotent_and_conflicting_evidence_fails_closed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    document = _document()
    _, inserted = repository.import_source_document(document)
    assert inserted is True
    _, inserted_again = repository.import_source_document(document)
    assert inserted_again is False
    with pytest.raises(SourceConflictError):
        repository.import_source_document(_document(market_value=Decimal(101)))
    with pytest.raises(SourceConflictError, match="undated"):
        repository.import_source_document(_document(filename="different.pdf", market_value=Decimal(101), content="b"))
    atomic_repository = _repository(tmp_path / "atomic")
    with pytest.raises(SourceConflictError, match="undated"):
        atomic_repository.import_source_documents(
            (
                _document(filename="first.pdf", content="first"),
                _document(filename="second.pdf", market_value=Decimal(101), content="second"),
            )
        )
    assert atomic_repository.source_snapshots() == ()


def test_source_directory_requires_confirmation_before_any_import(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "screen.pdf").write_bytes(b"synthetic")
    confirmations = tmp_path / "manual.json"
    confirmations.write_text(
        json.dumps({"schema_version": 1, "documents": [{"source_filename": "screen.pdf", "manual_confirmed": False}]}),
        encoding="utf-8",
    )
    repository = _repository(tmp_path)
    result = import_george_pdf_directory(repository, source, confirmations)
    assert result.status == SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION
    assert repository.source_snapshots() == ()


def test_source_directory_import_uses_only_synthetic_confirmed_tbsz_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "screen.pdf").write_bytes(b"synthetic")
    confirmations = tmp_path / "manual.json"
    confirmations.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [
                    {
                        "source_filename": "screen.pdf",
                        "manual_confirmed": True,
                        "account_label": "TBSZ fixture",
                        "view_type": "POSITIONS",
                        "source_date": None,
                        "evidence_status": "SYNTHETIC",
                        "positions": [{"provider_name": "Synthetic", "market_value": "1", "market_currency": "HUF"}],
                        "cash": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repository = _repository(tmp_path)
    assert import_george_pdf_directory(repository, source, confirmations).status == IMPORTED
    assert import_george_pdf_directory(repository, source, confirmations).status == ALREADY_IMPORTED_IDENTICAL


def test_manual_buy_sell_records_are_append_only_and_validated(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    snapshot, _ = repository.import_source_document(_document())
    instrument_id = repository.positions_for_snapshot(snapshot.snapshot_id)[0].instrument.instrument_id
    buy = repository.record_manual_transaction(
        account_label="TBSZ synthetic",
        action=TransactionAction.BUY,
        instrument_id=instrument_id,
        quantity=Decimal(2),
        price=Decimal(10),
        currency="HUF",
        transaction_date=date(2026, 1, 2),
        client_reference="user-confirmed-buy",
    )
    repeated = repository.record_manual_transaction(
        account_label="TBSZ synthetic",
        action=TransactionAction.BUY,
        instrument_id=instrument_id,
        quantity=Decimal(2),
        price=Decimal(10),
        currency="HUF",
        transaction_date=date(2026, 1, 2),
        client_reference="user-confirmed-buy",
    )
    sell = repository.record_manual_transaction(
        account_label="TBSZ synthetic",
        action=TransactionAction.SELL,
        instrument_id=instrument_id,
        quantity=Decimal(1),
        price=Decimal(11),
        currency="HUF",
        transaction_date=date(2026, 1, 3),
    )
    assert buy.transaction_id == repeated.transaction_id
    assert [item.action for item in repository.transactions()] == [TransactionAction.BUY, TransactionAction.SELL]
    assert repository.positions_for_snapshot(snapshot.snapshot_id)[0].market_value == Decimal(100)
    with pytest.raises(TbszError, match="positive"):
        repository.record_manual_transaction(
            account_label="TBSZ synthetic",
            action=TransactionAction.SELL,
            instrument_id=instrument_id,
            quantity=Decimal(0),
            price=Decimal(1),
            currency="HUF",
            transaction_date=date(2026, 1, 3),
        )
    assert sell.action is TransactionAction.SELL


def test_exact_isin_and_reviewed_alias_are_only_identity_paths(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    direct, _ = repository.import_source_document(_document())
    direct_instrument = repository.positions_for_snapshot(direct.snapshot_id)[0].instrument
    assert direct_instrument.identity_status is IdentityStatus.EXACT_ISIN
    same_isin, _ = repository.import_source_document(
        _document(
            filename="same-isin.pdf",
            source_date=date(2026, 1, 2),
            provider_name="Provider's exact alternate name",
            content="same-isin",
        )
    )
    assert repository.positions_for_snapshot(same_isin.snapshot_id)[0].instrument.instrument_id == direct_instrument.instrument_id
    candidate, _ = repository.import_source_document(
        _document(filename="candidate.pdf", source_date=date(2026, 1, 2), isin=None, provider_name="Similar but unverified")
    )
    candidate_instrument = repository.positions_for_snapshot(candidate.snapshot_id)[0].instrument
    assert candidate_instrument.identity_status is IdentityStatus.PROVIDER_NAME_EXACT_CANDIDATE
    mapped = repository.confirm_instrument_mapping(candidate_instrument.instrument_id, _ISIN_B, "Exact reviewed alias")
    assert mapped.identity_status is IdentityStatus.MANUAL_CONFIRMED
    assert mapped.isin == _ISIN_B


def test_later_snapshot_reconciliation_is_append_only_and_fail_closed_for_unresolved_identity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first, _ = repository.import_source_document(
        _document(filename="first.pdf", source_date=date(2026, 1, 1), market_value=Decimal(100))
    )
    second, _ = repository.import_source_document(
        _document(filename="second.pdf", source_date=date(2026, 2, 1), market_value=Decimal(120), content="b")
    )
    result = reconcile_position_snapshots(
        repository,
        account_label="TBSZ synthetic",
        previous_snapshot_id=first.snapshot_id,
        later_snapshot_id=second.snapshot_id,
    )
    assert result.status is ReconciliationStatus.RECONCILIATION_DIFFERENCE
    assert len(repository.source_snapshots("TBSZ synthetic")) == 2
    unresolved, _ = repository.import_source_document(
        _document(
            filename="unresolved.pdf",
            source_date=date(2026, 3, 1),
            isin=None,
            provider_name="Unmapped source name",
            content="c",
        )
    )
    blocked = reconcile_position_snapshots(
        repository,
        account_label="TBSZ synthetic",
        previous_snapshot_id=second.snapshot_id,
        later_snapshot_id=unresolved.snapshot_id,
    )
    assert blocked.status is ReconciliationStatus.IDENTITY_UNRESOLVED


def test_comparison_emits_buy_sell_and_hold_from_explicit_tolerance(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(_document())
    model = ModelPortfolioRepository(_model_database(tmp_path, {_ISIN_A: Decimal(20), _ISIN_B: Decimal(80)}))
    result = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=model,
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal("0.01"),
    )
    assert {row.action for row in result.rows} >= {ComparisonAction.BUY, ComparisonAction.SELL}
    hold = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path / "hold", {_ISIN_A: Decimal(100)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert [row.action for row in hold.rows] == [ComparisonAction.HOLD]
    assert result.cash_by_currency == ()


def test_comparison_blocks_unresolved_identity_and_missing_fx_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(_document(isin=None))
    model = ModelPortfolioRepository(_model_database(tmp_path, {_ISIN_A: Decimal(100)}))
    unresolved = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=model,
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert any(row.action is ComparisonAction.IDENTITY_MAPPING_REQUIRED for row in unresolved.rows)
    assert not {ComparisonAction.BUY, ComparisonAction.SELL} & {row.action for row in unresolved.rows}
    repository = _repository(tmp_path / "fx")
    repository.import_source_document(_document(market_currency="EUR"))
    monkeypatch.setattr("requests.sessions.Session.request", lambda *args, **kwargs: pytest.fail("network used"))
    blocked = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path / "fx", {_ISIN_A: Decimal(100)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert blocked.rows[0].action is ComparisonAction.FX_REQUIRED_FOR_EXACT_TRADE_AMOUNT
    assert blocked.fx_blockers


def test_comparison_requires_later_pdf_reconciliation_after_manual_transaction(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source, _ = repository.import_source_document(_document())
    instrument_id = repository.positions_for_snapshot(source.snapshot_id)[0].instrument.instrument_id
    repository.record_manual_transaction(
        account_label="TBSZ synthetic",
        action=TransactionAction.BUY,
        instrument_id=instrument_id,
        quantity=Decimal(1),
        price=Decimal(1),
        currency="HUF",
        transaction_date=date(2026, 1, 2),
    )
    blocked = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path, {_ISIN_A: Decimal(100)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert blocked.manual_transaction_blockers == ("TBSZ synthetic",)
    assert {row.action for row in blocked.rows} == {ComparisonAction.INSUFFICIENT_DATA}


def test_private_paths_are_explicitly_git_ignored_and_tests_use_temporary_databases(tmp_path: Path) -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "/data/tbsz/source/" in gitignore
    assert "/database/tbsz_portfolio.sqlite" in gitignore
    repository = _repository(tmp_path)
    repository.import_source_document(_document())
    assert repository.path.parent == tmp_path
    assert repository.path.name != "tbsz_portfolio.sqlite"
