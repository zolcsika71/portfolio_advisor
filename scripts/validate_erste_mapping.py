#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from math import isfinite
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ERSTE_BASE_URL = "https://www.erstemarket.hu"
FUND_PAGE_URL = ERSTE_BASE_URL + "/befektetesi_alapok/alap/{isin}"
AUTOCOMPLETE_URL = ERSTE_BASE_URL + "/autocomplete/Fund/{isin}?style=full&maxRows=100"
CHART_URL = ERSTE_BASE_URL + "/funds/chart/{instrument_id}"
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
CONFIRMED_SOURCE_SENTINEL_ISINS = frozenset({"IE00B7KFL990", "IE00B84J9L26"})
SOURCE_SENTINEL_TIMESTAMP = 68_400_000
SOURCE_SENTINEL_DATE = "1970-01-01"
SOURCE_SENTINEL_FIRST_VALID_TIMESTAMP = 1_354_302_000_000
PRIMARY_SOURCE_NAME = "erste_market"
PRIMARY_SOURCE_PRIORITY = 1
SECONDARY_SOURCE_PRIORITY = 2
PRIMARY_USABLE_STATUSES = frozenset({"PASS", "PASS_WITH_FILTERED_SENTINEL"})


@dataclass(frozen=True)
class IsinSource:
    table: str
    isin_column: str
    currency_column: str | None = None


@dataclass(frozen=True)
class ResolutionResult:
    instrument_id: str | None
    method: str
    status: str
    detail: str | None = None
    attempts: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    isin: str
    currency: str | None
    instrument_id: str | None
    returned_isin: str | None
    resolution_method: str | None
    observations: int
    unique_timestamps: int
    duplicates: int
    chronological: bool
    non_positive_values: int
    first_date: str | None
    last_date: str | None
    status: str
    error: str | None = None
    resolution_attempts: tuple[dict[str, str], ...] = ()
    anomaly_details: tuple[dict[str, object], ...] = ()
    usable_for_backtest: bool = False
    normalized_observations: int = 0
    filtered_observations: int = 0
    normalized_first_date: str | None = None
    normalized_last_date: str | None = None
    normalization_actions: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class SourceSentinelNormalization:
    """A narrowly recognized raw source sentinel and its filtered view."""

    normalized: tuple[tuple[int, float], ...]
    anomaly_detail: dict[str, object]
    action: dict[str, object]


@dataclass(frozen=True)
class SourceProvenance:
    """Identity and retrieval evidence for one immutable source response."""

    source_name: str
    source_priority: int
    source_identifier: str | None
    endpoint_metadata: dict[str, str]
    retrieved_at: str


@dataclass(frozen=True)
class SourceHistoryValidation:
    """Source-neutral validation outcome used by fallback orchestration.

    The raw observations remain owned by the source-specific acquisition
    layer.  This record carries only validation facts and provenance needed to
    select, reject, or document a source.
    """

    isin: str
    currency: str | None
    status: str
    usable_for_backtest: bool
    provenance: SourceProvenance
    raw_observation_count: int
    normalized_observation_count: int
    filtered_observation_count: int
    date_range: tuple[str | None, str | None]
    normalized_date_range: tuple[str | None, str | None]
    warnings: tuple[str, ...] = ()
    anomalies: tuple[dict[str, object], ...] = ()


class HistoricalNavSource(Protocol):
    """A source-specific resolver, fetcher, and validator boundary.

    Implementations must return a fail-closed `SourceHistoryValidation` and
    preserve raw source observations outside this orchestration layer.
    """

    source_name: str
    source_priority: int

    def validate_history(
        self, isin: str, currency: str | None
    ) -> SourceHistoryValidation: ...


@dataclass(frozen=True)
class SecondaryHistoryPayload:
    """Raw secondary-source payload supplied by an approved adapter only."""

    isin: str
    currency: str | None
    observations: tuple[tuple[int, float], ...]
    provenance: SourceProvenance


def utc_retrieval_timestamp() -> str:
    return datetime.now(UTC).isoformat()


class ErsteFundPageParser(HTMLParser):
    """Extract the chart container's instrument-id from an Erste fund page."""

    def __init__(self) -> None:
        super().__init__()
        self.instrument_ids: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        instrument_id = attributes.get("instrument-id")

        if (
            "simpleChartContainer" in classes
            and instrument_id
            and instrument_id.isdigit()
        ):
            self.instrument_ids.append(instrument_id)


def error(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def http_get(
    url: str,
    *,
    referer: str | None = None,
    timeout: int = 20,
) -> bytes:
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 PortfolioAdvisor-ErsteMarketValidation/1.1",
    }

    if referer:
        headers["Referer"] = referer
        headers["X-Requested-With"] = "XMLHttpRequest"

    request = Request(url, headers=headers, method="GET")

    with urlopen(request, timeout=timeout) as response:
        return response.read()


def list_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    escaped_table = table.replace('"', '""')
    rows = connection.execute(f'PRAGMA table_info("{escaped_table}")').fetchall()
    return [str(row[1]) for row in rows]


