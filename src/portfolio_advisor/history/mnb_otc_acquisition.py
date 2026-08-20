"""Narrow, auditable acquisition of KELER weekly OTC PDFs from MNB.

This is deliberately separate from :mod:`mnb_otc`: parsers, imports and audit
generation are offline-only.  The only network-capable entry point is the
explicit acquisition command which uses MNB's public advanced-search form and
the document links returned by that form.  It never constructs publication
identifiers or crawls beyond the caller's finite date interval.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urljoin, urlparse

import requests

OFFICIAL_HOST = "kozzetetelek.mnb.hu"
SEARCH_URL = f"https://{OFFICIAL_HOST}/search/advanced"
DETAIL_URL = f"https://{OFFICIAL_HOST}/kozzetetelek?viewid={{publication_id}}"
MAX_INTERVAL_DAYS = 400
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024
MAX_LISTING_RESULTS = 100
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_RETRIES = 2
RATE_LIMIT_SECONDS = 0.15
KELER_NAME = "KELER Központi Értéktár"
OTC_SUBJECT = "Heti OTC"
_PERIOD_RE = re.compile(
    r"(?P<start>\d{4}\.\d{2}\.\d{2})\.?\s*-\s*"
    r"(?P<end>\d{4}\.\d{2}\.\d{2})\.?"
)


class MnbOtcAcquisitionError(RuntimeError):
    """An official acquisition result cannot be trusted safely."""


class _Response(Protocol):
    status_code: int
    headers: object
    content: bytes


@dataclass(frozen=True, slots=True)
class OfficialReportListing:
    """One report returned from MNB's bounded public search result."""

    publication_id: str
    subject: str
    publisher: str
    publication_timestamp: str
    period_start: date
    period_end: date

    @property
    def detail_url(self) -> str:
        return DETAIL_URL.format(publication_id=self.publication_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "publication_id": self.publication_id,
            "detail_url": self.detail_url,
            "publisher": self.publisher,
            "subject": self.subject,
            "publication_timestamp": self.publication_timestamp,
            "reporting_period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
        }


@dataclass(frozen=True, slots=True)
class AcquisitionRecord:
    """The result for one MNB-listed report, including failures as evidence."""

    listing: OfficialReportListing
    status: str
    filename: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    source_url: str | None = None
    detail_url: str | None = None
    acquisition_timestamp: str | None = None
    error: str | None = None
    duplicate_of: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = self.listing.as_dict()
        result.update(
            {
                "acquisition_status": self.status,
                "filename": self.filename,
                "sha256": self.sha256,
                "size_bytes": self.size_bytes,
                "source_authority": "MNB public publication infrastructure / KELER publication",
                "source_host": OFFICIAL_HOST,
                "source_url": self.source_url,
                "acquisition_timestamp": self.acquisition_timestamp,
                "error": self.error,
                "duplicate_of": self.duplicate_of,
            }
        )
        return result


