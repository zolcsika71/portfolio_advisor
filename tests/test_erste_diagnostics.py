from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from urllib.error import URLError

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_erste_mapping.py"
SPEC = importlib.util.spec_from_file_location("validate_erste_mapping", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
erste = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = erste
SPEC.loader.exec_module(erste)


ISIN = "HU0000702477"


def _detail_page(instrument_id: str | None) -> bytes:
    if instrument_id is None:
        return b"<html><body>No chart is available.</body></html>"
    return (
        '<div class="simpleChartContainer" instrument-id="'
        f"{instrument_id}\"></div>"
    ).encode()


def _chart(series: list[list[float]], *, isin: str = ISIN, instrument_id: str = "42") -> bytes:
    return json.dumps(
        {"isin": isin, "instrument_id": instrument_id, "series": series}
    ).encode()


def _confirmed_sentinel_series() -> list[list[float]]:
    return [
        [68_400_000, 0.0],
        [1_354_302_000_000, 10.0],
        [1_354_388_400_000, 10.1],
        [1_354_474_800_000, 10.2],
    ]


def test_resolver_uses_exact_autocomplete_fallback_and_chart_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_http_get(url: str, **_: object) -> bytes:
        calls.append(url)
        if "/befektetesi_alapok/alap/" in url:
            return _detail_page(None)
        if "/autocomplete/Fund/" in url:
            return json.dumps([{"isin": ISIN, "id": "42"}, {"isin": "OTHER", "id": "999"}]).encode()
        return _chart([[1_000, 10.0], [2_000, 11.0]])

    monkeypatch.setattr(erste, "http_get", fake_http_get)
    result = erste.validate_isin(ISIN, "HUF")

    assert result.status == "PASS"
    assert result.usable_for_backtest
    assert result.normalized_observations == result.observations
    assert result.filtered_observations == 0
    assert result.instrument_id == "42"
    assert result.resolution_method == "autocomplete"
    assert [attempt["path"] for attempt in result.resolution_attempts] == [
        "detail_page",
        "autocomplete",
    ]
    assert "/befektetesi_alapok/alap/" in calls[0]
    assert "/autocomplete/Fund/" in calls[1]
    assert "/funds/chart/42" in calls[2]


def test_invalid_nav_records_raw_observation_and_five_neighbors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = [[index * 1_000, 10.0] for index in range(13)]
    series[6][1] = 0.0

    def fake_http_get(url: str, **_: object) -> bytes:
        return _detail_page("42") if "/alap/" in url else _chart(series)

    monkeypatch.setattr(erste, "http_get", fake_http_get)
    result = erste.validate_isin(ISIN, "HUF")

    assert result.status == "INVALID_NAV"
    assert not result.usable_for_backtest
    detail = result.anomaly_details[0]
    assert detail["kind"] == "INVALID_NAV"
    assert detail["observation"] == {
        "timestamp": 6_000,
        "date": "1970-01-01",
        "value": 0.0,
    }
    assert len(detail["before"]) == 5
    assert len(detail["after"]) == 5


@pytest.mark.parametrize(
    ("isin", "currency"),
    [("IE00B7KFL990", "USD"), ("IE00B84J9L26", "EUR")],
)
def test_confirmed_epoch_sentinel_is_filtered_with_full_provenance(
    monkeypatch: pytest.MonkeyPatch, isin: str, currency: str
) -> None:
    series = _confirmed_sentinel_series()

    def fake_http_get(url: str, **_: object) -> bytes:
        return _detail_page("42") if "/alap/" in url else _chart(series, isin=isin)

    monkeypatch.setattr(erste, "http_get", fake_http_get)
    result = erste.validate_isin(isin, currency)
    record = erste.audit_record(result)

    assert result.status == "PASS_WITH_FILTERED_SENTINEL"
    assert result.usable_for_backtest
    assert result.observations == 4
    assert result.normalized_observations == 3
    assert result.filtered_observations == 1
    assert result.first_date == "1970-01-01"
    assert result.normalized_first_date == "2012-11-30"
    assert result.anomaly_details[0]["kind"] == "SOURCE_SENTINEL"
    assert result.anomaly_details[0]["original_raw_observation"] == {
        "timestamp": 68_400_000,
        "date": "1970-01-01",
        "value": 0.0,
    }
    assert result.normalization_actions[0]["action"] == (
        "exclude_raw_sentinel_from_normalized_series"
    )
    assert record["raw_observation_count"] == 4
    assert record["normalized_observation_count"] == 3
    assert record["filtered_observation_count"] == 1
    assert record["normalized_date_range"]["first"] == "2012-11-30"


def test_zero_nav_at_a_normal_historical_date_remains_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = [[1_354_302_000_000, 10.0], [1_354_388_400_000, 0.0]]

    def fake_http_get(url: str, **_: object) -> bytes:
        return _detail_page("42") if "/alap/" in url else _chart(series)

    monkeypatch.setattr(erste, "http_get", fake_http_get)
    result = erste.validate_isin(ISIN, "HUF")

    assert result.status == "INVALID_NAV"
    assert not result.usable_for_backtest
    assert result.filtered_observations == 0
    assert result.normalization_actions == ()


def test_negative_nav_remains_invalid_even_for_a_confirmed_isin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = [
        [68_400_000, -1.0],
        [1_354_302_000_000, 10.0],
        [1_354_388_400_000, 10.1],
    ]

    def fake_http_get(url: str, **_: object) -> bytes:
        return _detail_page("42") if "/alap/" in url else _chart(series, isin="IE00B7KFL990")

    monkeypatch.setattr(erste, "http_get", fake_http_get)
    result = erste.validate_isin("IE00B7KFL990", "USD")

    assert result.status == "INVALID_NAV"
    assert not result.usable_for_backtest
    assert result.filtered_observations == 0


def test_multiple_invalid_nav_values_are_not_silently_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = _confirmed_sentinel_series()
    series.append([1_354_561_200_000, 0.0])

    def fake_http_get(url: str, **_: object) -> bytes:
        return _detail_page("42") if "/alap/" in url else _chart(series, isin="IE00B84J9L26")

    monkeypatch.setattr(erste, "http_get", fake_http_get)
    result = erste.validate_isin("IE00B84J9L26", "EUR")

    assert result.status == "INVALID_NAV"
    assert not result.usable_for_backtest
    assert result.filtered_observations == 0


def test_conflicting_duplicates_retain_all_values_and_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = [[index * 1_000, 10.0] for index in range(6)]
    series.extend([[6_000, 10.0], [6_000, 20.0]])
    series.extend([[index * 1_000, 10.0] for index in range(7, 13)])

    def fake_http_get(url: str, **_: object) -> bytes:
        return _detail_page("42") if "/alap/" in url else _chart(series)

    monkeypatch.setattr(erste, "http_get", fake_http_get)
    result = erste.validate_isin(ISIN, "HUF")

    assert result.status == "CONFLICTING_HISTORY"
    assert not result.usable_for_backtest
    assert result.normalization_actions == ()
    assert result.normalized_observations == result.observations
    detail = result.anomaly_details[0]
    assert detail["timestamp"] == 6_000
    assert detail["values"] == [10.0, 20.0]
    assert detail["occurrence_indexes"] == [6, 7]
    context = detail["surrounding_observations"]
    assert len(context["before"]) == 5
    assert len(context["after"]) == 5


def test_unmapped_isin_records_both_failed_resolution_paths_and_json_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def fake_http_get(url: str, **_: object) -> bytes:
        calls.append(url)
        if "/alap/" in url:
            return _detail_page(None)
        return b"[]"

    monkeypatch.setattr(erste, "http_get", fake_http_get)
    result = erste.validate_isin(ISIN, "HUF")
    output = tmp_path / "diagnostics.json"
    erste.write_audit_output(output, [result], {result.status: 1})

    assert result.status == "NO_ERSTE_MAPPING"
    assert not result.usable_for_backtest
    assert [attempt["outcome"] for attempt in result.resolution_attempts] == [
        "no_instrument_id",
        "no_exact_isin_match",
    ]
    assert len(calls) == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    record = payload["results"][0]
    assert record["isin"] == ISIN
    assert record["status"] == "NO_ERSTE_MAPPING"
    assert record["instrument_id"] is None
    assert record["resolution_method"] == "detail_page_then_autocomplete"
    assert record["observation_count"] == 0
    assert record["date_range"] == {"first": None, "last": None}
    assert not record["usable_for_backtest"]


@pytest.mark.parametrize("status", ["NO_ERSTE_MAPPING", "INVALID_NAV", "CONFLICTING_HISTORY", "NO_CHART_HISTORY", "SOURCE_ERROR"])
def test_non_pass_states_fail_closed(status: str) -> None:
    result = erste.make_result(
        isin=ISIN,
        currency="HUF",
        instrument_id=None,
        returned_isin=None,
        resolution_method="none",
        status=status,
    )

    assert not result.usable_for_backtest


def test_source_and_missing_chart_history_have_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_chart_http_get(url: str, **_: object) -> bytes:
        return _detail_page("42") if "/alap/" in url else _chart([])

    monkeypatch.setattr(erste, "http_get", no_chart_http_get)
    no_chart = erste.validate_isin(ISIN, "HUF")
    assert no_chart.status == "NO_CHART_HISTORY"
    assert no_chart.anomaly_details[0]["kind"] == "NO_CHART_HISTORY"

    def source_error_http_get(_: str, **__: object) -> bytes:
        raise URLError("unavailable")

    monkeypatch.setattr(erste, "http_get", source_error_http_get)
    source_error = erste.validate_isin(ISIN, "HUF")
    assert source_error.status == "SOURCE_ERROR"
    assert source_error.anomaly_details[0]["kind"] == "SOURCE_ERROR"


def _source_validation(
    status: str,
    *,
    isin: str = ISIN,
    currency: str | None = "HUF",
    parsed: list[tuple[int, float]] | None = None,
) -> object:
    result = erste.make_result(
        isin=isin,
        currency=currency,
        instrument_id="42" if status != "NO_ERSTE_MAPPING" else None,
        returned_isin=isin,
        resolution_method="detail_page",
        status=status,
        parsed=parsed,
        chronological=True,
    )
    return erste.source_validation_from_erste(result, "2026-08-08T00:00:00+00:00")


class _FakeSecondarySource:
    source_name = "approved_secondary"
    source_priority = 2

    def __init__(self, validation: object) -> None:
        self.validation = validation
        self.calls: list[tuple[str, str | None]] = []

    def validate_history(self, isin: str, currency: str | None) -> object:
        self.calls.append((isin, currency))
        return self.validation


def _secondary_payload(
    *,
    isin: str = ISIN,
    currency: str | None = "HUF",
    observations: tuple[tuple[int, float], ...] = ((1_000, 10.0), (2_000, 11.0)),
) -> object:
    return erste.SecondaryHistoryPayload(
        isin=isin,
        currency=currency,
        observations=observations,
        provenance=erste.SourceProvenance(
            source_name="approved_secondary",
            source_priority=2,
            source_identifier="secondary-42",
            endpoint_metadata={"history": "https://secondary.example/history/42"},
            retrieved_at="2026-08-08T00:00:00+00:00",
        ),
    )


def test_existing_erste_pass_and_sentinel_remain_selected_primary() -> None:
    resolver = erste.FallbackSourceResolver()
    passed = resolver.resolve(_source_validation("PASS"))
    sentinel = resolver.resolve(
        _source_validation(
            "PASS_WITH_FILTERED_SENTINEL",
            isin="IE00B7KFL990",
            currency="USD",
            parsed=[
                (68_400_000, 0.0),
                (1_354_302_000_000, 10.0),
                (1_354_388_400_000, 10.1),
            ],
        )
    )

    assert passed.status == "PASS"
    assert passed.selected_source == "erste_market"
    assert passed.usable_for_backtest
    assert sentinel.status == "PASS_WITH_FILTERED_SENTINEL"
    assert sentinel.selected_source == "erste_market"
    assert sentinel.usable_for_backtest


def test_erste_source_adapter_reuses_existing_validation_result() -> None:
    primary_result = erste.make_result(
        isin=ISIN,
        currency="HUF",
        instrument_id="42",
        returned_isin=ISIN,
        resolution_method="detail_page",
        status="PASS",
        parsed=[(1_000, 10.0), (2_000, 11.0)],
        chronological=True,
    )
    source = erste.ErsteMarketNavSource(
        validator=lambda _isin, _currency: primary_result,
        timestamp_factory=lambda: "2026-08-08T00:00:00+00:00",
    )

    validation = source.validate_history(ISIN, "HUF")

    assert validation.status == "PASS"
    assert validation.provenance.source_name == "erste_market"
    assert validation.provenance.source_identifier == "42"
    assert validation.provenance.endpoint_metadata["chart"].endswith("/42")


def test_fallback_is_only_invoked_for_no_erste_mapping() -> None:
    payload = _secondary_payload()
    secondary_validation = erste.make_secondary_validation(
        payload, expected_isin=ISIN, expected_currency="HUF"
    )
    secondary = _FakeSecondarySource(secondary_validation)
    resolver = erste.FallbackSourceResolver(secondary)

    passed = resolver.resolve(_source_validation("PASS"))
    unresolved = resolver.resolve(_source_validation("NO_ERSTE_MAPPING"))

    assert passed.status == "PASS"
    assert unresolved.status == "PASS_WITH_FALLBACK_SOURCE"
    assert secondary.calls == [(ISIN, "HUF")]


def test_exact_isin_secondary_history_is_accepted_and_records_provenance() -> None:
    fallback = erste.make_secondary_validation(
        _secondary_payload(), expected_isin=ISIN, expected_currency="HUF"
    )
    coverage = erste.FallbackSourceResolver(_FakeSecondarySource(fallback)).resolve(
        _source_validation("NO_ERSTE_MAPPING")
    )
    record = erste.source_coverage_record(coverage)

    assert coverage.status == "PASS_WITH_FALLBACK_SOURCE"
    assert coverage.usable_for_backtest
    assert record["selected_source"] == "approved_secondary"
    assert record["source_priority"] == 2
    assert record["source_identifier"] == "secondary-42"
    assert record["fallback_source_status"] == "PASS"
    assert record["primary_provenance"]["source_name"] == "erste_market"
    assert record["fallback_provenance"]["endpoint_metadata"] == {
        "history": "https://secondary.example/history/42"
    }


def test_mismatched_or_invalid_secondary_history_is_rejected_fail_closed() -> None:
    mismatch = erste.make_secondary_validation(
        _secondary_payload(isin="OTHER"), expected_isin=ISIN, expected_currency="HUF"
    )
    invalid = erste.make_secondary_validation(
        _secondary_payload(observations=((1_000, 10.0), (2_000, 0.0))),
        expected_isin=ISIN,
        expected_currency="HUF",
    )

    mismatch_coverage = erste.FallbackSourceResolver(
        _FakeSecondarySource(mismatch)
    ).resolve(_source_validation("NO_ERSTE_MAPPING"))
    invalid_coverage = erste.FallbackSourceResolver(
        _FakeSecondarySource(invalid)
    ).resolve(_source_validation("NO_ERSTE_MAPPING"))

    assert mismatch.status == "SOURCE_ERROR"
    assert mismatch_coverage.status == "SOURCE_ERROR"
    assert not mismatch_coverage.usable_for_backtest
    assert invalid.status == "INVALID_NAV"
    assert invalid_coverage.status == "INVALID_NAV"
    assert not invalid_coverage.usable_for_backtest


def test_no_configured_secondary_is_explicitly_required(tmp_path: Path) -> None:
    coverage = erste.FallbackSourceResolver().resolve(
        _source_validation("NO_ERSTE_MAPPING")
    )
    output = tmp_path / "source_coverage.json"
    erste.write_source_coverage_output(output, [coverage])
    payload = json.loads(output.read_text(encoding="utf-8"))
    record = payload["results"][0]

    assert coverage.status == "SECONDARY_SOURCE_REQUIRED"
    assert coverage.primary_source_status == "NO_ERSTE_MAPPING"
    assert coverage.fallback_source_status is None
    assert not coverage.usable_for_backtest
    assert payload["status_counts"] == {"SECONDARY_SOURCE_REQUIRED": 1}
    assert record["primary_provenance"]["source_priority"] == 1
    assert record["date_range"] == {"first": None, "last": None}


def test_conflicting_history_requires_reconciliation_without_fallback() -> None:
    fallback = erste.make_secondary_validation(
        _secondary_payload(), expected_isin="AT0000605324", expected_currency="USD"
    )
    secondary = _FakeSecondarySource(fallback)
    coverage = erste.FallbackSourceResolver(secondary).resolve(
        _source_validation(
            "CONFLICTING_HISTORY",
            isin="AT0000605324",
            currency="USD",
            parsed=[(1_000, 10.0), (1_000, 20.0)],
        )
    )

    assert coverage.status == "RECONCILIATION_REQUIRED"
    assert coverage.reconciliation_status == "independent_secondary_history_required"
    assert not coverage.usable_for_backtest
    assert secondary.calls == []
