"""Strict offline contracts for official MNB daily HUFONIA evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path, PurePosixPath
from struct import unpack_from
from typing import Any
from urllib.parse import urlsplit

import xlrd  # type: ignore[import-untyped]
from xlrd.compdoc import CompDoc  # type: ignore[import-untyped]

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

HUFONIA_ADAPTER_SCHEMA_VERSION = 1
HUFONIA_BENCHMARK_ID = "HUFONIA"
HUFONIA_MACHINE_URL = "https://www.mnb.hu/letoltes/hufonia.xls"
HUFONIA_OFFICIAL_PAGE_URL = (
    "https://statisztika.mnb.hu/statistical-topics/monetary-policy-statistics"
)
HUFONIA_DEFINITION_URL = (
    "https://www.mnb.hu/en/pressroom/press-releases/press-releases-2010/"
    "press-release-on-the-introduction-of-the-hufonia-name"
)
HUFONIA_TERMS_URL = (
    "https://www.mnb.hu/en/the-central-bank/practical-issues/disclaimer"
)
HUFONIA_REQUEST_PARAMETERS: Mapping[str, str] = {}
HUFONIA_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
HUFONIA_EXPECTED_OBSERVATION_COUNT = 6231
HUFONIA_FIRST_OBSERVATION_DATE = date(2002, 1, 2)
HUFONIA_LAST_OBSERVATION_DATE = date(2026, 8, 31)
HUFONIA_NAMED_LAUNCH_DATE = date(2010, 9, 1)
HUFONIA_TRADE_DATE_SEMANTICS_START = date(2016, 10, 4)

_EXPECTED_SHEETS = (
    "2023", "2022", "2024", "info", "2025", "2026", "2021", "2020",
    "2019", "2018", "2017", "2016", "2015", "2014", "2013", "2012",
    "2011", "2010", "2009", "2008", "2007", "2006", "2005", "2004",
    "2003", "2002",
)
HUFONIA_INFO_FINGERPRINT = (
    "682fe07a416bde864b668c5087f104eed63b344bd3af2b75ee805462e19c7cbc"
)
_CORRECTION_NOTE = "módosítva 14:53-kor"
_DATE_BASIS_NOTE = (
    "from 2016.10.04. the base of the data and the publication changed from Value "
    "Date to Trade Date"
)
_GENERAL_FORMAT_DATES = frozenset(
    {
        date(2006, 11, 6),
        date(2006, 12, 4),
        date(2023, 2, 24),
        date(2023, 2, 27),
    }
)
_BIFF_BOUNDSHEET = 0x0085
_BIFF_EOF = 0x000A
_BIFF_NUMBER = 0x0203
_BIFF_RK = 0x027E
_BIFF_MULRK = 0x00BD
_BIFF_FORMULA = 0x0006
_EXCEL_EPOCH = date(1899, 12, 30)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class HufoniaError(RuntimeError):
    """Official HUFONIA evidence is missing, malformed, conflicting, or unsafe."""


@dataclass(frozen=True, slots=True)
class _BiffNumericCell:
    value: Decimal | None
    record_type: int
    xf_index: int


@dataclass(frozen=True, slots=True)
class ParsedHufoniaObservation:
    """One exact displayed HUFONIA rate and its official workbook context."""

    observation_date: date
    rate: Decimal
    turnover_million_huf: Decimal
    quote_decimal_places: int
    observation_date_basis: str
    provider_annotation: str | None
    revision_indicator: str | None

    @property
    def rate_decimal(self) -> str:
        return _decimal_text(self.rate)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "observation_date": self.observation_date.isoformat(),
            "observation_date_basis": self.observation_date_basis,
            "provider_annotation": self.provider_annotation,
            "quote_decimal_places": self.quote_decimal_places,
            "rate_percent_per_annum": format(self.rate, "f"),
            "revision_indicator": self.revision_indicator,
            "turnover_million_huf": format(self.turnover_million_huf, "f"),
        }


@dataclass(frozen=True, slots=True)
class ParsedHufoniaDataset:
    """Canonical fixed-range official daily HUFONIA dataset."""

    observations: tuple[ParsedHufoniaObservation, ...]

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
            "adapter_schema_version": HUFONIA_ADAPTER_SCHEMA_VERSION,
            "benchmark_id": HUFONIA_BENCHMARK_ID,
            "fixed_history_end_date": HUFONIA_LAST_OBSERVATION_DATE.isoformat(),
            "named_launch_date": HUFONIA_NAMED_LAUNCH_DATE.isoformat(),
            "rate_units": "PERCENT_PER_ANNUM",
            "request_parameters": dict(
                canonical_request_parameters(HUFONIA_REQUEST_PARAMETERS)
            ),
            "trade_date_semantics_start": (
                HUFONIA_TRADE_DATE_SEMANTICS_START.isoformat()
            ),
            "wire_contract": "MNB_HUFONIA_BIFF8_ANNUAL_SHEETS_V1",
            "observations": [item.canonical_payload() for item in self.observations],
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class PreparedHufoniaBundle:
    """Fully reconstructed contracts from retained HUFONIA evidence."""

    receipt: HufoniaAcquisitionReceipt
    dataset: ParsedHufoniaDataset
    definition: ReferenceRateDefinition
    source: ReferenceRateSource
    manifest: ReferenceRateImportManifest
    observations: tuple[ReferenceRateObservation, ...]


@dataclass(frozen=True, slots=True)
class HufoniaImportResult:
    """Deterministic outcome of one transactional offline HUFONIA import."""

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
class HufoniaAcquisitionReceipt:
    """Immutable transport provenance retained beside one raw MNB workbook."""

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
            raise HufoniaError("unsupported HUFONIA acquisition receipt schema version")
        if self.request_url != HUFONIA_MACHINE_URL:
            raise HufoniaError("HUFONIA request URL differs from the reviewed endpoint")
        if self.request_parameters != canonical_request_parameters(HUFONIA_REQUEST_PARAMETERS):
            raise HufoniaError("HUFONIA request parameters differ from the fixed reviewed query")
        _text(self.effective_url, "effective_url")
        _validate_effective_url(self.effective_url)
        _aware_timestamp(self.retrieval_timestamp, "retrieval_timestamp")
        if type(self.http_status) is not int or self.http_status != 200:
            raise HufoniaError("only MNB HTTP 200 evidence may be retained")
        _text(self.response_content_type, "response_content_type")
        _validate_xls_content_type(self.response_content_type)
        if not isinstance(self.content_encoding, str) or self.content_encoding not in {
            "",
            "identity",
        }:
            raise HufoniaError("HUFONIA response content encoding must be absent or identity")
        if self.content_length is not None and (
            type(self.content_length) is not int or self.content_length <= 0
        ):
            raise HufoniaError("HUFONIA Content-Length must be a positive integer when present")
        for value, field in (
            (self.response_date, "response_date"),
            (self.last_modified, "last_modified"),
            (self.etag, "etag"),
        ):
            if value is not None:
                _text(value, field)
        if type(self.byte_count) is not int or not 0 < self.byte_count <= HUFONIA_MAX_RESPONSE_BYTES:
            raise HufoniaError("HUFONIA response byte count is outside the admitted bound")
        if self.content_length is not None and self.content_length != self.byte_count:
            raise HufoniaError("HUFONIA Content-Length differs from retained byte count")
        _relative_artifact_reference(self.raw_artifact_reference)
        if _SHA256.fullmatch(self.raw_artifact_sha256) is None:
            raise HufoniaError("raw_artifact_sha256 must be a lowercase SHA-256")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> HufoniaAcquisitionReceipt:
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
            raise HufoniaError("HUFONIA acquisition receipt fields differ from the contract")
        parameters = value["request_parameters"]
        if not isinstance(parameters, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in parameters.items()
        ):
            raise HufoniaError("HUFONIA receipt request_parameters must be string-to-string")
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


def receipt_json(receipt: HufoniaAcquisitionReceipt) -> str:
    """Serialize one receipt canonically for tamper-evident offline reuse."""
    return canonical_json(receipt.canonical_payload()) + "\n"


def load_hufonia_receipt(path: Path) -> HufoniaAcquisitionReceipt:
    """Load an exact canonical receipt and reject duplicate keys or altered bytes."""
    if path.is_symlink() or not path.is_file():
        raise HufoniaError("HUFONIA receipt must be a regular non-symlink file")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise HufoniaError("HUFONIA receipt is not strict UTF-8") from error
    if text.startswith("\ufeff") or "\x00" in text:
        raise HufoniaError("HUFONIA receipt contains a prohibited BOM or NUL byte")
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except HufoniaError:
        raise
    except json.JSONDecodeError as error:
        raise HufoniaError("HUFONIA receipt is not strict JSON") from error
    if not isinstance(value, Mapping):
        raise HufoniaError("HUFONIA receipt root must be an object")
    receipt = HufoniaAcquisitionReceipt.from_mapping(value)
    if raw != receipt_json(receipt).encode("utf-8"):
        raise HufoniaError("HUFONIA receipt bytes are not canonical JSON")
    return receipt


def verified_hufonia_artifact(
    *, repository_root: Path, raw_artifact: Path, receipt: HufoniaAcquisitionReceipt
) -> bytes:
    """Verify retained path, exact bytes, size, and hash before offline parsing."""
    root = _lexical_absolute(repository_root)
    raw = _lexical_absolute(raw_artifact)
    expected = _lexical_absolute(
        root / PurePosixPath(receipt.raw_artifact_reference)
    )
    approved = root / "data" / "raw" / "reference_rates" / "mnb" / "hufonia"
    if root.is_symlink() or _has_symlink_component(raw, root):
        raise HufoniaError("HUFONIA raw artifact path contains a symlink component")
    if not raw.is_file():
        raise HufoniaError("HUFONIA raw artifact must be a regular non-symlink file")
    if raw != expected or not raw.is_relative_to(approved):
        raise HufoniaError("HUFONIA raw artifact path differs from immutable receipt provenance")
    data = raw.read_bytes()
    if len(data) != receipt.byte_count:
        raise HufoniaError("HUFONIA raw artifact byte count differs from its receipt")
    if hashlib.sha256(data).hexdigest() != receipt.raw_artifact_sha256:
        raise HufoniaError("HUFONIA raw artifact SHA-256 differs from its receipt")
    return data


def parse_hufonia_xls(raw_bytes: bytes) -> ParsedHufoniaDataset:
    """Parse MNB's BIFF8 workbook with Decimal-only financial conversion."""
    if not isinstance(raw_bytes, bytes) or not 0 < len(raw_bytes) <= HUFONIA_MAX_RESPONSE_BYTES:
        raise HufoniaError("HUFONIA XLS byte size is empty or outside the admitted bound")
    try:
        book = xlrd.open_workbook(
            file_contents=raw_bytes,
            formatting_info=True,
            on_demand=False,
        )
        numeric_cells, sheet_contract = _biff_numeric_cells(raw_bytes)
    except HufoniaError:
        raise
    except (OSError, ValueError, xlrd.XLRDError) as error:
        raise HufoniaError("HUFONIA workbook is malformed, encrypted, or truncated") from error
    if (
        book.biff_version != 80
        or book.datemode != 0
        or tuple(book.sheet_names()) != _EXPECTED_SHEETS
        or sheet_contract != _EXPECTED_SHEETS
    ):
        raise HufoniaError("HUFONIA workbook identity differs from the reviewed BIFF8 contract")
    if any(book.sheet_by_name(name).visibility != 0 for name in _EXPECTED_SHEETS):
        raise HufoniaError("HUFONIA workbook contains a hidden or non-visible reviewed sheet")
    info = book.sheet_by_name("info")
    info_values = [
        info.cell_value(row, 0)
        for row in range(info.nrows)
    ]
    if (
        info.ncols != 1
        or any(not isinstance(item, str) for item in info_values)
        or canonical_fingerprint(info_values) != HUFONIA_INFO_FINGERPRINT
    ):
        raise HufoniaError("HUFONIA definition sheet differs from the reviewed official text")

    parsed: list[ParsedHufoniaObservation] = []
    excluded_overlap: ParsedHufoniaObservation | None = None
    excluded_future_dates: list[date] = []
    orphan_turnover_seen = False
    for year in range(2002, 2027):
        sheet_name = str(year)
        sheet = book.sheet_by_name(sheet_name)
        cells = numeric_cells[sheet_name]
        header_rows = _validate_year_headers(sheet_name, sheet)
        previous_sheet_date: date | None = None
        for row_index in range(header_rows, sheet.nrows):
            values = tuple(
                sheet.cell_value(row_index, column)
                for column in range(sheet.ncols)
            )
            nonempty_extra = [
                (column, value)
                for column, value in enumerate(values[3:], start=3)
                if value not in {"", None}
            ]
            annotation = _validated_annotation(
                sheet_name=sheet_name,
                row_index=row_index,
                nonempty_extra=nonempty_extra,
            )
            rate_is_text = sheet.cell_type(row_index, 1) == xlrd.XL_CELL_TEXT
            coordinates = [
                (row_index, 0) in cells,
                (row_index, 1) in cells or rate_is_text,
                (row_index, 2) in cells,
            ]
            if coordinates == [False, False, False] and all(
                value == "" or value is None for value in values
            ):
                continue
            if coordinates == [False, False, True]:
                turnover = _integral_decimal(cells[(row_index, 2)], "orphan turnover")
                if (
                    orphan_turnover_seen
                    or sheet_name != "2002"
                    or row_index != sheet.nrows - 1
                    or turnover != Decimal(30625)
                    or annotation is not None
                ):
                    raise HufoniaError("HUFONIA workbook contains an unsupported partial row")
                orphan_turnover_seen = True
                continue
            if coordinates != [True, True, True]:
                raise HufoniaError("HUFONIA workbook contains a missing or malformed observation")
            observation = _parse_year_observation(
                book=book,
                sheet_name=sheet_name,
                row_index=row_index,
                cells=cells,
                annotation=annotation,
            )
            if (
                previous_sheet_date is not None
                and observation.observation_date <= previous_sheet_date
            ):
                raise HufoniaError(
                    "HUFONIA worksheet dates are not strictly increasing"
                )
            previous_sheet_date = observation.observation_date
            if observation.observation_date.year != year:
                if (
                    excluded_overlap is not None
                    or sheet_name != "2010"
                    or row_index != sheet.nrows - 1
                    or observation.observation_date != date(2011, 1, 3)
                ):
                    raise HufoniaError("HUFONIA workbook contains an out-of-year observation")
                excluded_overlap = observation
                continue
            if observation.observation_date > HUFONIA_LAST_OBSERVATION_DATE:
                if sheet_name != "2026" or annotation is not None:
                    raise HufoniaError("HUFONIA workbook contains an unsupported future observation")
                excluded_future_dates.append(observation.observation_date)
                continue
            parsed.append(observation)
    if not orphan_turnover_seen:
        raise HufoniaError("HUFONIA workbook lost its reviewed legacy partial-row marker")
    if excluded_future_dates != [date(2026, 9, 1), date(2026, 9, 2)]:
        raise HufoniaError("HUFONIA workbook future boundary rows differ from the capture")
    ordered = tuple(sorted(parsed, key=lambda item: item.observation_date))
    if excluded_overlap is None:
        raise HufoniaError("HUFONIA workbook lost its reviewed year-overlap row")
    matching = [item for item in ordered if item.observation_date == date(2011, 1, 3)]
    if len(matching) != 1 or matching[0].canonical_payload() != excluded_overlap.canonical_payload():
        raise HufoniaError("HUFONIA workbook year-overlap row conflicts with 2011 evidence")
    dates = [item.observation_date for item in ordered]
    if len(dates) != len(set(dates)):
        raise HufoniaError("HUFONIA workbook contains a duplicate or conflicting date")
    dataset = ParsedHufoniaDataset(ordered)
    if (
        dataset.observation_count != HUFONIA_EXPECTED_OBSERVATION_COUNT
        or dataset.first_observation_date != HUFONIA_FIRST_OBSERVATION_DATE
        or dataset.last_observation_date != HUFONIA_LAST_OBSERVATION_DATE
    ):
        raise HufoniaError("HUFONIA workbook is incomplete for the fixed historical boundary")
    return dataset


