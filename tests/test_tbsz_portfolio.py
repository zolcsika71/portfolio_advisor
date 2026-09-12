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
from portfolio_advisor.objectives.models import PortfolioObjective
from portfolio_advisor.tbsz.comparison import (
    DescriptiveStrategy,
    compare_tbsz_to_recommended_portfolio,
    compare_tbsz_to_selected_portfolio_descriptively,
)
from portfolio_advisor.tbsz.ltia_reconciliation import normalize_name
from portfolio_advisor.tbsz.models import (
    ComparisonAction,
    CurrentPortfolioRecordType,
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
from portfolio_advisor.tbsz.repository import (
    CURRENT_SCHEMA_VERSION,
    TbszPortfolioRepository,
    TbszSchemaMigrationError,
)
from portfolio_advisor.tbsz.service import current_portfolio_records
from portfolio_advisor.tbsz.source_import import (
    ALREADY_IMPORTED_IDENTICAL,
    IMPORTED,
    SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION,
    import_george_pdf_directory,
)
from scripts.compare_tbsz_portfolio import main as comparison_main

_RULES = Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml")
_ISIN_A = "HU0000000001"
_ISIN_B = "HU0000000002"
_VALID_ISIN_A = "US0378331005"
_VALID_ISIN_B = "US5949181045"


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
    observed_roi: Decimal | None = None,
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
                observed_roi=observed_roi,
            ),
        )
        if view_type == "POSITIONS"
        else (),
        cash=(SourceCashInput("HUF", Decimal(10)),) if view_type == "CASH" else (),
    )


def _model_database(tmp_path: Path, allocations: dict[str, Decimal]) -> Path:
    return _model_database_rows(
        tmp_path,
        tuple(
            ("Target", f"Synthetic {isin}", isin, allocation, "Bond", "HUF")
            for isin, allocation in allocations.items()
        ),
    )


