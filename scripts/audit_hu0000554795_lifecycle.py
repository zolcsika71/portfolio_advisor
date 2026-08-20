"""Validate retained HU0000554795 lifecycle evidence and classify audit windows offline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from portfolio_advisor.history.security_lifecycle import (
    SecurityLifecycleError,
    SecurityLifecycleEvidence,
    extract_pdf_layout_text,
    load_akk_issuance_lifecycle,
    require_consistent_lifecycle_evidence,
)

TARGET_ISIN = "HU0000554795"
EXPECTED_TOTAL = 132
EXPECTED_HORIZON_COUNTS = {"90": 44, "180": 44, "365": 44}
LIFECYCLE_CLASSIFICATIONS = (
    "PRE_MATURITY",
    "ENDS_ON_MATURITY",
    "CROSSES_MATURITY",
    "STARTS_ON_MATURITY",
    "POST_MATURITY",
    "LIFECYCLE_UNKNOWN",
)


class LifecycleAuditError(RuntimeError):
    """Raised when lifecycle evidence or actual coverage windows are unsafe."""


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifecycleAuditError(f"Unable to read backtest coverage audit: {exc}") from exc
    if not isinstance(payload, dict):
        raise LifecycleAuditError("Backtest coverage audit must be an object")
    return payload


def _parse_iso_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise LifecycleAuditError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LifecycleAuditError(f"{field} must be an ISO date") from exc


def affected_windows(coverage_path: Path) -> list[dict[str, object]]:
    payload = _load_json(coverage_path)
    raw_windows = payload.get("windows")
    if not isinstance(raw_windows, list):
        raise LifecycleAuditError("Backtest coverage audit has no windows list")
    result: list[dict[str, object]] = []
    for window in raw_windows:
        if not isinstance(window, dict):
            raise LifecycleAuditError("Backtest coverage audit contains a malformed window")
        unusable = window.get("unusable_isins")
        if isinstance(unusable, list) and TARGET_ISIN in unusable:
            result.append(window)
    return result


def classify_window(start: date, end: date, maturity: date | None) -> tuple[str, dict[str, bool]]:
    """Classify exact lifecycle boundaries without modifying coverage eligibility."""
    if start > end:
        raise LifecycleAuditError("Window start must not follow window end")
    flags = {
        "requires_post_maturity_lifecycle_handling": False,
        "requires_redemption_methodology": False,
        "maturity_boundary_required": False,
        "instrument_matured_before_window_start": False,
    }
    if maturity is None:
        return "LIFECYCLE_UNKNOWN", flags
    if end < maturity:
        return "PRE_MATURITY", flags
    if start == maturity:
        flags["maturity_boundary_required"] = True
        flags["requires_redemption_methodology"] = True
        flags["requires_post_maturity_lifecycle_handling"] = end > maturity
        return "STARTS_ON_MATURITY", flags
    if start < maturity and end == maturity:
        flags["maturity_boundary_required"] = True
        flags["requires_redemption_methodology"] = True
        return "ENDS_ON_MATURITY", flags
    if start < maturity < end:
        flags["maturity_boundary_required"] = True
        flags["requires_redemption_methodology"] = True
        flags["requires_post_maturity_lifecycle_handling"] = True
        return "CROSSES_MATURITY", flags
    if start > maturity:
        flags["instrument_matured_before_window_start"] = True
        flags["requires_post_maturity_lifecycle_handling"] = True
        flags["requires_redemption_methodology"] = True
        return "POST_MATURITY", flags
    raise LifecycleAuditError("Unhandled lifecycle equality case")


def classify_actual_windows(
    windows: list[dict[str, object]], maturity: date | None
) -> tuple[list[dict[str, object]], dict[str, int], dict[str, dict[str, int]]]:
    details: list[dict[str, object]] = []
    overall: Counter[str] = Counter()
    by_horizon: dict[str, Counter[str]] = {}
    for window in windows:
        start = _parse_iso_date(window.get("required_start"), "required_start")
        end = _parse_iso_date(window.get("required_end"), "required_end")
        horizon = str(window.get("horizon"))
        classification, flags = classify_window(start, end, maturity)
        overall[classification] += 1
        by_horizon.setdefault(horizon, Counter())[classification] += 1
        details.append(
            {
                "observation_date": window.get("observation_date"),
                "portfolio_name": window.get("portfolio_name"),
                "horizon": window.get("horizon"),
                "required_start": start.isoformat(),
                "required_end": end.isoformat(),
                "coverage_status": window.get("status"),
                "lifecycle_classification": classification,
                **flags,
            }
        )
    return details, {name: overall[name] for name in LIFECYCLE_CLASSIFICATIONS}, {
        horizon: {name: counts[name] for name in LIFECYCLE_CLASSIFICATIONS}
        for horizon, counts in sorted(by_horizon.items())
    }


def _validate_actual_reconciliation(
    windows: list[dict[str, object]], overall: dict[str, int], by_horizon: dict[str, dict[str, int]]
) -> None:
    if len(windows) != EXPECTED_TOTAL or sum(overall.values()) != EXPECTED_TOTAL:
        raise LifecycleAuditError("Actual HU0000554795 lifecycle window total must reconcile to 132")
    actual_horizons = Counter(str(window.get("horizon")) for window in windows)
    if dict(sorted(actual_horizons.items())) != EXPECTED_HORIZON_COUNTS:
        raise LifecycleAuditError("Actual HU0000554795 lifecycle horizon totals must reconcile to 44 each")
    for horizon, expected in EXPECTED_HORIZON_COUNTS.items():
        if sum(by_horizon.get(horizon, {}).values()) != expected:
            raise LifecycleAuditError(f"Lifecycle classifications for horizon {horizon} do not reconcile")


def _all_local_evidence(source_path: Path) -> tuple[SecurityLifecycleEvidence, ...]:
    evidence: list[SecurityLifecycleEvidence] = []
    for candidate in sorted(source_path.parent.glob("*.pdf")):
        text = extract_pdf_layout_text(candidate)
        if TARGET_ISIN not in text:
            continue
        evidence.append(load_akk_issuance_lifecycle(candidate))
    if not any(item.source_document == str(source_path) for item in evidence):
        raise LifecycleAuditError("LIFECYCLE_DOCUMENT_UNUSABLE: requested source lacks the exact ISIN")
    return tuple(evidence)


def build_lifecycle_audit(coverage_path: Path, source_path: Path) -> dict[str, object]:
    try:
        lifecycle = require_consistent_lifecycle_evidence(_all_local_evidence(source_path))
    except SecurityLifecycleError as exc:
        raise LifecycleAuditError(str(exc)) from exc
    windows = affected_windows(coverage_path)
    details, overall, by_horizon = classify_actual_windows(windows, lifecycle.maturity_date)
    _validate_actual_reconciliation(windows, overall, by_horizon)
    earliest_required = min(_parse_iso_date(item.get("required_start"), "required_start") for item in windows)
    latest_required = max(_parse_iso_date(item.get("required_end"), "required_end") for item in windows)
    if lifecycle.issue_date is None or lifecycle.maturity_date is None:
        raise LifecycleAuditError("MATURITY_NOT_VALIDATED: useful OTC acquisition interval cannot be calculated")
    acquisition_start = max(earliest_required, lifecycle.issue_date)
    return {
        "schema_version": 1,
        **lifecycle.as_audit_dict(),
        "lifecycle_status": "MATURITY_VALIDATED" if lifecycle.maturity_validated else "MATURITY_NOT_VALIDATED",
        "affected_actual_windows": len(windows),
        "lifecycle_classification_counts": overall,
        "lifecycle_classification_counts_by_horizon": by_horizon,
        "affected_window_lifecycle": details,
        "required_audit_range": {"start": earliest_required.isoformat(), "end": latest_required.isoformat()},
        "recommended_otc_acquisition_start": acquisition_start.isoformat(),
        "recommended_otc_acquisition_end": lifecycle.maturity_date.isoformat(),
        "recommended_otc_acquisition_rationale": (
            "Acquire exact-ISIN weekly OTC reports only from the later of the validated issue date "
            "and earliest required audit date, through validated maturity; no post-maturity price is assumed."
        ),
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, default=Path("data/audit/backtest_window_coverage.json"))
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/security_lifecycle/raw/mnbreport_20240527_20240531.pdf"),
    )
    parser.add_argument("--output", type=Path, default=Path("data/audit/hu0000554795_lifecycle.json"))
    args = parser.parse_args()
    try:
        audit = build_lifecycle_audit(args.coverage, args.source)
    except LifecycleAuditError as exc:
        print(f"HU0000554795 lifecycle audit failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Maturity validated: {'YES' if audit['maturity_validated'] else 'NO'}")
    print(f"Lifecycle-classified windows: {audit['affected_actual_windows']}")
    print("Backtest return series approved: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
