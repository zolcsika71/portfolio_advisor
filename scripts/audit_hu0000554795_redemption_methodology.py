"""Audit HU0000554795 contractual redemption terms without implementing cash flows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.redemption_methodology import (
    RedemptionMethodologyError,
    classify_crossing_maturity_methodology,
    load_akk_public_offering_redemption,
)

TARGET_ISIN = "HU0000554795"


class RedemptionAuditError(RuntimeError):
    """Raised for invalid source provenance or inconsistent audit inputs."""


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RedemptionAuditError(f"Unable to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RedemptionAuditError(f"{label} must be an object")
    return value


def _affected_crossing_windows(coverage_path: Path, maturity_date: str) -> list[dict[str, object]]:
    coverage = _load_json(coverage_path, "backtest coverage")
    windows = coverage.get("windows")
    if not isinstance(windows, list):
        raise RedemptionAuditError("Backtest coverage has no windows")
    result: list[dict[str, object]] = []
    for window in windows:
        if not isinstance(window, dict):
            raise RedemptionAuditError("Backtest coverage contains a malformed window")
        unusable = window.get("unusable_isins")
        if (
            isinstance(unusable, list)
            and TARGET_ISIN in unusable
            and isinstance(window.get("required_start"), str)
            and isinstance(window.get("required_end"), str)
            and window["required_start"] < maturity_date < window["required_end"]
        ):
            result.append(window)
    return result


def build_redemption_audit(coverage_path: Path, source_path: Path) -> dict[str, object]:
    try:
        evidence = load_akk_public_offering_redemption(source_path)
    except RedemptionMethodologyError as exc:
        raise RedemptionAuditError(str(exc)) from exc
    crossings = _affected_crossing_windows(coverage_path, evidence.maturity_date.isoformat())
    if len(crossings) != 52:
        raise RedemptionAuditError("Expected exactly 52 current crossing-maturity windows")
    by_horizon: dict[str, int] = {}
    for window in crossings:
        horizon = str(window.get("horizon"))
        by_horizon[horizon] = by_horizon.get(horizon, 0) + 1
    if by_horizon != {"180": 8, "365": 44}:
        raise RedemptionAuditError("Crossing-maturity horizon counts are inconsistent")
    return {
        "schema_version": 1,
        **evidence.as_audit_dict(),
        "source_documents": [
            {
                "filename": source_path.name,
                "authority": evidence.source_authority,
                "host": evidence.source_host,
                "document_type": evidence.source_document_type,
                "sha256": evidence.source_document_sha256,
                "exact_isin": evidence.isin,
                "exact_series": evidence.series,
            }
        ],
        "methodology_classification": classify_crossing_maturity_methodology(
            evidence,
            pre_maturity_value_available=False,
            post_maturity_portfolio_policy_specified=False,
            cash_flow_methodology_approved=False,
        ),
        "methodology_unresolved_fields": [
            "PRE_MATURITY_APPROVED_PRICE_SERIES",
            "MNB_OTC_CLEAN_DIRTY_PRICE_CONVENTION",
            "POST_MATURITY_PORTFOLIO_CASH_OR_REINVESTMENT_POLICY",
            "BACKTEST_CASH_FLOW_APPROVAL",
        ],
        "crossing_window_impact": {
            "crossing_window_count": len(crossings),
            "by_horizon": dict(sorted(by_horizon.items())),
            "requires_redemption_methodology": len(crossings),
            "potentially_resolvable_if_redemption_methodology_approved": 0,
            "blocked_by_missing_pre_maturity_price_history": len(crossings),
            "blocked_by_both_redemption_methodology_and_price_history": len(crossings),
        },
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
        "ordinary_coverage_changed": False,
        "recommended_next_action": "ACQUIRE_MISSING_MNB_KELER_WEEKLY_REPORTS_FOR_VALIDATED_ACTIVE_PERIOD",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, default=Path("data/audit/backtest_window_coverage.json"))
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/security_lifecycle/raw/redemption/k2025_23_public_offering_20240521.pdf"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/audit/hu0000554795_redemption_methodology.json")
    )
    args = parser.parse_args()
    try:
        audit = build_redemption_audit(args.coverage, args.source)
    except RedemptionAuditError as exc:
        print(f"HU0000554795 redemption methodology audit failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Redemption mechanics validated: {'YES' if audit['redemption_mechanics_validated'] else 'NO'}")
    print(f"Crossing-maturity windows: {audit['crossing_window_impact']['crossing_window_count']}")
    print("Backtest return series approved: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
