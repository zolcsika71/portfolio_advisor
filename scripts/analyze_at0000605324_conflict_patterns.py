"""Read-only structural diagnostics for AT0000605324 Erste NAV conflicts.

This script only compares the existing Erste diagnostic record with the existing
local Morningstar reconciliation result.  It never fetches data, changes source
NAV values, or accepts a reconciliation rule.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

TARGET_ISIN = "AT0000605324"
TARGET_CURRENCY = "USD"
EXPECTED_CONFLICT_COUNT = 28
CLASSIFICATION_A = "MATCH_ERSTE_VALUE_A"
CLASSIFICATION_B = "MATCH_ERSTE_VALUE_B"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
DEFAULT_DIAGNOSTICS = Path("data/audit/erste_nav_diagnostics.json")
DEFAULT_RECONCILIATION = Path("data/audit/at0000605324_morningstar_reconciliation.json")
DEFAULT_CSV_OUTPUT = Path("data/audit/at0000605324_conflict_patterns.csv")
DEFAULT_JSON_OUTPUT = Path("data/audit/at0000605324_conflict_patterns.json")

CSV_FIELDS = (
    "date",
    "erste_a",
    "erste_b",
    "morningstar_nav",
    "classification",
    "occurrence_index_a",
    "occurrence_index_b",
    "previous_nav",
    "next_nav",
    "difference_a_b",
    "difference_ms_a",
    "difference_ms_b",
    "distance_a_from_previous",
    "distance_b_from_previous",
    "distance_a_from_next",
    "distance_b_from_next",
    "a_closer_to_previous",
    "b_closer_to_previous",
    "previous_tie",
    "a_closer_to_next",
    "b_closer_to_next",
    "next_tie",
    "same_occurrence_order",
)


class ConflictPatternError(RuntimeError):
    """An input invariant failed and the analysis must stop without output."""


@dataclass(frozen=True)
class Observation:
    calendar_date: date
    nav: Decimal


@dataclass(frozen=True)
class Conflict:
    calendar_date: date
    erste_a: Decimal
    erste_b: Decimal
    occurrence_index_a: int
    occurrence_index_b: int
    before: tuple[Observation, ...]
    after: tuple[Observation, ...]


@dataclass(frozen=True)
class ReconciliationMatch:
    calendar_date: date
    morningstar_nav: Decimal
    classification: str


def decimal_value(value: object, field: str) -> Decimal:
    """Parse a finite decimal without ever using binary floating arithmetic."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConflictPatternError(f"{field} is not a decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ConflictPatternError(f"{field} is not finite: {value!r}")
    return result


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ConflictPatternError(f"{field} is not an ISO calendar date: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConflictPatternError(f"{field} is not an ISO calendar date: {value!r}") from exc


def recursive_objects(value: object) -> Iterator[dict[str, object]]:
    """Yield every nested mapping in stable depth-first order."""
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from recursive_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from recursive_objects(nested)


def load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConflictPatternError(f"Unable to load {label}: {exc}") from exc


def parse_observations(value: object, field: str) -> tuple[Observation, ...]:
    if not isinstance(value, list):
        raise ConflictPatternError(f"{field} must be a list")
    observations: list[Observation] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ConflictPatternError(f"{field}[{index}] must be an object")
        observations.append(
            Observation(
                calendar_date=parse_date(raw.get("date"), f"{field}[{index}].date"),
                nav=decimal_value(raw.get("value"), f"{field}[{index}].value"),
            )
        )
    return tuple(observations)


def parse_occurrence_indexes(value: object) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ConflictPatternError("occurrence_indexes must be a two-entry list")
    if any(isinstance(index, bool) or not isinstance(index, int) for index in value):
        raise ConflictPatternError("occurrence_indexes must contain integer indexes")
    first, second = value
    if first < 0 or second < 0 or first == second:
        raise ConflictPatternError("occurrence_indexes must contain distinct non-negative indexes")
    return first, second


def read_conflicts(path: Path) -> list[Conflict]:
    """Load the one target record and recursively extract exactly 28 conflicts."""
    payload = load_json(path, "Erste diagnostics")
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ConflictPatternError("Erste diagnostics has no results list")
    targets = [
        item
        for item in payload["results"]
        if isinstance(item, dict)
        and item.get("isin") == TARGET_ISIN
        and item.get("status") == "CONFLICTING_HISTORY"
    ]
    if len(targets) != 1:
        raise ConflictPatternError(
            f"Expected one CONFLICTING_HISTORY target record for {TARGET_ISIN}; found {len(targets)}"
        )

    conflicts: list[Conflict] = []
    for raw in recursive_objects(targets[0]):
        if raw.get("kind") != "CONFLICTING_HISTORY":
            continue
        values = raw.get("values")
        if not isinstance(values, list) or len(values) != 2:
            raise ConflictPatternError("Each conflict values field must contain exactly two entries")
        occurrence_index_a, occurrence_index_b = parse_occurrence_indexes(
            raw.get("occurrence_indexes")
        )
        surrounding = raw.get("surrounding_observations")
        if not isinstance(surrounding, dict):
            raise ConflictPatternError("Each conflict must contain surrounding_observations")
        conflicts.append(
            Conflict(
                calendar_date=parse_date(raw.get("date"), "Erste conflict date"),
                erste_a=decimal_value(values[0], "Erste value A"),
                erste_b=decimal_value(values[1], "Erste value B"),
                occurrence_index_a=occurrence_index_a,
                occurrence_index_b=occurrence_index_b,
                before=parse_observations(surrounding.get("before"), "surrounding before"),
                after=parse_observations(surrounding.get("after"), "surrounding after"),
            )
        )
    if len(conflicts) != EXPECTED_CONFLICT_COUNT:
        raise ConflictPatternError(
            f"Expected {EXPECTED_CONFLICT_COUNT} nested conflicts; found {len(conflicts)}"
        )
    if len({item.calendar_date for item in conflicts}) != len(conflicts):
        raise ConflictPatternError("Conflict dates must be unique")
    return sorted(conflicts, key=lambda item: item.calendar_date)


def read_reconciliation_matches(path: Path) -> dict[date, ReconciliationMatch]:
    """Read the existing local result, retaining exact-date Morningstar evidence."""
    payload = load_json(path, "Morningstar reconciliation")
    if not isinstance(payload, dict):
        raise ConflictPatternError("Morningstar reconciliation must be an object")
    if payload.get("target_isin") != TARGET_ISIN:
        raise ConflictPatternError("Morningstar reconciliation target ISIN does not match")
    comparisons = payload.get("comparisons")
    if not isinstance(comparisons, list):
        raise ConflictPatternError("Morningstar reconciliation has no comparisons list")

    matches: dict[date, ReconciliationMatch] = {}
    for index, raw in enumerate(comparisons):
        if not isinstance(raw, dict):
            raise ConflictPatternError(f"comparisons[{index}] must be an object")
        calendar_date = parse_date(raw.get("date"), f"comparisons[{index}].date")
        if calendar_date in matches:
            raise ConflictPatternError(
                f"Morningstar reconciliation contains duplicate date {calendar_date.isoformat()}"
            )
        observation = raw.get("morningstar_observation")
        if not isinstance(observation, dict):
            raise ConflictPatternError(
                f"comparisons[{index}] has no Morningstar observation for exact date join"
            )
        if parse_date(observation.get("date"), f"comparisons[{index}].morningstar date") != calendar_date:
            raise ConflictPatternError("Morningstar observation date does not exactly match comparison date")
        classification = raw.get("classification")
        if classification not in {CLASSIFICATION_A, CLASSIFICATION_B}:
            raise ConflictPatternError(f"Unexpected reconciliation classification: {classification!r}")
        matches[calendar_date] = ReconciliationMatch(
            calendar_date=calendar_date,
            morningstar_nav=decimal_value(observation.get("nav"), "Morningstar NAV"),
            classification=classification,
        )
    return matches


def representative_previous(conflict_date: date, observations: Sequence[Observation]) -> Decimal | None:
    """Return the chronologically last strictly-earlier observation, never same-day data."""
    candidates = [item for item in observations if item.calendar_date < conflict_date]
    return max(candidates, key=lambda item: item.calendar_date).nav if candidates else None


def representative_next(conflict_date: date, observations: Sequence[Observation]) -> Decimal | None:
    """Return the chronologically first strictly-later observation, never same-day data."""
    candidates = [item for item in observations if item.calendar_date > conflict_date]
    return min(candidates, key=lambda item: item.calendar_date).nav if candidates else None


def closeness_flags(
    value_a: Decimal, value_b: Decimal, reference: Decimal | None, suffix: str
) -> dict[str, bool]:
    flags = {
        f"a_closer_to_{suffix}": False,
        f"b_closer_to_{suffix}": False,
        f"{suffix}_tie": False,
    }
    if reference is None:
        return flags
    distance_a = abs(value_a - reference)
    distance_b = abs(value_b - reference)
    if distance_a < distance_b:
        flags[f"a_closer_to_{suffix}"] = True
    elif distance_b < distance_a:
        flags[f"b_closer_to_{suffix}"] = True
    else:
        flags[f"{suffix}_tie"] = True
    return flags


def analyze(conflicts: Sequence[Conflict], matches: Mapping[date, ReconciliationMatch]) -> list[dict[str, object]]:
    """Join by exact date and calculate per-conflict diagnostics with Decimal values."""
    rows: list[dict[str, object]] = []
    for conflict in conflicts:
        match = matches.get(conflict.calendar_date)
        if match is None:
            raise ConflictPatternError(
                f"Missing exact-date reconciliation result for {conflict.calendar_date.isoformat()}"
            )
        previous_nav = representative_previous(conflict.calendar_date, conflict.before)
        next_nav = representative_next(conflict.calendar_date, conflict.after)
        previous_flags = closeness_flags(conflict.erste_a, conflict.erste_b, previous_nav, "previous")
        next_flags = closeness_flags(conflict.erste_a, conflict.erste_b, next_nav, "next")
        rows.append(
            {
                "date": conflict.calendar_date.isoformat(),
                "erste_a": conflict.erste_a,
                "erste_b": conflict.erste_b,
                "morningstar_nav": match.morningstar_nav,
                "classification": match.classification,
                "occurrence_index_a": conflict.occurrence_index_a,
                "occurrence_index_b": conflict.occurrence_index_b,
                "previous_nav": previous_nav,
                "next_nav": next_nav,
                "difference_a_b": abs(conflict.erste_a - conflict.erste_b),
                "difference_ms_a": abs(match.morningstar_nav - conflict.erste_a),
                "difference_ms_b": abs(match.morningstar_nav - conflict.erste_b),
                "distance_a_from_previous": (
                    abs(conflict.erste_a - previous_nav) if previous_nav is not None else None
                ),
                "distance_b_from_previous": (
                    abs(conflict.erste_b - previous_nav) if previous_nav is not None else None
                ),
                "distance_a_from_next": (
                    abs(conflict.erste_a - next_nav) if next_nav is not None else None
                ),
                "distance_b_from_next": (
                    abs(conflict.erste_b - next_nav) if next_nav is not None else None
                ),
                **previous_flags,
                **next_flags,
                "same_occurrence_order": conflict.occurrence_index_a < conflict.occurrence_index_b,
            }
        )
    return rows


def mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else None


def median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def decimal_stats(rows: Sequence[dict[str, object]], field: str) -> tuple[str | None, str | None]:
    values = [value for row in rows if isinstance(value := row[field], Decimal)]
    return decimal_text(mean(values)), decimal_text(median(values))


def relationship(row: Mapping[str, object], reference: str) -> str:
    if row[f"a_closer_to_{reference}"] is True:
        return "A_CLOSER"
    if row[f"b_closer_to_{reference}"] is True:
        return "B_CLOSER"
    if row[f"{reference}_tie"] is True:
        return "TIE"
    return "NO_REFERENCE"


def summary_for(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    if not rows:
        raise ConflictPatternError("Cannot summarize an empty classification")
    result: dict[str, object] = {
        "count": len(rows),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "occurrence_index_pairs": [
            {
                "occurrence_index_a": pair[0],
                "occurrence_index_b": pair[1],
                "count": count,
            }
            for pair, count in sorted(
                Counter(
                    (int(row["occurrence_index_a"]), int(row["occurrence_index_b"]))
                    for row in rows
                ).items()
            )
        ],
        "closeness_flag_counts": {
            field: sum(row[field] is True for row in rows)
            for field in (
                "a_closer_to_previous",
                "b_closer_to_previous",
                "previous_tie",
                "a_closer_to_next",
                "b_closer_to_next",
                "next_tie",
                "same_occurrence_order",
            )
        },
    }
    for metric in (
        "difference_a_b",
        "distance_a_from_previous",
        "distance_b_from_previous",
        "distance_a_from_next",
        "distance_b_from_next",
    ):
        metric_mean, metric_median = decimal_stats(rows, metric)
        result[f"mean_{metric}"] = metric_mean
        result[f"median_{metric}"] = metric_median
    return result


def candidate_pattern_summary(
    a_cases: Sequence[dict[str, object]], b_cases: Sequence[dict[str, object]]
) -> dict[str, object]:
    """Report structural properties only; none is a reconciliation rule."""
    candidate_properties: list[tuple[str, object, list[bool]]] = []
    b_pairs = {
        (row["occurrence_index_a"], row["occurrence_index_b"]) for row in b_cases
    }
    if len(b_pairs) == 1:
        pair = next(iter(b_pairs))
        candidate_properties.append(
            (
                "occurrence_index_pair",
                list(pair),
                [
                    (row["occurrence_index_a"], row["occurrence_index_b"]) == pair
                    for row in a_cases
                ],
            )
        )
    for reference in ("previous", "next"):
        b_relations = {relationship(row, reference) for row in b_cases}
        if len(b_relations) == 1:
            relation = next(iter(b_relations))
            candidate_properties.append(
                (
                    f"{reference}_relationship",
                    relation,
                    [relationship(row, reference) == relation for row in a_cases],
                )
            )
    b_orders = {row["same_occurrence_order"] for row in b_cases}
    if len(b_orders) == 1:
        order = next(iter(b_orders))
        candidate_properties.append(
            (
                "same_occurrence_order",
                order,
                [row["same_occurrence_order"] == order for row in a_cases],
            )
        )
    b_signatures = {
        (relationship(row, "previous"), relationship(row, "next"), row["same_occurrence_order"])
        for row in b_cases
    }
    if len(b_signatures) == 1:
        signature = next(iter(b_signatures))
        candidate_properties.append(
            (
                "previous_next_order_signature",
                list(signature),
                [
                    (
                        relationship(row, "previous"),
                        relationship(row, "next"),
                        row["same_occurrence_order"],
                    )
                    == signature
                    for row in a_cases
                ],
            )
        )

    candidates = [
        {"property": name, "b_value": value, "a_cases_with_same_property": sum(a_matches)}
        for name, value, a_matches in candidate_properties
    ]
    unique = [item["property"] for item in candidates if item["a_cases_with_same_property"] == 0]
    b_all_previous = all(row["b_closer_to_previous"] is True for row in b_cases)
    b_all_next = all(row["b_closer_to_next"] is True for row in b_cases)
    same_pattern_count = min(
        (int(item["a_cases_with_same_property"]) for item in candidates), default=0
    )
    return {
        "all_b_cases_share_occurrence_pair": len(b_pairs) == 1,
        "b_cases_all_closer_to_previous": b_all_previous,
        "b_cases_all_closer_to_next": b_all_next,
        "a_cases_same_pattern_count": same_pattern_count,
        "evaluated_structural_properties": candidates,
        "unique_b_discriminator_found": bool(unique),
        "unique_b_discriminators": unique,
        "statement": (
            "A simple observed B-only structural discriminator was found; it is diagnostic only and is not an accepted reconciliation rule."
            if unique
            else "No unique observed structural discriminator was found across the evaluated occurrence-order and neighboring-NAV properties."
        ),
    }


def serialize_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        field: decimal_text(value) if isinstance(value := row[field], Decimal) else value
        for field in CSV_FIELDS
    }


def build_payload(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    classification_counts = Counter(str(row["classification"]) for row in rows)
    if classification_counts != Counter({CLASSIFICATION_A: 24, CLASSIFICATION_B: 4}):
        raise ConflictPatternError(
            "Expected classification preservation of 24 MATCH_ERSTE_VALUE_A and 4 MATCH_ERSTE_VALUE_B"
        )
    a_cases = [serialize_row(row) for row in rows if row["classification"] == CLASSIFICATION_A]
    b_cases = [serialize_row(row) for row in rows if row["classification"] == CLASSIFICATION_B]
    return {
        "isin": TARGET_ISIN,
        "currency": TARGET_CURRENCY,
        "total_conflicts": len(rows),
        "classification_counts": dict(sorted(classification_counts.items())),
        "a_cases": a_cases,
        "b_cases": b_cases,
        "summary_by_classification": {
            CLASSIFICATION_A: summary_for([row for row in rows if row["classification"] == CLASSIFICATION_A]),
            CLASSIFICATION_B: summary_for([row for row in rows if row["classification"] == CLASSIFICATION_B]),
        },
        "candidate_pattern_summary": candidate_pattern_summary(
            [row for row in rows if row["classification"] == CLASSIFICATION_A],
            [row for row in rows if row["classification"] == CLASSIFICATION_B],
        ),
        "reconciliation_status": RECONCILIATION_REQUIRED,
        "usable_for_backtest": False,
        "deterministic_reconciliation_rule_accepted": False,
        "audit_scope": "Read-only structural diagnostics only; no reconciliation rule is accepted.",
    }


def write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_row(row))


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_summary(payload: Mapping[str, object]) -> None:
    counts = payload["classification_counts"]
    assert isinstance(counts, dict)
    b_cases = payload["b_cases"]
    assert isinstance(b_cases, list)
    pattern = payload["candidate_pattern_summary"]
    assert isinstance(pattern, dict)
    print("AT0000605324 CONFLICT PATTERN ANALYSIS")
    print()
    print(f"Total conflicts: {payload['total_conflicts']}")
    print(f"Morningstar matches A: {counts[CLASSIFICATION_A]}")
    print(f"Morningstar matches B: {counts[CLASSIFICATION_B]}")
    print()
    print("B-match dates:")
    for row in b_cases:
        assert isinstance(row, dict)
        print(row["date"])
    print()
    print(
        "Unique B discriminator found: "
        f"{'YES' if pattern['unique_b_discriminator_found'] else 'NO'}"
    )
    print(f"Reconciliation status: {payload['reconciliation_status']}")
    print(f"Usable for backtest: {'YES' if payload['usable_for_backtest'] else 'NO'}")
    for row in b_cases:
        assert isinstance(row, dict)
        print()
        print(f"date: {row['date']}")
        print(f"Erste A: {row['erste_a']}")
        print(f"Erste B: {row['erste_b']}")
        print(f"Morningstar NAV: {row['morningstar_nav']}")
        print(f"previous NAV: {row['previous_nav']}")
        print(f"next NAV: {row['next_nav']}")
        print(
            "occurrence indexes: "
            f"{row['occurrence_index_a']}, {row['occurrence_index_b']}"
        )
        print(f"A-B difference: {row['difference_a_b']}")
        print(f"closer to previous: {relationship(row, 'previous')}")
        print(f"closer to next: {relationship(row, 'next')}")


def run(diagnostics_path: Path, reconciliation_path: Path) -> dict[str, object]:
    """Perform a local-only analysis; no networking or source mutation is possible."""
    conflicts = read_conflicts(diagnostics_path)
    matches = read_reconciliation_matches(reconciliation_path)
    rows = analyze(conflicts, matches)
    return build_payload(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics", type=Path, default=DEFAULT_DIAGNOSTICS)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--csv-output", type=Path, default=DEFAULT_CSV_OUTPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    args = parser.parse_args()
    try:
        payload = run(args.diagnostics, args.reconciliation)
    except ConflictPatternError as exc:
        print(f"AT0000605324 conflict pattern analysis failed closed: {exc}", file=sys.stderr)
        return 1
    csv_rows = sorted(
        [*payload["a_cases"], *payload["b_cases"]],
        key=lambda row: str(row["date"]),
    )
    write_csv(args.csv_output, csv_rows)
    write_json(args.json_output, payload)
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
