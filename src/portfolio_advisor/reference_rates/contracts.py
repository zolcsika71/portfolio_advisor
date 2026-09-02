"""Immutable, exact reference-rate evidence contracts.

These types validate evidence supplied by reviewed provider adapters. They do
not align, compound, or calculate financial values.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from types import MappingProxyType
from urllib.parse import urlsplit

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.objectives.construction_policy import (
    CapitalDefensiveConstructionPolicy,
)

LEGACY_REFERENCE_RATE_CONTRACT_SCHEMA_VERSION = 1
REFERENCE_RATE_CONTRACT_SCHEMA_VERSION = 2
INTERNAL_EVIDENCE_IDENTITY_SCHEME = "SYSTEM_CANONICAL_ARTIFACT_V1"
_SUPPORTED_CURRENCIES = frozenset({"EUR", "USD", "HUF"})
_TOKEN = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]*$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class ReferenceRateContractError(ValueError):
    """Reference-rate evidence is incomplete, inexact, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ReferenceRateDefinition:
    """Versioned benchmark identity and official financial conventions."""

    contract_schema_version: int
    benchmark_id: str
    benchmark_name: str
    currency_code: str
    administrator: str
    series_identifier: str
    rate_units: str
    day_count_convention: str
    compounding_convention: str
    definition_version: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.contract_schema_version, bool)
            or self.contract_schema_version != REFERENCE_RATE_CONTRACT_SCHEMA_VERSION
        ):
            raise ReferenceRateContractError("unsupported reference-rate contract schema version")
        _token(self.benchmark_id, "benchmark_id")
        _text(self.benchmark_name, "benchmark_name")
        if self.currency_code not in _SUPPORTED_CURRENCIES:
            raise ReferenceRateContractError("unsupported reference-rate currency")
        _text(self.administrator, "administrator")
        _token(self.series_identifier, "series_identifier")
        if self.rate_units != "PERCENT_PER_ANNUM":
            raise ReferenceRateContractError("reference-rate units must be PERCENT_PER_ANNUM")
        _token(self.day_count_convention, "day_count_convention")
        _token(self.compounding_convention, "compounding_convention")
        if _SEMVER.fullmatch(self.definition_version) is None:
            raise ReferenceRateContractError("definition_version must be semantic versioning")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ReferenceRateDefinition:
        data = _exact_mapping(
            value,
            {
                "contract_schema_version",
                "benchmark_id",
                "benchmark_name",
                "currency_code",
                "administrator",
                "series_identifier",
                "rate_units",
                "day_count_convention",
                "compounding_convention",
                "definition_version",
            },
            "reference-rate definition",
        )
        return cls(**data)  # type: ignore[arg-type]

    def canonical_payload(self) -> dict[str, object]:
        return {
            "administrator": self.administrator,
            "benchmark_id": self.benchmark_id,
            "benchmark_name": self.benchmark_name,
            "compounding_convention": self.compounding_convention,
            "contract_schema_version": self.contract_schema_version,
            "currency_code": self.currency_code,
            "day_count_convention": self.day_count_convention,
            "definition_version": self.definition_version,
            "rate_units": self.rate_units,
            "series_identifier": self.series_identifier,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class ReferenceRateSource:
    """Reviewed source identity and explicit access/licensing state."""

    source_code: str
    benchmark_id: str
    source_organization: str
    official_page_url: str
    machine_readable_url: str
    response_format: str
    source_role: str
    authentication_requirement: str
    automated_use_status: str
    licensing_reference: str
    raw_retention_status: str

    def __post_init__(self) -> None:
        _token(self.source_code, "source_code")
        _token(self.benchmark_id, "benchmark_id")
        _text(self.source_organization, "source_organization")
        _https_url(self.official_page_url, "official_page_url")
        _https_url(self.machine_readable_url, "machine_readable_url")
        _token(self.response_format, "response_format")
        _choice(
            self.source_role,
            {"OFFICIAL_ADMINISTRATOR", "OFFICIAL_PLATFORM"},
            "source_role",
        )
        _choice(
            self.authentication_requirement,
            {"NONE", "REQUIRED"},
            "authentication_requirement",
        )
        _choice(
            self.automated_use_status,
            {"NOT_REVIEWED", "PERMITTED", "PROHIBITED"},
            "automated_use_status",
        )
        _text(self.licensing_reference, "licensing_reference")
        _choice(
            self.raw_retention_status,
            {"NOT_REVIEWED", "PERMITTED", "PROHIBITED"},
            "raw_retention_status",
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "authentication_requirement": self.authentication_requirement,
            "automated_use_status": self.automated_use_status,
            "benchmark_id": self.benchmark_id,
            "licensing_reference": self.licensing_reference,
            "machine_readable_url": self.machine_readable_url,
            "official_page_url": self.official_page_url,
            "raw_retention_status": self.raw_retention_status,
            "response_format": self.response_format,
            "source_code": self.source_code,
            "source_organization": self.source_organization,
            "source_role": self.source_role,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class ReferenceRateImportManifest:
    """Immutable provenance for one retained raw official response."""

    provenance_contract_version: int
    source_contract_fingerprint: str
    retrieval_timestamp: str
    request_url: str
    request_parameters: tuple[tuple[str, str], ...]
    response_content_type: str
    http_status: int
    raw_artifact_reference: str
    raw_artifact_sha256: str
    provider_dataset_version: str | None
    provider_dataset_version_source_field: str | None
    internal_evidence_identity_scheme: str
    internal_evidence_identity: str
    import_status: str
    dataset_fingerprint: str

    def __post_init__(self) -> None:
        if (
            type(self.provenance_contract_version) is not int
            or self.provenance_contract_version != REFERENCE_RATE_CONTRACT_SCHEMA_VERSION
        ):
            raise ReferenceRateContractError("unsupported manifest provenance contract version")
        _sha256(self.source_contract_fingerprint, "source_contract_fingerprint")
        _timestamp(self.retrieval_timestamp, "retrieval_timestamp")
        _https_url(self.request_url, "request_url")
        if (
            tuple(sorted(self.request_parameters)) != self.request_parameters
            or len(dict(self.request_parameters)) != len(self.request_parameters)
            or any(not isinstance(key, str) or not isinstance(item, str) for key, item in self.request_parameters)
        ):
            raise ReferenceRateContractError(
                "request_parameters must be unique sorted string pairs"
            )
        _text(self.response_content_type, "response_content_type")
        if isinstance(self.http_status, bool) or self.http_status != 200:
            raise ReferenceRateContractError("only a successful HTTP 200 response may be admitted")
        _relative_reference(self.raw_artifact_reference)
        _sha256(self.raw_artifact_sha256, "raw_artifact_sha256")
        _optional_provider_pair(
            self.provider_dataset_version,
            self.provider_dataset_version_source_field,
            "provider_dataset_version",
            "provider_dataset_version_source_field",
        )
        if self.internal_evidence_identity_scheme != INTERNAL_EVIDENCE_IDENTITY_SCHEME:
            raise ReferenceRateContractError("unsupported internal evidence identity scheme")
        _sha256(self.internal_evidence_identity, "internal_evidence_identity")
        _choice(
            self.import_status,
            {"VALIDATED_ADMITTED", "VALIDATED_REJECTED"},
            "import_status",
        )
        _sha256(self.dataset_fingerprint, "dataset_fingerprint")
        expected_identity = internal_evidence_identity(
            source_contract_fingerprint=self.source_contract_fingerprint,
            retrieval_timestamp=self.retrieval_timestamp,
            request_url=self.request_url,
            request_parameters=self.request_parameters,
            response_content_type=self.response_content_type,
            http_status=self.http_status,
            raw_artifact_reference=self.raw_artifact_reference,
            raw_artifact_sha256=self.raw_artifact_sha256,
            scheme=self.internal_evidence_identity_scheme,
        )
        if self.internal_evidence_identity != expected_identity:
            raise ReferenceRateContractError(
                "internal evidence identity differs from canonical artifact evidence"
            )
        if _looks_system_generated_provider_identity(
            self.provider_dataset_version,
            internal_values={
            self.internal_evidence_identity,
            self.raw_artifact_sha256,
            self.dataset_fingerprint,
            self.retrieval_timestamp,
                self.request_url,
                self.raw_artifact_reference,
                *(key for key, _ in self.request_parameters),
                *(value for _, value in self.request_parameters),
            },
        ):
            raise ReferenceRateContractError(
                "system-generated evidence must not be presented as provider dataset metadata"
            )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "dataset_fingerprint": self.dataset_fingerprint,
            "http_status": self.http_status,
            "internal_evidence_identity": self.internal_evidence_identity,
            "internal_evidence_identity_scheme": self.internal_evidence_identity_scheme,
            "import_status": self.import_status,
            "provider_dataset_version": self.provider_dataset_version,
            "provider_dataset_version_source_field": (
                self.provider_dataset_version_source_field
            ),
            "provenance_contract_version": self.provenance_contract_version,
            "raw_artifact_reference": self.raw_artifact_reference,
            "raw_artifact_sha256": self.raw_artifact_sha256,
            "request_parameters": dict(self.request_parameters),
            "request_url": self.request_url,
            "response_content_type": self.response_content_type,
            "retrieval_timestamp": self.retrieval_timestamp,
            "source_contract_fingerprint": self.source_contract_fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class ReferenceRateObservation:
    """One exact admitted observation and its revision lineage."""

    provenance_contract_version: int
    benchmark_id: str
    source_contract_fingerprint: str
    import_manifest_fingerprint: str
    observation_date: date
    provider_publication_date: date | None
    rate: Decimal
    provider_revision_id: str | None
    provider_revision_id_source_field: str | None
    provider_revision_indicator: str | None
    provider_revision_indicator_source_field: str | None
    provider_revision_status: str
    provider_revision_contract_id: str | None
    provider_revision_contract_version: str | None
    provider_revision_contract_revision_indicator_value: str | None
    provider_revision_contract_authoritative_reference: str | None
    provider_revision_contract_fingerprint: str | None
    provider_publication_value: str | None
    provider_publication_value_kind: str | None
    provider_publication_source_field: str | None
    availability_basis: str
    availability_boundary_utc: str
    availability_derivation_rule_id: str | None
    availability_derivation_rule_version: str | None
    availability_policy_reference: str | None
    availability_calendar_id: str | None
    availability_calendar_version: str | None
    availability_calendar_fingerprint: str | None
    revision_sequence: int
    supersedes_observation_fingerprint: str | None
    is_current: bool
    quality_status: str

    def __post_init__(self) -> None:
        if (
            type(self.provenance_contract_version) is not int
            or self.provenance_contract_version != REFERENCE_RATE_CONTRACT_SCHEMA_VERSION
        ):
            raise ReferenceRateContractError("unsupported observation provenance contract version")
        _token(self.benchmark_id, "benchmark_id")
        _sha256(self.source_contract_fingerprint, "source_contract_fingerprint")
        _sha256(self.import_manifest_fingerprint, "import_manifest_fingerprint")
        if type(self.observation_date) is not date:
            raise ReferenceRateContractError("observation_date must be an exact date")
        if self.provider_publication_date is not None:
            if type(self.provider_publication_date) is not date:
                raise ReferenceRateContractError(
                    "provider_publication_date must be an exact date when supplied"
                )
            if self.provider_publication_date < self.observation_date:
                raise ReferenceRateContractError(
                    "provider publication date precedes observation date"
                )
        if not isinstance(self.rate, Decimal) or not self.rate.is_finite():
            raise ReferenceRateContractError("rate must be a finite exact Decimal")
        _optional_provider_pair(
            self.provider_revision_id,
            self.provider_revision_id_source_field,
            "provider_revision_id",
            "provider_revision_id_source_field",
        )
        if type(self.revision_sequence) is not int or self.revision_sequence < 1:
            raise ReferenceRateContractError("revision_sequence must be a positive integer")
        _validate_revision_indicator(self)
        _validate_provider_revision_contract(self)
        _validate_provider_publication(self)
        _validate_availability_shape(self)
        if self.revision_sequence == 1 and self.supersedes_observation_fingerprint is not None:
            raise ReferenceRateContractError("initial observation cannot supersede another revision")
        if self.revision_sequence > 1:
            if self.supersedes_observation_fingerprint is None:
                raise ReferenceRateContractError("a later revision must identify its predecessor")
            _sha256(
                self.supersedes_observation_fingerprint,
                "supersedes_observation_fingerprint",
            )
        if type(self.is_current) is not bool:
            raise ReferenceRateContractError("is_current must be boolean")
        if self.quality_status != "ADMITTED_VALIDATED":
            raise ReferenceRateContractError("observation quality must be ADMITTED_VALIDATED")

    @property
    def rate_decimal(self) -> str:
        rendered = format(self.rate, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        if rendered in {"-0", ""}:
            return "0"
        return rendered

    def canonical_payload(self) -> dict[str, object]:
        return {
            "availability_basis": self.availability_basis,
            "availability_boundary_utc": self.availability_boundary_utc,
            "availability_calendar_fingerprint": self.availability_calendar_fingerprint,
            "availability_calendar_id": self.availability_calendar_id,
            "availability_calendar_version": self.availability_calendar_version,
            "availability_derivation_rule_id": self.availability_derivation_rule_id,
            "availability_derivation_rule_version": self.availability_derivation_rule_version,
            "availability_policy_reference": self.availability_policy_reference,
            "benchmark_id": self.benchmark_id,
            "import_manifest_fingerprint": self.import_manifest_fingerprint,
            "is_current": self.is_current,
            "observation_date": self.observation_date.isoformat(),
            "provider_revision_id": self.provider_revision_id,
            "provider_revision_id_source_field": self.provider_revision_id_source_field,
            "provider_revision_indicator": self.provider_revision_indicator,
            "provider_revision_indicator_source_field": (
                self.provider_revision_indicator_source_field
            ),
            "provider_revision_status": self.provider_revision_status,
            "provider_revision_contract_authoritative_reference": (
                self.provider_revision_contract_authoritative_reference
            ),
            "provider_revision_contract_fingerprint": (
                self.provider_revision_contract_fingerprint
            ),
            "provider_revision_contract_id": self.provider_revision_contract_id,
            "provider_revision_contract_revision_indicator_value": (
                self.provider_revision_contract_revision_indicator_value
            ),
            "provider_revision_contract_version": self.provider_revision_contract_version,
            "provider_publication_date": (
                self.provider_publication_date.isoformat()
                if self.provider_publication_date is not None
                else None
            ),
            "provider_publication_source_field": self.provider_publication_source_field,
            "provider_publication_value": self.provider_publication_value,
            "provider_publication_value_kind": self.provider_publication_value_kind,
            "provenance_contract_version": self.provenance_contract_version,
            "quality_status": self.quality_status,
            "rate_decimal": self.rate_decimal,
            "revision_sequence": self.revision_sequence,
            "source_contract_fingerprint": self.source_contract_fingerprint,
            "supersedes_observation_fingerprint": self.supersedes_observation_fingerprint,
        }

    def fingerprint_payload(self) -> dict[str, object]:
        """Return immutable evidence identity, excluding the derived current projection."""
        payload = self.canonical_payload()
        del payload["is_current"]
        return payload

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.fingerprint_payload())


@dataclass(frozen=True, slots=True)
class ApprovedAvailabilitySchedule:
    """Reviewed, versioned calendar evidence used for deterministic derivation."""

    rule_id: str
    rule_version: str
    authoritative_policy_reference: str
    benchmark_id: str
    source_contract_fingerprint: str
    calendar_id: str
    calendar_version: str
    boundaries: tuple[tuple[date, str], ...]

    def __post_init__(self) -> None:
        _token(self.rule_id, "rule_id")
        if _SEMVER.fullmatch(self.rule_version) is None:
            raise ReferenceRateContractError("rule_version must be semantic versioning")
        _https_url(self.authoritative_policy_reference, "authoritative_policy_reference")
        _token(self.benchmark_id, "benchmark_id")
        _sha256(self.source_contract_fingerprint, "source_contract_fingerprint")
        _token(self.calendar_id, "calendar_id")
        if _SEMVER.fullmatch(self.calendar_version) is None:
            raise ReferenceRateContractError("calendar_version must be semantic versioning")
        if not self.boundaries:
            raise ReferenceRateContractError("approved calendar must contain boundaries")
        dates = tuple(item[0] for item in self.boundaries)
        if any(type(item) is not date for item in dates) or dates != tuple(sorted(set(dates))):
            raise ReferenceRateContractError(
                "approved calendar dates must be unique and canonically ordered"
            )
        for _, boundary in self.boundaries:
            _canonical_utc_timestamp(boundary, "approved availability boundary")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "authoritative_policy_reference": self.authoritative_policy_reference,
            "benchmark_id": self.benchmark_id,
            "boundaries": [
                {"availability_boundary_utc": boundary, "observation_date": day.isoformat()}
                for day, boundary in self.boundaries
            ],
            "calendar_id": self.calendar_id,
            "calendar_version": self.calendar_version,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "source_contract_fingerprint": self.source_contract_fingerprint,
        }

    @property
    def calendar_fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())

    def derive(self, observation_date: date) -> str:
        for day, boundary in self.boundaries:
            if day == observation_date:
                return boundary
        raise ReferenceRateContractError(
            "approved calendar has no boundary for observation date"
        )


