from __future__ import annotations

import importlib.util
import socket
import sqlite3
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.history import mnb_otc

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
COVERAGE_SPEC = importlib.util.spec_from_file_location(
    "audit_backtest_window_coverage_mnb",
    ROOT / "scripts" / "audit_backtest_window_coverage.py",
)
assert COVERAGE_SPEC is not None
assert COVERAGE_SPEC.loader is not None
coverage = importlib.util.module_from_spec(COVERAGE_SPEC)
sys.modules[COVERAGE_SPEC.name] = coverage
COVERAGE_SPEC.loader.exec_module(coverage)

ASSESSMENT_SPEC = importlib.util.spec_from_file_location(
    "audit_hu0000554795_mnb_otc", ROOT / "scripts" / "audit_hu0000554795_mnb_otc.py"
)
assert ASSESSMENT_SPEC is not None
assert ASSESSMENT_SPEC.loader is not None
assessment = importlib.util.module_from_spec(ASSESSMENT_SPEC)
sys.modules[ASSESSMENT_SPEC.name] = assessment
ASSESSMENT_SPEC.loader.exec_module(assessment)

MANIFEST_SPEC = importlib.util.spec_from_file_location(
    "generate_mnb_otc_coverage", ROOT / "scripts" / "generate_mnb_otc_coverage.py"
)
assert MANIFEST_SPEC is not None
assert MANIFEST_SPEC.loader is not None
manifest_generator = importlib.util.module_from_spec(MANIFEST_SPEC)
sys.modules[MANIFEST_SPEC.name] = manifest_generator
MANIFEST_SPEC.loader.exec_module(manifest_generator)


VALID_REPORT = (
    (FIXTURES / "mnb_otc_20241129_extract.txt").read_text(encoding="utf-8").strip()
)


def _observation() -> mnb_otc.MnbOtcObservation:
    return mnb_otc.parse_mnb_otc_report_text(VALID_REPORT, "validated.pdf", "a" * 64)


def test_parse_validated_hu0000554795_row_with_exact_decimals() -> None:
    observation = _observation()

    assert observation.isin == "HU0000554795"
    assert observation.instrument_name == "K250604 Egyéves Magyar Állampapír"
    assert observation.period_start == date(2024, 11, 25)
    assert observation.period_end == date(2024, 12, 1)
    assert observation.nominal_value_huf_thousand == Decimal("51100.0")
    assert observation.purchase_value_huf_thousand == Decimal("52586.805")
    assert observation.average_price == Decimal("102.909598")
    assert observation.minimum_price == Decimal("102.909597")
    assert observation.maximum_price == Decimal("102.909600")
    assert mnb_otc.decimal_text(observation.maximum_price) == "102.909600"
    assert observation.transaction_count == 3
    assert observation.price_type == "OTC_WEEKLY_TRANSACTION_AVERAGE"
    assert observation.as_dict()["nav_equivalent"] is False


def test_parse_real_layout_fixture_with_wrapped_section_and_name() -> None:
    observation = _observation()

    assert observation.instrument_name == "K250604 Egyéves Magyar Állampapír"
    assert observation.transaction_count == 3
    assert observation.maximum_price == Decimal("102.909600")


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("MISSING_TETELSZAM", "missing required columns"),
        ("51100.0", "malformed"),
        ("HU0000554796", "no exact"),
        ("102.909597 102.909600 102.909599", "minimum <= average <= maximum"),
        ("0 102.909597 102.909600", "finite and positive"),
        ("count_zero", "transaction count"),
    ],
)
def test_parser_fails_closed_for_invalid_report_data(
    replacement: str, message: str
) -> None:
    if replacement == "MISSING_TETELSZAM":
        text = VALID_REPORT.replace("Tételszám", "")
    elif replacement == "51100.0":
        text = VALID_REPORT.replace("51100.0", "not-a-number")
    elif replacement == "HU0000554796":
        text = VALID_REPORT.replace("HU0000554795", replacement)
    elif replacement.startswith(("102", "0 ")):
        text = VALID_REPORT.replace(
            "102.909598         102.909597   102.9096", replacement
        )
    else:
        text = VALID_REPORT.replace("102.9096     3.0", "102.9096     0")
    with pytest.raises(mnb_otc.MnbOtcError, match=message):
        mnb_otc.parse_mnb_otc_report_text(text, "validated.pdf", "a" * 64)


