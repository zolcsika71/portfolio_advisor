"""Offline, evidence-scoped backtest-resolvability decisions.

This audit domain intentionally ends before portfolio missing-data policy.  A
resolution records why a defensible return series cannot currently be approved;
it neither changes a source nor changes portfolio constituents or coverage.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from datetime import date

TARGET_ISIN = "HU0000554795"
TARGET_CURRENCY = "HUF"
REQUIRED_START = date(2024, 7, 2)
REQUIRED_END = date(2025, 6, 4)
RESOLUTION_STATUS = "BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE"
EVIDENCE_SCOPE = "CURRENT_VALIDATED_PUBLIC_EVIDENCE"
DEFAULT_ARTIFACT_REFERENCES = {
    "erste_diagnostics": "data/audit/erste_nav_diagnostics.json",
    "mnb_otc_coverage": "data/audit/mnb_otc_coverage.json",
    "lifecycle": "data/audit/hu0000554795_lifecycle.json",
    "redemption_methodology": "data/audit/hu0000554795_redemption_methodology.json",
    "sparse_trading_semantics": "data/audit/hu0000554795_sparse_trading_semantics.json",
    "report_scope_semantics": "data/audit/mnb_keler_report_scope_semantics.json",
    "scope_research_ledger": "data/audit/mnb_keler_scope_research_evidence.json",
    "absence_semantics_freeze": "data/audit/mnb_keler_absence_semantics_freeze.json",
    "price_semantics_evidence": "data/audit/mnb_keler_price_semantics_evidence.json",
    "price_semantics_audit": "data/audit/mnb_keler_price_semantics.json",
    "alternative_source_research": "data/audit/hu0000554795_alternative_price_sources.json",
    "alternative_source_audit": "data/audit/hu0000554795_alternative_price_sources_audit.json",
    "backtest_window_coverage": "data/audit/backtest_window_coverage.json",
}


class BacktestResolvabilityError(RuntimeError):
    """Required audit evidence cannot support a terminal resolution safely."""


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BacktestResolvabilityError(f"{label} must be an object")
    return value


def _require_isin(payload: Mapping[str, object], label: str) -> None:
    if payload.get("isin") != TARGET_ISIN:
        raise BacktestResolvabilityError(f"{label} does not apply to {TARGET_ISIN}")


def _require_false_flags(payload: Mapping[str, object], label: str) -> None:
    fields = ("nav_equivalent", "backtest_return_series_approved", "usable_for_backtest")
    if any(payload.get(field) is not False for field in fields):
        raise BacktestResolvabilityError(f"{label} has unsafe approval flags")


def _erste_status(payload: Mapping[str, object]) -> str:
    records = payload.get("results")
    if not isinstance(records, list):
        raise BacktestResolvabilityError("Erste diagnostics has no results")
    matches = [item for item in records if isinstance(item, Mapping) and item.get("isin") == TARGET_ISIN]
    if len(matches) != 1 or matches[0].get("status") != "NO_ERSTE_MAPPING":
        raise BacktestResolvabilityError("Erste diagnostics is inconsistent for the exact ISIN")
    return "NO_ERSTE_MAPPING"


def _coverage_summary(payload: Mapping[str, object]) -> dict[str, object]:
    windows = payload.get("windows")
    if not isinstance(windows, list):
        raise BacktestResolvabilityError("Coverage audit has no windows")
    affected = [
        item
        for item in windows
        if isinstance(item, Mapping)
        and isinstance(item.get("unusable_isins"), list)
        and TARGET_ISIN in item["unusable_isins"]
    ]
    if len(affected) != 132 or any(item.get("status") != "UNUSABLE_SOURCE" for item in affected):
        raise BacktestResolvabilityError("Coverage audit no longer has 132 unusable HU windows")
    horizons = Counter(str(item.get("horizon")) for item in affected)
    if horizons != Counter({"90": 44, "180": 44, "365": 44}):
        raise BacktestResolvabilityError("Coverage horizon counts are inconsistent")
    return {
        "affected_window_count": len(affected),
        "horizon_counts": dict(sorted(horizons.items())),
        "ordinary_coverage_status": "UNUSABLE_SOURCE",
        "newly_complete_windows": 0,
    }


def _fingerprint(value: Mapping[str, object]) -> str:
    """Hash only stable evidence facts; never include paths or run timestamps."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_resolution(
    *,
    erste: Mapping[str, object],
    mnb_coverage: Mapping[str, object],
    lifecycle: Mapping[str, object],
    redemption: Mapping[str, object],
    sparse: Mapping[str, object],
    report_scope: Mapping[str, object],
    scope_ledger: Mapping[str, object],
    absence_freeze: Mapping[str, object],
    price_evidence: Mapping[str, object],
    price_audit: Mapping[str, object],
    alternative_research: Mapping[str, object],
    alternative_audit: Mapping[str, object],
    coverage: Mapping[str, object],
    artifact_references: Mapping[str, str],
) -> dict[str, object]:
    """Assemble a deterministic, reversible terminal evidence decision."""
    erste_status = _erste_status(erste)
    _require_isin(lifecycle, "Lifecycle audit")
    _require_isin(redemption, "Redemption audit")
    _require_isin(sparse, "Sparse-trading audit")
    _require_isin(price_audit, "Price-semantics audit")
    _require_isin(alternative_research, "Alternative-source research")
    _require_isin(alternative_audit, "Alternative-source audit")
    for label, payload in (
        ("Lifecycle audit", lifecycle),
        ("Redemption audit", redemption),
        ("Sparse-trading audit", sparse),
        ("Price-semantics audit", price_audit),
        ("Alternative-source research", alternative_research),
        ("Alternative-source audit", alternative_audit),
    ):
        _require_false_flags(payload, label)
    if (
        lifecycle.get("currency") != TARGET_CURRENCY
        or lifecycle.get("maturity_validated") is not True
        or lifecycle.get("issue_date") != "2024-06-04"
        or lifecycle.get("maturity_date") != REQUIRED_END.isoformat()
    ):
        raise BacktestResolvabilityError("Lifecycle evidence is inconsistent")
    if (
        redemption.get("currency") != TARGET_CURRENCY
        or redemption.get("redemption_mechanics_validated") is not True
        or redemption.get("maturity_date") != REQUIRED_END.isoformat()
        or redemption.get("coupon_rate") != "6.00"
        or redemption.get("coupon_frequency") != "AT_MATURITY"
    ):
        raise BacktestResolvabilityError("Redemption evidence is inconsistent")
    mnb_results = mnb_coverage.get("results")
    if not isinstance(mnb_results, list):
        raise BacktestResolvabilityError("MNB coverage has no results")
    mnb_matches = [item for item in mnb_results if isinstance(item, Mapping) and item.get("isin") == TARGET_ISIN]
    if len(mnb_matches) != 1:
        raise BacktestResolvabilityError("MNB coverage has no unique exact-ISIN result")
    mnb_record = mnb_matches[0]
    quality = _mapping(mnb_record.get("quality"), "MNB quality")
    acquisition = _mapping(mnb_record.get("acquisition_summary"), "MNB acquisition")
    if (
        mnb_record.get("currency") != TARGET_CURRENCY
        or quality.get("observation_count") != 3
        or acquisition.get("reports_acquired") != 26
        or acquisition.get("exact_isin_absent_reports") != 23
        or mnb_record.get("backtest_return_series_approved") is not False
    ):
        raise BacktestResolvabilityError("MNB OTC evidence is inconsistent")
    scope_chain = _mapping(report_scope.get("evidence_chain"), "Report-scope evidence")
    if (
        report_scope.get("semantic_status") != "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
        or scope_chain.get("absence_semantics_validated") is not False
        or scope_ledger.get("research_status") != "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
        or scope_ledger.get("stopping_rule_completed") is not True
    ):
        raise BacktestResolvabilityError("Report-scope research is inconsistent")
    freeze_counts = _mapping(absence_freeze.get("classification_counts"), "Absence-freeze counts")
    if (
        absence_freeze.get("absence_semantics_status") != "AUTHORITATIVE_EVIDENCE_NOT_FOUND"
        or absence_freeze.get("frozen_interpretation") != "ABSENCE_SEMANTICS_UNKNOWN"
        or absence_freeze.get("absence_semantics_research_closed") is not True
        or absence_freeze.get("absence_semantics_validated") is not False
        or freeze_counts.get("NO_EXACT_ISIN_OBSERVATION") != 23
        or freeze_counts.get("NO_REPORTED_KELER_OTC_ACTIVITY") != 0
    ):
        raise BacktestResolvabilityError("Absence-semantics freeze is inconsistent")
    if (
        price_evidence.get("research_status") != "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND"
        or price_evidence.get("stopping_rule_completed") is not True
        or price_audit.get("price_semantics_status") != "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND"
    ):
        raise BacktestResolvabilityError("MNB price-semantics research is inconsistent")
    if (
        alternative_research.get("research_outcome") != "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND"
        or alternative_research.get("stopping_rule_completed") is not True
        or alternative_audit.get("research_outcome") != "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND"
        or alternative_audit.get("validated_candidate_count") != 0
    ):
        raise BacktestResolvabilityError("Alternative-source research is inconsistent")
    window_summary = _coverage_summary(coverage)
    lifecycle_counts = _mapping(
        lifecycle.get("lifecycle_classification_counts"), "Lifecycle classifications"
    )
    if lifecycle_counts.get("PRE_MATURITY") != 80 or lifecycle_counts.get("CROSSES_MATURITY") != 52:
        raise BacktestResolvabilityError("Lifecycle window classifications are inconsistent")
    expected_references = {
        "erste_diagnostics",
        "mnb_otc_coverage",
        "lifecycle",
        "redemption_methodology",
        "sparse_trading_semantics",
        "report_scope_semantics",
        "scope_research_ledger",
        "absence_semantics_freeze",
        "price_semantics_evidence",
        "price_semantics_audit",
        "alternative_source_research",
        "alternative_source_audit",
        "backtest_window_coverage",
    }
    if set(artifact_references) != expected_references or any(
        not isinstance(path, str) or path.startswith("/") for path in artifact_references.values()
    ):
        raise BacktestResolvabilityError("Resolution artifact references are incomplete or non-relative")
    terminal_results = {
        "erste": erste_status,
        "report_scope": "REPORT_SCOPE_SEMANTICS_NOT_FOUND",
        "absence_semantics": "ABSENCE_SEMANTICS_UNKNOWN",
        "price_semantics": "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND",
        "alternative_source": "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND",
    }
    fingerprint_input = {
        "isin": TARGET_ISIN,
        "required_interval": {"start": REQUIRED_START.isoformat(), "end": REQUIRED_END.isoformat()},
        "terminal_results": terminal_results,
        "lifecycle": {
            "issue_date": lifecycle["issue_date"],
            "maturity_date": lifecycle["maturity_date"],
            "source_document_sha256": lifecycle.get("source_document_sha256"),
        },
        "redemption": {"source_document_sha256": redemption.get("source_document_sha256")},
        "mnb_source_hashes": sorted(
            str(item.get("source_document_hash"))
            for item in mnb_record.get("provenance", [])
            if isinstance(item, Mapping) and isinstance(item.get("source_document_hash"), str)
        ),
        "window_summary": window_summary,
        "lifecycle_counts": dict(sorted(lifecycle_counts.items())),
    }
    blocking_factors = [
        {"id": "NO_ERSTE_MAPPING", "status": erste_status},
        {"id": "SPARSE_MNB_OTC_OBSERVATIONS", "observation_count": 3},
        {"id": "MNB_ABSENCE_SEMANTICS_UNKNOWN", "absent_reports": 23},
        {"id": "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND", "status": terminal_results["price_semantics"]},
        {"id": "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND", "status": terminal_results["alternative_source"]},
        {"id": "PRE_MATURITY_RETURN_METHODOLOGY_NOT_APPROVED"},
        {"id": "EXACT_BACKTEST_BOUNDARY_METHODOLOGY_NOT_APPROVED"},
    ]
    return {
        "schema_version": 1,
        "isin": TARGET_ISIN,
        "series": "K2025/23",
        "instrument_name": "K250604 Egyéves Magyar Állampapír",
        "currency": TARGET_CURRENCY,
        "resolution_status": RESOLUTION_STATUS,
        "evidence_scope": EVIDENCE_SCOPE,
        "research_closed": True,
        "reopen_allowed": True,
        "required_interval": {"start": REQUIRED_START.isoformat(), "end": REQUIRED_END.isoformat()},
        "terminal_research_results": terminal_results,
        "blocking_factors": blocking_factors,
        "contractual_evidence_limitation": "Validated issue/maturity/coupon/redemption mechanics are lifecycle and cash-flow evidence, not substitutes for missing pre-maturity historical prices.",
        "reopen_policy": {
            "valid_triggers": [
                "new locally retained, hash-verified authoritative exact-ISIN historical series with useful coverage and documented price/date semantics",
                "reproducible authenticated authoritative Treasury historical export with adequate provenance and semantics",
                "new applicable authoritative ÁKK/MNB/KELER methodology resolving price, accrual, quotation, completeness, or row-inclusion semantics",
                "other newly discovered authoritative exact-ISIN evidence satisfying repository validation requirements",
            ],
            "invalid_triggers": [
                "rerunning completed searches or receiving duplicate content",
                "additional absent KELER rows or unresolved sparse observations",
                "issuance or lifecycle evidence alone",
                "current-only pages, generic datasets, commercial aggregators, snippets, screenshots, AI inference, or manually entered prices",
            ],
            "reopen_eligibility_requires_explicit_reaudit": True,
            "current_evidence_requires_reopen": False,
        },
        "forbidden_fallbacks": [
            "issue-price substitution",
            "maturity/redemption-value substitution",
            "nearest KELER observation substitution",
            "interpolation or fill",
            "linear convergence or theoretical bond pricing",
            "proxy ISIN, index, cash, or zero-return substitution",
            "source stitching",
        ],
        "affected_windows": {
            **window_summary,
            "lifecycle_classification_counts": dict(sorted(lifecycle_counts.items())),
        },
        "source_artifact_references": dict(sorted(artifact_references.items())),
        "evidence_fingerprint": _fingerprint(fingerprint_input),
        "backtest_admission": {
            "nav_equivalent": False,
            "backtest_return_series_approved": False,
            "usable_for_backtest": False,
        },
        "portfolio_policy": "NOT_DECIDED_IN_THIS_TASK",
        "synthetic_prices_created": 0,
        "synthetic_returns_created": 0,
        "source_stitching_performed": False,
    }


def reopen_eligibility(candidate: Mapping[str, object]) -> str:
    """Assess new evidence only; never turn eligibility into admission."""
    valid_kinds = {
        "AUTHORITATIVE_EXACT_ISIN_HISTORICAL_SERIES",
        "AUTHENTICATED_AUTHORITATIVE_TREASURY_EXPORT",
        "APPLICABLE_AUTHORITATIVE_METHODOLOGY",
    }
    if (
        candidate.get("kind") in valid_kinds
        and candidate.get("locally_retained") is True
        and candidate.get("hash_verified") is True
        and candidate.get("authoritative") is True
        and candidate.get("applicable_2024_2025") is True
    ):
        return "REOPEN_ELIGIBLE"
    return "NOT_REOPEN_ELIGIBLE"
