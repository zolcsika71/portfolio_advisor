"""Authoritative, non-pricing redemption and coupon evidence.

This module records contract terms only.  It does not create a cash flow,
price observation, or return calculation.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from portfolio_advisor.history.security_lifecycle import (
    AKK_AUTHORITY,
    SecurityLifecycleError,
    extract_pdf_layout_text,
    sha256_file,
)

TARGET_ISIN = "HU0000554795"
TARGET_SERIES = "K2025/23"
TARGET_CURRENCY = "HUF"
SOURCE_HOST = "www.allampapir.hu"
DOCUMENT_TYPE = "ÁKK public offering"
_HUNGARIAN_MONTHS = {
    "január": 1,
    "február": 2,
    "március": 3,
    "április": 4,
    "május": 5,
    "június": 6,
    "július": 7,
    "augusztus": 8,
    "szeptember": 9,
    "október": 10,
    "november": 11,
    "december": 12,
}


class RedemptionMethodologyError(RuntimeError):
    """Raised when contractual redemption evidence is incomplete or ambiguous."""


def _normalise_text(value: str) -> str:
    return " ".join(value.split())


def _exact_group(text: str, pattern: str, field: str) -> str:
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    if len(matches) != 1:
        raise RedemptionMethodologyError(f"REDEMPTION_IDENTITY_CONFLICT: ambiguous {field}")
    value = matches[0]
    return value if isinstance(value, str) else value[0]


def _parse_hungarian_date(value: str, field: str) -> date:
    match = re.fullmatch(r"(\d{4})\. ([a-záéíóöőúüű]+) (\d{1,2})\.", value.casefold())
    if match is None or match.group(2) not in _HUNGARIAN_MONTHS:
        raise RedemptionMethodologyError(f"REDEMPTION_DOCUMENT_UNUSABLE: malformed {field}")
    try:
        return date(int(match.group(1)), _HUNGARIAN_MONTHS[match.group(2)], int(match.group(3)))
    except ValueError as exc:
        raise RedemptionMethodologyError(f"REDEMPTION_DOCUMENT_UNUSABLE: invalid {field}") from exc


def _parse_decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value.replace(".", "").replace(",", "."))
    except InvalidOperation as exc:
        raise RedemptionMethodologyError(f"REDEMPTION_DOCUMENT_UNUSABLE: malformed {field}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise RedemptionMethodologyError(f"REDEMPTION_DOCUMENT_UNUSABLE: invalid {field}")
    return parsed


@dataclass(frozen=True, slots=True)
class RedemptionCouponEvidence:
    """Validated contract facts, deliberately excluding any calculated payment."""

    isin: str
    series: str
    currency: str
    issue_date: date
    maturity_date: date
    nominal_value_basis_huf: Decimal
    principal_repayment_at_par: bool
    principal_redemption_percentage: Decimal | None
    principal_redemption_date: date | None
    coupon_rate: Decimal | None
    coupon_payment_dates: tuple[date, ...]
    final_coupon_payment_date: date | None
    coupon_frequency: str | None
    day_count_convention: str | None
    accrual_start_date: date | None
    accrued_interest_at_maturity: Decimal | None
    redemption_includes_coupon: bool | None
    settlement_currency: str | None
    price_convention: str
    clean_price_convention: str | None
    dirty_price_convention: str | None
    quoted_price_semantics: str
    source_authority: str
    source_host: str | None
    source_document: str
    source_document_sha256: str
    source_document_type: str
    validation_warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.isin != TARGET_ISIN or self.series != TARGET_SERIES or self.currency != TARGET_CURRENCY:
            raise RedemptionMethodologyError("REDEMPTION_IDENTITY_CONFLICT: exact ISIN/series/currency required")
        if self.issue_date >= self.maturity_date:
            raise RedemptionMethodologyError("REDEMPTION_DATE_CONFLICT: issue date must precede maturity")
        if self.nominal_value_basis_huf <= 0:
            raise RedemptionMethodologyError("REDEMPTION_DOCUMENT_UNUSABLE: nominal value must be positive")
        if self.principal_redemption_percentage is not None and self.principal_redemption_percentage <= 0:
            raise RedemptionMethodologyError("REDEMPTION_DOCUMENT_UNUSABLE: principal percentage must be positive")
        if self.principal_redemption_date is not None and self.principal_redemption_date != self.maturity_date:
            raise RedemptionMethodologyError("REDEMPTION_DATE_CONFLICT: redemption date conflicts with maturity")
        if self.coupon_rate is not None and self.coupon_rate <= 0:
            raise RedemptionMethodologyError("REDEMPTION_DOCUMENT_UNUSABLE: coupon rate must be positive")
        if self.final_coupon_payment_date is not None and self.final_coupon_payment_date not in self.coupon_payment_dates:
            raise RedemptionMethodologyError("REDEMPTION_DATE_CONFLICT: final coupon date is not in the schedule")
        if self.price_convention != "UNKNOWN":
            raise RedemptionMethodologyError("REDEMPTION_DOCUMENT_UNUSABLE: unapproved price convention")
        if self.clean_price_convention is not None or self.dirty_price_convention is not None:
            raise RedemptionMethodologyError("REDEMPTION_DOCUMENT_UNUSABLE: no clean/dirty conversion is supported")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_document_sha256):
            raise RedemptionMethodologyError("REDEMPTION_DOCUMENT_UNUSABLE: source hash is invalid")

    @property
    def coupon_rate_validated(self) -> bool:
        return self.coupon_rate is not None

    @property
    def coupon_schedule_validated(self) -> bool:
        return self.final_coupon_payment_date is not None and bool(self.coupon_payment_dates)

    @property
    def redemption_mechanics_validated(self) -> bool:
        return (
            self.principal_repayment_at_par
            and self.principal_redemption_date is not None
            and self.redemption_includes_coupon is False
            and self.coupon_schedule_validated
        )

    def as_audit_dict(self) -> dict[str, object]:
        result = asdict(self)
        for field in (
            "issue_date",
            "maturity_date",
            "principal_redemption_date",
            "final_coupon_payment_date",
            "accrual_start_date",
        ):
            value = result[field]
            result[field] = value.isoformat() if isinstance(value, date) else None
        result["coupon_payment_dates"] = [value.isoformat() for value in self.coupon_payment_dates]
        for field in (
            "nominal_value_basis_huf",
            "principal_redemption_percentage",
            "coupon_rate",
            "accrued_interest_at_maturity",
        ):
            value = result[field]
            result[field] = str(value) if isinstance(value, Decimal) else None
        result["validation_warnings"] = list(self.validation_warnings)
        result["coupon_rate_validated"] = self.coupon_rate_validated
        result["coupon_schedule_validated"] = self.coupon_schedule_validated
        result["redemption_mechanics_validated"] = self.redemption_mechanics_validated
        return result


def parse_akk_public_offering_redemption_text(
    text: str, source_document: str, source_document_sha256: str
) -> RedemptionCouponEvidence:
    """Parse the exact-series ÁKK public offering without inferring missing terms."""
    compact = _normalise_text(text)
    if AKK_AUTHORITY not in compact or "NYILVÁNOS AJÁNLATTÉTEL" not in compact:
        raise RedemptionMethodologyError("REDEMPTION_DOCUMENT_UNUSABLE: authoritative public offering is absent")
    isin = _exact_group(compact, r"ISIN-kód:\s*(HU[A-Z0-9]{10})", "ISIN")
    series = _exact_group(compact, r"Sorozatszám:\s*(K\d{4}/\d{2})", "series")
    issue_date = _parse_hungarian_date(
        _exact_group(compact, r"Kibocsátás napja:\s*(\d{4}\. [^ ]+ \d{1,2}\.)", "issue date"),
        "issue date",
    )
    maturity_date = _parse_hungarian_date(
        _exact_group(compact, r"A lejárat napja:\s*(\d{4}\. [^ ]+ \d{1,2}\.)", "maturity date"),
        "maturity date",
    )
    nominal = _parse_decimal(
        _exact_group(compact, r"Névérték:\s*([0-9.]+),- Ft", "nominal value"),
        "nominal value",
    )
    coupon_rate = _parse_decimal(
        _exact_group(compact, r"kifizetendő kamat mértéke: évi ([0-9,]+)%", "coupon rate"),
        "coupon rate",
    )
    required_phrases = (
        "A névérték kifizetése a lejárat napjától esedékes.",
        "A kamat kifizetése a lejárat napjától esedékes.",
        "kamat mértéke: évi 6,00%",
    )
    if not all(phrase in compact for phrase in required_phrases):
        raise RedemptionMethodologyError("REDEMPTION_DOCUMENT_UNUSABLE: payment terms are incomplete")
    return RedemptionCouponEvidence(
        isin=isin,
        series=series,
        currency=TARGET_CURRENCY,
        issue_date=issue_date,
        maturity_date=maturity_date,
        nominal_value_basis_huf=nominal,
        principal_repayment_at_par=True,
        principal_redemption_percentage=None,
        principal_redemption_date=maturity_date,
        coupon_rate=coupon_rate,
        coupon_payment_dates=(maturity_date,),
        final_coupon_payment_date=maturity_date,
        coupon_frequency="AT_MATURITY",
        day_count_convention=None,
        accrual_start_date=issue_date,
        accrued_interest_at_maturity=None,
        redemption_includes_coupon=False,
        settlement_currency=TARGET_CURRENCY,
        price_convention="UNKNOWN",
        clean_price_convention=None,
        dirty_price_convention=None,
        quoted_price_semantics="OTC_TRANSACTION_PRICE_UNSPECIFIED",
        source_authority=AKK_AUTHORITY,
        source_host=SOURCE_HOST,
        source_document=source_document,
        source_document_sha256=source_document_sha256,
        source_document_type=DOCUMENT_TYPE,
        validation_warnings=(
            "DAY_COUNT_CONVENTION_NOT_VALIDATED",
            "ACCRUED_INTEREST_AT_MATURITY_NOT_VALIDATED",
            "MNB_OTC_CLEAN_DIRTY_PRICE_CONVENTION_UNKNOWN",
            "NO_POST_MATURITY_PORTFOLIO_POLICY",
        ),
    )


def load_akk_public_offering_redemption(path: Path) -> RedemptionCouponEvidence:
    """Read one local public offering; no network operation occurs here."""
    try:
        text = extract_pdf_layout_text(path)
    except SecurityLifecycleError as exc:
        raise RedemptionMethodologyError(str(exc)) from exc
    return parse_akk_public_offering_redemption_text(text, str(path), sha256_file(path))


def require_consistent_redemption_evidence(
    evidence: tuple[RedemptionCouponEvidence, ...]
) -> RedemptionCouponEvidence:
    """Reject contradictory exact-series local contractual evidence."""
    if not evidence:
        raise RedemptionMethodologyError("REDEMPTION_EVIDENCE_MISSING")
    first = evidence[0]
    for candidate in evidence[1:]:
        if (candidate.isin, candidate.series, candidate.currency) != (first.isin, first.series, first.currency):
            raise RedemptionMethodologyError("REDEMPTION_IDENTITY_CONFLICT")
        if candidate.principal_redemption_date != first.principal_redemption_date:
            raise RedemptionMethodologyError("REDEMPTION_DATE_CONFLICT")
        if candidate.principal_repayment_at_par != first.principal_repayment_at_par:
            raise RedemptionMethodologyError("REDEMPTION_PRINCIPAL_CONFLICT")
    return first


def classify_crossing_maturity_methodology(
    evidence: RedemptionCouponEvidence,
    *,
    pre_maturity_value_available: bool,
    post_maturity_portfolio_policy_specified: bool,
    cash_flow_methodology_approved: bool,
) -> str:
    """Classify specification completeness without calculating a return.

    The last three inputs are deliberately external approvals/evidence; this
    function makes no assumptions about them from contractual terms alone.
    """
    if not evidence.redemption_mechanics_validated:
        return "METHODOLOGY_BLOCKED"
    if (
        pre_maturity_value_available
        and post_maturity_portfolio_policy_specified
        and cash_flow_methodology_approved
    ):
        return "METHODOLOGY_READY"
    return "METHODOLOGY_PARTIALLY_SPECIFIED"