@dataclass(frozen=True, slots=True)
class EvidenceTransition:
    """Deterministic comparison result; conflicts are never admitted rows."""

    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderRevisionTransitionContract:
    """Separately reviewed authority for appending provider-declared revisions."""

    contract_id: str
    contract_version: str
    benchmark_id: str
    source_contract_fingerprint: str
    revision_indicator_source_field: str
    revision_indicator_value: str
    authoritative_reference: str

    def __post_init__(self) -> None:
        _token(self.contract_id, "provider revision contract_id")
        if _SEMVER.fullmatch(self.contract_version) is None:
            raise ReferenceRateContractError(
                "provider revision contract_version must be semantic versioning"
            )
        _token(self.benchmark_id, "provider revision benchmark_id")
        _sha256(
            self.source_contract_fingerprint,
            "provider revision source_contract_fingerprint",
        )
        _text(
            self.revision_indicator_source_field,
            "provider revision indicator source field",
        )
        _text(self.revision_indicator_value, "provider revision indicator value")
        _https_url(self.authoritative_reference, "provider revision authoritative_reference")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "authoritative_reference": self.authoritative_reference,
            "benchmark_id": self.benchmark_id,
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "revision_indicator_source_field": self.revision_indicator_source_field,
            "revision_indicator_value": self.revision_indicator_value,
            "source_contract_fingerprint": self.source_contract_fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


