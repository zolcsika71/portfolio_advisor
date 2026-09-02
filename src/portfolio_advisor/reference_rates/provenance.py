"""Read-only validation for the provider-neutral reference-rate provenance contract."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path, PurePosixPath

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.migrations.reference_rate import (
    reference_rate_schema_contract,
)
from portfolio_advisor.database.migrations.reference_rate_provenance import (
    REFERENCE_RATE_PROVENANCE_MIGRATION_REVISION,
    ReferenceRateProvenanceMigrationError,
    _prepare_ecb_bundle,
)
from portfolio_advisor.database.schema.v3 import (
    REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
    REFERENCE_RATE_FEATURE_FINGERPRINT,
    REFERENCE_RATE_FEATURE_ID,
    REFERENCE_RATE_FEATURE_REVISION,
    SchemaVersionError,
    validate_schema,
)
from portfolio_advisor.reference_rates.ecb_estr import EcbEstrError

from .contracts import (
    ApprovedAvailabilitySchedule,
    ProviderRevisionTransitionContract,
    ReferenceRateContractError,
    ReferenceRateDefinition,
    ReferenceRateImportManifest,
    ReferenceRateObservation,
    ReferenceRateSource,
    canonical_request_parameters,
    classify_evidence_transition,
    validate_observation_availability,
)


class ReferenceRateProvenanceValidationError(RuntimeError):
    """Stored reference-rate provenance is incomplete, corrupt, or inconsistent."""


_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True, slots=True)
class ReferenceRateValidationRegistry:
    """Immutable operator-reviewed schedule and revision-transition approvals."""

    artifact_sha256: str
    approved_schedules: tuple[ApprovedAvailabilitySchedule, ...]
    approved_revision_contracts: tuple[ProviderRevisionTransitionContract, ...]


def load_reference_rate_validation_registry(
    path: Path,
) -> ReferenceRateValidationRegistry:
    """Load a strict offline governance registry; the file itself is never provider data."""
    if path.is_symlink():
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation registry must not be a symlink"
        )
    resolved = path.resolve()
    if not resolved.is_file() or resolved.stat().st_size > 1024 * 1024:
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation registry must be a bounded regular file"
        )
    try:
        root = json.loads(resolved.read_text(encoding="utf-8"), object_pairs_hook=_unique_json)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation registry is malformed"
        ) from error
    if not isinstance(root, dict) or set(root) != {
        "approved_availability_schedules",
        "approved_provider_revision_contracts",
        "registry_schema_version",
    }:
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation registry fields differ"
        )
    if root["registry_schema_version"] != 1:
        raise ReferenceRateProvenanceValidationError(
            "unsupported reference-rate validation registry schema"
        )
    raw_schedules = root["approved_availability_schedules"]
    raw_contracts = root["approved_provider_revision_contracts"]
    if not isinstance(raw_schedules, list) or not isinstance(raw_contracts, list):
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation registry approvals must be lists"
        )
    schedules: list[ApprovedAvailabilitySchedule] = []
    for raw in raw_schedules:
        if not isinstance(raw, dict) or set(raw) != {
            "authoritative_policy_reference",
            "benchmark_id",
            "boundaries",
            "calendar_id",
            "calendar_version",
            "rule_id",
            "rule_version",
            "source_contract_fingerprint",
        }:
            raise ReferenceRateProvenanceValidationError(
                "approved availability schedule fields differ"
            )
        boundaries = raw["boundaries"]
        if not isinstance(boundaries, list):
            raise ReferenceRateProvenanceValidationError(
                "approved availability schedule boundaries must be a list"
            )
        parsed_boundaries: list[tuple[date, str]] = []
        for boundary in boundaries:
            if not isinstance(boundary, dict) or set(boundary) != {
                "availability_boundary_utc",
                "observation_date",
            }:
                raise ReferenceRateProvenanceValidationError(
                    "approved availability boundary fields differ"
                )
            parsed_boundaries.append(
                (
                    _date(_required_string(boundary["observation_date"])),
                    _required_string(boundary["availability_boundary_utc"]),
                )
            )
        schedules.append(
            ApprovedAvailabilitySchedule(
                rule_id=_required_string(raw["rule_id"]),
                rule_version=_required_string(raw["rule_version"]),
                authoritative_policy_reference=_required_string(
                    raw["authoritative_policy_reference"]
                ),
                benchmark_id=_required_string(raw["benchmark_id"]),
                source_contract_fingerprint=_required_string(
                    raw["source_contract_fingerprint"]
                ),
                calendar_id=_required_string(raw["calendar_id"]),
                calendar_version=_required_string(raw["calendar_version"]),
                boundaries=tuple(parsed_boundaries),
            )
        )
    contracts: list[ProviderRevisionTransitionContract] = []
    for raw in raw_contracts:
        if not isinstance(raw, dict) or set(raw) != {
            "authoritative_reference",
            "benchmark_id",
            "contract_id",
            "contract_version",
            "revision_indicator_source_field",
            "revision_indicator_value",
            "source_contract_fingerprint",
        }:
            raise ReferenceRateProvenanceValidationError(
                "approved provider-revision contract fields differ"
            )
        contracts.append(
            ProviderRevisionTransitionContract(
                contract_id=_required_string(raw["contract_id"]),
                contract_version=_required_string(raw["contract_version"]),
                benchmark_id=_required_string(raw["benchmark_id"]),
                source_contract_fingerprint=_required_string(
                    raw["source_contract_fingerprint"]
                ),
                revision_indicator_source_field=_required_string(
                    raw["revision_indicator_source_field"]
                ),
                revision_indicator_value=_required_string(
                    raw["revision_indicator_value"]
                ),
                authoritative_reference=_required_string(raw["authoritative_reference"]),
            )
        )
    schedule_fingerprints = [item.calendar_fingerprint for item in schedules]
    contract_fingerprints = [item.fingerprint for item in contracts]
    if (
        len(schedule_fingerprints) != len(set(schedule_fingerprints))
        or len(contract_fingerprints) != len(set(contract_fingerprints))
    ):
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation registry contains duplicate approvals"
        )
    return ReferenceRateValidationRegistry(
        artifact_sha256=_sha256(resolved),
        approved_schedules=tuple(schedules),
        approved_revision_contracts=tuple(contracts),
    )


def validate_reference_rate_database(
    *,
    target: Path,
    repository_root: Path,
    approved_schedules: tuple[ApprovedAvailabilitySchedule, ...] = (),
    approved_revision_contracts: tuple[ProviderRevisionTransitionContract, ...] = (),
) -> dict[str, object]:
    """Validate all benchmark bundles and exact v2 schema without modifying bytes."""
    if target.is_symlink() or repository_root.is_symlink():
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation paths must not be symlinks"
        )
    target = target.resolve()
    root = repository_root.resolve()
    if not target.is_file():
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation target must be a regular SQLite file"
        )
    before = _sha256(target)
    try:
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA query_only=ON")
            validate_schema(connection)
            definitions = _definitions(connection)
            sources = _sources(connection, definitions)
            manifests = _manifests(connection, definitions, sources, root)
            observations = _observations(
                connection,
                definitions,
                sources,
                manifests,
                approved_schedules=approved_schedules,
            )
            _validate_revision_chains(
                observations,
                manifests,
                approved_revision_contracts=approved_revision_contracts,
            )
            benchmark_ids = {item.benchmark_id for item in definitions.values()}
            if benchmark_ids != {"ESTR"}:
                raise ReferenceRateProvenanceValidationError(
                    "Phase C0 admits exactly the existing ESTR benchmark scope"
                )
            _validate_phase_c0_ecb_bundle(
                definitions=definitions,
                sources=sources,
                manifests=manifests,
                observations=observations,
                repository_root=root,
            )
            integrity = tuple(
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != ("ok",) or violations:
                raise ReferenceRateProvenanceValidationError(
                    "reference-rate database integrity or foreign-key validation failed"
                )
            schema_contract = reference_rate_schema_contract(connection)
            constructed = _constructed_counts(connection)
            if any(constructed.values()):
                raise ReferenceRateProvenanceValidationError(
                    "constructed-portfolio production tables must remain empty"
                )
    except (
        sqlite3.Error,
        EcbEstrError,
        ReferenceRateContractError,
        SchemaVersionError,
        ReferenceRateProvenanceMigrationError,
        ValueError,
    ) as error:
        if isinstance(error, ReferenceRateProvenanceValidationError):
            raise
        raise ReferenceRateProvenanceValidationError(
            "reference-rate provenance validation failed closed"
        ) from error
    after = _sha256(target)
    if after != before:
        raise ReferenceRateProvenanceValidationError(
            "read-only reference-rate validation changed database bytes"
        )
    benchmark_counts = Counter(item.benchmark_id for item in observations.values())
    availability_counts = Counter(item.availability_basis for item in observations.values())
    revision_counts = Counter(item.provider_revision_status for item in observations.values())
    ranges: dict[str, dict[str, object]] = {}
    for benchmark_id in sorted({item.benchmark_id for item in observations.values()}):
        dates = sorted(
            {
                item.observation_date
                for item in observations.values()
                if item.benchmark_id == benchmark_id
            }
        )
        ranges[benchmark_id] = {
            "first_observation_date": dates[0].isoformat(),
            "last_observation_date": dates[-1].isoformat(),
            "observation_count": len(dates),
            "version_count": benchmark_counts[benchmark_id],
        }
    return {
        "audit_schema_version": 2,
        "availability_basis_counts": dict(sorted(availability_counts.items())),
        "approved_availability_schedule_fingerprints": sorted(
            item.calendar_fingerprint for item in approved_schedules
        ),
        "approved_provider_revision_contract_fingerprints": sorted(
            item.fingerprint for item in approved_revision_contracts
        ),
        "admitted_benchmark_ids": sorted(benchmark_ids),
        "benchmark_ranges": ranges,
        "constructed_portfolio_row_counts": constructed,
        "contract_schema_version": REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        "database_sha256": before,
        "definition_count": len(definitions),
        "feature_contract_fingerprint": REFERENCE_RATE_FEATURE_FINGERPRINT,
        "feature_id": REFERENCE_RATE_FEATURE_ID,
        "feature_revision": REFERENCE_RATE_FEATURE_REVISION,
        "foreign_key_violations": 0,
        "hufonia_evidence": (
            "NOT_STARTED" if "HUFONIA" not in benchmark_ids else "PRESENT_UNEXPECTED"
        ),
        "integrity_check": "ok",
        "manifest_count": len(manifests),
        "migration_revision": REFERENCE_RATE_PROVENANCE_MIGRATION_REVISION,
        "milestone_11": "NO_GO",
        "milestone_12": "NO_GO",
        "milestone_13": "NO_GO",
        "observation_version_count": len(observations),
        "production_cutover": "NOT_AUTHORIZED",
        "provider_revision_status_counts": dict(sorted(revision_counts.items())),
        "reference_rate_runtime_admission": (
            "EUR_ESTR_ONLY" if benchmark_ids == {"ESTR"} else "NO_GO"
        ),
        "schema_contract_fingerprint": canonical_fingerprint(schema_contract),
        "sofr_evidence": (
            "NOT_ADMITTED" if "SOFR" not in benchmark_ids else "PRESENT_UNEXPECTED"
        ),
        "source_count": len(sources),
        "status": "PASS",
    }


def _definitions(connection: sqlite3.Connection) -> dict[int, ReferenceRateDefinition]:
    result: dict[int, ReferenceRateDefinition] = {}
    rows = connection.execute(
        "SELECT * FROM reference_rate_definition ORDER BY reference_rate_definition_id"
    ).fetchall()
    for row in rows:
        item = ReferenceRateDefinition(
            contract_schema_version=_integer(row["contract_schema_version"]),
            benchmark_id=str(row["benchmark_id"]),
            benchmark_name=str(row["benchmark_name"]),
            currency_code=str(row["currency_code"]),
            administrator=str(row["administrator"]),
            series_identifier=str(row["series_identifier"]),
            rate_units=str(row["rate_units"]),
            day_count_convention=str(row["day_count_convention"]),
            compounding_convention=str(row["compounding_convention"]),
            definition_version=str(row["definition_version"]),
        )
        if str(row["definition_fingerprint"]) != item.fingerprint:
            raise ReferenceRateProvenanceValidationError(
                "stored reference-rate definition fingerprint is invalid"
            )
        result[_integer(row["reference_rate_definition_id"])] = item
    return result


def _validate_phase_c0_ecb_bundle(
    *,
    definitions: dict[int, ReferenceRateDefinition],
    sources: dict[int, tuple[int, ReferenceRateSource]],
    manifests: dict[int, tuple[int, int, ReferenceRateImportManifest]],
    observations: dict[int, ReferenceRateObservation],
    repository_root: Path,
) -> None:
    if len(definitions) != 1 or len(sources) != 1 or len(manifests) != 1:
        raise ReferenceRateProvenanceValidationError(
            "Phase C0 requires exactly one preserved ECB ESTR evidence bundle"
        )
    stored_manifest = next(iter(manifests.values()))[2]
    raw_artifact = repository_root / PurePosixPath(
        stored_manifest.raw_artifact_reference
    )
    prepared = _prepare_ecb_bundle(
        repository_root=repository_root,
        raw_artifact=raw_artifact,
        receipt_path=raw_artifact.with_suffix(".receipt.json"),
    )
    stored_observations = tuple(
        observations[key]
        for key in sorted(
            observations,
            key=lambda item: (
                observations[item].observation_date,
                observations[item].revision_sequence,
                item,
            ),
        )
    )
    if (
        tuple(definitions.values()) != (prepared.definition,)
        or tuple(item[1] for item in sources.values()) != (prepared.source,)
        or tuple(item[2] for item in manifests.values()) != (prepared.manifest,)
        or stored_observations != prepared.observations
    ):
        raise ReferenceRateProvenanceValidationError(
            "stored ESTR bundle differs from retained ECB artifact and receipt"
        )


def _sources(
    connection: sqlite3.Connection,
    definitions: dict[int, ReferenceRateDefinition],
) -> dict[int, tuple[int, ReferenceRateSource]]:
    result: dict[int, tuple[int, ReferenceRateSource]] = {}
    rows = connection.execute(
        "SELECT * FROM reference_rate_source ORDER BY reference_rate_source_id"
    ).fetchall()
    for row in rows:
        definition_id = _integer(row["reference_rate_definition_id"])
        definition = definitions.get(definition_id)
        if definition is None:
            raise ReferenceRateProvenanceValidationError(
                "reference-rate source has no definition"
            )
        item = ReferenceRateSource(
            source_code=str(row["source_code"]),
            benchmark_id=definition.benchmark_id,
            source_organization=str(row["source_organization"]),
            official_page_url=str(row["official_page_url"]),
            machine_readable_url=str(row["machine_readable_url"]),
            response_format=str(row["response_format"]),
            source_role=str(row["source_role"]),
            authentication_requirement=str(row["authentication_requirement"]),
            automated_use_status=str(row["automated_use_status"]),
            licensing_reference=str(row["licensing_reference"]),
            raw_retention_status=str(row["raw_retention_status"]),
        )
        if str(row["source_contract_fingerprint"]) != item.fingerprint:
            raise ReferenceRateProvenanceValidationError(
                "stored reference-rate source fingerprint is invalid"
            )
        result[_integer(row["reference_rate_source_id"])] = (definition_id, item)
    return result


def _manifests(
    connection: sqlite3.Connection,
    definitions: dict[int, ReferenceRateDefinition],
    sources: dict[int, tuple[int, ReferenceRateSource]],
    root: Path,
) -> dict[int, tuple[int, int, ReferenceRateImportManifest]]:
    result: dict[int, tuple[int, int, ReferenceRateImportManifest]] = {}
    rows = connection.execute(
        """SELECT * FROM reference_rate_import_manifest
           ORDER BY reference_rate_import_manifest_id"""
    ).fetchall()
    for row in rows:
        definition_id = _integer(row["reference_rate_definition_id"])
        source_id = _integer(row["reference_rate_source_id"])
        source_entry = sources.get(source_id)
        if definition_id not in definitions or source_entry is None or source_entry[0] != definition_id:
            raise ReferenceRateProvenanceValidationError(
                "reference-rate manifest identity crosses definition/source boundaries"
            )
        parameters = _json_string_mapping(str(row["request_parameters_json"]))
        source = source_entry[1]
        item = ReferenceRateImportManifest(
            provenance_contract_version=_integer(row["provenance_contract_version"]),
            source_contract_fingerprint=source.fingerprint,
            retrieval_timestamp=str(row["retrieval_timestamp"]),
            request_url=str(row["request_url"]),
            request_parameters=canonical_request_parameters(parameters),
            response_content_type=str(row["response_content_type"]),
            http_status=_integer(row["http_status"]),
            raw_artifact_reference=str(row["raw_artifact_reference"]),
            raw_artifact_sha256=str(row["raw_artifact_sha256"]),
            provider_dataset_version=_optional_text(row["provider_dataset_version"]),
            provider_dataset_version_source_field=_optional_text(
                row["provider_dataset_version_source_field"]
            ),
            internal_evidence_identity_scheme=str(
                row["internal_evidence_identity_scheme"]
            ),
            internal_evidence_identity=str(row["internal_evidence_identity"]),
            import_status=str(row["import_status"]),
            dataset_fingerprint=str(row["dataset_fingerprint"]),
        )
        if str(row["manifest_fingerprint"]) != item.fingerprint:
            raise ReferenceRateProvenanceValidationError(
                "stored reference-rate manifest fingerprint is invalid"
            )
        unresolved_artifact = root / PurePosixPath(item.raw_artifact_reference)
        approved_root = (root / "data" / "raw" / "reference_rates").resolve()
        if _has_symlink_component(unresolved_artifact, root):
            raise ReferenceRateProvenanceValidationError(
                "retained reference-rate artifact path contains a symlink"
            )
        artifact = unresolved_artifact.resolve()
        if (
            not artifact.is_relative_to(approved_root)
            or not artifact.is_file()
            or _sha256(artifact) != item.raw_artifact_sha256
        ):
            raise ReferenceRateProvenanceValidationError(
                "retained reference-rate artifact is missing or has been tampered with"
            )
        result[_integer(row["reference_rate_import_manifest_id"])] = (
            definition_id,
            source_id,
            item,
        )
    return result


def _observations(
    connection: sqlite3.Connection,
    definitions: dict[int, ReferenceRateDefinition],
    sources: dict[int, tuple[int, ReferenceRateSource]],
    manifests: dict[int, tuple[int, int, ReferenceRateImportManifest]],
    *,
    approved_schedules: tuple[ApprovedAvailabilitySchedule, ...],
) -> dict[int, ReferenceRateObservation]:
    result: dict[int, ReferenceRateObservation] = {}
    rows = connection.execute(
        """SELECT * FROM reference_rate_observation
           ORDER BY reference_rate_definition_id, observation_date,
                    revision_sequence, reference_rate_observation_id"""
    ).fetchall()
    for row in rows:
        observation_id = _integer(row["reference_rate_observation_id"])
        definition_id = _integer(row["reference_rate_definition_id"])
        source_id = _integer(row["reference_rate_source_id"])
        manifest_id = _integer(row["reference_rate_import_manifest_id"])
        definition = definitions.get(definition_id)
        source_entry = sources.get(source_id)
        manifest_entry = manifests.get(manifest_id)
        if (
            definition is None
            or source_entry is None
            or manifest_entry is None
            or source_entry[0] != definition_id
            or manifest_entry[:2] != (definition_id, source_id)
        ):
            raise ReferenceRateProvenanceValidationError(
                "reference-rate observation crosses benchmark/source/manifest identity"
            )
        if manifest_entry[2].import_status != "VALIDATED_ADMITTED":
            raise ReferenceRateProvenanceValidationError(
                "admitted observation is backed by a rejected import manifest"
            )
        predecessor_id = row["supersedes_observation_id"]
        predecessor = result.get(_integer(predecessor_id)) if predecessor_id is not None else None
        item = ReferenceRateObservation(
            provenance_contract_version=_integer(row["provenance_contract_version"]),
            benchmark_id=definition.benchmark_id,
            source_contract_fingerprint=source_entry[1].fingerprint,
            import_manifest_fingerprint=manifest_entry[2].fingerprint,
            observation_date=_date(str(row["observation_date"])),
            provider_publication_date=(
                _date(str(row["provider_publication_date"]))
                if row["provider_publication_date"] is not None
                else None
            ),
            rate=_decimal(str(row["rate_decimal"])),
            provider_revision_id=_optional_text(row["provider_revision_id"]),
            provider_revision_id_source_field=_optional_text(
                row["provider_revision_id_source_field"]
            ),
            provider_revision_indicator=_optional_text_allow_empty(
                row["provider_revision_indicator"]
            ),
            provider_revision_indicator_source_field=_optional_text(
                row["provider_revision_indicator_source_field"]
            ),
            provider_revision_status=str(row["provider_revision_status"]),
            provider_revision_contract_id=_optional_text(
                row["provider_revision_contract_id"]
            ),
            provider_revision_contract_version=_optional_text(
                row["provider_revision_contract_version"]
            ),
            provider_revision_contract_revision_indicator_value=_optional_text(
                row["provider_revision_contract_revision_indicator_value"]
            ),
            provider_revision_contract_authoritative_reference=_optional_text(
                row["provider_revision_contract_authoritative_reference"]
            ),
            provider_revision_contract_fingerprint=_optional_text(
                row["provider_revision_contract_fingerprint"]
            ),
            provider_publication_value=_optional_text(row["provider_publication_value"]),
            provider_publication_value_kind=_optional_text(
                row["provider_publication_value_kind"]
            ),
            provider_publication_source_field=_optional_text(
                row["provider_publication_source_field"]
            ),
            availability_basis=str(row["availability_basis"]),
            availability_boundary_utc=str(row["availability_boundary_utc"]),
            availability_derivation_rule_id=_optional_text(
                row["availability_derivation_rule_id"]
            ),
            availability_derivation_rule_version=_optional_text(
                row["availability_derivation_rule_version"]
            ),
            availability_policy_reference=_optional_text(
                row["availability_policy_reference"]
            ),
            availability_calendar_id=_optional_text(row["availability_calendar_id"]),
            availability_calendar_version=_optional_text(
                row["availability_calendar_version"]
            ),
            availability_calendar_fingerprint=_optional_text(
                row["availability_calendar_fingerprint"]
            ),
            revision_sequence=_integer(row["revision_sequence"]),
            supersedes_observation_fingerprint=(
                predecessor.fingerprint if predecessor is not None else None
            ),
            is_current=bool(_boolean(row["is_current"])),
            quality_status=str(row["quality_status"]),
        )
        if predecessor_id is not None and predecessor is None:
            raise ReferenceRateProvenanceValidationError(
                "reference-rate observation predecessor is unavailable or crosses ordering"
            )
        if str(row["observation_fingerprint"]) != item.fingerprint:
            raise ReferenceRateProvenanceValidationError(
                "stored reference-rate observation fingerprint is invalid"
            )
        manifest = manifest_entry[2]
        _validate_provider_identity_separation(item, manifest)
        validate_observation_availability(
            item,
            manifest,
            approved_schedules=approved_schedules,
        )
        result[observation_id] = item
    return result


def _validate_revision_chains(
    observations: dict[int, ReferenceRateObservation],
    manifests: dict[int, tuple[int, int, ReferenceRateImportManifest]],
    *,
    approved_revision_contracts: tuple[ProviderRevisionTransitionContract, ...],
) -> None:
    grouped: dict[tuple[str, str, date], list[ReferenceRateObservation]] = defaultdict(list)
    manifest_by_fingerprint = {item[2].fingerprint: item[2] for item in manifests.values()}
    sources_by_benchmark: dict[str, set[str]] = defaultdict(set)
    for item in observations.values():
        sources_by_benchmark[item.benchmark_id].add(item.source_contract_fingerprint)
        grouped[
            (item.benchmark_id, item.source_contract_fingerprint, item.observation_date)
        ].append(item)
    if any(len(sources) != 1 for sources in sources_by_benchmark.values()):
        raise ReferenceRateProvenanceValidationError(
            "reference-rate observations would stitch sources across dates"
        )
    for items in grouped.values():
        ordered = sorted(items, key=lambda item: item.revision_sequence)
        if [item.revision_sequence for item in ordered] != list(range(1, len(ordered) + 1)):
            raise ReferenceRateProvenanceValidationError(
                "reference-rate revision sequence is not contiguous"
            )
        if sum(item.is_current for item in ordered) != 1 or not ordered[-1].is_current:
            raise ReferenceRateProvenanceValidationError(
                "reference-rate revision chain has no unique latest current projection"
            )
        for previous, current in pairwise(ordered):
            if current.supersedes_observation_fingerprint != previous.fingerprint:
                raise ReferenceRateProvenanceValidationError(
                    "reference-rate revision chain predecessor is inconsistent"
                )
            if current.availability_boundary_utc < previous.availability_boundary_utc:
                raise ReferenceRateProvenanceValidationError(
                    "reference-rate revision availability moves backwards"
                )
            matches = [
                contract
                for contract in approved_revision_contracts
                if contract.fingerprint == current.provider_revision_contract_fingerprint
            ]
            if len(matches) != 1:
                raise ReferenceRateProvenanceValidationError(
                    "changed reference-rate value lacks a unique approved revision contract"
                )
            previous_manifest = manifest_by_fingerprint.get(
                previous.import_manifest_fingerprint
            )
            current_manifest = manifest_by_fingerprint.get(current.import_manifest_fingerprint)
            if previous_manifest is None or current_manifest is None:
                raise ReferenceRateProvenanceValidationError(
                    "reference-rate revision manifest is unavailable"
                )
            transition = classify_evidence_transition(
                previous_internal_evidence_identity=(
                    previous_manifest.internal_evidence_identity
                ),
                incoming_internal_evidence_identity=(
                    current_manifest.internal_evidence_identity
                ),
                previous_rate=previous.rate,
                incoming_rate=current.rate,
                benchmark_id=current.benchmark_id,
                source_contract_fingerprint=current.source_contract_fingerprint,
                provider_revision_status=current.provider_revision_status,
                provider_revision_indicator=current.provider_revision_indicator,
                provider_revision_indicator_source_field=(
                    current.provider_revision_indicator_source_field
                ),
                provider_revision_contract=matches[0],
            )
            if transition.status != "AUTHORIZED_PROVIDER_REVISION":
                raise ReferenceRateProvenanceValidationError(
                    "changed reference-rate value is not an authorized provider revision"
                )


def _constructed_counts(connection: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "constructed_portfolio_holding_lineage": (
            "SELECT count(*) FROM constructed_portfolio_holding_lineage"
        ),
        "constructed_portfolio_metadata": "SELECT count(*) FROM constructed_portfolio_metadata",
        "portfolio_metric_observation": "SELECT count(*) FROM portfolio_metric_observation",
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
    }
    return {name: int(connection.execute(sql).fetchone()[0]) for name, sql in queries.items()}


def _validate_provider_identity_separation(
    observation: ReferenceRateObservation,
    manifest: ReferenceRateImportManifest,
) -> None:
    internal_values = {
        manifest.internal_evidence_identity,
        manifest.raw_artifact_sha256,
        manifest.dataset_fingerprint,
        manifest.retrieval_timestamp,
        manifest.request_url,
        manifest.raw_artifact_reference,
        observation.observation_date.isoformat(),
        observation.rate_decimal,
        str(observation.revision_sequence),
        observation.provider_revision_id_source_field,
        observation.provider_revision_indicator_source_field,
        observation.provider_publication_source_field,
        *(key for key, _ in manifest.request_parameters),
        *(value for _, value in manifest.request_parameters),
    }
    internal_values.discard(None)
    for label, value in (
        ("provider revision", observation.provider_revision_id),
        ("provider dataset", manifest.provider_dataset_version),
    ):
        if value is not None and (
            value in internal_values
            or value.upper() == "INITIAL"
            or _UUID.fullmatch(value) is not None
        ):
            raise ReferenceRateProvenanceValidationError(
                f"system-generated evidence is falsely presented as {label} metadata"
            )


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _json_string_mapping(value: str) -> dict[str, str]:
    parsed = json.loads(value, object_pairs_hook=_unique_json)
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in parsed.items()
    ):
        raise ReferenceRateProvenanceValidationError(
            "stored request parameters are not a string mapping"
        )
    if canonical_json(parsed) != value:
        raise ReferenceRateProvenanceValidationError(
            "stored request parameters are not canonical JSON"
        )
    return parsed


def _unique_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise ReferenceRateProvenanceValidationError(
                "JSON object contains a duplicate key"
            )
        result[key] = item
    return result


def _required_string(value: object) -> str:
    if not isinstance(value, str):
        raise ReferenceRateProvenanceValidationError(
            "reference-rate validation registry values must be strings"
        )
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ReferenceRateProvenanceValidationError("stored integer is not exact")
    return value


def _boolean(value: object) -> int:
    result = _integer(value)
    if result not in {0, 1}:
        raise ReferenceRateProvenanceValidationError("stored Boolean is not zero or one")
    return result


def _date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ReferenceRateProvenanceValidationError("stored date is not canonical")
    return parsed


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ReferenceRateProvenanceValidationError("stored rate is not Decimal") from error
    if not result.is_finite():
        raise ReferenceRateProvenanceValidationError("stored rate is not finite")
    return result


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReferenceRateProvenanceValidationError("stored optional text is malformed")
    return value


def _optional_text_allow_empty(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReferenceRateProvenanceValidationError("stored raw indicator is not text")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