def discover_isin_source(connection: sqlite3.Connection) -> IsinSource:
    candidates: list[tuple[int, IsinSource]] = []
    currency_names = {"currency", "currency_code", "ccy", "devizanem", "deviza"}

    for table in list_tables(connection):
        columns = table_columns(connection, table)
        isin_columns = [column for column in columns if "isin" in column.lower()]

        for isin_column in isin_columns:
            escaped_table = table.replace('"', '""')
            escaped_column = isin_column.replace('"', '""')
            rows = connection.execute(
                f'''
                SELECT DISTINCT "{escaped_column}"
                FROM "{escaped_table}"
                WHERE "{escaped_column}" IS NOT NULL
                '''
            ).fetchall()

            valid_isins = {
                str(row[0]).strip().upper()
                for row in rows
                if ISIN_PATTERN.fullmatch(str(row[0]).strip().upper())
            }
            if not valid_isins:
                continue

            currency_column = next(
                (column for column in columns if column.lower() in currency_names),
                None,
            )
            candidates.append(
                (
                    len(valid_isins),
                    IsinSource(
                        table=table,
                        isin_column=isin_column,
                        currency_column=currency_column,
                    ),
                )
            )

    if not candidates:
        raise RuntimeError(
            "Could not find a column containing valid ISINs in the SQLite database."
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def load_sample_isins(
    connection: sqlite3.Connection,
    source: IsinSource,
    limit: int,
) -> list[tuple[str, str | None]]:
    table = source.table.replace('"', '""')
    isin_column = source.isin_column.replace('"', '""')

    if source.currency_column:
        currency_column = source.currency_column.replace('"', '""')
        rows = connection.execute(
            f'''
            SELECT DISTINCT "{isin_column}", "{currency_column}"
            FROM "{table}"
            WHERE "{isin_column}" IS NOT NULL
            ORDER BY "{currency_column}", "{isin_column}"
            '''
        ).fetchall()
    else:
        rows = connection.execute(
            f'''
            SELECT DISTINCT "{isin_column}", NULL
            FROM "{table}"
            WHERE "{isin_column}" IS NOT NULL
            ORDER BY "{isin_column}"
            '''
        ).fetchall()

    valid: list[tuple[str, str | None]] = []
    for isin_raw, currency_raw in rows:
        isin = str(isin_raw).strip().upper()
        if not ISIN_PATTERN.fullmatch(isin):
            continue
        currency = (
            str(currency_raw).strip().upper()
            if currency_raw is not None
            else None
        )
        valid.append((isin, currency))

    if not source.currency_column:
        return valid[:limit]

    groups: dict[str | None, list[tuple[str, str | None]]] = {}
    for item in valid:
        groups.setdefault(item[1], []).append(item)

    result: list[tuple[str, str | None]] = []
    while len(result) < limit and any(groups.values()):
        for currency in sorted(groups, key=lambda value: str(value)):
            group = groups[currency]
            if group and len(result) < limit:
                result.append(group.pop(0))
    return result


def resolve_instrument_id_from_detail_page(isin: str) -> str | None:
    url = FUND_PAGE_URL.format(isin=isin)
    html = http_get(url).decode("utf-8", errors="replace")

    parser = ErsteFundPageParser()
    parser.feed(html)
    instrument_ids = sorted(set(parser.instrument_ids))

    if not instrument_ids:
        return None
    if len(instrument_ids) > 1:
        raise ValueError(
            "Multiple chart instrument-ids found on detail page: "
            f"{instrument_ids}"
        )
    return instrument_ids[0]


def resolve_instrument_id_from_autocomplete(isin: str) -> str | None:
    """Fallback resolver using Erste's autocomplete endpoint with exact ISIN match."""
    url = AUTOCOMPLETE_URL.format(isin=quote(isin, safe=""))
    referer = ERSTE_BASE_URL + "/befektetesi_alapok/osszehasonlitas"
    payload = http_get(url, referer=referer)

    try:
        data = json.loads(payload.decode("utf-8", errors="strict"))
    except json.JSONDecodeError as exc:
        raise ValueError("Autocomplete response is not valid JSON") from exc

    if not isinstance(data, list):
        raise TypeError("Autocomplete response is not a JSON list")

    exact_matches: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        returned_isin = str(item.get("isin", "")).strip().upper()
        instrument_id = str(item.get("id", "")).strip()
        if returned_isin == isin and instrument_id.isdigit():
            exact_matches.append(instrument_id)

    exact_matches = sorted(set(exact_matches))
    if not exact_matches:
        return None
    if len(exact_matches) > 1:
        raise ValueError(
            "Multiple exact autocomplete matches found: "
            f"{exact_matches}"
        )
    return exact_matches[0]


def resolve_instrument_id(isin: str) -> ResolutionResult:
    """Try detail-page resolution first, then exact-match autocomplete fallback."""
    detail_id = resolve_instrument_id_from_detail_page(isin)
    if detail_id is not None:
        return ResolutionResult(
            instrument_id=detail_id,
            method="detail_page",
            status="RESOLVED",
            attempts=(
                {
                    "path": "detail_page",
                    "outcome": "resolved",
                    "instrument_id": detail_id,
                },
            ),
        )

    autocomplete_id = resolve_instrument_id_from_autocomplete(isin)
    if autocomplete_id is not None:
        return ResolutionResult(
            instrument_id=autocomplete_id,
            method="autocomplete",
            status="RESOLVED",
            attempts=(
                {"path": "detail_page", "outcome": "no_instrument_id"},
                {
                    "path": "autocomplete",
                    "outcome": "resolved_exact_isin",
                    "instrument_id": autocomplete_id,
                },
            ),
        )

    return ResolutionResult(
        instrument_id=None,
        method="detail_page_then_autocomplete",
        status="NO_ERSTE_MAPPING",
        detail=(
            "No instrument-id on fund detail page and no exact ISIN match "
            "from Erste autocomplete."
        ),
        attempts=(
            {"path": "detail_page", "outcome": "no_instrument_id"},
            {"path": "autocomplete", "outcome": "no_exact_isin_match"},
        ),
    )


def fetch_chart(isin: str, instrument_id: str) -> dict[str, Any]:
    fund_url = FUND_PAGE_URL.format(isin=isin)
    chart_url = CHART_URL.format(instrument_id=instrument_id)
    payload = http_get(chart_url, referer=fund_url)

    try:
        data = json.loads(payload.decode("utf-8", errors="strict"))
    except json.JSONDecodeError as exc:
        raise ValueError("Chart response is not valid JSON") from exc

    if not isinstance(data, dict):
        raise TypeError("Chart response is not a JSON object")
    return data


def timestamp_to_date(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        .date()
        .isoformat()
    )


def observation_record(observation: tuple[int, float]) -> dict[str, object]:
    """Serialize one raw source observation without changing its value."""
    timestamp, value = observation
    return {
        "timestamp": timestamp,
        "date": timestamp_to_date(timestamp),
        "value": json_numeric(value),
    }


def json_numeric(value: float) -> float | str:
    """Keep non-finite source values observable while producing valid JSON."""
    return value if isfinite(value) else str(value)


def surrounding_observations(
    parsed: list[tuple[int, float]], index: int
) -> dict[str, object]:
    """Return the raw observation plus up to five source rows on each side."""
    return {
        "observation": observation_record(parsed[index]),
        "before": [
            observation_record(item) for item in parsed[max(0, index - 5) : index]
        ],
        "after": [
            observation_record(item) for item in parsed[index + 1 : index + 6]
        ],
    }


def invalid_nav_details(parsed: list[tuple[int, float]]) -> tuple[dict[str, object], ...]:
    """Describe every non-positive or non-finite NAV without repairing it."""
    details: list[dict[str, object]] = []
    for index, (_, nav) in enumerate(parsed):
        if isfinite(nav) and nav > 0.0:
            continue
        details.append(
            {
                "kind": "INVALID_NAV",
                "reason": "non_positive" if isfinite(nav) else "non_finite",
                **surrounding_observations(parsed, index),
            }
        )
    return tuple(details)


def conflicting_history_details(
    parsed: list[tuple[int, float]],
) -> tuple[dict[str, object], ...]:
    """Describe each duplicate timestamp with all source values retained."""
    indexes_by_timestamp: dict[int, list[int]] = {}
    for index, (timestamp, _) in enumerate(parsed):
        indexes_by_timestamp.setdefault(timestamp, []).append(index)

    details: list[dict[str, object]] = []
    for timestamp, indexes in indexes_by_timestamp.items():
        values = {parsed[index][1] for index in indexes}
        if len(values) <= 1:
            continue
        details.append(
            {
                "kind": "CONFLICTING_HISTORY",
                "timestamp": timestamp,
                "date": timestamp_to_date(timestamp),
                "values": [
                    json_numeric(value)
                    for value in sorted(
                        values,
                        key=lambda value: (not isfinite(value), str(value)),
                    )
                ],
                "occurrence_indexes": indexes,
                "surrounding_observations": surrounding_observations(parsed, indexes[0]),
            }
        )
    return tuple(details)


def classify_source_sentinel(
    isin: str, parsed: list[tuple[int, float]]
) -> SourceSentinelNormalization | None:
    """Recognize only the confirmed Erste epoch-era sentinel pattern.

    This is intentionally not a generic invalid-NAV repair: it is restricted
    to two confirmed ISINs, one known timestamp/date, exactly one non-positive
    source value, and a known substantially later positive series start.
    """
    if isin not in CONFIRMED_SOURCE_SENTINEL_ISINS:
        return None
    invalid_indexes = [
        index
        for index, (_, nav) in enumerate(parsed)
        if not isfinite(nav) or nav <= 0.0
    ]
    if len(invalid_indexes) != 1:
        return None
    sentinel_index = invalid_indexes[0]
    sentinel = parsed[sentinel_index]
    if (
        sentinel[0] != SOURCE_SENTINEL_TIMESTAMP
        or timestamp_to_date(sentinel[0]) != SOURCE_SENTINEL_DATE
        or not isfinite(sentinel[1])
        or sentinel[1] != 0.0
    ):
        return None
    normalized = tuple(item for index, item in enumerate(parsed) if index != sentinel_index)
    if not normalized or any(not isfinite(nav) or nav <= 0.0 for _, nav in normalized):
        return None
    if min(timestamp for timestamp, _ in normalized) < SOURCE_SENTINEL_FIRST_VALID_TIMESTAMP:
        return None
    raw_sentinel = surrounding_observations(parsed, sentinel_index)
    anomaly_detail: dict[str, object] = {
        "kind": "SOURCE_SENTINEL",
        "classification": "SOURCE_SENTINEL",
        "recognition_rule": {
            "confirmed_isin": isin,
            "timestamp": SOURCE_SENTINEL_TIMESTAMP,
            "date": SOURCE_SENTINEL_DATE,
            "non_positive_value": True,
            "confirmed_value": 0.0,
            "minimum_first_valid_timestamp": SOURCE_SENTINEL_FIRST_VALID_TIMESTAMP,
        },
        "original_raw_observation": raw_sentinel["observation"],
        "surrounding_observations": {
            "before": raw_sentinel["before"],
            "after": raw_sentinel["after"],
        },
    }
    action: dict[str, object] = {
        "classification": "SOURCE_SENTINEL",
        "action": "exclude_raw_sentinel_from_normalized_series",
        "filtered_source_index": sentinel_index,
        "original_raw_observation": raw_sentinel["observation"],
    }
    return SourceSentinelNormalization(normalized, anomaly_detail, action)


def make_result(
    *,
    isin: str,
    currency: str | None,
    instrument_id: str | None,
    returned_isin: str | None,
    resolution_method: str | None,
    status: str,
    error_message: str | None = None,
    parsed: list[tuple[int, float]] | None = None,
    chronological: bool = False,
    non_positive_values: int = 0,
    resolution_attempts: tuple[dict[str, str], ...] = (),
    anomaly_details: tuple[dict[str, object], ...] = (),
    normalized: tuple[tuple[int, float], ...] | None = None,
    normalization_actions: tuple[dict[str, object], ...] = (),
) -> ValidationResult:
    parsed = parsed or []
    normalized = tuple(parsed) if normalized is None else normalized
    timestamps = [ts for ts, _ in parsed]
    return ValidationResult(
        isin=isin,
        currency=currency,
        instrument_id=instrument_id,
        returned_isin=returned_isin,
        resolution_method=resolution_method,
        observations=len(parsed),
        unique_timestamps=len(set(timestamps)),
        duplicates=len(timestamps) - len(set(timestamps)),
        chronological=chronological,
        non_positive_values=non_positive_values,
        first_date=timestamp_to_date(parsed[0][0]) if parsed else None,
        last_date=timestamp_to_date(parsed[-1][0]) if parsed else None,
        status=status,
        error=error_message,
        resolution_attempts=resolution_attempts,
        anomaly_details=anomaly_details,
        usable_for_backtest=status in {"PASS", "PASS_WITH_FILTERED_SENTINEL"},
        normalized_observations=len(normalized),
        filtered_observations=len(parsed) - len(normalized),
        normalized_first_date=timestamp_to_date(normalized[0][0]) if normalized else None,
        normalized_last_date=timestamp_to_date(normalized[-1][0]) if normalized else None,
        normalization_actions=normalization_actions,
    )


def validate_isin(isin: str, currency: str | None) -> ValidationResult:
    instrument_id: str | None = None
    returned_isin: str | None = None
    resolution_method: str | None = None
    resolution_attempts: tuple[dict[str, str], ...] = ()

    try:
        resolution = resolve_instrument_id(isin)
        resolution_method = resolution.method
        instrument_id = resolution.instrument_id
        resolution_attempts = resolution.attempts

        if resolution.status == "NO_ERSTE_MAPPING":
            return make_result(
                isin=isin,
                currency=currency,
                instrument_id=None,
                returned_isin=None,
                resolution_method=resolution_method,
                status="NO_ERSTE_MAPPING",
                error_message=resolution.detail,
                resolution_attempts=resolution.attempts,
                anomaly_details=(
                    {
                        "kind": "NO_ERSTE_MAPPING",
                        "resolution_attempts": list(resolution.attempts),
                    },
                ),
            )

        if instrument_id is None:
            raise ValueError("Resolver returned RESOLVED without an instrument ID")

        data = fetch_chart(isin, instrument_id)
        returned_isin = str(data.get("isin", "")).strip().upper()

        if returned_isin != isin:
            return make_result(
                isin=isin,
                currency=currency,
                instrument_id=instrument_id,
                returned_isin=returned_isin,
                resolution_method=resolution_method,
                status="SOURCE_ERROR",
                error_message=(
                    f"Chart ISIN mismatch: requested={isin}, "
                    f"returned={returned_isin!r}"
                ),
                resolution_attempts=resolution.attempts,
                anomaly_details=(
                    {
                        "kind": "SOURCE_ERROR",
                        "reason": "chart_isin_mismatch",
                    },
                ),
            )

        returned_instrument_id = str(data.get("instrument_id", "")).strip()
        if returned_instrument_id and returned_instrument_id != instrument_id:
            return make_result(
                isin=isin,
                currency=currency,
                instrument_id=instrument_id,
                returned_isin=returned_isin,
                resolution_method=resolution_method,
                status="SOURCE_ERROR",
                error_message=(
                    f"Instrument ID mismatch: resolved={instrument_id}, "
                    f"chart={returned_instrument_id}"
                ),
                resolution_attempts=resolution.attempts,
                anomaly_details=(
                    {
                        "kind": "SOURCE_ERROR",
                        "reason": "instrument_id_mismatch",
                    },
                ),
            )

        series = data.get("series")
        if not isinstance(series, list) or not series:
            return make_result(
                isin=isin,
                currency=currency,
                instrument_id=instrument_id,
                returned_isin=returned_isin,
                resolution_method=resolution_method,
                status="NO_CHART_HISTORY",
                error_message=(
                    "Historical series is empty" if isinstance(series, list) else "Chart response has no valid series list"
                ),
                resolution_attempts=resolution.attempts,
                anomaly_details=(
                    {
                        "kind": "NO_CHART_HISTORY",
                        "reason": "empty_series"
                        if isinstance(series, list)
                        else "missing_or_invalid_series",
                    },
                ),
            )

        parsed: list[tuple[int, float]] = []
        for index, row in enumerate(series):
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise ValueError(f"Invalid series row #{index}: {row!r}")
            try:
                timestamp = int(row[0])
                nav = float(row[1])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid numeric values in series row #{index}: {row!r}"
                ) from exc
            parsed.append((timestamp, nav))

        timestamps = [timestamp for timestamp, _ in parsed]
        chronological = timestamps == sorted(timestamps)
        invalid_values = invalid_nav_details(parsed)

        if not chronological:
            return make_result(
                isin=isin,
                currency=currency,
                instrument_id=instrument_id,
                returned_isin=returned_isin,
                resolution_method=resolution_method,
                status="SOURCE_ERROR",
                error_message="Historical NAV series is not chronological",
                parsed=parsed,
                chronological=False,
                non_positive_values=sum(nav <= 0 for _, nav in parsed if isfinite(nav)),
                resolution_attempts=resolution.attempts,
                anomaly_details=(
                    {
                        "kind": "SOURCE_ERROR",
                        "reason": "non_chronological_series",
                    },
                    *invalid_values,
                ),
            )

        conflicting_values = conflicting_history_details(parsed)

        sentinel_normalization = classify_source_sentinel(isin, parsed)
        if sentinel_normalization is not None and not conflicting_values:
            return make_result(
                isin=isin,
                currency=currency,
                instrument_id=instrument_id,
                returned_isin=returned_isin,
                resolution_method=resolution_method,
                status="PASS_WITH_FILTERED_SENTINEL",
                error_message=(
                    "One confirmed Erste Market source-sentinel observation "
                    "was excluded from the normalized series"
                ),
                parsed=parsed,
                chronological=True,
                non_positive_values=1,
                resolution_attempts=resolution.attempts,
                anomaly_details=(sentinel_normalization.anomaly_detail,),
                normalized=sentinel_normalization.normalized,
                normalization_actions=(sentinel_normalization.action,),
            )

        if invalid_values:
            return make_result(
                isin=isin,
                currency=currency,
                instrument_id=instrument_id,
                returned_isin=returned_isin,
                resolution_method=resolution_method,
                status="INVALID_NAV",
                error_message=f"{len(invalid_values)} invalid NAV value(s)",
                parsed=parsed,
                chronological=True,
                non_positive_values=sum(nav <= 0 for _, nav in parsed if isfinite(nav)),
                resolution_attempts=resolution.attempts,
                anomaly_details=(*invalid_values, *conflicting_values),
            )

        if conflicting_values:
            return make_result(
                isin=isin,
                currency=currency,
                instrument_id=instrument_id,
                returned_isin=returned_isin,
                resolution_method=resolution_method,
                status="CONFLICTING_HISTORY",
                error_message=f"{len(conflicting_values)} conflicting timestamp(s)",
                parsed=parsed,
                chronological=True,
                non_positive_values=0,
                resolution_attempts=resolution.attempts,
                anomaly_details=conflicting_values,
            )

        return make_result(
            isin=isin,
            currency=currency,
            instrument_id=instrument_id,
            returned_isin=returned_isin,
            resolution_method=resolution_method,
            status="PASS",
            parsed=parsed,
            chronological=True,
            non_positive_values=0,
            resolution_attempts=resolution.attempts,
        )

    except (HTTPError, URLError, TimeoutError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return make_result(
            isin=isin,
            currency=currency,
            instrument_id=instrument_id,
            returned_isin=returned_isin,
            resolution_method=resolution_method,
            status="SOURCE_ERROR",
            error_message=str(exc),
            resolution_attempts=resolution_attempts,
            anomaly_details=(
                {
                    "kind": "SOURCE_ERROR",
                    "reason": "request_or_response_error",
                    "message": str(exc),
                },
            ),
        )


def erste_provenance(
    result: ValidationResult, retrieved_at: str
) -> SourceProvenance:
    """Build provenance for the existing Erste acquisition result.

    This deliberately delegates resolution, chart retrieval, and validation to
    `validate_isin`; the source layer does not repeat any Erste logic.
    """
    endpoint_metadata = {
        "detail_page": FUND_PAGE_URL.format(isin=result.isin),
        "autocomplete": AUTOCOMPLETE_URL.format(isin=quote(result.isin, safe="")),
    }
    if result.instrument_id is not None:
        endpoint_metadata["chart"] = CHART_URL.format(
            instrument_id=result.instrument_id
        )
    return SourceProvenance(
        source_name=PRIMARY_SOURCE_NAME,
        source_priority=PRIMARY_SOURCE_PRIORITY,
        source_identifier=result.instrument_id,
        endpoint_metadata=endpoint_metadata,
        retrieved_at=retrieved_at,
    )


def source_validation_from_erste(
    result: ValidationResult, retrieved_at: str
) -> SourceHistoryValidation:
    return SourceHistoryValidation(
        isin=result.isin,
        currency=result.currency,
        status=result.status,
        usable_for_backtest=result.usable_for_backtest,
        provenance=erste_provenance(result, retrieved_at),
        raw_observation_count=result.observations,
        normalized_observation_count=result.normalized_observations,
        filtered_observation_count=result.filtered_observations,
        date_range=(result.first_date, result.last_date),
        normalized_date_range=(
            result.normalized_first_date,
            result.normalized_last_date,
        ),
        warnings=(result.error,) if result.error is not None else (),
        anomalies=result.anomaly_details,
    )


class ErsteMarketNavSource:
    """Adapter around the established Erste resolver and validator."""

    source_name = PRIMARY_SOURCE_NAME
    source_priority = PRIMARY_SOURCE_PRIORITY

    def __init__(
        self,
        validator: Callable[[str, str | None], ValidationResult] | None = None,
        timestamp_factory: Callable[[], str] = utc_retrieval_timestamp,
    ) -> None:
        self._validator = validator or validate_isin
        self._timestamp_factory = timestamp_factory

    def validate_history(
        self, isin: str, currency: str | None
    ) -> SourceHistoryValidation:
        return source_validation_from_erste(
            self._validator(isin, currency), self._timestamp_factory()
        )


def make_secondary_validation(
    payload: SecondaryHistoryPayload,
    *,
    expected_isin: str,
    expected_currency: str | None,
) -> SourceHistoryValidation:
    """Validate an approved secondary payload without changing raw history.

    This is the reusable validation contract for future provider adapters.  It
    accepts only an exact ISIN, explicit compatible currency, chronological
    observations, and positive finite NAV values. Exact duplicate observations
    are retained; conflicting duplicate timestamps fail closed.
    """
    observations = list(payload.observations)
    date_range = (
        timestamp_to_date(observations[0][0]) if observations else None,
        timestamp_to_date(observations[-1][0]) if observations else None,
    )
    def result(
        status: str,
        usable_for_backtest: bool,
        warnings: tuple[str, ...] = (),
        anomalies: tuple[dict[str, object], ...] = (),
    ) -> SourceHistoryValidation:
        return SourceHistoryValidation(
            isin=expected_isin,
            currency=payload.currency,
            status=status,
            usable_for_backtest=usable_for_backtest,
            provenance=payload.provenance,
            raw_observation_count=len(observations),
            normalized_observation_count=len(observations),
            filtered_observation_count=0,
            date_range=date_range,
            normalized_date_range=date_range,
            warnings=warnings,
            anomalies=anomalies,
        )
    if payload.isin != expected_isin:
        return result(
            "SOURCE_ERROR",
            False,
            warnings=(
                (
                    "Secondary source ISIN mismatch: "
                    f"requested={expected_isin}, returned={payload.isin}"
                ),
            ),
            anomalies=(
                {
                    "kind": "SOURCE_ERROR",
                    "reason": "secondary_isin_mismatch",
                    "returned_isin": payload.isin,
                },
            ),
        )
    if payload.currency is None or (
        expected_currency is not None and payload.currency != expected_currency
    ):
        return result(
            "SOURCE_ERROR",
            False,
            warnings=(
                (
                    "Secondary source currency metadata is missing or incompatible: "
                    f"expected={expected_currency}, returned={payload.currency}"
                ),
            ),
            anomalies=(
                {
                    "kind": "SOURCE_ERROR",
                    "reason": "secondary_currency_mismatch",
                    "returned_currency": payload.currency,
                },
            ),
        )
    if not observations:
        return result(
            "NO_CHART_HISTORY",
            False,
            warnings=("Secondary source returned no historical NAV observations",),
            anomalies=({"kind": "NO_CHART_HISTORY", "reason": "empty_series"},),
        )

    timestamps = [timestamp for timestamp, _ in observations]
    if timestamps != sorted(timestamps):
        return result(
            "SOURCE_ERROR",
            False,
            warnings=("Secondary historical NAV series is not chronological",),
            anomalies=(
                {"kind": "SOURCE_ERROR", "reason": "non_chronological_series"},
            ),
        )

    invalid_values = invalid_nav_details(observations)
    conflicting_values = conflicting_history_details(observations)
    if invalid_values:
        return result(
            "INVALID_NAV",
            False,
            warnings=(f"{len(invalid_values)} invalid secondary NAV value(s)",),
            anomalies=(*invalid_values, *conflicting_values),
        )
    if conflicting_values:
        return result(
            "CONFLICTING_HISTORY",
            False,
            warnings=(
                f"{len(conflicting_values)} conflicting secondary timestamp(s)",
            ),
            anomalies=conflicting_values,
        )
    return result("PASS", True)


@dataclass(frozen=True)
class SourceCoverageResult:
    """Final selected-source decision and complete primary/fallback evidence."""

    isin: str
    currency: str | None
    status: str
    selected_source: str | None
    primary_source_status: str
    fallback_source_status: str | None
    source_priority: int | None
    source_identifier: str | None
    endpoint_metadata: dict[str, str]
    retrieval_timestamp: str | None
    raw_observation_count: int
    normalized_observation_count: int
    filtered_observation_count: int
    date_range: tuple[str | None, str | None]
    usable_for_backtest: bool
    reconciliation_status: str | None
    warnings: tuple[str, ...]
    anomalies: tuple[dict[str, object], ...]
    primary_provenance: SourceProvenance
    fallback_provenance: SourceProvenance | None


class FallbackSourceResolver:
    """Fail-closed primary/fallback policy, with no provider configured here."""

    def __init__(
        self, secondary_source: HistoricalNavSource | None = None
    ) -> None:
        self._secondary_source = secondary_source

    def resolve(
        self, primary: SourceHistoryValidation
    ) -> SourceCoverageResult:
        if primary.status in PRIMARY_USABLE_STATUSES and primary.usable_for_backtest:
            return self._from_selected_primary(primary)
        if primary.status == "NO_ERSTE_MAPPING":
            return self._resolve_unmapped(primary)
        if primary.status == "CONFLICTING_HISTORY":
            return self._reconciliation_required(primary)
        return self._from_unusable_primary(primary)

    def _from_selected_primary(
        self, primary: SourceHistoryValidation
    ) -> SourceCoverageResult:
        return SourceCoverageResult(
            isin=primary.isin,
            currency=primary.currency,
            status=primary.status,
            selected_source=primary.provenance.source_name,
            primary_source_status=primary.status,
            fallback_source_status=None,
            source_priority=primary.provenance.source_priority,
            source_identifier=primary.provenance.source_identifier,
            endpoint_metadata=primary.provenance.endpoint_metadata,
            retrieval_timestamp=primary.provenance.retrieved_at,
            raw_observation_count=primary.raw_observation_count,
            normalized_observation_count=primary.normalized_observation_count,
            filtered_observation_count=primary.filtered_observation_count,
            date_range=primary.normalized_date_range,
            usable_for_backtest=True,
            reconciliation_status=None,
            warnings=primary.warnings,
            anomalies=primary.anomalies,
            primary_provenance=primary.provenance,
            fallback_provenance=None,
        )

    def _resolve_unmapped(
        self, primary: SourceHistoryValidation
    ) -> SourceCoverageResult:
        if self._secondary_source is None:
            return self._from_unresolved(
                primary,
                status="SECONDARY_SOURCE_REQUIRED",
                warning=(
                    "Erste returned NO_ERSTE_MAPPING and no approved secondary "
                    "historical NAV source is configured."
                ),
            )
        fallback = self._secondary_source.validate_history(primary.isin, primary.currency)
        if fallback.status == "PASS" and fallback.usable_for_backtest:
            return SourceCoverageResult(
                isin=primary.isin,
                currency=fallback.currency,
                status="PASS_WITH_FALLBACK_SOURCE",
                selected_source=fallback.provenance.source_name,
                primary_source_status=primary.status,
                fallback_source_status=fallback.status,
                source_priority=fallback.provenance.source_priority,
                source_identifier=fallback.provenance.source_identifier,
                endpoint_metadata=fallback.provenance.endpoint_metadata,
                retrieval_timestamp=fallback.provenance.retrieved_at,
                raw_observation_count=fallback.raw_observation_count,
                normalized_observation_count=fallback.normalized_observation_count,
                filtered_observation_count=fallback.filtered_observation_count,
                date_range=fallback.normalized_date_range,
                usable_for_backtest=True,
                reconciliation_status=None,
                warnings=(*primary.warnings, *fallback.warnings),
                anomalies=(*primary.anomalies, *fallback.anomalies),
                primary_provenance=primary.provenance,
                fallback_provenance=fallback.provenance,
            )
        return self._from_unresolved(
            primary,
            status=fallback.status,
            warning=(
                "Configured secondary source did not pass exact identity and "
                "historical NAV validation."
            ),
            fallback=fallback,
        )

    def _reconciliation_required(
        self, primary: SourceHistoryValidation
    ) -> SourceCoverageResult:
        return self._from_unresolved(
            primary,
            status="RECONCILIATION_REQUIRED",
            warning=(
                "Primary history contains conflicting timestamps. An independent "
                "secondary series and an explicitly accepted reconciliation rule "
                "are required before the series can be used."
            ),
            reconciliation_status="independent_secondary_history_required",
        )

    def _from_unusable_primary(
        self, primary: SourceHistoryValidation
    ) -> SourceCoverageResult:
        return self._from_unresolved(
            primary,
            status=primary.status,
            warning="Primary source did not pass historical NAV validation.",
        )

    def _from_unresolved(
        self,
        primary: SourceHistoryValidation,
        *,
        status: str,
        warning: str,
        fallback: SourceHistoryValidation | None = None,
        reconciliation_status: str | None = None,
    ) -> SourceCoverageResult:
        return SourceCoverageResult(
            isin=primary.isin,
            currency=primary.currency,
            status=status,
            selected_source=None,
            primary_source_status=primary.status,
            fallback_source_status=fallback.status if fallback is not None else None,
            source_priority=None,
            source_identifier=None,
            endpoint_metadata={},
            retrieval_timestamp=None,
            raw_observation_count=(
                fallback.raw_observation_count
                if fallback is not None
                else primary.raw_observation_count
            ),
            normalized_observation_count=(
                fallback.normalized_observation_count
                if fallback is not None
                else primary.normalized_observation_count
            ),
            filtered_observation_count=(
                fallback.filtered_observation_count
                if fallback is not None
                else primary.filtered_observation_count
            ),
            date_range=(
                fallback.normalized_date_range
                if fallback is not None
                else primary.normalized_date_range
            ),
            usable_for_backtest=False,
            reconciliation_status=reconciliation_status,
            warnings=(*primary.warnings, warning, *(fallback.warnings if fallback else ())),
            anomalies=(*primary.anomalies, *(fallback.anomalies if fallback else ())),
            primary_provenance=primary.provenance,
            fallback_provenance=fallback.provenance if fallback is not None else None,
        )


def source_coverage_record(result: SourceCoverageResult) -> dict[str, object]:
    """Stable source-selection/provenance record; no source data is mutated."""
    return {
        "isin": result.isin,
        "currency": result.currency,
        "status": result.status,
        "selected_source": result.selected_source,
        "primary_source_status": result.primary_source_status,
        "fallback_source_status": result.fallback_source_status,
        "source_priority": result.source_priority,
        "source_identifier": result.source_identifier,
        "endpoint_metadata": result.endpoint_metadata,
        "retrieval_timestamp": result.retrieval_timestamp,
        "raw_observation_count": result.raw_observation_count,
        "normalized_observation_count": result.normalized_observation_count,
        "filtered_observation_count": result.filtered_observation_count,
        "date_range": {"first": result.date_range[0], "last": result.date_range[1]},
        "usable_for_backtest": result.usable_for_backtest,
        "reconciliation_status": result.reconciliation_status,
        "warnings": list(result.warnings),
        "anomalies": list(result.anomalies),
        "primary_provenance": {
            "source_name": result.primary_provenance.source_name,
            "source_priority": result.primary_provenance.source_priority,
            "source_identifier": result.primary_provenance.source_identifier,
            "endpoint_metadata": result.primary_provenance.endpoint_metadata,
            "retrieved_at": result.primary_provenance.retrieved_at,
        },
        "fallback_provenance": (
            None
            if result.fallback_provenance is None
            else {
                "source_name": result.fallback_provenance.source_name,
                "source_priority": result.fallback_provenance.source_priority,
                "source_identifier": result.fallback_provenance.source_identifier,
                "endpoint_metadata": result.fallback_provenance.endpoint_metadata,
                "retrieved_at": result.fallback_provenance.retrieved_at,
            }
        ),
    }


def write_source_coverage_output(
    output_path: Path, results: list[SourceCoverageResult]
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    payload: dict[str, object] = {
        "status_counts": dict(sorted(status_counts.items())),
        "results": [source_coverage_record(result) for result in results],
    }
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def print_result(result: ValidationResult) -> None:
    currency = result.currency or "-"
    method = result.resolution_method or "-"

    if result.status == "PASS":
        print(
            f"{result.status:20}  "
            f"{result.isin:12}  "
            f"{currency:4}  "
            f"id={result.instrument_id:<7}  "
            f"via={method:<12}  "
            f"rows={result.observations:<6}  "
            f"unique={result.unique_timestamps:<6}  "
            f"dup={result.duplicates:<3}  "
            f"{result.first_date} -> {result.last_date}"
        )
    else:
        print(
            f"{result.status:20}  "
            f"{result.isin:12}  "
            f"{currency:4}  "
            f"id={result.instrument_id or '-':<7}  "
            f"via={method:<28}  "
            f"{result.error}"
        )


def audit_record(result: ValidationResult) -> dict[str, object]:
    """Convert one validation result to the stable machine-readable audit shape."""
    return {
        "isin": result.isin,
        "currency": result.currency,
        "status": result.status,
        "instrument_id": result.instrument_id,
        "returned_isin": result.returned_isin,
        "resolution_method": result.resolution_method,
        "resolution_attempts": list(result.resolution_attempts),
        "observation_count": result.observations,
        "raw_observation_count": result.observations,
        "normalized_observation_count": result.normalized_observations,
        "filtered_observation_count": result.filtered_observations,
        "unique_timestamp_count": result.unique_timestamps,
        "duplicate_count": result.duplicates,
        "chronological": result.chronological,
        "non_positive_value_count": result.non_positive_values,
        "date_range": {"first": result.first_date, "last": result.last_date},
        "normalized_date_range": {
            "first": result.normalized_first_date,
            "last": result.normalized_last_date,
        },
        "anomaly_details": list(result.anomaly_details),
        "normalization_actions": list(result.normalization_actions),
        "error": result.error,
        "usable_for_backtest": result.usable_for_backtest,
    }


def write_audit_output(
    output_path: Path, results: list[ValidationResult], status_counts: dict[str, int]
) -> None:
    """Write diagnostics without altering source observations or source SQLite."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "status_counts": dict(sorted(status_counts.items())),
        "results": [audit_record(result) for result in results],
    }
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the Portfolio Advisor ISIN -> Erste instrument_id -> "
            "historical NAV acquisition contract."
        )
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("database/model_portfolio.sqlite"),
        help="Path to model_portfolio.sqlite",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of distinct ISINs to validate.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between Erste validation requests in seconds.",
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=Path("data/audit/erste_nav_diagnostics.json"),
        help="Path for machine-readable exception diagnostics JSON.",
    )
    parser.add_argument(
        "--source-coverage-output",
        type=Path,
        default=Path("data/audit/historical_nav_source_coverage.json"),
        help=(
            "Path for source-selection and fallback-readiness provenance JSON. "
            "No secondary provider is configured by this script."
        ),
    )
    args = parser.parse_args()

    if args.limit <= 0:
        error("ERROR: --limit must be greater than zero.")
        return 2
    if args.delay < 0:
        error("ERROR: --delay cannot be negative.")
        return 2
    if not args.database.is_file():
        error(f"ERROR: database not found: {args.database}")
        return 2

    database_uri = f"file:{args.database.resolve()}?mode=ro"

    try:
        connection = sqlite3.connect(database_uri, uri=True)
    except sqlite3.Error as exc:
        error(f"ERROR: cannot open database in read-only mode: {exc}")
        return 2

    try:
        source = discover_isin_source(connection)
        print("SQLite source")
        print("-------------")
        print(f"Table:           {source.table}")
        print(f"ISIN column:     {source.isin_column}")
        print(f"Currency column: {source.currency_column or 'not detected'}")
        print()
        samples = load_sample_isins(connection, source, args.limit)
    except (sqlite3.Error, RuntimeError) as exc:
        error(f"ERROR: {exc}")
        return 2
    finally:
        connection.close()

    if not samples:
        error("ERROR: no valid ISINs found")
        return 2

    print(f"Validating {len(samples)} ISIN(s)")
    print("-----------------------------")

    results: list[ValidationResult] = []
    for index, (isin, currency) in enumerate(samples, start=1):
        print(f"[{index}/{len(samples)}] {isin}")
        result = validate_isin(isin, currency)
        print_result(result)
        results.append(result)
        if index < len(samples):
            time.sleep(args.delay)

    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1

    try:
        write_audit_output(args.audit_output, results, status_counts)
        coverage_resolver = FallbackSourceResolver()
        coverage_results = [
            coverage_resolver.resolve(
                source_validation_from_erste(result, utc_retrieval_timestamp())
            )
            for result in results
        ]
        write_source_coverage_output(args.source_coverage_output, coverage_results)
    except OSError as exc:
        error(f"ERROR: could not write audit output: {exc}")
        return 2

    usable = sum(result.usable_for_backtest for result in results)
    blocked = len(results) - usable

    print()
    print("Summary")
    print("-------")
    print(f"Tested: {len(results)}")
    print(f"Usable for backtest: {usable}")
    print(f"Not usable for backtest: {blocked}")
    print()
    print("Status counts:")
    for status in sorted(status_counts):
        print(f"  {status}: {status_counts[status]}")
    print(f"Machine-readable audit: {args.audit_output}")
    print(f"Machine-readable source coverage: {args.source_coverage_output}")

    if blocked_results := [
        result for result in results if not result.usable_for_backtest
    ]:
        print()
        print("Non-usable mappings:")
        for result in blocked_results:
            print(f"  {result.isin}: {result.status}: {result.error}")

    print()
    if blocked == 0:
        print("Acquisition contract: PASS")
        return 0

    print("Acquisition contract: NOT YET VALIDATED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