def internal_evidence_identity(
    *,
    source_contract_fingerprint: str,
    retrieval_timestamp: str,
    request_url: str,
    request_parameters: tuple[tuple[str, str], ...],
    response_content_type: str,
    http_status: int,
    raw_artifact_reference: str,
    raw_artifact_sha256: str,
    scheme: str = INTERNAL_EVIDENCE_IDENTITY_SCHEME,
) -> str:
    """Derive the system-labelled snapshot ID from immutable artifact evidence."""
    return canonical_fingerprint(
        {
            "http_status": http_status,
            "identity_scheme": scheme,
            "raw_artifact_reference": raw_artifact_reference,
            "raw_artifact_sha256": raw_artifact_sha256,
            "request_parameters": dict(request_parameters),
            "request_url": request_url,
            "response_content_type": response_content_type,
            "retrieval_timestamp": retrieval_timestamp,
            "source_contract_fingerprint": source_contract_fingerprint,
        }
    )


def canonical_utc_timestamp(value: str) -> str:
    """Normalize an exact aware timestamp to fixed-width UTC for safe lexical ordering."""
    parsed = _timestamp(value, "timestamp")
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def validate_observation_availability(
    observation: ReferenceRateObservation,
    manifest: ReferenceRateImportManifest,
    *,
    approved_schedules: tuple[ApprovedAvailabilitySchedule, ...] = (),
) -> None:
    """Bind one availability assertion to retrieval evidence or a reviewed schedule."""
    boundary = _canonical_utc_timestamp(
        observation.availability_boundary_utc, "availability_boundary_utc"
    )
    retrieval = canonical_utc_timestamp(manifest.retrieval_timestamp)
    if _parse_canonical_utc(boundary).date() < observation.observation_date:
        raise ReferenceRateContractError(
            "availability boundary precedes observation value date"
        )
    if boundary > retrieval:
        raise ReferenceRateContractError("availability boundary follows artifact retrieval")
    if observation.availability_basis == "RETRIEVAL_BOUND":
        if boundary != retrieval:
            raise ReferenceRateContractError(
                "retrieval-bound availability must equal exact retrieval time"
            )
        return
    if observation.availability_basis == "PROVIDER_REPORTED":
        assert observation.provider_publication_value is not None
        provider_boundary = canonical_utc_timestamp(observation.provider_publication_value)
        if boundary != provider_boundary:
            raise ReferenceRateContractError(
                "provider-reported boundary differs from provider publication timestamp"
            )
        return
    matches = [
        item
        for item in approved_schedules
        if (
            item.rule_id == observation.availability_derivation_rule_id
            and item.rule_version == observation.availability_derivation_rule_version
            and item.authoritative_policy_reference == observation.availability_policy_reference
            and item.benchmark_id == observation.benchmark_id
            and item.source_contract_fingerprint
            == observation.source_contract_fingerprint
            and item.calendar_id == observation.availability_calendar_id
            and item.calendar_version == observation.availability_calendar_version
            and item.calendar_fingerprint == observation.availability_calendar_fingerprint
        )
    ]
    if len(matches) != 1:
        raise ReferenceRateContractError(
            "official-schedule availability has no unique approved rule/calendar"
        )
    if matches[0].derive(observation.observation_date) != boundary:
        raise ReferenceRateContractError(
            "official-schedule availability is not reproducible"
        )


