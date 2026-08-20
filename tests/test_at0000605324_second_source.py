from __future__ import annotations

import importlib.util
import json
import socket
import sys
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_at0000605324_second_source.py"
SPEC = importlib.util.spec_from_file_location("at0000605324_second_source", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
second_source = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = second_source
SPEC.loader.exec_module(second_source)

DIAGNOSTICS = ROOT / "data" / "audit" / "erste_nav_diagnostics.json"
MORNINGSTAR = ROOT / "data" / "audit" / "at0000605324_morningstar_reconciliation.json"
PATTERNS = ROOT / "data" / "audit" / "at0000605324_conflict_patterns.json"


def _references() -> dict[date, Any]:
    return second_source.read_references(DIAGNOSTICS, MORNINGSTAR, PATTERNS)


def _evidence(values: Mapping[date, Decimal]) -> Any:
    observations = {
        calendar_date: (
            second_source.NavObservation(
                calendar_date=calendar_date,
                nav=value,
                provenance={"source_row": calendar_date.isoformat()},
            ),
        )
        for calendar_date, value in values.items()
    }
    return second_source.SecondSourceEvidence(
        identity="oekb",
        currency="USD",
        provenance={"artifact_path": "fixture.json"},
        observations_by_date=observations,
    )


def _report(values: Mapping[date, Decimal]) -> dict[str, object]:
    references = _references()
    report = second_source.build_report(references, _evidence(values), [])
    assert isinstance(report, dict)
    return report


def _write_oekb_payload(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    payload: dict[str, Any] = {
        "target_isin": "AT0000605324",
        "oekb_provenance": {"source_name": "oekb", "local": True},
        "comparisons": rows,
    }
    path = tmp_path / "oekb.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _oekb_row(
    calendar_date: date,
    value: object,
    *,
    isin: str = "AT0000605324",
    currency: str = "USD",
) -> dict[str, object]:
    return {
        "date": calendar_date.isoformat(),
        "oekb_observation": {
            "numWkn": isin,
            "waehrung": currency,
            "datKurs": calendar_date.isoformat(),
            "numKursErrechneterWert": str(value),
        },
    }


def test_all_four_second_source_values_match_b_and_morningstar() -> None:
    references = _references()
    report = _report({item: reference.erste_b for item, reference in references.items()})

    assert report["classification_counts"] == {"SECOND_SOURCE_MATCHES_B": 4}
    assert report["second_source_supports_morningstar_b_cases"] is True


def test_all_four_second_source_values_match_a() -> None:
    references = _references()
    report = _report({item: reference.erste_a for item, reference in references.items()})

    assert report["classification_counts"] == {"SECOND_SOURCE_MATCHES_A": 4}
    assert report["second_source_supports_morningstar_b_cases"] is False


def test_mixed_a_and_b_second_source_matches() -> None:
    references = _references()
    values = {
        calendar_date: (
            reference.erste_a if index % 2 == 0 else reference.erste_b
        )
        for index, (calendar_date, reference) in enumerate(references.items())
    }

    assert _report(values)["classification_counts"] == {
        "SECOND_SOURCE_MATCHES_A": 2,
        "SECOND_SOURCE_MATCHES_B": 2,
    }


def test_one_second_source_value_matches_neither() -> None:
    references = _references()
    values = {item: reference.erste_b for item, reference in references.items()}
    first_date = next(iter(values))
    values[first_date] = Decimal("999.99")

    counts = _report(values)["classification_counts"]
    assert isinstance(counts, dict)
    assert counts["SECOND_SOURCE_MATCHES_NEITHER"] == 1


def test_one_missing_exact_second_source_date() -> None:
    references = _references()
    values = {item: reference.erste_b for item, reference in references.items()}
    values.pop(next(iter(values)))

    counts = _report(values)["classification_counts"]
    assert isinstance(counts, dict)
    assert counts["NO_SECOND_SOURCE_OBSERVATION"] == 1


def test_duplicate_identical_rows_reduce_to_one_comparison_value(tmp_path: Path) -> None:
    references = _references()
    first_date = next(iter(references))
    rows = [
        _oekb_row(first_date, references[first_date].erste_b),
        _oekb_row(first_date, references[first_date].erste_b),
    ]
    evidence = second_source.read_oekb_evidence(_write_oekb_payload(tmp_path, rows))
    nav, conflict, provenance = second_source.reduce_observations(
        evidence.observations_by_date[first_date]
    )

    assert nav == references[first_date].erste_b
    assert conflict is False
    assert len(provenance) == 2


def test_duplicate_conflicting_rows_are_classified_as_conflict(tmp_path: Path) -> None:
    references = _references()
    first_date = next(iter(references))
    rows = [
        _oekb_row(first_date, references[first_date].erste_a),
        _oekb_row(first_date, references[first_date].erste_b),
    ]
    evidence = second_source.read_oekb_evidence(_write_oekb_payload(tmp_path, rows))
    result = second_source.build_results(references, evidence)[0]

    assert result["classification"] == "SECOND_SOURCE_CONFLICT"


def test_wrong_isin_fails_closed(tmp_path: Path) -> None:
    first_date = next(iter(_references()))

    with pytest.raises(second_source.SecondSourceCheckError, match="ISIN mismatch"):
        second_source.read_oekb_evidence(
            _write_oekb_payload(tmp_path, [_oekb_row(first_date, "97.54", isin="WRONG")])
        )


def test_wrong_currency_fails_closed(tmp_path: Path) -> None:
    first_date = next(iter(_references()))

    with pytest.raises(second_source.SecondSourceCheckError, match="currency mismatch"):
        second_source.read_oekb_evidence(
            _write_oekb_payload(tmp_path, [_oekb_row(first_date, "97.54", currency="EUR")])
        )


def test_non_positive_nav_fails_closed(tmp_path: Path) -> None:
    first_date = next(iter(_references()))

    with pytest.raises(second_source.SecondSourceCheckError, match="positive NAV"):
        second_source.read_oekb_evidence(
            _write_oekb_payload(tmp_path, [_oekb_row(first_date, "0")])
        )


def test_decimal_exact_equality_does_not_use_tolerance() -> None:
    reference = second_source.ReferenceValues(
        calendar_date=second_source.TARGET_DATES[0],
        erste_a=Decimal("1.00"),
        erste_b=Decimal("1.01"),
        morningstar_nav=Decimal("1.01"),
    )

    assert second_source.classify(reference, Decimal("1.010"), False) == "SECOND_SOURCE_MATCHES_B"
    assert second_source.classify(reference, Decimal("1.0101"), False) == "SECOND_SOURCE_MATCHES_NEITHER"


def test_no_network_behavior_and_reconciliation_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("Network access is prohibited")

    monkeypatch.setattr(socket, "create_connection", no_network)
    report = second_source.run(
        DIAGNOSTICS,
        MORNINGSTAR,
        PATTERNS,
        [
            ROOT / "data" / "audit" / "at0000605324_reconciliation.json",
            ROOT / "data" / "audit" / "at0000605324_external_check.json",
        ],
    )

    assert report["status"] == "NO_LOCAL_SECOND_SOURCE_AVAILABLE"
    assert report["reconciliation_status"] == "RECONCILIATION_REQUIRED"
    assert report["usable_for_backtest"] is False
    assert report["deterministic_reconciliation_rule_accepted"] is False
