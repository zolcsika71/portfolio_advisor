from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "audit_backtest_window_coverage.py"
)
SPEC = importlib.util.spec_from_file_location("audit_backtest_window_coverage", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
coverage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = coverage
SPEC.loader.exec_module(coverage)


def _source(
    source_name: str,
    status: str,
    *,
    first: date | None = date(2024, 1, 1),
    last: date | None = date(2026, 12, 31),
    usable: bool = True,
    fallback_eligible: bool = False,
):
    return coverage.SourceAvailability(
        source_name,
        status,
        first,
        last,
        validated_usable=usable,
        eligible_for_fallback_coverage=fallback_eligible,
    )


def _availability(
    isin: str,
    *,
    erste_status: str = "PASS",
    erste_usable: bool = True,
    erste_first: date | None = date(2024, 1, 1),
    erste_last: date | None = date(2026, 12, 31),
    oekb_first: date | None = None,
    oekb_last: date | None = None,
    morningstar_first: date | None = None,
    morningstar_last: date | None = None,
):
    oekb = None
    if oekb_first is not None or oekb_last is not None:
        oekb = _source(
            "oekb",
            "VALIDATED_FALLBACK_HISTORY",
            first=oekb_first,
            last=oekb_last,
            fallback_eligible=True,
        )
    morningstar = None
    if morningstar_first is not None or morningstar_last is not None:
        morningstar = _source(
            "morningstar",
            "VERIFIED_FOR_RANGE",
            first=morningstar_first,
            last=morningstar_last,
            fallback_eligible=True,
        )
    return coverage.HistoricalAvailability(
        isin,
        _source(
            "erste_market",
            erste_status,
            first=erste_first,
            last=erste_last,
            usable=erste_usable,
        ),
        oekb,
        morningstar,
    )


def _window(availability: dict[str, object], isins: tuple[str, ...] = ("AAA",)):
    return _window_at(date(2025, 1, 1), availability, isins)


def _window_at(
    observation_date: date,
    availability: dict[str, object],
    isins: tuple[str, ...] = ("AAA",),
):
    return coverage.evaluate_window(
        observation_date=observation_date,
        horizon=90,
        portfolio_name="Alpha",
        required_isins=isins,
        availability=availability,
    )


def _lineage_availability() -> dict[str, object]:
    predecessor = coverage.SourceAvailability(
        "oekb",
        "VALIDATED_FALLBACK_HISTORY",
        date(2024, 7, 2),
        date(2025, 10, 16),
        validated_usable=True,
        eligible_for_fallback_coverage=True,
    )
    successor = coverage.SourceAvailability(
        "morningstar",
        "VERIFIED_FOR_RANGE",
        date(2025, 10, 17),
        date(2026, 8, 14),
        validated_usable=True,
        eligible_for_fallback_coverage=True,
    )
    lineage = coverage.CorporateActionLineage(
        predecessor_isin="AT0000A2VH41",
        successor_isin="AT0000A3P9Z2",
        predecessor=predecessor,
        successor=successor,
        transition_date=date(2025, 10, 17),
        currency="USD",
    )
    return {
        "AT0000A2VH41": coverage.HistoricalAvailability(
            "AT0000A2VH41",
            _source("erste_market", "NO_ERSTE_MAPPING", first=None, last=None, usable=False),
            corporate_action_lineage=lineage,
        )
    }


def _predecessor_availability() -> dict[str, object]:
    return {
        "AT0000A2VH41": coverage.HistoricalAvailability(
            "AT0000A2VH41",
            _source("erste_market", "NO_ERSTE_MAPPING", first=None, last=None, usable=False),
        )
    }


def _lineage_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data/audit/corporate_action_lineage.json"


def test_erste_only_covered_constituent_and_exact_boundaries_are_complete() -> None:
    result = _window(
        {"AAA": _availability("AAA", erste_first=date(2025, 1, 1), erste_last=date(2025, 4, 1))}
    )

    assert result.status == coverage.COMPLETE
    assert result.required_end == date(2025, 4, 1)
    assert result.coverage_ratio == 1.0
    assert result.source_used_by_isin == {"AAA": "erste_market"}


def test_oekb_only_covered_constituent_is_complete() -> None:
    result = _window(
        {
            "AAA": _availability(
                "AAA",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                oekb_first=date(2025, 1, 1),
                oekb_last=date(2025, 4, 1),
            )
        }
    )

    assert result.status == coverage.COMPLETE
    assert result.source_used_by_isin == {"AAA": "oekb"}


def test_erste_oekb_source_union_results_in_complete() -> None:
    result = _window(
        {
            "AAA": _availability("AAA"),
            "BBB": _availability(
                "BBB",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                oekb_first=date(2025, 1, 1),
                oekb_last=date(2025, 4, 1),
            ),
        },
        ("AAA", "BBB"),
    )

    assert result.status == coverage.COMPLETE
    assert result.source_used_by_isin == {"AAA": "erste_market", "BBB": "oekb"}


def test_oekb_range_missing_required_start() -> None:
    result = _window(
        {
            "AAA": _availability(
                "AAA",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                oekb_first=date(2025, 1, 2),
                oekb_last=date(2025, 4, 1),
            )
        }
    )

    assert result.status == coverage.MISSING_START
    assert result.missing_isins == ("AAA",)


def test_oekb_range_missing_required_end() -> None:
    result = _window(
        {
            "AAA": _availability(
                "AAA",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                oekb_first=date(2025, 1, 1),
                oekb_last=date(2025, 3, 31),
            )
        }
    )

    assert result.status == coverage.MISSING_END


def test_lu2180923653_inside_morningstar_range_is_covered() -> None:
    result = _window(
        {
            "LU2180923653": _availability(
                "LU2180923653",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                morningstar_first=date(2020, 9, 30),
                morningstar_last=date(2026, 3, 27),
            )
        },
        ("LU2180923653",),
    )

    assert result.status == coverage.COMPLETE
    assert result.source_used_by_isin == {"LU2180923653": "morningstar"}


def test_morningstar_exact_start_and_end_boundaries_are_covered() -> None:
    availability = {
        "LU2180923653": _availability(
            "LU2180923653",
            erste_status="NO_ERSTE_MAPPING",
            erste_usable=False,
            erste_first=None,
            erste_last=None,
            morningstar_first=date(2020, 9, 30),
            morningstar_last=date(2026, 3, 27),
        )
    }

    exact_start = _window_at(date(2020, 9, 30), availability, ("LU2180923653",))
    exact_end = _window_at(date(2025, 12, 27), availability, ("LU2180923653",))

    assert exact_start.status == coverage.COMPLETE
    assert exact_end.status == coverage.COMPLETE
    assert exact_end.required_end == date(2026, 3, 27)


def test_morningstar_dates_outside_verified_range_fail_closed() -> None:
    availability = {
        "LU2180923653": _availability(
            "LU2180923653",
            erste_status="NO_ERSTE_MAPPING",
            erste_usable=False,
            erste_first=None,
            erste_last=None,
            morningstar_first=date(2020, 9, 30),
            morningstar_last=date(2026, 3, 27),
        )
    }

    before_start = _window_at(date(2020, 9, 29), availability, ("LU2180923653",))
    after_end = _window_at(date(2025, 12, 28), availability, ("LU2180923653",))

    assert before_start.status == coverage.MISSING_START
    assert after_end.status == coverage.MISSING_END


def test_at0000627484_morningstar_exact_boundaries_and_outside_range() -> None:
    availability = {
        "AT0000627484": _availability(
            "AT0000627484",
            erste_status="NO_ERSTE_MAPPING",
            erste_usable=False,
            erste_first=None,
            erste_last=None,
            morningstar_first=date(2024, 7, 2),
            morningstar_last=date(2026, 3, 18),
        )
    }

    exact_start = _window_at(date(2024, 7, 2), availability, ("AT0000627484",))
    exact_end = _window_at(date(2025, 12, 18), availability, ("AT0000627484",))
    before_start = _window_at(date(2024, 7, 1), availability, ("AT0000627484",))
    after_end = _window_at(date(2025, 12, 19), availability, ("AT0000627484",))

    assert exact_start.status == coverage.COMPLETE
    assert exact_end.status == coverage.COMPLETE
    assert exact_end.source_used_by_isin == {"AT0000627484": "morningstar"}
    assert before_start.status == coverage.MISSING_START
    assert after_end.status == coverage.MISSING_END


def test_audit_does_not_stitch_oekb_and_morningstar_ranges() -> None:
    result = _window_at(
        date(2024, 12, 1),
        {
            "AT0000627484": _availability(
                "AT0000627484",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                oekb_first=date(2025, 1, 15),
                oekb_last=date(2026, 3, 18),
                morningstar_first=date(2024, 7, 2),
                morningstar_last=date(2025, 1, 15),
            )
        },
        ("AT0000627484",),
    )

    assert result.status != coverage.COMPLETE
    assert result.source_used_by_isin == {}


def test_corporate_action_lineage_before_transition_uses_predecessor_oekb_only() -> None:
    result = _window_at(date(2025, 7, 18), _lineage_availability(), ("AT0000A2VH41",))

    assert result.status == coverage.COMPLETE
    assert result.required_end == date(2025, 10, 16)
    assert result.source_used_by_isin == {"AT0000A2VH41": "oekb"}
    assert result.coverage_via_corporate_action is False
    assert result.corporate_action_lineage_by_isin["AT0000A2VH41"]["coverage_type"] == (
        "predecessor_only"
    )


def test_corporate_action_lineage_after_transition_uses_successor_morningstar_only() -> None:
    result = _window_at(date(2025, 10, 17), _lineage_availability(), ("AT0000A2VH41",))

    assert result.status == coverage.COMPLETE
    assert result.source_used_by_isin == {"AT0000A2VH41": "morningstar"}
    assert result.coverage_via_corporate_action is False
    assert result.corporate_action_lineage_by_isin["AT0000A2VH41"]["coverage_type"] == (
        "successor_only"
    )


def test_corporate_action_lineage_exact_transition_boundaries() -> None:
    ending_with_predecessor = _window_at(
        date(2025, 7, 18), _lineage_availability(), ("AT0000A2VH41",)
    )
    starting_with_successor = _window_at(
        date(2025, 10, 17), _lineage_availability(), ("AT0000A2VH41",)
    )

    assert ending_with_predecessor.required_end == date(2025, 10, 16)
    assert ending_with_predecessor.source_used_by_isin == {"AT0000A2VH41": "oekb"}
    assert starting_with_successor.required_start == date(2025, 10, 17)
    assert starting_with_successor.source_used_by_isin == {"AT0000A2VH41": "morningstar"}


def test_corporate_action_lineage_crossing_window_is_coverage_only() -> None:
    result = _window_at(date(2025, 8, 1), _lineage_availability(), ("AT0000A2VH41",))

    provenance = result.corporate_action_lineage_by_isin["AT0000A2VH41"]
    assert result.status == coverage.COMPLETE
    assert result.coverage_via_corporate_action is True
    assert result.source_used_by_isin == {"AT0000A2VH41": "corporate_action_lineage"}
    assert provenance["coverage_type"] == "corporate_action_lineage"
    assert provenance["predecessor"] == {"isin": "AT0000A2VH41", "source": "oekb"}
    assert provenance["successor"] == {"isin": "AT0000A3P9Z2", "source": "morningstar"}
    assert provenance["nav_continuity"] is False
    assert provenance["return_continuity"] == "not_validated"
    assert provenance["return_calculation_approved"] is False


def test_corporate_action_lineage_outside_validated_range_fails_closed() -> None:
    before_start = _window_at(date(2024, 7, 1), _lineage_availability(), ("AT0000A2VH41",))
    after_end = _window_at(date(2026, 5, 17), _lineage_availability(), ("AT0000A2VH41",))

    assert before_start.status == coverage.MISSING_START
    assert after_end.status == coverage.MISSING_END


def test_corporate_action_lineage_manifest_rejects_wrong_pairs_and_currency(tmp_path: Path) -> None:
    manifest = json.loads(_lineage_manifest_path().read_text(encoding="utf-8"))
    for field, value in (
        ("predecessor.isin", "AT0000000000"),
        ("successor.isin", "AT0000000000"),
        ("currency", "EUR"),
    ):
        candidate = json.loads(json.dumps(manifest))
        if field == "predecessor.isin":
            candidate["lineages"][0]["predecessor"]["isin"] = value
        elif field == "successor.isin":
            candidate["lineages"][0]["successor"]["isin"] = value
        else:
            candidate["lineages"][0][field] = value
        path = tmp_path / f"{field.replace('.', '_')}.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")

        try:
            coverage.merge_corporate_action_lineage_availability(_predecessor_availability(), path)
        except coverage.CoverageAuditError:
            pass
        else:
            raise AssertionError(f"{field} mismatch must fail closed")