def test_identical_duplicate_row_is_deduplicated_but_conflicting_duplicate_fails() -> (
    None
):
    target_row = "K250604 Egyéves       HU0000554795     51100.0           52586.805       102.909598         102.909597   102.9096     3.0\nMagyar Állampapír"
    identical = VALID_REPORT.replace(
        "\nJelzáloglevél", f"\n{target_row}\n\nJelzáloglevél"
    )
    assert (
        mnb_otc.parse_mnb_otc_report_text(identical, "validated.pdf", "a" * 64)
        == _observation()
    )

    conflicting = identical.replace(
        "102.909598         102.909597   102.9096",
        "102.909599         102.909597   102.9096",
        1,
    )
    with pytest.raises(mnb_otc.MnbOtcError, match="conflicting duplicate"):
        mnb_otc.parse_mnb_otc_report_text(conflicting, "validated.pdf", "a" * 64)


def test_parser_requires_the_validated_government_security_section() -> None:
    outside_section = VALID_REPORT.replace(
        "Egyéves Magyar        ISIN azonosító",
        "Jelzáloglevél         ISIN azonosító",
        1,
    )

    with pytest.raises(mnb_otc.MnbOtcError, match="section"):
        mnb_otc.parse_mnb_otc_report_text(outside_section, "validated.pdf", "a" * 64)


def test_text_parser_never_uses_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("MNB text parser attempted network I/O")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    assert _observation().average_price == Decimal("102.909598")


def test_sqlite_round_trip_is_idempotent_and_rejects_conflicting_reimport(
    tmp_path: Path,
) -> None:
    repository = mnb_otc.MnbOtcRepository(tmp_path / "prices.sqlite")
    observation = _observation()

    assert repository.import_observation(observation) is True
    assert repository.import_observation(observation) is False
    stored = repository.observations("HU0000554795")
    assert stored == (observation,)
    assert stored[0].frequency == "WEEKLY_OTC_AGGREGATE"
    assert stored[0].source_document_hash == "a" * 64

    conflicting = replace(observation, average_price=Decimal("102.909599"))
    with pytest.raises(mnb_otc.MnbOtcError, match="Conflicting persisted"):
        repository.import_observation(conflicting)

    with sqlite3.connect(tmp_path / "prices.sqlite") as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(mnb_otc_observations)"
            ).fetchall()
        }
    assert "average_price" in columns
    assert "net_asset_value" not in columns


def test_quality_summary_uses_zero_gap_for_contiguous_reporting_periods() -> None:
    first = _observation()
    contiguous = replace(
        first, period_start=date(2024, 12, 2), period_end=date(2024, 12, 8)
    )

    quality = mnb_otc.quality_summary((first, contiguous))

    assert quality["maximum_gap_days"] == 0
    assert quality["median_gap_days"] == 0
    assert quality["median_average_price"] == "102.909598"


def test_manifest_and_reader_preserve_non_nav_semantics(tmp_path: Path) -> None:
    database = tmp_path / "prices.sqlite"
    assert mnb_otc.MnbOtcRepository(database).import_observation(_observation())
    manifest = manifest_generator.build_manifest(database)

    assert manifest["nav_equivalent"] is False
    assert manifest["backtest_return_series_approved"] is False
    result = manifest["results"]
    assert isinstance(result, list)
    assert result[0]["quality"]["observation_count"] == 1

    path = tmp_path / "mnb.json"
    manifest_generator.write_manifest(path, manifest)
    availability = {
        "HU0000554795": coverage.HistoricalAvailability(
            "HU0000554795",
            coverage.SourceAvailability("erste_market", "NO_ERSTE_MAPPING", None, None),
        )
    }
    merged = coverage.merge_mnb_otc_evidence(availability, path)
    window = coverage.evaluate_window(
        observation_date=date(2024, 11, 25),
        horizon=90,
        portfolio_name="Test",
        required_isins=("HU0000554795",),
        availability=merged,
    )
    assert window.status == coverage.UNUSABLE_SOURCE
    assert window.source_used_by_isin == {}


def test_mnb_evidence_is_visible_to_audit_but_never_completes_nav_coverage() -> None:
    availability = {
        "HU0000554795": coverage.HistoricalAvailability(
            "HU0000554795",
            coverage.SourceAvailability("erste_market", "NO_ERSTE_MAPPING", None, None),
            mnb_otc=coverage.MnbOtcAvailability(
                first_period_start=date(2024, 11, 25),
                last_period_end=date(2024, 12, 1),
                observation_count=1,
                price_type="OTC_WEEKLY_TRANSACTION_AVERAGE",
                frequency="WEEKLY_OTC_AGGREGATE",
            ),
        )
    }
    result = coverage.evaluate_window(
        observation_date=date(2024, 11, 25),
        horizon=90,
        portfolio_name="Test",
        required_isins=("HU0000554795",),
        availability=availability,
    )

    assert result.status == coverage.UNUSABLE_SOURCE
    evidence = result.mnb_otc_evidence_by_isin["HU0000554795"]
    assert evidence["nav_equivalent"] is False
    assert evidence["frequency_sufficient"] is False
    assert evidence["boundary_price_available"] is False
    assert evidence["backtest_return_series_approved"] is False


