"""Phase E exact-share-class NAV provenance, offline import, and validation."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.database.schema.v3 import (
    NAV_PROVENANCE_CONTRACT_VERSION,
    NAV_PROVENANCE_FEATURE_FINGERPRINT,
    NAV_PROVENANCE_FEATURE_ID,
    connect,
    transaction,
    upgrade_schema_v3_nav_provenance_extension,
    validate_nav_provenance_schema,
)

PHASE_E_CUTOFF = date(2026, 8, 31)
PHASE_E_CURRENCIES = ("EUR", "HUF")
PHASE_E_SECURITY_COUNT = 8
PHASE_E_MINIMUM_GROUP_COUNT = 3
PHASE_E_MAXIMUM_GROUP_SIZE = 4
PHASE_E_MINIMUM_HISTORY_DAYS = 365
PHASE_E_MAXIMUM_STALENESS_DAYS = 30
PHASE_E_INDEX_SCHEMA_VERSION = 1
PHASE_E_SOURCE_CODE = "ERSTE_MARKET_APPROVED_NAV"
PHASE_E_SOURCE_ORGANIZATION = "Erste Befektetesi Zrt."
ERSTE_MARKET_SOURCE_GOVERNANCE = "APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE"
ERSTE_MARKET_MEDIA_CONTRACT_VERSION = 1
ERSTE_MARKET_CHART_HOST = "www.erstemarket.hu"
ERSTE_MARKET_CHART_MEDIA_TYPE = "text/html; charset=utf-8"
PHASE_E_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PHASE_E_IDENTITY_URL = "https://www.erstemarket.hu/befektetesi_alapok/alap/{isin}"
PHASE_E_SERIES_URL = "https://www.erstemarket.hu/funds/chart/{instrument_id}"
PHASE_E_LICENSING_REFERENCE = "https://www.hozamplaza.hu/jogi-nyilatkozat"
PHASE_E_APPROVAL_BASIS = "LEGACY_APPROVED_EXACT_ISIN_NAV_ACQUISITION_PATH"
PHASE_E_COHORT_ISINS = {
    "EUR": frozenset({
        "AT0000673322",
        "AT0000A00GL9",
        "AT0000A0H8D4",
        "HU0000722442",
        "LU0244270723",
        "LU0594300682",
        "LU1931957093",
        "LU2334866550",
    }),
    "HUF": frozenset({
        "AT0000A00GE4",
        "HU0000702477",
        "HU0000708243",
        "HU0000722434",
        "HU0000723572",
        "LU0979392684",
        "LU0979393062",
        "LU1295422502",
    }),
}
PHASE_E_PROVIDER_CHART_IDS = {
    "AT0000673322": "11752",
    "AT0000A00GL9": "692",
    "AT0000A0H8D4": "7271",
    "HU0000722442": "11002",
    "LU0244270723": "5812",
    "LU0594300682": "8171",
    "LU1931957093": "10952",
    "LU2334866550": "12970",
    "AT0000A00GE4": "332",
    "HU0000702477": "392",
    "HU0000708243": "3962",
    "HU0000722434": "11831",
    "HU0000723572": "11011",
    "LU0979392684": "6971",
    "LU0979393062": "6981",
    "LU1295422502": "8361",
}
LEGACY_NAV_DATASET_FINGERPRINT = (
    "b2e6e4b8c2066c932d6933dbb07d8f22ab1fa9e2cd04c88eae7283334829f99a"
)
LEGACY_NAV_OBSERVATION_COUNT = 8_770
LEGACY_NAV_ISIN_COUNT = 19
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class NavProvenanceError(RuntimeError):
    """Phase E evidence cannot be admitted without changing its meaning."""


@dataclass(frozen=True, slots=True)
class CohortMember:
    instrument_id: int
    isin: str
    share_class_name: str
    currency: str
    asset_class: str
    sub_asset_class: str

    @property
    def group(self) -> tuple[str, str]:
        return (self.asset_class, self.sub_asset_class)


@dataclass(frozen=True, slots=True)
class ArtifactEvidence:
    request_url: str
    retrieval_timestamp: str
    raw_reference: str
    raw_sha256: str
    receipt_reference: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class NavValue:
    observation_date: date
    decimal_text: str
    provider_identity: str
    provider_revision_id: str | None = None
    supersedes_fingerprint: str | None = None

    def payload(self, *, isin: str, currency: str, raw_sha256: str) -> dict[str, object]:
        return {
            "currency": currency,
            "date": self.observation_date.isoformat(),
            "isin": isin,
            "nav_decimal": self.decimal_text,
            "provider_observation_identity": self.provider_identity,
            "provider_revision_id": self.provider_revision_id,
            "raw_artifact_sha256": raw_sha256,
            "supersedes_fingerprint": self.supersedes_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PreparedBundle:
    member: CohortMember
    provider_instrument_id: str
    identity: ArtifactEvidence
    series: ArtifactEvidence
    observations: tuple[NavValue, ...]
    dataset_fingerprint: str
    acquisition_identity: str
    manifest_fingerprint: str
    revision_semantics: str = "PROVIDER_REVISION_FIELD_NOT_SUPPLIED"
    replaces_manifest_fingerprint: str | None = None
    replacement_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalBundleLineage:
    """Immutable external bundle manifests that authorize an offline import."""

    currency_manifest_reference: str
    currency_manifest_sha256: str
    combined_manifest_reference: str
    combined_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class NavImportResult:
    manifest_insert_count: int
    observation_insert_count: int
    total_manifest_count: int
    total_observation_count: int
    phase_e_dataset_fingerprint: str
    currency_dataset_fingerprints: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "currency_dataset_fingerprints": dict(self.currency_dataset_fingerprints),
            "manifest_insert_count": self.manifest_insert_count,
            "observation_insert_count": self.observation_insert_count,
            "phase_e_dataset_fingerprint": self.phase_e_dataset_fingerprint,
            "total_manifest_count": self.total_manifest_count,
            "total_observation_count": self.total_observation_count,
        }


class _IdentityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.instrument_ids: list[str] = []
        self._in_h1 = False
        self._h1_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "h1":
            self._in_h1 = True
        classes = set((attributes.get("class") or "").split())
        instrument_id = attributes.get("instrument-id")
        if "simpleChartContainer" in classes and instrument_id:
            self.instrument_ids.append(instrument_id)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.text_parts.append(value)
            if self._in_h1:
                self._h1_parts.append(value)

    @property
    def heading(self) -> str:
        return " ".join(self._h1_parts).strip()

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NavProvenanceError(f"{label} is missing or malformed: {path}") from error
    if not isinstance(value, dict):
        raise NavProvenanceError(f"{label} must be a JSON object")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise NavProvenanceError(f"{label} must be non-empty canonical text")
    return value


def _require_hash(value: object, label: str) -> str:
    result = _require_text(value, label)
    if _HEX64.fullmatch(result) is None:
        raise NavProvenanceError(f"{label} must be a lowercase SHA-256")
    return result


def _artifact_path(repository_root: Path, reference: object, label: str) -> Path:
    text = _require_text(reference, label)
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise NavProvenanceError(f"{label} escapes the repository root")
    resolved = (repository_root / relative).resolve()
    raw_root = (repository_root / "data" / "raw" / "nav" / "erste_market").resolve()
    if resolved == raw_root or raw_root not in resolved.parents or resolved.is_symlink():
        raise NavProvenanceError(f"{label} is outside the retained NAV evidence directory")
    if not resolved.is_file():
        raise NavProvenanceError(f"{label} is missing")
    return resolved


def _validate_receipt(
    repository_root: Path,
    raw: dict[str, Any],
    *,
    expected_isin: str,
    expected_role: str,
) -> ArtifactEvidence:
    exact = {
        "byte_count",
        "content_type",
        "http_status",
        "provider",
        "raw_artifact_reference",
        "raw_artifact_sha256",
        "request_role",
        "request_url",
        "requested_isin",
        "response_headers",
        "retrieval_timestamp",
        "schema_version",
    }
    if set(raw) != exact:
        raise NavProvenanceError("receipt fields do not match the Phase E contract")
    if raw["schema_version"] != 1 or raw["provider"] != PHASE_E_SOURCE_CODE:
        raise NavProvenanceError("receipt source identity is unsupported")
    if raw["http_status"] != 200 or raw["request_role"] != expected_role:
        raise NavProvenanceError("receipt status or request role is invalid")
    if raw["requested_isin"] != expected_isin:
        raise NavProvenanceError("receipt exact ISIN does not match the cohort")
    raw_path = _artifact_path(
        repository_root, raw["raw_artifact_reference"], "raw artifact reference"
    )
    raw_sha = _require_hash(raw["raw_artifact_sha256"], "raw artifact SHA-256")
    if _sha256(raw_path) != raw_sha or raw_path.name.split(".", 1)[0] != raw_sha:
        raise NavProvenanceError("raw artifact content or content-addressed name mismatches")
    if raw["byte_count"] != raw_path.stat().st_size:
        raise NavProvenanceError("raw artifact byte count mismatches receipt")
    timestamp = _require_text(raw["retrieval_timestamp"], "retrieval timestamp")
    try:
        datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise NavProvenanceError("retrieval timestamp is invalid") from error
    return ArtifactEvidence(
        request_url=_require_text(raw["request_url"], "request URL"),
        retrieval_timestamp=timestamp,
        raw_reference=str(raw["raw_artifact_reference"]),
        raw_sha256=raw_sha,
        receipt_reference="",
        receipt_sha256="",
    )


def _validate_semantic_receipt(
    repository_root: Path,
    raw: dict[str, Any],
    *,
    expected_isin: str,
) -> ArtifactEvidence:
    exact = {
        "assessment",
        "raw_artifact_reference",
        "raw_artifact_sha256",
        "receipt_type",
        "schema_version",
        "transport_receipt_reference",
        "transport_receipt_sha256",
    }
    if set(raw) != exact or raw.get("schema_version") != 1:
        raise NavProvenanceError("semantic-admission receipt fields are invalid")
    if raw.get("receipt_type") != "ERSTE_MARKET_CHART_SEMANTIC_ADMISSION":
        raise NavProvenanceError("semantic-admission receipt type is unsupported")
    raw_path = _artifact_path(
        repository_root, raw["raw_artifact_reference"], "semantic raw artifact reference"
    )
    raw_sha = _require_hash(raw["raw_artifact_sha256"], "semantic raw artifact SHA-256")
    if _sha256(raw_path) != raw_sha or raw_path.name.split(".", 1)[0] != raw_sha:
        raise NavProvenanceError("semantic raw artifact hash or content-addressed name mismatches")
    transport_path = _artifact_path(
        repository_root,
        raw["transport_receipt_reference"],
        "semantic transport receipt reference",
    )
    transport_sha = _require_hash(
        raw["transport_receipt_sha256"], "semantic transport receipt SHA-256"
    )
    if _sha256(transport_path) != transport_sha:
        raise NavProvenanceError("semantic transport receipt hash mismatch")
    transport = _read_json(transport_path, "semantic transport receipt")
    if (
        transport.get("raw_artifact_reference") != raw["raw_artifact_reference"]
        or transport.get("raw_artifact_sha256") != raw_sha
        or transport.get("requested_isin") != expected_isin
        or transport.get("request_role") != "series"
        or transport.get("http_status") != 200
        or transport.get("body_complete") is not True
        or transport.get("redirect_history") != []
        or transport.get("transport_error") is not None
    ):
        raise NavProvenanceError("semantic receipt does not reconcile to its transport receipt")
    parsed_url = urlsplit(str(transport.get("requested_url", "")))
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc != ERSTE_MARKET_CHART_HOST
        or parsed_url.query
        or parsed_url.fragment
        or re.fullmatch(r"/funds/chart/[0-9]+", parsed_url.path) is None
        or transport.get("final_url") != transport.get("requested_url")
    ):
        raise NavProvenanceError("semantic transport URL is outside the provider-specific contract")
    media_type, transport_classification = _chart_transport_media(transport)
    assessment = raw.get("assessment")
    if not isinstance(assessment, dict):
        raise NavProvenanceError("semantic-admission assessment is malformed")
    core = {
        key: value
        for key, value in assessment.items()
        if key not in {"assessment_fingerprint", "semantic_status"}
    }
    if (
        assessment.get("semantic_status") != "SEMANTIC_ADMISSIBLE_IN_MEMORY_ONLY"
        or assessment.get("assessment_fingerprint") != canonical_fingerprint(core)
        or assessment.get("raw_artifact_sha256") != raw_sha
        or assessment.get("receipt_sha256") != transport_sha
        or assessment.get("isin") != expected_isin
        or assessment.get("provider") != PHASE_E_SOURCE_CODE
        or assessment.get("source_governance") != ERSTE_MARKET_SOURCE_GOVERNANCE
        or assessment.get("normalized_media_type") != media_type
        or assessment.get("transport_classification") != transport_classification
    ):
        raise NavProvenanceError("semantic-admission assessment fingerprint or identity is invalid")
    timestamp = _require_text(transport.get("retrieval_timestamp"), "transport retrieval timestamp")
    try:
        datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise NavProvenanceError("transport retrieval timestamp is invalid") from error
    return ArtifactEvidence(
        request_url=_require_text(transport.get("requested_url"), "transport request URL"),
        retrieval_timestamp=timestamp,
        raw_reference=str(raw["raw_artifact_reference"]),
        raw_sha256=raw_sha,
        receipt_reference="",
        receipt_sha256="",
    )


def _evidence_from_entry(
    repository_root: Path,
    entry: dict[str, Any],
    *,
    isin: str,
    role: str,
) -> ArtifactEvidence:
    expected = {"raw_artifact_reference", "raw_artifact_sha256", "receipt_reference", "receipt_sha256"}
    if set(entry) != expected:
        raise NavProvenanceError(f"{role} index entry fields are invalid")
    receipt_path = _artifact_path(repository_root, entry["receipt_reference"], f"{role} receipt")
    receipt_sha = _require_hash(entry["receipt_sha256"], f"{role} receipt SHA-256")
    if _sha256(receipt_path) != receipt_sha:
        raise NavProvenanceError(f"{role} receipt hash mismatch")
    receipt = _read_json(receipt_path, f"{role} receipt")
    result = (
        _validate_semantic_receipt(repository_root, receipt, expected_isin=isin)
        if role == "series" and receipt.get("receipt_type") is not None
        else _validate_receipt(
            repository_root, receipt, expected_isin=isin, expected_role=role
        )
    )
    if (
        result.raw_reference != entry["raw_artifact_reference"]
        or result.raw_sha256 != entry["raw_artifact_sha256"]
    ):
        raise NavProvenanceError(f"{role} index and receipt do not reconcile")
    return ArtifactEvidence(
        request_url=result.request_url,
        retrieval_timestamp=result.retrieval_timestamp,
        raw_reference=result.raw_reference,
        raw_sha256=result.raw_sha256,
        receipt_reference=str(entry["receipt_reference"]),
        receipt_sha256=receipt_sha,
    )


def select_phase_e_cohorts(database_path: Path) -> dict[str, tuple[CohortMember, ...]]:
    """Derive the smallest exact EUR/HUF cohorts from the latest reviewed shortlist."""
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise NavProvenanceError("cohort source database failed integrity_check")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise NavProvenanceError("cohort source database failed foreign_key_check")
        snapshot = connection.execute(
            "SELECT max(shortlist_snapshot_id) FROM shortlist_snapshot WHERE snapshot_date<=?",
            (PHASE_E_CUTOFF.isoformat(),),
        ).fetchone()[0]
        if snapshot is None:
            raise NavProvenanceError("no reviewed shortlist exists at the evidence cutoff")
        rows = connection.execute(
            """SELECT i.instrument_id, i.isin, o.observed_product_name,
                      o.observed_currency_code, o.observed_asset_class,
                      o.observed_sub_asset_class, o.conflict_status
               FROM shortlist_entry e
               JOIN instrument i ON i.instrument_id=e.instrument_id
               JOIN shortlist_entry_lineage l ON l.shortlist_entry_id=e.shortlist_entry_id
               JOIN shortlist_entry_source_occurrence o
                 ON o.shortlist_entry_source_occurrence_id=l.source_occurrence_id
               WHERE e.shortlist_snapshot_id=?
                 AND o.observed_currency_code IN ('EUR','HUF')
               ORDER BY o.observed_currency_code, i.isin""",
            (snapshot,),
        ).fetchall()
    grouped: dict[str, list[CohortMember]] = {currency: [] for currency in PHASE_E_CURRENCIES}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        currency = str(row["observed_currency_code"])
        isin = str(row["isin"])
        if isin not in PHASE_E_COHORT_ISINS[currency]:
            continue
        key = (currency, isin)
        if key in seen:
            raise NavProvenanceError("cohort member has duplicate shortlist lineage")
        seen.add(key)
        values = (
            row["observed_product_name"],
            row["observed_asset_class"],
            row["observed_sub_asset_class"],
        )
        if row["conflict_status"] != "SOURCE_REPORTED" or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise NavProvenanceError("cohort member lacks conflict-free category evidence")
        if _ISIN.fullmatch(isin) is None:
            raise NavProvenanceError("cohort member has an invalid exact ISIN")
        grouped[currency].append(
            CohortMember(
                instrument_id=int(row["instrument_id"]),
                isin=isin,
                share_class_name=str(row["observed_product_name"]).strip(),
                currency=currency,
                asset_class=str(row["observed_asset_class"]).strip(),
                sub_asset_class=str(row["observed_sub_asset_class"]).strip(),
            )
        )
    result: dict[str, tuple[CohortMember, ...]] = {}
    for currency in PHASE_E_CURRENCIES:
        members = tuple(sorted(grouped[currency], key=lambda item: item.isin))
        if {member.isin for member in members} != PHASE_E_COHORT_ISINS[currency]:
            raise NavProvenanceError(f"{currency} reviewed Phase E cohort is incomplete")
        if len(members) != PHASE_E_SECURITY_COUNT:
            raise NavProvenanceError(
                f"{currency} exact-ISIN minimum cohort has {len(members)} members; expected 8"
            )
        groups: dict[tuple[str, str], int] = {}
        for member in members:
            groups[member.group] = groups.get(member.group, 0) + 1
        if len(groups) < PHASE_E_MINIMUM_GROUP_COUNT or max(groups.values()) > PHASE_E_MAXIMUM_GROUP_SIZE:
            raise NavProvenanceError(f"{currency} cohort fails reviewed diversification bounds")
        result[currency] = members
    return result


def _parse_identity(raw: bytes, member: CohortMember) -> tuple[str, str]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise NavProvenanceError("identity artifact is not UTF-8 HTML") from error
    parser = _IdentityParser()
    parser.feed(text)
    instrument_ids = sorted(set(parser.instrument_ids))
    if len(instrument_ids) != 1 or not instrument_ids[0].isdigit():
        raise NavProvenanceError("identity page does not provide one chart instrument ID")
    visible = parser.text
    if re.search(rf"\bISIN\s*:?\s*{re.escape(member.isin)}\b", visible) is None:
        raise NavProvenanceError("identity page does not identify the exact ISIN")
    if re.search(rf"Alap devizaneme\s+{member.currency}\b", visible) is None:
        raise NavProvenanceError("identity page does not identify the exact NAV currency")
    if not parser.heading:
        raise NavProvenanceError("identity page has no exact share-class heading")
    return instrument_ids[0], parser.heading


def _decimal_text(value: object) -> str:
    if isinstance(value, bool):
        raise NavProvenanceError("NAV value is not a Decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise NavProvenanceError("NAV value is not a Decimal") from error
    if not result.is_finite() or result <= 0:
        raise NavProvenanceError("NAV value must be finite and positive")
    return str(result)


def _parse_series(
    raw: bytes,
    member: CohortMember,
    provider_instrument_id: str,
) -> tuple[NavValue, ...]:
    try:
        payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NavProvenanceError("series artifact is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or str(payload.get("isin", "")).strip().upper() != member.isin:
        raise NavProvenanceError("series artifact does not identify the exact ISIN")
    returned_id = str(payload.get("instrument_id", "")).strip()
    if returned_id and returned_id != provider_instrument_id:
        raise NavProvenanceError("series artifact instrument ID conflicts with identity page")
    raw_series = payload.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise NavProvenanceError("series artifact is empty or malformed")
    all_values: list[NavValue] = []
    dates: set[date] = set()
    identities: set[str] = set()
    for row in raw_series:
        if not isinstance(row, list) or len(row) != 2:
            raise NavProvenanceError("series observation is malformed")
        timestamp = row[0]
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise NavProvenanceError("series timestamp must be an integer")
        try:
            observed = datetime.fromtimestamp(timestamp / 1000, tz=UTC).date()
        except (OSError, OverflowError, ValueError) as error:
            raise NavProvenanceError("series timestamp is outside the supported range") from error
        identity = str(timestamp)
        if observed in dates or identity in identities:
            raise NavProvenanceError("series contains a duplicate observation")
        dates.add(observed)
        identities.add(identity)
        all_values.append(NavValue(observed, _decimal_text(row[1]), identity))
    all_values.sort(key=lambda item: item.observation_date)
    bounded = [item for item in all_values if item.observation_date <= PHASE_E_CUTOFF]
    if not bounded:
        raise NavProvenanceError("series has no observation at or before the evidence cutoff")
    last = bounded[-1].observation_date
    if (PHASE_E_CUTOFF - last).days > PHASE_E_MAXIMUM_STALENESS_DAYS:
        raise NavProvenanceError("series is more than 30 calendar days stale")
    eligible_starts = [
        index
        for index, item in enumerate(bounded)
        if (last - item.observation_date).days >= PHASE_E_MINIMUM_HISTORY_DAYS
    ]
    if not eligible_starts:
        raise NavProvenanceError("series has less than 365 calendar days of history")
    selected = tuple(bounded[max(eligible_starts) :])
    if (selected[-1].observation_date - selected[0].observation_date).days < PHASE_E_MINIMUM_HISTORY_DAYS:
        raise NavProvenanceError("minimal series slice does not span 365 calendar days")
    return selected


def _normalize_erste_market_chart_media_type(value: object) -> str:
    """Accept only Erste Market's observed chart mislabelling, never generic HTML."""
    text = _require_text(value, "chart content type")
    pieces = [item.strip() for item in text.split(";")]
    if len(pieces) != 2 or pieces[0].lower() != "text/html":
        raise NavProvenanceError("chart content type is outside the Erste Market media contract")
    name, separator, raw_charset = pieces[1].partition("=")
    if separator != "=" or name.strip().lower() != "charset":
        raise NavProvenanceError("chart content type has an unsupported parameter")
    charset = raw_charset.strip().strip('"').lower()
    if charset != "utf-8":
        raise NavProvenanceError("chart content type charset is outside the Erste Market media contract")
    return ERSTE_MARKET_CHART_MEDIA_TYPE


