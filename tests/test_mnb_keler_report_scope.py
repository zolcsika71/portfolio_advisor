from __future__ import annotations

import hashlib
import importlib.util
import socket
import sys
from pathlib import Path

import pytest

from portfolio_advisor.history.mnb_keler_report_scope import (
    ReportScopeEvidenceChain,
    ReportScopeSemanticsError,
    ScopeEvidenceDocument,
    classify_report_row,
)
from portfolio_advisor.history.sparse_trading_semantics import (
    NO_EXACT_ISIN_OBSERVATION,
    NO_REPORTED_KELER_OTC_ACTIVITY,
    OBSERVED_OTC_ACTIVITY,
    REPORT_CONFLICT,
    STRUCTURAL_UNCERTAINTY,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "audit_mnb_keler_report_scope_semantics",
    ROOT / "scripts" / "audit_mnb_keler_report_scope_semantics.py",
)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
audit_script = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = audit_script
SCRIPT_SPEC.loader.exec_module(audit_script)


def complete_chain() -> ReportScopeEvidenceChain:
    return ReportScopeEvidenceChain(
        report_scope_validated=True,
        report_completeness_validated=True,
        row_inclusion_rule_validated=True,
        zero_transaction_omission_validated=True,
        transaction_count_semantics_validated=True,
        documents=(
            ScopeEvidenceDocument(
                filename="official.pdf",
                sha256="a" * 64,
                authority="KELER",
                host="kozzetetelek.mnb.hu",
                locally_retained=True,
                official=True,
            ),
        ),
    )


def test_complete_evidence_chain_promotes_only_narrow_absence_status() -> None:
    chain = complete_chain()

    assert chain.semantic_status == "REPORT_SCOPE_SEMANTICS_VALIDATED"
    assert chain.absence_semantics_validated is True
    assert (
        classify_report_row(
            structure_valid=True, exact_isin_present=False, chain=chain
        )
        == NO_REPORTED_KELER_OTC_ACTIVITY
    )
    assert (
        classify_report_row(structure_valid=True, exact_isin_present=True, chain=chain)
        == OBSERVED_OTC_ACTIVITY
    )


@pytest.mark.parametrize(
    "field",
    [
        "report_completeness_validated",
        "row_inclusion_rule_validated",
        "zero_transaction_omission_validated",
        "transaction_count_semantics_validated",
    ],
)
def test_missing_required_evidence_link_fails_closed(field: str) -> None:
    chain = ReportScopeEvidenceChain(
        report_scope_validated=True,
        report_completeness_validated=field != "report_completeness_validated",
        row_inclusion_rule_validated=field != "row_inclusion_rule_validated",
        zero_transaction_omission_validated=(
            field != "zero_transaction_omission_validated"
        ),
        transaction_count_semantics_validated=(
            field != "transaction_count_semantics_validated"
        ),
        documents=complete_chain().documents,
    )

    assert chain.semantic_status == "REPORT_SCOPE_SEMANTICS_PARTIAL"
    assert chain.absence_semantics_validated is False
    assert (
        classify_report_row(
            structure_valid=True, exact_isin_present=False, chain=chain
        )
        == NO_EXACT_ISIN_OBSERVATION
    )


def test_conflicting_semantics_and_malformed_layout_never_promote_absence() -> None:
    conflict = ReportScopeEvidenceChain(
        report_scope_validated=True,
        report_completeness_validated=True,
        row_inclusion_rule_validated=True,
        zero_transaction_omission_validated=True,
        transaction_count_semantics_validated=True,
        conflicting_authoritative_evidence=True,
        documents=complete_chain().documents,
    )

    assert conflict.semantic_status == "REPORT_SCOPE_SEMANTICS_CONFLICT"
    assert (
        classify_report_row(
            structure_valid=True, exact_isin_present=False, chain=conflict
        )
        == REPORT_CONFLICT
    )
    assert (
        classify_report_row(
            structure_valid=False, exact_isin_present=False, chain=complete_chain()
        )
        == STRUCTURAL_UNCERTAINTY
    )


def test_only_retained_official_documents_are_eligible_evidence() -> None:
    with pytest.raises(ReportScopeSemanticsError, match="locally retained"):
        ScopeEvidenceDocument(
            filename="fixture.txt",
            sha256="a" * 64,
            authority="Test fixture",
            host="example.test",
            locally_retained=False,
            official=True,
        )
    with pytest.raises(ReportScopeSemanticsError, match="locally retained"):
        ScopeEvidenceDocument(
            filename="unofficial.pdf",
            sha256="a" * 64,
            authority="Unofficial source",
            host="example.test",
            locally_retained=True,
            official=False,
        )


def test_boolean_claims_without_retained_evidence_cannot_promote_semantics() -> None:
    fixture_only_claim = ReportScopeEvidenceChain(
        report_scope_validated=True,
        report_completeness_validated=True,
        row_inclusion_rule_validated=True,
        zero_transaction_omission_validated=True,
        transaction_count_semantics_validated=True,
    )

    assert fixture_only_claim.semantic_status == "REPORT_SCOPE_SEMANTICS_PARTIAL"
    assert fixture_only_claim.absence_semantics_validated is False


def test_bounded_not_found_research_keeps_absent_report_classification() -> None:
    chain = ReportScopeEvidenceChain(
        report_scope_validated=True,
        report_completeness_validated=False,
        row_inclusion_rule_validated=False,
        zero_transaction_omission_validated=False,
        transaction_count_semantics_validated=False,
        documents=complete_chain().documents,
        bounded_research_completed=True,
        no_additional_adequate_evidence_found=True,
    )

    assert chain.semantic_status == "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
    assert (
        classify_report_row(
            structure_valid=True, exact_isin_present=False, chain=chain
        )
        == NO_EXACT_ISIN_OBSERVATION
    )


def test_retained_report_hash_is_verified_and_changed_hash_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.pdf"
    report.write_bytes(b"retained report bytes")
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    monkeypatch.setattr(
        audit_script,
        "extract_pdf_text",
        lambda _: (
            audit_script.REPORT_TITLE
            + "\n"
            + " ".join(audit_script.REQUIRED_HEADERS)
        ),
    )
    records = [
        {
            "filename": report.name,
            "sha256": digest,
            "reporting_period": {"start": "2024-01-01", "end": "2024-01-07"},
        }
    ]

    validated = audit_script.validate_retained_reports(records, tmp_path)

    assert validated[0]["sha256"] == digest
    with pytest.raises(audit_script.ReportScopeAuditError, match="hash changed"):
        audit_script.validate_retained_reports(
            [
                {
                    "filename": report.name,
                    "sha256": "b" * 64,
                    "reporting_period": {
                        "start": "2024-01-01",
                        "end": "2024-01-07",
                    },
                }
            ],
            tmp_path,
        )


def test_scope_classification_has_no_network_or_price_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )

    status = classify_report_row(
        structure_valid=True,
        exact_isin_present=False,
        chain=ReportScopeEvidenceChain(
            report_scope_validated=True,
            report_completeness_validated=False,
            row_inclusion_rule_validated=False,
            zero_transaction_omission_validated=False,
            transaction_count_semantics_validated=False,
            documents=complete_chain().documents,
        ),
    )

    assert status == NO_EXACT_ISIN_OBSERVATION