def observations_available_as_of(
    observations: tuple[ReferenceRateObservation, ...], cutoff: datetime
) -> tuple[ReferenceRateObservation, ...]:
    """Return the latest revision known by an aware cutoff without current-state leakage."""
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ReferenceRateContractError("as-of cutoff must be a timezone-aware datetime")
    cutoff_utc = cutoff.astimezone(UTC)
    available = [
        item
        for item in observations
        if _parse_canonical_utc(item.availability_boundary_utc) <= cutoff_utc
    ]
    source_scope: dict[str, set[str]] = defaultdict(set)
    for item in observations:
        source_scope[item.benchmark_id].add(item.source_contract_fingerprint)
    if any(len(sources) != 1 for sources in source_scope.values()):
        raise ReferenceRateContractError(
            "as-of evidence contains cross-source benchmark contamination"
        )
    selected: dict[tuple[str, str, date], ReferenceRateObservation] = {}
    for item in sorted(
        available,
        key=lambda value: (
            value.benchmark_id,
            value.source_contract_fingerprint,
            value.observation_date,
            value.revision_sequence,
        ),
    ):
        key = (
            item.benchmark_id,
            item.source_contract_fingerprint,
            item.observation_date,
        )
        previous = selected.get(key)
        if previous is not None and item.revision_sequence <= previous.revision_sequence:
            raise ReferenceRateContractError("available revision sequences are not strictly ordered")
        selected[key] = item
    return tuple(selected[key] for key in sorted(selected))