def hufonia_definition() -> ReferenceRateDefinition:
    """Return the reviewed official daily overnight HUFONIA identity."""
    return ReferenceRateDefinition(
        contract_schema_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id=HUFONIA_BENCHMARK_ID,
        benchmark_name="HUFONIA",
        currency_code="HUF",
        administrator="Magyar Nemzeti Bank",
        series_identifier="MNB_HUFONIA_XLS_HISTORY",
        rate_units="PERCENT_PER_ANNUM",
        day_count_convention="NOT_SUPPLIED_BY_MNB",
        compounding_convention="NONE_DAILY_OVERNIGHT_RATE",
        definition_version="1.0.0",
    )


def hufonia_source() -> ReferenceRateSource:
    """Return the reviewed official MNB HUFONIA workbook source contract."""
    return ReferenceRateSource(
        source_code="MNB_HUFONIA_XLS",
        benchmark_id=HUFONIA_BENCHMARK_ID,
        source_organization="Magyar Nemzeti Bank",
        official_page_url=HUFONIA_OFFICIAL_PAGE_URL,
        machine_readable_url=HUFONIA_MACHINE_URL,
        response_format="XLS_BIFF8_ANNUAL_DAILY_RATE",
        source_role="OFFICIAL_ADMINISTRATOR",
        authentication_requirement="NONE",
        automated_use_status="PERMITTED",
        licensing_reference=HUFONIA_TERMS_URL,
        raw_retention_status="PERMITTED",
    )


