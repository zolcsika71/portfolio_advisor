"""Audit sparse MNB/KELER OTC-report semantics using retained local PDFs only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from portfolio_advisor.history.mnb_keler_absence_semantics import (
    classify_frozen_absence,
)
from portfolio_advisor.history.mnb_keler_report_scope import (
    ReportScopeEvidenceChain,
    ScopeEvidenceDocument,
)
from portfolio_advisor.history.mnb_otc import (
    REQUIRED_HEADERS,
    TARGET_HU_ISIN,
    MnbOtcRepository,
    extract_pdf_text,
    normalize_layout_whitespace,
)
from portfolio_advisor.history.sparse_trading_semantics import (
    ReportScopeEvidence,
    SparseTradingSemanticsError,
    build_sparse_trading_semantics,
    methodology_assessment,
    report_from_manifest,
)

TARGET_SERIES = "K2025/23"
TARGET_NAME = "K250604 Egyéves Magyar Állampapír"
REQUIRED_START = date(2024, 7, 2)
REPORT_SCOPE_STATEMENT = "Összesítő a KELER Zrt-n keresztül lebonyolított tőzsdén kívüli (OTC) értékpapír forgalomról"


class SparseAuditError(RuntimeError):
    """Required local sparse-trading audit input is invalid."""


def load_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SparseAuditError(f"Unable to read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SparseAuditError(f"{label} must be an object")
    return payload


def load_maturity(path: Path) -> date:
    payload = load_json(path, "lifecycle audit")
    if (
        payload.get("isin") != TARGET_HU_ISIN
        or payload.get("maturity_validated") is not True
    ):
        raise SparseAuditError("Lifecycle audit does not validate exact-ISIN maturity")
    value = payload.get("maturity_date")
    if not isinstance(value, str):
        raise SparseAuditError("Lifecycle audit maturity date is missing")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SparseAuditError("Lifecycle audit maturity date is malformed") from exc


def load_report_scope_semantics(path: Path) -> tuple[ReportScopeEvidenceChain, str]:
    """Read the separately generated offline scope-evidence conclusion."""
    payload = load_json(path, "MNB/KELER report-scope semantics audit")
    if payload.get("source") != "mnb_otc":
        raise SparseAuditError("Report-scope semantics audit has an unexpected source")
    evidence = payload.get("evidence_chain")
    if not isinstance(evidence, dict):
        raise SparseAuditError("Report-scope semantics audit has no evidence chain")
    fields = (
        "report_scope_validated",
        "report_completeness_validated",
        "row_inclusion_rule_validated",
        "zero_transaction_omission_validated",
        "transaction_count_semantics_validated",
    )
    if any(not isinstance(evidence.get(field), bool) for field in fields):
        raise SparseAuditError("Report-scope semantics evidence chain is malformed")
    status = payload.get("semantic_status")
    if status not in {
        "REPORT_SCOPE_SEMANTICS_VALIDATED",
        "REPORT_SCOPE_SEMANTICS_PARTIAL",
        "REPORT_SCOPE_SEMANTICS_NOT_FOUND",
        "REPORT_SCOPE_SEMANTICS_UNKNOWN",
        "REPORT_SCOPE_SEMANTICS_CONFLICT",
    }:
        raise SparseAuditError("Report-scope semantics status is invalid")
    if (
        payload.get("nav_equivalent") is not False
        or payload.get("backtest_return_series_approved") is not False
        or payload.get("usable_for_backtest") is not False
    ):
        raise SparseAuditError("Report-scope semantics audit has unsafe approval flags")
    documents = payload.get("retained_source_documents")
    if not isinstance(documents, list) or not documents:
        raise SparseAuditError("Report-scope semantics audit has no retained documents")
    evidence_documents: list[ScopeEvidenceDocument] = []
    for document in documents:
        if not isinstance(document, dict):
            raise SparseAuditError("Report-scope source document is malformed")
        filename = document.get("filename")
        sha256 = document.get("sha256")
        authority = document.get("authority")
        host = document.get("host")
        if not all(isinstance(value, str) for value in (filename, sha256, authority, host)):
            raise SparseAuditError("Report-scope source provenance is malformed")
        evidence_documents.append(
            ScopeEvidenceDocument(
                filename=filename,
                sha256=sha256,
                authority=authority,
                host=host,
                locally_retained=True,
                official=True,
            )
        )
    chain = ReportScopeEvidenceChain(
        report_scope_validated=evidence["report_scope_validated"],
        report_completeness_validated=evidence["report_completeness_validated"],
        row_inclusion_rule_validated=evidence["row_inclusion_rule_validated"],
        zero_transaction_omission_validated=evidence[
            "zero_transaction_omission_validated"
        ],
        transaction_count_semantics_validated=evidence[
            "transaction_count_semantics_validated"
        ],
        conflicting_authoritative_evidence=status == "REPORT_SCOPE_SEMANTICS_CONFLICT",
        documents=tuple(evidence_documents),
        bounded_research_completed=status == "REPORT_SCOPE_SEMANTICS_NOT_FOUND",
        no_additional_adequate_evidence_found=(
            status == "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
        ),
    )
    if chain.semantic_status != status:
        raise SparseAuditError("Report-scope semantics status does not match evidence")
    return chain, status


def load_absence_freeze(path: Path) -> dict[str, object]:
    """Read the terminal, non-promoting absent-row decision."""
    payload = load_json(path, "MNB/KELER absence-semantics freeze")
    if (
        payload.get("absence_semantics_status")
        != "AUTHORITATIVE_EVIDENCE_NOT_FOUND"
        or payload.get("frozen_interpretation") != "ABSENCE_SEMANTICS_UNKNOWN"
        or payload.get("absence_semantics_research_closed") is not True
        or payload.get("absence_semantics_validated") is not False
        or payload.get("absent_report_classification") != "NO_EXACT_ISIN_OBSERVATION"
    ):
        raise SparseAuditError("Absence-semantics freeze is malformed or unsafe")
    return payload


def load_price_semantics(path: Path) -> dict[str, object]:
    """Read additive quotation research without granting return approval."""
    payload = load_json(path, "MNB/KELER price-semantics audit")
    if (
        payload.get("isin") != TARGET_HU_ISIN
        or payload.get("price_semantics_status")
        not in {
            "MNB_OTC_PRICE_SEMANTICS_VALIDATED",
            "MNB_OTC_PRICE_SEMANTICS_PARTIAL",
            "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND",
            "MNB_OTC_PRICE_SEMANTICS_CONFLICT",
        }
        or payload.get("nav_equivalent") is not False
        or payload.get("backtest_return_series_approved") is not False
        or payload.get("usable_for_backtest") is not False
    ):
        raise SparseAuditError("Price-semantics audit is malformed or unsafe")
    return payload


def _source_records(manifest: dict[str, object]) -> list[dict[str, object]]:
    if manifest.get("source") != "mnb_otc":
        raise SparseAuditError("MNB acquisition manifest has unexpected source")
    authority = manifest.get("source_authority")
    records = manifest.get("local_report_inventory")
    if not isinstance(authority, str) or not authority or not isinstance(records, list):
        raise SparseAuditError(
            "MNB acquisition manifest has no authoritative local inventory"
        )
    pdfs = [
        item
        for item in records
        if isinstance(item, dict) and item.get("artifact_type") == "PDF"
    ]
    if len(pdfs) != len(records):
        # Text fixtures/extracts are never production source evidence.
        non_source = [
            item
            for item in records
            if isinstance(item, dict) and item.get("artifact_type") != "PDF"
        ]
        if len(pdfs) + len(non_source) != len(records):
            raise SparseAuditError("MNB local inventory contains malformed records")
    if not pdfs:
        raise SparseAuditError("MNB acquisition manifest has no retained PDFs")
    return pdfs


def validate_report_scope_documents(
    records: list[dict[str, object]], raw_directory: Path
) -> list[dict[str, object]]:
    """Validate document identity/scope wording without inventing absence semantics."""
    documents: list[dict[str, object]] = []
    for record in records:
        filename = record.get("filename")
        digest = record.get("sha256")
        if not isinstance(filename, str) or not isinstance(digest, str):
            raise SparseAuditError("MNB inventory record has malformed provenance")
        path = raw_directory / filename
        if not path.is_file():
            raise SparseAuditError(f"Retained MNB report is missing: {path}")
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_digest != digest:
            raise SparseAuditError(f"Retained MNB report SHA-256 mismatch: {filename}")
        text = extract_pdf_text(path)
        normalized = normalize_layout_whitespace(text)
        if REPORT_SCOPE_STATEMENT not in normalized:
            raise SparseAuditError(
                f"MNB report lacks the validated scope statement: {filename}"
            )
        if not all(header in text for header in REQUIRED_HEADERS):
            raise SparseAuditError(f"MNB report lacks required OTC columns: {filename}")
        documents.append(
            {
                "filename": filename,
                "path": str(path),
                "sha256": digest,
                "scope_statement_verified": True,
                "transaction_count_column_verified": True,
            }
        )
    return documents


def methodology_questions() -> dict[str, dict[str, str]]:
    """Answers are separate from source facts and never grant return approval."""
    return {
        "q1_zero_otc_transactions": {
            "answer": "UNKNOWN",
            "rationale": "The retained report title identifies KELER-mediated OTC turnover but does not document an all-security zero-row suppression/completeness rule.",
        },
        "q2_zero_keler_settled_otc_transactions": {
            "answer": "UNKNOWN",
            "rationale": "No retained methodology document explicitly states that every reportable security-period appears or that an absent ISIN represents zero reportable KELER-settled transactions.",
        },
        "q3_zero_market_activity_all_venues": {
            "answer": "NO",
            "rationale": "The report scope is KELER-mediated OTC activity, not all trading venues.",
        },
        "q4_no_market_price_existed": {
            "answer": "NO",
            "rationale": "No exact reported OTC aggregate is weaker than evidence that no market price existed.",
        },
        "q5_forward_fill_previous_average_price": {
            "answer": "NO",
            "rationale": "No authoritative carry-forward/valuation rule is retained; coupon and quotation-convention uncertainty remains.",
        },
        "q6_interpolate_missing_periods": {
            "answer": "NO",
            "rationale": "Irregular transaction aggregates do not establish an interpolation path.",
        },
        "q7_nearest_weekly_price_as_exact_boundary": {
            "answer": "NO",
            "rationale": "A weekly aggregate is not an exact start, end, or maturity boundary observation.",
        },
        "q8_daily_returns": {"answer": "NO", "rationale": "No daily series exists."},
        "q9_daily_volatility": {"answer": "NO", "rationale": "No daily series exists."},
        "q10_sharpe": {
            "answer": "NO",
            "rationale": "No approved return series exists.",
        },
        "q11_maximum_drawdown": {
            "answer": "NO",
            "rationale": "No approved ordered valuation series exists.",
        },
        "q12_var_cvar": {
            "answer": "NO",
            "rationale": "No approved return distribution exists.",
        },
        "q13_point_to_point_descriptive_price_change": {
            "answer": "YES",
            "rationale": "Two actual reported average OTC prices can be compared descriptively, without calling the change an economic return.",
        },
        "q14_ready_for_quotation_semantics_research": {
            "answer": "NO",
            "rationale": "Absence semantics remain incomplete; report-scope methodology must be resolved before treating no-row periods more strongly.",
        },
    }


def build_audit(
    acquisition_manifest_path: Path,
    raw_directory: Path,
    database_path: Path,
    lifecycle_path: Path,
    report_scope_semantics_path: Path,
    absence_semantics_freeze_path: Path,
    price_semantics_path: Path,
) -> dict[str, object]:
    manifest = load_json(acquisition_manifest_path, "MNB acquisition manifest")
    authority = manifest.get("source_authority")
    if not isinstance(authority, str):
        raise SparseAuditError("MNB acquisition manifest source authority is missing")
    records = _source_records(manifest)
    documents = validate_report_scope_documents(records, raw_directory)
    scope_chain, scope_status = load_report_scope_semantics(report_scope_semantics_path)
    absence_freeze = load_absence_freeze(absence_semantics_freeze_path)
    price_semantics = load_price_semantics(price_semantics_path)
    # A completed UNKNOWN decision is an audit-domain guard, not a heuristic.
    # Neither repeated no-row reports nor table structure may reach the stronger
    # no-reported-activity class through this path.
    if (
        scope_chain.absence_semantics_validated
        or classify_frozen_absence(exact_isin_present=False)
        != "NO_EXACT_ISIN_OBSERVATION"
    ):
        raise SparseAuditError("Frozen absent-row semantics would be promoted unsafely")
    evidence = ReportScopeEvidence(
        authoritative=True,
        scope_statement=REPORT_SCOPE_STATEMENT,
        transaction_count_column_present=True,
        complete_period_absence_policy_validated=scope_chain.absence_semantics_validated,
        absence_policy_reason=(
            "The separately retained report-scope evidence chain is incomplete for "
            "report completeness, qualifying-row inclusion, zero-row omission, and "
            "formal Tételszám semantics."
            if not scope_chain.absence_semantics_validated
            else "The retained report-scope evidence chain validates the limited "
            "no-reported-KELER-OTC-activity conclusion."
        ),
    )
    report_evidence = [
        report_from_manifest(item, source_authority=authority) for item in records
    ]
    maturity = load_maturity(lifecycle_path)
    observations = MnbOtcRepository(database_path).observations(TARGET_HU_ISIN)
    try:
        metrics = build_sparse_trading_semantics(
            report_evidence,
            observations,
            scope=evidence,
            required_start=REQUIRED_START,
            maturity_date=maturity,
        )
    except SparseTradingSemanticsError as exc:
        raise SparseAuditError(str(exc)) from exc
    guardrails = methodology_assessment()
    return {
        "schema_version": 1,
        "isin": TARGET_HU_ISIN,
        "series": TARGET_SERIES,
        "instrument_name": TARGET_NAME,
        "currency": "HUF",
        "source": "mnb_otc",
        "methodology_status": "SPARSE_TRADING_SEMANTICS_PARTIAL",
        "authoritative_semantic_evidence": {
            "source_authority": authority,
            "scope_statement": REPORT_SCOPE_STATEMENT,
            "transaction_count_column": "Tételszám",
            "scope_established": "KELER-mediated OTC securities turnover report",
            "complete_period_absence_policy_validated": scope_chain.absence_semantics_validated,
            "absence_policy_reason": evidence.absence_policy_reason,
        },
        "report_scope_semantics": {
            "artifact": str(report_scope_semantics_path),
            "semantic_status": scope_status,
            "evidence_chain": {
                "report_scope_validated": scope_chain.report_scope_validated,
                "report_completeness_validated": scope_chain.report_completeness_validated,
                "row_inclusion_rule_validated": scope_chain.row_inclusion_rule_validated,
                "zero_transaction_omission_validated": scope_chain.zero_transaction_omission_validated,
                "transaction_count_semantics_validated": scope_chain.transaction_count_semantics_validated,
                "absence_semantics_validated": scope_chain.absence_semantics_validated,
            },
            "research_status": scope_status,
            "absent_row_semantic_status": (
                "NO_REPORTED_KELER_OTC_ACTIVITY"
                if scope_chain.absence_semantics_validated
                else "NO_EXACT_ISIN_OBSERVATION"
            ),
            "missing_evidence_links": [
                name
                for name, validated in (
                    (
                        "B_report_completeness",
                        scope_chain.report_completeness_validated,
                    ),
                    ("C_row_inclusion", scope_chain.row_inclusion_rule_validated),
                    (
                        "D_zero_transaction_omission",
                        scope_chain.zero_transaction_omission_validated,
                    ),
                    (
                        "transaction_count_semantics",
                        scope_chain.transaction_count_semantics_validated,
                    ),
                )
                if not validated
            ],
        },
        "absence_semantics_freeze": {
            "artifact": str(absence_semantics_freeze_path),
            "status": absence_freeze["absence_semantics_status"],
            "frozen_interpretation": absence_freeze["frozen_interpretation"],
            "research_closed": True,
            "absent_report_classification": "NO_EXACT_ISIN_OBSERVATION",
            "reopen_policy": absence_freeze.get("reopen_policy"),
        },
        "price_semantics": {
            "artifact": str(price_semantics_path),
            "status": price_semantics["price_semantics_status"],
            "validated_price_properties": price_semantics.get(
                "validated_price_properties"
            ),
            "unknown_price_properties": price_semantics.get(
                "unknown_price_properties"
            ),
            "return_suitability": price_semantics.get("return_suitability"),
        },
        "source_document_provenance": documents,
        **metrics,
        "forward_fill_assessment": {
            "methodologically_approved": guardrails[
                "forward_fill_methodologically_approved"
            ],
            "rationale": guardrails["forward_fill_rationale"],
        },
        "interpolation_assessment": {
            "methodologically_approved": guardrails[
                "interpolation_methodologically_approved"
            ],
            "rationale": guardrails["interpolation_rationale"],
        },
        "nearest_date_boundary_assessment": {
            "methodologically_approved": guardrails[
                "nearest_date_boundary_methodologically_approved"
            ],
            "rationale": guardrails["nearest_date_boundary_rationale"],
        },
        "metric_support_assessment": {
            "point_to_point_descriptive_price_change": guardrails[
                "point_to_point_descriptive_price_change_supported"
            ],
            "daily_returns": guardrails["daily_return_series_supported"],
            "daily_volatility": guardrails["daily_volatility_supported"],
            "sharpe": guardrails["sharpe_supported"],
            "maximum_drawdown": guardrails["maximum_drawdown_supported"],
            "var_cvar": guardrails["var_cvar_supported"],
        },
        "methodology_questions": methodology_questions(),
        "unresolved_questions": [
            "MNB/KELER absence semantics are frozen as unknown pending qualifying new primary evidence.",
            "MNB OTC clean/dirty quotation convention and field semantics.",
            "Day-count/accrual convention relevant to an economic return methodology.",
            "Approved pre-maturity valuation series and post-maturity portfolio policy.",
        ],
        "recommended_next_action": "ASSESS_ALTERNATIVE_AUTHORITATIVE_PRICE_SOURCE_FOR_HU0000554795"
        if price_semantics["price_semantics_status"] == "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND"
        else "RESOLVE_REMAINING_MNB_OTC_PRICE_SEMANTICS_GAPS",
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
        "synthetic_observations_created": 0,
        "zero_prices_created": 0,
        "daily_resampling_performed": False,
        "return_metrics_implemented": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acquisition-manifest",
        type=Path,
        default=Path("data/mnb_otc/report_acquisition_manifest.json"),
    )
    parser.add_argument("--raw-directory", type=Path, default=Path("data/mnb_otc/raw"))
    parser.add_argument(
        "--database", type=Path, default=Path("database/model_portfolio.sqlite")
    )
    parser.add_argument(
        "--lifecycle", type=Path, default=Path("data/audit/hu0000554795_lifecycle.json")
    )
    parser.add_argument(
        "--report-scope-semantics",
        type=Path,
        default=Path("data/audit/mnb_keler_report_scope_semantics.json"),
    )
    parser.add_argument(
        "--absence-semantics-freeze",
        type=Path,
        default=Path("data/audit/mnb_keler_absence_semantics_freeze.json"),
    )
    parser.add_argument(
        "--price-semantics",
        type=Path,
        default=Path("data/audit/mnb_keler_price_semantics.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/audit/hu0000554795_sparse_trading_semantics.json"),
    )
    args = parser.parse_args()
    try:
        audit = build_audit(
            args.acquisition_manifest,
            args.raw_directory,
            args.database,
            args.lifecycle,
            args.report_scope_semantics,
            args.absence_semantics_freeze,
            args.price_semantics,
        )
    except SparseAuditError as exc:
        print(
            f"HU0000554795 sparse-trading audit failed closed: {exc}", file=sys.stderr
        )
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"HU0000554795 acquired reports: {audit['report_count']}")
    print(
        f"HU0000554795 positive OTC observations: {audit['positive_observation_count']}"
    )
    print("Sparse-trading methodology status: PARTIAL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