def test_lifecycle_classifies_validated_maturity_without_synthetic_prices() -> None:
    windows = [
        {"required_start": "2025-05-01", "required_end": "2025-05-31"},
        {"required_start": "2025-05-01", "required_end": "2025-06-30"},
        {"required_start": "2025-07-01", "required_end": "2025-09-01"},
    ]

    result = assessment.lifecycle_counts(windows, date(2025, 6, 4))

    assert result["windows_ending_before_maturity"] == 1
    assert result["windows_crossing_maturity"] == 1
    assert result["windows_entirely_post_maturity"] == 1
    assert result["windows_requiring_lifecycle_handling"] == 2


def test_assessment_without_local_maturity_or_observations_remains_unapproved(
    tmp_path: Path,
) -> None:
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(
        '{"windows":[{"unusable_isins":["HU0000554795"],"required_start":"2024-07-02",'
        '"required_end":"2024-09-30","horizon":90,"status":"UNUSABLE_SOURCE"}]}',
        encoding="utf-8",
    )
    manifest_path = tmp_path / "mnb.json"
    manifest_path.write_text(
        '{"source":"mnb_otc","results":[],"nav_equivalent":false,'
        '"backtest_return_series_approved":false}',
        encoding="utf-8",
    )

    result = assessment.build_assessment(coverage_path, manifest_path)

    assert result["maturity_status"] == "MATURITY_NOT_VALIDATED_LOCALLY"
    assert result["observations_found"] == 0
    assert result["windows_still_missing_source_evidence"] == 1
    assert result["affected_windows_by_horizon"] == {"90": 1}
    details = result["affected_window_mnb_evidence"]
    assert isinstance(details, list)
    assert details[0]["mnb_evidence_status"] == "NO_MNB_EVIDENCE"
    assert result["nav_equivalent"] is False
    assert result["backtest_return_series_approved"] is False


def test_assessment_describes_three_overlapping_periods_without_approving_them() -> (
    None
):
    windows = [{"required_start": "2024-08-01", "required_end": "2024-12-15"}]
    quality = {
        "observed_periods": [
            {"start": "2024-08-26", "end": "2024-09-01"},
            {"start": "2024-10-28", "end": "2024-11-03"},
            {"start": "2024-11-25", "end": "2024-12-01"},
        ]
    }

    counts = assessment.window_evidence_counts(windows, quality)
    details = assessment.window_evidence_details(windows, quality)

    assert counts["windows_with_substantial_mnb_history"] == 1
    assert details[0]["mnb_evidence_status"] == "SUBSTANTIAL_MNB_HISTORY"
    assert details[0]["boundary_price_available"] is False


def test_assessment_consumes_validated_lifecycle_without_approving_returns(
    tmp_path: Path,
) -> None:
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(
        '{"windows":[{"unusable_isins":["HU0000554795"],"required_start":"2024-07-02",'
        '"required_end":"2024-09-30","horizon":90,"status":"UNUSABLE_SOURCE"}]}',
        encoding="utf-8",
    )
    manifest_path = tmp_path / "mnb.json"
    manifest_path.write_text(
        '{"source":"mnb_otc","results":[],"nav_equivalent":false,'
        '"backtest_return_series_approved":false}',
        encoding="utf-8",
    )
    lifecycle_path = tmp_path / "lifecycle.json"
    lifecycle_path.write_text(
        '{"isin":"HU0000554795","maturity_validated":true,'
        '"redemption_mechanics_validated":false,"maturity_date":"2025-06-04",'
        '"source_document_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"lifecycle_classification_counts":{"PRE_MATURITY":1},'
        '"lifecycle_classification_counts_by_horizon":{"90":{"PRE_MATURITY":1}},'
        '"lifecycle_status":"MATURITY_VALIDATED","nav_equivalent":false,'
        '"backtest_return_series_approved":false,"usable_for_backtest":false,'
        '"recommended_otc_acquisition_start":"2024-07-02",'
        '"recommended_otc_acquisition_end":"2025-06-04"}',
        encoding="utf-8",
    )

    result = assessment.build_assessment(
        coverage_path, manifest_path, lifecycle_audit_path=lifecycle_path
    )

    assert result["maturity_validated"] is True
    assert result["redemption_mechanics_validated"] is False
    assert result["maturity_date"] == "2025-06-04"
    assert result["nav_equivalent"] is False
    assert result["backtest_return_series_approved"] is False
    assert result["usable_for_backtest"] is False