def _model_database_rows(
    tmp_path: Path,
    rows: tuple[
        tuple[str, str, str | None, Decimal | None, str | None, str | None], ...
    ],
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "model.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE model_portfolios (
                "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
                "Allocation (%)" REAL, "Asset Class" TEXT, "Currency" TEXT,
                "Currency Risk" TEXT, "1 Year" REAL, "1Y Sharpe Ratio" REAL,
                "1Y Volatility" REAL, "Downside Risk" REAL, "Maximum Drawdown" REAL
            )"""
        )
        for portfolio_name, product, isin, allocation, asset_class, currency in rows:
            connection.execute(
                "INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "2026/01/01",
                    portfolio_name,
                    product,
                    isin,
                    float(allocation) if allocation is not None else None,
                    asset_class,
                    currency,
                    currency,
                    1.0,
                    1.0,
                    1.0,
                    1.0,
                    1.0,
                ),
            )
    return path


def _positions_document(
    *,
    filename: str,
    account: str,
    source_date: date,
    positions: tuple[SourcePositionInput, ...],
    content: str,
) -> SourceDocumentInput:
    return SourceDocumentInput(
        source_filename=filename,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        account_label=account,
        view_type="POSITIONS",
        source_date=source_date,
        evidence_status="SYNTHETIC_TEST_EVIDENCE",
        positions=positions,
    )


def _cash_document(
    *,
    filename: str,
    account: str,
    source_date: date,
    cash: tuple[SourceCashInput, ...],
    content: str,
) -> SourceDocumentInput:
    return SourceDocumentInput(
        source_filename=filename,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        account_label=account,
        view_type="CASH",
        source_date=source_date,
        evidence_status="SYNTHETIC_TEST_EVIDENCE",
        cash=cash,
    )


def _identity_confirmation_evidence(
    tmp_path: Path,
    *,
    source_name: str,
    isin: str,
    currency: str,
    record_overrides: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    normalized = normalize_name(source_name)
    record: dict[str, object] = {
        "isin": isin,
        "confirmed_by": "USER_APPROVED_MILESTONE_6_GATE",
        "confirmed_at": "2026-01-01T00:00:00+00:00",
        "source_identity": source_name,
        "rule": "UNIQUE_EXACT_NORMALIZED_PROVIDER_NAME",
        "candidate_count": 1,
        "source_support": "canonical_model_or_shortlist_registry",
        "currency_checked": True,
        "share_class_checked": True,
        "contradictory_product_evidence": False,
    }
    record.update(record_overrides or {})
    store_path = tmp_path / "identity_confirmations.json"
    store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mappings": {normalized: isin},
                "confirmation_records": {normalized: record},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "identity_registry_audit.json"
    registry_path.write_text(
        json.dumps(
            {
                "xls_inventory": {
                    "files": [
                        {
                            "identity_records": [
                                {
                                    "product_name": source_name,
                                    "isin": isin,
                                    "currency": currency,
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return store_path, registry_path


def _downgrade_to_v1(path: Path) -> None:
    """Create the recognized historical v1 shape from synthetic v2 evidence."""
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE position_snapshots RENAME TO position_snapshots_v2")
        connection.execute(
            """CREATE TABLE position_snapshots (
                position_id INTEGER PRIMARY KEY,
                snapshot_id INTEGER NOT NULL REFERENCES source_snapshots(snapshot_id),
                account_id INTEGER NOT NULL REFERENCES tbsz_accounts(account_id),
                instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
                provider_name TEXT NOT NULL,
                normalized_provider_name TEXT NOT NULL,
                quantity TEXT NULL,
                unit_price TEXT NULL,
                market_value TEXT NULL,
                market_currency TEXT NULL,
                reporting_value TEXT NULL,
                reporting_currency TEXT NULL,
                data_quality_status TEXT NOT NULL,
                UNIQUE(snapshot_id, normalized_provider_name)
            )"""
        )
        connection.execute(
            """INSERT INTO position_snapshots (
                position_id, snapshot_id, account_id, instrument_id, provider_name,
                normalized_provider_name, quantity, unit_price, market_value,
                market_currency, reporting_value, reporting_currency, data_quality_status
            ) SELECT
                position_id, snapshot_id, account_id, instrument_id, provider_name,
                normalized_provider_name, quantity, unit_price, market_value,
                market_currency, reporting_value, reporting_currency, data_quality_status
            FROM position_snapshots_v2"""
        )
        connection.execute("DROP TABLE position_snapshots_v2")
        connection.execute("PRAGMA user_version = 1")


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


def test_unified_current_portfolio_exposes_all_source_supported_asset_and_cash_currencies(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    position_source, _ = repository.import_source_document(
        _positions_document(
            filename="positions-current.pdf",
            account="TBSZ 2025",
            source_date=date(2026, 2, 1),
            content="current-positions",
            positions=(
                SourcePositionInput("EUR asset", Decimal(10), "EUR", isin=_ISIN_A),
                SourcePositionInput("USD asset", Decimal(20), "USD", isin=_ISIN_B),
                SourcePositionInput("HUF asset", Decimal(30), "HUF", isin="HU0000000003"),
            ),
        )
    )
    cash_source, _ = repository.import_source_document(
        _cash_document(
            filename="cash-current.pdf",
            account="TBSZ 2025",
            source_date=date(2026, 2, 2),
            content="current-cash",
            cash=(
                SourceCashInput("EUR", Decimal(1)),
                SourceCashInput("USD", Decimal(2)),
                SourceCashInput("HUF", Decimal(3)),
            ),
        )
    )

    records = current_portfolio_records(repository, "TBSZ 2025")
    assets = [item for item in records if item.record_type is CurrentPortfolioRecordType.ASSET]
    cash = [item for item in records if item.record_type is CurrentPortfolioRecordType.CASH]

    assert len(records) == 6
    assert {item.currency for item in assets} == {"EUR", "USD", "HUF"}
    assert {item.currency for item in cash} == {"EUR", "USD", "HUF"}
    assert all(item.account == "TBSZ 2025" for item in records)
    assert all(item.source_snapshot_id == position_source.snapshot_id for item in assets)
    assert all(item.source_snapshot_id == cash_source.snapshot_id for item in cash)
    assert all(item.isin is None and item.roi is None for item in cash)
    assert all(item.asset_name == "CASH" for item in cash)
    assert all(item.amount is not None for item in records)
    assert records == tuple(sorted(records, key=lambda item: (
        item.record_type.value,
        item.asset_name.casefold(),
        item.currency or "",
        item.isin or "",
        item.source_snapshot_id,
    )))


def test_unified_current_portfolio_uses_latest_snapshot_per_view_without_zero_filling_cash(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    old_positions, _ = repository.import_source_document(
        _positions_document(
            filename="positions-old.pdf",
            account="TBSZ 2024",
            source_date=date(2026, 1, 1),
            content="old-positions",
            positions=(SourcePositionInput("Old asset", Decimal(1), "EUR", isin=_ISIN_A),),
        )
    )
    current_positions, _ = repository.import_source_document(
        _positions_document(
            filename="positions-new.pdf",
            account="TBSZ 2024",
            source_date=date(2026, 2, 1),
            content="new-positions",
            positions=(SourcePositionInput("New asset", Decimal(2), "USD", isin=_ISIN_B),),
        )
    )
    repository.import_source_document(
        _cash_document(
            filename="cash-old.pdf",
            account="TBSZ 2024",
            source_date=date(2026, 1, 3),
            content="old-cash",
            cash=(SourceCashInput("EUR", Decimal(4)),),
        )
    )
    current_cash, _ = repository.import_source_document(
        _cash_document(
            filename="cash-new.pdf",
            account="TBSZ 2024",
            source_date=date(2026, 2, 3),
            content="new-cash",
            cash=(SourceCashInput("HUF", Decimal(5)), SourceCashInput("USD", Decimal(6))),
        )
    )

    records = current_portfolio_records(repository, "TBSZ 2024")

    assert old_positions.snapshot_id not in {item.source_snapshot_id for item in records}
    assert [item.asset_name for item in records if item.record_type is CurrentPortfolioRecordType.ASSET] == ["New asset"]
    assert {item.currency for item in records if item.record_type is CurrentPortfolioRecordType.CASH} == {"HUF", "USD"}
    assert all(item.source_snapshot_id == current_positions.snapshot_id for item in records if item.record_type is CurrentPortfolioRecordType.ASSET)
    assert all(item.source_snapshot_id == current_cash.snapshot_id for item in records if item.record_type is CurrentPortfolioRecordType.CASH)


def test_unified_current_portfolio_does_not_invent_missing_cash_or_roi(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(account="TBSZ 2024 (2019)", source_date=date(2026, 1, 1), observed_roi=None)
    )

    records = current_portfolio_records(repository, "TBSZ 2024 (2019)")

    assert len(records) == 1
    assert records[0].record_type is CurrentPortfolioRecordType.ASSET
    assert records[0].roi is None
    assert not [item for item in records if item.record_type is CurrentPortfolioRecordType.CASH]


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


def test_comparison_blocks_unresolved_identity_and_mixed_currency_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    repository.import_source_document(
        _positions_document(
            filename="mixed.pdf",
            account="TBSZ synthetic",
            source_date=date(2026, 1, 1),
            content="mixed",
            positions=(
                SourcePositionInput("Synthetic Fund A", Decimal(100), "EUR", isin=_ISIN_A),
                SourcePositionInput("Synthetic Fund B", Decimal(100), "HUF", isin=_ISIN_B),
            ),
        )
    )
    monkeypatch.setattr("requests.sessions.Session.request", lambda *args, **kwargs: pytest.fail("network used"))
    blocked = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path / "fx", {_ISIN_A: Decimal(50), _ISIN_B: Decimal(50)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert {row.action for row in blocked.rows} == {ComparisonAction.FX_REQUIRED_FOR_EXACT_TRADE_AMOUNT}
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


def test_v1_to_v2_migration_preserves_evidence_and_creates_verified_backup(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    snapshot, _ = repository.import_source_document(_document(source_date=date(2026, 1, 1)))
    instrument_id = repository.positions_for_snapshot(snapshot.snapshot_id)[0].instrument.instrument_id
    repository.record_manual_transaction(
        account_label="TBSZ synthetic",
        action=TransactionAction.BUY,
        instrument_id=instrument_id,
        quantity=Decimal(1),
        price=Decimal(10),
        currency="HUF",
        transaction_date=date(2026, 1, 1),
        client_reference="migration-preservation",
    )
    _downgrade_to_v1(repository.path)
    assert repository.schema_version() == 1

    backup = repository.initialize()

    assert backup is not None
    assert backup.parent == tmp_path / "backups"
    assert backup.is_file()
    assert repository.schema_version() == CURRENT_SCHEMA_VERSION == 2
    assert repository.positions_for_snapshot(snapshot.snapshot_id)[0].observed_roi is None
    assert len(repository.source_snapshots()) == 1
    assert len(repository.transactions()) == 1
    with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1


def test_v2_migration_is_idempotent_and_unknown_versions_fail_closed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(_document())
    _downgrade_to_v1(repository.path)
    backup = repository.initialize()
    assert backup is not None
    assert repository.initialize() is None
    assert len(tuple((tmp_path / "backups").glob("*.sqlite"))) == 1

    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(TbszSchemaMigrationError, match="unsupported TBSZ schema version"):
        repository.initialize()
    assert len(tuple((tmp_path / "backups").glob("*.sqlite"))) == 1


def test_comparison_reports_signed_trade_values_weights_and_current_only_sell(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _positions_document(
            filename="allocation.pdf",
            account="TBSZ synthetic",
            source_date=date(2026, 1, 1),
            content="allocation",
            positions=(
                SourcePositionInput("Fund A", Decimal(40), "HUF", isin=_ISIN_A),
                SourcePositionInput("Fund B", Decimal(60), "HUF", isin=_ISIN_B),
            ),
        )
    )
    result = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path, {_ISIN_A: Decimal(50)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal("0.01"),
    )
    rows = {row.isin: row for row in result.rows}
    assert result.comparison_currency == "HUF"
    assert result.total_comparison_value == Decimal(100)
    assert rows[_ISIN_A].current_weight == Decimal("0.4")
    assert rows[_ISIN_A].target_weight == Decimal("0.5")
    assert rows[_ISIN_A].weight_difference == Decimal("0.1")
    assert rows[_ISIN_A].target_value == Decimal(50)
    assert rows[_ISIN_A].estimated_trade_value == Decimal(10)
    assert rows[_ISIN_A].action is ComparisonAction.BUY
    assert rows[_ISIN_B].target_weight == Decimal()
    assert rows[_ISIN_B].estimated_trade_value == Decimal(-60)
    assert rows[_ISIN_B].action is ComparisonAction.SELL


def test_comparison_holds_at_explicit_tolerance_and_keeps_cash_separate(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(_document(source_date=date(2026, 1, 1), observed_roi=None))
    repository.import_source_document(
        _document(
            filename="cash.pdf",
            source_date=date(2026, 1, 1),
            view_type="CASH",
            content="cash",
        )
    )
    result = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path, {_ISIN_A: Decimal(100)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert result.total_comparison_value == Decimal(100)
    assert result.cash_by_currency[0].balance == Decimal(10)
    assert result.cash_by_currency[0].currency == "HUF"
    assert result.rows[0].action is ComparisonAction.HOLD
    assert result.rows[0].roi is None


def test_manual_mapping_allows_exact_isin_target_comparison(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source, _ = repository.import_source_document(
        _document(isin=None, provider_name="Manual mapping candidate", source_date=date(2026, 1, 1))
    )
    candidate_id = repository.positions_for_snapshot(source.snapshot_id)[0].instrument.instrument_id
    repository.confirm_instrument_mapping(candidate_id, _ISIN_A, "Manual mapping candidate")
    result = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path, {_ISIN_A: Decimal(100)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert result.rows[0].identity_status == IdentityStatus.MANUAL_CONFIRMED.value
    assert result.rows[0].action is ComparisonAction.HOLD


def test_exact_provider_name_candidate_and_fuzzy_name_remain_identity_blockers(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(isin=None, provider_name=f"Synthetic {_ISIN_A}", source_date=date(2026, 1, 1))
    )
    exact_candidate = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path, {_ISIN_A: Decimal(100)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert exact_candidate.rows[0].identity_status == IdentityStatus.PROVIDER_NAME_EXACT_CANDIDATE.value
    assert {row.action for row in exact_candidate.rows} == {ComparisonAction.IDENTITY_MAPPING_REQUIRED}

    fuzzy_repository = _repository(tmp_path / "fuzzy")
    fuzzy_repository.import_source_document(
        _document(isin=None, provider_name=f"Synthetic {_ISIN_A} approximately", source_date=date(2026, 1, 1))
    )
    fuzzy = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=fuzzy_repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path / "fuzzy", {_ISIN_A: Decimal(100)})),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert {row.action for row in fuzzy.rows} == {ComparisonAction.IDENTITY_MAPPING_REQUIRED}
    assert fuzzy.identity_blockers


def test_target_only_buy_has_exact_identity_and_no_transaction_side_effect(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(_document(source_date=date(2026, 1, 1)))
    before = repository.transactions()
    result = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path, {_ISIN_A: Decimal(20), _ISIN_B: Decimal(80)})
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    target_only = next(row for row in result.rows if row.isin == _ISIN_B)
    assert target_only.action is ComparisonAction.BUY
    assert target_only.current_value == Decimal()
    assert target_only.identity_status == "TARGET_EXACT_ISIN"
    assert repository.transactions() == before


def test_single_non_huf_currency_is_sized_without_fx_and_all_tbsz_retains_provenance(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(account="TBSZ 2024", source_date=date(2026, 1, 1), market_value=Decimal(30), market_currency="EUR")
    )
    repository.import_source_document(
        _document(
            filename="second.pdf",
            account="TBSZ 2025",
            source_date=date(2026, 1, 1),
            market_value=Decimal(70),
            market_currency="EUR",
            content="second",
        )
    )
    result = compare_tbsz_to_recommended_portfolio(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(_model_database(tmp_path, {_ISIN_A: Decimal(100)})),
        rules_path=_RULES,
        all_tbsz=True,
        target_portfolio_name="Target",
        tolerance=Decimal(0),
    )
    assert result.account_scope == "ALL_TBSZ"
    assert result.comparison_currency == "EUR"
    assert result.rows[0].account_provenance == ("TBSZ 2024", "TBSZ 2025")
    assert result.rows[0].action is ComparisonAction.HOLD


@pytest.mark.parametrize(
    ("strategy", "objective", "policy_availability"),
    (
        (
            DescriptiveStrategy.CAPITAL_PRESERVATION,
            PortfolioObjective.CAPITAL_CONSERVATION,
            "VALIDATED_ACTIVE_POLICY",
        ),
        (
            DescriptiveStrategy.DIVIDEND_MAXIMISATION,
            PortfolioObjective.DIVIDEND_PORTFOLIO,
            "NO_VALIDATED_ACTIVE_POLICY",
        ),
    ),
)
def test_descriptive_mode_requires_and_records_explicit_reference_strategy_and_horizon(
    tmp_path: Path,
    strategy: DescriptiveStrategy,
    objective: PortfolioObjective,
    policy_availability: str,
) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(source_date=date(2026, 1, 2), market_value=Decimal(100))
    )
    repository.import_source_document(
        _cash_document(
            filename="cash.pdf",
            account="TBSZ synthetic",
            source_date=date(2026, 1, 3),
            cash=(SourceCashInput("HUF", Decimal(10)),),
            content="cash",
        )
    )
    model = ModelPortfolioRepository(
        _model_database_rows(
            tmp_path,
            (
                ("Not selected", "Other", _ISIN_B, Decimal(100), "Bond", "HUF"),
                ("Explicit target", "Selected", _ISIN_A, Decimal(100), "Bond", "HUF"),
            ),
        )
    )

    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=model,
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Explicit target",
        strategy=strategy,
        investment_horizon_days=17,
    )

    assert result.mode == "DESCRIPTIVE_SINGLE_ACCOUNT"
    assert result.target_portfolio_name == "Explicit target"
    assert result.model_observation_date == date(2026, 1, 1)
    assert result.position_source_date == date(2026, 1, 2)
    assert result.cash_source_date == date(2026, 1, 3)
    assert result.report_timestamp.tzinfo is not None
    assert result.position_source_age_days == (
        result.report_timestamp.date() - date(2026, 1, 2)
    ).days
    assert result.cash_source_age_days == (
        result.report_timestamp.date() - date(2026, 1, 3)
    ).days
    assert result.cash_by_currency[0].balance == Decimal(10)
    assert result.cash_treatment == "RECORDED_SEPARATELY_NOT_ESTABLISHED_SPENDABLE"
    assert result.investment_horizon_days == 17
    assert (
        result.investment_horizon_status
        == "RECORDED_ONLY_POLICY_SUPPORT_NOT_ESTABLISHED"
    )
    assert result.internal_objective is objective
    assert result.strategy_policy_availability == policy_availability
    assert result.reference_selection_status == (
        "MANUALLY_SELECTED_NOT_A_VALIDATED_STRATEGY_RECOMMENDATION"
    )
    assert result.descriptive_comparison_availability == "AVAILABLE"
    assert result.trade_proposal_availability == "UNAVAILABLE_DESCRIPTIVE_MODE"
    assert result.shortlist_selection_availability == "UNAVAILABLE_NOT_IMPLEMENTED"
    assert not hasattr(result, "tolerance")
    assert all(not hasattr(row, "action") for row in result.rows)
    if strategy is DescriptiveStrategy.CAPITAL_PRESERVATION:
        assert result.strategy_policy_id == "CAPITAL_PRESERVATION_RANKING_POLICY"
        assert result.strategy_policy_version == "1.0.1"
        assert (
            result.strategy_policy_fingerprint
            == hashlib.sha256(_RULES.read_bytes()).hexdigest()
        )
        assert result.strategy_blockers == ()
    else:
        assert result.strategy_policy_id is None
        assert result.strategy_policy_fingerprint is None
        assert (
            result.strategy_ranking_availability
            == "UNAVAILABLE_NO_VALIDATED_ACTIVE_POLICY"
        )
        assert result.strategy_blockers == ("DIVIDEND_POLICY_NOT_VALIDATED_OR_ACTIVE",)


@pytest.mark.parametrize(
    "strategy",
    ("CAPITAL_CONSERVATION", "DIVIDEND_MAXIMIZATION", "capital_preservation"),
)
def test_descriptive_strategy_parser_rejects_legacy_and_alias_labels(strategy: str) -> None:
    with pytest.raises(ValueError, match="unsupported descriptive strategy"):
        DescriptiveStrategy.parse(strategy)


@pytest.mark.parametrize("horizon", (0, -1, 366, True))
def test_descriptive_mode_rejects_invalid_horizon(tmp_path: Path, horizon: int) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(_document(source_date=date(2026, 1, 1)))
    with pytest.raises(ValueError, match="integer from 1 to 365"):
        compare_tbsz_to_selected_portfolio_descriptively(
            tbsz_repository=repository,
            model_repository=ModelPortfolioRepository(
                _model_database(tmp_path, {_ISIN_A: Decimal(100)})
            ),
            rules_path=_RULES,
            account_label="TBSZ synthetic",
            target_portfolio_name="Target",
            strategy="CAPITAL_PRESERVATION",
            investment_horizon_days=horizon,
        )


def test_descriptive_mode_computes_action_free_signed_gaps_over_security_denominator(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _positions_document(
            filename="positions.pdf",
            account="TBSZ synthetic",
            source_date=date(2026, 1, 2),
            content="positions",
            positions=(
                SourcePositionInput("Fund A", Decimal(40), "HUF", isin=_ISIN_A),
                SourcePositionInput("Fund B", Decimal(60), "HUF", isin=_ISIN_B),
            ),
        )
    )
    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path, {_ISIN_A: Decimal(50), _ISIN_B: Decimal(50)})
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=365,
    )

    rows = {row.isin: row for row in result.rows}
    assert (
        result.denominator_basis == "COMPARABLE_SECURITY_VALUES_EXCLUDING_RECORDED_CASH"
    )
    assert result.total_comparable_security_value == Decimal(100)
    assert rows[_ISIN_A].current_weight == Decimal("0.4")
    assert rows[_ISIN_A].source_target_weight == Decimal("0.5")
    assert rows[_ISIN_A].weight_gap == Decimal("0.1")
    assert rows[_ISIN_A].signed_monetary_gap == Decimal(10)
    assert rows[_ISIN_B].signed_monetary_gap == Decimal(-10)


def test_descriptive_mode_preserves_target_cash_and_blocks_incomplete_target(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(_document(source_date=date(2026, 1, 2)))
    model = ModelPortfolioRepository(
        _model_database_rows(
            tmp_path,
            (
                ("Target", "Fund A", _ISIN_A, Decimal(80), "Bond", "HUF"),
                ("Target", "Cash", None, Decimal(10), "Cash", "HUF"),
                ("Target", "Unsupported", None, Decimal(10), "Bond", "HUF"),
            ),
        )
    )
    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=model,
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=30,
    )

    target_rows = {item.asset_name: item for item in result.target_allocations}
    assert target_rows["Cash"].source_weight == Decimal("0.1")
    assert target_rows["Cash"].allocation_type == "CASH"
    assert (
        target_rows["Cash"].support_status
        == "SOURCE_CASH_ALLOCATION_PRESERVED_SEPARATELY"
    )
    assert target_rows["Unsupported"].support_status == "UNSUPPORTED_MISSING_EXACT_ISIN"
    assert result.descriptive_comparison_availability == "BLOCKED"
    assert result.target_completeness_blockers == (
        "TARGET_EXACT_ISIN_UNAVAILABLE:Unsupported",
    )
    assert all(row.current_weight is None for row in result.rows)
    assert all(row.signed_monetary_gap is None for row in result.rows)
    assert "SOURCE_TARGET_CASH_GAP_NOT_COMPUTED" in result.cash_blockers


def test_descriptive_mode_retains_missing_identity_value_dates_and_cash_as_unknown(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(
            source_date=None,
            isin=None,
            market_value=None,
            market_currency=None,
        )
    )
    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path, {_ISIN_A: Decimal(100)})
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="DIVIDEND_MAXIMISATION",
        investment_horizon_days=1,
    )

    assert result.position_source_date is None
    assert result.position_source_age_days is None
    assert result.cash_source_date is None
    assert result.cash_source_age_days is None
    assert result.cash_by_currency == ()
    assert result.identity_blockers == ("TBSZ synthetic:Synthetic Fund A",)
    assert "POSITION_SOURCE_DATE_UNAVAILABLE" in result.date_blockers
    assert "CASH_SOURCE_UNAVAILABLE" in result.date_blockers
    assert result.cash_blockers == ("RECORDED_CASH_UNKNOWN_NO_SOURCE",)
    assert result.rows[0].current_value is None
    assert result.rows[0].signed_monetary_gap is None


def test_descriptive_mode_blocks_missing_value_and_mixed_currency_without_fx(
    tmp_path: Path,
) -> None:
    missing_repository = _repository(tmp_path / "missing")
    missing_repository.import_source_document(
        _document(
            source_date=date(2026, 1, 2),
            market_value=None,
            market_currency=None,
        )
    )
    model_path = _model_database(tmp_path / "models", {_ISIN_A: Decimal(100)})
    missing = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=missing_repository,
        model_repository=ModelPortfolioRepository(model_path),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
    )
    assert missing.valuation_blockers == (
        "VALUE_OR_CURRENCY_UNAVAILABLE:TBSZ synthetic:Synthetic Fund A",
    )

    mixed_repository = _repository(tmp_path / "mixed")
    mixed_repository.import_source_document(
        _positions_document(
            filename="mixed.pdf",
            account="TBSZ synthetic",
            source_date=date(2026, 1, 2),
            content="mixed",
            positions=(
                SourcePositionInput("Fund A", Decimal(40), "EUR", isin=_ISIN_A),
                SourcePositionInput("Fund B", Decimal(60), "HUF", isin=_ISIN_B),
            ),
        )
    )
    mixed = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=mixed_repository,
        model_repository=ModelPortfolioRepository(
            _model_database(
                tmp_path / "mixed_models",
                {_ISIN_A: Decimal(50), _ISIN_B: Decimal(50)},
            )
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
    )
    assert mixed.descriptive_comparison_availability == "BLOCKED"
    assert all(
        item.startswith("MIXED_CURRENCIES_NO_FX:")
        for item in mixed.valuation_blockers
    )
    assert all(row.signed_monetary_gap is None for row in mixed.rows)


def test_descriptive_mode_keeps_accounts_isolated(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(
            account="TBSZ selected",
            source_date=date(2026, 1, 2),
            market_value=Decimal(25),
        )
    )
    repository.import_source_document(
        _document(
            filename="other.pdf",
            account="TBSZ other",
            source_date=date(2026, 1, 2),
            market_value=Decimal(999),
            content="other",
        )
    )
    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path, {_ISIN_A: Decimal(100)})
        ),
        rules_path=_RULES,
        account_label="TBSZ selected",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
    )

    assert result.account_label == "TBSZ selected"
    assert result.total_comparable_security_value == Decimal(25)
    assert {row.account for row in result.rows} == {"TBSZ selected"}
    assert {account for row in result.rows for account in row.account_provenance} == {
        "TBSZ selected"
    }


def test_descriptive_mode_retains_post_transaction_reconciliation_blocker(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    snapshot, _ = repository.import_source_document(
        _document(source_date=date(2026, 1, 1))
    )
    instrument_id = repository.positions_for_snapshot(snapshot.snapshot_id)[
        0
    ].instrument.instrument_id
    repository.record_manual_transaction(
        account_label="TBSZ synthetic",
        action=TransactionAction.BUY,
        instrument_id=instrument_id,
        quantity=Decimal(1),
        price=Decimal(1),
        currency="HUF",
        transaction_date=date(2026, 1, 2),
    )

    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path, {_ISIN_A: Decimal(100)})
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
    )

    assert result.reconciliation_blockers == (
        "POST_TRADE_PDF_RECONCILIATION_REQUIRED:TBSZ synthetic",
    )
    assert result.descriptive_comparison_availability == "BLOCKED"
    assert all(row.signed_monetary_gap is None for row in result.rows)


def test_comparison_entry_point_exposes_explicit_descriptive_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(source_date=date(2026, 1, 2), market_value=Decimal(100))
    )
    model_path = _model_database(tmp_path, {_ISIN_A: Decimal(100)})

    exit_code = comparison_main(
        [
            "--mode",
            "descriptive",
            "--account",
            "TBSZ synthetic",
            "--target-portfolio",
            "Target",
            "--strategy",
            "CAPITAL_PRESERVATION",
            "--investment-horizon-days",
            "45",
            "--tbsz-database",
            str(repository.path),
            "--model-database",
            str(model_path),
            "--rules",
            str(_RULES),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "DESCRIPTIVE_SINGLE_ACCOUNT"
    assert payload["investment_horizon_days"] == 45
    assert payload["target_portfolio_name"] == "Target"
    assert "tolerance" not in payload
    assert "action" not in payload["rows"][0]


def test_descriptive_mode_consumes_validated_identity_confirmation_read_only(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    source, _ = repository.import_source_document(
        _document(
            source_date=date(2026, 1, 2),
            isin=None,
            provider_name="Approved Fund",
            market_value=Decimal(100),
            market_currency="HUF",
        )
    )
    store_path, registry_path = _identity_confirmation_evidence(
        tmp_path,
        source_name="Approved Fund",
        isin=_VALID_ISIN_A,
        currency="HUF",
    )
    database_hash = hashlib.sha256(repository.path.read_bytes()).hexdigest()
    store_hash = hashlib.sha256(store_path.read_bytes()).hexdigest()
    registry_hash = hashlib.sha256(registry_path.read_bytes()).hexdigest()

    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path / "model", {_VALID_ISIN_A: Decimal(100)})
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
        identity_confirmations_path=store_path,
        identity_registry_audit_path=registry_path,
    )

    row = result.rows[0]
    assert result.identity_blockers == ()
    assert result.identity_resolution_blockers == ()
    assert result.identity_confirmation_validation_blockers == ()
    assert result.identity_confirmation_store_fingerprint == store_hash
    assert result.identity_registry_audit_fingerprint == registry_hash
    assert row.isin == _VALID_ISIN_A
    assert row.raw_isin is None
    assert row.identity_status == IdentityStatus.MANUAL_CONFIRMED.value
    assert row.raw_identity_status == IdentityStatus.PROVIDER_NAME_EXACT_CANDIDATE.value
    assert row.identity_provenance is not None
    assert row.identity_provenance.source == "APPROVED_LTIA_IDENTITY_CONFIRMATION_STORE"
    assert row.identity_provenance.rule == "UNIQUE_EXACT_NORMALIZED_PROVIDER_NAME"
    assert row.identity_provenance.confirmation_store_fingerprint == store_hash
    assert row.identity_provenance.identity_registry_audit_fingerprint == registry_hash
    assert repository.positions_for_snapshot(source.snapshot_id)[0].instrument.isin is None
    assert hashlib.sha256(repository.path.read_bytes()).hexdigest() == database_hash
    assert hashlib.sha256(store_path.read_bytes()).hexdigest() == store_hash
    assert hashlib.sha256(registry_path.read_bytes()).hexdigest() == registry_hash


def test_descriptive_mode_keeps_absent_confirmation_explicit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(
            source_date=date(2026, 1, 2),
            isin=None,
            provider_name="Unapproved Fund",
        )
    )
    store_path, registry_path = _identity_confirmation_evidence(
        tmp_path,
        source_name="Different Approved Fund",
        isin=_VALID_ISIN_A,
        currency="HUF",
    )

    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path / "model", {_VALID_ISIN_A: Decimal(100)})
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
        identity_confirmations_path=store_path,
        identity_registry_audit_path=registry_path,
    )

    assert len(result.identity_blockers) == 1
    assert result.identity_confirmation_validation_blockers == ()
    assert result.identity_resolution_blockers == (
        "NO_APPLICABLE_APPROVED_IDENTITY_CONFIRMATION",
    )
    current_row = next(row for row in result.rows if row.instrument_id is not None)
    assert current_row.isin is None


@pytest.mark.parametrize(
    ("record_overrides", "expected_blocker"),
    (
        (
            {"confirmed_by": "NOT_APPROVED"},
            "IDENTITY_CONFIRMATION_APPROVAL_INVALID",
        ),
        (
            {"share_class_checked": False},
            "IDENTITY_CONFIRMATION_SHARE_CLASS_APPROVAL_MISSING",
        ),
        (
            {"isin": _VALID_ISIN_B},
            "IDENTITY_CONFIRMATION_RECORD_ISIN_CONFLICT",
        ),
    ),
)
def test_descriptive_mode_rejects_invalid_confirmation_provenance(
    tmp_path: Path,
    record_overrides: dict[str, object],
    expected_blocker: str,
) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(
            source_date=date(2026, 1, 2),
            isin=None,
            provider_name="Approval Required Fund",
        )
    )
    store_path, registry_path = _identity_confirmation_evidence(
        tmp_path,
        source_name="Approval Required Fund",
        isin=_VALID_ISIN_A,
        currency="HUF",
        record_overrides=record_overrides,
    )

    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path / "model", {_VALID_ISIN_A: Decimal(100)})
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
        identity_confirmations_path=store_path,
        identity_registry_audit_path=registry_path,
    )

    assert result.identity_confirmation_validation_blockers == (expected_blocker,)
    assert result.identity_resolution_blockers == (
        "NO_APPLICABLE_APPROVED_IDENTITY_CONFIRMATION",
    )
    assert result.rows[0].isin is None


def test_descriptive_mode_does_not_override_conflicting_source_isin(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(
            source_date=date(2026, 1, 2),
            isin=_VALID_ISIN_B,
            provider_name="Conflicting Fund",
        )
    )
    store_path, registry_path = _identity_confirmation_evidence(
        tmp_path,
        source_name="Conflicting Fund",
        isin=_VALID_ISIN_A,
        currency="HUF",
    )

    result = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=ModelPortfolioRepository(
            _model_database(tmp_path / "model", {_VALID_ISIN_B: Decimal(100)})
        ),
        rules_path=_RULES,
        account_label="TBSZ synthetic",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
        identity_confirmations_path=store_path,
        identity_registry_audit_path=registry_path,
    )

    assert result.identity_resolution_blockers == (
        "IDENTITY_CONFIRMATION_CONFLICTS_WITH_SOURCE_ISIN",
    )
    assert result.rows[0].raw_isin == _VALID_ISIN_B
    assert result.rows[0].isin is None
    assert result.rows[0].signed_monetary_gap is None


def test_descriptive_confirmation_respects_account_and_currency_isolation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.import_source_document(
        _document(
            account="TBSZ HUF",
            source_date=date(2026, 1, 2),
            isin=None,
            provider_name="Currency Bound Fund",
            market_currency="HUF",
        )
    )
    repository.import_source_document(
        _document(
            filename="eur.pdf",
            account="TBSZ EUR",
            source_date=date(2026, 1, 2),
            isin=None,
            provider_name="Currency Bound Fund",
            market_currency="EUR",
            content="eur",
        )
    )
    store_path, registry_path = _identity_confirmation_evidence(
        tmp_path,
        source_name="Currency Bound Fund",
        isin=_VALID_ISIN_A,
        currency="HUF",
    )
    model = ModelPortfolioRepository(
        _model_database(tmp_path / "model", {_VALID_ISIN_A: Decimal(100)})
    )

    huf = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=model,
        rules_path=_RULES,
        account_label="TBSZ HUF",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
        identity_confirmations_path=store_path,
        identity_registry_audit_path=registry_path,
    )
    eur = compare_tbsz_to_selected_portfolio_descriptively(
        tbsz_repository=repository,
        model_repository=model,
        rules_path=_RULES,
        account_label="TBSZ EUR",
        target_portfolio_name="Target",
        strategy="CAPITAL_PRESERVATION",
        investment_horizon_days=90,
        identity_confirmations_path=store_path,
        identity_registry_audit_path=registry_path,
    )

    assert huf.identity_blockers == ()
    assert {row.account for row in huf.rows} == {"TBSZ HUF"}
    assert eur.identity_resolution_blockers == (
        "IDENTITY_CONFIRMATION_CURRENCY_MISMATCH",
    )
    assert {row.account for row in eur.rows} == {"TBSZ EUR"}
    assert eur.rows[0].isin is None
