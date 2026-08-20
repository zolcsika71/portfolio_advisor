from __future__ import annotations

import importlib.util
import json
import socket
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "analyze_hu0000554795_coverage_gap.py"
SPEC = importlib.util.spec_from_file_location("hu0000554795_coverage_gap", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
gap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gap
SPEC.loader.exec_module(gap)

COVERAGE = ROOT / "data" / "audit" / "backtest_window_coverage.json"
DIAGNOSTICS = ROOT / "data" / "audit" / "erste_nav_diagnostics.json"
OEKB = ROOT / "data" / "audit" / "oekb_fallback_coverage.json"
MORNINGSTAR = ROOT / "data" / "audit" / "morningstar_fallback_coverage.json"
LINEAGE = ROOT / "data" / "audit" / "corporate_action_lineage.json"


def _nav_payload(
    *,
    isin: str = "HU0000554795",
    currency: str = "HUF",
    dates: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source": "validated_local_fixture",
        "isin": isin,
        "currency": currency,
        "fund_name": "Exact fixture share class",
        "provenance": {"artifact": "fixture", "downloaded_at": "local"},
        "nav_observations": [
            {"date": current, "nav": "100.00"}
            for current in (dates or ["2024-07-02", "2026-03-03"])
        ],
    }


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_extracts_all_affected_windows_and_only_blockers() -> None:
    windows = gap.read_affected_windows(COVERAGE)
    summary = gap.gap_summary(windows)

    assert len(windows) == 132
    assert summary["only_blocker_window_count"] == 117
    assert summary["other_isins_also_block_window_count"] == 15
    assert summary["failure_reason_counts"] == {
        "HU0000554795: Erste source status NO_ERSTE_MAPPING is not usable": 132
    }


def test_horizon_counts_and_required_boundaries() -> None:
    summary = gap.gap_summary(gap.read_affected_windows(COVERAGE))

    assert summary["horizon_counts"] == {90: 44, 180: 44, 365: 44}
    assert summary["earliest_required_start"] == "2024-07-02"
    assert summary["latest_required_start"] == "2025-03-03"
    assert summary["earliest_required_end"] == "2024-09-30"
    assert summary["latest_required_end"] == "2026-03-03"
    assert summary["distinct_required_start_dates"] == 16
    assert summary["distinct_required_end_dates"] == 44


def test_exact_isin_local_nav_source_is_accepted(tmp_path: Path) -> None:
    source = gap.source_from_local_nav_payload(
        _write_json(tmp_path / "source.json", _nav_payload())
    )

    assert source.isin == "HU0000554795"
    assert source.currency == "HUF"
    assert source.first_date == date(2024, 7, 2)
    assert source.last_date == date(2026, 3, 3)


def test_wrong_isin_and_currency_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(gap.CoverageGapError, match="ISIN mismatch"):
        gap.source_from_local_nav_payload(
            _write_json(tmp_path / "wrong-isin.json", _nav_payload(isin="HU0000705991"))
        )
    with pytest.raises(gap.CoverageGapError, match="currency mismatch"):
        gap.source_from_local_nav_payload(
            _write_json(tmp_path / "wrong-currency.json", _nav_payload(currency="EUR"))
        )


def test_partial_range_classification_uses_strict_boundaries(tmp_path: Path) -> None:
    windows = gap.read_affected_windows(COVERAGE)
    partial = gap.source_from_local_nav_payload(
        _write_json(
            tmp_path / "partial.json",
            _nav_payload(dates=["2024-07-02", "2025-01-01"]),
        )
    )
    coverable, uncoverable = gap.coverability(windows, [partial])

    assert coverable
    assert uncoverable
    assert all(item.required_end <= date(2025, 1, 1) for item in coverable)
    assert all(item.required_end > date(2025, 1, 1) for item in uncoverable)


def test_no_source_fails_closed_and_preserves_backtest_unusable() -> None:
    report = gap.run(COVERAGE, DIAGNOSTICS, OEKB, MORNINGSTAR, LINEAGE)

    assert report["currently_coverable_window_count"] == 0
    assert report["currently_uncoverable_window_count"] == 132
    assert report["recommended_next_action"] == "EXTERNAL_SOURCE_RESEARCH_REQUIRED"
    assert report["usable_for_backtest"] is False


def test_report_json_is_deterministic(tmp_path: Path) -> None:
    report = gap.run(COVERAGE, DIAGNOSTICS, OEKB, MORNINGSTAR, LINEAGE)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    gap.write_report(first, report)
    gap.write_report(second, report)

    assert first.read_bytes() == second.read_bytes()


def test_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("Network access is prohibited")

    monkeypatch.setattr(socket, "create_connection", no_network)
    report = gap.run(COVERAGE, DIAGNOSTICS, OEKB, MORNINGSTAR, LINEAGE)

    assert report["affected_window_count"] == 132
    assert report["usable_for_backtest"] is False
