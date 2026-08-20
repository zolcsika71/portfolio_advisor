"""Validate locally retained MNB/KELER weekly-OTC report-scope semantics.

The command is offline-only. It intentionally does not discover sources, infer
zero rows, or claim report completeness from report layout alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from portfolio_advisor.history.mnb_keler_report_scope import (
    ReportScopeEvidenceChain,
    ScopeEvidenceDocument,
)
from portfolio_advisor.history.mnb_otc import (
    REQUIRED_HEADERS,
    extract_pdf_text,
    normalize_layout_whitespace,
)

REPORT_TITLE = "Összesítő a KELER Zrt-n keresztül lebonyolított tőzsdén kívüli (OTC) értékpapír forgalomról"
SOURCE = "mnb_otc"


class ReportScopeAuditError(RuntimeError):
    """Retained report-scope source evidence is unusable."""


def load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportScopeAuditError(f"Unable to read acquisition manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("source") != SOURCE:
        raise ReportScopeAuditError("Acquisition manifest is malformed")
    return payload


def load_research_ledger(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportScopeAuditError(f"Unable to read scope research ledger: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ReportScopeAuditError("Scope research ledger is malformed")
    status = payload.get("research_status")
    if status not in {
        "REPORT_SCOPE_SEMANTICS_VALIDATED",
        "REPORT_SCOPE_SEMANTICS_PARTIAL",
        "REPORT_SCOPE_SEMANTICS_NOT_FOUND",
        "REPORT_SCOPE_SEMANTICS_CONFLICT",
    }:
        raise ReportScopeAuditError("Scope research ledger has an invalid status")
    if payload.get("stopping_rule_completed") is not True:
        raise ReportScopeAuditError("Scope research stopping rule was not completed")
    chain = payload.get("evidence_chain")
    if not isinstance(chain, dict):
        raise ReportScopeAuditError("Scope research ledger has no evidence chain")
    required = (
        "A_report_scope",
        "B_report_completeness",
        "C_row_inclusion",
        "D_zero_transaction_omission",
        "transaction_count_semantics",
    )
    if any(
        not isinstance(chain.get(link), dict)
        or chain[link].get("answer") not in {"YES", "UNKNOWN"}
        for link in required
    ):
        raise ReportScopeAuditError("Scope research evidence chain is malformed")
    accepted = payload.get("accepted_documents")
    if not isinstance(accepted, list):
        raise ReportScopeAuditError("Scope research accepted-documents list is malformed")
    for document in accepted:
        if not isinstance(document, dict):
            raise ReportScopeAuditError("Accepted scope-research source is malformed")
        local_path = document.get("local_path")
        expected_hash = document.get("sha256")
        relevant_location = document.get("relevant_location")
        if (
            not isinstance(local_path, str)
            or not isinstance(expected_hash, str)
            or not isinstance(relevant_location, str)
        ):
            raise ReportScopeAuditError("Accepted scope-research provenance is incomplete")
        source = Path(local_path)
        if not source.is_file():
            raise ReportScopeAuditError(
                f"Accepted scope-research source is missing: {source}"
            )
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
            raise ReportScopeAuditError(
                f"Accepted scope-research source hash changed: {source.name}"
            )
    return payload


def source_records(manifest: dict[str, object]) -> list[dict[str, object]]:
    records = manifest.get("local_report_inventory")
    if not isinstance(records, list):
        raise ReportScopeAuditError("Acquisition manifest has no local report inventory")
    pdfs = [record for record in records if isinstance(record, dict) and record.get("artifact_type") == "PDF"]
    if not pdfs:
        raise ReportScopeAuditError("Acquisition manifest has no retained PDF reports")
    if len(pdfs) != sum(isinstance(record, dict) and record.get("artifact_type") == "PDF" for record in records):
        raise ReportScopeAuditError("Acquisition manifest has malformed report records")
    return pdfs


def validate_retained_reports(records: list[dict[str, object]], raw_directory: Path) -> list[dict[str, object]]:
    """Check all report wording/hashes; this alone never validates completeness."""
    validated: list[dict[str, object]] = []
    for record in records:
        filename = record.get("filename")
        expected_hash = record.get("sha256")
        if not isinstance(filename, str) or not isinstance(expected_hash, str):
            raise ReportScopeAuditError("Report provenance is malformed")
        reporting_period = record.get("reporting_period")
        if not isinstance(reporting_period, dict):
            raise ReportScopeAuditError("Report provenance has no reporting period")
        period_start = reporting_period.get("start")
        period_end = reporting_period.get("end")
        if not isinstance(period_start, str) or not isinstance(period_end, str):
            raise ReportScopeAuditError("Report reporting period is malformed")
        path = raw_directory / filename
        if not path.is_file():
            raise ReportScopeAuditError(f"Retained official report is missing: {path}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ReportScopeAuditError(f"Retained official report hash changed: {filename}")
        text = extract_pdf_text(path)
        normalized = normalize_layout_whitespace(text)
        if REPORT_TITLE not in normalized:
            raise ReportScopeAuditError(f"Report title is missing: {filename}")
        if not all(header in text for header in REQUIRED_HEADERS):
            raise ReportScopeAuditError(f"Report table headers are incomplete: {filename}")
        validated.append(
            {
                "filename": filename,
                "path": str(path),
                "sha256": expected_hash,
                "authority": "KELER Központi Értéktár Zrt.",
                "host": "kozzetetelek.mnb.hu",
                "document_type": "weekly OTC report",
                "reporting_period": {"start": period_start, "end": period_end},
                "title_verified": True,
                "transaction_count_header_verified": True,
                "extraction_status": "VALIDATED",
            }
        )
    return validated


def questions(chain: ReportScopeEvidenceChain) -> dict[str, dict[str, str]]:
    absent_activity = "YES" if chain.absence_semantics_validated else "UNKNOWN"
    return {
        "q1_complete_for_stated_scope": {
            "answer": "YES" if chain.report_completeness_validated else "UNKNOWN",
            "rationale": "No retained methodology source explicitly defines completeness." if not chain.report_completeness_validated else "Validated by retained authoritative methodology.",
        },
        "q2_only_qualifying_transaction_securities_listed": {
            "answer": "YES" if chain.row_inclusion_rule_validated else "UNKNOWN",
            "rationale": "No retained row-inclusion rule was found." if not chain.row_inclusion_rule_validated else "Validated by retained authoritative methodology.",
        },
        "q3_zero_transaction_securities_omitted": {
            "answer": "YES" if chain.zero_transaction_omission_validated else "UNKNOWN",
            "rationale": "No retained zero-row omission rule was found." if not chain.zero_transaction_omission_validated else "Validated by retained authoritative methodology.",
        },
        "q4_transaction_count_qualifying_transactions": {
            "answer": "YES" if chain.transaction_count_semantics_validated else "UNKNOWN",
            "rationale": "The Tételszám header is present, but no retained formal definition establishes qualifying-transaction semantics." if not chain.transaction_count_semantics_validated else "Validated by retained authoritative methodology.",
        },
        "q5_absent_isin_no_reportable_keler_otc_activity": {
            "answer": absent_activity,
            "rationale": "The full evidence chain is required; it is incomplete in retained evidence." if absent_activity == "UNKNOWN" else "Validated only for the stated KELER-mediated reporting scope.",
        },
        "q6_absent_isin_no_otc_activity_anywhere": {"answer": "NO", "rationale": "The report scope is not all venues."},
        "q7_absent_isin_no_market_price": {"answer": "NO", "rationale": "No report row is not evidence that no market price existed."},
        "q8_absent_isin_price_zero": {"answer": "NO", "rationale": "No price is generated from an absent row."},
        "q9_absent_isin_forward_fill": {"answer": "NO", "rationale": "Absence does not validate a carry-forward valuation rule."},
        "q10_absent_isin_interpolation": {"answer": "NO", "rationale": "Absence does not establish an interpolation method."},
    }


def build_audit(
    manifest_path: Path, raw_directory: Path, research_ledger_path: Path
) -> dict[str, object]:
    manifest = load_json(manifest_path)
    authority = manifest.get("source_authority")
    host = manifest.get("source_host")
    if not isinstance(authority, str) or not isinstance(host, str):
        raise ReportScopeAuditError("Acquisition authority/host provenance is missing")
    documents = validate_retained_reports(source_records(manifest), raw_directory)
    research = load_research_ledger(research_ledger_path)
    research_chain = research["evidence_chain"]
    assert isinstance(research_chain, dict)
    def link_is_validated(link: str) -> bool:
        value = research_chain[link]
        assert isinstance(value, dict)
        return value["answer"] == "YES"

    evidence_documents = tuple(
        ScopeEvidenceDocument(
            filename=str(document["filename"]),
            sha256=str(document["sha256"]),
            authority=str(document["authority"]),
            host=str(document["host"]),
            locally_retained=True,
            official=True,
        )
        for document in documents
    )
    chain = ReportScopeEvidenceChain(
        report_scope_validated=link_is_validated("A_report_scope"),
        report_completeness_validated=link_is_validated("B_report_completeness"),
        row_inclusion_rule_validated=link_is_validated("C_row_inclusion"),
        zero_transaction_omission_validated=link_is_validated(
            "D_zero_transaction_omission"
        ),
        transaction_count_semantics_validated=link_is_validated(
            "transaction_count_semantics"
        ),
        conflicting_authoritative_evidence=(
            research["research_status"] == "REPORT_SCOPE_SEMANTICS_CONFLICT"
        ),
        documents=evidence_documents,
        bounded_research_completed=True,
        no_additional_adequate_evidence_found=(
            research["research_status"] == "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
        ),
    )
    if chain.semantic_status != research["research_status"]:
        raise ReportScopeAuditError("Scope research result does not match evidence chain")
    accepted_documents = research.get("accepted_documents")
    if not isinstance(accepted_documents, list):
        raise ReportScopeAuditError("Scope research ledger has no accepted-documents list")
    return {
        "schema_version": 1,
        "source": SOURCE,
        "source_acquisition_manifest": str(manifest_path),
        "scope_research_evidence_ledger": str(research_ledger_path),
        "research_run_metadata": {
            "research_status": research["research_status"],
            "stopping_rule_completed": research["stopping_rule_completed"],
            "source_families_searched": research.get("source_families_searched"),
            "query_families_searched": research.get("query_families_searched"),
            "candidate_document_count": research.get("candidate_document_count"),
            "accepted_document_count": research.get("accepted_document_count"),
            "hard_limits": research.get("hard_limits"),
        },
        "authorities": ["KELER Központi Értéktár Zrt."],
        "hosts": [host],
        "retained_source_documents": documents,
        "retained_report_wording": {
            "uniform_report_count": len(documents),
            "report_title": REPORT_TITLE,
            "report_title_semantics": "KELER-mediated OTC securities turnover report",
            "transaction_count_column": "Tételszám",
            "reporting_period_and_table_headers_verified": True,
            "footnotes_or_inclusion_criteria_found": False,
        },
        "external_authoritative_sources": accepted_documents,
        "additional_authoritative_sources_found": len(accepted_documents),
        "evidence_chain": {
            "report_scope_validated": chain.report_scope_validated,
            "report_completeness_validated": chain.report_completeness_validated,
            "row_inclusion_rule_validated": chain.row_inclusion_rule_validated,
            "zero_transaction_omission_validated": chain.zero_transaction_omission_validated,
            "transaction_count_semantics_validated": chain.transaction_count_semantics_validated,
            "absence_semantics_validated": chain.absence_semantics_validated,
            "retained_authoritative_document_count": len(chain.documents),
        },
        "semantic_status": chain.semantic_status,
        "supported_conclusions": [
            "A retained weekly PDF reports KELER-mediated OTC securities turnover for its stated period.",
            "A listed exact ISIN has a reported aggregate observation with a Tételszám field.",
        ],
        "unsupported_conclusions": [
            "An absent ISIN proves zero reportable KELER-mediated OTC activity.",
            "An absent ISIN proves no all-venue activity, no market price, price zero, or security inactivity.",
            "An absent ISIN enables forward-fill, interpolation, nearest-date substitution, daily conversion, or return calculation.",
        ],
        "validation_warnings": [
            "Retained report layout/title is not evidence of a formal completeness policy.",
            "Retained report rows and Tételszám header are not evidence of a formal zero-transaction omission rule.",
            research["research_conclusion"],
        ],
        "remaining_evidence_gaps": research.get("remaining_gaps"),
        "conflicts": research.get("conflicts"),
        "questions": questions(chain),
        "exact_reclassification_decision": (
            "PROMOTE_ABSENT_ROWS_TO_NO_REPORTED_KELER_OTC_ACTIVITY"
            if chain.absence_semantics_validated
            else "RETAIN_NO_EXACT_ISIN_OBSERVATION"
        ),
        "recommended_next_action": (
            "VALIDATE_MNB_OTC_PRICE_QUOTATION_SEMANTICS"
            if chain.absence_semantics_validated
            else "FREEZE_MNB_KELER_ABSENCE_SEMANTICS_AS_UNKNOWN_AND_MOVE_TO_PRICE_SEMANTICS"
            if chain.semantic_status == "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
            else "TARGET_REMAINING_MNB_KELER_SCOPE_EVIDENCE_GAPS"
        ),
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
        "synthetic_observations_created": 0,
        "zero_prices_created": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/mnb_otc/report_acquisition_manifest.json"))
    parser.add_argument("--raw-directory", type=Path, default=Path("data/mnb_otc/raw"))
    parser.add_argument(
        "--research-ledger",
        type=Path,
        default=Path("data/audit/mnb_keler_scope_research_evidence.json"),
    )
    parser.add_argument("--output", type=Path, default=Path("data/audit/mnb_keler_report_scope_semantics.json"))
    args = parser.parse_args()
    try:
        audit = build_audit(args.manifest, args.raw_directory, args.research_ledger)
    except ReportScopeAuditError as exc:
        print(f"MNB/KELER report-scope audit failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"MNB/KELER retained reports checked: {len(audit['retained_source_documents'])}")
    print(f"Report-scope semantics: {audit['semantic_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