def _normalize_json_media_type(value: object) -> str:
    text = _require_text(value, "chart content type")
    pieces = [item.strip() for item in text.split(";")]
    if not pieces or pieces[0].lower() != "application/json" or len(pieces) > 2:
        raise NavProvenanceError("chart content type is not application/json")
    if len(pieces) == 2:
        name, separator, raw_charset = pieces[1].partition("=")
        if (
            separator != "="
            or name.strip().lower() != "charset"
            or raw_charset.strip().strip('"').lower() != "utf-8"
        ):
            raise NavProvenanceError("application/json content type has an unsupported parameter")
    return "application/json"


def _chart_transport_media(receipt: dict[str, Any]) -> tuple[str, str]:
    headers = receipt.get("response_headers")
    if not isinstance(headers, dict):
        raise NavProvenanceError("quarantine response headers are malformed")
    raw_content_type = receipt.get("content_type")
    header_content_type = headers.get("content-type")
    try:
        media_type = _normalize_json_media_type(raw_content_type)
        if _normalize_json_media_type(header_content_type) != media_type:
            raise NavProvenanceError("quarantine content-type header does not reconcile to the receipt")
        return media_type, "VALID_NAV_RESPONSE"
    except NavProvenanceError:
        media_type = _normalize_erste_market_chart_media_type(raw_content_type)
        if _normalize_erste_market_chart_media_type(header_content_type) != media_type:
            raise NavProvenanceError("quarantine content-type header does not reconcile to the receipt")
        return media_type, "QUARANTINED_REJECTED_RESPONSE"


