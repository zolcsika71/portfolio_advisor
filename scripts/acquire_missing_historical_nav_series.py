"""Acquire the next bounded batch of approved constituent NAV histories.

This is the only network-capable constituent-history path. It persists
validated provider evidence locally but never builds a portfolio NAV or calls
the backtester.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import requests

from portfolio_advisor.history.nav_acquisition import EXCLUDED_ISINS
from portfolio_advisor.history.oekb import (
    OEK_B_PLATFORM_CONTEXT,
    OekbHttpResponse,
    fetch_bounded_oekb_history,
)
from portfolio_advisor.history.official_nav_store import (
    OfficialNavObservation,
    OfficialNavStore,
    OfficialNavStoreError,
)

VALIDATED = "VALIDATED"
PARTIAL_HISTORY = "PARTIAL_HISTORY"
EMPTY_HISTORY = "EMPTY_HISTORY"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


def _load_targets(path: Path) -> list[dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    targets = value.get("targets") if isinstance(value, dict) else None
    if not isinstance(targets, list):
        raise TypeError("target inventory has no targets")
    return [item for item in targets if isinstance(item, dict)]


def _target_interval(target: dict[str, object]) -> tuple[date, date]:
    return (
        date.fromisoformat(str(target["required_start_date"])),
        date.fromisoformat(str(target["required_end_date"])),
    )


def _target_provider(target: dict[str, object]) -> str:
    provider = target.get("preferred_existing_provider")
    if not isinstance(provider, str) or not provider:
        raise OfficialNavStoreError("target has no approved provider")
    return provider


def _retained_status(store: OfficialNavStore, target: dict[str, object]) -> dict[str, object]:
    isin = str(target["isin"])
    provider = _target_provider(target)
    start, end = _target_interval(target)
    coverage = store.coverage(isin, provider)
    if coverage is None:
        return {"status": "NOT_ACQUIRED"}
    exact = coverage.first_observation == start and coverage.last_observation == end
    return {
        "status": "ACQUIRED_VALIDATED" if exact else PARTIAL_HISTORY,
        "observation_count": coverage.observation_count,
        "first_observation": coverage.first_observation.isoformat(),
        "last_observation": coverage.last_observation.isoformat(),
        "coverage_status": "EXACT_INTERVAL_BOUNDARIES" if exact else "PARTIAL_INTERVAL_BOUNDARIES",
    }


def select_continuation_targets(
    targets: list[dict[str, object]], store: OfficialNavStore, limit: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Deterministically skip retained exact intervals and select the next batch."""
    if limit <= 0:
        raise ValueError("limit must be positive for bounded continuation acquisition")
    remaining: list[dict[str, object]] = []
    acquired: list[dict[str, object]] = []
    for target in targets:
        isin = str(target.get("isin", ""))
        if isin in EXCLUDED_ISINS:
            continue
        current_status = str(target.get("current_source_status", ""))
        if "TERMINAL" in current_status or "UNUSABLE" in current_status:
            continue
        retained = _retained_status(store, target)
        if retained["status"] == "ACQUIRED_VALIDATED":
            acquired.append({**target, "retained_status": retained})
        else:
            remaining.append(target)
    remaining.sort(key=lambda item: (-int(item["recoverable_label_count"]), str(item["isin"])))
    acquired.sort(key=lambda item: str(item["isin"]))
    return remaining[:limit], acquired


def _oekb_get(url: str, timeout: int) -> OekbHttpResponse:
    response = requests.get(
        url,
        headers={"Accept": "application/json", "OeKB-Platform-Context": OEK_B_PLATFORM_CONTEXT},
        timeout=timeout,
    )
    return OekbHttpResponse(response.status_code, response.content)


def _result_metadata(
    *,
    target: dict[str, object],
    provider: str,
    reference: str,
    observations: tuple[OfficialNavObservation, ...],
) -> dict[str, object]:
    start, end = _target_interval(target)
    if not observations:
        return {
            "provider": provider,
            "status": EMPTY_HISTORY,
            "raw_reference": reference,
            "observation_count": 0,
            "requested_range": [start.isoformat(), end.isoformat()],
            "actual_range": None,
            "coverage_status": EMPTY_HISTORY,
        }
    first, last = observations[0].observation_date, observations[-1].observation_date
    exact = first == start and last == end
    return {
        "provider": provider,
        "status": VALIDATED if exact else PARTIAL_HISTORY,
        "raw_reference": reference,
        "observation_count": len(observations),
        "requested_range": [start.isoformat(), end.isoformat()],
        "actual_range": [first.isoformat(), last.isoformat()],
        "coverage_status": "EXACT_INTERVAL_BOUNDARIES" if exact else "PARTIAL_INTERVAL_BOUNDARIES",
    }


