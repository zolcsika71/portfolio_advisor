from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "reconcile_at0000605324.py"
)
SPEC = importlib.util.spec_from_file_location("reconcile_at0000605324", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
reconcile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reconcile
SPEC.loader.exec_module(reconcile)


def _diagnostics_payload(step_days: int = 1) -> dict[str, object]:
    start = date(2005, 3, 2)
    anomalies: list[dict[str, object]] = []
    for index in range(28):
        current = start + timedelta(days=index * step_days)
        anomalies.append(
            {
                "kind": "CONFLICTING_HISTORY",
                "date": current.isoformat(),
                "timestamp": index,
                "values": [str(index + 10), str(index + 20)],
            }
        )
    return {
        "results": [
            {
                "isin": "AT0000605324",
                "status": "CONFLICTING_HISTORY",
                "anomaly_details": anomalies,
            }
        ]
    }


def _write_diagnostics(tmp_path: Path, step_days: int = 1) -> Path:
    path = tmp_path / "erste_nav_diagnostics.json"
    path.write_text(json.dumps(_diagnostics_payload(step_days)), encoding="utf-8")
    return path


def _morningstar_payload(
    *,
    conflicts: list[Any] | None = None,
    query_key: str = "F0000008OS",
) -> list[dict[str, object]]:
    first = date(2005, 3, 1)
    last = date(2012, 4, 25)
    dates = {first, last}
    if conflicts is not None:
        dates.update(conflict.calendar_date for conflict in conflicts)
    current = first
    while len(dates) < 1772:
        dates.add(current)
        current += timedelta(days=1)
    series = [
        {"date": current_date.isoformat(), "nav": 100.0}
        for current_date in sorted(dates)
    ]
    series[0]["nav"] = 92.98
    series[-1]["nav"] = 128.61
    return [{"queryKey": query_key, "series": series}]


def _write_morningstar_evidence(
    tmp_path: Path, *, conflicts: list[Any] | None = None
) -> Path:
    path = tmp_path / "morningstar.json"
    path.write_text(json.dumps(_morningstar_payload(conflicts=conflicts)), encoding="utf-8")
    return path


def _oekb_row(
    current: date,
    value: str,
    *,
    isin: str = "AT0000605324",
    wfs_ku: object = "wfs-1",
) -> dict[str, object]:
    return {
        "numWkn": isin,
        "numKursErrechneterWert": value,
        "waehrung": "USD",
        "datKurs": current.isoformat(),
        "numWfsKu": wfs_ku,
    }


def _paged_http(rows: list[dict[str, object]], calls: list[dict[str, str]]):
    def fake_http_get(url: str, timeout: int) -> bytes:
        assert timeout == 30
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        calls.append(query)
        offset = int(query["offset"])
        limit = int(query["limit"])
        return json.dumps({"anz": len(rows), "list": rows[offset : offset + limit]}).encode()

    return fake_http_get


def test_exact_comparison_preserves_oekb_fields_and_all_classifications(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    diagnostics = _write_diagnostics(tmp_path)
    conflicts, digest = reconcile.read_erste_conflicts(diagnostics)
    first, second, third, fourth = [conflict.calendar_date for conflict in conflicts[:4]]
    conflicts[3] = reconcile.ErsteConflict(
        calendar_date=fourth,
        value_a=Decimal(13),
        value_b=Decimal(13),
        raw_detail=conflicts[3].raw_detail,
    )
    calls: list[dict[str, str]] = []
    rows = [
        _oekb_row(first, "10", wfs_ku="wfs-a"),
        _oekb_row(second, "21", wfs_ku="wfs-b"),
        _oekb_row(third, "999", wfs_ku="wfs-c"),
        _oekb_row(fourth, "13", wfs_ku="wfs-d"),
    ]
    monkeypatch.setattr(reconcile, "http_get", _paged_http(rows, calls))

    history = reconcile.fetch_oekb_history(
        start_date=conflicts[0].calendar_date,
        end_date=conflicts[-1].calendar_date,
        limit=100,
        timeout=30,
    )
    payload = reconcile.build_reconciliation_payload(
        diagnostics_path=diagnostics,
        conflicts=conflicts,
        diagnostics_sha256=digest,
        history=history,
    )
    comparisons = payload["comparisons"]

    assert comparisons[0]["classification"] == "MATCH_ERSTE_VALUE_A"
    assert comparisons[1]["classification"] == "MATCH_ERSTE_VALUE_B"
    assert comparisons[2]["classification"] == "MATCH_NEITHER"
    assert comparisons[3]["classification"] == "MATCH_BOTH"
    assert comparisons[4]["classification"] == "NO_OEKB_OBSERVATION"
    assert comparisons[0]["oekb_observation"] == {
        "numWkn": "AT0000605324",
        "numKursErrechneterWert": "10",
        "waehrung": "USD",
        "datKurs": first.isoformat(),
        "numWfsKu": "wfs-a",
    }
    assert payload["summary_counts"] == {
        "MATCH_BOTH": 1,
        "MATCH_ERSTE_VALUE_A": 1,
        "MATCH_ERSTE_VALUE_B": 1,
        "MATCH_NEITHER": 1,
        "NO_OEKB_OBSERVATION": 24,
    }
    assert payload["usable_for_backtest"] is False
    assert payload["deterministic_reconciliation_rule_accepted"] is False
    assert calls[0]["von"] == first.strftime("%Y%m%d")
    assert payload["oekb_provenance"]["merged_result"]["raw_observation_count"] == 4


def test_reconciliation_acquires_across_multiple_bounded_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    diagnostics = _write_diagnostics(tmp_path, step_days=90)
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(reconcile, "http_get", _paged_http([], calls))

    payload = reconcile.run_reconciliation(diagnostics, limit=100, timeout=30)

    chunks = payload["oekb_provenance"]["chunks"]
    assert len(chunks) > 1
    assert all(chunk["reported_anz"] == 0 for chunk in chunks)
    assert payload["summary_counts"] == {"NO_OEKB_OBSERVATION": 28}
    assert len(calls) == len(chunks)


def test_oekb_isin_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _oekb_row(date(2005, 3, 2), "10", isin="AT0000000000")
    monkeypatch.setattr(reconcile, "http_get", _paged_http([row], []))

    with pytest.raises(reconcile.ReconciliationError, match="ISIN mismatch"):
        reconcile.fetch_oekb_history(
            start_date=date(2005, 3, 2),
            end_date=date(2005, 3, 2),
            limit=100,
            timeout=30,
        )


def test_failure_payload_is_explicitly_not_usable_for_backtest(tmp_path: Path) -> None:
    payload = reconcile.build_failure_payload(
        _write_diagnostics(tmp_path), "OeKB ISIN mismatch"
    )

    assert payload["status"] == "SOURCE_ERROR"
    assert payload["reconciliation_status"] == "RECONCILIATION_REQUIRED"
    assert payload["usable_for_backtest"] is False
    assert payload["deterministic_reconciliation_rule_accepted"] is False


def test_local_morningstar_comparison_is_exact_and_never_approves_backtesting(
    tmp_path: Path,
) -> None:
    diagnostics = _write_diagnostics(tmp_path)
    conflicts, digest = reconcile.read_erste_conflicts(diagnostics)
    evidence = _write_morningstar_evidence(tmp_path, conflicts=conflicts)
    payload = _morningstar_payload(conflicts=conflicts)
    rows = payload[0]["series"]
    assert isinstance(rows, list)
    values_by_date = {
        conflicts[0].calendar_date: 10.0,
        conflicts[1].calendar_date: 21.0,
        conflicts[2].calendar_date: 999.0,
        conflicts[3].calendar_date: 13.0,
    }
    for row in rows:
        assert isinstance(row, dict)
        row_date = date.fromisoformat(str(row["date"]))
        if row_date in values_by_date:
            row["nav"] = values_by_date[row_date]
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    conflicts[3] = reconcile.ErsteConflict(
        calendar_date=conflicts[3].calendar_date,
        value_a=Decimal(13),
        value_b=Decimal(13),
        raw_detail=conflicts[3].raw_detail,
    )

    history = reconcile.read_morningstar_history(evidence)
    report = reconcile.build_morningstar_reconciliation_payload(
        diagnostics_path=diagnostics,
        morningstar_path=evidence,
        conflicts=conflicts,
        diagnostics_sha256=digest,
        history=history,
    )

    comparisons = report["comparisons"]
    assert comparisons[0]["classification"] == "MATCH_ERSTE_VALUE_A"
    assert comparisons[1]["classification"] == "MATCH_ERSTE_VALUE_B"
    assert comparisons[2]["classification"] == "MATCH_NEITHER"
    assert comparisons[3]["classification"] == "MATCH_BOTH"
    assert comparisons[4]["classification"] == "MATCH_NEITHER"
    assert comparisons[0]["morningstar_observation"] == {"date": "2005-03-02", "nav": "10.0"}
    assert report["summary_counts"] == {
        "MATCH_BOTH": 1,
        "MATCH_ERSTE_VALUE_A": 1,
        "MATCH_ERSTE_VALUE_B": 1,
        "MATCH_NEITHER": 25,
    }
    assert report["morningstar_provenance"]["observations"] == 1772
    assert report["morningstar_provenance"]["conflict_interval_fully_covered"] is True
    assert report["usable_for_backtest"] is False
    assert report["reconciliation_status"] == "RECONCILIATION_REQUIRED"
    assert report["deterministic_reconciliation_rule_accepted"] is False


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda payload: payload[0].__setitem__("queryKey", "WRONG"), "queryKey"),
        (lambda payload: payload[0]["series"].pop(), "observation count"),
        (lambda payload: payload[0]["series"].append(payload[0]["series"][1]), "count"),
        (lambda payload: payload[0]["series"][10].__setitem__("nav", 0), "non-positive"),
    ],
)
def test_morningstar_evidence_validation_fails_closed(
    tmp_path: Path, mutator: object, message: str
) -> None:
    payload = _morningstar_payload()
    assert callable(mutator)
    mutator(payload)
    evidence = tmp_path / "invalid-morningstar.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(reconcile.ReconciliationError, match=message):
        reconcile.read_morningstar_history(evidence)


def test_offline_morningstar_reconciliation_does_not_call_oekb(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    diagnostics = _write_diagnostics(tmp_path)
    conflicts, _ = reconcile.read_erste_conflicts(diagnostics)
    evidence = _write_morningstar_evidence(tmp_path, conflicts=conflicts)

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("Network acquisition must not run")

    monkeypatch.setattr(reconcile, "fetch_oekb_history", fail_if_called)
    first = reconcile.run_morningstar_reconciliation(diagnostics, evidence)
    second = reconcile.run_morningstar_reconciliation(diagnostics, evidence)

    assert first == second
    assert first["status"] == "EVIDENCE_GENERATED_NOT_ACCEPTED"
    assert first["summary_counts"]["MATCH_NEITHER"] == 28


def test_morningstar_failure_payload_remains_reconciliation_required(tmp_path: Path) -> None:
    payload = reconcile.build_morningstar_failure_payload(
        _write_diagnostics(tmp_path), tmp_path / "missing.json", "missing evidence"
    )

    assert payload["status"] == "SOURCE_ERROR"
    assert payload["reconciliation_status"] == "RECONCILIATION_REQUIRED"
    assert payload["usable_for_backtest"] is False
