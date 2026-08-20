from __future__ import annotations

import importlib.util
import socket
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.history.security_lifecycle import (
    SecurityLifecycleError,
    SecurityLifecycleEvidence,
    extract_pdf_layout_text,
    load_akk_issuance_lifecycle,
    parse_akk_issuance_lifecycle_text,
    require_consistent_lifecycle_evidence,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests" / "fixtures" / "hu0000554795_lifecycle_extract.txt").read_text(
    encoding="utf-8"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "audit_hu0000554795_lifecycle", ROOT / "scripts" / "audit_hu0000554795_lifecycle.py"
)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
lifecycle_audit = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = lifecycle_audit
SCRIPT_SPEC.loader.exec_module(lifecycle_audit)


def _evidence() -> SecurityLifecycleEvidence:
    return parse_akk_issuance_lifecycle_text(FIXTURE, "fixture.pdf", "a" * 64)


def test_extracts_exact_isin_column_not_adjacent_security_column() -> None:
    evidence = _evidence()

    assert evidence.isin == "HU0000554795"
    assert evidence.series == "K2025/23"
    assert evidence.currency == "HUF"
    assert evidence.issue_date == date(2024, 6, 4)
    assert evidence.maturity_date == date(2025, 6, 4)
    assert evidence.coupon_rate == Decimal("6.00")
    assert evidence.source_authority is not None
    assert evidence.source_host is None
    assert evidence.redemption_date is None
    assert evidence.redemption_value is None
    assert evidence.coupon_frequency is None
    assert evidence.maturity_validated is True
    assert evidence.redemption_mechanics_validated is False


def test_retained_pdf_is_hashed_and_extracts_exact_lifecycle_evidence() -> None:
    source = ROOT / "data" / "security_lifecycle" / "raw" / "mnbreport_20240527_20240531.pdf"

    assert source.is_file()
    assert sha256_file(source) == sha256_file(source)
    assert "HU0000554795" in extract_pdf_layout_text(source)
    evidence = load_akk_issuance_lifecycle(source)
    assert evidence.maturity_date == date(2025, 6, 4)


def test_invalid_pdf_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "not-a-pdf.pdf"
    source.write_bytes(b"not a PDF")

    with pytest.raises(SecurityLifecycleError, match="not a PDF"):
        extract_pdf_layout_text(source)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("HU0000554796", "exact ISIN"),
        ("HU0000554795              HU0000554795", "exact ISIN"),
        ("2025. 06. 04.", "issue date must precede"),
    ],
)
def test_lifecycle_extraction_fails_closed_for_identity_or_dates(
    replacement: str, message: str
) -> None:
    if replacement.startswith("2025"):
        text = FIXTURE.replace("2024. 06. 04.", replacement)
    else:
        text = FIXTURE.replace("HU0000554795", replacement)
    with pytest.raises(SecurityLifecycleError, match=message):
        parse_akk_issuance_lifecycle_text(text, "fixture.pdf", "a" * 64)


def test_lifecycle_record_conflict_is_rejected_not_selected() -> None:
    first = _evidence()
    conflicting = replace(first, maturity_date=date(2025, 6, 5))
    with pytest.raises(SecurityLifecycleError, match="LIFECYCLE_DATE_CONFLICT"):
        require_consistent_lifecycle_evidence((first, conflicting))


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2025, 5, 1), date(2025, 6, 3), "PRE_MATURITY"),
        (date(2025, 5, 1), date(2025, 6, 4), "ENDS_ON_MATURITY"),
        (date(2025, 5, 1), date(2025, 6, 5), "CROSSES_MATURITY"),
        (date(2025, 6, 4), date(2025, 7, 1), "STARTS_ON_MATURITY"),
        (date(2025, 6, 5), date(2025, 7, 1), "POST_MATURITY"),
    ],
)
def test_exact_maturity_window_classification(
    start: date, end: date, expected: str
) -> None:
    classification, flags = lifecycle_audit.classify_window(start, end, date(2025, 6, 4))

    assert classification == expected
    assert flags["requires_post_maturity_lifecycle_handling"] is (
        expected in {"CROSSES_MATURITY", "STARTS_ON_MATURITY", "POST_MATURITY"}
    )


def test_unknown_maturity_stays_unknown_and_creates_no_price() -> None:
    classification, flags = lifecycle_audit.classify_window(date(2025, 5, 1), date(2025, 7, 1), None)

    assert classification == "LIFECYCLE_UNKNOWN"
    assert not any(flags.values())
    assert _evidence().redemption_value is None


def test_lifecycle_parser_is_offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("Lifecycle parser attempted network I/O")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    assert _evidence().maturity_date == date(2025, 6, 4)


def test_actual_window_reconciliation_requires_expected_totals() -> None:
    windows = [{"required_start": "2025-05-01", "required_end": "2025-06-03", "horizon": 90}]
    _, overall, by_horizon = lifecycle_audit.classify_actual_windows(windows, date(2025, 6, 4))
    with pytest.raises(lifecycle_audit.LifecycleAuditError, match="132"):
        lifecycle_audit._validate_actual_reconciliation(windows, overall, by_horizon)