def test_corporate_action_lineage_manifest_requires_documentary_provenance(tmp_path: Path) -> None:
    manifest = json.loads(_lineage_manifest_path().read_text(encoding="utf-8"))
    manifest["lineages"][0]["documentary_provenance"] = {}
    path = tmp_path / "missing-provenance.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        coverage.merge_corporate_action_lineage_availability(_lineage_availability(), path)
    except coverage.CoverageAuditError:
        pass
    else:
        raise AssertionError("Missing documentary provenance must fail closed")


def test_mixed_erste_oekb_morningstar_portfolio_is_complete() -> None:
    result = _window(
        {
            "AAA": _availability("AAA"),
            "BBB": _availability(
                "BBB",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                oekb_first=date(2025, 1, 1),
                oekb_last=date(2025, 4, 1),
            ),
            "AT0000627484": _availability(
                "AT0000627484",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                morningstar_first=date(2024, 7, 2),
                morningstar_last=date(2026, 3, 18),
            ),
        },
        ("AAA", "BBB", "AT0000627484"),
    )

    assert result.status == coverage.COMPLETE
    assert result.source_used_by_isin == {
        "AAA": "erste_market",
        "BBB": "oekb",
        "AT0000627484": "morningstar",
    }
    assert result.coverage_ratio == 1.0