def _oekb(target: dict[str, object]) -> tuple[tuple[OfficialNavObservation, ...], dict[str, object]]:
    isin = str(target["isin"])
    start, end = _target_interval(target)
    history = fetch_bounded_oekb_history(
        isin=isin, date_from=start, date_to=end, limit=100, timeout=30, http_get=_oekb_get
    )
    if history.returned_isin != isin or history.currency != target.get("currency"):
        raise OfficialNavStoreError("OeKB returned an unexpected identity or currency")
    reference = f"data/raw/official_nav/oekb/{isin}.json"
    _write_json_atomic(
        Path(reference),
        {
            "provider": "oekb",
            "isin": isin,
            "requested_range": [start.isoformat(), end.isoformat()],
            "currency": history.currency,
            "validation_status": VALIDATED,
            "chunks": [item.as_dict() for item in history.chunks],
            "observations": [
                {"date": item.calendar_date.isoformat(), "value": str(item.calculated_value)}
                for item in history.observations
            ],
        },
    )
    observations = tuple(
        OfficialNavObservation(
            isin,
            item.calendar_date,
            float(item.calculated_value),
            item.currency,
            "NAV",
            "oekb",
            isin,
            reference,
        )
        for item in history.observations
    )
    return observations, _result_metadata(
        target=target, provider="oekb", reference=reference, observations=observations
    )


def _erste(target: dict[str, object]) -> tuple[tuple[OfficialNavObservation, ...], dict[str, object]]:
    spec = importlib.util.spec_from_file_location(
        "portfolio_advisor_erste_acquisition", Path(__file__).with_name("validate_erste_mapping.py")
    )
    if spec is None or spec.loader is None:
        raise OfficialNavStoreError("existing Erste acquisition adapter cannot be loaded")
    erste = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = erste
    spec.loader.exec_module(erste)
    isin = str(target["isin"])
    expected_currency = str(target["currency"])
    validation = erste.validate_isin(isin, expected_currency)
    if validation.status not in {"PASS", "PASS_WITH_FILTERED_SENTINEL"} or not validation.usable_for_backtest:
        raise OfficialNavStoreError(f"Erste validation failed: {validation.status}")
    if validation.instrument_id is None:
        raise OfficialNavStoreError("Erste validation lacks an instrument identifier")
    chart = erste.fetch_chart(isin, validation.instrument_id)
    if str(chart.get("isin", "")).upper() != isin:
        raise OfficialNavStoreError("Erste chart ISIN mismatch")
    start, end = _target_interval(target)
    series = chart.get("series")
    if not isinstance(series, list):
        raise OfficialNavStoreError("Erste chart lacks a series")
    selected: list[tuple[date, float]] = []
    for row in series:
        if not isinstance(row, list) or len(row) != 2:
            raise OfficialNavStoreError("Erste chart row is malformed")
        observed = date.fromisoformat(erste.timestamp_to_date(int(row[0])))
        value = float(row[1])
        if start <= observed <= end:
            selected.append((observed, value))
    reference = f"data/raw/official_nav/erste_market/{isin}.json"
    _write_json_atomic(
        Path(reference),
        {
            "provider": "erste_market",
            "isin": isin,
            "instrument_id": validation.instrument_id,
            "requested_range": [start.isoformat(), end.isoformat()],
            "currency": expected_currency,
            "validation_status": VALIDATED,
            "observations": [{"date": item[0].isoformat(), "value": item[1]} for item in selected],
        },
    )
    observations = tuple(
        OfficialNavObservation(
            isin,
            observed,
            value,
            expected_currency,
            "NAV",
            "erste_market",
            validation.instrument_id,
            reference,
        )
        for observed, value in selected
    )
    return observations, _result_metadata(
        target=target, provider="erste_market", reference=reference, observations=observations
    )


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _source_usage(results: list[dict[str, object]]) -> dict[str, int]:
    return {
        provider: sum(item.get("provider") == provider for item in results)
        for provider in sorted({str(item.get("provider")) for item in results})
    }


