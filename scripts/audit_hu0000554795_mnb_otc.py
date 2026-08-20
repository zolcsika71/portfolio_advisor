"""Audit HU0000554795 MNB OTC evidence without approving it for backtesting."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

TARGET_ISIN = "HU0000554795"
TARGET_NAME = "K250604 Egyéves Magyar Állampapír"
SUBSTANTIAL_HISTORY_PERIOD_COUNT = 3


class MnbOtcAssessmentError(RuntimeError):
    """Required local MNB OTC audit evidence is malformed."""


def load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MnbOtcAssessmentError(f"Unable to load {label}: {exc}") from exc


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise MnbOtcAssessmentError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise MnbOtcAssessmentError(f"{field} must be an ISO date") from exc


def affected_windows(coverage_path: Path) -> list[dict[str, object]]:
    payload = load_json(coverage_path, "backtest coverage audit")
    if not isinstance(payload, dict) or not isinstance(payload.get("windows"), list):
        raise MnbOtcAssessmentError("Backtest coverage audit has no windows list")
    windows: list[dict[str, object]] = []
    for item in payload["windows"]:
        if not isinstance(item, dict):
            raise MnbOtcAssessmentError(
                "Backtest coverage audit has a non-object window"
            )
        unusable = item.get("unusable_isins")
        if isinstance(unusable, list) and TARGET_ISIN in unusable:
            windows.append(item)
    return windows


def validated_maturity(metadata_path: Path | None) -> date | None:
    if metadata_path is None:
        return None
    payload = load_json(metadata_path, "MNB OTC maturity metadata")
    if not isinstance(payload, dict):
        raise MnbOtcAssessmentError("Maturity metadata must be an object")
    if payload.get("isin") != TARGET_ISIN or not isinstance(
        payload.get("provenance"), dict
    ):
        raise MnbOtcAssessmentError("Maturity metadata has no exact-ISIN provenance")
    return parse_date(payload.get("maturity_date"), "maturity_date")


def lifecycle_counts(
    windows: list[dict[str, object]], maturity: date | None
) -> dict[str, int | str]:
    if maturity is None:
        return {
            "maturity_status": "MATURITY_NOT_VALIDATED_LOCALLY",
            "windows_ending_before_maturity": 0,
            "windows_crossing_maturity": 0,
            "windows_entirely_post_maturity": 0,
            "windows_requiring_lifecycle_handling": 0,
        }
    before = crossing = post = 0
    for window in windows:
        start = parse_date(window.get("required_start"), "required_start")
        end = parse_date(window.get("required_end"), "required_end")
        if end < maturity:
            before += 1
        elif start > maturity:
            post += 1
        else:
            crossing += 1
    return {
        "maturity_status": "MATURITY_VALIDATED_LOCALLY",
        "windows_ending_before_maturity": before,
        "windows_crossing_maturity": crossing,
        "windows_entirely_post_maturity": post,
        "windows_requiring_lifecycle_handling": crossing + post,
    }


def validated_lifecycle_audit(path: Path | None) -> dict[str, object] | None:
    """Read only a separately validated, exact-ISIN lifecycle audit artifact."""
    if path is None:
        return None
    payload = load_json(path, "HU0000554795 lifecycle audit")
    if not isinstance(payload, dict):
        raise MnbOtcAssessmentError("Lifecycle audit must be an object")
    if (
        payload.get("isin") != TARGET_ISIN
        or payload.get("maturity_validated") is not True
        or payload.get("redemption_mechanics_validated") is not False
        or payload.get("nav_equivalent") is not False
        or payload.get("backtest_return_series_approved") is not False
        or payload.get("usable_for_backtest") is not False
        or not isinstance(payload.get("source_document_sha256"), str)
    ):
        raise MnbOtcAssessmentError(
            "Lifecycle audit has unsafe or incomplete provenance"
        )
    parse_date(payload.get("maturity_date"), "maturity_date")
    classifications = payload.get("lifecycle_classification_counts")
    by_horizon = payload.get("lifecycle_classification_counts_by_horizon")
    if not isinstance(classifications, dict) or not isinstance(by_horizon, dict):
        raise MnbOtcAssessmentError("Lifecycle audit has no classification summary")
    return payload


def validated_sparse_trading_audit(path: Path | None) -> dict[str, object] | None:
    """Read additive sparse-trading research without granting coverage approval."""
    if path is None or not path.is_file():
        return None
    payload = load_json(path, "HU0000554795 sparse-trading audit")
    if (
        payload.get("isin") != TARGET_ISIN
        or payload.get("methodology_status")
        not in {
            "SPARSE_TRADING_SEMANTICS_VALIDATED",
            "SPARSE_TRADING_SEMANTICS_PARTIAL",
            "SPARSE_TRADING_SEMANTICS_UNKNOWN",
            "SPARSE_TRADING_SEMANTICS_CONFLICT",
        }
        or payload.get("nav_equivalent") is not False
        or payload.get("backtest_return_series_approved") is not False
        or payload.get("usable_for_backtest") is not False
    ):
        raise MnbOtcAssessmentError(
            "Sparse-trading audit has unsafe or incomplete semantics"
        )
    return payload


def validated_alternative_source_audit(path: Path | None) -> dict[str, object] | None:
    """Read isolated alternative-source research without changing MNB evidence."""
    if path is None or not path.is_file():
        return None
    payload = load_json(path, "HU0000554795 alternative-source audit")
    if (
        not isinstance(payload, dict)
        or payload.get("isin") != TARGET_ISIN
        or payload.get("research_outcome")
        not in {
            "ALTERNATIVE_PRICE_SOURCE_FOUND",
            "ALTERNATIVE_PRICE_SOURCE_PARTIAL",
            "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND",
            "ALTERNATIVE_PRICE_SOURCE_CONFLICT",
        }
        or payload.get("nav_equivalent") is not False
        or payload.get("backtest_return_series_approved") is not False
        or payload.get("usable_for_backtest") is not False
    ):
        raise MnbOtcAssessmentError("Alternative-source audit has unsafe or incomplete semantics")
    return payload


def validated_backtest_resolvability(path: Path | None) -> dict[str, object] | None:
    """Read terminal evidence metadata without changing a coverage outcome."""
    if path is None or not path.is_file():
        return None
    payload = load_json(path, "HU0000554795 backtest-resolvability audit")
    if (
        not isinstance(payload, dict)
        or payload.get("isin") != TARGET_ISIN
        or payload.get("resolution_status")
        != "BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE"
        or payload.get("research_closed") is not True
        or payload.get("reopen_allowed") is not True
    ):
        raise MnbOtcAssessmentError("Backtest-resolvability audit is malformed")
    admission = payload.get("backtest_admission")
    if not isinstance(admission, dict) or any(
        admission.get(field) is not False
        for field in (
            "nav_equivalent",
            "backtest_return_series_approved",
            "usable_for_backtest",
        )
    ):
        raise MnbOtcAssessmentError("Backtest-resolvability audit has unsafe admission")
    return payload


def window_evidence_counts(
    windows: list[dict[str, object]], source_quality: object
) -> dict[str, int]:
    """Describe exact-period evidence only; never substitute a nearby observation."""
    periods: list[tuple[date, date]] = []
    if isinstance(source_quality, dict):
        raw_periods = source_quality.get("observed_periods")
        if isinstance(raw_periods, list):
            for item in raw_periods:
                if not isinstance(item, dict):
                    raise MnbOtcAssessmentError("MNB observed period is malformed")
                periods.append(
                    (
                        parse_date(item.get("start"), "observed period start"),
                        parse_date(item.get("end"), "observed period end"),
                    )
                )
    partial = no_evidence = exact_start = exact_end = substantial = 0
    for window in windows:
        start = parse_date(window.get("required_start"), "required_start")
        end = parse_date(window.get("required_end"), "required_end")
        overlapping = [
            (period_start, period_end)
            for period_start, period_end in periods
            if period_start <= end and period_end >= start
        ]
        if overlapping:
            partial += 1
        else:
            no_evidence += 1
        substantial += len(overlapping) >= SUBSTANTIAL_HISTORY_PERIOD_COUNT
        exact_start += any(period_start == start for period_start, _ in periods)
        exact_end += any(period_end == end for _, period_end in periods)
    return {
        "windows_with_partial_mnb_evidence": partial,
        "windows_with_substantial_mnb_history": substantial,
        "substantial_mnb_history_definition": (
            f"at least {SUBSTANTIAL_HISTORY_PERIOD_COUNT} retained weekly OTC reporting periods "
            "overlap the window; descriptive only, not return-series approval"
        ),
        "windows_with_no_mnb_evidence": no_evidence,
        "windows_with_exact_start_period_evidence": exact_start,
        "windows_with_exact_end_period_evidence": exact_end,
    }


def window_evidence_details(
    windows: list[dict[str, object]], source_quality: object
) -> list[dict[str, object]]:
    """Emit deterministic per-window evidence status without an implied price boundary."""
    periods: list[tuple[date, date]] = []
    if isinstance(source_quality, dict) and isinstance(
        source_quality.get("observed_periods"), list
    ):
        for item in source_quality["observed_periods"]:
            if not isinstance(item, dict):
                raise MnbOtcAssessmentError("MNB observed period is malformed")
            periods.append(
                (
                    parse_date(item.get("start"), "observed period start"),
                    parse_date(item.get("end"), "observed period end"),
                )
            )
    details: list[dict[str, object]] = []
    for window in windows:
        start = parse_date(window.get("required_start"), "required_start")
        end = parse_date(window.get("required_end"), "required_end")
        overlaps = [
            {"start": period_start.isoformat(), "end": period_end.isoformat()}
            for period_start, period_end in periods
            if period_start <= end and period_end >= start
        ]
        details.append(
            {
                "observation_date": window.get("observation_date"),
                "portfolio_name": window.get("portfolio_name"),
                "horizon": window.get("horizon"),
                "required_start": start.isoformat(),
                "required_end": end.isoformat(),
                "mnb_evidence_status": (
                    "SUBSTANTIAL_MNB_HISTORY"
                    if len(overlaps) >= SUBSTANTIAL_HISTORY_PERIOD_COUNT
                    else "PARTIAL_MNB_EVIDENCE"
                    if overlaps
                    else "NO_MNB_EVIDENCE"
                ),
                "overlapping_reporting_periods": overlaps,
                "overlapping_reporting_period_count": len(overlaps),
                "exact_start_period_evidence": any(
                    period_start == start for period_start, _ in periods
                ),
                "exact_end_period_evidence": any(
                    period_end == end for _, period_end in periods
                ),
                "boundary_price_available": False,
                "backtest_return_series_approved": False,
            }
        )
    return details


def build_assessment(
    coverage_path: Path,
    mnb_manifest_path: Path,
    maturity_metadata_path: Path | None = None,
    lifecycle_audit_path: Path | None = None,
    sparse_trading_audit_path: Path | None = None,
    alternative_source_audit_path: Path | None = None,
    backtest_resolvability_path: Path | None = None,
) -> dict[str, object]:
    windows = affected_windows(coverage_path)
    manifest = load_json(mnb_manifest_path, "MNB OTC coverage manifest")
    if not isinstance(manifest, dict) or manifest.get("source") != "mnb_otc":
        raise MnbOtcAssessmentError("MNB OTC manifest is invalid")
    if (
        manifest.get("nav_equivalent") is not False
        or manifest.get("backtest_return_series_approved") is not False
    ):
        raise MnbOtcAssessmentError(
            "MNB OTC manifest has unsafe NAV/backtest semantics"
        )
    results = manifest.get("results")
    if not isinstance(results, list):
        raise MnbOtcAssessmentError("MNB OTC manifest has no results list")
    matches = [
        item
        for item in results
        if isinstance(item, dict) and item.get("isin") == TARGET_ISIN
    ]
    if len(matches) > 1:
        raise MnbOtcAssessmentError("MNB OTC manifest has duplicate exact-ISIN records")
    record = matches[0] if matches else None
    lifecycle_audit = validated_lifecycle_audit(lifecycle_audit_path)
    sparse_trading_audit = validated_sparse_trading_audit(sparse_trading_audit_path)
    alternative_source_audit = validated_alternative_source_audit(
        alternative_source_audit_path
    )
    backtest_resolvability = validated_backtest_resolvability(
        backtest_resolvability_path
    )
    maturity = (
        parse_date(lifecycle_audit.get("maturity_date"), "maturity_date")
        if lifecycle_audit is not None
        else validated_maturity(maturity_metadata_path)
    )
    lifecycle = lifecycle_counts(windows, maturity)
    source_quality = record.get("quality") if record else None
    evidence = window_evidence_counts(windows, source_quality)
    evidence_details = window_evidence_details(windows, source_quality)
    acquisition = record.get("acquisition_summary") if record else None
    result: dict[str, object] = {
        "isin": TARGET_ISIN,
        "exact_instrument_name": TARGET_NAME,
        "currency": "HUF",
        "instrument_type": "Hungarian government security / Egyéves Magyar Állampapír",
        "mnb_source_status": record.get("status")
        if record
        else "NO_LOCAL_MNB_OTC_REPORTS",
        "observations_found": source_quality.get("observation_count", 0)
        if isinstance(source_quality, dict)
        else 0,
        "first_reporting_period": source_quality.get("first_period")
        if isinstance(source_quality, dict)
        else None,
        "last_reporting_period": source_quality.get("last_period")
        if isinstance(source_quality, dict)
        else None,
        "average_minimum_maximum_validation": "VALIDATED"
        if record
        else "NO_OBSERVATIONS_TO_VALIDATE",
        "maximum_observation_gap_days": source_quality.get("maximum_gap_days")
        if isinstance(source_quality, dict)
        else None,
        "median_observation_gap_days": source_quality.get("median_gap_days")
        if isinstance(source_quality, dict)
        else None,
        "observed_period_span": source_quality.get("observed_periods")
        if isinstance(source_quality, dict)
        else [],
        "acquisition_summary": acquisition if isinstance(acquisition, dict) else {},
        "affected_actual_windows": len(windows),
        "affected_windows_by_horizon": dict(
            sorted(Counter(str(window.get("horizon")) for window in windows).items())
        ),
        **lifecycle,
        **evidence,
        "affected_window_mnb_evidence": evidence_details,
        "windows_with_mnb_evidence_at_exact_boundaries": 0,
        "windows_still_missing_source_evidence": evidence[
            "windows_with_no_mnb_evidence"
        ],
        "windows_still_unapproved_for_return_series": len(windows),
        "window_failure_status_counts": dict(
            Counter(str(window.get("status")) for window in windows)
        ),
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
        "recommended_next_action": (
            "IMPORT_MANUALLY_DOWNLOADED_VALIDATED_MNB_OTC_REPORTS_AND_OBTAIN_AUTHORITATIVE_MATURITY_METADATA"
            if record is None
            else "REQUIRE_SEPARATE_METHODOLOGY_AND_LIFECYCLE_APPROVAL_BEFORE_USING_OTC_PRICES_FOR_RETURNS"
        ),
    }
    if lifecycle_audit is not None:
        result.update(
            {
                "lifecycle_status": lifecycle_audit["lifecycle_status"],
                "lifecycle_source_provenance": {
                    "source_authority": lifecycle_audit.get("source_authority"),
                    "source_host": lifecycle_audit.get("source_host"),
                    "source_document": lifecycle_audit.get("source_document"),
                    "source_document_sha256": lifecycle_audit.get(
                        "source_document_sha256"
                    ),
                    "source_document_type": lifecycle_audit.get("source_document_type"),
                },
                "issue_date": lifecycle_audit.get("issue_date"),
                "maturity_date": lifecycle_audit.get("maturity_date"),
                "coupon_rate": lifecycle_audit.get("coupon_rate"),
                "coupon_frequency": lifecycle_audit.get("coupon_frequency"),
                "redemption_date": lifecycle_audit.get("redemption_date"),
                "redemption_value": lifecycle_audit.get("redemption_value"),
                "maturity_validated": True,
                "redemption_mechanics_validated": False,
                "lifecycle_classification_counts": lifecycle_audit[
                    "lifecycle_classification_counts"
                ],
                "lifecycle_classification_counts_by_horizon": lifecycle_audit[
                    "lifecycle_classification_counts_by_horizon"
                ],
                "recommended_otc_acquisition_start": lifecycle_audit[
                    "recommended_otc_acquisition_start"
                ],
                "recommended_otc_acquisition_end": lifecycle_audit[
                    "recommended_otc_acquisition_end"
                ],
            }
        )
    if sparse_trading_audit is not None:
        scope = sparse_trading_audit.get("report_scope_semantics")
        if scope is not None and not isinstance(scope, dict):
            raise MnbOtcAssessmentError(
                "Sparse-trading audit has malformed report-scope semantics"
            )
        result["sparse_trading_semantics"] = {
            "artifact": str(sparse_trading_audit_path),
            "methodology_status": sparse_trading_audit["methodology_status"],
            "semantic_status_counts": sparse_trading_audit.get(
                "semantic_status_counts"
            ),
            "positive_observation_count": sparse_trading_audit.get(
                "positive_observation_count"
            ),
            "maximum_consecutive_absent_report_run": sparse_trading_audit.get(
                "maximum_consecutive_absent_report_run"
            ),
            "forward_fill_methodologically_approved": False,
            "interpolation_methodologically_approved": False,
            "nearest_date_boundary_methodologically_approved": False,
            "report_scope_semantics_artifact": scope.get("artifact")
            if isinstance(scope, dict)
            else None,
            "report_scope_semantic_status": scope.get("semantic_status")
            if isinstance(scope, dict)
            else None,
            "report_scope_research_status": scope.get("research_status")
            if isinstance(scope, dict)
            else None,
            "report_scope_missing_evidence_links": scope.get("missing_evidence_links")
            if isinstance(scope, dict)
            else None,
            "absent_row_semantic_status": scope.get("absent_row_semantic_status")
            if isinstance(scope, dict)
            else None,
            "absence_semantics_frozen": sparse_trading_audit.get(
                "absence_semantics_freeze", {}
            ).get("research_closed")
            if isinstance(sparse_trading_audit.get("absence_semantics_freeze"), dict)
            else None,
            "absence_semantics_freeze_artifact": sparse_trading_audit.get(
                "absence_semantics_freeze", {}
            ).get("artifact")
            if isinstance(sparse_trading_audit.get("absence_semantics_freeze"), dict)
            else None,
            "price_semantics_status": sparse_trading_audit.get(
                "price_semantics", {}
            ).get("status")
            if isinstance(sparse_trading_audit.get("price_semantics"), dict)
            else None,
            "price_semantics_artifact": sparse_trading_audit.get(
                "price_semantics", {}
            ).get("artifact")
            if isinstance(sparse_trading_audit.get("price_semantics"), dict)
            else None,
        }
    if alternative_source_audit is not None:
        result["alternative_price_source_research"] = {
            "artifact": str(alternative_source_audit_path),
            "status": alternative_source_audit["research_outcome"],
            "preferred_audit_candidate": alternative_source_audit.get(
                "preferred_audit_candidate"
            ),
            "candidate_count": alternative_source_audit.get("candidate_count"),
            "exact_boundary_capable_candidate_exists": alternative_source_audit.get(
                "exact_boundary_capable_candidate_exists"
            ),
            "validated_price_semantics_candidate_exists": alternative_source_audit.get(
                "validated_price_semantics_candidate_exists"
            ),
        }
    if backtest_resolvability is not None:
        result["terminal_backtest_resolvability"] = {
            "artifact": str(backtest_resolvability_path),
            "resolution_status": backtest_resolvability["resolution_status"],
            "research_closed": True,
            "reopen_allowed": True,
            "evidence_fingerprint": backtest_resolvability.get(
                "evidence_fingerprint"
            ),
            "blocking_factors": backtest_resolvability.get("blocking_factors"),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("data/audit/backtest_window_coverage.json"),
    )
    parser.add_argument(
        "--mnb-manifest", type=Path, default=Path("data/audit/mnb_otc_coverage.json")
    )
    parser.add_argument("--maturity-metadata", type=Path)
    parser.add_argument(
        "--lifecycle-audit",
        type=Path,
        default=Path("data/audit/hu0000554795_lifecycle.json"),
    )
    parser.add_argument(
        "--sparse-trading-audit",
        type=Path,
        default=Path("data/audit/hu0000554795_sparse_trading_semantics.json"),
    )
    parser.add_argument(
        "--alternative-source-audit",
        type=Path,
        default=Path("data/audit/hu0000554795_alternative_price_sources_audit.json"),
    )
    parser.add_argument(
        "--backtest-resolvability",
        type=Path,
        default=Path("data/audit/hu0000554795_backtest_resolvability.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/audit/hu0000554795_mnb_otc_assessment.json"),
    )
    args = parser.parse_args()
    try:
        assessment = build_assessment(
            args.coverage,
            args.mnb_manifest,
            args.maturity_metadata,
            args.lifecycle_audit,
            args.sparse_trading_audit,
            args.alternative_source_audit,
            args.backtest_resolvability,
        )
    except MnbOtcAssessmentError as exc:
        print(f"HU0000554795 MNB OTC assessment failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(assessment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"HU0000554795 MNB OTC observations: {assessment['observations_found']}")
    print(f"Maturity status: {assessment['maturity_status']}")
    print("Backtest return series approved: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