def test_morningstar_fallback_does_not_override_reconciliation_required() -> None:
    result = _window(
        {
            "AT0000605324": _availability(
                "AT0000605324",
                erste_status="CONFLICTING_HISTORY",
                erste_usable=False,
                morningstar_first=date(2020, 9, 30),
                morningstar_last=date(2026, 3, 27),
            )
        },
        ("AT0000605324",),
    )

    assert result.status == coverage.RECONCILIATION_REQUIRED
    assert result.source_used_by_isin == {}


def test_at0000605324_remains_reconciliation_required_despite_oekb_data() -> None:
    result = _window(
        {
            "AT0000605324": _availability(
                "AT0000605324",
                erste_status="CONFLICTING_HISTORY",
                erste_usable=False,
                oekb_first=date(2025, 1, 1),
                oekb_last=date(2026, 12, 31),
            )
        },
        ("AT0000605324",),
    )

    assert result.status == coverage.RECONCILIATION_REQUIRED
    assert result.source_used_by_isin == {}


def test_unusable_source_and_wrong_instrument_type_are_preserved() -> None:
    unusable = _window({"AAA": _availability("AAA", erste_status="INVALID_NAV", erste_usable=False)})
    wrong_type = _window(
        {"AAA": _availability("AAA", erste_status="WRONG_INSTRUMENT_TYPE", erste_usable=False)}
    )

    assert unusable.status == coverage.UNUSABLE_SOURCE
    assert wrong_type.status == coverage.WRONG_INSTRUMENT_TYPE