def _phase_e_cohort_member(database_path: Path, isin: str) -> CohortMember:
    members = [
        member
        for currency in PHASE_E_CURRENCIES
        for member in select_phase_e_cohorts(database_path)[currency]
        if member.isin == isin
    ]
    if len(members) != 1:
        raise NavProvenanceError("quarantined chart ISIN is not one exact Phase E cohort member")
    return members[0]


def _retained_identity_for_quarantined_chart(
    *, repository_root: Path, index_path: Path, member: CohortMember
) -> tuple[str, str]:
    state = _read_json(index_path, "Phase E acquisition index")
    if (
        state.get("schema_version") != PHASE_E_INDEX_SCHEMA_VERSION
        or state.get("provider") != PHASE_E_SOURCE_CODE
        or state.get("evidence_cutoff") != PHASE_E_CUTOFF.isoformat()
    ):
        raise NavProvenanceError("Phase E acquisition index identity is unsupported")
    bundles = state.get("bundles")
    if not isinstance(bundles, list):
        raise NavProvenanceError("Phase E acquisition index has no bundle list")
    entries = [item for item in bundles if isinstance(item, dict) and item.get("isin") == member.isin]
    if len(entries) != 1:
        raise NavProvenanceError("Phase E acquisition index has no unique exact-ISIN bundle")
    entry = entries[0]
    if set(entry) not in (
        {"currency", "identity", "isin"},
        {"currency", "identity", "isin", "series"},
    ) or entry["currency"] != member.currency:
        raise NavProvenanceError("quarantined chart has an unexpected acquisition-index state")
    identity = entry["identity"]
    if not isinstance(identity, dict):
        raise NavProvenanceError("quarantined chart lacks retained identity evidence")
    evidence = _evidence_from_entry(repository_root, identity, isin=member.isin, role="identity")
    instrument_id, heading = _parse_identity(
        (repository_root / evidence.raw_reference).read_bytes(), member
    )
    if PHASE_E_PROVIDER_CHART_IDS.get(member.isin) != instrument_id:
        raise NavProvenanceError("retained identity chart ID conflicts with the reviewed cohort mapping")
    return instrument_id, heading


