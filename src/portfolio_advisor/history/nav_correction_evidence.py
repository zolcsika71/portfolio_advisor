"""Offline structural validation for proposed NAV-correction evidence candidates.

This module checks caller-selected local bytes, structured bindings, and exact
observation keys.  It does not interpret PDF prose, authenticate an issuer,
establish historical availability, admit evidence, select a NAV, or change any
persistent or operational state.

The filesystem threat model is the repository's operator-controlled local
workspace.  Symlink components are rejected, and each non-SQLite structured
artifact is hashed and parsed from one byte buffer.  SQLite snapshots are
hashed once and deserialized from those same bytes into an in-memory,
query-only connection; no database file is opened by SQLite.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import urlsplit

from portfolio_advisor.canonical import canonical_fingerprint

CANDIDATE_SCHEMA_VERSION: Final = 1
CANDIDATE_RECORD_TYPE: Final = "NAV_CORRECTION_EVIDENCE_CANDIDATE"
RECEIPT_SCHEMA_VERSION: Final = 1
RECEIPT_RECORD_TYPE: Final = "NAV_CORRECTION_ACQUISITION_RECEIPT"
DISTRIBUTION_EVIDENCE_RECEIPT_RECORD_TYPE: Final = (
    "DISTRIBUTION_EVIDENCE_ACQUISITION_RECEIPT"
)
BINDING_SCHEMA_VERSION: Final = 1
BINDING_RECORD_TYPE: Final = "NAV_OBSERVATION_REFERENCE_BINDING"
MAX_ARTIFACT_BYTES: Final = 128 * 1024 * 1024
ERSTE_MARKET_PROVIDER: Final = "ERSTE_MARKET_APPROVED_NAV"
ERSTE_MARKET_HOST: Final = "www.erstemarket.hu"
ERSTE_MARKET_SOURCE_GOVERNANCE: Final = "APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE"
ERSTE_MARKET_TRANSPORT_FIELDS: Final = frozenset(
    {
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
)
ERSTE_MARKET_IDENTITY_RECEIPT_FIELDS: Final = frozenset(
    {
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
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class CorrectionEvidenceError(ValueError):
    """A candidate or one of its local references is structurally invalid."""


class ObservationReferenceType(StrEnum):
    PHASE_E_SQLITE_V1 = "PHASE_E_SQLITE_V1"
    RETAINED_RAW_JSON_V1 = "RETAINED_RAW_JSON_V1"
    LEGACY_SQLITE_V1 = "LEGACY_SQLITE_V1"


class ClaimLocatorType(StrEnum):
    JSON_POINTER = "JSON_POINTER"
    DOCUMENT_LOCATOR = "DOCUMENT_LOCATOR"


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    relative_path: str
    raw_sha256: str
    byte_count: int

    def __post_init__(self) -> None:
        _relative_reference(self.relative_path)
        _sha256(self.raw_sha256, "raw_sha256")
        if type(self.byte_count) is not int or self.byte_count <= 0:
            raise CorrectionEvidenceError("byte_count must be a positive integer")


@dataclass(frozen=True, slots=True)
class NavObservationKey:
    isin: str
    share_class: str
    valuation_date: date
    value_type: str
    currency: str
    unit: str
    precision_basis: str

    def __post_init__(self) -> None:
        if not isinstance(self.isin, str) or _ISIN.fullmatch(self.isin) is None:
            raise CorrectionEvidenceError("ISIN must be canonical 12-character text")
        _required_text(self.share_class, "share_class")
        if type(self.valuation_date) is not date:
            raise CorrectionEvidenceError("valuation_date must be a date")
        if self.value_type != "NAV":
            raise CorrectionEvidenceError("value_type must be NAV")
        if (
            not isinstance(self.currency, str)
            or _CURRENCY.fullmatch(self.currency) is None
        ):
            raise CorrectionEvidenceError("currency must be uppercase ISO-style text")
        if self.unit != "PER_UNIT":
            raise CorrectionEvidenceError("unit must be PER_UNIT")
        if self.precision_basis != "PUBLISHED_DECIMAL_TEXT":
            raise CorrectionEvidenceError(
                "precision_basis must be PUBLISHED_DECIMAL_TEXT"
            )


@dataclass(frozen=True, slots=True)
class StructuredBindingReference:
    artifact: ArtifactReference
    locator: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactReference):
            raise CorrectionEvidenceError("binding artifact has the wrong type")
        _json_pointer(self.locator, "binding locator")


@dataclass(frozen=True, slots=True)
class PhaseEObservationReference:
    reference_type: ObservationReferenceType
    observation_id: str
    database_snapshot: ArtifactReference
    row_id: int
    manifest_fingerprint: str
    observation_fingerprint: str
    raw_artifact_sha256: str
    source_fingerprint: str
    provider_observation_identity: str
    raw_value: str
    decimal_value: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if self.reference_type is not ObservationReferenceType.PHASE_E_SQLITE_V1:
            raise CorrectionEvidenceError("Phase E reference discriminator differs")
        _identifier(self.observation_id, "observation_id")
        if type(self.row_id) is not int or self.row_id <= 0:
            raise CorrectionEvidenceError("row_id must be a positive integer")
        for value, label in (
            (self.manifest_fingerprint, "manifest_fingerprint"),
            (self.observation_fingerprint, "observation_fingerprint"),
            (self.raw_artifact_sha256, "raw_artifact_sha256"),
            (self.source_fingerprint, "source_fingerprint"),
        ):
            _sha256(value, label)
        _required_text(
            self.provider_observation_identity, "provider_observation_identity"
        )
        object.__setattr__(
            self, "decimal_value", Decimal(_decimal_text(self.raw_value, "raw_value"))
        )


@dataclass(frozen=True, slots=True)
class RawObservationReference:
    reference_type: ObservationReferenceType
    observation_id: str
    artifact: ArtifactReference
    value_locator: str
    binding: StructuredBindingReference
    raw_value: str
    decimal_value: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if self.reference_type is not ObservationReferenceType.RETAINED_RAW_JSON_V1:
            raise CorrectionEvidenceError("raw reference discriminator differs")
        _identifier(self.observation_id, "observation_id")
        _json_pointer(self.value_locator, "value_locator")
        object.__setattr__(
            self, "decimal_value", Decimal(_decimal_text(self.raw_value, "raw_value"))
        )


@dataclass(frozen=True, slots=True)
class ErsteMarketChartObservationReference:
    """Retained chart occurrence with its closed transport/semantic chain."""

    reference_type: ObservationReferenceType
    observation_id: str
    artifact: ArtifactReference
    series_locator: str
    provider_observation_identity: str
    transport_receipt: ArtifactReference
    semantic_receipt: ArtifactReference
    identity_artifact: ArtifactReference
    identity_receipt: ArtifactReference
    binding: StructuredBindingReference
    raw_value: str
    decimal_value: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if self.reference_type is not ObservationReferenceType.RETAINED_RAW_JSON_V1:
            raise CorrectionEvidenceError("chart reference discriminator differs")
        _identifier(self.observation_id, "observation_id")
        _json_pointer(self.series_locator, "series_locator")
        if re.fullmatch(r"/series/(0|[1-9][0-9]*)", self.series_locator) is None:
            raise CorrectionEvidenceError(
                "series_locator must identify one retained chart pair"
            )
        if not self.provider_observation_identity.isdigit():
            raise CorrectionEvidenceError(
                "provider_observation_identity must be epoch-millisecond text"
            )
        object.__setattr__(
            self,
            "decimal_value",
            Decimal(_decimal_text(self.raw_value, "raw_value")),
        )


@dataclass(frozen=True, slots=True)
class LegacyObservationReference:
    reference_type: ObservationReferenceType
    observation_id: str
    database_snapshot: ArtifactReference
    row_id: int
    source_provider: str
    source_identifier: str
    source_fingerprint: str
    binding: StructuredBindingReference
    raw_value: str
    decimal_value: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if self.reference_type is not ObservationReferenceType.LEGACY_SQLITE_V1:
            raise CorrectionEvidenceError("legacy reference discriminator differs")
        _identifier(self.observation_id, "observation_id")
        if type(self.row_id) is not int or self.row_id <= 0:
            raise CorrectionEvidenceError("row_id must be a positive integer")
        _required_text(self.source_provider, "source_provider")
        _required_text(self.source_identifier, "source_identifier")
        _sha256(self.source_fingerprint, "source_fingerprint")
        object.__setattr__(
            self, "decimal_value", Decimal(_decimal_text(self.raw_value, "raw_value"))
        )


type ObservationReference = (
    PhaseEObservationReference
    | RawObservationReference
    | ErsteMarketChartObservationReference
    | LegacyObservationReference
)


@dataclass(frozen=True, slots=True)
class CorrectionClaim:
    correction_id: str
    artifact: ArtifactReference
    locator_type: ClaimLocatorType
    locator: str
    raw_value: str
    decimal_value: Decimal = field(init=False)

    def __post_init__(self) -> None:
        _identifier(self.correction_id, "correction_id")
        _required_text(self.locator, "correction locator")
        if self.locator_type is ClaimLocatorType.JSON_POINTER:
            _json_pointer(self.locator, "correction locator")
        object.__setattr__(
            self,
            "decimal_value",
            Decimal(_decimal_text(self.raw_value, "correction raw_value")),
        )


@dataclass(frozen=True, slots=True)
class AcquisitionEvent:
    event_id: str
    artifact_role: str
    artifact: ArtifactReference
    receipt: ArtifactReference
    timestamp_locator: str
    retrieved_at_utc: datetime

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        if self.artifact_role not in {
            "ISSUER_CORRECTION",
            "RETAINED_RAW_OBSERVATION",
            "RETAINED_IDENTITY",
            "PUBLICATION_RECORD",
        }:
            raise CorrectionEvidenceError("unsupported acquisition artifact_role")
        _json_pointer(self.timestamp_locator, "timestamp_locator")
        if self.retrieved_at_utc.tzinfo != UTC:
            raise CorrectionEvidenceError("retrieved_at_utc must be UTC")


@dataclass(frozen=True, slots=True)
class PublicationReference:
    publication_id: str
    artifact: ArtifactReference
    locator_type: ClaimLocatorType
    locator: str
    recorded_availability_bound_utc: datetime | None

    def __post_init__(self) -> None:
        _identifier(self.publication_id, "publication_id")
        _required_text(self.locator, "publication locator")
        if self.locator_type is ClaimLocatorType.JSON_POINTER:
            _json_pointer(self.locator, "publication locator")
        if (
            self.recorded_availability_bound_utc is not None
            and self.recorded_availability_bound_utc.tzinfo != UTC
        ):
            raise CorrectionEvidenceError("recorded_availability_bound_utc must be UTC")


@dataclass(frozen=True, slots=True)
class CorrectionRelationship:
    relationship_type: str
    correction_id: str
    target_observation_id: str

    def __post_init__(self) -> None:
        if self.relationship_type != "CORRECTION_APPLIES_TO_KEY":
            raise CorrectionEvidenceError("unsupported correction relationship_type")
        _identifier(self.correction_id, "correction_id")
        _identifier(self.target_observation_id, "target_observation_id")


@dataclass(frozen=True, slots=True)
class CorrectionEvidenceCandidate:
    candidate_id: str
    key: NavObservationKey
    retained_observation: ObservationReference
    issuer_correction: CorrectionClaim
    acquisition_events: tuple[AcquisitionEvent, ...]
    publication_references: tuple[PublicationReference, ...]
    relationship: CorrectionRelationship
    provenance_limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.candidate_id, "candidate_id")
        if not isinstance(self.key, NavObservationKey):
            raise CorrectionEvidenceError("candidate key has the wrong type")
        if not isinstance(
            self.retained_observation,
            (
                PhaseEObservationReference,
                RawObservationReference,
                ErsteMarketChartObservationReference,
                LegacyObservationReference,
            ),
        ):
            raise CorrectionEvidenceError("retained observation has the wrong type")
        if not isinstance(self.issuer_correction, CorrectionClaim):
            raise CorrectionEvidenceError("issuer correction has the wrong type")
        events = tuple(self.acquisition_events)
        publications = tuple(self.publication_references)
        limitations = tuple(self.provenance_limitations)
        if not events or any(not isinstance(item, AcquisitionEvent) for item in events):
            raise CorrectionEvidenceError("candidate requires typed acquisition events")
        if any(not isinstance(item, PublicationReference) for item in publications):
            raise CorrectionEvidenceError("publication reference has the wrong type")
        if any(not isinstance(item, str) or not item.strip() for item in limitations):
            raise CorrectionEvidenceError(
                "provenance limitations must be non-empty text"
            )
        object.__setattr__(
            self,
            "acquisition_events",
            tuple(sorted(events, key=lambda item: item.event_id)),
        )
        object.__setattr__(
            self,
            "publication_references",
            tuple(
                sorted(
                    publications,
                    key=lambda item: item.publication_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "provenance_limitations",
            tuple(sorted(limitations)),
        )
        if not isinstance(self.relationship, CorrectionRelationship):
            raise CorrectionEvidenceError("correction relationship has the wrong type")
        if len(set(self.provenance_limitations)) != len(self.provenance_limitations):
            raise CorrectionEvidenceError("provenance limitations are duplicated")
        if len({item.event_id for item in self.acquisition_events}) != len(
            self.acquisition_events
        ):
            raise CorrectionEvidenceError("acquisition event IDs are ambiguous")
        if len({item.publication_id for item in self.publication_references}) != len(
            self.publication_references
        ):
            raise CorrectionEvidenceError("publication reference IDs are ambiguous")


@dataclass(frozen=True, slots=True)
class CandidateInspectionResult:
    candidate_reference: ArtifactReference
    candidate_fingerprint: str | None
    candidate: CorrectionEvidenceCandidate | None
    structurally_valid: bool
    validation_errors: tuple[str, ...]
    verified_bindings: tuple[str, ...]
    unsupported_reference_types: tuple[str, ...]
    unverified_semantic_claims: tuple[str, ...]
    provenance_limitations: tuple[str, ...]
    source_approved: bool = field(default=False, init=False)
    documentary_review_performed: bool = field(default=False, init=False)
    issuer_authenticated: bool = field(default=False, init=False)
    historical_availability_established: bool = field(default=False, init=False)
    correction_precedence_established: bool = field(default=False, init=False)
    admitted: bool = field(default=False, init=False)
    operational_eligibility_granted: bool = field(default=False, init=False)
    persistent_or_operational_state_changed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "validation_errors",
            "verified_bindings",
            "unsupported_reference_types",
            "unverified_semantic_claims",
            "provenance_limitations",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))


def inspect_nav_correction_candidate(
    *, repository_root: Path, candidate_reference: ArtifactReference
) -> CandidateInspectionResult:
    """Inspect one explicitly selected candidate without discovery or writes."""

    verified: list[str] = []
    semantic_gaps: list[str] = []
    limitations: list[str] = []
    try:
        candidate_bytes = _verify_artifact(
            repository_root, candidate_reference, "candidate"
        )
        candidate_raw = _load_json_bytes(candidate_bytes, "candidate")
        candidate = _parse_candidate(candidate_raw)
        fingerprint = canonical_fingerprint(candidate_raw)
        verified.append("candidate bytes, hash, byte count, and strict JSON profile")

        observation = candidate.retained_observation
        if isinstance(observation, PhaseEObservationReference):
            verified.extend(
                _validate_phase_e_reference(repository_root, candidate.key, observation)
            )
        elif isinstance(observation, ErsteMarketChartObservationReference):
            verified.extend(
                _validate_erste_market_chart_reference(
                    repository_root, candidate.key, observation, semantic_gaps
                )
            )
            limitations.extend(
                (
                    "chart semantic receipt is in-memory-only and does not admit the retained prefix occurrence",
                    "temporary/full-key binding remains caller-supplied and does not authenticate issuer authority or historical availability",
                )
            )
        elif isinstance(observation, RawObservationReference):
            verified.extend(
                _validate_raw_reference(repository_root, candidate.key, observation)
            )
            limitations.append(
                "raw binding record is a structured caller-supplied relationship, not issuer authentication or historical availability"
            )
        else:
            verified.extend(
                _validate_legacy_reference(repository_root, candidate.key, observation)
            )
            limitations.extend(
                (
                    "legacy row ID is snapshot-local; logical identity is bound to the declared snapshot hash",
                    "legacy reference and binding do not establish historical availability",
                    "legacy REAL storage cannot recover publication precision beyond recorded display text",
                )
            )

        verified.extend(
            _validate_correction_claim(
                repository_root,
                candidate.key,
                candidate.issuer_correction,
                semantic_gaps,
            )
        )
        verified.extend(
            _validate_acquisition_events(repository_root, candidate.acquisition_events)
        )
        verified.extend(
            _validate_publication_references(
                repository_root, candidate.publication_references, semantic_gaps
            )
        )
        _validate_relationship(candidate)
        verified.append("explicit correction-to-observation candidate relationship")
        limitations.extend(candidate.provenance_limitations)
        semantic_gaps.extend(
            (
                "matching bytes and hashes do not authenticate the issuer or its authority",
                "recorded documentary locators and conclusions are not substantive PDF review",
                "recorded availability assertions are not historical-availability admission",
                "matching or differing values do not independently prove correction lineage",
            )
        )
        return CandidateInspectionResult(
            candidate_reference=candidate_reference,
            candidate_fingerprint=fingerprint,
            candidate=candidate,
            structurally_valid=True,
            validation_errors=(),
            verified_bindings=tuple(sorted(set(verified))),
            unsupported_reference_types=(),
            unverified_semantic_claims=tuple(sorted(set(semantic_gaps))),
            provenance_limitations=tuple(sorted(set(limitations))),
        )
    except CorrectionEvidenceError as error:
        unsupported: tuple[str, ...] = ()
        if str(error).startswith("unsupported observation reference_type: "):
            unsupported = (str(error).split(": ", 1)[1],)
        return CandidateInspectionResult(
            candidate_reference=candidate_reference,
            candidate_fingerprint=None,
            candidate=None,
            structurally_valid=False,
            validation_errors=(str(error),),
            verified_bindings=tuple(sorted(set(verified))),
            unsupported_reference_types=unsupported,
            unverified_semantic_claims=(),
            provenance_limitations=(),
        )


def _parse_candidate(raw: Mapping[str, object]) -> CorrectionEvidenceCandidate:
    _keys(
        raw,
        {
            "schema_version",
            "record_type",
            "candidate_id",
            "key",
            "retained_observation",
            "issuer_correction",
            "acquisition_events",
            "publication_references",
            "correction_relationship",
            "provenance_limitations",
        },
        "candidate",
    )
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise CorrectionEvidenceError("unsupported candidate schema_version")
    if raw["record_type"] != CANDIDATE_RECORD_TYPE:
        raise CorrectionEvidenceError("unsupported candidate record_type")
    candidate_id = _identifier(raw["candidate_id"], "candidate_id")
    key = _parse_key(_mapping(raw["key"], "key"))
    observation = _parse_observation_reference(
        _mapping(raw["retained_observation"], "retained_observation")
    )
    correction = _parse_correction(
        _mapping(raw["issuer_correction"], "issuer_correction")
    )
    events = tuple(
        _parse_acquisition_event(_mapping(item, "acquisition event"))
        for item in _sequence(raw["acquisition_events"], "acquisition_events")
    )
    publications = tuple(
        _parse_publication_reference(_mapping(item, "publication reference"))
        for item in _sequence(raw["publication_references"], "publication_references")
    )
    relationship = _parse_relationship(
        _mapping(raw["correction_relationship"], "correction_relationship")
    )
    limitations = tuple(
        _required_text(item, "provenance limitation")
        for item in _sequence(raw["provenance_limitations"], "provenance_limitations")
    )
    if not events:
        raise CorrectionEvidenceError("candidate requires acquisition_events")
    if len({item.event_id for item in events}) != len(events):
        raise CorrectionEvidenceError("acquisition event IDs are ambiguous")
    if len({item.publication_id for item in publications}) != len(publications):
        raise CorrectionEvidenceError("publication reference IDs are ambiguous")
    if observation.observation_id == correction.correction_id:
        raise CorrectionEvidenceError("observation and correction IDs must differ")
    return CorrectionEvidenceCandidate(
        candidate_id=candidate_id,
        key=key,
        retained_observation=observation,
        issuer_correction=correction,
        acquisition_events=events,
        publication_references=publications,
        relationship=relationship,
        provenance_limitations=limitations,
    )


def _parse_key(raw: Mapping[str, object]) -> NavObservationKey:
    _keys(
        raw,
        {
            "isin",
            "share_class",
            "valuation_date",
            "value_type",
            "currency",
            "unit",
            "precision_basis",
        },
        "key",
    )
    return NavObservationKey(
        isin=_required_text(raw["isin"], "ISIN"),
        share_class=_required_text(raw["share_class"], "share_class"),
        valuation_date=_date(raw["valuation_date"], "valuation_date"),
        value_type=_required_text(raw["value_type"], "value_type"),
        currency=_required_text(raw["currency"], "currency"),
        unit=_required_text(raw["unit"], "unit"),
        precision_basis=_required_text(raw["precision_basis"], "precision_basis"),
    )


def _parse_observation_reference(raw: Mapping[str, object]) -> ObservationReference:
    reference_text = _required_text(raw.get("reference_type"), "reference_type")
    try:
        reference_type = ObservationReferenceType(reference_text)
    except ValueError as error:
        raise CorrectionEvidenceError(
            f"unsupported observation reference_type: {reference_text}"
        ) from error
    if reference_type is ObservationReferenceType.PHASE_E_SQLITE_V1:
        _keys(
            raw,
            {
                "reference_type",
                "observation_id",
                "database_snapshot",
                "row_id",
                "manifest_fingerprint",
                "observation_fingerprint",
                "raw_artifact_sha256",
                "source_fingerprint",
                "provider_observation_identity",
                "raw_value",
            },
            "Phase E observation reference",
        )
        row_id = _positive_integer(raw["row_id"], "row_id")
        return PhaseEObservationReference(
            reference_type=reference_type,
            observation_id=_identifier(raw["observation_id"], "observation_id"),
            database_snapshot=_parse_artifact(raw["database_snapshot"]),
            row_id=row_id,
            manifest_fingerprint=_hash_text(
                raw["manifest_fingerprint"], "manifest_fingerprint"
            ),
            observation_fingerprint=_hash_text(
                raw["observation_fingerprint"], "observation_fingerprint"
            ),
            raw_artifact_sha256=_hash_text(
                raw["raw_artifact_sha256"], "raw_artifact_sha256"
            ),
            source_fingerprint=_hash_text(
                raw["source_fingerprint"], "source_fingerprint"
            ),
            provider_observation_identity=_required_text(
                raw["provider_observation_identity"], "provider_observation_identity"
            ),
            raw_value=_decimal_text(raw["raw_value"], "raw_value"),
        )
    if reference_type is ObservationReferenceType.RETAINED_RAW_JSON_V1:
        generic_fields = {
            "reference_type",
            "observation_id",
            "artifact",
            "value_locator",
            "binding",
            "raw_value",
        }
        chart_fields = {
            "reference_type",
            "observation_id",
            "artifact",
            "series_locator",
            "provider_observation_identity",
            "transport_receipt",
            "semantic_receipt",
            "identity_artifact",
            "identity_receipt",
            "binding",
            "raw_value",
        }
        if set(raw) == generic_fields:
            return RawObservationReference(
                reference_type=reference_type,
                observation_id=_identifier(raw["observation_id"], "observation_id"),
                artifact=_parse_artifact(raw["artifact"]),
                value_locator=_json_pointer(raw["value_locator"], "value_locator"),
                binding=_parse_binding_reference(raw["binding"]),
                raw_value=_decimal_text(raw["raw_value"], "raw_value"),
            )
        if set(raw) == chart_fields:
            return ErsteMarketChartObservationReference(
                reference_type=reference_type,
                observation_id=_identifier(raw["observation_id"], "observation_id"),
                artifact=_parse_artifact(raw["artifact"]),
                series_locator=_json_pointer(raw["series_locator"], "series_locator"),
                provider_observation_identity=_required_text(
                    raw["provider_observation_identity"],
                    "provider_observation_identity",
                ),
                transport_receipt=_parse_artifact(raw["transport_receipt"]),
                semantic_receipt=_parse_artifact(raw["semantic_receipt"]),
                identity_artifact=_parse_artifact(raw["identity_artifact"]),
                identity_receipt=_parse_artifact(raw["identity_receipt"]),
                binding=_parse_binding_reference(raw["binding"]),
                raw_value=_decimal_text(raw["raw_value"], "raw_value"),
            )
        _keys(raw, generic_fields, "raw observation reference")
        raise AssertionError("unreachable")
    _keys(
        raw,
        {
            "reference_type",
            "observation_id",
            "database_snapshot",
            "row_id",
            "source_provider",
            "source_identifier",
            "source_fingerprint",
            "binding",
            "raw_value",
        },
        "legacy observation reference",
    )
    return LegacyObservationReference(
        reference_type=reference_type,
        observation_id=_identifier(raw["observation_id"], "observation_id"),
        database_snapshot=_parse_artifact(raw["database_snapshot"]),
        row_id=_positive_integer(raw["row_id"], "row_id"),
        source_provider=_required_text(raw["source_provider"], "source_provider"),
        source_identifier=_required_text(raw["source_identifier"], "source_identifier"),
        source_fingerprint=_hash_text(raw["source_fingerprint"], "source_fingerprint"),
        binding=_parse_binding_reference(raw["binding"]),
        raw_value=_decimal_text(raw["raw_value"], "raw_value"),
    )


def _parse_correction(raw: Mapping[str, object]) -> CorrectionClaim:
    _keys(
        raw,
        {"correction_id", "artifact", "locator_type", "locator", "raw_value"},
        "issuer_correction",
    )
    locator_text = _required_text(raw["locator_type"], "locator_type")
    try:
        locator_type = ClaimLocatorType(locator_text)
    except ValueError as error:
        raise CorrectionEvidenceError("unsupported correction locator_type") from error
    locator = _required_text(raw["locator"], "correction locator")
    if locator_type is ClaimLocatorType.JSON_POINTER:
        locator = _json_pointer(locator, "correction locator")
    return CorrectionClaim(
        correction_id=_identifier(raw["correction_id"], "correction_id"),
        artifact=_parse_artifact(raw["artifact"]),
        locator_type=locator_type,
        locator=locator,
        raw_value=_decimal_text(raw["raw_value"], "correction raw_value"),
    )


def _parse_acquisition_event(raw: Mapping[str, object]) -> AcquisitionEvent:
    _keys(
        raw,
        {
            "event_id",
            "artifact_role",
            "artifact",
            "receipt",
            "timestamp_locator",
            "retrieved_at_utc",
        },
        "acquisition event",
    )
    return AcquisitionEvent(
        event_id=_identifier(raw["event_id"], "event_id"),
        artifact_role=_required_text(raw["artifact_role"], "artifact_role"),
        artifact=_parse_artifact(raw["artifact"]),
        receipt=_parse_artifact(raw["receipt"]),
        timestamp_locator=_json_pointer(raw["timestamp_locator"], "timestamp_locator"),
        retrieved_at_utc=_utc(raw["retrieved_at_utc"], "retrieved_at_utc"),
    )


def _parse_publication_reference(raw: Mapping[str, object]) -> PublicationReference:
    _keys(
        raw,
        {
            "publication_id",
            "artifact",
            "locator_type",
            "locator",
            "recorded_availability_bound_utc",
        },
        "publication reference",
    )
    bound_value = raw["recorded_availability_bound_utc"]
    bound = (
        None
        if bound_value is None
        else _utc(bound_value, "recorded_availability_bound_utc")
    )
    locator_text = _required_text(raw["locator_type"], "publication locator_type")
    try:
        locator_type = ClaimLocatorType(locator_text)
    except ValueError as error:
        raise CorrectionEvidenceError("unsupported publication locator_type") from error
    return PublicationReference(
        publication_id=_identifier(raw["publication_id"], "publication_id"),
        artifact=_parse_artifact(raw["artifact"]),
        locator_type=locator_type,
        locator=_required_text(raw["locator"], "publication locator"),
        recorded_availability_bound_utc=bound,
    )


def _parse_relationship(raw: Mapping[str, object]) -> CorrectionRelationship:
    _keys(
        raw,
        {"relationship_type", "correction_id", "target_observation_id"},
        "correction_relationship",
    )
    relation = _required_text(raw["relationship_type"], "relationship_type")
    if relation != "CORRECTION_APPLIES_TO_KEY":
        raise CorrectionEvidenceError("unsupported correction relationship_type")
    return CorrectionRelationship(
        relationship_type=relation,
        correction_id=_identifier(raw["correction_id"], "correction_id"),
        target_observation_id=_identifier(
            raw["target_observation_id"], "target_observation_id"
        ),
    )


def _parse_artifact(value: object) -> ArtifactReference:
    raw = _mapping(value, "artifact reference")
    _keys(raw, {"relative_path", "raw_sha256", "byte_count"}, "artifact reference")
    return ArtifactReference(
        relative_path=_required_text(raw["relative_path"], "relative_path"),
        raw_sha256=_hash_text(raw["raw_sha256"], "raw_sha256"),
        byte_count=_positive_integer(raw["byte_count"], "byte_count"),
    )


def _parse_binding_reference(value: object) -> StructuredBindingReference:
    raw = _mapping(value, "binding reference")
    _keys(raw, {"artifact", "locator"}, "binding reference")
    return StructuredBindingReference(
        artifact=_parse_artifact(raw["artifact"]),
        locator=_json_pointer(raw["locator"], "binding locator"),
    )


def _validate_phase_e_reference(
    root: Path, key: NavObservationKey, reference: PhaseEObservationReference
) -> tuple[str, ...]:
    raw = _verify_artifact(root, reference.database_snapshot, "Phase E database")
    connection = _sqlite_from_bytes(raw, "Phase E database")
    try:
        row = connection.execute(
            """SELECT n.nav_observation_version_id,n.exact_isin,n.observation_date,
                      n.nav_decimal,n.currency_code,n.provider_observation_identity,
                      n.raw_artifact_sha256,n.observation_fingerprint,
                      m.share_class_name,m.manifest_fingerprint,m.provider_instrument_id,
                      n.quality_status,m.contract_version,m.import_status,
                      s.source_fingerprint,s.source_governance
               FROM nav_observation_version n
               JOIN nav_import_manifest m
                 ON m.nav_import_manifest_id=n.nav_import_manifest_id
                AND m.instrument_id=n.instrument_id AND m.exact_isin=n.exact_isin
               JOIN nav_evidence_source s
                 ON s.nav_evidence_source_id=m.nav_evidence_source_id
               WHERE n.nav_observation_version_id=?""",
            (reference.row_id,),
        ).fetchone()
    except sqlite3.Error as error:
        raise CorrectionEvidenceError(
            "Phase E database profile is unsupported"
        ) from error
    finally:
        connection.close()
    if row is None:
        raise CorrectionEvidenceError("Phase E observation row is missing")
    actual = {
        "isin": str(row[1]),
        "valuation_date": str(row[2]),
        "raw_value": str(row[3]),
        "currency": str(row[4]),
        "provider_identity": str(row[5]),
        "raw_sha": str(row[6]),
        "observation_fingerprint": str(row[7]),
        "share_class": str(row[8]),
        "manifest_fingerprint": str(row[9]),
        "quality_status": str(row[11]),
        "contract_version": int(row[12]),
        "import_status": str(row[13]),
        "source_fingerprint": str(row[14]),
        "source_governance": str(row[15]),
    }
    expected = {
        "isin": key.isin,
        "valuation_date": key.valuation_date.isoformat(),
        "raw_value": reference.raw_value,
        "currency": key.currency,
        "provider_identity": reference.provider_observation_identity,
        "raw_sha": reference.raw_artifact_sha256,
        "observation_fingerprint": reference.observation_fingerprint,
        "share_class": key.share_class,
        "manifest_fingerprint": reference.manifest_fingerprint,
        "quality_status": "ADMITTED_VALIDATED",
        "contract_version": 1,
        "import_status": "VALIDATED_ADMITTED",
        "source_fingerprint": reference.source_fingerprint,
        "source_governance": "APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE",
    }
    if actual != expected:
        raise CorrectionEvidenceError(
            "Phase E observation binding differs from candidate"
        )
    return (
        "Phase E snapshot raw hash and byte count",
        "Phase E exact row, manifest, identity, date, currency, value, and fingerprints",
    )


def _validate_raw_reference(
    root: Path, key: NavObservationKey, reference: RawObservationReference
) -> tuple[str, ...]:
    artifact_raw = _verify_artifact(
        root, reference.artifact, "raw observation artifact"
    )
    document = _load_json_bytes(artifact_raw, "raw observation artifact")
    value = _pointer(document, reference.value_locator, "raw observation value")
    if _decimal_from_json_value(value, "raw observation value") != Decimal(
        reference.raw_value
    ):
        raise CorrectionEvidenceError("raw observation value differs from candidate")
    _validate_binding(root, key, reference, reference.binding)
    return (
        "raw observation artifact bytes and JSON value locator",
        "raw observation structured binding to full candidate key and value",
    )


def _validate_erste_market_chart_reference(
    root: Path,
    key: NavObservationKey,
    reference: ErsteMarketChartObservationReference,
    semantic_gaps: list[str],
) -> tuple[str, ...]:
    raw = _verify_artifact(root, reference.artifact, "Erste Market chart artifact")
    document = _load_chart_json_bytes(raw, "Erste Market chart artifact")
    _keys(
        document,
        {
            "decimals",
            "id",
            "instrument_id",
            "isin",
            "last_close",
            "series",
            "ticker",
            "title",
        },
        "Erste Market chart",
    )
    instrument_id = _required_text(document["instrument_id"], "chart instrument_id")
    if (
        not instrument_id.isdigit()
        or document["id"] != instrument_id
        or document["isin"] != key.isin
        or document["title"] != key.share_class
        or document["ticker"] != key.share_class
    ):
        raise CorrectionEvidenceError(
            "Erste Market chart identity differs from candidate"
        )
    decimals = document["decimals"]
    if type(decimals) is not int or not 0 <= decimals <= 12:
        raise CorrectionEvidenceError("Erste Market chart decimals field is invalid")
    _decimal_from_json_value(document["last_close"], "chart last_close")

    series = _sequence(document["series"], "chart series")
    if not series:
        raise CorrectionEvidenceError("chart series is empty")
    timestamps: set[int] = set()
    observation_dates: set[date] = set()
    for item in series:
        row = _sequence(item, "chart series occurrence")
        if len(row) != 2:
            raise CorrectionEvidenceError(
                "chart series occurrence must be a timestamp/value pair"
            )
        row_timestamp = row[0]
        if type(row_timestamp) is not int or row_timestamp < 0:
            raise CorrectionEvidenceError(
                "chart series timestamp must be a non-negative integer"
            )
        try:
            row_date = (
                datetime.fromtimestamp(row_timestamp // 1000, tz=UTC)
                + timedelta(milliseconds=row_timestamp % 1000)
            ).date()
        except (OSError, OverflowError, ValueError) as error:
            raise CorrectionEvidenceError(
                "chart series timestamp is outside the supported range"
            ) from error
        _decimal_from_json_value(row[1], "chart series value")
        if row_timestamp in timestamps or row_date in observation_dates:
            raise CorrectionEvidenceError(
                "chart series contains duplicate observations"
            )
        timestamps.add(row_timestamp)
        observation_dates.add(row_date)

    pair = _sequence(
        _pointer(document, reference.series_locator, "chart series occurrence"),
        "chart series occurrence",
    )
    if len(pair) != 2:
        raise CorrectionEvidenceError(
            "chart series occurrence must be a timestamp/value pair"
        )
    timestamp = pair[0]
    if type(timestamp) is not int or timestamp < 0:
        raise CorrectionEvidenceError(
            "chart series timestamp must be a non-negative integer"
        )
    if str(timestamp) != reference.provider_observation_identity:
        raise CorrectionEvidenceError(
            "chart provider observation identity differs from occurrence"
        )
    try:
        observed = (
            datetime.fromtimestamp(timestamp // 1000, tz=UTC)
            + timedelta(milliseconds=timestamp % 1000)
        ).date()
    except (OSError, OverflowError, ValueError) as error:
        raise CorrectionEvidenceError(
            "chart series timestamp is outside the supported range"
        ) from error
    if observed != key.valuation_date:
        raise CorrectionEvidenceError("chart valuation date differs from candidate")
    if (
        _decimal_from_json_value(pair[1], "chart series value")
        != reference.decimal_value
    ):
        raise CorrectionEvidenceError("raw observation value differs from candidate")

    transport_raw = _verify_artifact(
        root, reference.transport_receipt, "Erste Market transport receipt"
    )
    transport = _load_json_bytes(transport_raw, "Erste Market transport receipt")
    _validate_erste_transport_receipt(
        transport, reference.artifact, expected_timestamp=None
    )
    if (
        transport["requested_isin"] != key.isin
        or _chart_url_instrument(transport["requested_url"]) != instrument_id
    ):
        raise CorrectionEvidenceError(
            "Erste Market transport receipt identity differs from candidate"
        )

    identity_raw = _verify_artifact(
        root, reference.identity_artifact, "Erste Market identity artifact"
    )
    if not identity_raw:
        raise CorrectionEvidenceError("Erste Market identity artifact is empty")
    identity_receipt_raw = _verify_artifact(
        root, reference.identity_receipt, "Erste Market identity receipt"
    )
    identity_receipt = _load_json_bytes(
        identity_receipt_raw, "Erste Market identity receipt"
    )
    _validate_erste_identity_receipt(
        identity_receipt, reference.identity_artifact, expected_timestamp=None
    )
    if identity_receipt["requested_isin"] != key.isin:
        raise CorrectionEvidenceError(
            "Erste Market identity receipt ISIN differs from candidate"
        )

    semantic_raw = _verify_artifact(
        root, reference.semantic_receipt, "Erste Market semantic receipt"
    )
    semantic = _load_json_bytes(semantic_raw, "Erste Market semantic receipt")
    _keys(
        semantic,
        {
            "assessment",
            "raw_artifact_reference",
            "raw_artifact_sha256",
            "receipt_type",
            "schema_version",
            "transport_receipt_reference",
            "transport_receipt_sha256",
        },
        "Erste Market semantic receipt",
    )
    if type(semantic["schema_version"]) is not int or semantic["schema_version"] != 1:
        raise CorrectionEvidenceError(
            "unsupported Erste Market semantic receipt schema_version"
        )
    if semantic["receipt_type"] != "ERSTE_MARKET_CHART_SEMANTIC_ADMISSION":
        raise CorrectionEvidenceError("unsupported Erste Market semantic receipt type")
    if (
        semantic["raw_artifact_reference"] != reference.artifact.relative_path
        or semantic["raw_artifact_sha256"] != reference.artifact.raw_sha256
        or semantic["transport_receipt_reference"]
        != reference.transport_receipt.relative_path
        or semantic["transport_receipt_sha256"]
        != reference.transport_receipt.raw_sha256
    ):
        raise CorrectionEvidenceError(
            "Erste Market semantic receipt chain differs from candidate"
        )
    assessment = _mapping(semantic["assessment"], "semantic assessment")
    _keys(
        assessment,
        {
            "assessment_fingerprint",
            "assessment_scope",
            "currency",
            "dataset_fingerprint",
            "first_observation_date",
            "instrument_id",
            "isin",
            "last_observation_date",
            "media_contract_version",
            "normalized_media_type",
            "observation_count",
            "provider",
            "raw_artifact_sha256",
            "receipt_sha256",
            "semantic_status",
            "source_governance",
            "transport_classification",
        },
        "semantic assessment",
    )
    core = {
        item_key: item_value
        for item_key, item_value in assessment.items()
        if item_key not in {"assessment_fingerprint", "semantic_status"}
    }
    first = _date(assessment["first_observation_date"], "semantic first date")
    last = _date(assessment["last_observation_date"], "semantic last date")
    if first > last:
        raise CorrectionEvidenceError("semantic assessment date range is reversed")
    if (
        assessment["assessment_fingerprint"] != canonical_fingerprint(core)
        or assessment["assessment_scope"]
        != "IN_MEMORY_ONLY_NO_ARTIFACT_OR_DATABASE_ADMISSION"
        or assessment["currency"] != key.currency
        or _hash_text(assessment["dataset_fingerprint"], "dataset_fingerprint")
        != assessment["dataset_fingerprint"]
        or assessment["instrument_id"] != instrument_id
        or assessment["isin"] != key.isin
        or type(assessment["media_contract_version"]) is not int
        or assessment["media_contract_version"] != 1
        or assessment["normalized_media_type"] != "text/html; charset=utf-8"
        or _positive_integer(assessment["observation_count"], "observation_count")
        != assessment["observation_count"]
        or assessment["provider"] != ERSTE_MARKET_PROVIDER
        or assessment["raw_artifact_sha256"] != reference.artifact.raw_sha256
        or assessment["receipt_sha256"] != reference.transport_receipt.raw_sha256
        or assessment["semantic_status"] != "SEMANTIC_ADMISSIBLE_IN_MEMORY_ONLY"
        or assessment["source_governance"] != ERSTE_MARKET_SOURCE_GOVERNANCE
        or assessment["transport_classification"] != "QUARANTINED_REJECTED_RESPONSE"
    ):
        raise CorrectionEvidenceError(
            "Erste Market semantic assessment binding differs from candidate"
        )
    if not first <= key.valuation_date <= last:
        semantic_gaps.append(
            "semantic receipt date range excludes the selected prefix occurrence; receipt is used only for retained media/identity lineage"
        )
    semantic_gaps.append(
        "identity HTML bytes are retained and receipt-bound but their visible prose was not interpreted by this validator"
    )
    semantic_gaps.append(
        "semantic assessment fingerprint is verified but its dataset fingerprint and admitted slice are not recomputed by this validator"
    )
    _validate_binding(root, key, reference, reference.binding)
    return (
        "Erste Market chart exact JSON schema, identity fields, UTC epoch-millisecond date, and Decimal value",
        "Erste Market chart transport receipt raw-artifact and retrieval binding",
        "Erste Market semantic receipt raw/transport/media/identity chain",
        "Erste Market identity artifact and receipt byte binding",
        "raw observation structured binding to full candidate key and value",
    )


def _validate_legacy_reference(
    root: Path, key: NavObservationKey, reference: LegacyObservationReference
) -> tuple[str, ...]:
    raw = _verify_artifact(root, reference.database_snapshot, "legacy database")
    connection = _sqlite_from_bytes(raw, "legacy database")
    try:
        row = connection.execute(
            """SELECT o.instrument_nav_observation_id,i.isin,o.observation_date,
                      CAST(o.nav_value AS TEXT),o.currency_code,o.value_type,o.source_provider,
                      o.source_identifier,o.source_fingerprint
               FROM instrument_nav_observation o
               JOIN instrument i ON i.instrument_id=o.instrument_id
               WHERE o.instrument_nav_observation_id=?""",
            (reference.row_id,),
        ).fetchone()
    except sqlite3.Error as error:
        raise CorrectionEvidenceError(
            "legacy database profile is unsupported"
        ) from error
    finally:
        connection.close()
    if row is None:
        raise CorrectionEvidenceError("legacy observation row is missing")
    actual = (
        str(row[1]),
        str(row[2]),
        Decimal(str(row[3])),
        str(row[4]),
        str(row[5]),
        str(row[6]),
        str(row[7]),
        str(row[8]),
    )
    expected = (
        key.isin,
        key.valuation_date.isoformat(),
        Decimal(reference.raw_value),
        key.currency,
        key.value_type,
        reference.source_provider,
        reference.source_identifier,
        reference.source_fingerprint,
    )
    if actual != expected:
        raise CorrectionEvidenceError(
            "legacy observation binding differs from candidate"
        )
    _validate_binding(root, key, reference, reference.binding)
    return (
        "legacy database snapshot hash and snapshot-local row",
        "legacy logical identity, date, currency, value type, source, and value",
        "legacy structured binding to share class and full candidate key",
    )


def _validate_binding(
    root: Path,
    key: NavObservationKey,
    reference: (
        RawObservationReference
        | ErsteMarketChartObservationReference
        | LegacyObservationReference
    ),
    binding_reference: StructuredBindingReference,
) -> None:
    raw = _verify_artifact(root, binding_reference.artifact, "observation binding")
    document = _load_json_bytes(raw, "observation binding")
    binding = _mapping(
        _pointer(document, binding_reference.locator, "observation binding"),
        "observation binding",
    )
    _keys(
        binding,
        {
            "schema_version",
            "record_type",
            "observation_id",
            "reference_type",
            "source_artifact_sha256",
            "source_locator",
            "key",
            "raw_value",
        },
        "observation binding",
    )
    if type(binding["schema_version"]) is not int or binding["schema_version"] != 1:
        raise CorrectionEvidenceError("unsupported observation binding schema_version")
    if binding["record_type"] != BINDING_RECORD_TYPE:
        raise CorrectionEvidenceError("unsupported observation binding record_type")
    if isinstance(reference, RawObservationReference):
        source_artifact = reference.artifact
        source_locator = reference.value_locator
    elif isinstance(reference, ErsteMarketChartObservationReference):
        source_artifact = reference.artifact
        source_locator = reference.series_locator
    else:
        source_artifact = reference.database_snapshot
        source_locator = f"instrument_nav_observation/{reference.row_id}"
    if (
        binding["observation_id"] != reference.observation_id
        or binding["reference_type"] != reference.reference_type.value
        or binding["source_artifact_sha256"] != source_artifact.raw_sha256
        or binding["source_locator"] != source_locator
        or _parse_key(_mapping(binding["key"], "binding key")) != key
        or _decimal_text(binding["raw_value"], "binding raw_value")
        != reference.raw_value
    ):
        raise CorrectionEvidenceError("observation binding differs from candidate")


def _validate_correction_claim(
    root: Path,
    key: NavObservationKey,
    correction: CorrectionClaim,
    semantic_gaps: list[str],
) -> tuple[str, ...]:
    raw = _verify_artifact(root, correction.artifact, "correction artifact")
    if correction.locator_type is ClaimLocatorType.DOCUMENT_LOCATOR:
        semantic_gaps.append(
            "document locator was retained but its prose/value meaning was not interpreted"
        )
        return ("correction artifact bytes, hash, byte count, and locator text",)
    document = _load_json_bytes(raw, "correction artifact")
    claim = _mapping(
        _pointer(document, correction.locator, "correction claim"), "claim"
    )
    _keys(
        claim,
        {
            "isin",
            "share_class",
            "valuation_date",
            "value_type",
            "currency",
            "unit",
            "precision_basis",
            "corrected_value",
        },
        "structured correction claim",
    )
    claim_key = NavObservationKey(
        isin=_required_text(claim["isin"], "claim ISIN"),
        share_class=_required_text(claim["share_class"], "claim share_class"),
        valuation_date=_date(claim["valuation_date"], "claim valuation_date"),
        value_type=_required_text(claim["value_type"], "claim value_type"),
        currency=_required_text(claim["currency"], "claim currency"),
        unit=_required_text(claim["unit"], "claim unit"),
        precision_basis=_required_text(
            claim["precision_basis"], "claim precision_basis"
        ),
    )
    if (
        claim_key != key
        or _decimal_text(claim["corrected_value"], "corrected_value")
        != correction.raw_value
    ):
        raise CorrectionEvidenceError(
            "structured correction claim differs from candidate"
        )
    return ("structured correction claim full key and exact corrected value",)


def _validate_acquisition_events(
    root: Path, events: tuple[AcquisitionEvent, ...]
) -> tuple[str, ...]:
    verified: list[str] = []
    for event in events:
        _verify_artifact(root, event.artifact, f"acquisition artifact {event.event_id}")
        receipt_bytes = _verify_artifact(
            root, event.receipt, f"acquisition receipt {event.event_id}"
        )
        receipt = _load_json_bytes(
            receipt_bytes, f"acquisition receipt {event.event_id}"
        )
        if "record_type" in receipt:
            _keys(
                receipt,
                {"schema_version", "record_type", "artifact", "retrieved_at_utc"},
                "acquisition receipt",
            )
            if (
                type(receipt["schema_version"]) is not int
                or receipt["schema_version"] != 1
            ):
                raise CorrectionEvidenceError(
                    "unsupported acquisition receipt schema_version"
                )
            record_type = _required_text(receipt["record_type"], "receipt record_type")
            if record_type not in {
                RECEIPT_RECORD_TYPE,
                DISTRIBUTION_EVIDENCE_RECEIPT_RECORD_TYPE,
            }:
                raise CorrectionEvidenceError(
                    "unsupported acquisition receipt record_type"
                )
            if event.timestamp_locator != "/retrieved_at_utc":
                raise CorrectionEvidenceError(
                    "timestamp_locator does not match acquisition receipt profile"
                )
            timestamp = _utc(receipt["retrieved_at_utc"], "receipt retrieved_at_utc")
            if timestamp != event.retrieved_at_utc:
                raise CorrectionEvidenceError(
                    "acquisition receipt timestamp differs from event"
                )
            artifact_raw = _mapping(receipt["artifact"], "receipt artifact")
            modern_fields = {"relative_path", "raw_sha256", "byte_count"}
            retained_fields = {"path", "sha256"}
            if (
                set(artifact_raw) & modern_fields
                and set(artifact_raw) & retained_fields
            ):
                raise CorrectionEvidenceError(
                    "mixed acquisition receipt artifact profiles"
                )
            if record_type == RECEIPT_RECORD_TYPE:
                receipt_artifact = _parse_artifact(artifact_raw)
                if receipt_artifact != event.artifact:
                    raise CorrectionEvidenceError(
                        "acquisition receipt artifact binding differs from event"
                    )
                verified.append(f"receipt-bound acquisition event {event.event_id}")
                continue
            _keys(
                artifact_raw,
                {"path", "sha256"},
                "distribution-evidence acquisition receipt artifact",
            )
            receipt_path = _required_text(artifact_raw["path"], "receipt artifact path")
            _relative_reference(receipt_path)
            receipt_sha = _hash_text(artifact_raw["sha256"], "receipt artifact sha256")
            if (
                receipt_path != event.artifact.relative_path
                or receipt_sha != event.artifact.raw_sha256
            ):
                raise CorrectionEvidenceError(
                    "distribution-evidence acquisition receipt artifact binding differs from event"
                )
            verified.append(
                f"retained distribution-evidence receipt-bound acquisition event {event.event_id}"
            )
            continue
        if set(receipt) == ERSTE_MARKET_TRANSPORT_FIELDS:
            if event.timestamp_locator != "/retrieval_timestamp":
                raise CorrectionEvidenceError(
                    "timestamp_locator does not match Erste Market transport profile"
                )
            _validate_erste_transport_receipt(
                receipt, event.artifact, expected_timestamp=event.retrieved_at_utc
            )
            verified.append(
                f"retained Erste Market transport receipt-bound acquisition event {event.event_id}"
            )
            continue
        if set(receipt) == ERSTE_MARKET_IDENTITY_RECEIPT_FIELDS:
            if event.timestamp_locator != "/retrieval_timestamp":
                raise CorrectionEvidenceError(
                    "timestamp_locator does not match Erste Market identity profile"
                )
            _validate_erste_identity_receipt(
                receipt, event.artifact, expected_timestamp=event.retrieved_at_utc
            )
            verified.append(
                f"retained Erste Market identity receipt-bound acquisition event {event.event_id}"
            )
            continue
        raise CorrectionEvidenceError("unsupported acquisition receipt profile")
    return tuple(verified)


def _validate_erste_transport_receipt(
    receipt: Mapping[str, object],
    artifact: ArtifactReference,
    *,
    expected_timestamp: datetime | None,
) -> datetime:
    _keys(receipt, set(ERSTE_MARKET_TRANSPORT_FIELDS), "Erste Market transport receipt")
    if type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1:
        raise CorrectionEvidenceError(
            "unsupported Erste Market transport receipt schema_version"
        )
    headers = _mapping(receipt["response_headers"], "transport response_headers")
    _keys(
        headers,
        {"content-encoding", "content-type", "date"},
        "transport response_headers",
    )
    requested_url = _required_text(receipt["requested_url"], "requested_url")
    if (
        receipt["provider"] != ERSTE_MARKET_PROVIDER
        or receipt["retention_status"] != "QUARANTINED_RESPONSE"
        or receipt["request_role"] != "series"
        or not isinstance(receipt["requested_isin"], str)
        or _ISIN.fullmatch(receipt["requested_isin"]) is None
        or receipt["final_url"] != requested_url
        or type(receipt["http_status"]) is not int
        or receipt["http_status"] != 200
        or receipt["redirect_history"] != []
        or receipt["transport_error"] is not None
        or receipt["body_complete"] is not True
        or receipt["content_encoding"] != "gzip"
        or _normalized_html_media_type(receipt["content_type"])
        != "text/html; charset=utf-8"
        or headers["content-encoding"] != "gzip"
        or _normalized_html_media_type(headers["content-type"])
        != "text/html; charset=utf-8"
    ):
        raise CorrectionEvidenceError(
            "Erste Market transport receipt is outside the retained profile"
        )
    _required_text(headers["date"], "transport response date")
    if (
        type(receipt["byte_count"]) is not int
        or receipt["byte_count"] != artifact.byte_count
        or type(receipt["max_response_bytes"]) is not int
        or receipt["max_response_bytes"] != 8 * 1024 * 1024
        or receipt["byte_count"] > receipt["max_response_bytes"]
        or receipt["raw_artifact_reference"] != artifact.relative_path
        or receipt["raw_artifact_sha256"] != artifact.raw_sha256
    ):
        raise CorrectionEvidenceError(
            "Erste Market transport receipt artifact binding differs from event"
        )
    _chart_url_instrument(requested_url)
    timestamp = _retained_utc(
        receipt["retrieval_timestamp"], "transport retrieval_timestamp"
    )
    if expected_timestamp is not None and timestamp != expected_timestamp:
        raise CorrectionEvidenceError(
            "Erste Market transport receipt timestamp differs from event"
        )
    return timestamp


def _validate_erste_identity_receipt(
    receipt: Mapping[str, object],
    artifact: ArtifactReference,
    *,
    expected_timestamp: datetime | None,
) -> datetime:
    _keys(
        receipt,
        set(ERSTE_MARKET_IDENTITY_RECEIPT_FIELDS),
        "Erste Market identity receipt",
    )
    if type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1:
        raise CorrectionEvidenceError(
            "unsupported Erste Market identity receipt schema_version"
        )
    headers = _mapping(receipt["response_headers"], "identity response_headers")
    _keys(
        headers,
        {"content-encoding", "content-type", "date"},
        "identity response_headers",
    )
    requested_isin = _required_text(receipt["requested_isin"], "requested_isin")
    request_url = _required_text(receipt["request_url"], "request_url")
    parsed = urlsplit(request_url)
    if (
        receipt["provider"] != ERSTE_MARKET_PROVIDER
        or receipt["request_role"] != "identity"
        or _ISIN.fullmatch(requested_isin) is None
        or parsed.scheme != "https"
        or parsed.netloc != ERSTE_MARKET_HOST
        or parsed.path != f"/befektetesi_alapok/alap/{requested_isin}"
        or parsed.query
        or parsed.fragment
        or type(receipt["http_status"]) is not int
        or receipt["http_status"] != 200
        or _normalized_html_media_type(receipt["content_type"])
        != "text/html; charset=utf-8"
        or headers["content-encoding"] != "gzip"
        or _normalized_html_media_type(headers["content-type"])
        != "text/html; charset=utf-8"
    ):
        raise CorrectionEvidenceError(
            "Erste Market identity receipt is outside the retained profile"
        )
    _required_text(headers["date"], "identity response date")
    if (
        type(receipt["byte_count"]) is not int
        or receipt["byte_count"] != artifact.byte_count
        or receipt["raw_artifact_reference"] != artifact.relative_path
        or receipt["raw_artifact_sha256"] != artifact.raw_sha256
    ):
        raise CorrectionEvidenceError(
            "Erste Market identity receipt artifact binding differs from event"
        )
    timestamp = _retained_utc(
        receipt["retrieval_timestamp"], "identity retrieval_timestamp"
    )
    if expected_timestamp is not None and timestamp != expected_timestamp:
        raise CorrectionEvidenceError(
            "Erste Market identity receipt timestamp differs from event"
        )
    return timestamp


def _validate_publication_references(
    root: Path,
    references: tuple[PublicationReference, ...],
    semantic_gaps: list[str],
) -> tuple[str, ...]:
    verified: list[str] = []
    for reference in references:
        raw = _verify_artifact(
            root, reference.artifact, f"publication {reference.publication_id}"
        )
        if reference.locator_type is ClaimLocatorType.JSON_POINTER:
            document = _load_json_bytes(raw, f"publication {reference.publication_id}")
            _pointer(document, reference.locator, "publication locator")
            verified.append(
                f"publication artifact {reference.publication_id} bytes and JSON locator"
            )
        else:
            verified.append(f"publication artifact {reference.publication_id} bytes")
            semantic_gaps.append(
                f"publication locator {reference.publication_id} was retained but not interpreted"
            )
        if reference.recorded_availability_bound_utc is not None:
            semantic_gaps.append(
                f"publication bound {reference.publication_id} is recorded, not established by structural validation"
            )
    return tuple(verified)


def _validate_relationship(candidate: CorrectionEvidenceCandidate) -> None:
    if (
        candidate.relationship.correction_id
        != candidate.issuer_correction.correction_id
    ):
        raise CorrectionEvidenceError("correction relationship target is dangling")
    if (
        candidate.relationship.target_observation_id
        != candidate.retained_observation.observation_id
    ):
        raise CorrectionEvidenceError("observation relationship target is dangling")
    events_by_role: dict[str, set[ArtifactReference]] = {}
    for event in candidate.acquisition_events:
        events_by_role.setdefault(event.artifact_role, set()).add(event.artifact)
    required_by_role: dict[str, set[ArtifactReference]] = {
        "ISSUER_CORRECTION": {candidate.issuer_correction.artifact},
        "RETAINED_RAW_OBSERVATION": set(),
        "RETAINED_IDENTITY": set(),
        "PUBLICATION_RECORD": {
            item.artifact for item in candidate.publication_references
        },
    }
    observation = candidate.retained_observation
    if isinstance(
        observation, (RawObservationReference, ErsteMarketChartObservationReference)
    ):
        required_by_role["RETAINED_RAW_OBSERVATION"].add(observation.artifact)
    if isinstance(observation, ErsteMarketChartObservationReference):
        required_by_role["RETAINED_IDENTITY"].add(observation.identity_artifact)
        if not any(
            event.artifact_role == "RETAINED_RAW_OBSERVATION"
            and event.artifact == observation.artifact
            and event.receipt == observation.transport_receipt
            for event in candidate.acquisition_events
        ):
            raise CorrectionEvidenceError(
                "chart transport receipt is not the retained-observation acquisition event"
            )
        if not any(
            event.artifact_role == "RETAINED_IDENTITY"
            and event.artifact == observation.identity_artifact
            and event.receipt == observation.identity_receipt
            for event in candidate.acquisition_events
        ):
            raise CorrectionEvidenceError(
                "chart identity receipt is not the retained-identity acquisition event"
            )
    for role, expected in required_by_role.items():
        actual = events_by_role.get(role, set())
        if not expected.issubset(actual):
            raise CorrectionEvidenceError(
                f"acquisition events do not bind every {role} artifact"
            )
        if not actual.issubset(expected):
            raise CorrectionEvidenceError(
                f"acquisition event {role} artifact is dangling"
            )


def _verify_artifact(root: Path, reference: ArtifactReference, label: str) -> bytes:
    path = _safe_path(root, reference.relative_path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CorrectionEvidenceError(f"could not read {label}") from error
    if not raw or len(raw) > MAX_ARTIFACT_BYTES:
        raise CorrectionEvidenceError(f"{label} byte size is empty or outside bound")
    if len(raw) != reference.byte_count:
        raise CorrectionEvidenceError(f"{label} byte count mismatch")
    if hashlib.sha256(raw).hexdigest() != reference.raw_sha256:
        raise CorrectionEvidenceError(f"{label} raw SHA-256 mismatch")
    return raw


def _safe_path(root: Path, relative_path: str) -> Path:
    _relative_reference(relative_path)
    if root.is_symlink():
        raise CorrectionEvidenceError("repository root must not be a symlink")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise CorrectionEvidenceError("repository root is unavailable") from error
    current = resolved_root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.is_symlink():
            raise CorrectionEvidenceError("artifact path contains a symlink component")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as error:
        raise CorrectionEvidenceError(
            "artifact path escapes the repository or is missing"
        ) from error
    if not resolved.is_file():
        raise CorrectionEvidenceError("artifact reference is not a regular file")
    return resolved


def _sqlite_from_bytes(raw: bytes, label: str) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.execute("PRAGMA query_only=ON")
        if str(connection.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
            raise CorrectionEvidenceError(f"{label} failed SQLite integrity_check")
        connection.row_factory = sqlite3.Row
        return connection
    except (sqlite3.Error, CorrectionEvidenceError) as error:
        connection.close()
        if isinstance(error, CorrectionEvidenceError):
            raise
        raise CorrectionEvidenceError(
            f"{label} is not a supported SQLite snapshot"
        ) from error


def _load_json_bytes(raw: bytes, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, CorrectionEvidenceError) as error:
        raise CorrectionEvidenceError(f"{label} is malformed strict JSON") from error
    return _mapping(value, label)


def _load_chart_json_bytes(raw: bytes, label: str) -> Mapping[str, object]:
    """Parse only the retained chart profile's number tokens as exact Decimals."""

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_decimal_json_token,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, CorrectionEvidenceError) as error:
        raise CorrectionEvidenceError(
            f"{label} is malformed strict chart JSON"
        ) from error
    return _mapping(value, label)