def classify_evidence_transition(
    *,
    previous_internal_evidence_identity: str,
    incoming_internal_evidence_identity: str,
    previous_rate: Decimal,
    incoming_rate: Decimal,
    benchmark_id: str,
    source_contract_fingerprint: str,
    provider_revision_status: str,
    provider_revision_indicator: str | None,
    provider_revision_indicator_source_field: str | None,
    provider_revision_contract: ProviderRevisionTransitionContract | None = None,
) -> EvidenceTransition:
    """Classify replay, internal snapshot change, authorized revision, or conflict."""
    _sha256(previous_internal_evidence_identity, "previous_internal_evidence_identity")
    _sha256(incoming_internal_evidence_identity, "incoming_internal_evidence_identity")
    if (
        previous_internal_evidence_identity == incoming_internal_evidence_identity
        and previous_rate == incoming_rate
    ):
        return EvidenceTransition("IDENTICAL_REPLAY", "internal evidence identity is unchanged")
    if (
        provider_revision_status == "PROVIDER_EXPLICIT_REVISION"
        and provider_revision_contract is not None
        and provider_revision_contract.benchmark_id == benchmark_id
        and provider_revision_contract.source_contract_fingerprint
        == source_contract_fingerprint
        and provider_revision_contract.revision_indicator_source_field
        == provider_revision_indicator_source_field
        and provider_revision_contract.revision_indicator_value
        == provider_revision_indicator
    ):
        return EvidenceTransition(
            "AUTHORIZED_PROVIDER_REVISION",
            "provider revision evidence and reviewed transition contract authorize append",
        )
    if previous_rate == incoming_rate:
        return EvidenceTransition(
            "INTERNAL_EVIDENCE_SNAPSHOT_CHANGED",
            "artifact snapshot changed without changing the observation value",
        )
    return EvidenceTransition(
        "CONFLICTING_EVIDENCE",
        "changed value lacks an authorized provider-revision transition",
    )