def _validate_erste_market_chart_schema(
    payload: object, *, member: CohortMember, instrument_id: str, heading: str
) -> None:
    expected = {
        "decimals",
        "id",
        "instrument_id",
        "isin",
        "last_close",
        "series",
        "ticker",
        "title",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise NavProvenanceError("chart JSON does not match the exact Erste Market provider schema")
    if payload["id"] != instrument_id or payload["instrument_id"] != instrument_id:
        raise NavProvenanceError("chart JSON instrument ID does not match retained identity evidence")
    if payload["isin"] != member.isin:
        raise NavProvenanceError("chart JSON ISIN does not match retained identity evidence")
    if (
        not isinstance(payload["title"], str)
        or not isinstance(payload["ticker"], str)
        or not payload["title"].strip()
        or payload["title"] != payload["ticker"]
        or heading != f"Befektetési alapok {payload['title']}"
    ):
        raise NavProvenanceError("chart JSON share-class identity does not match the retained identity page")
    decimals = payload["decimals"]
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 12:
        raise NavProvenanceError("chart JSON decimals field is invalid")
    _decimal_text(payload["last_close"])


def assess_erste_market_quarantined_chart(
    *,
    repository_root: Path,
    database_path: Path,
    index_path: Path,
    isin: str,
    raw_reference: str,
    raw_sha256: str,
    receipt_reference: str,
    receipt_sha256: str,
) -> dict[str, object]:
    """Read-only semantic assessment for the one approved-distributor media quirk.

    This function intentionally leaves the original transport classification and
    quarantine untouched.  Its result is in-memory only; promotion requires a
    later, separately authorized acquisition step.
    """
    member = _phase_e_cohort_member(database_path, isin)
    instrument_id, heading = _retained_identity_for_quarantined_chart(
        repository_root=repository_root, index_path=index_path, member=member
    )
    expected_url = PHASE_E_SERIES_URL.format(instrument_id=instrument_id)
    supplied_raw_sha = _require_hash(raw_sha256, "supplied quarantined raw SHA-256")
    supplied_receipt_sha = _require_hash(receipt_sha256, "supplied quarantine receipt SHA-256")
    raw_path = _artifact_path(repository_root, raw_reference, "quarantined raw artifact reference")
    receipt_path = _artifact_path(repository_root, receipt_reference, "quarantine receipt reference")
    if _sha256(raw_path) != supplied_raw_sha:
        raise NavProvenanceError("quarantined raw artifact hash does not match the supplied hash")
    if _sha256(receipt_path) != supplied_receipt_sha:
        raise NavProvenanceError("quarantine receipt hash does not match the supplied hash")
    receipt = _read_json(receipt_path, "quarantine receipt")
    expected_receipt = {
        "body_complete",
        "byte_count",
        "content_encoding",
        "content_type",
        "final_url",
        "http_status",
        "max_response_bytes",
        "provider",
        "raw_artifact_reference",
        "raw_artifact_sha256",
        "redirect_history",
        "request_role",
        "requested_isin",
        "requested_url",
        "response_headers",
        "retention_status",
        "retrieval_timestamp",
        "schema_version",
        "transport_error",
    }
    if set(receipt) != expected_receipt:
        raise NavProvenanceError("quarantine receipt fields do not match the media contract")
    if (
        receipt["schema_version"] != 1
        or receipt["provider"] != PHASE_E_SOURCE_CODE
        or receipt["retention_status"] != "QUARANTINED_RESPONSE"
        or receipt["request_role"] != "series"
        or receipt["requested_isin"] != member.isin
        or receipt["raw_artifact_reference"] != raw_reference
        or receipt["raw_artifact_sha256"] != supplied_raw_sha
        or receipt["requested_url"] != expected_url
        or receipt["final_url"] != expected_url
        or receipt["http_status"] != 200
        or receipt["redirect_history"] != []
        or receipt["transport_error"] is not None
        or receipt["body_complete"] is not True
    ):
        raise NavProvenanceError("quarantined transport receipt is outside the Erste Market media contract")
    if (
        isinstance(receipt["byte_count"], bool)
        or not isinstance(receipt["byte_count"], int)
        or receipt["byte_count"] <= 0
        or receipt["byte_count"] != raw_path.stat().st_size
        or isinstance(receipt["max_response_bytes"], bool)
        or not isinstance(receipt["max_response_bytes"], int)
        or receipt["max_response_bytes"] != PHASE_E_MAX_RESPONSE_BYTES
        or receipt["byte_count"] > receipt["max_response_bytes"]
    ):
        raise NavProvenanceError("quarantined response size is outside the Erste Market media contract")
    parsed_url = urlsplit(expected_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc != ERSTE_MARKET_CHART_HOST
        or parsed_url.query
        or parsed_url.fragment
        or re.fullmatch(r"/funds/chart/[0-9]+", parsed_url.path) is None
    ):
        raise NavProvenanceError("Erste Market chart URL is outside the provider-specific media contract")
    media_type, transport_classification = _chart_transport_media(receipt)
    try:
        body = raw_path.read_bytes()
        payload = json.loads(body.decode("utf-8", errors="strict"), parse_float=Decimal)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NavProvenanceError("quarantined chart body is not one whole UTF-8 JSON document") from error
    _validate_erste_market_chart_schema(
        payload, member=member, instrument_id=instrument_id, heading=heading
    )
    observations = _parse_series(body, member, instrument_id)
    dataset_fingerprint = canonical_fingerprint(
        [item.payload(isin=member.isin, currency=member.currency, raw_sha256=supplied_raw_sha) for item in observations]
    )
    assessment = {
        "assessment_scope": "IN_MEMORY_ONLY_NO_ARTIFACT_OR_DATABASE_ADMISSION",
        "currency": member.currency,
        "dataset_fingerprint": dataset_fingerprint,
        "first_observation_date": observations[0].observation_date.isoformat(),
        "instrument_id": instrument_id,
        "isin": member.isin,
        "last_observation_date": observations[-1].observation_date.isoformat(),
        "media_contract_version": ERSTE_MARKET_MEDIA_CONTRACT_VERSION,
        "normalized_media_type": media_type,
        "observation_count": len(observations),
        "provider": PHASE_E_SOURCE_CODE,
        "raw_artifact_sha256": supplied_raw_sha,
        "receipt_sha256": supplied_receipt_sha,
        "source_governance": ERSTE_MARKET_SOURCE_GOVERNANCE,
        "transport_classification": transport_classification,
    }
    return {
        **assessment,
        "assessment_fingerprint": canonical_fingerprint(assessment),
        "semantic_status": "SEMANTIC_ADMISSIBLE_IN_MEMORY_ONLY",
    }


def _manifest_payload(
    *,
    member: CohortMember,
    provider_instrument_id: str,
    identity: ArtifactEvidence,
    series: ArtifactEvidence,
    dataset_fingerprint: str,
    observations: tuple[NavValue, ...],
    acquisition_identity: str,
    revision_semantics: str,
    replaces_manifest_fingerprint: str | None,
    replacement_reason: str | None,
) -> dict[str, object]:
    return {
        "acquisition_identity": acquisition_identity,
        "admitted_first_date": observations[0].observation_date.isoformat(),
        "admitted_last_date": observations[-1].observation_date.isoformat(),
        "admitted_observation_count": len(observations),
        "contract_version": NAV_PROVENANCE_CONTRACT_VERSION,
        "dataset_fingerprint": dataset_fingerprint,
        "evidence_cutoff": PHASE_E_CUTOFF.isoformat(),
        "exact_isin": member.isin,
        "identity": identity.__dict__ if hasattr(identity, "__dict__") else {
            name: getattr(identity, name) for name in ArtifactEvidence.__slots__
        },
        "nav_currency": member.currency,
        "provider_instrument_id": provider_instrument_id,
        "replacement_reason": replacement_reason,
        "replaces_manifest_fingerprint": replaces_manifest_fingerprint,
        "revision_semantics": revision_semantics,
        "series": series.__dict__ if hasattr(series, "__dict__") else {
            name: getattr(series, name) for name in ArtifactEvidence.__slots__
        },
        "share_class_name": member.share_class_name,
        "source_code": PHASE_E_SOURCE_CODE,
    }


def prepare_bundles(
    *, repository_root: Path, database_path: Path, index_path: Path
) -> tuple[PreparedBundle, ...]:
    cohorts = select_phase_e_cohorts(database_path)
    expected_members = {
        member.isin: member for members in cohorts.values() for member in members
    }
    index = _read_json(index_path, "Phase E acquisition index")
    if set(index) != {"bundles", "evidence_cutoff", "provider", "schema_version"}:
        raise NavProvenanceError("Phase E acquisition index fields are invalid")
    if (
        index["schema_version"] != PHASE_E_INDEX_SCHEMA_VERSION
        or index["provider"] != PHASE_E_SOURCE_CODE
        or index["evidence_cutoff"] != PHASE_E_CUTOFF.isoformat()
    ):
        raise NavProvenanceError("Phase E acquisition index identity is invalid")
    raw_bundles = index["bundles"]
    if not isinstance(raw_bundles, list) or len(raw_bundles) != len(expected_members):
        raise NavProvenanceError("Phase E acquisition index is incomplete")
    prepared: list[PreparedBundle] = []
    seen: set[str] = set()
    for raw_bundle in raw_bundles:
        if not isinstance(raw_bundle, dict) or set(raw_bundle) != {
            "currency", "identity", "isin", "series"
        }:
            raise NavProvenanceError("Phase E bundle index entry is malformed")
        isin = _require_text(raw_bundle["isin"], "bundle ISIN")
        if isin in seen or isin not in expected_members:
            raise NavProvenanceError("Phase E bundle has a duplicate or unexpected ISIN")
        seen.add(isin)
        member = expected_members[isin]
        if raw_bundle["currency"] != member.currency:
            raise NavProvenanceError("Phase E bundle currency conflicts with cohort evidence")
        if not isinstance(raw_bundle["identity"], dict) or not isinstance(raw_bundle["series"], dict):
            raise NavProvenanceError("Phase E bundle artifact entries are malformed")
        identity = _evidence_from_entry(
            repository_root, raw_bundle["identity"], isin=isin, role="identity"
        )
        series = _evidence_from_entry(
            repository_root, raw_bundle["series"], isin=isin, role="series"
        )
        identity_raw = (repository_root / identity.raw_reference).read_bytes()
        series_raw = (repository_root / series.raw_reference).read_bytes()
        provider_instrument_id, provider_name = _parse_identity(identity_raw, member)
        provider_member = CohortMember(
            instrument_id=member.instrument_id,
            isin=member.isin,
            share_class_name=provider_name,
            currency=member.currency,
            asset_class=member.asset_class,
            sub_asset_class=member.sub_asset_class,
        )
        if identity.request_url != PHASE_E_IDENTITY_URL.format(isin=isin):
            raise NavProvenanceError("identity request URL is not exact")
        if series.request_url != PHASE_E_SERIES_URL.format(
            instrument_id=provider_instrument_id
        ):
            raise NavProvenanceError("series request URL is not exact")
        observations = _parse_series(series_raw, provider_member, provider_instrument_id)
        observation_payloads = tuple(
            item.payload(isin=isin, currency=member.currency, raw_sha256=series.raw_sha256)
            for item in observations
        )
        dataset_fingerprint = canonical_fingerprint(observation_payloads)
        acquisition_identity = canonical_fingerprint(
            {
                "identity_receipt_sha256": identity.receipt_sha256,
                "identity_raw_sha256": identity.raw_sha256,
                "series_receipt_sha256": series.receipt_sha256,
                "series_raw_sha256": series.raw_sha256,
            }
        )
        manifest_payload = _manifest_payload(
            member=provider_member,
            provider_instrument_id=provider_instrument_id,
            identity=identity,
            series=series,
            dataset_fingerprint=dataset_fingerprint,
            observations=observations,
            acquisition_identity=acquisition_identity,
            revision_semantics="PROVIDER_REVISION_FIELD_NOT_SUPPLIED",
            replaces_manifest_fingerprint=None,
            replacement_reason=None,
        )
        prepared.append(
            PreparedBundle(
                member=provider_member,
                provider_instrument_id=provider_instrument_id,
                identity=identity,
                series=series,
                observations=observations,
                dataset_fingerprint=dataset_fingerprint,
                acquisition_identity=acquisition_identity,
                manifest_fingerprint=canonical_fingerprint(manifest_payload),
            )
        )
    if seen != set(expected_members):
        raise NavProvenanceError("Phase E acquisition index omits cohort members")
    return tuple(sorted(prepared, key=lambda item: (item.member.currency, item.member.isin)))


def _content_addressed_manifest(path: Path, label: str) -> tuple[dict[str, Any], str]:
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if not path.name.startswith(f"{digest}."):
        raise NavProvenanceError(f"{label} is not content-addressed")
    try:
        value = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NavProvenanceError(f"{label} is not valid canonical JSON") from error
    if not isinstance(value, dict):
        raise NavProvenanceError(f"{label} is not an object")
    return value, digest


def _external_bundle_lineage(
    *, repository_root: Path, index_path: Path, bundles: tuple[PreparedBundle, ...]
) -> dict[str, ExternalBundleLineage]:
    """Bind imported rows to the exact retained currency and combined manifests."""
    directory = index_path.parent / "manifests"
    if not directory.is_dir():
        raise NavProvenanceError("Phase E currency bundle manifests are missing")
    currency_manifests: dict[str, tuple[Path, str]] = {}
    for currency in PHASE_E_CURRENCIES:
        matching: list[tuple[Path, str]] = []
        expected = {item.member.isin: item for item in bundles if item.member.currency == currency}
        for path in sorted(directory.glob(f"*.{currency.lower()}.bundle.manifest.json")):
            value, digest = _content_addressed_manifest(path, f"{currency} bundle manifest")
            instruments = value.get("instruments")
            if (
                value.get("manifest_type") != "PHASE_E_CURRENCY_ACQUISITION_BUNDLE"
                or value.get("schema_version") != 1
                or value.get("currency") != currency
                or value.get("source_governance") != ERSTE_MARKET_SOURCE_GOVERNANCE
                or not isinstance(instruments, list)
            ):
                continue
            seen: set[str] = set()
            valid = len(instruments) == len(expected)
            for raw in instruments:
                if not isinstance(raw, dict):
                    valid = False
                    break
                isin = raw.get("isin")
                if not isinstance(isin, str):
                    valid = False
                    break
                bundle = expected.get(isin)
                if bundle is None or isin in seen:
                    valid = False
                    break
                seen.add(isin)
                expected_fields = {
                    "dataset_fingerprint": bundle.dataset_fingerprint,
                    "identity_raw_reference": bundle.identity.raw_reference,
                    "identity_raw_sha256": bundle.identity.raw_sha256,
                    "identity_receipt_reference": bundle.identity.receipt_reference,
                    "identity_receipt_sha256": bundle.identity.receipt_sha256,
                    "semantic_receipt_reference": bundle.series.receipt_reference,
                    "semantic_receipt_sha256": bundle.series.receipt_sha256,
                    "series_raw_reference": bundle.series.raw_reference,
                    "series_raw_sha256": bundle.series.raw_sha256,
                }
                if any(raw.get(key) != expected_value for key, expected_value in expected_fields.items()):
                    valid = False
                    break
            if valid and seen == set(expected):
                matching.append((path, digest))
        if len(matching) != 1:
            raise NavProvenanceError(f"{currency} bundle manifest is missing, ambiguous, or mismatched")
        currency_manifests[currency] = matching[0]
    combined: list[tuple[Path, str]] = []
    for path in sorted(directory.glob("*.combined.acquisition.manifest.json")):
        value, digest = _content_addressed_manifest(path, "combined Phase E bundle manifest")
        raw_currencies = value.get("currency_manifests")
        if (
            value.get("audit_contract") != "MILESTONE_11C_PHASE_E_ACQUISITION_V1"
            or value.get("schema_version") != 1
            or value.get("source_governance") != ERSTE_MARKET_SOURCE_GOVERNANCE
            or not isinstance(raw_currencies, dict)
        ):
            continue
        expected_references = {
            currency: {
                "reference": currency_manifests[currency][0]
                .resolve()
                .relative_to(repository_root.resolve())
                .as_posix(),
                "sha256": currency_manifests[currency][1],
            }
            for currency in PHASE_E_CURRENCIES
        }
        if raw_currencies == expected_references:
            combined.append((path, digest))
    if len(combined) != 1:
        raise NavProvenanceError("combined Phase E bundle manifest is missing, ambiguous, or mismatched")
    combined_path, combined_sha = combined[0]
    return {
        currency: ExternalBundleLineage(
            currency_manifest_reference=currency_manifests[currency][0]
            .resolve()
            .relative_to(repository_root.resolve())
            .as_posix(),
            currency_manifest_sha256=currency_manifests[currency][1],
            combined_manifest_reference=combined_path.resolve()
            .relative_to(repository_root.resolve())
            .as_posix(),
            combined_manifest_sha256=combined_sha,
        )
        for currency in PHASE_E_CURRENCIES
    }


def _source_payload() -> dict[str, object]:
    return {
        "approval_basis": PHASE_E_APPROVAL_BASIS,
        "automated_use_status": "PREVIOUSLY_APPROVED",
        "contract_version": NAV_PROVENANCE_CONTRACT_VERSION,
        "identity_url_template": PHASE_E_IDENTITY_URL,
        "licensing_reference": PHASE_E_LICENSING_REFERENCE,
        "raw_retention_status": "PREVIOUSLY_APPROVED",
        "series_url_template": PHASE_E_SERIES_URL,
        "source_code": PHASE_E_SOURCE_CODE,
        "source_governance": ERSTE_MARKET_SOURCE_GOVERNANCE,
        "source_organization": PHASE_E_SOURCE_ORGANIZATION,
        "source_role": "APPROVED_DISTRIBUTOR",
    }


def _insert_source(connection: sqlite3.Connection) -> int:
    payload = _source_payload()
    fingerprint = canonical_fingerprint(payload)
    row = connection.execute(
        "SELECT nav_evidence_source_id, source_fingerprint FROM nav_evidence_source WHERE source_code=?",
        (PHASE_E_SOURCE_CODE,),
    ).fetchone()
    if row is not None:
        if str(row[1]) != fingerprint:
            raise NavProvenanceError("stored NAV source contract conflicts with Phase E")
        return int(row[0])
    cursor = connection.execute(
        """INSERT INTO nav_evidence_source(
               contract_version,source_code,source_organization,source_governance,identity_url_template,
               series_url_template,source_role,approval_basis,licensing_reference,
               automated_use_status,raw_retention_status,source_fingerprint
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            NAV_PROVENANCE_CONTRACT_VERSION,
            PHASE_E_SOURCE_CODE,
            PHASE_E_SOURCE_ORGANIZATION,
            ERSTE_MARKET_SOURCE_GOVERNANCE,
            PHASE_E_IDENTITY_URL,
            PHASE_E_SERIES_URL,
            "APPROVED_DISTRIBUTOR",
            PHASE_E_APPROVAL_BASIS,
            PHASE_E_LICENSING_REFERENCE,
            "PREVIOUSLY_APPROVED",
            "PREVIOUSLY_APPROVED",
            fingerprint,
        ),
    )
    if cursor.lastrowid is None:
        raise NavProvenanceError("SQLite did not return a NAV source ID")
    return int(cursor.lastrowid)


def _stored_bundle_matches(
    connection: sqlite3.Connection,
    bundle: PreparedBundle,
    lineage: ExternalBundleLineage,
) -> bool:
    row = connection.execute(
        "SELECT nav_import_manifest_id FROM nav_import_manifest WHERE manifest_fingerprint=?",
        (bundle.manifest_fingerprint,),
    ).fetchone()
    if row is None:
        return False
    manifest_id = int(row[0])
    manifest_lineage = connection.execute(
        """SELECT currency_bundle_manifest_reference,currency_bundle_manifest_sha256,
                  combined_bundle_manifest_reference,combined_bundle_manifest_sha256
           FROM nav_import_manifest WHERE nav_import_manifest_id=?""",
        (manifest_id,),
    ).fetchone()
    if manifest_lineage is None or tuple(manifest_lineage) != (
        lineage.currency_manifest_reference,
        lineage.currency_manifest_sha256,
        lineage.combined_manifest_reference,
        lineage.combined_manifest_sha256,
    ):
        raise NavProvenanceError("stored NAV manifest external bundle lineage mismatches")
    stored = connection.execute(
        """SELECT observation_date,nav_decimal,provider_observation_identity,
                  provider_revision_id,raw_artifact_sha256,observation_fingerprint
           FROM nav_observation_version WHERE nav_import_manifest_id=?
           ORDER BY observation_date""",
        (manifest_id,),
    ).fetchall()
    expected = []
    for item in bundle.observations:
        payload = item.payload(
            isin=bundle.member.isin,
            currency=bundle.member.currency,
            raw_sha256=bundle.series.raw_sha256,
        )
        expected.append(
            (
                item.observation_date.isoformat(),
                item.decimal_text,
                item.provider_identity,
                item.provider_revision_id,
                bundle.series.raw_sha256,
                canonical_fingerprint(payload),
            )
        )
    if [tuple(row) for row in stored] != expected:
        raise NavProvenanceError("stored NAV manifest rows do not reproduce offline")
    return True


def _insert_bundle(
    connection: sqlite3.Connection,
    source_id: int,
    bundle: PreparedBundle,
    lineage: ExternalBundleLineage,
) -> tuple[int, int]:
    if _stored_bundle_matches(connection, bundle, lineage):
        return (0, 0)
    current = connection.execute(
        """SELECT m.manifest_fingerprint
           FROM nav_import_manifest m
           WHERE m.nav_evidence_source_id=? AND m.instrument_id=?""",
        (source_id, bundle.member.instrument_id),
    ).fetchall()
    replaces_id: int | None = None
    if current:
        if bundle.revision_semantics != "EXPLICIT_REPLACEMENT":
            raise NavProvenanceError("changed or duplicate NAV evidence lacks replacement provenance")
        if len(current) != 1 or str(current[0][0]) != bundle.replaces_manifest_fingerprint:
            raise NavProvenanceError("explicit replacement does not identify the stored manifest")
        replaces_id = int(
            connection.execute(
                "SELECT nav_import_manifest_id FROM nav_import_manifest WHERE manifest_fingerprint=?",
                (bundle.replaces_manifest_fingerprint,),
            ).fetchone()[0]
        )
    manifest = connection.execute(
        """INSERT INTO nav_import_manifest(
               contract_version,nav_evidence_source_id,instrument_id,exact_isin,
               share_class_name,nav_currency,provider_instrument_id,
               identity_request_url,identity_retrieval_timestamp,
               identity_raw_artifact_reference,identity_raw_artifact_sha256,
               identity_receipt_reference,identity_receipt_sha256,
               series_request_url,series_retrieval_timestamp,
               series_raw_artifact_reference,series_raw_artifact_sha256,
               series_receipt_reference,series_receipt_sha256,acquisition_identity,
               currency_bundle_manifest_reference,currency_bundle_manifest_sha256,
               combined_bundle_manifest_reference,combined_bundle_manifest_sha256,
               evidence_cutoff,admitted_first_date,admitted_last_date,
               admitted_observation_count,revision_semantics,replaces_manifest_id,
               replacement_reason,dataset_fingerprint,manifest_fingerprint,import_status
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            NAV_PROVENANCE_CONTRACT_VERSION,
            source_id,
            bundle.member.instrument_id,
            bundle.member.isin,
            bundle.member.share_class_name,
            bundle.member.currency,
            bundle.provider_instrument_id,
            bundle.identity.request_url,
            bundle.identity.retrieval_timestamp,
            bundle.identity.raw_reference,
            bundle.identity.raw_sha256,
            bundle.identity.receipt_reference,
            bundle.identity.receipt_sha256,
            bundle.series.request_url,
            bundle.series.retrieval_timestamp,
            bundle.series.raw_reference,
            bundle.series.raw_sha256,
            bundle.series.receipt_reference,
            bundle.series.receipt_sha256,
            bundle.acquisition_identity,
            lineage.currency_manifest_reference,
            lineage.currency_manifest_sha256,
            lineage.combined_manifest_reference,
            lineage.combined_manifest_sha256,
            PHASE_E_CUTOFF.isoformat(),
            bundle.observations[0].observation_date.isoformat(),
            bundle.observations[-1].observation_date.isoformat(),
            len(bundle.observations),
            bundle.revision_semantics,
            replaces_id,
            bundle.replacement_reason,
            bundle.dataset_fingerprint,
            bundle.manifest_fingerprint,
            "VALIDATED_ADMITTED",
        ),
    )
    if manifest.lastrowid is None:
        raise NavProvenanceError("SQLite did not return a NAV manifest ID")
    manifest_id = int(manifest.lastrowid)
    inserted = 0
    for item in bundle.observations:
        existing = connection.execute(
            """SELECT n.nav_observation_version_id,n.nav_decimal,
                      n.observation_fingerprint,n.revision_sequence
               FROM nav_observation_version n
               WHERE n.instrument_id=? AND n.observation_date=?
                 AND NOT EXISTS (
                     SELECT 1 FROM nav_observation_version successor
                     WHERE successor.supersedes_observation_id=n.nav_observation_version_id
                 )""",
            (bundle.member.instrument_id, item.observation_date.isoformat()),
        ).fetchone()
        supersedes: int | None = None
        sequence = 1
        if existing is not None:
            if bundle.revision_semantics != "EXPLICIT_REPLACEMENT":
                raise NavProvenanceError("duplicate or conflicting NAV observation is not a revision")
            if item.provider_revision_id is None or item.supersedes_fingerprint != str(existing[2]):
                raise NavProvenanceError("explicit NAV revision lacks exact supersession provenance")
            supersedes = int(existing[0])
            sequence = int(existing[3]) + 1
        payload = item.payload(
            isin=bundle.member.isin,
            currency=bundle.member.currency,
            raw_sha256=bundle.series.raw_sha256,
        )
        connection.execute(
            """INSERT INTO nav_observation_version(
                   nav_import_manifest_id,instrument_id,exact_isin,observation_date,
                   nav_decimal,currency_code,provider_observation_identity,
                   provider_revision_id,revision_sequence,supersedes_observation_id,
                   raw_artifact_sha256,quality_status,observation_fingerprint
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                manifest_id,
                bundle.member.instrument_id,
                bundle.member.isin,
                item.observation_date.isoformat(),
                item.decimal_text,
                bundle.member.currency,
                item.provider_identity,
                item.provider_revision_id,
                sequence,
                supersedes,
                bundle.series.raw_sha256,
                "ADMITTED_VALIDATED",
                canonical_fingerprint(payload),
            ),
        )
        inserted += 1
    return (1, inserted)


def _dataset_summary(bundles: tuple[PreparedBundle, ...]) -> tuple[str, tuple[tuple[str, str], ...]]:
    by_currency: dict[str, list[dict[str, object]]] = {currency: [] for currency in PHASE_E_CURRENCIES}
    for bundle in bundles:
        by_currency[bundle.member.currency].extend(
            item.payload(
                isin=bundle.member.isin,
                currency=bundle.member.currency,
                raw_sha256=bundle.series.raw_sha256,
            )
            for item in bundle.observations
        )
    currency_fingerprints = tuple(
        (currency, canonical_fingerprint(tuple(by_currency[currency])))
        for currency in PHASE_E_CURRENCIES
    )
    return canonical_fingerprint(tuple(item for currency in PHASE_E_CURRENCIES for item in by_currency[currency])), currency_fingerprints


def import_phase_e_nav(
    *, repository_root: Path, target: Path, index_path: Path
) -> NavImportResult:
    bundles = prepare_bundles(
        repository_root=repository_root, database_path=target, index_path=index_path
    )
    external_lineage = _external_bundle_lineage(
        repository_root=repository_root, index_path=index_path, bundles=bundles
    )
    phase_fingerprint, currency_fingerprints = _dataset_summary(bundles)
    with connect(target) as connection:
        upgrade_schema_v3_nav_provenance_extension(connection)
        manifest_insert_count = 0
        observation_insert_count = 0
        source_row = connection.execute(
            "SELECT nav_evidence_source_id,source_fingerprint FROM nav_evidence_source WHERE source_code=?",
            (PHASE_E_SOURCE_CODE,),
        ).fetchone()
        needs_write = source_row is None
        if source_row is not None:
            if str(source_row[1]) != canonical_fingerprint(_source_payload()):
                raise NavProvenanceError("stored NAV source contract conflicts with Phase E")
            source_id = int(source_row[0])
            needs_write = any(
                not _stored_bundle_matches(
                    connection, bundle, external_lineage[bundle.member.currency]
                )
                for bundle in bundles
            )
        if needs_write:
            with transaction(connection):
                source_id = _insert_source(connection)
                for bundle in bundles:
                    manifests, observations = _insert_bundle(
                        connection,
                        source_id,
                        bundle,
                        external_lineage[bundle.member.currency],
                    )
                    manifest_insert_count += manifests
                    observation_insert_count += observations
        validate_nav_provenance_schema(connection)
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM nav_import_manifest), "
            "(SELECT count(*) FROM nav_observation_version)"
        ).fetchone()
    return NavImportResult(
        manifest_insert_count=manifest_insert_count,
        observation_insert_count=observation_insert_count,
        total_manifest_count=int(counts[0]),
        total_observation_count=int(counts[1]),
        phase_e_dataset_fingerprint=phase_fingerprint,
        currency_dataset_fingerprints=currency_fingerprints,
    )


def legacy_nav_fingerprint(database_path: Path) -> tuple[int, int, str]:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """SELECT i.isin, observation_date, nav_value, currency_code, value_type,
                      source_provider, source_identifier, provenance_reference, quality_status
               FROM instrument_nav_observation n
               JOIN instrument i ON i.instrument_id=n.instrument_id
               ORDER BY 1,2,6,5,7"""
        ).fetchall()
    payload = [tuple(row) for row in rows]
    return len(payload), len({str(row[0]) for row in payload}), hashlib.sha256(
        repr(payload).encode()
    ).hexdigest()


def logical_table_fingerprints(database_path: Path, *, omit_phase_e: bool) -> dict[str, str]:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        result: dict[str, str] = {}
        for table in tables:
            if omit_phase_e and table in {
                "nav_evidence_source", "nav_import_manifest", "nav_observation_version"
            }:
                continue
            escaped = table.replace('"', '""')
            rows = connection.execute(f'SELECT * FROM "{escaped}" ORDER BY rowid').fetchall()
            if omit_phase_e and table == "schema_feature_contract":
                rows = [row for row in rows if str(row[0]) != NAV_PROVENANCE_FEATURE_ID]
            result[table] = canonical_fingerprint([list(row) for row in rows])
    return result


def build_phase_e_candidate(
    *, repository_root: Path, source: Path, candidate: Path, index_path: Path
) -> dict[str, object]:
    if source.resolve() == candidate.resolve():
        raise NavProvenanceError("candidate must differ from the installed database")
    source_sha = _sha256(source)
    before = logical_table_fingerprints(source, omit_phase_e=True)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    if candidate.exists():
        raise NavProvenanceError("candidate already exists")
    shutil.copy2(source, candidate)
    pre_migration_sha = _sha256(candidate)
    if pre_migration_sha != source_sha:
        candidate.unlink(missing_ok=True)
        raise NavProvenanceError("candidate copy is not byte-identical to the installed database")
    try:
        imported = import_phase_e_nav(
            repository_root=repository_root, target=candidate, index_path=index_path
        )
        after = logical_table_fingerprints(candidate, omit_phase_e=True)
        if before != after:
            raise NavProvenanceError("candidate changed pre-existing logical table evidence")
        with sqlite3.connect(f"file:{candidate.resolve()}?mode=ro", uri=True) as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if integrity != "ok" or foreign_keys:
            raise NavProvenanceError("candidate failed SQLite integrity or foreign-key checks")
        if any(candidate.with_name(candidate.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise NavProvenanceError("candidate has active SQLite sidecars")
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise
    return {
        **imported.to_dict(),
        "candidate_pre_migration_sha256": pre_migration_sha,
        "candidate_sha256": _sha256(candidate),
        "foreign_key_violations": 0,
        "integrity_check": "ok",
        "preserved_logical_table_count": len(before),
        "source_sha256": source_sha,
        "status": "PHASE_E_CANDIDATE_VALIDATED",
    }


def validate_phase_e_nav(
    *,
    repository_root: Path,
    target: Path,
    index_path: Path,
    legacy_source: Path,
) -> dict[str, object]:
    before_sha = _sha256(target)
    bundles = prepare_bundles(
        repository_root=repository_root, database_path=target, index_path=index_path
    )
    external_lineage = _external_bundle_lineage(
        repository_root=repository_root, index_path=index_path, bundles=bundles
    )
    phase_fingerprint, currency_fingerprints = _dataset_summary(bundles)
    with sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        validate_nav_provenance_schema(connection)
        source_rows = connection.execute("SELECT * FROM nav_evidence_source").fetchall()
        if len(source_rows) != 1 or str(source_rows[0]["source_fingerprint"]) != canonical_fingerprint(_source_payload()):
            raise NavProvenanceError("stored NAV source does not match the reviewed source contract")
        for bundle in bundles:
            if not _stored_bundle_matches(
                connection, bundle, external_lineage[bundle.member.currency]
            ):
                raise NavProvenanceError("stored NAV bundle is missing")
        manifest_count = int(connection.execute("SELECT count(*) FROM nav_import_manifest").fetchone()[0])
        observation_count = int(connection.execute("SELECT count(*) FROM nav_observation_version").fetchone()[0])
        current_count = int(connection.execute(
            """SELECT count(*) FROM nav_observation_version n
               WHERE NOT EXISTS (
                   SELECT 1 FROM nav_observation_version successor
                   WHERE successor.supersedes_observation_id=n.nav_observation_version_id
               )"""
        ).fetchone()[0])
        currency_rows = connection.execute(
            """SELECT currency_code,count(*),count(DISTINCT exact_isin),min(observation_date),max(observation_date)
               FROM nav_observation_version n
               WHERE NOT EXISTS (
                   SELECT 1 FROM nav_observation_version successor
                   WHERE successor.supersedes_observation_id=n.nav_observation_version_id
               )
               GROUP BY currency_code ORDER BY currency_code"""
        ).fetchall()
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        constructed = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("constructed_portfolio_metadata", "constructed_portfolio_holding_lineage")
        }
    if integrity != "ok" or foreign_keys:
        raise NavProvenanceError("installed NAV evidence failed SQLite checks")
    if manifest_count != len(bundles) or observation_count != current_count:
        raise NavProvenanceError("stored Phase E scope contains unexpected manifests or revisions")
    if any(constructed.values()):
        raise NavProvenanceError("constructed-portfolio production rows are non-zero")
    target_legacy = legacy_nav_fingerprint(target)
    source_legacy = legacy_nav_fingerprint(legacy_source)
    expected_legacy = (
        LEGACY_NAV_OBSERVATION_COUNT,
        LEGACY_NAV_ISIN_COUNT,
        LEGACY_NAV_DATASET_FINGERPRINT,
    )
    if target_legacy != source_legacy or target_legacy != expected_legacy:
        raise NavProvenanceError("legacy NAV evidence or dataset fingerprint changed")
    if _sha256(target) != before_sha:
        raise NavProvenanceError("Phase E validator modified the database")
    cohort_payload = {
        currency: [
            {
                "asset_class": item.member.asset_class,
                "first_date": item.observations[0].observation_date.isoformat(),
                "isin": item.member.isin,
                "last_date": item.observations[-1].observation_date.isoformat(),
                "observation_count": len(item.observations),
                "sub_asset_class": item.member.sub_asset_class,
            }
            for item in bundles
            if item.member.currency == currency
        ]
        for currency in PHASE_E_CURRENCIES
    }
    return {
        "cohorts": cohort_payload,
        "constructed_portfolio_row_counts": constructed,
        "contract_version": NAV_PROVENANCE_CONTRACT_VERSION,
        "currency_dataset_fingerprints": dict(currency_fingerprints),
        "currency_ranges": {
            str(row[0]): {
                "observation_count": int(row[1]),
                "isin_count": int(row[2]),
                "first_date": str(row[3]),
                "last_date": str(row[4]),
            }
            for row in currency_rows
        },
        "database_sha256": before_sha,
        "evidence_cutoff": PHASE_E_CUTOFF.isoformat(),
        "feature_fingerprint": NAV_PROVENANCE_FEATURE_FINGERPRINT,
        "feature_id": NAV_PROVENANCE_FEATURE_ID,
        "foreign_key_violations": foreign_keys,
        "integrity_check": integrity,
        "legacy_nav": {
            "dataset_fingerprint": target_legacy[2],
            "isin_count": target_legacy[1],
            "observation_count": target_legacy[0],
            "provenance_status": "LEGACY_RETAINED_NOT_PHASE_E_PROVENANCE_ADMITTED",
            "source_database_sha256": _sha256(legacy_source),
        },
        "manifest_count": manifest_count,
        "observation_count": observation_count,
        "phase_e_dataset_fingerprint": phase_fingerprint,
        "portfolio_construction": "NOT_PERFORMED",
        "production_cutover": "NOT_AUTHORIZED",
        "source_fingerprint": canonical_fingerprint(_source_payload()),
        "status": "PASS",
    }