def _decimal_json_token(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise CorrectionEvidenceError("chart JSON number is not a Decimal") from error
    if not value.is_finite():
        raise CorrectionEvidenceError("chart JSON number must be finite")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CorrectionEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_float(_: str) -> object:
    raise CorrectionEvidenceError("JSON floating-point numbers are prohibited")


def _reject_constant(value: str) -> object:
    raise CorrectionEvidenceError(f"non-finite JSON value is prohibited: {value}")


def _pointer(document: object, pointer: str, label: str) -> object:
    _json_pointer(pointer, label)
    current = document
    if pointer == "":
        return current
    for encoded in pointer.split("/")[1:]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise CorrectionEvidenceError(f"{label} locator is dangling")
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            if not token.isdigit() or (token.startswith("0") and token != "0"):
                raise CorrectionEvidenceError(f"{label} list locator is invalid")
            index = int(token)
            if index >= len(current):
                raise CorrectionEvidenceError(f"{label} locator is dangling")
            current = current[index]
        else:
            raise CorrectionEvidenceError(f"{label} locator traverses a scalar")
    return current


def _decimal_from_json_value(value: object, field: str) -> Decimal:
    if isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value
    elif isinstance(value, Decimal):
        if not value.is_finite() or value <= 0:
            raise CorrectionEvidenceError(f"{field} must be finite and positive")
        return value
    else:
        raise CorrectionEvidenceError(f"{field} must be decimal text or integer")
    return Decimal(_decimal_text(text, field))


def _decimal_text(value: object, field: str) -> str:
    text = _required_text(value, field)
    if text != text.strip():
        raise CorrectionEvidenceError(f"{field} must be normalized decimal text")
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise CorrectionEvidenceError(f"{field} must be decimal text") from error
    if not number.is_finite() or number <= 0:
        raise CorrectionEvidenceError(f"{field} must be finite and positive")
    return text


def _keys(raw: Mapping[str, object], required: set[str], label: str) -> None:
    actual = set(raw)
    if actual != required:
        missing = sorted(required - actual)
        unexpected = sorted(actual - required)
        raise CorrectionEvidenceError(
            f"{label} fields differ; missing={missing}, unexpected={unexpected}"
        )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise CorrectionEvidenceError(f"{field} must be a string-keyed object")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CorrectionEvidenceError(f"{field} must be an array")
    return value


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorrectionEvidenceError(f"{field} must be non-empty text")
    return value


def _identifier(value: object, field: str) -> str:
    text = _required_text(value, field)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", text):
        raise CorrectionEvidenceError(f"{field} has an invalid identifier")
    return text


def _positive_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CorrectionEvidenceError(f"{field} must be a positive integer")
    return value


def _hash_text(value: object, field: str) -> str:
    text = _required_text(value, field)
    _sha256(text, field)
    return text


def _sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CorrectionEvidenceError(f"{field} must be lowercase SHA-256 text")


def _date(value: object, field: str) -> date:
    text = _required_text(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise CorrectionEvidenceError(f"{field} must be an ISO date") from error
    if parsed.isoformat() != text:
        raise CorrectionEvidenceError(f"{field} must be canonical YYYY-MM-DD")
    return parsed


def _utc(value: object, field: str) -> datetime:
    text = _required_text(value, field)
    if not text.endswith("Z"):
        raise CorrectionEvidenceError(f"{field} must be canonical UTC text ending Z")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise CorrectionEvidenceError(
            f"{field} must be a valid UTC timestamp"
        ) from error
    if parsed.tzinfo != UTC:
        raise CorrectionEvidenceError(f"{field} must be UTC")
    return parsed


def _retained_utc(value: object, field: str) -> datetime:
    text = _required_text(value, field)
    if not text.endswith("+00:00"):
        raise CorrectionEvidenceError(
            f"{field} must be retained UTC text ending +00:00"
        )
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise CorrectionEvidenceError(
            f"{field} must be a valid UTC timestamp"
        ) from error
    if parsed.tzinfo != UTC:
        raise CorrectionEvidenceError(f"{field} must be UTC")
    return parsed


def _normalized_html_media_type(value: object) -> str:
    text = _required_text(value, "content_type")
    pieces = [piece.strip() for piece in text.split(";")]
    if len(pieces) != 2 or pieces[0].lower() != "text/html":
        raise CorrectionEvidenceError(
            "content_type is outside the retained HTML profile"
        )
    name, separator, charset = pieces[1].partition("=")
    if (
        separator != "="
        or name.strip().lower() != "charset"
        or charset.strip().strip('"').lower() != "utf-8"
    ):
        raise CorrectionEvidenceError(
            "content_type is outside the retained HTML profile"
        )
    return "text/html; charset=utf-8"


def _chart_url_instrument(value: object) -> str:
    text = _required_text(value, "chart URL")
    parsed = urlsplit(text)
    match = re.fullmatch(r"/funds/chart/([0-9]+)", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != ERSTE_MARKET_HOST
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise CorrectionEvidenceError(
            "chart URL is outside the retained provider profile"
        )
    return match.group(1)


def _json_pointer(value: object, field: str) -> str:
    text = _required_text(value, field)
    if text != "" and not text.startswith("/"):
        raise CorrectionEvidenceError(f"{field} must be an absolute JSON pointer")
    if re.search(r"~(?![01])", text):
        raise CorrectionEvidenceError(f"{field} contains invalid JSON-pointer escape")
    return text


def _relative_reference(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CorrectionEvidenceError("artifact reference must be non-empty POSIX text")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise CorrectionEvidenceError(
            "artifact reference must be normalized and repository-relative"
        )
