"""Strict offline parsing, persistence, and validation of official ECB €STR evidence."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from itertools import pairwise
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlsplit

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.schema.v3 import (
    REFERENCE_RATE_FEATURE_FINGERPRINT,
    REFERENCE_RATE_FEATURE_ID,
    REFERENCE_RATE_FEATURE_REVISION,
    SchemaVersionError,
    connect,
    initialize_schema,
    transaction,
    validate_schema,
)
from portfolio_advisor.objectives.construction_policy import (
    CapitalDefensiveConstructionPolicy,
)

from .contracts import (
    REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
    ReferenceRateContractError,
    ReferenceRateDefinition,
    ReferenceRateImportManifest,
    ReferenceRateObservation,
    ReferenceRateSource,
    canonical_request_parameters,
    canonical_utc_timestamp,
    internal_evidence_identity,
    validate_observation_availability,
    validate_policy_binding,
)

ECB_ESTR_ADAPTER_SCHEMA_VERSION = 1
ECB_ESTR_BENCHMARK_ID = "ESTR"
ECB_ESTR_DATAFLOW = "EST"
ECB_ESTR_DATAFLOW_REFERENCE = "ECB,EST,1.0"
ECB_ESTR_DSD_REFERENCE = "ECB:ECB_EST1(1.0)"
ECB_ESTR_SERIES_KEY = "B.EU000A2X2A25.WT"
ECB_ESTR_SERIES_IDENTIFIER = f"{ECB_ESTR_DATAFLOW}.{ECB_ESTR_SERIES_KEY}"
ECB_ESTR_ISIN = "EU000A2X2A25"
ECB_ESTR_MACHINE_URL = (
    "https://data-api.ecb.europa.eu/service/data/"
    f"{ECB_ESTR_DATAFLOW_REFERENCE}/{ECB_ESTR_SERIES_KEY}"
)
ECB_ESTR_OFFICIAL_PAGE_URL = (
    "https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/"
    "euro_short-term_rate/html/index.en.html"
)
ECB_ESTR_SERIES_PAGE_URL = (
    "https://data.ecb.europa.eu/data/datasets/EST/EST.B.EU000A2X2A25.WT"
)
ECB_COPYRIGHT_URL = (
    "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html"
)
ECB_ESTR_REQUEST_PARAMETERS: Mapping[str, str] = {
    "detail": "full",
    "format": "csvdata",
    "includeHistory": "true",
}
ECB_ESTR_MAX_RESPONSE_BYTES = 16 * 1024 * 1024

_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HISTORY_HEADER = (
    "KEY",
    "FREQ",
    "BENCHMARK_ITEM",
    "DATA_TYPE_EST",
    "TIME_PERIOD",
    "OBS_VALUE",
    "OBS_STATUS",
    "CONF_STATUS",
    "PRE_BREAK_VALUE",
    "COMMENT_OBS",
    "CALCUL_START_DATE",
    "CALCUL_END_DATE",
    "TIME_FORMAT",
    "BREAKS",
    "COMMENT_TS",
    "COMPILING_ORG",
    "COVERAGE",
    "DATA_COMP",
    "DECIMALS",
    "DISS_ORG",
    "PUBL_ECB",
    "PUBL_MU",
    "PUBL_PUBLIC",
    "TIME_PER_COLLECT",
    "TITLE",
    "TITLE_COMPL",
    "UNIT_INDEX_BASE",
    "UNIT_MEASURE",
    "UNIT_MULT",
    "ACTION",
    "VALID_FROM",
    "VALID_TO",
)
_EXPECTED_ROW_METADATA = {
    "KEY": ECB_ESTR_SERIES_IDENTIFIER,
    "FREQ": "B",
    "BENCHMARK_ITEM": ECB_ESTR_ISIN,
    "DATA_TYPE_EST": "WT",
    "CONF_STATUS": "F",
    "TIME_FORMAT": "P1D",
    "DECIMALS": "3",
    "TIME_PER_COLLECT": "A",
    "UNIT_MEASURE": "PC",
    "UNIT_MULT": "0",
}
_ADMITTED_OBSERVATION_STATUSES = frozenset({"A", "R"})
_ADMITTED_ACTIONS = frozenset({"Replace"})


class EcbEstrError(RuntimeError):
    """Official €STR evidence is missing, malformed, conflicting, or unsafe."""


@dataclass(frozen=True, slots=True)
class EcbEstrAcquisitionReceipt:
    """Immutable transport provenance retained beside one raw ECB response."""

    receipt_schema_version: int
    request_url: str
    request_parameters: tuple[tuple[str, str], ...]
    effective_url: str
    retrieval_timestamp: str
    http_status: int
    response_content_type: str
    content_encoding: str
    content_length: int | None
    content_disposition: str
    last_modified: str
    etag: str | None
    byte_count: int
    raw_artifact_reference: str
    raw_artifact_sha256: str

    def __post_init__(self) -> None:
        if type(self.receipt_schema_version) is not int or self.receipt_schema_version != 1:
            raise EcbEstrError("unsupported ECB acquisition receipt schema version")
        if self.request_url != ECB_ESTR_MACHINE_URL:
            raise EcbEstrError("ECB request URL differs from the reviewed endpoint")
        if self.request_parameters != canonical_request_parameters(ECB_ESTR_REQUEST_PARAMETERS):
            raise EcbEstrError("ECB request parameters differ from the reviewed query")
        _nonempty_text(self.effective_url, "effective_url")
        assert isinstance(self.effective_url, str)
        _validate_effective_url(self.effective_url)
        _aware_timestamp(self.retrieval_timestamp, "retrieval_timestamp")
        if type(self.http_status) is not int or self.http_status != 200:
            raise EcbEstrError("only ECB HTTP 200 evidence may be retained")
        _nonempty_text(self.response_content_type, "response_content_type")
        assert isinstance(self.response_content_type, str)
        if _media_type(self.response_content_type) != "text/csv":
            raise EcbEstrError("ECB response content type must be text/csv")
        if not isinstance(self.content_encoding, str) or self.content_encoding not in {
            "",
            "identity",
        }:
            raise EcbEstrError("ECB response content encoding must be absent or identity")
        if self.content_length is not None and (
            type(self.content_length) is not int or self.content_length <= 0
        ):
            raise EcbEstrError("ECB Content-Length must be a positive integer when present")
        _nonempty_text(self.content_disposition, "content_disposition")
        last_modified = _http_date(self.last_modified, "last_modified")
        if last_modified > _aware_timestamp(self.retrieval_timestamp, "retrieval_timestamp"):
            raise EcbEstrError("ECB Last-Modified follows the retrieval timestamp")
        if self.etag is not None:
            _nonempty_text(self.etag, "etag")
        if type(self.byte_count) is not int or not 0 < self.byte_count <= ECB_ESTR_MAX_RESPONSE_BYTES:
            raise EcbEstrError("ECB response byte count is outside the admitted bound")
        if self.content_length is not None and self.content_length != self.byte_count:
            raise EcbEstrError("ECB Content-Length differs from retained byte count")
        _relative_artifact_reference(self.raw_artifact_reference)
        _sha256_text(self.raw_artifact_sha256, "raw_artifact_sha256")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> EcbEstrAcquisitionReceipt:
        expected = {
            "byte_count",
            "content_disposition",
            "content_encoding",
            "content_length",
            "effective_url",
            "etag",
            "http_status",
            "last_modified",
            "raw_artifact_reference",
            "raw_artifact_sha256",
            "receipt_schema_version",
            "request_parameters",
            "request_url",
            "response_content_type",
            "retrieval_timestamp",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise EcbEstrError("ECB acquisition receipt fields differ from the contract")
        parameters = value["request_parameters"]
        if not isinstance(parameters, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in parameters.items()
        ):
            raise EcbEstrError("ECB receipt request_parameters must be string-to-string")
        data = dict(value)
        data["request_parameters"] = canonical_request_parameters(parameters)
        return cls(**data)  # type: ignore[arg-type]

    def canonical_payload(self) -> dict[str, object]:
        return {
            "byte_count": self.byte_count,
            "content_disposition": self.content_disposition,
            "content_encoding": self.content_encoding,
            "content_length": self.content_length,
            "effective_url": self.effective_url,
            "etag": self.etag,
            "http_status": self.http_status,
            "last_modified": self.last_modified,
            "raw_artifact_reference": self.raw_artifact_reference,
            "raw_artifact_sha256": self.raw_artifact_sha256,
            "receipt_schema_version": self.receipt_schema_version,
            "request_parameters": dict(self.request_parameters),
            "request_url": self.request_url,
            "response_content_type": self.response_content_type,
            "retrieval_timestamp": self.retrieval_timestamp,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class ParsedEcbEstrVersion:
    """One provider-versioned CSV observation before database identities are assigned."""

    observation_date: date
    publication_date: date
    rate: Decimal
    observation_status: str
    confidentiality_status: str
    action: str
    valid_from: str
    valid_to: str | None

    @property
    def rate_decimal(self) -> str:
        return _canonical_decimal(self.rate)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "action": self.action,
            "confidentiality_status": self.confidentiality_status,
            "observation_date": self.observation_date.isoformat(),
            "observation_status": self.observation_status,
            "publication_date": self.publication_date.isoformat(),
            "rate_decimal": self.rate_decimal,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }


@dataclass(frozen=True, slots=True)
class ParsedEcbEstrDataset:
    """Canonical semantic interpretation of one immutable ECB CSV artifact."""

    versions: tuple[ParsedEcbEstrVersion, ...]

    def __post_init__(self) -> None:
        if not self.versions:
            raise EcbEstrError("ECB €STR dataset has no observations")
        ordered = tuple(sorted(self.versions, key=lambda item: (item.observation_date, item.valid_from)))
        if ordered != self.versions:
            raise EcbEstrError("ECB €STR versions must be canonically ordered")

    @property
    def observation_count(self) -> int:
        return len({item.observation_date for item in self.versions})

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def first_observation_date(self) -> date:
        return self.versions[0].observation_date

    @property
    def last_observation_date(self) -> date:
        return self.versions[-1].observation_date

    def canonical_payload(self) -> dict[str, object]:
        return {
            "adapter_schema_version": ECB_ESTR_ADAPTER_SCHEMA_VERSION,
            "dataflow": ECB_ESTR_DATAFLOW,
            "dataflow_reference": ECB_ESTR_DATAFLOW_REFERENCE,
            "decimals": 3,
            "dsd_reference": ECB_ESTR_DSD_REFERENCE,
            "frequency": "B",
            "isin": ECB_ESTR_ISIN,
            "series_identifier": ECB_ESTR_SERIES_IDENTIFIER,
            "series_key": ECB_ESTR_SERIES_KEY,
            "time_format": "P1D",
            "time_per_collect": "A",
            "unit_measure": "PC",
            "unit_multiplier": 0,
            "versions": [item.canonical_payload() for item in self.versions],
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class EcbEstrImportResult:
    """Deterministic result of one transactional offline import."""

    definition_fingerprint: str
    source_fingerprint: str
    manifest_fingerprint: str
    dataset_fingerprint: str
    receipt_fingerprint: str
    observation_count: int
    version_count: int
    first_observation_date: str
    last_observation_date: str
    inserted_rows: int
    reused: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_fingerprint": self.dataset_fingerprint,
            "definition_fingerprint": self.definition_fingerprint,
            "first_observation_date": self.first_observation_date,
            "inserted_rows": self.inserted_rows,
            "last_observation_date": self.last_observation_date,
            "manifest_fingerprint": self.manifest_fingerprint,
            "observation_count": self.observation_count,
            "receipt_fingerprint": self.receipt_fingerprint,
            "reused": self.reused,
            "source_fingerprint": self.source_fingerprint,
            "version_count": self.version_count,
        }


def ecb_estr_definition() -> ReferenceRateDefinition:
    """Return the reviewed immutable definition for the official €STR series."""
    return ReferenceRateDefinition(
        contract_schema_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id=ECB_ESTR_BENCHMARK_ID,
        benchmark_name="€STR",
        currency_code="EUR",
        administrator="European Central Bank",
        series_identifier=ECB_ESTR_SERIES_IDENTIFIER,
        rate_units="PERCENT_PER_ANNUM",
        day_count_convention="ACT_360",
        compounding_convention="SIMPLE_ACT_360_OVERNIGHT",
        definition_version="1.0.0",
    )


def ecb_estr_source() -> ReferenceRateSource:
    """Return the reviewed official ECB Data Portal source contract."""
    return ReferenceRateSource(
        source_code="ECB_DATA_API_ESTR",
        benchmark_id=ECB_ESTR_BENCHMARK_ID,
        source_organization="European Central Bank",
        official_page_url=ECB_ESTR_OFFICIAL_PAGE_URL,
        machine_readable_url=ECB_ESTR_MACHINE_URL,
        response_format="CSV_SDMX_2_1_HISTORY",
        source_role="OFFICIAL_ADMINISTRATOR",
        authentication_requirement="NONE",
        automated_use_status="PERMITTED",
        licensing_reference=ECB_COPYRIGHT_URL,
        raw_retention_status="PERMITTED",
    )


def validate_ecb_estr_policy(policy: CapitalDefensiveConstructionPolicy) -> None:
    """Bind the adapter to the unchanged reviewed construction policy."""
    validate_policy_binding(ecb_estr_definition(), ecb_estr_source(), policy)


def load_ecb_estr_receipt(path: Path) -> EcbEstrAcquisitionReceipt:
    """Load a strict duplicate-key-free receipt without contacting the provider."""
    if path.is_symlink():
        raise EcbEstrError("ECB acquisition receipt must not be a symlink")
    resolved = path.resolve()
    if not resolved.is_file():
        raise EcbEstrError("ECB acquisition receipt must be a regular non-symlink file")
    try:
        receipt_text = resolved.read_text(encoding="utf-8")
        value = json.loads(receipt_text, object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EcbEstrError("ECB acquisition receipt is unreadable or malformed") from error
    if not isinstance(value, Mapping):
        raise EcbEstrError("ECB acquisition receipt must be a JSON object")
    receipt = EcbEstrAcquisitionReceipt.from_mapping(value)
    if receipt_text != receipt_json(receipt):
        raise EcbEstrError("ECB acquisition receipt is not canonical JSON")
    return receipt


def receipt_json(receipt: EcbEstrAcquisitionReceipt) -> str:
    """Serialize an acquisition receipt deterministically for immutable retention."""
    return json.dumps(receipt.canonical_payload(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def parse_ecb_estr_csv(raw_bytes: bytes) -> ParsedEcbEstrDataset:
    """Parse strict official history CSV bytes with no interpolation or inference."""
    if not isinstance(raw_bytes, bytes) or not 0 < len(raw_bytes) <= ECB_ESTR_MAX_RESPONSE_BYTES:
        raise EcbEstrError("ECB CSV byte size is empty or outside the admitted bound")
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EcbEstrError("ECB CSV is not strict UTF-8") from error
    if text.startswith("\ufeff") or "\x00" in text:
        raise EcbEstrError("ECB CSV contains a prohibited BOM or NUL byte")
    try:
        rows = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = tuple(next(rows))
    except (StopIteration, csv.Error) as error:
        raise EcbEstrError("ECB CSV is empty or malformed") from error
    if header != _HISTORY_HEADER or len(set(header)) != len(header):
        raise EcbEstrError("ECB CSV header differs from the reviewed history contract")
    positions = {name: index for index, name in enumerate(header)}
    versions: list[ParsedEcbEstrVersion] = []
    seen_versions: set[tuple[date, str]] = set()
    try:
        for line_number, row in enumerate(rows, start=2):
            if len(row) != len(header):
                raise EcbEstrError(f"ECB CSV row {line_number} has an unexpected field count")
            values = {name: row[index] for name, index in positions.items()}
            for field, expected in _EXPECTED_ROW_METADATA.items():
                if values[field] != expected:
                    raise EcbEstrError(
                        f"ECB CSV row {line_number} has unexpected {field} metadata"
                    )
            if values["OBS_STATUS"] not in _ADMITTED_OBSERVATION_STATUSES:
                raise EcbEstrError(f"ECB CSV row {line_number} has an unadmitted observation status")
            if values["ACTION"] not in _ADMITTED_ACTIONS:
                raise EcbEstrError(f"ECB CSV row {line_number} has an unadmitted history action")
            observation_date = _strict_date(values["TIME_PERIOD"], "TIME_PERIOD")
            rate = _strict_decimal(values["OBS_VALUE"])
            valid_from, publication_date = _provider_timestamp(
                values["VALID_FROM"], "VALID_FROM"
            )
            valid_to_value = values["VALID_TO"]
            valid_to: str | None = None
            if valid_to_value:
                valid_to, _ = _provider_timestamp(valid_to_value, "VALID_TO")
                if _timestamp_sort_value(valid_to) <= _timestamp_sort_value(valid_from):
                    raise EcbEstrError("ECB VALID_TO must follow VALID_FROM")
            if publication_date < observation_date:
                raise EcbEstrError("ECB publication availability precedes its observation date")
            identity = (observation_date, valid_from)
            if identity in seen_versions:
                raise EcbEstrError("ECB CSV contains a duplicate provider observation version")
            seen_versions.add(identity)
            versions.append(
                ParsedEcbEstrVersion(
                    observation_date=observation_date,
                    publication_date=publication_date,
                    rate=rate,
                    observation_status=values["OBS_STATUS"],
                    confidentiality_status=values["CONF_STATUS"],
                    action=values["ACTION"],
                    valid_from=valid_from,
                    valid_to=valid_to,
                )
            )
    except csv.Error as error:
        raise EcbEstrError("ECB CSV quoting or row structure is malformed") from error
    if not versions:
        raise EcbEstrError("ECB CSV has a header but no observations")
    ordered = tuple(sorted(versions, key=lambda item: (item.observation_date, item.valid_from)))
    _validate_version_chains(ordered)
    return ParsedEcbEstrDataset(ordered)


def verified_ecb_estr_artifact(
    *, repository_root: Path, raw_artifact: Path, receipt: EcbEstrAcquisitionReceipt
) -> tuple[bytes, ParsedEcbEstrDataset]:
    """Verify path, bytes, hash, size, and receipt before an offline import."""
    root = repository_root.resolve()
    if raw_artifact.is_symlink():
        raise EcbEstrError("ECB raw artifact must not be a symlink")
    raw = raw_artifact.resolve()
    if not raw.is_file():
        raise EcbEstrError("ECB raw artifact must be a regular non-symlink file")
    expected = (root / PurePosixPath(receipt.raw_artifact_reference)).resolve()
    if raw != expected or not raw.is_relative_to(root / "data" / "raw" / "reference_rates"):
        raise EcbEstrError("ECB raw artifact path differs from immutable receipt provenance")
    data = raw.read_bytes()
    if len(data) != receipt.byte_count:
        raise EcbEstrError("ECB raw artifact byte count differs from its receipt")
    if hashlib.sha256(data).hexdigest() != receipt.raw_artifact_sha256:
        raise EcbEstrError("ECB raw artifact SHA-256 differs from its receipt")
    dataset = parse_ecb_estr_csv(data)
    retrieval_timestamp = _aware_timestamp(
        receipt.retrieval_timestamp, "retrieval_timestamp"
    )
    if any(
        _timestamp_sort_value(item.valid_from) > retrieval_timestamp
        or (
            item.valid_to is not None
            and _timestamp_sort_value(item.valid_to) > retrieval_timestamp
        )
        for item in dataset.versions
    ):
        raise EcbEstrError("ECB dataset contains evidence unavailable at retrieval time")
    return data, dataset


def import_ecb_estr_evidence(
    *,
    target: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    policy: CapitalDefensiveConstructionPolicy,
    failure_hook: Callable[[str], None] | None = None,
) -> EcbEstrImportResult:
    """Import one fully validated artifact atomically; exact repeats are no-ops."""
    if target.is_symlink():
        raise EcbEstrError("ECB import target must not be a symlink")
    target = target.resolve()
    if not target.is_file():
        raise EcbEstrError("ECB import target must be a regular SQLite file")
    validate_ecb_estr_policy(policy)
    receipt = load_ecb_estr_receipt(receipt_path)
    _validate_receipt_location(repository_root, raw_artifact, receipt_path, receipt)
    _, dataset = verified_ecb_estr_artifact(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt=receipt,
    )
    definition = ecb_estr_definition()
    source = ecb_estr_source()
    manifest = _manifest(receipt, source, dataset)
    observations = _observation_contracts(dataset, source, manifest)
    try:
        with connect(target) as connection:
            _validate_current_reference_rate_schema(connection)
            _validated_reference_rate_schema_contract(connection)
            existing = _reference_counts(connection)
            if any(existing.values()):
                _require_existing_bundle(
                    connection,
                    definition=definition,
                    source=source,
                    manifest=manifest,
                    observations=observations,
                )
                return _import_result(
                    definition, source, manifest, dataset, receipt, inserted_rows=0, reused=True
                )
            with transaction(connection):
                definition_id = _insert_definition(connection, definition)
                _call_hook(failure_hook, "after_definition")
                source_id = _insert_source(connection, definition_id, source)
                _call_hook(failure_hook, "after_source")
                manifest_id = _insert_manifest(
                    connection, definition_id, source_id, manifest
                )
                _call_hook(failure_hook, "after_manifest")
                _insert_observations(
                    connection,
                    definition_id=definition_id,
                    source_id=source_id,
                    manifest_id=manifest_id,
                    observations=observations,
                    failure_hook=failure_hook,
                )
                _require_existing_bundle(
                    connection,
                    definition=definition,
                    source=source,
                    manifest=manifest,
                    observations=observations,
                )
                _validate_current_reference_rate_schema(connection)
                _call_hook(failure_hook, "before_commit")
    except (sqlite3.Error, ReferenceRateContractError, SchemaVersionError) as error:
        raise EcbEstrError("ECB €STR persistence failed closed") from error
    inserted_rows = 3 + len(observations)
    return _import_result(
        definition,
        source,
        manifest,
        dataset,
        receipt,
        inserted_rows=inserted_rows,
        reused=False,
    )


def validate_ecb_estr_database(
    *,
    target: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    policy: CapitalDefensiveConstructionPolicy,
) -> dict[str, object]:
    """Validate installed Phase B evidence read-only and return deterministic audit data."""
    if target.is_symlink():
        raise EcbEstrError("ECB validation target must not be a symlink")
    target = target.resolve()
    if not target.is_file():
        raise EcbEstrError("ECB validation target must be a regular SQLite file")
    before = _file_sha256(target)
    validate_ecb_estr_policy(policy)
    receipt = load_ecb_estr_receipt(receipt_path)
    _validate_receipt_location(repository_root, raw_artifact, receipt_path, receipt)
    _, dataset = verified_ecb_estr_artifact(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt=receipt,
    )
    definition = ecb_estr_definition()
    source = ecb_estr_source()
    manifest = _manifest(receipt, source, dataset)
    observations = _observation_contracts(dataset, source, manifest)
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        _validate_current_reference_rate_schema(connection)
        schema_contract = _validated_reference_rate_schema_contract(connection)
        _require_existing_bundle(
            connection,
            definition=definition,
            source=source,
            manifest=manifest,
            observations=observations,
        )
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != ("ok",) or violations:
            raise EcbEstrError("ECB database integrity or foreign-key validation failed")
        row_counts = _reference_counts(connection)
        constructed_counts = _constructed_counts(connection)
        if any(constructed_counts.values()):
            raise EcbEstrError("production contains a constructed shortlist portfolio")
        schema_contract_fingerprint = canonical_fingerprint(schema_contract)
    after = _file_sha256(target)
    if before != after:
        raise EcbEstrError("read-only ECB validation changed the database bytes")
    status_counts: dict[str, int] = defaultdict(int)
    for item in dataset.versions:
        status_counts[item.observation_status] += 1
    return {
        "adapter_schema_version": ECB_ESTR_ADAPTER_SCHEMA_VERSION,
        "benchmark": {
            "administrator": "European Central Bank",
            "benchmark_id": ECB_ESTR_BENCHMARK_ID,
            "compounding_convention": definition.compounding_convention,
            "currency": "EUR",
            "dataflow": ECB_ESTR_DATAFLOW,
            "dataflow_reference": ECB_ESTR_DATAFLOW_REFERENCE,
            "day_count_convention": definition.day_count_convention,
            "decimals": 3,
            "dsd_reference": ECB_ESTR_DSD_REFERENCE,
            "frequency": "B",
            "isin": ECB_ESTR_ISIN,
            "rate_units": definition.rate_units,
            "series_identifier": ECB_ESTR_SERIES_IDENTIFIER,
            "series_key": ECB_ESTR_SERIES_KEY,
            "unit_measure": "PC",
            "unit_multiplier": 0,
        },
        "constructed_portfolio_row_counts": constructed_counts,
        "database_sha256": before,
        "dataset_fingerprint": dataset.fingerprint,
        "definition_fingerprint": definition.fingerprint,
        "evidence_status": "EUR_ESTR_ADMITTED_VALIDATED",
        "feature_contract_fingerprint": REFERENCE_RATE_FEATURE_FINGERPRINT,
        "feature_id": REFERENCE_RATE_FEATURE_ID,
        "feature_revision": REFERENCE_RATE_FEATURE_REVISION,
        "first_observation_date": dataset.first_observation_date.isoformat(),
        "foreign_key_violations": 0,
        "integrity_check": "ok",
        "last_observation_date": dataset.last_observation_date.isoformat(),
        "manifest_fingerprint": manifest.fingerprint,
        "milestone_11_runtime": "IMPLEMENTED_BLOCKED_BY_DATA",
        "milestone_12": "NO_GO",
        "milestone_13": "NO_GO",
        "observation_count": dataset.observation_count,
        "observation_status_counts": dict(sorted(status_counts.items())),
        "official_source": {
            "machine_readable_url": source.machine_readable_url,
            "official_page_url": source.official_page_url,
            "provider_dataset_version": receipt.last_modified,
            "request_parameters": dict(receipt.request_parameters),
            "response_content_type": receipt.response_content_type,
            "retrieval_timestamp": receipt.retrieval_timestamp,
        },
        "production_cutover": "NOT_AUTHORIZED",
        "raw_artifact": {
            "byte_count": receipt.byte_count,
            "reference": receipt.raw_artifact_reference,
            "sha256": receipt.raw_artifact_sha256,
        },
        "receipt_artifact": {
            "reference": receipt_path.resolve().relative_to(repository_root.resolve()).as_posix(),
            "sha256": _file_sha256(receipt_path.resolve()),
        },
        "receipt_fingerprint": receipt.fingerprint,
        "reference_rate_row_counts": row_counts,
        "reference_rate_runtime_admission": "NO_GO",
        "schema_contract_fingerprint": schema_contract_fingerprint,
        "source_fingerprint": source.fingerprint,
        "status": "PASS",
        "version_count": dataset.version_count,
    }


def _manifest(
    receipt: EcbEstrAcquisitionReceipt,
    source: ReferenceRateSource,
    dataset: ParsedEcbEstrDataset,
) -> ReferenceRateImportManifest:
    identity = internal_evidence_identity(
        source_contract_fingerprint=source.fingerprint,
        retrieval_timestamp=receipt.retrieval_timestamp,
        request_url=receipt.request_url,
        request_parameters=receipt.request_parameters,
        response_content_type=receipt.response_content_type,
        http_status=receipt.http_status,
        raw_artifact_reference=receipt.raw_artifact_reference,
        raw_artifact_sha256=receipt.raw_artifact_sha256,
    )
    return ReferenceRateImportManifest(
        provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        source_contract_fingerprint=source.fingerprint,
        retrieval_timestamp=receipt.retrieval_timestamp,
        request_url=receipt.request_url,
        request_parameters=receipt.request_parameters,
        response_content_type=receipt.response_content_type,
        http_status=receipt.http_status,
        raw_artifact_reference=receipt.raw_artifact_reference,
        raw_artifact_sha256=receipt.raw_artifact_sha256,
        provider_dataset_version=receipt.last_modified,
        provider_dataset_version_source_field="HTTP_LAST_MODIFIED",
        internal_evidence_identity_scheme="SYSTEM_CANONICAL_ARTIFACT_V1",
        internal_evidence_identity=identity,
        import_status="VALIDATED_ADMITTED",
        dataset_fingerprint=dataset.fingerprint,
    )


def _observation_contracts(
    dataset: ParsedEcbEstrDataset,
    source: ReferenceRateSource,
    manifest: ReferenceRateImportManifest,
) -> tuple[ReferenceRateObservation, ...]:
    result: list[ReferenceRateObservation] = []
    predecessor_by_date: dict[date, str] = {}
    sequence_by_date: dict[date, int] = defaultdict(int)
    for version in dataset.versions:
        sequence_by_date[version.observation_date] += 1
        sequence = sequence_by_date[version.observation_date]
        predecessor = predecessor_by_date.get(version.observation_date)
        observation = ReferenceRateObservation(
            provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
            benchmark_id=ECB_ESTR_BENCHMARK_ID,
            source_contract_fingerprint=source.fingerprint,
            import_manifest_fingerprint=manifest.fingerprint,
            observation_date=version.observation_date,
            provider_publication_date=version.publication_date,
            rate=version.rate,
            provider_revision_id=version.valid_from,
            provider_revision_id_source_field="VALID_FROM",
            provider_revision_indicator=version.observation_status,
            provider_revision_indicator_source_field="OBS_STATUS",
            provider_revision_status=(
                "PROVIDER_EXPLICIT_REVISION"
                if version.observation_status == "R"
                else "PROVIDER_EXPLICIT_NO_REVISION"
            ),
            provider_revision_contract_id=None,
            provider_revision_contract_version=None,
            provider_revision_contract_revision_indicator_value=None,
            provider_revision_contract_authoritative_reference=None,
            provider_revision_contract_fingerprint=None,
            provider_publication_value=version.valid_from,
            provider_publication_value_kind="TIMESTAMP",
            provider_publication_source_field="VALID_FROM",
            availability_basis="PROVIDER_REPORTED",
            availability_boundary_utc=canonical_utc_timestamp(version.valid_from),
            availability_derivation_rule_id=None,
            availability_derivation_rule_version=None,
            availability_policy_reference=None,
            availability_calendar_id=None,
            availability_calendar_version=None,
            availability_calendar_fingerprint=None,
            revision_sequence=sequence,
            supersedes_observation_fingerprint=predecessor,
            is_current=version.valid_to is None,
            quality_status="ADMITTED_VALIDATED",
        )
        validate_observation_availability(observation, manifest)
        predecessor_by_date[version.observation_date] = observation.fingerprint
        result.append(observation)
    return tuple(result)


def _insert_definition(connection: sqlite3.Connection, item: ReferenceRateDefinition) -> int:
    cursor = connection.execute(
        """INSERT INTO reference_rate_definition (
               contract_schema_version, benchmark_id, benchmark_name, currency_code,
               administrator, series_identifier, rate_units, day_count_convention,
               compounding_convention, definition_version, definition_fingerprint
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item.contract_schema_version,
            item.benchmark_id,
            item.benchmark_name,
            item.currency_code,
            item.administrator,
            item.series_identifier,
            item.rate_units,
            item.day_count_convention,
            item.compounding_convention,
            item.definition_version,
            item.fingerprint,
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _insert_source(
    connection: sqlite3.Connection, definition_id: int, item: ReferenceRateSource
) -> int:
    cursor = connection.execute(
        """INSERT INTO reference_rate_source (
               reference_rate_definition_id, source_code, source_organization,
               official_page_url, machine_readable_url, response_format, source_role,
               authentication_requirement, automated_use_status, licensing_reference,
               raw_retention_status, source_contract_fingerprint
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            definition_id,
            item.source_code,
            item.source_organization,
            item.official_page_url,
            item.machine_readable_url,
            item.response_format,
            item.source_role,
            item.authentication_requirement,
            item.automated_use_status,
            item.licensing_reference,
            item.raw_retention_status,
            item.fingerprint,
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _insert_manifest(
    connection: sqlite3.Connection,
    definition_id: int,
    source_id: int,
    item: ReferenceRateImportManifest,
) -> int:
    cursor = connection.execute(
        """INSERT INTO reference_rate_import_manifest (
               provenance_contract_version, reference_rate_source_id,
               reference_rate_definition_id, retrieval_timestamp,
               request_url, request_parameters_json, response_content_type, http_status,
               raw_artifact_reference, raw_artifact_sha256, provider_dataset_version,
               provider_dataset_version_source_field,
               internal_evidence_identity_scheme, internal_evidence_identity,
               import_status, dataset_fingerprint, manifest_fingerprint
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item.provenance_contract_version,
            source_id,
            definition_id,
            item.retrieval_timestamp,
            item.request_url,
            canonical_json(dict(item.request_parameters)),
            item.response_content_type,
            item.http_status,
            item.raw_artifact_reference,
            item.raw_artifact_sha256,
            item.provider_dataset_version,
            item.provider_dataset_version_source_field,
            item.internal_evidence_identity_scheme,
            item.internal_evidence_identity,
            item.import_status,
            item.dataset_fingerprint,
            item.fingerprint,
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _insert_observations(
    connection: sqlite3.Connection,
    *,
    definition_id: int,
    source_id: int,
    manifest_id: int,
    observations: Sequence[ReferenceRateObservation],
    failure_hook: Callable[[str], None] | None,
) -> None:
    database_id_by_fingerprint: dict[str, int] = {}
    for index, item in enumerate(observations):
        predecessor_id = (
            database_id_by_fingerprint[item.supersedes_observation_fingerprint]
            if item.supersedes_observation_fingerprint is not None
            else None
        )
        cursor = connection.execute(
            """INSERT INTO reference_rate_observation (
                   provenance_contract_version, reference_rate_definition_id,
                   reference_rate_source_id, reference_rate_import_manifest_id,
                   observation_date, provider_publication_date, rate_decimal,
                   provider_revision_id, provider_revision_id_source_field,
                   provider_revision_indicator, provider_revision_indicator_source_field,
                   provider_revision_status, provider_revision_contract_id,
                   provider_revision_contract_version,
                   provider_revision_contract_revision_indicator_value,
                   provider_revision_contract_authoritative_reference,
                   provider_revision_contract_fingerprint, provider_publication_value,
                   provider_publication_value_kind, provider_publication_source_field,
                   availability_basis, availability_boundary_utc,
                   availability_derivation_rule_id,
                   availability_derivation_rule_version,
                   availability_policy_reference, availability_calendar_id,
                   availability_calendar_version, availability_calendar_fingerprint,
                   revision_sequence,
                   supersedes_observation_id, is_current, quality_status,
                   observation_fingerprint
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                         ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.provenance_contract_version,
                definition_id,
                source_id,
                manifest_id,
                item.observation_date.isoformat(),
                (
                    item.provider_publication_date.isoformat()
                    if item.provider_publication_date is not None
                    else None
                ),
                item.rate_decimal,
                item.provider_revision_id,
                item.provider_revision_id_source_field,
                item.provider_revision_indicator,
                item.provider_revision_indicator_source_field,
                item.provider_revision_status,
                item.provider_revision_contract_id,
                item.provider_revision_contract_version,
                item.provider_revision_contract_revision_indicator_value,
                item.provider_revision_contract_authoritative_reference,
                item.provider_revision_contract_fingerprint,
                item.provider_publication_value,
                item.provider_publication_value_kind,
                item.provider_publication_source_field,
                item.availability_basis,
                item.availability_boundary_utc,
                item.availability_derivation_rule_id,
                item.availability_derivation_rule_version,
                item.availability_policy_reference,
                item.availability_calendar_id,
                item.availability_calendar_version,
                item.availability_calendar_fingerprint,
                item.revision_sequence,
                predecessor_id,
                int(item.is_current),
                item.quality_status,
                item.fingerprint,
            ),
        )
        assert cursor.lastrowid is not None
        database_id_by_fingerprint[item.fingerprint] = int(cursor.lastrowid)
        if index == 0:
            _call_hook(failure_hook, "after_first_observation")
        if index == len(observations) // 2:
            _call_hook(failure_hook, "after_middle_observation")
    _call_hook(failure_hook, "after_observations")


def _require_existing_bundle(
    connection: sqlite3.Connection,
    *,
    definition: ReferenceRateDefinition,
    source: ReferenceRateSource,
    manifest: ReferenceRateImportManifest,
    observations: Sequence[ReferenceRateObservation],
) -> None:
    definition_rows = connection.execute(
        "SELECT * FROM reference_rate_definition ORDER BY reference_rate_definition_id"
    ).fetchall()
    source_rows = connection.execute(
        "SELECT * FROM reference_rate_source ORDER BY reference_rate_source_id"
    ).fetchall()
    manifest_rows = connection.execute(
        "SELECT * FROM reference_rate_import_manifest ORDER BY reference_rate_import_manifest_id"
    ).fetchall()
    observation_rows = connection.execute(
        """SELECT * FROM reference_rate_observation
           ORDER BY observation_date, revision_sequence, reference_rate_observation_id"""
    ).fetchall()
    if (
        len(definition_rows) != 1
        or len(source_rows) != 1
        or len(manifest_rows) != 1
        or len(observation_rows) != len(observations)
    ):
        raise EcbEstrError("reference-rate tables contain missing or extra evidence rows")
    definition_row = definition_rows[0]
    actual_definition = ReferenceRateDefinition(
        contract_schema_version=_stored_integer(
            definition_row["contract_schema_version"], "contract_schema_version"
        ),
        benchmark_id=str(definition_row["benchmark_id"]),
        benchmark_name=str(definition_row["benchmark_name"]),
        currency_code=str(definition_row["currency_code"]),
        administrator=str(definition_row["administrator"]),
        series_identifier=str(definition_row["series_identifier"]),
        rate_units=str(definition_row["rate_units"]),
        day_count_convention=str(definition_row["day_count_convention"]),
        compounding_convention=str(definition_row["compounding_convention"]),
        definition_version=str(definition_row["definition_version"]),
    )
    if actual_definition != definition or definition_row["definition_fingerprint"] != definition.fingerprint:
        raise EcbEstrError("stored ECB definition conflicts with its reviewed contract")
    source_row = source_rows[0]
    actual_source = ReferenceRateSource(
        source_code=str(source_row["source_code"]),
        benchmark_id=actual_definition.benchmark_id,
        source_organization=str(source_row["source_organization"]),
        official_page_url=str(source_row["official_page_url"]),
        machine_readable_url=str(source_row["machine_readable_url"]),
        response_format=str(source_row["response_format"]),
        source_role=str(source_row["source_role"]),
        authentication_requirement=str(source_row["authentication_requirement"]),
        automated_use_status=str(source_row["automated_use_status"]),
        licensing_reference=str(source_row["licensing_reference"]),
        raw_retention_status=str(source_row["raw_retention_status"]),
    )
    if (
        int(source_row["reference_rate_definition_id"])
        != int(definition_row["reference_rate_definition_id"])
        or actual_source != source
        or source_row["source_contract_fingerprint"] != source.fingerprint
    ):
        raise EcbEstrError("stored ECB source conflicts with its reviewed contract")
    manifest_row = manifest_rows[0]
    parameters = _strict_json_string_mapping(str(manifest_row["request_parameters_json"]))
    actual_manifest = ReferenceRateImportManifest(
        provenance_contract_version=_stored_integer(
            manifest_row["provenance_contract_version"], "provenance_contract_version"
        ),
        source_contract_fingerprint=actual_source.fingerprint,
        retrieval_timestamp=str(manifest_row["retrieval_timestamp"]),
        request_url=str(manifest_row["request_url"]),
        request_parameters=canonical_request_parameters(parameters),
        response_content_type=str(manifest_row["response_content_type"]),
        http_status=_stored_integer(manifest_row["http_status"], "http_status"),
        raw_artifact_reference=str(manifest_row["raw_artifact_reference"]),
        raw_artifact_sha256=str(manifest_row["raw_artifact_sha256"]),
        provider_dataset_version=(
            str(manifest_row["provider_dataset_version"])
            if manifest_row["provider_dataset_version"] is not None
            else None
        ),
        provider_dataset_version_source_field=(
            str(manifest_row["provider_dataset_version_source_field"])
            if manifest_row["provider_dataset_version_source_field"] is not None
            else None
        ),
        internal_evidence_identity_scheme=str(
            manifest_row["internal_evidence_identity_scheme"]
        ),
        internal_evidence_identity=str(manifest_row["internal_evidence_identity"]),
        import_status=str(manifest_row["import_status"]),
        dataset_fingerprint=str(manifest_row["dataset_fingerprint"]),
    )
    if (
        int(manifest_row["reference_rate_source_id"])
        != int(source_row["reference_rate_source_id"])
        or int(manifest_row["reference_rate_definition_id"])
        != int(definition_row["reference_rate_definition_id"])
        or actual_manifest != manifest
        or str(manifest_row["manifest_fingerprint"]) != actual_manifest.fingerprint
    ):
        raise EcbEstrError("stored ECB import manifest conflicts with retained provenance")
    fingerprint_by_id: dict[int, str] = {}
    reconstructed: list[ReferenceRateObservation] = []
    for row in observation_rows:
        predecessor_id = row["supersedes_observation_id"]
        predecessor = (
            fingerprint_by_id[int(predecessor_id)] if predecessor_id is not None else None
        )
        actual = ReferenceRateObservation(
            provenance_contract_version=_stored_integer(
                row["provenance_contract_version"], "provenance_contract_version"
            ),
            benchmark_id=actual_definition.benchmark_id,
            source_contract_fingerprint=actual_source.fingerprint,
            import_manifest_fingerprint=actual_manifest.fingerprint,
            observation_date=_strict_date(str(row["observation_date"]), "observation_date"),
            provider_publication_date=(
                _strict_date(
                    str(row["provider_publication_date"]), "provider_publication_date"
                )
                if row["provider_publication_date"] is not None
                else None
            ),
            rate=_strict_decimal(str(row["rate_decimal"])),
            provider_revision_id=(
                str(row["provider_revision_id"])
                if row["provider_revision_id"] is not None
                else None
            ),
            provider_revision_id_source_field=(
                str(row["provider_revision_id_source_field"])
                if row["provider_revision_id_source_field"] is not None
                else None
            ),
            provider_revision_indicator=(
                str(row["provider_revision_indicator"])
                if row["provider_revision_indicator"] is not None
                else None
            ),
            provider_revision_indicator_source_field=(
                str(row["provider_revision_indicator_source_field"])
                if row["provider_revision_indicator_source_field"] is not None
                else None
            ),
            provider_revision_status=str(row["provider_revision_status"]),
            provider_revision_contract_id=(
                str(row["provider_revision_contract_id"])
                if row["provider_revision_contract_id"] is not None
                else None
            ),
            provider_revision_contract_version=(
                str(row["provider_revision_contract_version"])
                if row["provider_revision_contract_version"] is not None
                else None
            ),
            provider_revision_contract_revision_indicator_value=(
                str(row["provider_revision_contract_revision_indicator_value"])
                if row["provider_revision_contract_revision_indicator_value"] is not None
                else None
            ),
            provider_revision_contract_authoritative_reference=(
                str(row["provider_revision_contract_authoritative_reference"])
                if row["provider_revision_contract_authoritative_reference"] is not None
                else None
            ),
            provider_revision_contract_fingerprint=(
                str(row["provider_revision_contract_fingerprint"])
                if row["provider_revision_contract_fingerprint"] is not None
                else None
            ),
            provider_publication_value=(
                str(row["provider_publication_value"])
                if row["provider_publication_value"] is not None
                else None
            ),
            provider_publication_value_kind=(
                str(row["provider_publication_value_kind"])
                if row["provider_publication_value_kind"] is not None
                else None
            ),
            provider_publication_source_field=(
                str(row["provider_publication_source_field"])
                if row["provider_publication_source_field"] is not None
                else None
            ),
            availability_basis=str(row["availability_basis"]),
            availability_boundary_utc=str(row["availability_boundary_utc"]),
            availability_derivation_rule_id=(
                str(row["availability_derivation_rule_id"])
                if row["availability_derivation_rule_id"] is not None
                else None
            ),
            availability_derivation_rule_version=(
                str(row["availability_derivation_rule_version"])
                if row["availability_derivation_rule_version"] is not None
                else None
            ),
            availability_policy_reference=(
                str(row["availability_policy_reference"])
                if row["availability_policy_reference"] is not None
                else None
            ),
            availability_calendar_id=(
                str(row["availability_calendar_id"])
                if row["availability_calendar_id"] is not None
                else None
            ),
            availability_calendar_version=(
                str(row["availability_calendar_version"])
                if row["availability_calendar_version"] is not None
                else None
            ),
            availability_calendar_fingerprint=(
                str(row["availability_calendar_fingerprint"])
                if row["availability_calendar_fingerprint"] is not None
                else None
            ),
            revision_sequence=_stored_integer(row["revision_sequence"], "revision_sequence"),
            supersedes_observation_fingerprint=predecessor,
            is_current=_stored_boolean(row["is_current"], "is_current"),
            quality_status=str(row["quality_status"]),
        )
        validate_observation_availability(actual, actual_manifest)
        if str(row["observation_fingerprint"]) != actual.fingerprint:
            raise EcbEstrError("stored ECB observation fingerprint is invalid")
        fingerprint_by_id[int(row["reference_rate_observation_id"])] = actual.fingerprint
        reconstructed.append(actual)
    if tuple(reconstructed) != tuple(observations):
        raise EcbEstrError("stored ECB observations conflict with retained raw evidence")


def _validate_version_chains(versions: Sequence[ParsedEcbEstrVersion]) -> None:
    grouped: dict[date, list[ParsedEcbEstrVersion]] = defaultdict(list)
    for item in versions:
        grouped[item.observation_date].append(item)
    for observation_date, items in grouped.items():
        current = [item for item in items if item.valid_to is None]
        if len(current) != 1 or current[0] != items[-1]:
            raise EcbEstrError(
                f"ECB history for {observation_date.isoformat()} has no unique current version"
            )
        for previous, following in pairwise(items):
            if previous.valid_to is None:
                raise EcbEstrError("ECB historical version is missing VALID_TO")
            if _timestamp_sort_value(previous.valid_to) != _timestamp_sort_value(
                following.valid_from
            ):
                raise EcbEstrError("ECB historical version validity chain is not contiguous")


def _import_result(
    definition: ReferenceRateDefinition,
    source: ReferenceRateSource,
    manifest: ReferenceRateImportManifest,
    dataset: ParsedEcbEstrDataset,
    receipt: EcbEstrAcquisitionReceipt,
    *,
    inserted_rows: int,
    reused: bool,
) -> EcbEstrImportResult:
    return EcbEstrImportResult(
        definition_fingerprint=definition.fingerprint,
        source_fingerprint=source.fingerprint,
        manifest_fingerprint=manifest.fingerprint,
        dataset_fingerprint=dataset.fingerprint,
        receipt_fingerprint=receipt.fingerprint,
        observation_count=dataset.observation_count,
        version_count=dataset.version_count,
        first_observation_date=dataset.first_observation_date.isoformat(),
        last_observation_date=dataset.last_observation_date.isoformat(),
        inserted_rows=inserted_rows,
        reused=reused,
    )


def _reference_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
        for table in (
            "reference_rate_definition",
            "reference_rate_import_manifest",
            "reference_rate_observation",
            "reference_rate_source",
        )
    }


def _validated_reference_rate_schema_contract(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    from portfolio_advisor.database.migrations.reference_rate import (
        reference_rate_schema_contract,
    )

    actual = reference_rate_schema_contract(connection)
    with sqlite3.connect(":memory:") as scratch:
        scratch.row_factory = sqlite3.Row
        initialize_schema(scratch)
        expected = reference_rate_schema_contract(scratch)
    if actual != expected:
        raise EcbEstrError("reference-rate schema differs from the reviewed Phase A contract")
    return actual


def _validate_current_reference_rate_schema(connection: sqlite3.Connection) -> None:
    try:
        validate_schema(connection)
    except SchemaVersionError as error:
        raise EcbEstrError(
            "reference-rate schema differs from the reviewed Phase A contract and Phase C0 revision"
        ) from error


def _constructed_counts(connection: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "constructed_portfolio_holding_lineage": (
            "SELECT count(*) FROM constructed_portfolio_holding_lineage"
        ),
        "constructed_portfolio_metadata": "SELECT count(*) FROM constructed_portfolio_metadata",
        "shortlist_constructed_cash": (
            """SELECT count(*) FROM portfolio_cash pc
               JOIN portfolio_snapshot ps ON ps.portfolio_snapshot_id=pc.portfolio_snapshot_id
               JOIN portfolio p ON p.portfolio_id=ps.portfolio_id
               WHERE p.portfolio_type='SHORTLIST_CONSTRUCTED'"""
        ),
        "shortlist_constructed_holdings": (
            """SELECT count(*) FROM portfolio_holding ph
               JOIN portfolio_snapshot ps ON ps.portfolio_snapshot_id=ph.portfolio_snapshot_id
               JOIN portfolio p ON p.portfolio_id=ps.portfolio_id
               WHERE p.portfolio_type='SHORTLIST_CONSTRUCTED'"""
        ),
        "shortlist_constructed_portfolios": (
            "SELECT count(*) FROM portfolio WHERE portfolio_type='SHORTLIST_CONSTRUCTED'"
        ),
        "shortlist_constructed_snapshots": (
            """SELECT count(*) FROM portfolio_snapshot ps
               JOIN portfolio p ON p.portfolio_id=ps.portfolio_id
               WHERE p.portfolio_type='SHORTLIST_CONSTRUCTED'"""
        ),
    }
    return {name: int(connection.execute(sql).fetchone()[0]) for name, sql in queries.items()}


def _strict_json_string_mapping(value: str) -> dict[str, str]:
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, EcbEstrError) as error:
        raise EcbEstrError("stored request parameters JSON is malformed") from error
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in parsed.items()
    ):
        raise EcbEstrError("stored request parameters must be string-to-string")
    if canonical_json(parsed) != value:
        raise EcbEstrError("stored request parameters JSON is not canonical")
    return parsed


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EcbEstrError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_date(value: str, field: str) -> date:
    if _DATE.fullmatch(value) is None:
        raise EcbEstrError(f"{field} must use exact YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise EcbEstrError(f"{field} is not a valid date") from error
    if parsed.isoformat() != value:
        raise EcbEstrError(f"{field} is not canonical")
    return parsed


def _strict_decimal(value: str) -> Decimal:
    if _DECIMAL.fullmatch(value) is None:
        raise EcbEstrError("ECB rate must be a plain decimal with at most three places")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise EcbEstrError("ECB rate is not an exact Decimal") from error
    if not parsed.is_finite():
        raise EcbEstrError("ECB rate must be finite")
    return parsed


def _canonical_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _provider_timestamp(value: str, field: str) -> tuple[str, date]:
    if not value or value != value.strip() or len(value) < 11 or value[10] != "T":
        raise EcbEstrError(f"ECB {field} is missing or malformed")
    publication_date = _strict_date(value[:10], field)
    _aware_timestamp(value, field)
    return value, publication_date


def _timestamp_sort_value(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(candidate)


def _aware_timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EcbEstrError(f"{field} must be an exact timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise EcbEstrError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EcbEstrError(f"{field} must include a timezone")
    return parsed


def _validate_effective_url(value: str) -> None:
    parts = urlsplit(value)
    expected = urlsplit(ECB_ESTR_MACHINE_URL)
    if (
        parts.scheme != "https"
        or parts.netloc != expected.netloc
        or parts.path != expected.path
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise EcbEstrError("ECB effective URL differs from the reviewed endpoint")
    pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    if len(pairs) != len(dict(pairs)) or dict(pairs) != dict(ECB_ESTR_REQUEST_PARAMETERS):
        raise EcbEstrError("ECB effective URL query differs from reviewed parameters")


def _validate_receipt_location(
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    receipt: EcbEstrAcquisitionReceipt,
) -> None:
    if raw_artifact.is_symlink() or receipt_path.is_symlink():
        raise EcbEstrError("ECB retained evidence paths must not be symlinks")
    root = repository_root.resolve()
    raw = raw_artifact.resolve()
    receipt_file = receipt_path.resolve()
    expected_raw = (root / PurePosixPath(receipt.raw_artifact_reference)).resolve()
    expected_receipt = expected_raw.with_suffix(".receipt.json")
    if raw != expected_raw or receipt_file != expected_receipt:
        raise EcbEstrError("ECB raw artifact and receipt do not form the retained evidence pair")
    if not receipt_file.is_relative_to(root / "data" / "raw" / "reference_rates" / "ecb"):
        raise EcbEstrError("ECB receipt must stay under the approved raw evidence directory")


def _http_date(value: object, field: str) -> datetime:
    _nonempty_text(value, field)
    assert isinstance(value, str)
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as error:
        raise EcbEstrError(f"{field} must be an RFC 5322 HTTP date") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EcbEstrError(f"{field} must include a timezone")
    return parsed


def _stored_integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise EcbEstrError(f"stored {field} must be an exact SQLite integer")
    return value


def _stored_boolean(value: object, field: str) -> bool:
    integer = _stored_integer(value, field)
    if integer not in {0, 1}:
        raise EcbEstrError(f"stored {field} must be zero or one")
    return bool(integer)


def _media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _relative_artifact_reference(value: str) -> None:
    _nonempty_text(value, "raw_artifact_reference")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or path.parts[:4] != ("data", "raw", "reference_rates", "ecb")
    ):
        raise EcbEstrError("raw artifact reference must be within data/raw/reference_rates/ecb")


def _nonempty_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EcbEstrError(f"{field} must be an exact non-empty string")


def _sha256_text(value: object, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise EcbEstrError(f"{field} must be lowercase SHA-256")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _call_hook(hook: Callable[[str], None] | None, stage: str) -> None:
    if hook is not None:
        hook(stage)
