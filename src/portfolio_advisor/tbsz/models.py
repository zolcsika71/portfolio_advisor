"""Typed, provenance-first records for TBSZ portfolio evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class TbszError(RuntimeError):
    """Raised when local TBSZ evidence cannot be handled safely."""


class SourceConflictError(TbszError):
    """A source retry or undated source set conflicts with retained evidence."""


class TransactionAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class IdentityStatus(StrEnum):
    EXACT_ISIN = "EXACT_ISIN"
    MANUAL_CONFIRMED = "MANUAL_CONFIRMED"
    PROVIDER_NAME_EXACT_CANDIDATE = "PROVIDER_NAME_EXACT_CANDIDATE"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"


class ComparisonAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    IDENTITY_MAPPING_REQUIRED = "IDENTITY_MAPPING_REQUIRED"
    FX_REQUIRED_FOR_EXACT_TRADE_AMOUNT = "FX_REQUIRED_FOR_EXACT_TRADE_AMOUNT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ReconciliationStatus(StrEnum):
    RECONCILED = "RECONCILED"
    RECONCILIATION_DIFFERENCE = "RECONCILIATION_DIFFERENCE"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    INSUFFICIENT_SOURCE_DETAIL = "INSUFFICIENT_SOURCE_DETAIL"


@dataclass(frozen=True, slots=True)
class TbszAccount:
    account_id: int
    label: str


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    snapshot_id: int
    account_id: int
    source_filename: str
    source_type: str
    view_type: str
    source_date: date | None
    ingested_at: datetime
    evidence_status: str
    evidence_fingerprint: str


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: int
    canonical_name: str
    isin: str | None
    identity_status: IdentityStatus


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    position_id: int
    snapshot_id: int
    account_id: int
    instrument: Instrument
    provider_name: str
    quantity: Decimal | None
    unit_price: Decimal | None
    market_value: Decimal | None
    market_currency: str | None
    reporting_value: Decimal | None
    reporting_currency: str | None
    data_quality_status: str


@dataclass(frozen=True, slots=True)
class CashSnapshot:
    cash_id: int
    snapshot_id: int
    account_id: int
    currency: str
    balance: Decimal
    data_quality_status: str


@dataclass(frozen=True, slots=True)
class ManualTransaction:
    transaction_id: str
    account_id: int
    instrument_id: int
    action: TransactionAction
    quantity: Decimal
    price: Decimal
    currency: str
    transaction_date: date
    recorded_at: datetime
    client_reference: str | None


@dataclass(frozen=True, slots=True)
class SourcePositionInput:
    provider_name: str
    market_value: Decimal | None
    market_currency: str | None
    reporting_value: Decimal | None = None
    reporting_currency: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    isin: str | None = None
    data_quality_status: str = "SOURCE_SUPPORTED"


@dataclass(frozen=True, slots=True)
class SourceCashInput:
    currency: str
    balance: Decimal
    data_quality_status: str = "SOURCE_SUPPORTED"


@dataclass(frozen=True, slots=True)
class SourceDocumentInput:
    source_filename: str
    content_sha256: str
    account_label: str
    view_type: str
    source_date: date | None
    evidence_status: str
    positions: tuple[SourcePositionInput, ...] = ()
    cash: tuple[SourceCashInput, ...] = ()
