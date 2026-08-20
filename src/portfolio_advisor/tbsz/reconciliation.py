"""Compare retained TBSZ source snapshots without rewriting historical evidence."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import ReconciliationStatus
from .repository import TbszPortfolioRepository


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: ReconciliationStatus
    account_label: str
    previous_snapshot_id: int
    later_snapshot_id: int
    detail: str
    changed_instrument_ids: tuple[int, ...] = ()


def reconcile_position_snapshots(
    repository: TbszPortfolioRepository,
    *,
    account_label: str,
    previous_snapshot_id: int,
    later_snapshot_id: int,
) -> ReconciliationResult:
    """Reconcile two dated, position-view sources using established identity only."""
    previous = _source(repository, previous_snapshot_id, account_label)
    later = _source(repository, later_snapshot_id, account_label)
    if previous.view_type != "POSITIONS" or later.view_type != "POSITIONS":
        return _result(ReconciliationStatus.INSUFFICIENT_SOURCE_DETAIL, account_label, previous_snapshot_id, later_snapshot_id, "both sources must be position snapshots")
    if previous.source_date is None or later.source_date is None or later.source_date <= previous.source_date:
        return _result(ReconciliationStatus.INSUFFICIENT_SOURCE_DETAIL, account_label, previous_snapshot_id, later_snapshot_id, "ordered source dates are required")
    old_positions = repository.positions_for_snapshot(previous_snapshot_id)
    new_positions = repository.positions_for_snapshot(later_snapshot_id)
    unresolved = [position.instrument.instrument_id for position in (*old_positions, *new_positions) if position.instrument.isin is None]
    if unresolved:
        return _result(ReconciliationStatus.IDENTITY_UNRESOLVED, account_label, previous_snapshot_id, later_snapshot_id, "exact ISIN or manual identity confirmation is required", tuple(sorted(set(unresolved))))
    old_by_isin = {position.instrument.isin: position for position in old_positions}
    new_by_isin = {position.instrument.isin: position for position in new_positions}
    assert None not in old_by_isin and None not in new_by_isin
    changed: set[int] = set()
    for isin in set(old_by_isin) | set(new_by_isin):
        old = old_by_isin.get(isin)
        new = new_by_isin.get(isin)
        if old is None or new is None:
            present = old if old is not None else new
            assert present is not None
            changed.add(present.instrument.instrument_id)
            continue
        if not _comparable(old.market_value, old.market_currency, new.market_value, new.market_currency):
            return _result(ReconciliationStatus.INSUFFICIENT_SOURCE_DETAIL, account_label, previous_snapshot_id, later_snapshot_id, "market values with matching currencies are required")
        if old.market_value != new.market_value:
            changed.add(old.instrument.instrument_id)
    if changed:
        return _result(ReconciliationStatus.RECONCILIATION_DIFFERENCE, account_label, previous_snapshot_id, later_snapshot_id, "established positions changed", tuple(sorted(changed)))
    return _result(ReconciliationStatus.RECONCILED, account_label, previous_snapshot_id, later_snapshot_id, "all established positions reconcile")


def _source(repository: TbszPortfolioRepository, snapshot_id: int, account_label: str):  # type: ignore[no-untyped-def]
    account = repository.account(account_label)
    source = next((item for item in repository.source_snapshots(account_label) if item.snapshot_id == snapshot_id), None)
    if source is None or source.account_id != account.account_id:
        raise ValueError("snapshot is not retained for the requested TBSZ account")
    return source


def _comparable(
    old_value: Decimal | None,
    old_currency: str | None,
    new_value: Decimal | None,
    new_currency: str | None,
) -> bool:
    return old_value is not None and new_value is not None and old_currency is not None and old_currency == new_currency


def _result(
    status: ReconciliationStatus,
    account_label: str,
    previous_snapshot_id: int,
    later_snapshot_id: int,
    detail: str,
    changed_instrument_ids: tuple[int, ...] = (),
) -> ReconciliationResult:
    return ReconciliationResult(status, account_label, previous_snapshot_id, later_snapshot_id, detail, changed_instrument_ids)