def acquisition_marginal_value(
    results: list[dict[str, object]],
    *,
    remaining_target_count: int,
    previous_payload: dict[str, object] | None,
) -> dict[str, object]:
    """Return operational evidence coverage only; never a ranking feature."""
    exact_acquired = [
        item
        for item in results
        if item.get("status") == VALIDATED
        and isinstance(item.get("new_observations_persisted"), int)
        and int(item["new_observations_persisted"]) > 0
    ]
    partial_acquired = [
        item
        for item in results
        if item.get("status") == PARTIAL_HISTORY
        and isinstance(item.get("new_observations_persisted"), int)
        and int(item["new_observations_persisted"]) > 0
    ]
    observations = sum(
        int(item["new_observations_persisted"])
        for item in [*exact_acquired, *partial_acquired]
    )
    incidences = sum(
        int(item.get("estimated_affected_strict_eligible_windows", 0))
        for item in exact_acquired
    )
    partial_incidences = sum(
        int(item.get("estimated_affected_strict_eligible_windows", 0))
        for item in partial_acquired
    )
    previous_observations = 0
    if previous_payload is not None:
        previous_observations = int(previous_payload.get("new_observations_persisted", 0))
    return {
        "new_targets_acquired": len(exact_acquired),
        "new_partial_targets": len(partial_acquired),
        "new_observations": observations,
        "new_constituent_window_incidences_covered": incidences,
        "partial_constituent_window_incidences": partial_incidences,
        "remaining_target_count": remaining_target_count,
        "remaining_high_impact_target_count": min(5, remaining_target_count),
        "comparison_to_previous_batch": {
            "previous_new_observations": previous_observations,
            "observation_delta": observations - previous_observations,
        },
    }


