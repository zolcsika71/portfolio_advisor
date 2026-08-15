"""Audit sparse MNB/KELER OTC-report semantics using retained local PDFs only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

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
) -> dict[str, object]:
    manifest = load_json(acquisition_manifest_path, "MNB acquisition manifest")
    authority = manifest.get("source_authority")
    if not isinstance(authority, str):
        raise SparseAuditError("MNB acquisition manifest source authority is missing")
    records = _source_records(manifest)
    documents = validate_report_scope_documents(records, raw_directory)
    evidence = ReportScopeEvidence(
        authoritative=True,
        scope_statement=REPORT_SCOPE_STATEMENT,
        transaction_count_column_present=True,
        complete_period_absence_policy_validated=False,
        absence_policy_reason=(
            "Retained report wording establishes KELER-mediated OTC turnover scope and a transaction-count column, "
            "but not an explicit zero-row/completeness policy for every security-period."
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
            "complete_period_absence_policy_validated": False,
            "absence_policy_reason": evidence.absence_policy_reason,
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
            "Whether an absent ISIN from a retained report proves zero reportable KELER-settled OTC transactions.",
            "MNB OTC clean/dirty quotation convention.",
            "Day-count/accrual convention relevant to an economic return methodology.",
            "Approved pre-maturity valuation series and post-maturity portfolio policy.",
        ],
        "recommended_next_action": "RESEARCH_AUTHORITATIVE_MNB_KELER_REPORT_SCOPE_SEMANTICS",
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
        "--output",
        type=Path,
        default=Path("data/audit/hu0000554795_sparse_trading_semantics.json"),
    )
    args = parser.parse_args()
    try:
        audit = build_audit(
            args.acquisition_manifest, args.raw_directory, args.database, args.lifecycle
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
