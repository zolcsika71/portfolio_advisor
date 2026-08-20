"""Create a local, audit-only coverage-gap analysis for HU0000554795.

The script reads existing audit artifacts and optional local NAV evidence in a
small documented JSON shape. It never performs I/O beyond local files and never
changes source selection, provider behavior, or backtest usability.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

TARGET_ISIN = "HU0000554795"
TARGET_CURRENCY = "HUF"
UNUSABLE_REASON = "HU0000554795: Erste source status NO_ERSTE_MAPPING is not usable"
NO_LOCAL_NAV_REASON = "NO_USABLE_LOCAL_EXACT_ISIN_NAV_SOURCE"
DEFAULT_COVERAGE = Path("data/audit/backtest_window_coverage.json")
DEFAULT_DIAGNOSTICS = Path("data/audit/erste_nav_diagnostics.json")
DEFAULT_OEKB = Path("data/audit/oekb_fallback_coverage.json")
DEFAULT_MORNINGSTAR = Path("data/audit/morningstar_fallback_coverage.json")
DEFAULT_LINEAGE = Path("data/audit/corporate_action_lineage.json")
DEFAULT_OUTPUT = Path("data/audit/hu0000554795_coverage_gap.json")


class CoverageGapError(RuntimeError):
    """A required local audit input is malformed and must fail closed."""


@dataclass(frozen=True)
class AffectedWindow:
    portfolio_name: str
    observation_date: date
    horizon: int
    required_start: date
    required_end: date
    unusable_isins: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class LocalNavSource:
    source: str
    isin: str
    currency: str
    fund_name: str | None
    provenance: dict[str, object]
    first_date: date
    last_date: date
    observation_count: int

    def covers(self, window: AffectedWindow) -> bool:
        """Use the existing audit's strict start/end boundary convention."""
        return self.first_date <= window.required_start and self.last_date >= window.required_end


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise CoverageGapError(f"{field} is not an ISO date: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CoverageGapError(f"{field} is not an ISO date: {value!r}") from exc


def decimal_value(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CoverageGapError(f"{field} is not a decimal value: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise CoverageGapError(f"{field} must be a finite positive NAV: {value!r}")
    return result


def load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoverageGapError(f"Unable to load {label}: {exc}") from exc


def read_affected_windows(path: Path) -> list[AffectedWindow]:
    payload = load_json(path, "backtest coverage audit")
    if not isinstance(payload, dict) or not isinstance(payload.get("windows"), list):
        raise CoverageGapError("Backtest coverage audit has no windows list")
    affected: list[AffectedWindow] = []
    for index, item in enumerate(payload["windows"]):
        if not isinstance(item, dict):
            raise CoverageGapError(f"Window {index} must be an object")
        unusable = item.get("unusable_isins")
        reasons = item.get("reasons")
        if not isinstance(unusable, list) or not isinstance(reasons, list):
            raise CoverageGapError(f"Window {index} has invalid blockers")
        if TARGET_ISIN not in unusable:
            continue
        if not isinstance(item.get("portfolio_name"), str) or not isinstance(item.get("horizon"), int):
            raise CoverageGapError(f"Window {index} has invalid identity fields")
        if not all(isinstance(value, str) for value in unusable + reasons):
            raise CoverageGapError(f"Window {index} has non-string blocker fields")
        affected.append(
            AffectedWindow(
                portfolio_name=item["portfolio_name"],
                observation_date=parse_date(item.get("observation_date"), "observation date"),
                horizon=item["horizon"],
                required_start=parse_date(item.get("required_start"), "required start"),
                required_end=parse_date(item.get("required_end"), "required end"),
                unusable_isins=tuple(unusable),
                reasons=tuple(reasons),
            )
        )
    if not affected:
        raise CoverageGapError(f"No affected windows found for {TARGET_ISIN}")
    return sorted(
        affected,
        key=lambda item: (item.required_start, item.required_end, item.portfolio_name, item.horizon),
    )


def gap_summary(windows: Sequence[AffectedWindow]) -> dict[str, object]:
    reason_counts = Counter(
        reason for window in windows for reason in window.reasons if reason.startswith(f"{TARGET_ISIN}:")
    )
    return {
        "isin": TARGET_ISIN,
        "affected_window_count": len(windows),
        "only_blocker_window_count": sum(
            window.unusable_isins == (TARGET_ISIN,) for window in windows
        ),
        "other_isins_also_block_window_count": sum(
            len(window.unusable_isins) > 1 for window in windows
        ),
        "failure_reason_counts": dict(sorted(reason_counts.items())),
        "horizon_counts": dict(sorted(Counter(window.horizon for window in windows).items())),
        "earliest_required_start": min(window.required_start for window in windows).isoformat(),
        "latest_required_start": max(window.required_start for window in windows).isoformat(),
        "earliest_required_end": min(window.required_end for window in windows).isoformat(),
        "latest_required_end": max(window.required_end for window in windows).isoformat(),
        "distinct_required_start_dates": len({window.required_start for window in windows}),
        "distinct_required_end_dates": len({window.required_end for window in windows}),
    }


def source_from_local_nav_payload(path: Path) -> LocalNavSource:
    """Validate a documented local exact-ISIN NAV artifact, never a return series."""
    payload = load_json(path, "local NAV source")
    if not isinstance(payload, dict):
        raise CoverageGapError("Local NAV source must be an object")
    if payload.get("isin") != TARGET_ISIN:
        raise CoverageGapError("Local NAV source exact ISIN mismatch")
    if payload.get("currency") != TARGET_CURRENCY:
        raise CoverageGapError("Local NAV source currency mismatch")
    source = payload.get("source")
    provenance = payload.get("provenance")
    observations = payload.get("nav_observations")
    if not isinstance(source, str) or not source:
        raise CoverageGapError("Local NAV source has no source identity")
    if not isinstance(provenance, dict) or not provenance:
        raise CoverageGapError("Local NAV source lacks reproducible provenance")
    if not isinstance(observations, list) or not observations:
        raise CoverageGapError("Local NAV source has no NAV observations")
    nav_by_date: dict[date, Decimal] = {}
    for index, item in enumerate(observations):
        if not isinstance(item, dict):
            raise CoverageGapError(f"NAV observation {index} must be an object")
        calendar_date = parse_date(item.get("date"), f"NAV observation {index} date")
        nav = decimal_value(item.get("nav"), f"NAV observation {index} nav")
        existing = nav_by_date.get(calendar_date)
        if existing is not None and existing != nav:
            raise CoverageGapError("Local NAV source has conflicting duplicate dates")
        nav_by_date[calendar_date] = nav
    fund_name = payload.get("fund_name")
    if fund_name is not None and not isinstance(fund_name, str):
        raise CoverageGapError("Local NAV source fund_name must be a string")
    return LocalNavSource(
        source=source,
        isin=TARGET_ISIN,
        currency=TARGET_CURRENCY,
        fund_name=fund_name,
        provenance=provenance,
        first_date=min(nav_by_date),
        last_date=max(nav_by_date),
        observation_count=len(nav_by_date),
    )


def read_erste_assessment(path: Path) -> dict[str, object]:
    payload = load_json(path, "Erste diagnostics")
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise CoverageGapError("Erste diagnostics has no results list")
    matches = [item for item in payload["results"] if isinstance(item, dict) and item.get("isin") == TARGET_ISIN]
    if len(matches) != 1:
        raise CoverageGapError("Expected exactly one HU0000554795 Erste diagnostic record")
    record = matches[0]
    return {
        "artifact": str(path),
        "source": "erste_market",
        "exact_isin": record.get("isin") == TARGET_ISIN,
        "fund_name": None,
        "currency": record.get("currency"),
        "first_date": record.get("date_range", {}).get("first") if isinstance(record.get("date_range"), dict) else None,
        "last_date": record.get("date_range", {}).get("last") if isinstance(record.get("date_range"), dict) else None,
        "observation_count": record.get("normalized_observation_count"),
        "status": record.get("status"),
        "provenance": record.get("resolution_attempts"),
        "accepted": False,
        "rejection_reasons": [
            "NO_EXACT_ERSTE_MAPPING",
            "NO_NAV_OBSERVATIONS",
            "NO_REPRODUCIBLE_EXACT_ISIN_NAV_HISTORY",
        ],
    }


def absent_fallback_assessment(path: Path, source: str) -> dict[str, object]:
    payload = load_json(path, f"{source} fallback audit")
    if not isinstance(payload, dict):
        raise CoverageGapError(f"{source} fallback audit must be an object")
    return {
        "artifact": str(path),
        "source": source,
        "exact_isin": False,
        "fund_name": None,
        "currency": None,
        "first_date": None,
        "last_date": None,
        "observation_count": 0,
        "status": "NO_EXACT_ISIN_EVIDENCE_IN_ARTIFACT",
        "provenance": {"audit_scope": payload.get("scope")},
        "accepted": False,
        "rejection_reasons": ["EXACT_ISIN_NOT_PRESENT"],
    }


def absent_lineage_assessment(path: Path) -> dict[str, object]:
    payload = load_json(path, "corporate-action lineage audit")
    if not isinstance(payload, dict):
        raise CoverageGapError("Corporate-action lineage audit must be an object")
    return {
        "artifact": str(path),
        "source": "corporate_action_lineage",
        "exact_isin": False,
        "fund_name": None,
        "currency": None,
        "first_date": None,
        "last_date": None,
        "observation_count": 0,
        "status": "NO_EXACT_ISIN_LINEAGE",
        "provenance": {"schema_version": payload.get("schema_version")},
        "accepted": False,
        "rejection_reasons": ["EXACT_ISIN_NOT_PRESENT", "NO_APPROVED_LINEAGE"],
    }


def local_source_assessment(source: LocalNavSource, artifact: Path) -> dict[str, object]:
    return {
        "artifact": str(artifact),
        "source": source.source,
        "exact_isin": source.isin == TARGET_ISIN,
        "fund_name": source.fund_name,
        "currency": source.currency,
        "first_date": source.first_date.isoformat(),
        "last_date": source.last_date.isoformat(),
        "observation_count": source.observation_count,
        "status": "VALIDATED_LOCAL_NAV_ARTIFACT",
        "provenance": source.provenance,
        "accepted": True,
        "rejection_reasons": [],
    }


def coverability(
    windows: Sequence[AffectedWindow], sources: Sequence[LocalNavSource]
) -> tuple[list[AffectedWindow], list[AffectedWindow]]:
    coverable = [window for window in windows if any(source.covers(window) for source in sources)]
    coverable_keys = {
        (window.portfolio_name, window.observation_date, window.horizon, window.required_end)
        for window in coverable
    }
    uncoverable = [
        window
        for window in windows
        if (window.portfolio_name, window.observation_date, window.horizon, window.required_end)
        not in coverable_keys
    ]
    return coverable, uncoverable


def window_record(window: AffectedWindow) -> dict[str, object]:
    return {
        "portfolio_name": window.portfolio_name,
        "observation_date": window.observation_date.isoformat(),
        "horizon": window.horizon,
        "required_start": window.required_start.isoformat(),
        "required_end": window.required_end.isoformat(),
        "unusable_isins": list(window.unusable_isins),
    }


def build_report(
    windows: Sequence[AffectedWindow],
    assessments: Sequence[dict[str, object]],
    sources: Sequence[LocalNavSource],
) -> dict[str, object]:
    summary = gap_summary(windows)
    coverable, uncoverable = coverability(windows, sources)
    valid_sources = [source for source in sources if source.isin == TARGET_ISIN and source.currency == TARGET_CURRENCY]
    required_range = {
        "earliest_required_start": summary["earliest_required_start"],
        "latest_required_start": summary["latest_required_start"],
        "earliest_required_end": summary["earliest_required_end"],
        "latest_required_end": summary["latest_required_end"],
    }
    report: dict[str, object] = {
        **summary,
        "required_range": required_range,
        "local_sources_found": list(assessments),
        "candidate_source_assessments": list(assessments),
        "currently_coverable_windows": [window_record(window) for window in coverable],
        "currently_uncoverable_windows": [window_record(window) for window in uncoverable],
        "currently_coverable_window_count": len(coverable),
        "currently_uncoverable_window_count": len(uncoverable),
        "uncovered_reason_counts": {
            NO_LOCAL_NAV_REASON: len(uncoverable)
        },
        "instrument_classification": "UNCLASSIFIED_FROM_LOCAL_REPOSITORY_EVIDENCE",
        "instrument_classification_evidence": (
            "The exact ISIN is recorded as HUF in Erste diagnostics, but no local fund name, "
            "share-class identity, or instrument-type evidence is present."
        ),
        "usable_for_backtest": False,
        "reconciliation_rule_accepted": False,
    }
    if not valid_sources:
        report.update(
            {
                "recommended_next_action": "EXTERNAL_SOURCE_RESEARCH_REQUIRED",
                "external_source_required_range": {
                    "start": summary["earliest_required_start"],
                    "end": summary["latest_required_end"],
                },
                "validated_local_start": None,
                "validated_local_end": None,
                "missing_start_range": None,
                "missing_end_range": None,
                "windows_potentially_coverable_if_gaps_filled": len(windows),
            }
        )
    else:
        report.update(
            {
                "recommended_next_action": "LOCAL_SOURCE_EVIDENCE_REVIEW_REQUIRED",
                "validated_local_start": min(source.first_date for source in valid_sources).isoformat(),
                "validated_local_end": max(source.last_date for source in valid_sources).isoformat(),
                "missing_start_range": (
                    None
                    if min(source.first_date for source in valid_sources)
                    <= min(window.required_start for window in windows)
                    else {
                        "start": min(window.required_start for window in windows).isoformat(),
                        "end": min(source.first_date for source in valid_sources).isoformat(),
                    }
                ),
                "missing_end_range": (
                    None
                    if max(source.last_date for source in valid_sources)
                    >= max(window.required_end for window in windows)
                    else {
                        "start": max(source.last_date for source in valid_sources).isoformat(),
                        "end": max(window.required_end for window in windows).isoformat(),
                    }
                ),
                "windows_potentially_coverable_if_gaps_filled": len(uncoverable),
            }
        )
    return report


def write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(
    coverage_path: Path,
    diagnostics_path: Path,
    oekb_path: Path,
    morningstar_path: Path,
    lineage_path: Path,
    local_nav_paths: Iterable[Path] = (),
) -> dict[str, object]:
    """Run an entirely local evidence and boundary-gap review."""
    windows = read_affected_windows(coverage_path)
    assessments = [
        read_erste_assessment(diagnostics_path),
        absent_fallback_assessment(oekb_path, "oekb"),
        absent_fallback_assessment(morningstar_path, "morningstar"),
        absent_lineage_assessment(lineage_path),
    ]
    sources: list[LocalNavSource] = []
    for path in local_nav_paths:
        source = source_from_local_nav_payload(path)
        sources.append(source)
        assessments.append(local_source_assessment(source, path))
    return build_report(windows, assessments, sources)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--diagnostics", type=Path, default=DEFAULT_DIAGNOSTICS)
    parser.add_argument("--oekb", type=Path, default=DEFAULT_OEKB)
    parser.add_argument("--morningstar", type=Path, default=DEFAULT_MORNINGSTAR)
    parser.add_argument("--lineage", type=Path, default=DEFAULT_LINEAGE)
    parser.add_argument(
        "--local-nav-artifact",
        type=Path,
        action="append",
        default=[],
        help="Optional existing local NAV evidence using the documented exact-ISIN JSON shape.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = run(
            args.coverage,
            args.diagnostics,
            args.oekb,
            args.morningstar,
            args.lineage,
            args.local_nav_artifact,
        )
    except CoverageGapError as exc:
        print(f"HU0000554795 coverage-gap analysis failed closed: {exc}", file=sys.stderr)
        return 1
    write_report(args.output, report)
    print("HU0000554795 COVERAGE GAP ANALYSIS")
    print(f"Affected windows: {report['affected_window_count']}")
    print(f"Only blocker windows: {report['only_blocker_window_count']}")
    print(f"Currently coverable: {report['currently_coverable_window_count']}")
    print(f"Recommended next action: {report['recommended_next_action']}")
    print("Usable for backtest: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
