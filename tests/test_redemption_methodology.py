from __future__ import annotations

import importlib.util
import json
import socket
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.history.redemption_methodology import (
    RedemptionCouponEvidence,
    RedemptionMethodologyError,
    classify_crossing_maturity_methodology,
    load_akk_public_offering_redemption,
    parse_akk_public_offering_redemption_text,
    require_consistent_redemption_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests" / "fixtures" / "hu0000554795_public_offering_extract.txt").read_text(
    encoding="utf-8"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "audit_hu0000554795_redemption_methodology",
    ROOT / "scripts" / "audit_hu0000554795_redemption_methodology.py",
)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
redemption_audit = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = redemption_audit
SCRIPT_SPEC.loader.exec_module(redemption_audit)


def _evidence() -> RedemptionCouponEvidence:
    return parse_akk_public_offering_redemption_text(FIXTURE, "offering.pdf", "b" * 64)


def test_exact_isin_and_series_establish_redemption_and_coupon_terms() -> None:
    evidence = _evidence()

    assert evidence.isin == "HU0000554795"
    assert evidence.series == "K2025/23"
    assert evidence.currency == "HUF"
    assert evidence.nominal_value_basis_huf == Decimal(10000)
    assert evidence.principal_repayment_at_par is True
    assert evidence.principal_redemption_percentage is None
    assert evidence.principal_redemption_date == date(2025, 6, 4)
    assert evidence.coupon_rate == Decimal("6.00")
    assert evidence.coupon_payment_dates == (date(2025, 6, 4),)
    assert evidence.final_coupon_payment_date == date(2025, 6, 4)
    assert evidence.coupon_frequency == "AT_MATURITY"
    assert evidence.redemption_includes_coupon is False
    assert evidence.redemption_mechanics_validated is True


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("HU0000554796", "exact ISIN/series/currency"),
        ("K2025/24", "exact ISIN/series/currency"),
        ("A névérték kifizetése nem igazolt.", "payment terms"),
    ],
)
def test_generic_or_wrong_identity_evidence_fails_closed(replacement: str, message: str) -> None:
    if replacement.startswith("HU"):
        text = FIXTURE.replace("HU0000554795", replacement)
    elif replacement.startswith("K"):
        text = FIXTURE.replace("K2025/23", replacement)
    else:
        text = FIXTURE.replace("A névérték kifizetése a lejárat napjától esedékes.", replacement)
    with pytest.raises(RedemptionMethodologyError, match=message):
        parse_akk_public_offering_redemption_text(text, "offering.pdf", "b" * 64)


def test_conflicting_redemption_terms_fail_closed() -> None:
    first = _evidence()
    different_principal = replace(first, principal_repayment_at_par=False)

    with pytest.raises(RedemptionMethodologyError, match="REDEMPTION_DATE_CONFLICT"):
        replace(first, principal_redemption_date=date(2025, 6, 3))
    with pytest.raises(RedemptionMethodologyError, match="REDEMPTION_PRINCIPAL_CONFLICT"):
        require_consistent_redemption_evidence((first, different_principal))


def test_coupon_rate_and_schedule_are_independent_and_price_convention_stays_unknown() -> None:
    evidence = _evidence()
    rate_only = replace(evidence, coupon_payment_dates=(), final_coupon_payment_date=None)

    assert rate_only.coupon_rate_validated is True
    assert rate_only.coupon_schedule_validated is False
    assert rate_only.redemption_mechanics_validated is False
    assert evidence.price_convention == "UNKNOWN"
    assert evidence.clean_price_convention is None
    assert evidence.dirty_price_convention is None


def test_methodology_ready_requires_external_price_policy_and_approval() -> None:
    evidence = _evidence()
    blocked = replace(evidence, coupon_payment_dates=(), final_coupon_payment_date=None)

    assert (
        classify_crossing_maturity_methodology(
            blocked,
            pre_maturity_value_available=True,
            post_maturity_portfolio_policy_specified=True,
            cash_flow_methodology_approved=True,
        )
        == "METHODOLOGY_BLOCKED"
    )
    assert (
        classify_crossing_maturity_methodology(
            evidence,
            pre_maturity_value_available=False,
            post_maturity_portfolio_policy_specified=False,
            cash_flow_methodology_approved=False,
        )
        == "METHODOLOGY_PARTIALLY_SPECIFIED"
    )
    assert (
        classify_crossing_maturity_methodology(
            evidence,
            pre_maturity_value_available=True,
            post_maturity_portfolio_policy_specified=True,
            cash_flow_methodology_approved=True,
        )
        == "METHODOLOGY_READY"
    )


def test_retained_offering_is_the_production_evidence_not_a_fixture() -> None:
    source = ROOT / "data" / "security_lifecycle" / "raw" / "redemption" / "k2025_23_public_offering_20240521.pdf"

    evidence = load_akk_public_offering_redemption(source)

    assert source.is_file()
    assert evidence.source_document == str(source)
    assert evidence.source_document_sha256 == "e33e3a9f5cff48c2ef5d0fb660f26b736c583de488d68d1f2149eb23f8689459"


def test_parser_and_audit_are_network_free(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("Redemption audit attempted network I/O")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    assert _evidence().redemption_mechanics_validated is True


def test_methodology_is_partial_and_crossing_impact_is_deterministic(tmp_path: Path) -> None:
    coverage_path = tmp_path / "coverage.json"
    windows = []
    for index in range(52):
        windows.append(
            {
                "unusable_isins": ["HU0000554795"],
                "required_start": "2025-01-01",
                "required_end": "2025-07-01",
                "horizon": 180 if index < 8 else 365,
                "status": "UNUSABLE_SOURCE",
            }
        )
    coverage_path.write_text(json.dumps({"windows": windows}), encoding="utf-8")
    source = ROOT / "data" / "security_lifecycle" / "raw" / "redemption" / "k2025_23_public_offering_20240521.pdf"

    audit = redemption_audit.build_redemption_audit(coverage_path, source)

    assert audit["methodology_classification"] == "METHODOLOGY_PARTIALLY_SPECIFIED"
    impact = audit["crossing_window_impact"]
    assert impact["requires_redemption_methodology"] == 52
    assert impact["potentially_resolvable_if_redemption_methodology_approved"] == 0
    assert impact["blocked_by_missing_pre_maturity_price_history"] == 52
    assert audit["nav_equivalent"] is False
    assert audit["backtest_return_series_approved"] is False
    assert audit["usable_for_backtest"] is False
