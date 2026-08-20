#!/usr/bin/env python3
"""Generate fail-closed, local-evidence reconciliation for AT0000605324 only.

The command-line workflow compares already-recorded Erste conflicts with one
reviewed, already-downloaded Morningstar response. It never performs network
I/O, selects a value, writes source data, or changes source-selection,
ranking, or backtesting behaviour. Legacy OeKB helpers remain for their
separate historical audit tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen

from portfolio_advisor.history.oekb import (
    OEK_B_HISTORY_URL,
    OEK_B_PLATFORM_CONTEXT,
    OekbAcquisitionError,
    OekbHistory,
    OekbHttpResponse,
    OekbObservation,
    fetch_bounded_oekb_history,
)

TARGET_ISIN = "AT0000605324"
TARGET_FUND_NAME = "ERSTE Bond Dollar USD R01 VTIA"
TARGET_CURRENCY = "USD"
MORNINGSTAR_ID = "F0000008OS"
DEFAULT_MORNINGSTAR_EVIDENCE = Path("/tmp/AT0000605324_morningstar.json")
DEFAULT_MORNINGSTAR_OUTPUT = Path("data/audit/at0000605324_morningstar_reconciliation.json")
MORNINGSTAR_EXPECTED_OBSERVATIONS = 1772
MORNINGSTAR_EXPECTED_FIRST_DATE = date(2005, 3, 1)
MORNINGSTAR_EXPECTED_FIRST_NAV = Decimal("92.98")
MORNINGSTAR_EXPECTED_LAST_DATE = date(2012, 4, 25)
MORNINGSTAR_EXPECTED_LAST_NAV = Decimal("128.61")
COMPARISON_CLASSIFICATIONS = frozenset(
    {
        "MATCH_ERSTE_VALUE_A",
        "MATCH_ERSTE_VALUE_B",
        "MATCH_BOTH",
        "MATCH_NEITHER",
        "NO_OEKB_OBSERVATION",
    }
)
MORNINGSTAR_COMPARISON_CLASSIFICATIONS = frozenset(
    {
        "MATCH_ERSTE_VALUE_A",
        "MATCH_ERSTE_VALUE_B",
        "MATCH_BOTH",
        "MATCH_NEITHER",
        "NO_MORNINGSTAR_OBSERVATION",
    }
)


class ReconciliationError(RuntimeError):
    """An input or independent-source condition that must fail closed."""


@dataclass(frozen=True)
class ErsteConflict:
    calendar_date: date
    value_a: Decimal
    value_b: Decimal
    raw_detail: dict[str, object]


@dataclass(frozen=True)
class MorningstarHistory:
    """Validated local Morningstar NAV evidence; no provider access occurs here."""

    nav_by_date: dict[date, Decimal]
    evidence_sha256: str

    @property
    def first_date(self) -> date:
        return min(self.nav_by_date)

    @property
    def last_date(self) -> date:
        return max(self.nav_by_date)


def error(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def decimal_value(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReconciliationError(f"{field} is not a decimal value: {value!r}") from exc
    if not parsed.is_finite():
        raise ReconciliationError(f"{field} is not finite: {value!r}")
    return parsed


def parse_calendar_date(value: object, field: str) -> date:
    raw = str(value).strip()
    for format_string in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, format_string).replace(tzinfo=UTC).date()
        except ValueError:
            pass
    if len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    raise ReconciliationError(f"{field} is not a supported calendar date: {value!r}")


def read_erste_conflicts(path: Path) -> tuple[list[ErsteConflict], str]:
    """Load exactly the raw two-value conflicts recorded for the target ISIN."""
    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"Invalid diagnostics JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ReconciliationError("Diagnostics JSON has no results list")

    target_records = [
        record
        for record in payload["results"]
        if isinstance(record, dict) and record.get("isin") == TARGET_ISIN
    ]
    if len(target_records) != 1:
        raise ReconciliationError(
            f"Expected exactly one diagnostics record for {TARGET_ISIN}; "
            f"found {len(target_records)}"
        )
    target = target_records[0]
    if target.get("status") != "CONFLICTING_HISTORY":
        raise ReconciliationError(
            f"Expected CONFLICTING_HISTORY for {TARGET_ISIN}; "
            f"found {target.get('status')!r}"
        )
    details = target.get("anomaly_details")
    if not isinstance(details, list):
        raise ReconciliationError("Target diagnostics has no anomaly_details list")

    conflicts: list[ErsteConflict] = []
    for detail in details:
        if not isinstance(detail, dict) or detail.get("kind") != "CONFLICTING_HISTORY":
            continue
        values = detail.get("values")
        if not isinstance(values, list) or len(values) != 2:
            raise ReconciliationError(
                "Each conflicting Erste timestamp must retain exactly two values"
            )
        calendar_date = parse_calendar_date(detail.get("date"), "Erste conflict date")
        conflicts.append(
            ErsteConflict(
                calendar_date=calendar_date,
                value_a=decimal_value(values[0], "Erste value A"),
                value_b=decimal_value(values[1], "Erste value B"),
                raw_detail=detail,
            )
        )
    if len(conflicts) != 28:
        raise ReconciliationError(
            f"Expected 28 conflicting Erste entries; found {len(conflicts)}"
        )
    if len({conflict.calendar_date for conflict in conflicts}) != len(conflicts):
        raise ReconciliationError("Erste conflict diagnostics contain duplicate dates")
    return sorted(conflicts, key=lambda conflict: conflict.calendar_date), hashlib.sha256(raw_bytes).hexdigest()


def read_morningstar_history(path: Path) -> MorningstarHistory:
    """Read and strictly validate the supplied, already-downloaded response.

    This intentionally recognizes only the reviewed AT0000605324 Morningstar
    evidence. It has no HTTP code and is not a reusable source adapter.
    """
    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"Invalid Morningstar evidence JSON: {exc}") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ReconciliationError("Morningstar evidence must contain exactly one response object")
    record = payload[0]
    if record.get("queryKey") != MORNINGSTAR_ID:
        raise ReconciliationError(
            f"Morningstar queryKey must be {MORNINGSTAR_ID}; found {record.get('queryKey')!r}"
        )
    series = record.get("series")
    if not isinstance(series, list) or len(series) != MORNINGSTAR_EXPECTED_OBSERVATIONS:
        raise ReconciliationError(
            "Morningstar evidence observation count does not match the validated evidence"
        )

    nav_by_date: dict[date, Decimal] = {}
    for row in series:
        if not isinstance(row, dict):
            raise ReconciliationError("Morningstar series contains a non-object observation")
        calendar_date = parse_calendar_date(row.get("date"), "Morningstar observation date")
        if calendar_date in nav_by_date:
            raise ReconciliationError("Morningstar evidence contains duplicate NAV dates")
        nav = decimal_value(row.get("nav"), "Morningstar NAV")
        if nav <= 0:
            raise ReconciliationError("Morningstar evidence contains a non-positive NAV")
        nav_by_date[calendar_date] = nav

    history = MorningstarHistory(
        nav_by_date=nav_by_date,
        evidence_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
    if (
        history.first_date != MORNINGSTAR_EXPECTED_FIRST_DATE
        or history.nav_by_date[history.first_date] != MORNINGSTAR_EXPECTED_FIRST_NAV
        or history.last_date != MORNINGSTAR_EXPECTED_LAST_DATE
        or history.nav_by_date[history.last_date] != MORNINGSTAR_EXPECTED_LAST_NAV
    ):
        raise ReconciliationError("Morningstar evidence range does not match the validated evidence")
    return history


def compare_conflicts_with_morningstar(
    conflicts: list[ErsteConflict], history: MorningstarHistory
) -> list[dict[str, object]]:
    """Compare exact same-day decimals without selecting an Erste value."""
    comparisons: list[dict[str, object]] = []
    for conflict in conflicts:
        morningstar_nav = history.nav_by_date.get(conflict.calendar_date)
        if morningstar_nav is None:
            classification = "NO_MORNINGSTAR_OBSERVATION"
            observation: dict[str, str] | None = None
        elif morningstar_nav == conflict.value_a and morningstar_nav == conflict.value_b:
            classification = "MATCH_BOTH"
            observation = morningstar_observation(conflict.calendar_date, morningstar_nav)
        elif morningstar_nav == conflict.value_a:
            classification = "MATCH_ERSTE_VALUE_A"
            observation = morningstar_observation(conflict.calendar_date, morningstar_nav)
        elif morningstar_nav == conflict.value_b:
            classification = "MATCH_ERSTE_VALUE_B"
            observation = morningstar_observation(conflict.calendar_date, morningstar_nav)
        else:
            classification = "MATCH_NEITHER"
            observation = morningstar_observation(conflict.calendar_date, morningstar_nav)
        comparisons.append(
            {
                "date": conflict.calendar_date.isoformat(),
                "erste_value_a": decimal_text(conflict.value_a),
                "erste_value_b": decimal_text(conflict.value_b),
                "classification": classification,
                "morningstar_observation": observation,
                "erste_conflict": conflict.raw_detail,
            }
        )
    return comparisons


def morningstar_observation(calendar_date: date, nav: Decimal) -> dict[str, str]:
    return {"date": calendar_date.isoformat(), "nav": decimal_text(nav)}


def build_morningstar_reconciliation_payload(
    *,
    diagnostics_path: Path,
    morningstar_path: Path,
    conflicts: list[ErsteConflict],
    diagnostics_sha256: str,
    history: MorningstarHistory,
) -> dict[str, object]:
    """Build stable audit evidence; matches do not approve a reconciliation."""
    comparisons = compare_conflicts_with_morningstar(conflicts, history)
    classifications = Counter(str(item["classification"]) for item in comparisons)
    if not set(classifications).issubset(MORNINGSTAR_COMPARISON_CLASSIFICATIONS):
        raise AssertionError("Unexpected Morningstar reconciliation classification")
    return {
        "target_isin": TARGET_ISIN,
        "fund_name": TARGET_FUND_NAME,
        "currency": TARGET_CURRENCY,
        "status": "EVIDENCE_GENERATED_NOT_ACCEPTED",
        "usable_for_backtest": False,
        "reconciliation_status": "RECONCILIATION_REQUIRED",
        "audit_scope": "local Morningstar evidence comparison only; no source selection or history mutation",
        "diagnostics_input": {
            "path": str(diagnostics_path),
            "sha256": diagnostics_sha256,
            "conflicting_entry_count": len(conflicts),
            "first_conflict_date": conflicts[0].calendar_date.isoformat(),
            "last_conflict_date": conflicts[-1].calendar_date.isoformat(),
        },
        "morningstar_provenance": {
            "source_name": "morningstar",
            "local_evidence_path": str(morningstar_path),
            "sha256": history.evidence_sha256,
            "morningstar_id": MORNINGSTAR_ID,
            "query_key": MORNINGSTAR_ID,
            "frequency": "daily",
            "observations": len(history.nav_by_date),
            "nav_observations": len(history.nav_by_date),
            "date_range": {
                "first": history.first_date.isoformat(),
                "last": history.last_date.isoformat(),
            },
            "first_nav": morningstar_observation(
                history.first_date, history.nav_by_date[history.first_date]
            ),
            "last_nav": morningstar_observation(
                history.last_date, history.nav_by_date[history.last_date]
            ),
            "conflict_interval_fully_covered": (
                history.first_date <= conflicts[0].calendar_date
                and history.last_date >= conflicts[-1].calendar_date
            ),
            "non_positive_nav_count": 0,
            "duplicate_date_count": 0,
        },
        "summary_counts": dict(sorted(classifications.items())),
        "comparisons": comparisons,
        "deterministic_reconciliation_rule_accepted": False,
        "warning": (
            "Audit-only comparison. No Erste value is selected, no history is rewritten, "
            "and the series remains unusable for backtesting."
        ),
    }


def build_morningstar_failure_payload(
    diagnostics_path: Path, morningstar_path: Path, message: str
) -> dict[str, object]:
    """Emit a stable, explicit non-approval when local evidence is unsafe."""
    return {
        "target_isin": TARGET_ISIN,
        "fund_name": TARGET_FUND_NAME,
        "currency": TARGET_CURRENCY,
        "status": "SOURCE_ERROR",
        "usable_for_backtest": False,
        "reconciliation_status": "RECONCILIATION_REQUIRED",
        "diagnostics_input": {"path": str(diagnostics_path)},
        "morningstar_provenance": {"local_evidence_path": str(morningstar_path)},
        "summary_counts": {},
        "comparisons": [],
        "deterministic_reconciliation_rule_accepted": False,
        "error": message,
    }


def run_morningstar_reconciliation(
    diagnostics_path: Path, morningstar_path: Path
) -> dict[str, object]:
    """Reconcile from local files only; this path never performs network I/O."""
    conflicts, diagnostics_sha256 = read_erste_conflicts(diagnostics_path)
    history = read_morningstar_history(morningstar_path)
    if (
        history.first_date > conflicts[0].calendar_date
        or history.last_date < conflicts[-1].calendar_date
    ):
        raise ReconciliationError("Morningstar evidence does not cover the complete conflict interval")
    return build_morningstar_reconciliation_payload(
        diagnostics_path=diagnostics_path,
        morningstar_path=morningstar_path,
        conflicts=conflicts,
        diagnostics_sha256=diagnostics_sha256,
        history=history,
    )


def http_get(url: str, timeout: int) -> OekbHttpResponse:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "OeKB-Platform-Context": OEK_B_PLATFORM_CONTEXT,
            "User-Agent": "PortfolioAdvisor-OeKBReconciliation/1.0",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        return OekbHttpResponse(
            status_code=getattr(response, "status", 200), body=response.read()
        )


def fetch_oekb_history(
    *,
    start_date: date,
    end_date: date,
    limit: int,
    timeout: int,
) -> OekbHistory:
    """Fetch all bounded chunks through the common fail-closed OeKB helper."""
    try:
        return fetch_bounded_oekb_history(
            isin=TARGET_ISIN,
            date_from=start_date,
            date_to=end_date,
            limit=limit,
            timeout=timeout,
            http_get=http_get,
        )
    except OekbAcquisitionError as exc:
        raise ReconciliationError(str(exc)) from exc


def compare_conflicts(
    conflicts: list[ErsteConflict], observations: tuple[OekbObservation, ...]
) -> list[dict[str, object]]:
    """Compare decimals by date and exact equality—there is no tolerance."""
    by_date = {observation.calendar_date: observation for observation in observations}
    comparisons: list[dict[str, object]] = []
    for conflict in conflicts:
        oekb = by_date.get(conflict.calendar_date)
        if oekb is None:
            classification = "NO_OEKB_OBSERVATION"
            independent: dict[str, object] | None = None
        elif (
            oekb.calculated_value == conflict.value_a
            and oekb.calculated_value == conflict.value_b
        ):
            classification = "MATCH_BOTH"
            independent = oekb_record(oekb)
        elif oekb.calculated_value == conflict.value_a:
            classification = "MATCH_ERSTE_VALUE_A"
            independent = oekb_record(oekb)
        elif oekb.calculated_value == conflict.value_b:
            classification = "MATCH_ERSTE_VALUE_B"
            independent = oekb_record(oekb)
        else:
            classification = "MATCH_NEITHER"
            independent = oekb_record(oekb)
        comparisons.append(
            {
                "date": conflict.calendar_date.isoformat(),
                "erste_value_a": decimal_text(conflict.value_a),
                "erste_value_b": decimal_text(conflict.value_b),
                "classification": classification,
                "oekb_observation": independent,
                "erste_conflict": conflict.raw_detail,
            }
        )
    return comparisons


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def oekb_record(observation: OekbObservation) -> dict[str, object]:
    return {
        "numWkn": TARGET_ISIN,
        "numKursErrechneterWert": decimal_text(observation.calculated_value),
        "waehrung": observation.currency,
        "datKurs": observation.dat_kurs,
        "numWfsKu": observation.raw_row.get("numWfsKu"),
    }


def build_reconciliation_payload(
    *,
    diagnostics_path: Path,
    conflicts: list[ErsteConflict],
    diagnostics_sha256: str,
    history: OekbHistory,
) -> dict[str, object]:
    comparisons = compare_conflicts(conflicts, history.observations)
    classifications = Counter(str(item["classification"]) for item in comparisons)
    return {
        "target_isin": TARGET_ISIN,
        "status": "EVIDENCE_GENERATED_NOT_ACCEPTED",
        "usable_for_backtest": False,
        "reconciliation_status": "RECONCILIATION_REQUIRED",
        "generated_at": datetime.now(UTC).isoformat(),
        "diagnostics_input": {
            "path": str(diagnostics_path),
            "sha256": diagnostics_sha256,
            "conflicting_entry_count": len(conflicts),
        },
        "oekb_provenance": {
            "source_name": "oekb",
            "endpoint": OEK_B_HISTORY_URL.format(isin=TARGET_ISIN),
            "required_header_name": "OeKB-Platform-Context",
            "required_header_value": OEK_B_PLATFORM_CONTEXT,
            "chunks": [chunk.as_dict() for chunk in history.chunks],
            "merged_result": history.summary(),
            "raw_observations": [item.raw_row for item in history.raw_observations],
        },
        "summary_counts": dict(sorted(classifications.items())),
        "comparisons": comparisons,
        "deterministic_reconciliation_rule_accepted": False,
        "warning": (
            "Audit-only evidence. No Erste value is selected, no history is "
            "rewritten, and the series remains unusable for backtesting."
        ),
    }


def build_failure_payload(
    diagnostics_path: Path, message: str
) -> dict[str, object]:
    return {
        "target_isin": TARGET_ISIN,
        "status": "SOURCE_ERROR",
        "usable_for_backtest": False,
        "reconciliation_status": "RECONCILIATION_REQUIRED",
        "generated_at": datetime.now(UTC).isoformat(),
        "diagnostics_input": {"path": str(diagnostics_path)},
        "summary_counts": {},
        "comparisons": [],
        "deterministic_reconciliation_rule_accepted": False,
        "error": message,
    }


def write_output(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run_reconciliation(
    diagnostics_path: Path,
    *,
    limit: int,
    timeout: int,
) -> dict[str, object]:
    conflicts, diagnostics_sha256 = read_erste_conflicts(diagnostics_path)
    history = fetch_oekb_history(
        start_date=conflicts[0].calendar_date,
        end_date=conflicts[-1].calendar_date,
        limit=limit,
        timeout=timeout,
    )
    return build_reconciliation_payload(
        diagnostics_path=diagnostics_path,
        conflicts=conflicts,
        diagnostics_sha256=diagnostics_sha256,
        history=history,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare AT0000605324 Erste conflicts with local Morningstar evidence."
    )
    parser.add_argument(
        "--diagnostics",
        type=Path,
        default=Path("data/audit/erste_nav_diagnostics.json"),
        help="Existing Erste diagnostics JSON input.",
    )
    parser.add_argument(
        "--morningstar-evidence",
        type=Path,
        default=DEFAULT_MORNINGSTAR_EVIDENCE,
        help="Already-downloaded validated Morningstar JSON; never fetched by this tool.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_MORNINGSTAR_OUTPUT,
        help="Offline Morningstar reconciliation JSON output.",
    )
    args = parser.parse_args()

    try:
        payload = run_morningstar_reconciliation(args.diagnostics, args.morningstar_evidence)
    except (OSError, ReconciliationError) as exc:
        payload = build_morningstar_failure_payload(
            args.diagnostics, args.morningstar_evidence, str(exc)
        )
        write_output(args.output, payload)
        error(f"Reconciliation failed closed: {exc}")
        error(f"Audit output: {args.output}")
        return 1

    write_output(args.output, payload)
    print("AT0000605324 local Morningstar reconciliation evidence generated")
    print("Conflict entries: 28")
    print(f"Summary: {payload['summary_counts']}")
    print("Usable for backtest: false")
    print(f"Audit output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
