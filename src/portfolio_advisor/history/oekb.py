"""Fail-closed, bounded acquisition of public OeKB historical NAV data."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

OEK_B_HISTORY_URL = "https://my.oekb.at/fond-info/rest/public/preisDaten/{isin}/hist"
OEK_B_PLATFORM_CONTEXT = (
    "eyJzdGFnZSI6IlBST0QiLCJsYW5ndWFnZSI6ImRlIiwicGxhdGZvcm0i"
    "OiJLTVMiLCJkYXNoYm9hcmQiOiJLTVNfT1VUUFVUIn0="
)
MAX_OEKB_CHUNK_DAYS = 90


class OekbAcquisitionError(RuntimeError):
    """Raised when an OeKB response cannot safely be used as source evidence."""


@dataclass(frozen=True, slots=True)
class OekbHttpResponse:
    """The minimum HTTP evidence needed by the bounded acquisition helper."""

    status_code: int
    body: bytes


@dataclass(frozen=True, slots=True)
class OekbObservation:
    """One validated raw OeKB observation, before duplicate normalization."""

    calendar_date: date
    calculated_value: Decimal
    currency: str
    dat_kurs: str
    raw_row: dict[str, object]


@dataclass(frozen=True, slots=True)
class OekbChunkProvenance:
    """Evidence for all requests made for one bounded interval."""

    requested_isin: str
    date_from: date
    date_to: date
    http_status: int
    result_status: str
    reported_anz: int
    retrieved_observation_count: int
    page_count: int
    pages: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "requested_isin": self.requested_isin,
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "http_status": self.http_status,
            "result_status": self.result_status,
            "reported_anz": self.reported_anz,
            "retrieved_observation_count": self.retrieved_observation_count,
            "page_count": self.page_count,
            "pages": list(self.pages),
        }


@dataclass(frozen=True, slots=True)
class OekbHistory:
    """Raw and duplicate-normalized OeKB history plus acquisition evidence."""

    requested_isin: str
    returned_isin: str | None
    currency: str | None
    raw_observations: tuple[OekbObservation, ...]
    observations: tuple[OekbObservation, ...]
    chunks: tuple[OekbChunkProvenance, ...]
    duplicate_count: int
    conflict_count: int

    @property
    def usable(self) -> bool:
        return bool(self.observations) and self.conflict_count == 0

    def summary(self) -> dict[str, object]:
        return {
            "requested_isin": self.requested_isin,
            "returned_isin": self.returned_isin,
            "currency": self.currency,
            "raw_observation_count": len(self.raw_observations),
            "normalized_observation_count": len(self.observations),
            "first_date": self.observations[0].calendar_date.isoformat()
            if self.observations
            else None,
            "last_date": self.observations[-1].calendar_date.isoformat()
            if self.observations
            else None,
            "duplicate_count": self.duplicate_count,
            "conflict_count": self.conflict_count,
            "usability_status": (
                "VALID_OEKB_EVIDENCE_NOT_APPROVED"
                if self.usable
                else "NO_OBSERVATIONS"
            ),
        }


OekbHttpGet = Callable[[str, int], OekbHttpResponse | bytes]


def bounded_date_chunks(date_from: date, date_to: date) -> tuple[tuple[date, date], ...]:
    """Return contiguous, inclusive intervals of at most 90 calendar days."""
    if date_to < date_from:
        raise ValueError("date_to must not be before date_from")

    chunks: list[tuple[date, date]] = []
    current = date_from
    while current <= date_to:
        chunk_end = min(current + timedelta(days=MAX_OEKB_CHUNK_DAYS - 1), date_to)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return tuple(chunks)


def _parse_date(value: object) -> date:
    raw = str(value).strip()
    for format_string in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, format_string).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    if len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    raise OekbAcquisitionError(f"OeKB datKurs is not a supported calendar date: {value!r}")


def _parse_nav(value: object) -> Decimal:
    try:
        nav = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OekbAcquisitionError(
            f"OeKB numKursErrechneterWert is not a decimal value: {value!r}"
        ) from exc
    if not nav.is_finite() or nav <= 0:
        raise OekbAcquisitionError(
            "OeKB numKursErrechneterWert must be finite and positive: "
            f"{value!r}"
        )
    return nav


def _normalize_response(response: OekbHttpResponse | bytes) -> OekbHttpResponse:
    if isinstance(response, bytes):
        # Compatibility for simple mocked transports. Production transports retain status.
        return OekbHttpResponse(status_code=200, body=response)
    return response


def _fetch_chunk(
    *,
    isin: str,
    date_from: date,
    date_to: date,
    limit: int,
    timeout: int,
    http_get: OekbHttpGet,
) -> tuple[list[OekbObservation], OekbChunkProvenance]:
    if limit <= 0:
        raise OekbAcquisitionError("Pagination limit must be greater than zero")

    offset = 0
    expected_total: int | None = None
    observations: list[OekbObservation] = []
    pages: list[dict[str, object]] = []
    last_status = 0
    endpoint = OEK_B_HISTORY_URL.format(isin=isin)

    while expected_total is None or len(observations) < expected_total:
        params = {
            "zeitraum": "BENUTZERDEFINIERT",
            "von": date_from.strftime("%Y%m%d"),
            "bis": date_to.strftime("%Y%m%d"),
            "offset": offset,
            "limit": limit,
        }
        url = f"{endpoint}?{urlencode(params)}"
        try:
            response = _normalize_response(http_get(url, timeout))
            payload = json.loads(response.body.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OekbAcquisitionError(f"OeKB request or response failure: {exc}") from exc
        last_status = response.status_code
        if not 200 <= response.status_code < 300:
            raise OekbAcquisitionError(
                f"OeKB returned unsuccessful HTTP status {response.status_code}"
            )
        if not isinstance(payload, dict):
            raise OekbAcquisitionError("OeKB response is not a JSON object")
        total = payload.get("anz")
        rows = payload.get("list")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise OekbAcquisitionError(
                "OeKB response field 'anz' is not a non-negative integer"
            )
        if total == 0 and rows is None:
            rows = []
        if not isinstance(rows, list):
            raise OekbAcquisitionError("OeKB response field 'list' is not a list")
        if len(rows) > limit:
            raise OekbAcquisitionError("OeKB pagination returned more rows than limit")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise OekbAcquisitionError(
                "OeKB pagination returned inconsistent 'anz' values"
            )
        pages.append(
            {
                "url": url,
                "offset": offset,
                "limit": limit,
                "http_status": response.status_code,
                "reported_anz": total,
                "returned_row_count": len(rows),
            }
        )
        if len(observations) + len(rows) > total:
            raise OekbAcquisitionError("OeKB returned more rows than its reported 'anz'")
        if not rows and len(observations) < total:
            raise OekbAcquisitionError(
                "OeKB pagination ended before the reported observation count"
            )
        for row in rows:
            if not isinstance(row, dict):
                raise OekbAcquisitionError("OeKB history list contains a non-object row")
            returned_isin = str(row.get("numWkn", "")).strip().upper()
            if returned_isin != isin:
                raise OekbAcquisitionError(
                    f"OeKB ISIN mismatch: requested={isin}, returned={returned_isin!r}"
                )
            currency = str(row.get("waehrung", "")).strip().upper()
            if not currency:
                raise OekbAcquisitionError("OeKB waehrung is missing or empty")
            dat_kurs = str(row.get("datKurs", "")).strip()
            calendar_date = _parse_date(dat_kurs)
            if not date_from <= calendar_date <= date_to:
                raise OekbAcquisitionError(
                    "OeKB datKurs is outside its requested chunk: "
                    f"{calendar_date.isoformat()}"
                )
            observations.append(
                OekbObservation(
                    calendar_date=calendar_date,
                    calculated_value=_parse_nav(row.get("numKursErrechneterWert")),
                    currency=currency,
                    dat_kurs=dat_kurs,
                    raw_row=dict(row),
                )
            )
        if not rows:
            break
        offset += len(rows)

    if expected_total is None:
        raise OekbAcquisitionError("OeKB pagination produced no response")
    if len(observations) != expected_total:
        raise OekbAcquisitionError(
            f"OeKB returned {len(observations)} rows; expected {expected_total}"
        )
    return observations, OekbChunkProvenance(
        requested_isin=isin,
        date_from=date_from,
        date_to=date_to,
        http_status=last_status,
        result_status="EMPTY" if expected_total == 0 else "OK",
        reported_anz=expected_total,
        retrieved_observation_count=len(observations),
        page_count=len(pages),
        pages=tuple(pages),
    )


def fetch_bounded_oekb_history(
    *,
    isin: str,
    date_from: date,
    date_to: date,
    limit: int,
    timeout: int,
    http_get: OekbHttpGet,
) -> OekbHistory:
    """Fetch, validate, and deterministically merge every bounded OeKB chunk."""
    requested_isin = isin.strip().upper()
    if not requested_isin:
        raise ValueError("isin must not be empty")

    raw_observations: list[OekbObservation] = []
    chunk_provenance: list[OekbChunkProvenance] = []
    for chunk_from, chunk_to in bounded_date_chunks(date_from, date_to):
        observations, provenance = _fetch_chunk(
            isin=requested_isin,
            date_from=chunk_from,
            date_to=chunk_to,
            limit=limit,
            timeout=timeout,
            http_get=http_get,
        )
        raw_observations.extend(observations)
        chunk_provenance.append(provenance)

    by_date: dict[date, OekbObservation] = {}
    duplicate_count = 0
    for observation in raw_observations:
        existing = by_date.get(observation.calendar_date)
        if existing is None:
            by_date[observation.calendar_date] = observation
        elif existing.raw_row == observation.raw_row:
            duplicate_count += 1
        else:
            raise OekbAcquisitionError(
                "OeKB contains conflicting observations for calendar date "
                f"{observation.calendar_date.isoformat()}"
            )
    normalized_observations = tuple(
        sorted(by_date.values(), key=lambda item: item.calendar_date)
    )
    currencies = {observation.currency for observation in normalized_observations}
    if len(currencies) > 1:
        raise OekbAcquisitionError(
            "OeKB returned inconsistent currencies: " + ", ".join(sorted(currencies))
        )
    return OekbHistory(
        requested_isin=requested_isin,
        returned_isin=requested_isin if normalized_observations else None,
        currency=next(iter(currencies), None),
        raw_observations=tuple(raw_observations),
        observations=normalized_observations,
        chunks=tuple(chunk_provenance),
        duplicate_count=duplicate_count,
        conflict_count=0,
    )