def test_mixed_erste_oekb_morningstar_portfolio_has_deterministic_coverage_ratio() -> None:
    result = _window(
        {
            "AAA": _availability("AAA"),
            "BBB": _availability(
                "BBB",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                oekb_first=date(2025, 1, 1),
                oekb_last=date(2025, 4, 1),
            ),
            "AT0000627484": _availability(
                "AT0000627484",
                erste_status="NO_ERSTE_MAPPING",
                erste_usable=False,
                erste_first=None,
                erste_last=None,
                morningstar_first=date(2024, 7, 2),
                morningstar_last=date(2026, 3, 18),
            ),
            "CCC": _availability("CCC", erste_status="NO_ERSTE_MAPPING", erste_usable=False),
        },
        ("AAA", "BBB", "AT0000627484", "CCC"),
    )

    assert result.status == coverage.UNUSABLE_SOURCE
    assert result.covered_isins == ("AAA", "BBB", "AT0000627484")
    assert result.source_used_by_isin == {
        "AAA": "erste_market",
        "BBB": "oekb",
        "AT0000627484": "morningstar",
    }
    assert result.coverage_ratio == 3 / 4


def test_json_and_csv_output(tmp_path: Path) -> None:
    row = _window_at(date(2025, 8, 1), _lineage_availability(), ("AT0000A2VH41",))
    payload = coverage.build_payload([row], inputs={"historical_audit": "fixture"})
    json_path = tmp_path / "coverage.json"
    csv_path = tmp_path / "coverage.csv"

    coverage.write_outputs(json_path, csv_path, payload)

    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["summary"]["complete_windows"] == 1
    with csv_path.open(newline="", encoding="utf-8") as input_file:
        csv_rows = list(csv.DictReader(input_file))
    assert csv_rows[0]["status"] == coverage.COMPLETE
    assert json.loads(csv_rows[0]["source_used_by_isin"]) == {
        "AT0000A2VH41": "corporate_action_lineage"
    }
    assert json.loads(csv_rows[0]["corporate_action_lineage_by_isin"])["AT0000A2VH41"][
        "successor"
    ] == {"isin": "AT0000A3P9Z2", "source": "morningstar"}
    assert saved["windows"][0]["coverage_via_corporate_action"] is True