def validate_hufonia_policy(policy: CapitalDefensiveConstructionPolicy) -> None:
    """Bind HUFONIA identity and source to the unchanged construction policy."""
    validate_policy_binding(hufonia_definition(), hufonia_source(), policy)


def prepare_hufonia_bundle(
    *, repository_root: Path, raw_artifact: Path, receipt_path: Path
) -> PreparedHufoniaBundle:
    """Reconstruct the complete provenance-v2 bundle from retained evidence."""
    _validate_evidence_path_components(repository_root, raw_artifact, receipt_path)
    receipt = load_hufonia_receipt(receipt_path)
    _validate_receipt_location(repository_root, raw_artifact, receipt_path, receipt)
    raw = verified_hufonia_artifact(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt=receipt,
    )
    dataset = parse_hufonia_xls(raw)
    definition = hufonia_definition()
    source = hufonia_source()
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
            benchmark_id=HUFONIA_BENCHMARK_ID,
            source_contract_fingerprint=source.fingerprint,
            import_manifest_fingerprint=manifest.fingerprint,
            observation_date=item.observation_date,
            provider_publication_date=None,
            rate=item.rate,
            provider_revision_id=None,
            provider_revision_id_source_field=None,
            provider_revision_indicator=item.revision_indicator,
            provider_revision_indicator_source_field=(
                "2015!D227" if item.revision_indicator is not None else None
            ),
            provider_revision_status=(
                "PROVIDER_EXPLICIT_REVISION"
                if item.revision_indicator is not None
                else "PROVIDER_REVISION_FIELD_NOT_SUPPLIED"
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
    return PreparedHufoniaBundle(
        receipt=receipt,
        dataset=dataset,
        definition=definition,
        source=source,
        manifest=manifest,
        observations=tuple(observations),
    )


def import_hufonia_evidence(
    *,
    target: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    policy: CapitalDefensiveConstructionPolicy,
    failure_hook: Callable[[str], None] | None = None,
) -> HufoniaImportResult:
    """Import the fixed retained snapshot atomically; exact replay is a no-op."""
    if target.is_symlink() or not target.resolve().is_file():
        raise HufoniaError("HUFONIA import target must be a regular non-symlink SQLite file")
    validate_hufonia_policy(policy)
    prepared = prepare_hufonia_bundle(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt_path=receipt_path,
    )
    target = target.resolve()
    try:
        with connect(target) as connection:
            validate_schema(connection)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise HufoniaError("HUFONIA import target has foreign-key violations")
            existing = int(
                connection.execute(
                    "SELECT count(*) FROM reference_rate_definition WHERE benchmark_id=?",
                    (HUFONIA_BENCHMARK_ID,),
                ).fetchone()[0]
            )
            if existing:
                _require_hufonia_bundle(connection, prepared, repository_root.resolve())
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
                _require_hufonia_bundle(connection, prepared, repository_root.resolve())
                validate_schema(connection)
                _call_hook(failure_hook, "before_commit")
    except (sqlite3.Error, ReferenceRateContractError, SchemaVersionError) as error:
        raise HufoniaError("HUFONIA persistence failed closed") from error
    return _import_result(prepared, inserted_rows=3 + len(prepared.observations), reused=False)


def validate_hufonia_database(
    *,
    target: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    policy: CapitalDefensiveConstructionPolicy,
) -> dict[str, object]:
    """Validate the HUFONIA bundle and retained evidence read-only."""
    if target.is_symlink() or not target.resolve().is_file():
        raise HufoniaError("HUFONIA validation target must be a regular non-symlink SQLite file")
    target = target.resolve()
    before = _file_sha256(target)
    validate_hufonia_policy(policy)
    prepared = prepare_hufonia_bundle(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt_path=receipt_path,
    )
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        validate_schema(connection)
        _require_hufonia_bundle(connection, prepared, repository_root.resolve())
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != ("ok",) or violations:
            raise HufoniaError("HUFONIA database integrity or foreign-key validation failed")
        schema_fingerprint = canonical_fingerprint(reference_rate_schema_contract(connection))
        row_counts = _reference_counts(connection)
        constructed = _constructed_counts(connection)
        if any(constructed.values()):
            raise HufoniaError("constructed-portfolio production tables must remain empty")
    if _file_sha256(target) != before:
        raise HufoniaError("read-only HUFONIA validation changed database bytes")
    revision_counts = Counter(
        "ABSENT" if item.revision_indicator is None else item.revision_indicator
        for item in prepared.dataset.observations
    )
    annotation_counts = Counter(
        "ABSENT" if item.provider_annotation is None else item.provider_annotation
        for item in prepared.dataset.observations
    )
    precision_counts = Counter(
        item.quote_decimal_places for item in prepared.dataset.observations
    )
    date_basis_counts = Counter(
        item.observation_date_basis for item in prepared.dataset.observations
    )
    return {
        "adapter_schema_version": HUFONIA_ADAPTER_SCHEMA_VERSION,
        "availability_basis": "RETRIEVAL_BOUND",
        "availability_boundary_utc": prepared.observations[0].availability_boundary_utc,
        "benchmark": {
            "administrator": prepared.definition.administrator,
            "benchmark_id": HUFONIA_BENCHMARK_ID,
            "compounding_convention": prepared.definition.compounding_convention,
            "currency": "HUF",
            "day_count_convention": prepared.definition.day_count_convention,
            "rate_units": prepared.definition.rate_units,
            "series_identifier": prepared.definition.series_identifier,
        },
        "constructed_portfolio_row_counts": constructed,
        "database_sha256": before,
        "dataset_fingerprint": prepared.dataset.fingerprint,
        "definition_fingerprint": prepared.definition.fingerprint,
        "date_basis_counts": dict(sorted(date_basis_counts.items())),
        "evidence_status": "HUF_HUFONIA_ADMITTED_VALIDATED",
        "feature_contract_fingerprint": REFERENCE_RATE_FEATURE_FINGERPRINT,
        "feature_id": REFERENCE_RATE_FEATURE_ID,
        "feature_revision": REFERENCE_RATE_FEATURE_REVISION,
        "first_observation_date": prepared.dataset.first_observation_date.isoformat(),
        "provider_annotation_counts": dict(sorted(annotation_counts.items())),
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
        "quote_decimal_place_counts": {
            str(key): value for key, value in sorted(precision_counts.items())
        },
        "reference_rate_runtime_admission": "HUF_HUFONIA_BENCHMARK_SCOPED",
        "revision_indicator_counts": dict(sorted(revision_counts.items())),
        "schema_contract_fingerprint": schema_fingerprint,
        "source_fingerprint": prepared.source.fingerprint,
        "status": "PASS",
    }


def _require_hufonia_bundle(
    connection: sqlite3.Connection,
    prepared: PreparedHufoniaBundle,
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
        raise HufoniaError("stored reference-rate provenance is corrupt") from error
    definition_ids = [
        key for key, item in definitions.items() if item.benchmark_id == HUFONIA_BENCHMARK_ID
    ]
    if len(definition_ids) != 1:
        raise HufoniaError("HUFONIA scope contains a missing or extra definition")
    definition_id = definition_ids[0]
    scoped_sources = [item for item in sources.values() if item[0] == definition_id]
    scoped_manifests = [item for item in manifests.values() if item[0] == definition_id]
    scoped_observations = tuple(
        sorted(
            (
                item
                for item in observations.values()
                if item.benchmark_id == HUFONIA_BENCHMARK_ID
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
        raise HufoniaError("stored HUFONIA bundle differs from retained artifact and receipt")


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
    prepared: PreparedHufoniaBundle, *, inserted_rows: int, reused: bool
) -> HufoniaImportResult:
    return HufoniaImportResult(
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
    receipt: HufoniaAcquisitionReceipt,
) -> None:
    root = _lexical_absolute(repository_root)
    raw = _lexical_absolute(raw_artifact)
    receipt_file = _lexical_absolute(receipt_path)
    expected_raw = _lexical_absolute(
        root / PurePosixPath(receipt.raw_artifact_reference)
    )
    approved = root / "data" / "raw" / "reference_rates" / "mnb" / "hufonia"
    _validate_evidence_path_components(root, raw, receipt_file)
    if (
        raw != expected_raw
        or not raw.is_relative_to(approved)
        or receipt_file != raw.with_suffix(".receipt.json")
        or not receipt_file.is_relative_to(approved)
    ):
        raise HufoniaError("HUFONIA raw artifact and receipt locations differ from provenance")


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
        raise HufoniaError("HUFONIA retained evidence path contains a symlink component")


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


def _validate_year_headers(sheet_name: str, sheet: object) -> int:
    row_values = getattr(sheet, "row_values", None)
    nrows = getattr(sheet, "nrows", None)
    ncols = getattr(sheet, "ncols", None)
    if not callable(row_values) or type(nrows) is not int or type(ncols) is not int:
        raise HufoniaError("HUFONIA worksheet object is malformed")
    year = int(sheet_name)
    expected: tuple[tuple[str, str, str], ...]
    if year >= 2010:
        expected = (
            ("Date", "HUFONIA", "Turnover (mio HUF)"),
            ("dátum", "HUFONIA", "forgalom (m Ft)"),
        )
    elif year >= 2007:
        expected = (("dátum", "átlag", "forgalom (mFt)"),)
    else:
        expected = (("datum", "atlag", "forgalom (mFt)"),)
    if nrows <= len(expected):
        raise HufoniaError(f"HUFONIA sheet {sheet_name} contains no observations")
    for row_index, expected_row in enumerate(expected):
        values = tuple(row_values(row_index))
        if tuple(values[:3]) != expected_row or any(
            value != "" and value is not None for value in values[3:]
        ):
            raise HufoniaError(
                f"HUFONIA sheet {sheet_name} headers differ from the reviewed contract"
            )
    return len(expected)


def _validated_annotation(
    *,
    sheet_name: str,
    row_index: int,
    nonempty_extra: list[tuple[int, object]],
) -> str | None:
    if not nonempty_extra:
        return None
    if len(nonempty_extra) != 1 or nonempty_extra[0][0] != 3:
        raise HufoniaError("HUFONIA workbook contains unsupported extra cell data")
    value = nonempty_extra[0][1]
    expected = {
        ("2015", 226): _CORRECTION_NOTE,
        ("2016", 195): _DATE_BASIS_NOTE,
    }.get((sheet_name, row_index))
    if not isinstance(value, str) or value != expected:
        raise HufoniaError("HUFONIA workbook contains an unknown provider annotation")
    return value


def _parse_year_observation(
    *,
    book: Any,
    sheet_name: str,
    row_index: int,
    cells: Mapping[tuple[int, int], _BiffNumericCell],
    annotation: str | None,
) -> ParsedHufoniaObservation:
    sheet = book.sheet_by_name(sheet_name)
    date_cell = cells[(row_index, 0)]
    rate_cell = cells.get((row_index, 1))
    turnover_cell = cells[(row_index, 2)]
    if (
        date_cell.record_type not in {_BIFF_RK, _BIFF_MULRK}
        or turnover_cell.record_type not in {_BIFF_RK, _BIFF_MULRK}
        or date_cell.value is None
        or turnover_cell.value is None
    ):
        raise HufoniaError("HUFONIA observation uses an unsupported cell record or formula")
    if rate_cell is not None and (
        rate_cell.record_type not in {_BIFF_NUMBER, _BIFF_RK, _BIFF_MULRK}
        or rate_cell.value is None
    ):
        raise HufoniaError("HUFONIA rate uses an unsupported cell record or formula")
    checked_cells: tuple[tuple[int, _BiffNumericCell], ...] = (
        (0, date_cell),
        (2, turnover_cell),
    )
    if rate_cell is not None:
        checked_cells += ((1, rate_cell),)
    for column, raw_cell in checked_cells:
        if sheet.cell(row_index, column).xf_index != raw_cell.xf_index:
            raise HufoniaError("HUFONIA cell style identity is inconsistent")
    serial = _integral_decimal(date_cell, "observation date")
    if not Decimal(1) <= serial <= Decimal(100000):
        raise HufoniaError("HUFONIA observation date serial is outside the safe range")
    observation_date = _EXCEL_EPOCH + timedelta(days=int(serial))
    quote_decimal_places = 2 if observation_date < HUFONIA_NAMED_LAUNCH_DATE else 3
    expected_format = "0.00" if quote_decimal_places == 2 else "0.000"
    quantum = Decimal(1).scaleb(-quote_decimal_places)
    if rate_cell is None:
        raw_rate = sheet.cell_value(row_index, 1)
        if observation_date != date(2023, 4, 26) or raw_rate != "17.880":
            raise HufoniaError("HUFONIA workbook contains an unsupported text rate")
        rate = Decimal(raw_rate)
    else:
        format_map = book.format_map
        xf_list = book.xf_list
        format_string = format_map[xf_list[rate_cell.xf_index].format_key].format_str
        if format_string != expected_format and not (
            format_string == "General" and observation_date in _GENERAL_FORMAT_DATES
        ):
            raise HufoniaError("HUFONIA rate cell precision differs from the official display")
        assert rate_cell.value is not None
        rate = rate_cell.value.quantize(quantum, rounding=ROUND_HALF_UP)
    turnover = _integral_decimal(turnover_cell, "turnover")
    if turnover < 0:
        raise HufoniaError("HUFONIA turnover must not be negative")
    expected_annotation: str | None = None
    revision_indicator: str | None = None
    if observation_date == date(2015, 11, 19):
        expected_annotation = _CORRECTION_NOTE
        revision_indicator = _CORRECTION_NOTE
    elif observation_date == HUFONIA_TRADE_DATE_SEMANTICS_START:
        expected_annotation = _DATE_BASIS_NOTE
    if annotation != expected_annotation:
        raise HufoniaError("HUFONIA provider annotation is missing or attached to the wrong date")
    return ParsedHufoniaObservation(
        observation_date=observation_date,
        rate=rate,
        turnover_million_huf=turnover,
        quote_decimal_places=quote_decimal_places,
        observation_date_basis=(
            "VALUE_DATE"
            if observation_date < HUFONIA_TRADE_DATE_SEMANTICS_START
            else "TRADE_DATE"
        ),
        provider_annotation=annotation,
        revision_indicator=revision_indicator,
    )


def _integral_decimal(cell: _BiffNumericCell, field: str) -> Decimal:
    value = cell.value
    if value is None or not value.is_finite() or value != value.to_integral_value():
        raise HufoniaError(f"HUFONIA {field} must be an exact integer")
    return value


def _biff_numeric_cells(
    raw_bytes: bytes,
) -> tuple[dict[str, dict[tuple[int, int], _BiffNumericCell]], tuple[str, ...]]:
    try:
        document = CompDoc(raw_bytes)
        stream_names = {
            entry.name for entry in document.dirlist if entry.etype == 2
        }
        allowed_streams = {
            "Workbook",
            "\x05SummaryInformation",
            "\x05DocumentSummaryInformation",
        }
        if "Workbook" not in stream_names or not stream_names <= allowed_streams:
            raise HufoniaError("HUFONIA workbook contains an unsupported embedded stream")
        stream = document.get_named_stream("Workbook")
    except HufoniaError:
        raise
    except Exception as error:
        raise HufoniaError("HUFONIA compound document is malformed") from error
    if not isinstance(stream, bytes) or not stream:
        raise HufoniaError("HUFONIA compound document has no Workbook stream")
    sheet_offsets: list[tuple[str, int]] = []
    for code, data in _iter_biff_records(stream, 0):
        if code == _BIFF_BOUNDSHEET:
            sheet_offsets.append(_decode_boundsheet(data))
    sheet_names = tuple(name for name, _ in sheet_offsets)
    if len(sheet_names) != len(set(sheet_names)):
        raise HufoniaError("HUFONIA workbook contains duplicate sheet names")
    result: dict[str, dict[tuple[int, int], _BiffNumericCell]] = {}
    for sheet_name, offset in sheet_offsets:
        cells: dict[tuple[int, int], _BiffNumericCell] = {}
        for code, data in _iter_biff_records(stream, offset):
            if code == _BIFF_NUMBER:
                if len(data) != 14:
                    raise HufoniaError("HUFONIA NUMBER record is malformed")
                row, column, xf_index = unpack_from("<HHH", data, 0)
                value = _ieee_decimal(int.from_bytes(data[6:14], "little"))
                _store_biff_cell(
                    cells, row, column, _BiffNumericCell(value, code, xf_index)
                )
            elif code == _BIFF_RK:
                if len(data) != 10:
                    raise HufoniaError("HUFONIA RK record is malformed")
                row, column, xf_index, encoded = unpack_from("<HHHI", data, 0)
                _store_biff_cell(
                    cells,
                    row,
                    column,
                    _BiffNumericCell(_rk_decimal(encoded), code, xf_index),
                )
            elif code == _BIFF_MULRK:
                if len(data) < 12 or (len(data) - 6) % 6:
                    raise HufoniaError("HUFONIA MULRK record is malformed")
                row, first_column = unpack_from("<HH", data, 0)
                count = (len(data) - 6) // 6
                last_column = unpack_from("<H", data, len(data) - 2)[0]
                if last_column != first_column + count - 1:
                    raise HufoniaError("HUFONIA MULRK column range is malformed")
                for index in range(count):
                    xf_index, encoded = unpack_from("<HI", data, 4 + index * 6)
                    _store_biff_cell(
                        cells,
                        row,
                        first_column + index,
                        _BiffNumericCell(_rk_decimal(encoded), code, xf_index),
                    )
            elif code == _BIFF_FORMULA:
                raise HufoniaError("HUFONIA workbook contains a prohibited formula")
        result[sheet_name] = cells
    return result, sheet_names


def _iter_biff_records(stream: bytes, offset: int) -> Iterator[tuple[int, bytes]]:
    position = offset
    while True:
        if position < 0 or position + 4 > len(stream):
            raise HufoniaError("HUFONIA BIFF stream is truncated")
        code, length = unpack_from("<HH", stream, position)
        end = position + 4 + length
        if end > len(stream):
            raise HufoniaError("HUFONIA BIFF record exceeds the Workbook stream")
        data = stream[position + 4 : end]
        yield code, data
        position = end
        if code == _BIFF_EOF:
            return


def _decode_boundsheet(data: bytes) -> tuple[str, int]:
    if len(data) < 8:
        raise HufoniaError("HUFONIA BOUNDSHEET record is malformed")
    offset = unpack_from("<I", data, 0)[0]
    if data[4] != 0 or data[5] != 0:
        raise HufoniaError("HUFONIA workbook contains a hidden or non-worksheet sheet")
    character_count = data[6]
    unicode_text = bool(data[7] & 1)
    length = character_count * (2 if unicode_text else 1)
    if len(data) != 8 + length:
        raise HufoniaError("HUFONIA worksheet name encoding is malformed")
    try:
        name = data[8:].decode("utf-16le" if unicode_text else "latin-1")
    except UnicodeDecodeError as error:
        raise HufoniaError("HUFONIA worksheet name is malformed") from error
    return name, offset


def _store_biff_cell(
    cells: dict[tuple[int, int], _BiffNumericCell],
    row: int,
    column: int,
    cell: _BiffNumericCell,
) -> None:
    coordinate = (row, column)
    if coordinate in cells:
        raise HufoniaError("HUFONIA BIFF stream contains a duplicate numeric cell")
    cells[coordinate] = cell


def _rk_decimal(encoded: int) -> Decimal:
    divided_by_100 = bool(encoded & 1)
    if encoded & 2:
        signed = encoded if encoded < 2**31 else encoded - 2**32
        value = Decimal(signed >> 2)
    else:
        value = _ieee_decimal((encoded & 0xFFFFFFFC) << 32)
    return value / Decimal(100) if divided_by_100 else value


def _ieee_decimal(bits: int) -> Decimal:
    sign = -1 if bits >> 63 else 1
    exponent = (bits >> 52) & 0x7FF
    fraction = bits & ((1 << 52) - 1)
    if exponent == 0x7FF:
        raise HufoniaError("HUFONIA workbook contains a non-finite numeric cell")
    if exponent == 0:
        significand = fraction
        binary_exponent = -1074
    else:
        significand = (1 << 52) | fraction
        binary_exponent = exponent - 1023 - 52
    with localcontext() as context:
        context.prec = 800
        value = Decimal(sign * significand)
        if binary_exponent >= 0:
            return value * (Decimal(2) ** binary_exponent)
        return value / (Decimal(2) ** -binary_exponent)


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f").rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_effective_url(value: str) -> None:
    parsed = urlsplit(value)
    expected = urlsplit(HUFONIA_MACHINE_URL)
    if (
        parsed.scheme != expected.scheme
        or parsed.hostname != expected.hostname
        or parsed.port is not None
        or parsed.path != expected.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise HufoniaError("HUFONIA effective URL differs from the reviewed endpoint")


def _validate_xls_content_type(value: str) -> None:
    pieces = [part.strip() for part in value.split(";")]
    if pieces != ["application/vnd.ms-excel"]:
        raise HufoniaError(
            "HUFONIA response Content-Type must be application/vnd.ms-excel "
            "without encoding parameters"
        )


def _relative_artifact_reference(value: str) -> None:
    _text(value, "raw_artifact_reference")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise HufoniaError("HUFONIA raw artifact reference must be canonical and relative")


def _aware_timestamp(value: str, field: str) -> datetime:
    _text(value, field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise HufoniaError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HufoniaError(f"{field} must include a timezone")
    if parsed.astimezone(UTC).isoformat() != value:
        raise HufoniaError(f"{field} must be canonical UTC ISO format")
    return parsed


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HufoniaError(f"{field} must be an exact non-empty string")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HufoniaError("HUFONIA receipt contains a duplicate object key")
        result[key] = value
    return result