class _TableParser(HTMLParser):
    """Small structural HTML reader for MNB result/detail tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, str | None]]] = []
        self._row: list[tuple[str, str | None]] | None = None
        self._cell_text: list[str] | None = None
        self._cell_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_text = []
            self._cell_href = None
        elif tag == "a" and self._cell_text is not None:
            self._cell_href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if self._cell_text is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if (
            tag in {"td", "th"}
            and self._cell_text is not None
            and self._row is not None
        ):
            text = " ".join("".join(self._cell_text).split())
            self._row.append((text, self._cell_href))
            self._cell_text = None
            self._cell_href = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _parse_hungarian_date(value: str) -> date:
    try:
        return date.fromisoformat(value.rstrip(".").replace(".", "-"))
    except ValueError as exc:
        raise MnbOtcAcquisitionError(
            f"MNB listing has malformed reporting date {value!r}"
        ) from exc


def _assert_official_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != OFFICIAL_HOST:
        raise MnbOtcAcquisitionError(
            f"MNB acquisition rejected non-authoritative URL: {url}"
        )


def validate_interval(start: date, end: date) -> None:
    if start > end:
        raise MnbOtcAcquisitionError("MNB acquisition start must not be after end")
    if (end - start).days > MAX_INTERVAL_DAYS:
        raise MnbOtcAcquisitionError(
            "MNB acquisition interval exceeds the bounded maximum"
        )


def _response_header(response: _Response, name: str) -> str:
    headers = response.headers
    if not hasattr(headers, "get"):
        return ""
    value = headers.get(name, "")  # type: ignore[union-attr]
    return str(value)


def _request(client: Any, method: str, url: str, **kwargs: object) -> _Response:
    _assert_official_url(url)
    request = client.post if method == "POST" else client.get
    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            response = request(
                url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=False, **kwargs
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < REQUEST_RETRIES:
                time.sleep(RATE_LIMIT_SECONDS)
                continue
            break
        if 300 <= response.status_code < 400:
            raise MnbOtcAcquisitionError("MNB acquisition rejected an HTTP redirect")
        if response.status_code != 200:
            raise MnbOtcAcquisitionError(
                f"MNB request failed with HTTP {response.status_code}"
            )
        return response
    raise MnbOtcAcquisitionError(f"MNB request failed: {last_error}") from last_error


def parse_search_listing(
    html: str, *, start: date, end: date
) -> tuple[OfficialReportListing, ...]:
    """Parse only KELER weekly-OTC records returned by the official listing."""
    parser = _TableParser()
    parser.feed(html)
    records: list[OfficialReportListing] = []
    for row in parser.rows:
        text_values = [value for value, _ in row]
        hrefs = [href for _, href in row if href]
        identifier_hrefs = [href for href in hrefs if href and "viewid=" in href]
        if len(identifier_hrefs) != 1 or not any(
            OTC_SUBJECT in value for value in text_values
        ):
            continue
        if not any(KELER_NAME in value for value in text_values):
            continue
        subject = next(value for value in text_values if OTC_SUBJECT in value)
        # The same official listing includes weekly OTC statistical notices.
        # Only the explicitly named ``file-ok`` records are the downloadable
        # transaction-report series consumed by the canonical PDF parser.
        if "file" not in subject.casefold():
            continue
        match = _PERIOD_RE.search(subject)
        if match is None:
            raise MnbOtcAcquisitionError(
                f"MNB OTC listing has no deterministic period: {subject!r}"
            )
        period_start = _parse_hungarian_date(match.group("start"))
        period_end = _parse_hungarian_date(match.group("end"))
        if period_start > period_end:
            raise MnbOtcAcquisitionError("MNB listing report period is reversed")
        if period_end < start or period_start > end:
            continue
        href = identifier_hrefs[0]
        publication_id = href.split("viewid=", maxsplit=1)[1].split("&", maxsplit=1)[0]
        if not re.fullmatch(r"K\d+/(?:20\d{2})", publication_id):
            raise MnbOtcAcquisitionError(
                "MNB listing provided malformed publication identifier"
            )
        timestamp = next(
            (value for value in text_values if re.match(r"\d{4}\.\d{2}\.\d{2}", value)),
            "",
        )
        publisher = next(value for value in text_values if KELER_NAME in value)
        records.append(
            OfficialReportListing(
                publication_id=publication_id,
                subject=subject,
                publisher=publisher,
                publication_timestamp=timestamp,
                period_start=period_start,
                period_end=period_end,
            )
        )
    unique = {record.publication_id: record for record in records}
    if len(unique) != len(records):
        raise MnbOtcAcquisitionError(
            "MNB listing returned duplicate publication identifiers"
        )
    return tuple(
        sorted(
            records,
            key=lambda item: (item.period_start, item.period_end, item.publication_id),
        )
    )


def discover_official_reports(
    start: date, end: date, *, client: Any | None = None
) -> tuple[OfficialReportListing, ...]:
    """Use the bounded MNB advanced search; no opaque ID enumeration occurs."""
    validate_interval(start, end)
    http_client: Any = client if client is not None else requests.Session()
    data = urlencode(
        {
            "DocumentSubject": OTC_SUBJECT,
            "DocumentType": "23",
            "DocumentSubType": "15",
            "publisheddatefrom": start.isoformat(),
            "publisheddateto": end.isoformat(),
            "pagesize": str(MAX_LISTING_RESULTS),
            "page": "1",
            "orderby": "1",
        }
    ).encode("utf-8")
    response = _request(
        http_client,
        "POST",
        SEARCH_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    listing = parse_search_listing(
        response.content.decode("utf-8", errors="strict"), start=start, end=end
    )
    if len(listing) >= MAX_LISTING_RESULTS:
        raise MnbOtcAcquisitionError(
            "MNB listing reached result cap; pagination would be required"
        )
    return listing


def _attachment_from_detail(html: str) -> tuple[str, str]:
    parser = _TableParser()
    parser.feed(html)
    attachments: list[tuple[str, str]] = []
    for row in parser.rows:
        names = [name for name, _ in row if name.casefold().endswith(".pdf")]
        hrefs = [
            href for _, href in row if href is not None and "downloadkozzetetel" in href
        ]
        if names or hrefs:
            if len(names) != 1 or len(hrefs) != 1:
                raise MnbOtcAcquisitionError(
                    "MNB detail page has ambiguous report attachment"
                )
            attachments.append((names[0], hrefs[0]))
    if len(attachments) != 1:
        raise MnbOtcAcquisitionError(
            "MNB detail page has ambiguous or missing report attachment"
        )
    filename, relative_url = attachments[0]
    if not filename.casefold().endswith(".pdf"):
        raise MnbOtcAcquisitionError("MNB detail attachment is not a PDF report")
    return filename, urljoin(
        f"https://{OFFICIAL_HOST}/kozzetetelek", unescape(relative_url)
    )


def _safe_filename(filename: str) -> str:
    safe = Path(filename).name
    if safe != filename or not re.fullmatch(r"[A-Za-z0-9_.-]+\.pdf", safe):
        raise MnbOtcAcquisitionError("MNB attachment filename is unsafe")
    return safe


def _pdf_content(response: _Response) -> bytes:
    content_type = _response_header(response, "Content-Type").casefold()
    if "application/pdf" not in content_type:
        raise MnbOtcAcquisitionError("MNB download does not declare application/pdf")
    declared_size = _response_header(response, "Content-Length")
    if declared_size:
        try:
            if int(declared_size) > MAX_DOWNLOAD_BYTES:
                raise MnbOtcAcquisitionError("MNB PDF exceeds maximum download size")
        except ValueError as exc:
            raise MnbOtcAcquisitionError(
                "MNB download has malformed Content-Length"
            ) from exc
    content = response.content
    if not content or len(content) > MAX_DOWNLOAD_BYTES:
        raise MnbOtcAcquisitionError(
            "MNB PDF is empty or exceeds maximum download size"
        )
    if not content.startswith(b"%PDF-"):
        raise MnbOtcAcquisitionError("MNB download is not a PDF by magic bytes")
    return content


def _existing_hash_paths(raw_directory: Path) -> dict[str, Path]:
    return {
        hashlib.sha256(path.read_bytes()).hexdigest(): path
        for path in sorted(raw_directory.glob("*.pdf"))
        if path.is_file()
    }


def acquire_official_reports(
    listings: tuple[OfficialReportListing, ...],
    raw_directory: Path,
    *,
    client: Any | None = None,
) -> tuple[AcquisitionRecord, ...]:
    """Download only attachment URLs discovered from a supplied MNB listing."""
    raw_directory.mkdir(parents=True, exist_ok=True)
    http_client: Any = client if client is not None else requests.Session()
    known_hashes = _existing_hash_paths(raw_directory)
    records: list[AcquisitionRecord] = []
    for listing in listings:
        detail_url = listing.detail_url
        try:
            detail_response = _request(http_client, "GET", detail_url)
            filename, source_url = _attachment_from_detail(
                detail_response.content.decode("utf-8", errors="strict")
            )
            filename = _safe_filename(filename)
            download_response = _request(http_client, "GET", source_url)
            content = _pdf_content(download_response)
            digest = hashlib.sha256(content).hexdigest()
            timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()
            duplicate_path = known_hashes.get(digest)
            if duplicate_path is not None:
                records.append(
                    AcquisitionRecord(
                        listing,
                        "REPORT_ACQUIRED_DUPLICATE",
                        filename,
                        digest,
                        len(content),
                        source_url,
                        detail_url,
                        timestamp,
                        duplicate_of=str(duplicate_path),
                    )
                )
                continue
            target = raw_directory / filename
            if target.exists():
                records.append(
                    AcquisitionRecord(
                        listing,
                        "REPORT_ACQUISITION_CONFLICTING_FILENAME",
                        filename,
                        digest,
                        len(content),
                        source_url,
                        detail_url,
                        timestamp,
                        error="Existing file with the deterministic filename has different content",
                    )
                )
                continue
            temporary = raw_directory / f".{filename}.{digest[:12]}.partial"
            try:
                temporary.write_bytes(content)
                temporary.replace(target)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise MnbOtcAcquisitionError(
                    f"Unable to atomically retain MNB PDF: {exc}"
                ) from exc
            known_hashes[digest] = target
            records.append(
                AcquisitionRecord(
                    listing,
                    "REPORT_ACQUIRED",
                    filename,
                    digest,
                    len(content),
                    source_url,
                    detail_url,
                    timestamp,
                )
            )
        except (MnbOtcAcquisitionError, UnicodeDecodeError) as exc:
            records.append(
                AcquisitionRecord(
                    listing,
                    "REPORT_ACQUISITION_FAILED",
                    detail_url=detail_url,
                    error=str(exc),
                )
            )
        time.sleep(RATE_LIMIT_SECONDS)
    return tuple(records)
