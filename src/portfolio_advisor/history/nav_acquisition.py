"""Offline planning helpers for approved historical-NAV acquisition.

The planner identifies only constituents of strict-complete windows whose
portfolio-level labels are unavailable because no local portfolio NAV was
persisted.  It deliberately makes no provider request and does not infer a
portfolio value from constituent prices.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
)
from portfolio_advisor.history.official_nav_store import OfficialNavStore

NO_LOCAL_HISTORY = "NO_LOCAL_HISTORY"
EXCLUDED_ISINS = frozenset({"HU0000554795", "AT0000605324"})
PROVIDER_PRIORITY = {"erste_market": 1, "oekb": 2, "morningstar": 3}


class HistoricalNavAcquisitionError(RuntimeError):
    """A target inventory cannot safely be reconciled to strict evidence."""


@dataclass(frozen=True, slots=True)
class HistoricalNavAcquisitionTarget:
    isin: str
    instrument_name: str | None
    currency: str | None
    required_start_date: str
    required_end_date: str
    affected_portfolios: tuple[str, ...]
    affected_decision_dates: tuple[str, ...]
    affected_horizons: tuple[int, ...]
    recoverable_label_count: int
    current_source_status: str
    preferred_existing_provider: str
    acquisition_status: str = "NOT_ACQUIRED"

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        for name in ("affected_portfolios", "affected_decision_dates", "affected_horizons"):
            value[name] = list(value[name])
        return value


@dataclass(slots=True)
class _TargetAccumulator:
    """Typed, deterministic aggregation state for one target ISIN."""

    products: set[str]
    currencies: set[str]
    starts: list[str]
    ends: list[str]
    portfolios: set[str]
    dates: set[str]
    horizons: set[int]
    keys: set[tuple[str, str, int]]
    providers: set[str]


def build_historical_nav_acquisition_targets(
    *,
    label_store_path: Path,
    coverage_path: Path,
    database_path: Path,
    history_store_path: Path | None = None,
) -> dict[str, object]:
    """Build a deterministic, no-network target list from strict-complete labels."""
    labels = _load_csv(label_store_path)
    coverage = _coverage_index(coverage_path)
    repository = ModelPortfolioRepository(database_path)
    holdings_cache: dict[str, list[HoldingObservation]] = {}
    targets: dict[str, _TargetAccumulator] = {}
    excluded: set[str] = set()

    for label in labels:
        if label.get("label_status") != NO_LOCAL_HISTORY:
            continue
        decision_date = _parse_date(label.get("decision_date"), "decision_date")
        portfolio_name = _text(label.get("portfolio_name"), "portfolio_name")
        horizon = _positive_int(label.get("horizon_days"), "horizon_days")
        key = (decision_date.isoformat(), portfolio_name, horizon)
        window = coverage.get(key)
        if window is None or window.get("status") != "COMPLETE":
            raise HistoricalNavAcquisitionError("NO_LOCAL_HISTORY label lacks a strict-complete coverage window")
        providers = window.get("source_used_by_isin")
        if not isinstance(providers, dict) or not providers:
            raise HistoricalNavAcquisitionError("strict-complete coverage window lacks source provenance")
        holdings = holdings_cache.setdefault(
            decision_date.isoformat(), repository.load_holdings(decision_date)
        )
        by_isin = {
            str(item.isin).upper(): item
            for item in holdings
            if item.portfolio_name == portfolio_name and item.isin is not None
        }
        if set(by_isin) != set(providers):
            raise HistoricalNavAcquisitionError("coverage constituents do not reconcile to point-in-time holdings")
        for isin, provider in providers.items():
            if not isinstance(isin, str) or not isinstance(provider, str):
                raise HistoricalNavAcquisitionError("coverage provider provenance is malformed")
            if isin in EXCLUDED_ISINS:
                excluded.add(isin)
                continue
            holding = by_isin[isin]
            target = targets.setdefault(
                isin,
                _TargetAccumulator(set(), set(), [], [], set(), set(), set(), set(), set()),
            )
            if holding.product:
                target.products.add(holding.product)
            if holding.currency:
                target.currencies.add(holding.currency)
            target.starts.append(_text(label.get("label_start_date"), "label_start_date"))
            target.ends.append(_text(label.get("label_end_date"), "label_end_date"))
            target.portfolios.add(portfolio_name)
            target.dates.add(decision_date.isoformat())
            target.horizons.add(horizon)
            target.keys.add(key)
            target.providers.add(provider)

    records = [_target_record(isin, value) for isin, value in targets.items()]
    if history_store_path is not None:
        store = OfficialNavStore(history_store_path)
        records = [_with_acquisition_status(record, store) for record in records]
    records.sort(key=lambda item: (-item.recoverable_label_count, item.isin))
    return {
        "schema_version": 1,
        "status": "HISTORICAL_NAV_ACQUISITION_TARGETS_VALIDATED",
        "target_basis": "strict-complete NO_LOCAL_HISTORY labels only",
        "target_count": len(records),
        "potentially_recoverable_label_count": sum(item.recoverable_label_count for item in records),
        "excluded_special_isins": sorted(excluded | EXCLUDED_ISINS),
        "targets": [item.as_dict() for item in records],
        "no_synthesis": [
            "no_interpolation", "no_fill", "no_nearest_date", "no_proxy", "no_portfolio_nav_inference",
        ],
    }


def _with_acquisition_status(
    record: HistoricalNavAcquisitionTarget, store: OfficialNavStore
) -> HistoricalNavAcquisitionTarget:
    coverage = store.coverage(record.isin, record.preferred_existing_provider)
    if coverage is None:
        return record
    exact = (
        coverage.first_observation.isoformat() == record.required_start_date
        and coverage.last_observation.isoformat() == record.required_end_date
    )
    return replace(
        record,
        acquisition_status="ACQUIRED_VALIDATED" if exact else "PARTIAL_HISTORY",
    )


def _target_record(isin: str, value: _TargetAccumulator) -> HistoricalNavAcquisitionTarget:
    if len(value.currencies) > 1:
        raise HistoricalNavAcquisitionError(f"target {isin} has conflicting point-in-time currencies")
    products_text = tuple(sorted(value.products))
    return HistoricalNavAcquisitionTarget(
        isin=isin,
        instrument_name=products_text[0] if len(products_text) == 1 else None,
        currency=next(iter(value.currencies), None),
        required_start_date=min(_string_items(value.starts, "starts")),
        required_end_date=max(_string_items(value.ends, "ends")),
        affected_portfolios=tuple(sorted(_string_items(value.portfolios, "portfolios"))),
        affected_decision_dates=tuple(sorted(_string_items(value.dates, "dates"))),
        affected_horizons=tuple(sorted(_int_items(value.horizons, "horizons"))),
        recoverable_label_count=len(_tuple_items(value.keys, "keys")),
        current_source_status=(
            "STRICT_COMPLETE_SOURCE_COVERAGE_NOT_PERSISTED:"
            + ",".join(sorted(value.providers))
        ),
        preferred_existing_provider=min(
            value.providers, key=lambda item: (PROVIDER_PRIORITY.get(item, 99), item)
        ),
    )


def write_historical_nav_acquisition_targets(path: Path, payload: dict[str, object]) -> None:
    _write_json_atomic(path, payload)


def _coverage_index(path: Path) -> dict[tuple[str, str, int], dict[str, object]]:
    payload = _load_json(path, "backtest coverage")
    raw_windows = payload.get("windows")
    if not isinstance(raw_windows, list):
        raise HistoricalNavAcquisitionError("backtest coverage has no windows")
    result: dict[tuple[str, str, int], dict[str, object]] = {}
    for raw in raw_windows:
        if not isinstance(raw, dict):
            raise HistoricalNavAcquisitionError("backtest coverage has a malformed window")
        key = (
            _text(raw.get("observation_date"), "coverage observation_date"),
            _text(raw.get("portfolio_name"), "coverage portfolio_name"),
            _positive_int(raw.get("horizon"), "coverage horizon"),
        )
        if key in result:
            raise HistoricalNavAcquisitionError("backtest coverage has a duplicate window")
        result[key] = raw
    return result


def _load_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise HistoricalNavAcquisitionError(f"cannot read label store: {path}") from exc
    if not rows or rows[0] is None:
        raise HistoricalNavAcquisitionError("label store has no rows")
    return [row for row in rows if row is not None]


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoricalNavAcquisitionError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise HistoricalNavAcquisitionError(f"{label} must be an object")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _parse_date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(_text(value, label))
    except ValueError as exc:
        raise HistoricalNavAcquisitionError(f"{label} is not an ISO date") from exc


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalNavAcquisitionError(f"{label} must be a non-empty string")
    return value.strip()


def _positive_int(value: object, label: str) -> int:
    try:
        parsed = int(_text(value, label)) if isinstance(value, str) else value
    except ValueError as exc:
        raise HistoricalNavAcquisitionError(f"{label} is not an integer") from exc
    if isinstance(parsed, bool) or not isinstance(parsed, int) or parsed <= 0:
        raise HistoricalNavAcquisitionError(f"{label} must be positive")
    return parsed


def _string_items(value: object, label: str) -> set[str]:
    if not isinstance(value, (set, list)) or any(not isinstance(item, str) for item in value):
        raise HistoricalNavAcquisitionError(f"target {label} is malformed")
    return set(value)


def _int_items(value: object, label: str) -> set[int]:
    if not isinstance(value, set) or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise HistoricalNavAcquisitionError(f"target {label} is malformed")
    return value


def _tuple_items(value: object, label: str) -> set[tuple[str, str, int]]:
    if not isinstance(value, set) or any(not isinstance(item, tuple) or len(item) != 3 for item in value):
        raise HistoricalNavAcquisitionError(f"target {label} is malformed")
    return value