def test_at0000627484_manifest_record_is_accepted_by_the_strict_allowlist() -> None:
    manifest_path = (
        Path(__file__).resolve().parents[1] / "data/audit/morningstar_fallback_coverage.json"
    )

    merged = coverage.merge_morningstar_fallback_availability(
        {
            isin: _availability(isin)
            for isin in coverage.MORNINGSTAR_VERIFIED_EVIDENCE
        },
        manifest_path,
    )

    morningstar = merged["AT0000627484"].morningstar
    assert morningstar is not None
    assert morningstar.source_name == "morningstar"
    assert morningstar.first_date == date(2024, 7, 2)
    assert morningstar.last_date == date(2026, 3, 18)


def test_morningstar_manifest_contains_no_secrets() -> None:
    manifest = (
        Path(__file__).resolve().parents[1] / "data/audit/morningstar_fallback_coverage.json"
    ).read_text(encoding="utf-8").lower()

    assert "bearer " not in manifest
    assert '"token"' not in manifest
    assert '"authorization"' not in manifest
    assert '"cookie"' not in manifest
    assert '"session_id"' not in manifest


def test_corporate_action_lineage_manifest_is_secret_free_and_accepted() -> None:
    manifest = _lineage_manifest_path().read_text(encoding="utf-8").lower()

    assert "bearer " not in manifest
    assert '"token"' not in manifest
    assert '"authorization"' not in manifest
    assert '"cookie"' not in manifest
    assert '"session_id"' not in manifest

    merged = coverage.merge_corporate_action_lineage_availability(
        _predecessor_availability(), _lineage_manifest_path()
    )
    lineage = merged["AT0000A2VH41"].corporate_action_lineage
    assert lineage is not None
    assert lineage.predecessor_isin == "AT0000A2VH41"
    assert lineage.successor_isin == "AT0000A3P9Z2"
