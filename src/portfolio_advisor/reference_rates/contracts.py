"""Immutable, exact reference-rate evidence contracts.

These types validate evidence supplied by reviewed provider adapters. They do
not align, compound, or calculate financial values.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from types import MappingProxyType
from urllib.parse import urlsplit

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.objectives.construction_policy import (
    CapitalDefensiveConstructionPolicy,
)

REFERENCE_RATE_CONTRACT_SCHEMA_VERSION = 1
_SUPPORTED_CURRENCIES = frozenset({"EUR", "USD", "HUF"})
_TOKEN = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]*$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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

    source_contract_fingerprint: str
    retrieval_timestamp: str
    request_url: str
    request_parameters: tuple[tuple[str, str], ...]
    response_content_type: str
    http_status: int
    raw_artifact_reference: str
    raw_artifact_sha256: str
    provider_dataset_version: str
    import_status: str
    dataset_fingerprint: str

    def __post_init__(self) -> None:
        _sha256(self.source_contract_fingerprint, "source_contract_fingerprint")
        _timestamp(self.retrieval_timestamp)
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
        _text(self.provider_dataset_version, "provider_dataset_version")
        _choice(
            self.import_status,
            {"VALIDATED_ADMITTED", "VALIDATED_REJECTED"},
            "import_status",
        )
        _sha256(self.dataset_fingerprint, "dataset_fingerprint")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "dataset_fingerprint": self.dataset_fingerprint,
            "http_status": self.http_status,
            "import_status": self.import_status,
            "provider_dataset_version": self.provider_dataset_version,
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

    benchmark_id: str
    source_contract_fingerprint: str
    import_manifest_fingerprint: str
    observation_date: date
    publication_date: date
    rate: Decimal
    provider_revision_id: str
    revision_sequence: int
    supersedes_observation_fingerprint: str | None
    is_current: bool
    quality_status: str

    def __post_init__(self) -> None:
        _token(self.benchmark_id, "benchmark_id")
        _sha256(self.source_contract_fingerprint, "source_contract_fingerprint")
        _sha256(self.import_manifest_fingerprint, "import_manifest_fingerprint")
        if type(self.observation_date) is not date or type(self.publication_date) is not date:
            raise ReferenceRateContractError("observation and publication dates must be exact dates")
        if self.publication_date < self.observation_date:
            raise ReferenceRateContractError("publication date precedes observation date")
        if not isinstance(self.rate, Decimal) or not self.rate.is_finite():
            raise ReferenceRateContractError("rate must be a finite exact Decimal")
        _text(self.provider_revision_id, "provider_revision_id")
        if type(self.revision_sequence) is not int or self.revision_sequence < 1:
            raise ReferenceRateContractError("revision_sequence must be a positive integer")
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
            "benchmark_id": self.benchmark_id,
            "import_manifest_fingerprint": self.import_manifest_fingerprint,
            "is_current": self.is_current,
            "observation_date": self.observation_date.isoformat(),
            "provider_revision_id": self.provider_revision_id,
            "publication_date": self.publication_date.isoformat(),
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


def _sha256(value: object, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ReferenceRateContractError(f"{field} must be lowercase SHA-256")


def _https_url(value: object, field: str) -> None:
    _text(value, field)
    assert isinstance(value, str)
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise ReferenceRateContractError(f"{field} must be a public HTTPS URL")


def _timestamp(value: object) -> None:
    _text(value, "retrieval_timestamp")
    assert isinstance(value, str)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ReferenceRateContractError("retrieval_timestamp must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReferenceRateContractError("retrieval_timestamp must include a timezone")


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