def cumulative_constituent_coverage(
    targets: list[dict[str, object]], store: OfficialNavStore
) -> dict[str, object]:
    """Summarize persisted constituent evidence without interpreting portfolio returns."""
    status_counts: dict[str, int] = {}
    exact_incidence = 0
    partial_incidence = 0
    unresolved: list[dict[str, object]] = []
    total_incidence = sum(int(item["recoverable_label_count"]) for item in targets)
    for target in targets:
        retained = _retained_status(store, target)
        status = str(retained["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        impact = int(target["recoverable_label_count"])
        if status == "ACQUIRED_VALIDATED":
            exact_incidence += impact
        elif status == PARTIAL_HISTORY:
            partial_incidence += impact
            unresolved.append(target)
        else:
            unresolved.append(target)
    unresolved.sort(key=lambda item: (-int(item["recoverable_label_count"]), str(item["isin"])))
    return {
        "target_status_counts": dict(sorted(status_counts.items())),
        "recoverable_constituent_window_incidences": total_incidence,
        "exact_constituent_window_incidences": exact_incidence,
        "partial_constituent_window_incidences": partial_incidence,
        "exact_coverage_fraction": round(exact_incidence / total_incidence, 12)
        if total_incidence
        else 0.0,
        "remaining_unresolved_targets": len(unresolved),
        "remaining_impact_distribution": {
            "total_incidences": sum(int(item["recoverable_label_count"]) for item in unresolved),
            "highest": int(unresolved[0]["recoverable_label_count"]) if unresolved else 0,
            "top_targets": [
                {
                    "isin": item["isin"],
                    "recoverable_label_count": item["recoverable_label_count"],
                    "acquisition_status": _retained_status(store, item)["status"],
                }
                for item in unresolved[:5]
            ],
        },
    }


def _load_previous_payload(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, default=Path("data/audit/historical_nav_acquisition_targets.json"))
    parser.add_argument("--store", type=Path, default=Path("database/official_historical_nav.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/historical_nav_acquisition_results.json"))
    parser.add_argument("--limit", type=int, default=3, help="Next unacquired targets to attempt.")
    parser.add_argument(
        "--summarize-existing",
        action="store_true",
        help="Refresh marginal accounting from an existing result artifact without network access.",
    )
    args = parser.parse_args(argv)
    previous_payload = _load_previous_payload(args.output)
    targets = _load_targets(args.targets)
    store = OfficialNavStore(args.store)
    if args.summarize_existing:
        if previous_payload is None:
            raise RuntimeError("cannot summarize a missing acquisition result artifact")
        existing_results = previous_payload.get("results")
        if not isinstance(existing_results, list) or not all(
            isinstance(item, dict) for item in existing_results
        ):
            raise RuntimeError("existing acquisition result artifact is malformed")
        remaining, _ = select_continuation_targets(targets, store, len(targets))
        target_by_isin = {str(item["isin"]): item for item in targets}
        corrected_results: list[dict[str, object]] = []
        for raw_result in existing_results:
            result = dict(raw_result)
            target = target_by_isin.get(str(result.get("isin", "")))
            if target is None:
                raise RuntimeError("existing result ISIN is absent from the target inventory")
            prior_status = target.get("acquisition_status")
            if prior_status in {"ACQUIRED_VALIDATED", PARTIAL_HISTORY}:
                result["new_observations_persisted"] = 0
            corrected_results.append(result)
        previous_observations = (
            previous_payload.get("marginal_acquisition_value", {})
            if isinstance(previous_payload.get("marginal_acquisition_value"), dict)
            else {}
        )
        comparison = previous_observations.get("comparison_to_previous_batch", {})
        prior = {
            "new_observations_persisted": comparison.get("previous_new_observations", 0)
        } if isinstance(comparison, dict) else None
        previous_payload["schema_version"] = 3
        previous_payload["marginal_acquisition_value"] = acquisition_marginal_value(
            corrected_results,
            remaining_target_count=len(remaining),
            previous_payload=prior,
        )
        previous_payload["results"] = corrected_results
        previous_payload["new_observations_persisted"] = sum(
            int(item.get("new_observations_persisted", 0)) for item in corrected_results
        )
        previous_payload["cumulative_constituent_coverage"] = cumulative_constituent_coverage(
            targets, store
        )
        _write_json_atomic(args.output, previous_payload)
        print(f"Historical NAV acquisition summary refreshed: {args.output}")
        return 0
    selected, already_acquired = select_continuation_targets(targets, store, args.limit)
    results: list[dict[str, object]] = []
    newly_persisted = 0
    for target in selected:
        try:
            provider = _target_provider(target)
            observations, result = (
                _oekb(target)
                if provider == "oekb"
                else _erste(target)
                if provider == "erste_market"
                else ((), {"provider": provider, "status": SOURCE_UNAVAILABLE, "reason": "NO_APPROVED_PROVIDER_PATH"})
            )
            inserted_for_target = store.persist(observations)
            newly_persisted += inserted_for_target
            reference = result.get("raw_reference")
            result.update(
                {
                    "isin": target.get("isin"),
                    "response_fingerprint": hashlib.sha256(Path(str(reference)).read_bytes()).hexdigest() if reference else None,
                    "new_observations_persisted": inserted_for_target,
                    "estimated_affected_strict_eligible_windows": target.get("recoverable_label_count"),
                }
            )
        except (OSError, ValueError, ImportError, requests.RequestException, OfficialNavStoreError, RuntimeError) as exc:
            result = {
                "isin": target.get("isin"),
                "provider": target.get("preferred_existing_provider"),
                "status": SOURCE_UNAVAILABLE,
                "error": str(exc),
                "new_observations_persisted": 0,
            }
        results.append(result)
    summary = store.summary()
    successful = sum(item["status"] == VALIDATED for item in results)
    partial = sum(item["status"] == PARTIAL_HISTORY for item in results)
    remaining, _ = select_continuation_targets(targets, store, len(targets))
    marginal_value = acquisition_marginal_value(
        results,
        remaining_target_count=len(remaining),
        previous_payload=previous_payload,
    )
    cumulative_coverage = cumulative_constituent_coverage(targets, store)
    payload = {
        "schema_version": 3,
        "validation_status": "HISTORICAL_NAV_ACQUISITION_CONTINUATION_PARTIAL",
        "targets_total": len(targets),
        "targets_already_acquired": len(already_acquired),
        "targets_attempted_this_run": len(selected),
        "successful_this_run": successful,
        "partial_this_run": partial,
        "failed_this_run": len(selected) - successful - partial,
        "new_observations_persisted": newly_persisted,
        "cumulative_acquired_targets": summary.acquired_isin_count,
        "cumulative_observations": summary.observation_count,
        "provider_usage": dict(summary.provider_observation_counts),
        "providers_used_this_run": _source_usage(results),
        "remaining_targets": len(remaining),
        "marginal_acquisition_value": marginal_value,
        "cumulative_constituent_coverage": cumulative_coverage,
        "remaining_high_impact_targets": [
            {
                "isin": item["isin"],
                "recoverable_label_count": item["recoverable_label_count"],
                "preferred_existing_provider": item["preferred_existing_provider"],
            }
            for item in remaining[:5]
        ],
        "already_acquired": [
            {"isin": item["isin"], "retained_status": item["retained_status"]}
            for item in already_acquired
        ],
        "results": results,
        "portfolio_label_recovery": "NOT_COMPUTED: asset NAVs are not portfolio NAVs and no approved portfolio aggregation methodology exists",
    }
    _write_json_atomic(args.output, payload)
    print(
        "Historical NAV acquisition continuation: "
        f"{payload['validation_status']}; attempted={len(selected)} "
        f"successful={successful} new_observations={newly_persisted}"
    )
    print(f"JSON output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
