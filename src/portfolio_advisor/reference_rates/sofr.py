"""Strict offline contracts for official New York Fed daily SOFR evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlsplit

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.migrations.reference_rate import (
    reference_rate_schema_contract,
)
from portfolio_advisor.database.schema.v3 import (
    REFERENCE_RATE_FEATURE_FINGERPRINT,
    REFERENCE_RATE_FEATURE_ID,
    REFERENCE_RATE_FEATURE_REVISION,
    SchemaVersionError,
    connect,
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

SOFR_ADAPTER_SCHEMA_VERSION = 1
SOFR_BENCHMARK_ID = "SOFR"
SOFR_MACHINE_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"
SOFR_OFFICIAL_PAGE_URL = "https://www.newyorkfed.org/markets/reference-rates/sofr"
SOFR_ADDITIONAL_INFORMATION_URL = (
    "https://www.newyorkfed.org/markets/reference-rates/"
    "additional-information-about-reference-rates"
)
SOFR_API_DOCUMENTATION_URL = "https://markets.newyorkfed.org/static/docs/markets-api.html"
SOFR_TERMS_URL = "https://www.newyorkfed.org/privacy/termsofuse"
SOFR_REQUEST_PARAMETERS: Mapping[str, str] = {
    "startDate": "2018-04-02",
    "endDate": "2026-08-31",
    "type": "rate",
}
SOFR_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SOFR_EXPECTED_OBSERVATION_COUNT = 2102
SOFR_FIRST_OBSERVATION_DATE = date(2018, 4, 2)
SOFR_LAST_OBSERVATION_DATE = date(2026, 8, 31)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_BASE_ROW_FIELDS = {
    "effectiveDate",
    "percentPercentile1",
    "percentPercentile25",
    "percentPercentile75",
    "percentPercentile99",
    "percentRate",
    "revisionIndicator",
    "type",
}
_PERCENTILE_FIELDS = (
    "percentPercentile1",
    "percentPercentile25",
    "percentPercentile75",
    "percentPercentile99",
)


class SofrError(RuntimeError):
    """Official SOFR evidence is missing, malformed, conflicting, or unsafe."""


@dataclass(frozen=True, slots=True)
class ParsedSofrObservation:
    """One exact daily overnight SOFR wire record before database identities."""

    observation_date: date
    rate: Decimal
    percentiles: tuple[Decimal | str, Decimal | str, Decimal | str, Decimal | str]
    revision_indicator: str
    footnote_id: int | None

    @property
    def rate_decimal(self) -> str:
        return _decimal_text(self.rate)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "effective_date": self.observation_date.isoformat(),
            "footnote_id": self.footnote_id,
            "percent_rate": format(self.rate, "f"),
            "percentiles": [
                format(item, "f") if isinstance(item, Decimal) else item
                for item in self.percentiles
            ],
            "revision_indicator": self.revision_indicator,
            "type": SOFR_BENCHMARK_ID,
        }


@dataclass(frozen=True, slots=True)
class ParsedSofrDataset:
    """Canonical fixed-range official daily SOFR dataset."""

    observations: tuple[ParsedSofrObservation, ...]

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def first_observation_date(self) -> date:
        return self.observations[0].observation_date

    @property
    def last_observation_date(self) -> date:
        return self.observations[-1].observation_date

    def canonical_payload(self) -> dict[str, object]:
        return {
            "adapter_schema_version": SOFR_ADAPTER_SCHEMA_VERSION,
            "benchmark_id": SOFR_BENCHMARK_ID,
            "rate_units": "PERCENT_PER_ANNUM",
            "request_parameters": dict(
                canonical_request_parameters(SOFR_REQUEST_PARAMETERS)
            ),
            "wire_contract": "NYFED_SECURED_SOFR_SEARCH_JSON_PERCENT_RATE_V1",
            "observations": [item.canonical_payload() for item in self.observations],
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class PreparedSofrBundle:
    """Fully reconstructed contracts from retained SOFR evidence."""

    receipt: SofrAcquisitionReceipt
    dataset: ParsedSofrDataset
    definition: ReferenceRateDefinition
    source: ReferenceRateSource
    manifest: ReferenceRateImportManifest
    observations: tuple[ReferenceRateObservation, ...]


@dataclass(frozen=True, slots=True)
class SofrImportResult:
    """Deterministic outcome of one transactional offline SOFR import."""

    dataset_fingerprint: str
    definition_fingerprint: str
    source_fingerprint: str
    manifest_fingerprint: str
    receipt_fingerprint: str
    observation_count: int
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
        }


@dataclass(frozen=True, slots=True)
class SofrAcquisitionReceipt:
    """Immutable transport provenance retained beside one raw NY Fed response."""

    receipt_schema_version: int
    request_url: str
    request_parameters: tuple[tuple[str, str], ...]
    effective_url: str
    retrieval_timestamp: str
    http_status: int
    response_content_type: str
    content_encoding: str
    content_length: int | None
    response_date: str | None
    last_modified: str | None
    etag: str | None
    byte_count: int
    raw_artifact_reference: str
    raw_artifact_sha256: str

    def __post_init__(self) -> None:
        if type(self.receipt_schema_version) is not int or self.receipt_schema_version != 1:
            raise SofrError("unsupported SOFR acquisition receipt schema version")
        if self.request_url != SOFR_MACHINE_URL:
            raise SofrError("SOFR request URL differs from the reviewed endpoint")
        if self.request_parameters != canonical_request_parameters(SOFR_REQUEST_PARAMETERS):
            raise SofrError("SOFR request parameters differ from the fixed reviewed query")
        _text(self.effective_url, "effective_url")
        _validate_effective_url(self.effective_url)
        _aware_timestamp(self.retrieval_timestamp, "retrieval_timestamp")
        if type(self.http_status) is not int or self.http_status != 200:
            raise SofrError("only New York Fed HTTP 200 evidence may be retained")
        _text(self.response_content_type, "response_content_type")
        _validate_json_content_type(self.response_content_type)
        if not isinstance(self.content_encoding, str) or self.content_encoding not in {
            "",
            "identity",
        }:
            raise SofrError("SOFR response content encoding must be absent or identity")
        if self.content_length is not None and (
            type(self.content_length) is not int or self.content_length <= 0
        ):
            raise SofrError("SOFR Content-Length must be a positive integer when present")
        for value, field in (
            (self.response_date, "response_date"),
            (self.last_modified, "last_modified"),
            (self.etag, "etag"),
        ):
            if value is not None:
                _text(value, field)
        if type(self.byte_count) is not int or not 0 < self.byte_count <= SOFR_MAX_RESPONSE_BYTES:
            raise SofrError("SOFR response byte count is outside the admitted bound")
        if self.content_length is not None and self.content_length != self.byte_count:
            raise SofrError("SOFR Content-Length differs from retained byte count")
        _relative_artifact_reference(self.raw_artifact_reference)
        if _SHA256.fullmatch(self.raw_artifact_sha256) is None:
            raise SofrError("raw_artifact_sha256 must be a lowercase SHA-256")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SofrAcquisitionReceipt:
        expected = {
            "byte_count",
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
            "response_date",
            "retrieval_timestamp",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise SofrError("SOFR acquisition receipt fields differ from the contract")
        parameters = value["request_parameters"]
        if not isinstance(parameters, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in parameters.items()
        ):
            raise SofrError("SOFR receipt request_parameters must be string-to-string")
        data = dict(value)
        data["request_parameters"] = canonical_request_parameters(parameters)
        return cls(**data)  # type: ignore[arg-type]

    def canonical_payload(self) -> dict[str, object]:
        return {
            "byte_count": self.byte_count,
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
            "response_date": self.response_date,
            "retrieval_timestamp": self.retrieval_timestamp,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


def receipt_json(receipt: SofrAcquisitionReceipt) -> str:
    """Serialize one receipt canonically for tamper-evident offline reuse."""
    return canonical_json(receipt.canonical_payload()) + "\n"


def load_sofr_receipt(path: Path) -> SofrAcquisitionReceipt:
    """Load an exact canonical receipt and reject duplicate keys or altered bytes."""
    if path.is_symlink() or not path.is_file():
        raise SofrError("SOFR receipt must be a regular non-symlink file")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SofrError("SOFR receipt is not strict UTF-8") from error
    if text.startswith("\ufeff") or "\x00" in text:
        raise SofrError("SOFR receipt contains a prohibited BOM or NUL byte")
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except SofrError:
        raise
    except json.JSONDecodeError as error:
        raise SofrError("SOFR receipt is not strict JSON") from error
    if not isinstance(value, Mapping):
        raise SofrError("SOFR receipt root must be an object")
    receipt = SofrAcquisitionReceipt.from_mapping(value)
    if raw != receipt_json(receipt).encode("utf-8"):
        raise SofrError("SOFR receipt bytes are not canonical JSON")
    return receipt


def verified_sofr_artifact(
    *, repository_root: Path, raw_artifact: Path, receipt: SofrAcquisitionReceipt
) -> bytes:
    """Verify retained path, exact bytes, size, and hash before offline parsing."""
    root = _lexical_absolute(repository_root)
    raw = _lexical_absolute(raw_artifact)
    expected = _lexical_absolute(
        root / PurePosixPath(receipt.raw_artifact_reference)
    )
    approved = root / "data" / "raw" / "reference_rates" / "new_york_fed" / "sofr"
    if root.is_symlink() or _has_symlink_component(raw, root):
        raise SofrError("SOFR raw artifact path contains a symlink component")
    if not raw.is_file():
        raise SofrError("SOFR raw artifact must be a regular non-symlink file")
    if raw != expected or not raw.is_relative_to(approved):
        raise SofrError("SOFR raw artifact path differs from immutable receipt provenance")
    data = raw.read_bytes()
    if len(data) != receipt.byte_count:
        raise SofrError("SOFR raw artifact byte count differs from its receipt")
    if hashlib.sha256(data).hexdigest() != receipt.raw_artifact_sha256:
        raise SofrError("SOFR raw artifact SHA-256 differs from its receipt")
    return data


def parse_sofr_json(raw_bytes: bytes) -> ParsedSofrDataset:
    """Parse the fixed official response without float conversion or inference."""
    if not isinstance(raw_bytes, bytes) or not 0 < len(raw_bytes) <= SOFR_MAX_RESPONSE_BYTES:
        raise SofrError("SOFR JSON byte size is empty or outside the admitted bound")
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SofrError("SOFR JSON is not strict UTF-8") from error
    if text.startswith("\ufeff") or "\x00" in text:
        raise SofrError("SOFR JSON contains a prohibited BOM or NUL byte")
    try:
        root = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
    except SofrError:
        raise
    except json.JSONDecodeError as error:
        raise SofrError("SOFR response is malformed or truncated JSON") from error
    if not isinstance(root, dict) or set(root) != {"refRates"}:
        raise SofrError("SOFR response envelope differs from the reviewed wire contract")
    raw_rows = root["refRates"]
    if not isinstance(raw_rows, list) or not raw_rows:
        raise SofrError("SOFR refRates must be a nonempty array")
    parsed: list[ParsedSofrObservation] = []
    seen_dates: set[date] = set()
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            raise SofrError(f"SOFR row {index} must be an object")
        fields = set(raw)
        if fields != _BASE_ROW_FIELDS and fields != _BASE_ROW_FIELDS | {"footnoteId"}:
            raise SofrError(f"SOFR row {index} fields differ from the observed wire contract")
        if raw["type"] != SOFR_BENCHMARK_ID:
            raise SofrError(f"SOFR row {index} has the wrong reference-rate product")
        observation_date = _strict_date(raw["effectiveDate"], "effectiveDate")
        if not SOFR_FIRST_OBSERVATION_DATE <= observation_date <= SOFR_LAST_OBSERVATION_DATE:
            raise SofrError(f"SOFR row {index} effectiveDate is outside the fixed query")
        if observation_date in seen_dates:
            raise SofrError("SOFR response contains a duplicate or conflicting effectiveDate")
        seen_dates.add(observation_date)
        rate = _wire_decimal(raw["percentRate"], "percentRate")
        revision = raw["revisionIndicator"]
        if not isinstance(revision, str) or revision not in {"", "Y"}:
            raise SofrError(f"SOFR row {index} has an unsupported revision indicator")
        footnote = raw.get("footnoteId")
        if "footnoteId" in raw and footnote is None:
            raise SofrError(f"SOFR row {index} has a null footnote identifier")
        if footnote is not None and (type(footnote) is not int or footnote != 2):
            raise SofrError(f"SOFR row {index} has an unsupported footnote identifier")
        percentiles = tuple(raw[field] for field in _PERCENTILE_FIELDS)
        if footnote == 2:
            if percentiles != ("NA", "NA", "NA", "NA"):
                raise SofrError("SOFR contingency footnote 2 requires unavailable percentiles")
        else:
            if any(item == "NA" for item in percentiles):
                raise SofrError("SOFR missing percentile evidence lacks contingency footnote 2")
            percentiles = tuple(
                _wire_decimal(item, field)
                for field, item in zip(_PERCENTILE_FIELDS, percentiles, strict=True)
            )
        parsed.append(
            ParsedSofrObservation(
                observation_date=observation_date,
                rate=rate,
                percentiles=percentiles,  # type: ignore[arg-type]
                revision_indicator=revision,
                footnote_id=footnote,
            )
        )
    ordered = tuple(sorted(parsed, key=lambda item: item.observation_date))
    dataset = ParsedSofrDataset(ordered)
    if (
        dataset.observation_count != SOFR_EXPECTED_OBSERVATION_COUNT
        or dataset.first_observation_date != SOFR_FIRST_OBSERVATION_DATE
        or dataset.last_observation_date != SOFR_LAST_OBSERVATION_DATE
    ):
        raise SofrError("SOFR response is incomplete for the fixed historical query")
    return dataset


def sofr_definition() -> ReferenceRateDefinition:
    """Return the reviewed official daily overnight SOFR identity."""
    return ReferenceRateDefinition(
        contract_schema_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id=SOFR_BENCHMARK_ID,
        benchmark_name="SOFR",
        currency_code="USD",
        administrator="Federal Reserve Bank of New York",
        series_identifier=SOFR_BENCHMARK_ID,
        rate_units="PERCENT_PER_ANNUM",
        day_count_convention="ACT_360",
        compounding_convention="SIMPLE_ACT_360_OVERNIGHT",
        definition_version="1.0.0",
    )


def sofr_source() -> ReferenceRateSource:
    """Return the reviewed New York Fed Markets Data API source contract."""
    return ReferenceRateSource(
        source_code="NYFED_MARKETS_API_SOFR",
        benchmark_id=SOFR_BENCHMARK_ID,
        source_organization="Federal Reserve Bank of New York",
        official_page_url=SOFR_OFFICIAL_PAGE_URL,
        machine_readable_url=SOFR_MACHINE_URL,
        response_format="JSON_MARKETS_API_DAILY_RATE",
        source_role="OFFICIAL_ADMINISTRATOR",
        authentication_requirement="NONE",
        automated_use_status="PERMITTED",
        licensing_reference=SOFR_TERMS_URL,
        raw_retention_status="PERMITTED",
    )


def validate_sofr_policy(policy: CapitalDefensiveConstructionPolicy) -> None:
    """Bind SOFR identity and source to the unchanged construction policy."""
    validate_policy_binding(sofr_definition(), sofr_source(), policy)


def prepare_sofr_bundle(
    *, repository_root: Path, raw_artifact: Path, receipt_path: Path
) -> PreparedSofrBundle:
    """Reconstruct the complete provenance-v2 bundle from retained evidence."""
    _validate_evidence_path_components(repository_root, raw_artifact, receipt_path)
    receipt = load_sofr_receipt(receipt_path)
    _validate_receipt_location(repository_root, raw_artifact, receipt_path, receipt)
    raw = verified_sofr_artifact(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt=receipt,
    )
    dataset = parse_sofr_json(raw)
    definition = sofr_definition()
    source = sofr_source()
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
    manifest = ReferenceRateImportManifest(
        provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        source_contract_fingerprint=source.fingerprint,
        retrieval_timestamp=receipt.retrieval_timestamp,
        request_url=receipt.request_url,
        request_parameters=receipt.request_parameters,
        response_content_type=receipt.response_content_type,
        http_status=receipt.http_status,
        raw_artifact_reference=receipt.raw_artifact_reference,
        raw_artifact_sha256=receipt.raw_artifact_sha256,
        provider_dataset_version=None,
        provider_dataset_version_source_field=None,
        internal_evidence_identity_scheme="SYSTEM_CANONICAL_ARTIFACT_V1",
        internal_evidence_identity=identity,
        import_status="VALIDATED_ADMITTED",
        dataset_fingerprint=dataset.fingerprint,
    )
    availability = canonical_utc_timestamp(receipt.retrieval_timestamp)
    observations: list[ReferenceRateObservation] = []
    for item in dataset.observations:
        observation = ReferenceRateObservation(
            provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
            benchmark_id=SOFR_BENCHMARK_ID,
            source_contract_fingerprint=source.fingerprint,
            import_manifest_fingerprint=manifest.fingerprint,
            observation_date=item.observation_date,
            provider_publication_date=None,
            rate=item.rate,
            provider_revision_id=None,
            provider_revision_id_source_field=None,
            provider_revision_indicator=item.revision_indicator,
            provider_revision_indicator_source_field="revisionIndicator",
            provider_revision_status=(
                "PROVIDER_EMPTY_REVISION_INDICATOR"
                if item.revision_indicator == ""
                else "PROVIDER_EXPLICIT_REVISION"
            ),
            provider_revision_contract_id=None,
            provider_revision_contract_version=None,
            provider_revision_contract_revision_indicator_value=None,
            provider_revision_contract_authoritative_reference=None,
            provider_revision_contract_fingerprint=None,
            provider_publication_value=None,
            provider_publication_value_kind=None,
            provider_publication_source_field=None,
            availability_basis="RETRIEVAL_BOUND",
            availability_boundary_utc=availability,
            availability_derivation_rule_id=None,
            availability_derivation_rule_version=None,
            availability_policy_reference=None,
            availability_calendar_id=None,
            availability_calendar_version=None,
            availability_calendar_fingerprint=None,
            revision_sequence=1,
            supersedes_observation_fingerprint=None,
            is_current=True,
            quality_status="ADMITTED_VALIDATED",
        )
        validate_observation_availability(observation, manifest)
        observations.append(observation)
    return PreparedSofrBundle(
        receipt=receipt,
        dataset=dataset,
        definition=definition,
        source=source,
        manifest=manifest,
        observations=tuple(observations),
    )


def import_sofr_evidence(
    *,
    target: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    policy: CapitalDefensiveConstructionPolicy,
    failure_hook: Callable[[str], None] | None = None,
) -> SofrImportResult:
    """Import the fixed retained snapshot atomically; exact replay is a no-op."""
    if target.is_symlink() or not target.resolve().is_file():
        raise SofrError("SOFR import target must be a regular non-symlink SQLite file")
    validate_sofr_policy(policy)
    prepared = prepare_sofr_bundle(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt_path=receipt_path,
    )
    target = target.resolve()
    try:
        with connect(target) as connection:
            validate_schema(connection)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise SofrError("SOFR import target has foreign-key violations")
            existing = int(
                connection.execute(
                    "SELECT count(*) FROM reference_rate_definition WHERE benchmark_id=?",
                    (SOFR_BENCHMARK_ID,),
                ).fetchone()[0]
            )
            if existing:
                _require_sofr_bundle(connection, prepared, repository_root.resolve())
                return _import_result(prepared, inserted_rows=0, reused=True)
            with transaction(connection):
                definition_id = _insert_definition(connection, prepared.definition)
                _call_hook(failure_hook, "after_definition")
                source_id = _insert_source(connection, definition_id, prepared.source)
                _call_hook(failure_hook, "after_source")
                manifest_id = _insert_manifest(
                    connection, definition_id, source_id, prepared.manifest
                )
                _call_hook(failure_hook, "after_manifest")
                _insert_observations(
                    connection,
                    definition_id,
                    source_id,
                    manifest_id,
                    prepared.observations,
                    failure_hook,
                )
                _require_sofr_bundle(connection, prepared, repository_root.resolve())
                validate_schema(connection)
                _call_hook(failure_hook, "before_commit")
    except (sqlite3.Error, ReferenceRateContractError, SchemaVersionError) as error:
        raise SofrError("SOFR persistence failed closed") from error
    return _import_result(prepared, inserted_rows=3 + len(prepared.observations), reused=False)


def validate_sofr_database(
    *,
    target: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    policy: CapitalDefensiveConstructionPolicy,
) -> dict[str, object]:
    """Validate the SOFR bundle and retained evidence read-only."""
    if target.is_symlink() or not target.resolve().is_file():
        raise SofrError("SOFR validation target must be a regular non-symlink SQLite file")
    target = target.resolve()
    before = _file_sha256(target)
    validate_sofr_policy(policy)
    prepared = prepare_sofr_bundle(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt_path=receipt_path,
    )
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        validate_schema(connection)
        _require_sofr_bundle(connection, prepared, repository_root.resolve())
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != ("ok",) or violations:
            raise SofrError("SOFR database integrity or foreign-key validation failed")
        schema_fingerprint = canonical_fingerprint(reference_rate_schema_contract(connection))
        row_counts = _reference_counts(connection)
        constructed = _constructed_counts(connection)
        if any(constructed.values()):
            raise SofrError("constructed-portfolio production tables must remain empty")
    if _file_sha256(target) != before:
        raise SofrError("read-only SOFR validation changed database bytes")
    revision_counts = Counter(item.revision_indicator for item in prepared.dataset.observations)
    footnote_counts = Counter(item.footnote_id for item in prepared.dataset.observations)
    return {
        "adapter_schema_version": SOFR_ADAPTER_SCHEMA_VERSION,
        "availability_basis": "RETRIEVAL_BOUND",
        "availability_boundary_utc": prepared.observations[0].availability_boundary_utc,
        "benchmark": {
            "administrator": prepared.definition.administrator,
            "benchmark_id": SOFR_BENCHMARK_ID,
            "compounding_convention": prepared.definition.compounding_convention,
            "currency": "USD",
            "day_count_convention": prepared.definition.day_count_convention,
            "rate_units": prepared.definition.rate_units,
            "series_identifier": prepared.definition.series_identifier,
        },
        "constructed_portfolio_row_counts": constructed,
        "database_sha256": before,
        "dataset_fingerprint": prepared.dataset.fingerprint,
        "definition_fingerprint": prepared.definition.fingerprint,
        "evidence_status": "USD_SOFR_ADMITTED_VALIDATED",
        "feature_contract_fingerprint": REFERENCE_RATE_FEATURE_FINGERPRINT,
        "feature_id": REFERENCE_RATE_FEATURE_ID,
        "feature_revision": REFERENCE_RATE_FEATURE_REVISION,
        "first_observation_date": prepared.dataset.first_observation_date.isoformat(),
        "footnote_id_counts": {
            "ABSENT" if key is None else str(key): value
            for key, value in sorted(footnote_counts.items(), key=lambda item: str(item[0]))
        },
        "foreign_key_violations": 0,
        "integrity_check": "ok",
        "last_observation_date": prepared.dataset.last_observation_date.isoformat(),
        "manifest_fingerprint": prepared.manifest.fingerprint,
        "milestone_11": "NO_GO",
        "milestone_12": "NO_GO",
        "milestone_13": "NO_GO",
        "observation_count": prepared.dataset.observation_count,
        "production_cutover": "NOT_AUTHORIZED",
        "provider_dataset_version": None,
        "provider_revision_id": None,
        "raw_artifact": {
            "byte_count": prepared.receipt.byte_count,
            "reference": prepared.receipt.raw_artifact_reference,
            "sha256": prepared.receipt.raw_artifact_sha256,
        },
        "receipt_artifact": {
            "reference": receipt_path.resolve().relative_to(repository_root.resolve()).as_posix(),
            "sha256": _file_sha256(receipt_path.resolve()),
        },
        "receipt_fingerprint": prepared.receipt.fingerprint,
        "reference_rate_row_counts": row_counts,
        "reference_rate_runtime_admission": "USD_SOFR_BENCHMARK_SCOPED",
        "revision_indicator_counts": dict(sorted(revision_counts.items())),
        "schema_contract_fingerprint": schema_fingerprint,
        "source_fingerprint": prepared.source.fingerprint,
        "status": "PASS",
    }


def _require_sofr_bundle(
    connection: sqlite3.Connection,
    prepared: PreparedSofrBundle,
    repository_root: Path,
) -> None:
    from .provenance import (  # Imported lazily to avoid module initialization cycles.
        ReferenceRateProvenanceValidationError,
        _definitions,
        _manifests,
        _observations,
        _sources,
        _validate_revision_chains,
    )

    try:
        definitions = _definitions(connection)
        sources = _sources(connection, definitions)
        manifests = _manifests(connection, definitions, sources, repository_root)
        observations = _observations(
            connection, definitions, sources, manifests, approved_schedules=()
        )
        _validate_revision_chains(
            observations, manifests, approved_revision_contracts=()
        )
    except ReferenceRateProvenanceValidationError as error:
        raise SofrError("stored reference-rate provenance is corrupt") from error
    definition_ids = [
        key for key, item in definitions.items() if item.benchmark_id == SOFR_BENCHMARK_ID
    ]
    if len(definition_ids) != 1:
        raise SofrError("SOFR scope contains a missing or extra definition")
    definition_id = definition_ids[0]
    scoped_sources = [item for item in sources.values() if item[0] == definition_id]
    scoped_manifests = [item for item in manifests.values() if item[0] == definition_id]
    scoped_observations = tuple(
        sorted(
            (
                item
                for item in observations.values()
                if item.benchmark_id == SOFR_BENCHMARK_ID
            ),
            key=lambda item: (item.observation_date, item.revision_sequence),
        )
    )
    if (
        definitions[definition_id] != prepared.definition
        or tuple(item[1] for item in scoped_sources) != (prepared.source,)
        or tuple(item[2] for item in scoped_manifests) != (prepared.manifest,)
        or scoped_observations != prepared.observations
    ):
        raise SofrError("stored SOFR bundle differs from retained artifact and receipt")


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
    definition_id: int,
    source_id: int,
    manifest_id: int,
    observations: tuple[ReferenceRateObservation, ...],
    failure_hook: Callable[[str], None] | None,
) -> None:
    for index, item in enumerate(observations):
        connection.execute(
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
                   revision_sequence, supersedes_observation_id, is_current,
                   quality_status, observation_fingerprint
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                         ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.provenance_contract_version,
                definition_id,
                source_id,
                manifest_id,
                item.observation_date.isoformat(),
                None,
                item.rate_decimal,
                None,
                None,
                item.provider_revision_indicator,
                item.provider_revision_indicator_source_field,
                item.provider_revision_status,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                item.availability_basis,
                item.availability_boundary_utc,
                None,
                None,
                None,
                None,
                None,
                None,
                1,
                None,
                1,
                item.quality_status,
                item.fingerprint,
            ),
        )
        if index == 0:
            _call_hook(failure_hook, "after_first_observation")
        if index == len(observations) // 2:
            _call_hook(failure_hook, "after_middle_observation")
    _call_hook(failure_hook, "after_observations")


def _import_result(
    prepared: PreparedSofrBundle, *, inserted_rows: int, reused: bool
) -> SofrImportResult:
    return SofrImportResult(
        dataset_fingerprint=prepared.dataset.fingerprint,
        definition_fingerprint=prepared.definition.fingerprint,
        source_fingerprint=prepared.source.fingerprint,
        manifest_fingerprint=prepared.manifest.fingerprint,
        receipt_fingerprint=prepared.receipt.fingerprint,
        observation_count=prepared.dataset.observation_count,
        first_observation_date=prepared.dataset.first_observation_date.isoformat(),
        last_observation_date=prepared.dataset.last_observation_date.isoformat(),
        inserted_rows=inserted_rows,
        reused=reused,
    )


def _call_hook(hook: Callable[[str], None] | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _reference_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "reference_rate_definition",
        "reference_rate_source",
        "reference_rate_import_manifest",
        "reference_rate_observation",
    )
    return {
        table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
        for table in tables
    }


def _constructed_counts(connection: sqlite3.Connection) -> dict[str, int]:
    from .provenance import _constructed_counts as counts

    return counts(connection)


def _validate_receipt_location(
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    receipt: SofrAcquisitionReceipt,
) -> None:
    root = _lexical_absolute(repository_root)
    raw = _lexical_absolute(raw_artifact)
    receipt_file = _lexical_absolute(receipt_path)
    expected_raw = _lexical_absolute(
        root / PurePosixPath(receipt.raw_artifact_reference)
    )
    approved = root / "data" / "raw" / "reference_rates" / "new_york_fed" / "sofr"
    _validate_evidence_path_components(root, raw, receipt_file)
    if (
        raw != expected_raw
        or not raw.is_relative_to(approved)
        or receipt_file != raw.with_suffix(".receipt.json")
        or not receipt_file.is_relative_to(approved)
    ):
        raise SofrError("SOFR raw artifact and receipt locations differ from provenance")


def _validate_evidence_path_components(
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
) -> None:
    root = _lexical_absolute(repository_root)
    raw = _lexical_absolute(raw_artifact)
    receipt = _lexical_absolute(receipt_path)
    if root.is_symlink() or _has_symlink_component(raw, root) or _has_symlink_component(
        receipt, root
    ):
        raise SofrError("SOFR retained evidence path contains a symlink component")


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _strict_date(value: object, field: str) -> date:
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        raise SofrError(f"{field} must be canonical YYYY-MM-DD text")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise SofrError(f"{field} is not a calendar date") from error
    if parsed.isoformat() != value:
        raise SofrError(f"{field} must be canonical YYYY-MM-DD text")
    return parsed


def _wire_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise SofrError(f"{field} must be an exact finite JSON decimal")
    if value.as_tuple().exponent != -2:
        raise SofrError(f"{field} must preserve the official two-decimal precision")
    return value


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f").rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _reject_json_constant(value: str) -> object:
    raise SofrError(f"SOFR JSON contains prohibited non-finite constant {value}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_effective_url(value: str) -> None:
    parsed = urlsplit(value)
    expected = urlsplit(SOFR_MACHINE_URL)
    if (
        parsed.scheme != expected.scheme
        or parsed.hostname != expected.hostname
        or parsed.port is not None
        or parsed.path != expected.path
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SofrError("SOFR effective URL differs from the reviewed endpoint")
    parameters = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if canonical_request_parameters(dict(parameters)) != canonical_request_parameters(
        SOFR_REQUEST_PARAMETERS
    ) or len(parameters) != len(dict(parameters)):
        raise SofrError("SOFR effective URL parameters differ from the fixed reviewed query")


def _validate_json_content_type(value: str) -> None:
    pieces = [part.strip() for part in value.split(";")]
    if pieces[0].lower() != "application/json":
        raise SofrError("SOFR response Content-Type must be application/json")
    parameters: dict[str, str] = {}
    for item in pieces[1:]:
        if not item or "=" not in item:
            raise SofrError("SOFR response Content-Type parameters are malformed")
        key, raw_value = item.split("=", 1)
        key = key.strip().lower()
        raw_value = raw_value.strip().strip('"').lower()
        if key in parameters:
            raise SofrError("SOFR response Content-Type has duplicate parameters")
        parameters[key] = raw_value
    if set(parameters) - {"charset"} or parameters.get("charset", "utf-8") not in {
        "utf-8",
        "utf8",
    }:
        raise SofrError("SOFR response Content-Type has an unsupported charset")


def _relative_artifact_reference(value: str) -> None:
    _text(value, "raw_artifact_reference")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise SofrError("SOFR raw artifact reference must be canonical and relative")


def _aware_timestamp(value: str, field: str) -> datetime:
    _text(value, field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise SofrError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SofrError(f"{field} must include a timezone")
    if parsed.astimezone(UTC).isoformat() != value:
        raise SofrError(f"{field} must be canonical UTC ISO format")
    return parsed


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SofrError(f"{field} must be an exact non-empty string")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SofrError("SOFR JSON contains a duplicate object key")
        result[key] = value
    return result