def validate_policy_binding(
    definition: ReferenceRateDefinition,
    source: ReferenceRateSource,
    policy: CapitalDefensiveConstructionPolicy,
) -> None:
    """Require exact binding to the reviewed currency benchmark and official page."""
    matches = [item for item in policy.reference_rates if item.currency == definition.currency_code]
    if len(matches) != 1:
        raise ReferenceRateContractError("policy has no unique benchmark for currency")
    approved = matches[0]
    if (
        approved.benchmark != definition.benchmark_name
        or approved.administrator != definition.administrator
        or approved.official_source_url != source.official_page_url
        or source.benchmark_id != definition.benchmark_id
        or source.source_organization != definition.administrator
    ):
        raise ReferenceRateContractError("reference-rate definition/source differs from policy")


def canonical_request_parameters(value: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Freeze exact request parameters for an immutable manifest."""
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise ReferenceRateContractError("request parameters must be string-to-string")
    return tuple(sorted(value.items()))


def _exact_mapping(
    value: Mapping[str, object], expected: set[str], label: str
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ReferenceRateContractError(f"{label} must be a mapping")
    keys = set(value)
    if keys != expected or any(not isinstance(key, str) for key in keys):
        raise ReferenceRateContractError(
            f"{label} fields differ: unknown={sorted(str(key) for key in keys - expected)}, "
            f"missing={sorted(expected - keys)}"
        )
    return dict(value)


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReferenceRateContractError(f"{field} must be an exact non-empty string")


def _token(value: object, field: str) -> None:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ReferenceRateContractError(f"{field} must be a canonical token")


def _choice(value: object, choices: set[str], field: str) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ReferenceRateContractError(f"unsupported {field}")


def _optional_provider_pair(
    value: object,
    source_field: object,
    value_name: str,
    source_name: str,
) -> None:
    if (value is None) != (source_field is None):
        raise ReferenceRateContractError(
            f"{value_name} and {source_name} must both be supplied or both be absent"
        )
    if value is not None:
        _text(value, value_name)
        _text(source_field, source_name)


def _validate_revision_indicator(observation: ReferenceRateObservation) -> None:
    status = observation.provider_revision_status
    indicator = observation.provider_revision_indicator
    source = observation.provider_revision_indicator_source_field
    choices = {
        "PROVIDER_EXPLICIT_REVISION",
        "PROVIDER_EXPLICIT_NO_REVISION",
        "PROVIDER_EMPTY_REVISION_INDICATOR",
        "PROVIDER_REVISION_FIELD_NOT_SUPPLIED",
    }
    _choice(status, choices, "provider_revision_status")
    if status == "PROVIDER_REVISION_FIELD_NOT_SUPPLIED":
        if indicator is not None or source is not None:
            raise ReferenceRateContractError(
                "missing provider revision field requires null indicator and source"
            )
        return
    _text(source, "provider_revision_indicator_source_field")
    if status == "PROVIDER_EMPTY_REVISION_INDICATOR":
        if indicator != "":
            raise ReferenceRateContractError(
                "empty provider revision status requires an exact empty indicator"
            )
        return
    if not isinstance(indicator, str) or not indicator:
        raise ReferenceRateContractError(
            "explicit provider revision status requires a non-empty raw indicator"
        )


def _validate_provider_revision_contract(observation: ReferenceRateObservation) -> None:
    supplied = (
        observation.provider_revision_contract_id,
        observation.provider_revision_contract_version,
        observation.provider_revision_contract_revision_indicator_value,
        observation.provider_revision_contract_authoritative_reference,
        observation.provider_revision_contract_fingerprint,
    )
    if all(value is None for value in supplied):
        if observation.revision_sequence > 1:
            raise ReferenceRateContractError(
                "later revision requires a separately validated provider-revision contract"
            )
        return
    if any(value is None for value in supplied):
        raise ReferenceRateContractError(
            "provider-revision contract identity must be supplied completely"
        )
    if observation.revision_sequence == 1:
        raise ReferenceRateContractError(
            "initial observation cannot claim a provider-revision transition contract"
        )
    if observation.provider_revision_status != "PROVIDER_EXPLICIT_REVISION":
        raise ReferenceRateContractError(
            "provider-revision contract requires explicit provider revision evidence"
        )
    if observation.provider_revision_indicator_source_field is None:
        raise ReferenceRateContractError(
            "provider-revision contract requires a provider revision source field"
        )
    assert observation.provider_revision_contract_id is not None
    assert observation.provider_revision_contract_version is not None
    assert observation.provider_revision_contract_revision_indicator_value is not None
    assert observation.provider_revision_contract_authoritative_reference is not None
    assert observation.provider_revision_contract_fingerprint is not None
    contract = ProviderRevisionTransitionContract(
        contract_id=observation.provider_revision_contract_id,
        contract_version=observation.provider_revision_contract_version,
        benchmark_id=observation.benchmark_id,
        source_contract_fingerprint=observation.source_contract_fingerprint,
        revision_indicator_source_field=(
            observation.provider_revision_indicator_source_field
        ),
        revision_indicator_value=(
            observation.provider_revision_contract_revision_indicator_value
        ),
        authoritative_reference=(
            observation.provider_revision_contract_authoritative_reference
        ),
    )
    _sha256(
        observation.provider_revision_contract_fingerprint,
        "provider_revision_contract_fingerprint",
    )
    if observation.provider_revision_contract_fingerprint != contract.fingerprint:
        raise ReferenceRateContractError(
            "provider-revision contract fingerprint is inconsistent"
        )
    if observation.provider_revision_indicator != contract.revision_indicator_value:
        raise ReferenceRateContractError(
            "provider revision indicator differs from the approved transition value"
        )


def _validate_provider_publication(observation: ReferenceRateObservation) -> None:
    supplied = (
        observation.provider_publication_value,
        observation.provider_publication_value_kind,
        observation.provider_publication_source_field,
    )
    if all(value is None for value in supplied):
        if observation.provider_publication_date is not None:
            raise ReferenceRateContractError(
                "provider publication date requires actual provider publication evidence"
            )
        return
    if any(value is None for value in supplied):
        raise ReferenceRateContractError(
            "provider publication value, kind, and source field must be supplied together"
        )
    assert observation.provider_publication_value is not None
    assert observation.provider_publication_value_kind is not None
    assert observation.provider_publication_source_field is not None
    if observation.provider_publication_value == "" or (
        observation.provider_publication_value
        != observation.provider_publication_value.strip()
    ):
        raise ReferenceRateContractError("provider publication value must be exact and non-empty")
    _choice(
        observation.provider_publication_value_kind,
        {"DATE", "TIMESTAMP"},
        "provider_publication_value_kind",
    )
    _text(observation.provider_publication_source_field, "provider_publication_source_field")
    if observation.provider_publication_value_kind == "DATE":
        parsed = _exact_date(observation.provider_publication_value, "provider publication value")
        if observation.provider_publication_date != parsed:
            raise ReferenceRateContractError(
                "provider publication date differs from provider date value"
            )
    else:
        _timestamp(observation.provider_publication_value, "provider publication value")
        if (
            observation.provider_publication_date is not None
            and observation.provider_publication_date.isoformat()
            != observation.provider_publication_value[:10]
        ):
            raise ReferenceRateContractError(
                "provider publication date differs from provider timestamp date"
            )


def _validate_availability_shape(observation: ReferenceRateObservation) -> None:
    _choice(
        observation.availability_basis,
        {"PROVIDER_REPORTED", "OFFICIAL_SCHEDULE_DERIVED", "RETRIEVAL_BOUND"},
        "availability_basis",
    )
    boundary = _canonical_utc_timestamp(
        observation.availability_boundary_utc, "availability_boundary_utc"
    )
    if _parse_canonical_utc(boundary).date() < observation.observation_date:
        raise ReferenceRateContractError(
            "availability boundary precedes observation value date"
        )
    schedule = (
        observation.availability_derivation_rule_id,
        observation.availability_derivation_rule_version,
        observation.availability_policy_reference,
        observation.availability_calendar_id,
        observation.availability_calendar_version,
        observation.availability_calendar_fingerprint,
    )
    if observation.availability_basis == "OFFICIAL_SCHEDULE_DERIVED":
        if any(value is None for value in schedule):
            raise ReferenceRateContractError(
                "official-schedule availability requires rule, policy, and calendar evidence"
            )
        assert observation.availability_derivation_rule_id is not None
        assert observation.availability_derivation_rule_version is not None
        assert observation.availability_policy_reference is not None
        assert observation.availability_calendar_id is not None
        assert observation.availability_calendar_version is not None
        assert observation.availability_calendar_fingerprint is not None
        _token(observation.availability_derivation_rule_id, "availability_derivation_rule_id")
        if _SEMVER.fullmatch(observation.availability_derivation_rule_version) is None:
            raise ReferenceRateContractError(
                "availability_derivation_rule_version must be semantic versioning"
            )
        _https_url(observation.availability_policy_reference, "availability_policy_reference")
        _token(observation.availability_calendar_id, "availability_calendar_id")
        if _SEMVER.fullmatch(observation.availability_calendar_version) is None:
            raise ReferenceRateContractError(
                "availability_calendar_version must be semantic versioning"
            )
        _sha256(
            observation.availability_calendar_fingerprint,
            "availability_calendar_fingerprint",
        )
        if (
            observation.provider_publication_value is not None
            and observation.provider_publication_value_kind != "DATE"
        ):
            raise ReferenceRateContractError(
                "official-schedule availability may retain only provider date metadata"
            )
        return
    if any(value is not None for value in schedule):
        raise ReferenceRateContractError(
            "non-schedule availability cannot contain schedule derivation evidence"
        )
    if observation.availability_basis == "PROVIDER_REPORTED" and (
        observation.provider_publication_value is None
        or observation.provider_publication_value_kind != "TIMESTAMP"
        or observation.provider_publication_source_field is None
    ):
        raise ReferenceRateContractError(
            "provider-reported availability requires an actual provider timestamp and source field"
        )


def _looks_system_generated_provider_identity(
    value: str | None, *, internal_values: set[str]
) -> bool:
    if value is None:
        return False
    return (
        value in internal_values
        or value.upper() == "INITIAL"
        or _UUID.fullmatch(value) is not None
    )


def _sha256(value: object, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ReferenceRateContractError(f"{field} must be lowercase SHA-256")


def _https_url(value: object, field: str) -> None:
    _text(value, field)
    assert isinstance(value, str)
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise ReferenceRateContractError(f"{field} must be a public HTTPS URL")


def _timestamp(value: object, field: str) -> datetime:
    _text(value, field)
    assert isinstance(value, str)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise ReferenceRateContractError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReferenceRateContractError(f"{field} must include a timezone")
    return parsed


def _canonical_utc_timestamp(value: object, field: str) -> str:
    _text(value, field)
    assert isinstance(value, str)
    parsed = _timestamp(value, field)
    canonical = parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if value != canonical:
        raise ReferenceRateContractError(f"{field} must be fixed-width canonical UTC")
    return canonical


def _parse_canonical_utc(value: str) -> datetime:
    _canonical_utc_timestamp(value, "availability_boundary_utc")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _exact_date(value: str, field: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ReferenceRateContractError(f"{field} must be YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ReferenceRateContractError(f"{field} must be canonical YYYY-MM-DD")
    return parsed


def _relative_reference(value: object) -> None:
    _text(value, "raw_artifact_reference")
    assert isinstance(value, str)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ReferenceRateContractError("raw_artifact_reference must be relative POSIX")


def canonical_contract_json(value: object) -> str:
    """Expose the shared canonical representation for audit tooling."""
    return canonical_json(value)


EMPTY_REQUEST_PARAMETERS: Mapping[str, str] = MappingProxyType({})
