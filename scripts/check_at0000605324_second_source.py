"""Audit local second-source NAV evidence for AT0000605324 without network I/O.

The default invocation examines the pre-existing OeKB audit artifacts only. It
does not acquire data, alter any source history, or accept a reconciliation
rule. A local OeKB reconciliation-shaped JSON file may be supplied explicitly
for repeatable offline review.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

TARGET_ISIN = "AT0000605324"
TARGET_CURRENCY = "USD"
TARGET_DATES = (
    date(2006, 11, 17),
    date(2006, 12, 12),
    date(2008, 10, 7),
    date(2008, 12, 9),
)
CLASSIFICATION_A = "SECOND_SOURCE_MATCHES_A"
CLASSIFICATION_B = "SECOND_SOURCE_MATCHES_B"
CLASSIFICATION_MORNINGSTAR = "SECOND_SOURCE_MATCHES_MORNINGSTAR"
CLASSIFICATION_NEITHER = "SECOND_SOURCE_MATCHES_NEITHER"
CLASSIFICATION_MISSING = "NO_SECOND_SOURCE_OBSERVATION"
CLASSIFICATION_CONFLICT = "SECOND_SOURCE_CONFLICT"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

DEFAULT_DIAGNOSTICS = Path("data/audit/erste_nav_diagnostics.json")
DEFAULT_MORNINGSTAR = Path("data/audit/at0000605324_morningstar_reconciliation.json")
DEFAULT_PATTERNS = Path("data/audit/at0000605324_conflict_patterns.json")
DEFAULT_OEKB_RECONCILIATION = Path("data/audit/at0000605324_reconciliation.json")
DEFAULT_OEKB_EXTERNAL_CHECK = Path("data/audit/at0000605324_external_check.json")
DEFAULT_OUTPUT = Path("data/audit/at0000605324_second_source_check.json")


class SecondSourceCheckError(RuntimeError):
    """A local audit input is unsafe and must fail closed."""


@dataclass(frozen=True)
class ReferenceValues:
    calendar_date: date
    erste_a: Decimal
    erste_b: Decimal
    morningstar_nav: Decimal


@dataclass(frozen=True)
class NavObservation:
    calendar_date: date
    nav: Decimal
    provenance: dict[str, object]


@dataclass(frozen=True)
class SecondSourceEvidence:
    identity: str
    currency: str
    provenance: dict[str, object]
    observations_by_date: dict[date, tuple[NavObservation, ...]]


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise SecondSourceCheckError(f"{field} is not an ISO date: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SecondSourceCheckError(f"{field} is not an ISO date: {value!r}") from exc


def decimal_value(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SecondSourceCheckError(f"{field} is not a decimal value: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise SecondSourceCheckError(f"{field} must be a finite positive NAV: {value!r}")
    return result


def decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecondSourceCheckError(f"Unable to load {label}: {exc}") from exc


def nested_objects(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nested_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_objects(child)


def read_erste_values(path: Path) -> dict[date, tuple[Decimal, Decimal]]:
    payload = load_json(path, "Erste diagnostics")
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SecondSourceCheckError("Erste diagnostics has no results list")
    targets = [
        item
        for item in payload["results"]
        if isinstance(item, dict)
        and item.get("isin") == TARGET_ISIN
        and item.get("status") == "CONFLICTING_HISTORY"
    ]
    if len(targets) != 1:
        raise SecondSourceCheckError("Expected exactly one target Erste conflict record")
    values_by_date: dict[date, tuple[Decimal, Decimal]] = {}
    for item in nested_objects(targets[0]):
        if item.get("kind") != "CONFLICTING_HISTORY":
            continue
        values = item.get("values")
        if not isinstance(values, list) or len(values) != 2:
            raise SecondSourceCheckError("Erste conflict values must contain exactly two entries")
        calendar_date = parse_date(item.get("date"), "Erste conflict date")
        if calendar_date in values_by_date:
            raise SecondSourceCheckError("Erste conflict dates must be unique")
        values_by_date[calendar_date] = (
            decimal_value(values[0], "Erste A"),
            decimal_value(values[1], "Erste B"),
        )
    if not set(TARGET_DATES).issubset(values_by_date):
        raise SecondSourceCheckError("Erste diagnostics lacks a target B-match date")
    return values_by_date


def read_morningstar_values(path: Path) -> dict[date, Decimal]:
    payload = load_json(path, "Morningstar reconciliation")
    if not isinstance(payload, dict):
        raise SecondSourceCheckError("Morningstar reconciliation must be an object")
    if payload.get("target_isin") != TARGET_ISIN or payload.get("currency") != TARGET_CURRENCY:
        raise SecondSourceCheckError("Morningstar reconciliation identity or currency mismatch")
    comparisons = payload.get("comparisons")
    if not isinstance(comparisons, list):
        raise SecondSourceCheckError("Morningstar reconciliation has no comparisons list")
    values_by_date: dict[date, Decimal] = {}
    for item in comparisons:
        if not isinstance(item, dict):
            raise SecondSourceCheckError("Morningstar comparison must be an object")
        if item.get("classification") != "MATCH_ERSTE_VALUE_B":
            continue
        calendar_date = parse_date(item.get("date"), "Morningstar comparison date")
        observation = item.get("morningstar_observation")
        if not isinstance(observation, dict):
            raise SecondSourceCheckError("B-match comparison lacks Morningstar observation")
        if parse_date(observation.get("date"), "Morningstar observation date") != calendar_date:
            raise SecondSourceCheckError("Morningstar observation does not have an exact matching date")
        if calendar_date in values_by_date:
            raise SecondSourceCheckError("Duplicate Morningstar B-match date")
        values_by_date[calendar_date] = decimal_value(observation.get("nav"), "Morningstar NAV")
    if set(values_by_date) != set(TARGET_DATES):
        raise SecondSourceCheckError("Expected exactly the four documented Morningstar B-match dates")
    return values_by_date


def validate_pattern_artifact(path: Path, references: Mapping[date, ReferenceValues]) -> None:
    payload = load_json(path, "conflict pattern analysis")
    if not isinstance(payload, dict):
        raise SecondSourceCheckError("Conflict pattern analysis must be an object")
    if payload.get("isin") != TARGET_ISIN or payload.get("currency") != TARGET_CURRENCY:
        raise SecondSourceCheckError("Conflict pattern analysis identity or currency mismatch")
    b_cases = payload.get("b_cases")
    if not isinstance(b_cases, list):
        raise SecondSourceCheckError("Conflict pattern analysis has no B cases")
    found: dict[date, dict[str, object]] = {}
    for item in b_cases:
        if not isinstance(item, dict):
            raise SecondSourceCheckError("Conflict pattern B case must be an object")
        calendar_date = parse_date(item.get("date"), "Conflict pattern B date")
        found[calendar_date] = item
    if set(found) != set(TARGET_DATES):
        raise SecondSourceCheckError("Conflict pattern analysis has unexpected B-match dates")
    for calendar_date, reference in references.items():
        item = found[calendar_date]
        if decimal_value(item.get("erste_a"), "Pattern Erste A") != reference.erste_a:
            raise SecondSourceCheckError("Pattern Erste A differs from diagnostics")
        if decimal_value(item.get("erste_b"), "Pattern Erste B") != reference.erste_b:
            raise SecondSourceCheckError("Pattern Erste B differs from diagnostics")
        if decimal_value(item.get("morningstar_nav"), "Pattern Morningstar NAV") != reference.morningstar_nav:
            raise SecondSourceCheckError("Pattern Morningstar NAV differs from reconciliation")


def read_references(
    diagnostics_path: Path, morningstar_path: Path, patterns_path: Path
) -> dict[date, ReferenceValues]:
    erste = read_erste_values(diagnostics_path)
    morningstar = read_morningstar_values(morningstar_path)
    references = {
        calendar_date: ReferenceValues(
            calendar_date=calendar_date,
            erste_a=erste[calendar_date][0],
            erste_b=erste[calendar_date][1],
            morningstar_nav=morningstar[calendar_date],
        )
        for calendar_date in TARGET_DATES
    }
    validate_pattern_artifact(patterns_path, references)
    return references


def read_oekb_evidence(path: Path) -> SecondSourceEvidence:
    """Read exact OeKB records from a pre-existing reconciliation-shaped artifact."""
    payload = load_json(path, "OeKB second-source artifact")
    if not isinstance(payload, dict):
        raise SecondSourceCheckError("OeKB artifact must be an object")
    if payload.get("target_isin") != TARGET_ISIN:
        raise SecondSourceCheckError("OeKB artifact ISIN mismatch")
    provenance = payload.get("oekb_provenance")
    if not isinstance(provenance, dict) or provenance.get("source_name") != "oekb":
        raise SecondSourceCheckError("OeKB artifact has no validated OeKB provenance")
    comparisons = payload.get("comparisons")
    if not isinstance(comparisons, list):
        raise SecondSourceCheckError("OeKB artifact has no comparisons list")

    observations: dict[date, list[NavObservation]] = {}
    for index, item in enumerate(comparisons):
        if not isinstance(item, dict):
            raise SecondSourceCheckError(f"OeKB comparison {index} must be an object")
        calendar_date = parse_date(item.get("date"), f"OeKB comparison {index} date")
        raw = item.get("oekb_observation")
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise SecondSourceCheckError(f"OeKB comparison {index} observation must be an object")
        if raw.get("numWkn") != TARGET_ISIN:
            raise SecondSourceCheckError("OeKB observation ISIN mismatch")
        if raw.get("waehrung") != TARGET_CURRENCY:
            raise SecondSourceCheckError("OeKB observation currency mismatch")
        if parse_date(raw.get("datKurs"), "OeKB observation date") != calendar_date:
            raise SecondSourceCheckError("OeKB observation does not exactly match comparison date")
        observation = NavObservation(
            calendar_date=calendar_date,
            nav=decimal_value(raw.get("numKursErrechneterWert"), "OeKB NAV"),
            provenance={"comparison_index": index, "raw_observation": raw},
        )
        observations.setdefault(calendar_date, []).append(observation)
    return SecondSourceEvidence(
        identity="oekb",
        currency=TARGET_CURRENCY,
        provenance={"artifact_path": str(path), "oekb_provenance": provenance},
        observations_by_date={key: tuple(value) for key, value in observations.items()},
    )


def reduce_observations(
    observations: Sequence[NavObservation],
) -> tuple[Decimal | None, bool, list[dict[str, object]]]:
    """Keep identical duplicates as provenance and expose conflicting duplicates."""
    if not observations:
        return None, False, []
    navs = {item.nav for item in observations}
    provenance = [item.provenance for item in observations]
    if len(navs) != 1:
        return None, True, provenance
    return next(iter(navs)), False, provenance


def classify(reference: ReferenceValues, nav: Decimal | None, conflict: bool) -> str:
    if conflict:
        return CLASSIFICATION_CONFLICT
    if nav is None:
        return CLASSIFICATION_MISSING
    if nav == reference.erste_a:
        return CLASSIFICATION_A
    if nav == reference.erste_b:
        return CLASSIFICATION_B
    if nav == reference.morningstar_nav:
        return CLASSIFICATION_MORNINGSTAR
    return CLASSIFICATION_NEITHER


def build_results(
    references: Mapping[date, ReferenceValues], evidence: SecondSourceEvidence | None
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for calendar_date in TARGET_DATES:
        reference = references[calendar_date]
        observations = evidence.observations_by_date.get(calendar_date, ()) if evidence else ()
        nav, conflict, observation_provenance = reduce_observations(observations)
        results.append(
            {
                "date": calendar_date.isoformat(),
                "erste_a": decimal_text(reference.erste_a),
                "erste_b": decimal_text(reference.erste_b),
                "morningstar_nav": decimal_text(reference.morningstar_nav),
                "second_source_nav": decimal_text(nav),
                "second_source_identity": evidence.identity if evidence else None,
                "second_source_currency": evidence.currency if evidence else None,
                "second_source_provenance": observation_provenance,
                "duplicate_observation_count": max(0, len(observations) - 1),
                "classification": classify(reference, nav, conflict),
            }
        )
    return results


def artifact_record(path: Path) -> dict[str, object]:
    record: dict[str, object] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return record
    raw_payload = load_json(path, "candidate second-source artifact")
    if (
        isinstance(raw_payload, dict)
        and raw_payload.get("isin") == TARGET_ISIN
        and raw_payload.get("source") == "oekb"
        and isinstance(raw_payload.get("results"), list)
    ):
        exact_rows = [
            item
            for item in raw_payload["results"]
            if isinstance(item, dict)
            and item.get("date") in {target.isoformat() for target in TARGET_DATES}
            and item.get("external_nav") is not None
        ]
        record.update(
            {
                "identity": "oekb",
                "currency": raw_payload.get("currency"),
                "exact_target_observation_count": len(exact_rows),
                "eligible_second_source_evidence": False,
                "reason": "Legacy OeKB audit contains no validated exact NAV observations for every target date",
            }
        )
        return record
    try:
        evidence = read_oekb_evidence(path)
    except SecondSourceCheckError as exc:
        record["eligible_second_source_evidence"] = False
        record["reason"] = str(exc)
        return record
    record["identity"] = evidence.identity
    record["currency"] = evidence.currency
    record["exact_target_observation_count"] = sum(
        len(evidence.observations_by_date.get(item, ())) for item in TARGET_DATES
    )
    record["eligible_second_source_evidence"] = all(
        evidence.observations_by_date.get(item) for item in TARGET_DATES
    )
    if not record["eligible_second_source_evidence"]:
        record["reason"] = "No exact OeKB NAV observations for every target date"
    return record


def select_local_evidence(paths: Sequence[Path]) -> tuple[SecondSourceEvidence | None, list[dict[str, object]]]:
    """Prefer valid issuer evidence over OeKB; only OeKB schema is currently local."""
    inspected = [artifact_record(path) for path in paths]
    for path, record in zip(paths, inspected, strict=True):
        if record.get("eligible_second_source_evidence") is True:
            return read_oekb_evidence(path), inspected
    return None, inspected


def build_report(
    references: Mapping[date, ReferenceValues],
    evidence: SecondSourceEvidence | None,
    inspected_artifacts: Sequence[dict[str, object]],
) -> dict[str, object]:
    results = build_results(references, evidence)
    counts = Counter(str(item["classification"]) for item in results)
    supports_b = evidence is not None and all(
        item["classification"] == CLASSIFICATION_B for item in results
    )
    return {
        "isin": TARGET_ISIN,
        "currency": TARGET_CURRENCY,
        "target_dates": [item.isoformat() for item in TARGET_DATES],
        "status": "LOCAL_SECOND_SOURCE_EVIDENCE_CHECKED" if evidence else "NO_LOCAL_SECOND_SOURCE_AVAILABLE",
        "second_source": evidence.identity if evidence else None,
        "source_provenance": (
            evidence.provenance
            if evidence
            else {
                "searched_artifacts": list(inspected_artifacts),
                "reason": "No suitable local independent source has exact NAV evidence for all four target dates.",
            }
        ),
        "results": results,
        "classification_counts": dict(sorted(counts.items())),
        "second_source_supports_morningstar_b_cases": supports_b,
        "deterministic_reconciliation_rule_accepted": False,
        "reconciliation_status": RECONCILIATION_REQUIRED,
        "usable_for_backtest": False,
        "warning": "Audit-only evidence comparison; no reconciliation rule is accepted.",
    }


def write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(
    diagnostics_path: Path,
    morningstar_path: Path,
    patterns_path: Path,
    second_source_paths: Sequence[Path],
) -> dict[str, object]:
    """Run a file-only exact-date evidence review."""
    references = read_references(diagnostics_path, morningstar_path, patterns_path)
    evidence, inspected = select_local_evidence(second_source_paths)
    return build_report(references, evidence, inspected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics", type=Path, default=DEFAULT_DIAGNOSTICS)
    parser.add_argument("--morningstar", type=Path, default=DEFAULT_MORNINGSTAR)
    parser.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
    parser.add_argument(
        "--second-source",
        type=Path,
        action="append",
        default=None,
        help="Existing local OeKB reconciliation-shaped artifact; may be repeated.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    sources = args.second_source or [DEFAULT_OEKB_RECONCILIATION, DEFAULT_OEKB_EXTERNAL_CHECK]
    try:
        report = run(args.diagnostics, args.morningstar, args.patterns, sources)
    except SecondSourceCheckError as exc:
        print(f"AT0000605324 second-source check failed closed: {exc}", file=sys.stderr)
        return 1
    write_report(args.output, report)
    print(f"AT0000605324 second-source status: {report['status']}")
    print(f"Second source: {report['second_source'] or 'none'}")
    print(f"Classification counts: {report['classification_counts']}")
    print("Reconciliation status: RECONCILIATION_REQUIRED")
    print("Usable for backtest: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
