"""Exact, immutable reference-rate evidence contract tests."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    load_capital_defensive_construction_policy,
)
from portfolio_advisor.reference_rates import (
    REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
    ApprovedAvailabilitySchedule,
    ProviderRevisionTransitionContract,
    ReferenceRateContractError,
    ReferenceRateDefinition,
    ReferenceRateImportManifest,
    ReferenceRateObservation,
    ReferenceRateSource,
    canonical_utc_timestamp,
    classify_evidence_transition,
    internal_evidence_identity,
    observations_available_as_of,
    validate_observation_availability,
    validate_policy_binding,
)
from portfolio_advisor.reference_rates.contracts import canonical_request_parameters
from portfolio_advisor.reference_rates.provenance import (
    ReferenceRateProvenanceValidationError,
    load_reference_rate_validation_registry,
)

ROOT = Path(__file__).resolve().parents[1]


def _definition() -> ReferenceRateDefinition:
    return ReferenceRateDefinition(
        contract_schema_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id="ESTR",
        benchmark_name="€STR",
        currency_code="EUR",
        administrator="European Central Bank",
        series_identifier="EST.B.EU000A2X2A25.WT",
        rate_units="PERCENT_PER_ANNUM",
        day_count_convention="ACT_360",
        compounding_convention="OFFICIAL_OVERNIGHT_DAILY_COMPOUNDING",
        definition_version="1.0.0",
    )


def _source() -> ReferenceRateSource:
    return ReferenceRateSource(
        source_code="ECB_DATA_API_ESTR",
        benchmark_id="ESTR",
        source_organization="European Central Bank",
        official_page_url=(
            "https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/"
            "euro_short-term_rate/html/index.en.html"
        ),
        machine_readable_url=(
            "https://data-api.ecb.europa.eu/service/data/EST/"
            "B.EU000A2X2A25.WT"
        ),
        response_format="CSV",
        source_role="OFFICIAL_ADMINISTRATOR",
        authentication_requirement="NONE",
        automated_use_status="PERMITTED",
        licensing_reference="ECB €STR official page reuse statement",
        raw_retention_status="PERMITTED",
    )


def _manifest(source: ReferenceRateSource) -> ReferenceRateImportManifest:
    parameters = canonical_request_parameters(
        {"endPeriod": "2026-08-31", "format": "csvdata"}
    )
    identity = internal_evidence_identity(
        source_contract_fingerprint=source.fingerprint,
        retrieval_timestamp="2026-09-01T12:00:00+00:00",
        request_url=source.machine_readable_url,
        request_parameters=parameters,
        response_content_type="text/csv",
        http_status=200,
        raw_artifact_reference="data/raw/reference_rates/ecb/estr-2026-09-01.csv",
        raw_artifact_sha256="a" * 64,
    )
    return ReferenceRateImportManifest(
        provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        source_contract_fingerprint=source.fingerprint,
        retrieval_timestamp="2026-09-01T12:00:00+00:00",
        request_url=source.machine_readable_url,
        request_parameters=parameters,
        response_content_type="text/csv",
        http_status=200,
        raw_artifact_reference="data/raw/reference_rates/ecb/estr-2026-09-01.csv",
        raw_artifact_sha256="a" * 64,
        provider_dataset_version="Tue, 01 Sep 2026 06:05:24 GMT",
        provider_dataset_version_source_field="HTTP_LAST_MODIFIED",
        internal_evidence_identity_scheme="SYSTEM_CANONICAL_ARTIFACT_V1",
        internal_evidence_identity=identity,
        import_status="VALIDATED_ADMITTED",
        dataset_fingerprint="b" * 64,
    )


def test_definition_is_strict_versioned_and_canonically_fingerprinted() -> None:
    definition = _definition()
    assert definition.fingerprint == _definition().fingerprint
    assert len(definition.fingerprint) == 64
    assert definition.canonical_payload()["series_identifier"] == "EST.B.EU000A2X2A25.WT"
    with pytest.raises(ReferenceRateContractError, match="schema version"):
        replace(definition, contract_schema_version=1)
    with pytest.raises(ReferenceRateContractError, match="currency"):
        replace(definition, currency_code="GBP")


def test_definition_mapping_rejects_unknown_and_missing_fields() -> None:
    payload = _definition().canonical_payload()
    payload["unknown"] = True
    with pytest.raises(ReferenceRateContractError, match="unknown"):
        ReferenceRateDefinition.from_mapping(payload)
    del payload["unknown"]
    del payload["series_identifier"]
    with pytest.raises(ReferenceRateContractError, match="missing"):
        ReferenceRateDefinition.from_mapping(payload)


def test_source_requires_explicit_access_licensing_and_raw_retention_states() -> None:
    source = _source()
    assert source.fingerprint == _source().fingerprint
    with pytest.raises(ReferenceRateContractError, match="automated_use_status"):
        replace(source, automated_use_status="ASSUMED")
    with pytest.raises(ReferenceRateContractError, match="HTTPS"):
        replace(source, machine_readable_url="http://example.test/data.csv")
    with pytest.raises(ReferenceRateContractError, match="non-empty"):
        replace(source, licensing_reference="")


def test_policy_binding_rejects_unapproved_page_benchmark_or_administrator() -> None:
    policy = load_capital_defensive_construction_policy(
        ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    )
    validate_policy_binding(_definition(), _source(), policy)
    with pytest.raises(ReferenceRateContractError, match="differs from policy"):
        validate_policy_binding(
            _definition(),
            replace(_source(), official_page_url="https://example.test/not-approved"),
            policy,
        )


def test_manifest_preserves_exact_request_raw_hash_and_retrieval_provenance() -> None:
    source = _source()
    manifest = _manifest(source)
    assert manifest.fingerprint == _manifest(source).fingerprint
    assert manifest.canonical_payload()["request_parameters"] == {
        "endPeriod": "2026-08-31",
        "format": "csvdata",
    }
    with pytest.raises(ReferenceRateContractError, match="timezone"):
        replace(manifest, retrieval_timestamp="2026-09-01T12:00:00")
    with pytest.raises(ReferenceRateContractError, match="HTTP 200"):
        replace(manifest, http_status=206)
    with pytest.raises(ReferenceRateContractError, match="relative POSIX"):
        replace(manifest, raw_artifact_reference="../private.csv")


def test_observation_requires_decimal_dates_quality_and_revision_lineage() -> None:
    source = _source()
    manifest = _manifest(source)
    observation = ReferenceRateObservation(
        provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id="ESTR",
        source_contract_fingerprint=source.fingerprint,
        import_manifest_fingerprint=manifest.fingerprint,
        observation_date=date(2026, 8, 31),
        provider_publication_date=date(2026, 9, 1),
        rate=Decimal("2.1880"),
        provider_revision_id="STANDARD-2026-09-01T08:00:00CET",
        provider_revision_id_source_field="VALID_FROM",
        provider_revision_indicator="A",
        provider_revision_indicator_source_field="OBS_STATUS",
        provider_revision_status="PROVIDER_EXPLICIT_NO_REVISION",
        provider_revision_contract_id=None,
        provider_revision_contract_version=None,
        provider_revision_contract_revision_indicator_value=None,
        provider_revision_contract_authoritative_reference=None,
        provider_revision_contract_fingerprint=None,
        provider_publication_value="2026-09-01T08:00:00+02:00",
        provider_publication_value_kind="TIMESTAMP",
        provider_publication_source_field="VALID_FROM",
        availability_basis="PROVIDER_REPORTED",
        availability_boundary_utc="2026-09-01T06:00:00.000000Z",
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
    assert observation.rate_decimal == "2.188"
    assert observation.fingerprint == replace(observation, rate=Decimal("2.188")).fingerprint
    with pytest.raises(ReferenceRateContractError, match="exact Decimal"):
        replace(observation, rate=2.188)  # type: ignore[arg-type]
    with pytest.raises(ReferenceRateContractError, match="precedes"):
        replace(observation, provider_publication_date=date(2026, 8, 30))
    with pytest.raises(ReferenceRateContractError, match="later revision"):
        replace(observation, revision_sequence=2)
    with pytest.raises(ReferenceRateContractError, match="quality"):
        replace(observation, quality_status="PENDING")


def test_optional_provider_metadata_and_raw_revision_presence_are_truthful() -> None:
    source = _source()
    manifest = replace(
        _manifest(source),
        provider_dataset_version=None,
        provider_dataset_version_source_field=None,
    )
    assert manifest.canonical_payload()["provider_dataset_version"] is None
    with pytest.raises(ReferenceRateContractError, match="both be supplied"):
        replace(manifest, provider_dataset_version="provider-v1")

    base = ReferenceRateObservation(
        provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id="ESTR",
        source_contract_fingerprint=source.fingerprint,
        import_manifest_fingerprint=manifest.fingerprint,
        observation_date=date(2026, 8, 31),
        provider_publication_date=None,
        rate=Decimal("2.188"),
        provider_revision_id=None,
        provider_revision_id_source_field=None,
        provider_revision_indicator=None,
        provider_revision_indicator_source_field=None,
        provider_revision_status="PROVIDER_REVISION_FIELD_NOT_SUPPLIED",
        provider_revision_contract_id=None,
        provider_revision_contract_version=None,
        provider_revision_contract_revision_indicator_value=None,
        provider_revision_contract_authoritative_reference=None,
        provider_revision_contract_fingerprint=None,
        provider_publication_value=None,
        provider_publication_value_kind=None,
        provider_publication_source_field=None,
        availability_basis="RETRIEVAL_BOUND",
        availability_boundary_utc="2026-09-01T12:00:00.000000Z",
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
    validate_observation_availability(base, manifest)
    empty = replace(
        base,
        provider_revision_indicator="",
        provider_revision_indicator_source_field="revisionIndicator",
        provider_revision_status="PROVIDER_EMPTY_REVISION_INDICATOR",
    )
    assert base.fingerprint != empty.fingerprint
    with pytest.raises(ReferenceRateContractError, match="empty"):
        replace(empty, provider_revision_indicator="INITIAL")


def test_all_availability_bases_and_temporal_no_lookahead() -> None:
    source = _source()
    manifest = _manifest(source)
    retrieval = ReferenceRateObservation(
        provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id="ESTR",
        source_contract_fingerprint=source.fingerprint,
        import_manifest_fingerprint=manifest.fingerprint,
        observation_date=date(2026, 8, 31),
        provider_publication_date=None,
        rate=Decimal("2.1"),
        provider_revision_id=None,
        provider_revision_id_source_field=None,
        provider_revision_indicator=None,
        provider_revision_indicator_source_field=None,
        provider_revision_status="PROVIDER_REVISION_FIELD_NOT_SUPPLIED",
        provider_revision_contract_id=None,
        provider_revision_contract_version=None,
        provider_revision_contract_revision_indicator_value=None,
        provider_revision_contract_authoritative_reference=None,
        provider_revision_contract_fingerprint=None,
        provider_publication_value=None,
        provider_publication_value_kind=None,
        provider_publication_source_field=None,
        availability_basis="RETRIEVAL_BOUND",
        availability_boundary_utc="2026-09-01T12:00:00.000000Z",
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
    validate_observation_availability(retrieval, manifest)
    assert observations_available_as_of(
        (retrieval,), datetime(2026, 9, 1, 11, 59, tzinfo=UTC)
    ) == ()
    assert observations_available_as_of(
        (retrieval,), datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    ) == (retrieval,)
    with pytest.raises(ReferenceRateContractError, match="aware"):
        observations_available_as_of(
            (retrieval,), datetime(2026, 9, 1, 12, 0, tzinfo=UTC).replace(tzinfo=None)
        )

    schedule = ApprovedAvailabilitySchedule(
        rule_id="SYNTHETIC_TEST_RULE",
        rule_version="1.0.0",
        authoritative_policy_reference="https://example.test/official-policy",
        benchmark_id=retrieval.benchmark_id,
        source_contract_fingerprint=retrieval.source_contract_fingerprint,
        calendar_id="SYNTHETIC_TEST_CALENDAR",
        calendar_version="1.0.0",
        boundaries=((date(2026, 8, 31), "2026-09-01T07:00:00.000000Z"),),
    )
    derived = replace(
        retrieval,
        availability_basis="OFFICIAL_SCHEDULE_DERIVED",
        availability_boundary_utc="2026-09-01T07:00:00.000000Z",
        availability_derivation_rule_id=schedule.rule_id,
        availability_derivation_rule_version=schedule.rule_version,
        availability_policy_reference=schedule.authoritative_policy_reference,
        availability_calendar_id=schedule.calendar_id,
        availability_calendar_version=schedule.calendar_version,
        availability_calendar_fingerprint=schedule.calendar_fingerprint,
    )
    validate_observation_availability(derived, manifest, approved_schedules=(schedule,))
    with pytest.raises(ReferenceRateContractError, match="approved"):
        validate_observation_availability(derived, manifest)

    provider_reported = replace(
        retrieval,
        provider_publication_date=date(2026, 9, 1),
        provider_publication_value="2026-09-01T08:00:00+01:00",
        provider_publication_value_kind="TIMESTAMP",
        provider_publication_source_field="publishedAt",
        availability_basis="PROVIDER_REPORTED",
        availability_boundary_utc="2026-09-01T07:00:00.000000Z",
    )
    validate_observation_availability(provider_reported, manifest)

    provider_date_with_schedule = replace(
        derived,
        provider_publication_date=date(2026, 9, 1),
        provider_publication_value="2026-09-01",
        provider_publication_value_kind="DATE",
        provider_publication_source_field="publicationDate",
    )
    validate_observation_availability(
        provider_date_with_schedule, manifest, approved_schedules=(schedule,)
    )


def test_availability_invalid_combinations_fail_closed() -> None:
    source = _source()
    manifest = _manifest(source)
    base = ReferenceRateObservation(
        provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id="ESTR",
        source_contract_fingerprint=source.fingerprint,
        import_manifest_fingerprint=manifest.fingerprint,
        observation_date=date(2026, 8, 31),
        provider_publication_date=None,
        rate=Decimal("2.1"),
        provider_revision_id=None,
        provider_revision_id_source_field=None,
        provider_revision_indicator=None,
        provider_revision_indicator_source_field=None,
        provider_revision_status="PROVIDER_REVISION_FIELD_NOT_SUPPLIED",
        provider_revision_contract_id=None,
        provider_revision_contract_version=None,
        provider_revision_contract_revision_indicator_value=None,
        provider_revision_contract_authoritative_reference=None,
        provider_revision_contract_fingerprint=None,
        provider_publication_value=None,
        provider_publication_value_kind=None,
        provider_publication_source_field=None,
        availability_basis="RETRIEVAL_BOUND",
        availability_boundary_utc="2026-09-01T12:00:00.000000Z",
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
    with pytest.raises(ReferenceRateContractError, match="value date"):
        replace(
            base,
            observation_date=date(2026, 9, 2),
            availability_boundary_utc="2026-09-01T12:00:00.000000Z",
        )
    with pytest.raises(ReferenceRateContractError, match="exact retrieval"):
        validate_observation_availability(
            replace(base, availability_boundary_utc="2026-09-01T11:59:59.000000Z"),
            manifest,
        )
    with pytest.raises(ReferenceRateContractError, match="follows"):
        validate_observation_availability(
            replace(base, availability_boundary_utc="2026-09-01T12:00:01.000000Z"),
            manifest,
        )
    with pytest.raises(ReferenceRateContractError, match="provider timestamp"):
        replace(base, availability_basis="PROVIDER_REPORTED")
    with pytest.raises(ReferenceRateContractError, match="supplied together"):
        replace(base, provider_publication_value="2026-09-01")
    with pytest.raises(ReferenceRateContractError, match="requires rule"):
        replace(base, availability_basis="OFFICIAL_SCHEDULE_DERIVED")

    schedule = ApprovedAvailabilitySchedule(
        rule_id="SYNTHETIC_TEST_RULE",
        rule_version="1.0.0",
        authoritative_policy_reference="https://example.test/official-policy",
        benchmark_id="ESTR",
        source_contract_fingerprint=source.fingerprint,
        calendar_id="SYNTHETIC_TEST_CALENDAR",
        calendar_version="1.0.0",
        boundaries=((date(2026, 8, 31), "2026-09-01T07:00:00.000000Z"),),
    )
    derived = replace(
        base,
        availability_basis="OFFICIAL_SCHEDULE_DERIVED",
        availability_boundary_utc="2026-09-01T07:00:00.000000Z",
        availability_derivation_rule_id=schedule.rule_id,
        availability_derivation_rule_version=schedule.rule_version,
        availability_policy_reference=schedule.authoritative_policy_reference,
        availability_calendar_id=schedule.calendar_id,
        availability_calendar_version=schedule.calendar_version,
        availability_calendar_fingerprint=schedule.calendar_fingerprint,
    )
    wrong_source = replace(schedule, source_contract_fingerprint="c" * 64)
    with pytest.raises(ReferenceRateContractError, match="approved"):
        validate_observation_availability(
            derived, manifest, approved_schedules=(wrong_source,)
        )


def test_revision_aware_temporal_selection_ignores_current_projection() -> None:
    source = _source()
    first_manifest = _manifest(source)
    base = ReferenceRateObservation(
        provenance_contract_version=REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        benchmark_id="ESTR",
        source_contract_fingerprint=source.fingerprint,
        import_manifest_fingerprint=first_manifest.fingerprint,
        observation_date=date(2026, 8, 31),
        provider_publication_date=None,
        rate=Decimal("2.1"),
        provider_revision_id=None,
        provider_revision_id_source_field=None,
        provider_revision_indicator=None,
        provider_revision_indicator_source_field=None,
        provider_revision_status="PROVIDER_REVISION_FIELD_NOT_SUPPLIED",
        provider_revision_contract_id=None,
        provider_revision_contract_version=None,
        provider_revision_contract_revision_indicator_value=None,
        provider_revision_contract_authoritative_reference=None,
        provider_revision_contract_fingerprint=None,
        provider_publication_value=None,
        provider_publication_value_kind=None,
        provider_publication_source_field=None,
        availability_basis="RETRIEVAL_BOUND",
        availability_boundary_utc="2026-09-01T12:00:00.000000Z",
        availability_derivation_rule_id=None,
        availability_derivation_rule_version=None,
        availability_policy_reference=None,
        availability_calendar_id=None,
        availability_calendar_version=None,
        availability_calendar_fingerprint=None,
        revision_sequence=1,
        supersedes_observation_fingerprint=None,
        is_current=False,
        quality_status="ADMITTED_VALIDATED",
    )
    transition_contract = ProviderRevisionTransitionContract(
        contract_id="SYNTHETIC_PROVIDER_REVISION_RULE",
        contract_version="1.0.0",
        benchmark_id="ESTR",
        source_contract_fingerprint=source.fingerprint,
        revision_indicator_source_field="revisionIndicator",
        revision_indicator_value="R",
        authoritative_reference="https://example.test/revision-policy",
    )
    revised = replace(
        base,
        import_manifest_fingerprint="c" * 64,
        rate=Decimal("2.2"),
        provider_revision_id="provider-revision-2",
        provider_revision_id_source_field="revisionId",
        provider_revision_indicator="R",
        provider_revision_indicator_source_field="revisionIndicator",
        provider_revision_status="PROVIDER_EXPLICIT_REVISION",
        provider_revision_contract_id=transition_contract.contract_id,
        provider_revision_contract_version=transition_contract.contract_version,
        provider_revision_contract_revision_indicator_value=(
            transition_contract.revision_indicator_value
        ),
        provider_revision_contract_authoritative_reference=(
            transition_contract.authoritative_reference
        ),
        provider_revision_contract_fingerprint=transition_contract.fingerprint,
        availability_boundary_utc="2026-09-02T12:00:00.000000Z",
        revision_sequence=2,
        supersedes_observation_fingerprint=base.fingerprint,
        is_current=True,
    )
    assert observations_available_as_of(
        (base, revised), datetime(2026, 9, 2, 11, 59, tzinfo=UTC)
    ) == (base,)
    assert observations_available_as_of(
        (base, revised), datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    ) == (revised,)
    with pytest.raises(ReferenceRateContractError, match="cross-source"):
        observations_available_as_of(
            (
                base,
                replace(
                    base,
                    observation_date=date(2026, 8, 30),
                    source_contract_fingerprint="c" * 64,
                ),
            ),
            datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
        )


def test_internal_identity_and_conflicts_never_become_provider_metadata() -> None:
    manifest = _manifest(_source())
    with pytest.raises(ReferenceRateContractError, match="system-generated"):
        replace(
            manifest,
            provider_dataset_version=manifest.internal_evidence_identity,
            provider_dataset_version_source_field="datasetVersion",
        )
    for false_value in (
        "INITIAL",
        "8f14e45f-ea9b-4f6d-8f0f-d4a2fca0c91b",
        "csvdata",
    ):
        with pytest.raises(ReferenceRateContractError, match="system-generated"):
            replace(
                manifest,
                provider_dataset_version=false_value,
                provider_dataset_version_source_field="datasetVersion",
            )
    with pytest.raises(ReferenceRateContractError, match="lowercase SHA-256"):
        replace(manifest, internal_evidence_identity="")
    with pytest.raises(ReferenceRateContractError, match="differs"):
        replace(manifest, internal_evidence_identity="c" * 64)
    assert classify_evidence_transition(
        previous_internal_evidence_identity="a" * 64,
        incoming_internal_evidence_identity="b" * 64,
        previous_rate=Decimal("1.0"),
        incoming_rate=Decimal("1.1"),
        benchmark_id="ESTR",
        source_contract_fingerprint=_source().fingerprint,
        provider_revision_status="PROVIDER_EMPTY_REVISION_INDICATOR",
        provider_revision_indicator="",
        provider_revision_indicator_source_field="OBS_STATUS",
    ).status == "CONFLICTING_EVIDENCE"
    transition_contract = ProviderRevisionTransitionContract(
        contract_id="SYNTHETIC_PROVIDER_REVISION_RULE",
        contract_version="1.0.0",
        benchmark_id="ESTR",
        source_contract_fingerprint=_source().fingerprint,
        revision_indicator_source_field="OBS_STATUS",
        revision_indicator_value="R",
        authoritative_reference="https://example.test/revision-policy",
    )
    assert classify_evidence_transition(
        previous_internal_evidence_identity="a" * 64,
        incoming_internal_evidence_identity="b" * 64,
        previous_rate=Decimal("1.0"),
        incoming_rate=Decimal("1.1"),
        benchmark_id="ESTR",
        source_contract_fingerprint=_source().fingerprint,
        provider_revision_status="PROVIDER_EXPLICIT_REVISION",
        provider_revision_indicator="R",
        provider_revision_indicator_source_field="OBS_STATUS",
        provider_revision_contract=transition_contract,
    ).status == "AUTHORIZED_PROVIDER_REVISION"
    assert classify_evidence_transition(
        previous_internal_evidence_identity="a" * 64,
        incoming_internal_evidence_identity="a" * 64,
        previous_rate=Decimal("1.0"),
        incoming_rate=Decimal("1.0"),
        benchmark_id="ESTR",
        source_contract_fingerprint=_source().fingerprint,
        provider_revision_status="PROVIDER_EXPLICIT_NO_REVISION",
        provider_revision_indicator="A",
        provider_revision_indicator_source_field="OBS_STATUS",
    ).status == "IDENTICAL_REPLAY"
    assert classify_evidence_transition(
        previous_internal_evidence_identity="a" * 64,
        incoming_internal_evidence_identity="b" * 64,
        previous_rate=Decimal("1.0"),
        incoming_rate=Decimal("1.0"),
        benchmark_id="ESTR",
        source_contract_fingerprint=_source().fingerprint,
        provider_revision_status="PROVIDER_EXPLICIT_NO_REVISION",
        provider_revision_indicator="A",
        provider_revision_indicator_source_field="OBS_STATUS",
    ).status == "INTERNAL_EVIDENCE_SNAPSHOT_CHANGED"
    assert classify_evidence_transition(
        previous_internal_evidence_identity="a" * 64,
        incoming_internal_evidence_identity="a" * 64,
        previous_rate=Decimal("1.0"),
        incoming_rate=Decimal("1.1"),
        benchmark_id="ESTR",
        source_contract_fingerprint=_source().fingerprint,
        provider_revision_status="PROVIDER_EXPLICIT_REVISION",
        provider_revision_indicator="R",
        provider_revision_indicator_source_field="OBS_STATUS",
        provider_revision_contract=transition_contract,
    ).status == "AUTHORIZED_PROVIDER_REVISION"
    assert classify_evidence_transition(
        previous_internal_evidence_identity="a" * 64,
        incoming_internal_evidence_identity="b" * 64,
        previous_rate=Decimal("1.0"),
        incoming_rate=Decimal("1.1"),
        benchmark_id="ESTR",
        source_contract_fingerprint=_source().fingerprint,
        provider_revision_status="PROVIDER_EXPLICIT_REVISION",
        provider_revision_indicator="A",
        provider_revision_indicator_source_field="OBS_STATUS",
        provider_revision_contract=transition_contract,
    ).status == "CONFLICTING_EVIDENCE"
    assert canonical_utc_timestamp("2026-09-01T08:00:00+02:00") == (
        "2026-09-01T06:00:00.000000Z"
    )


def test_offline_validation_registry_is_strict_source_bound_and_deterministic(
    tmp_path: Path,
) -> None:
    source = _source()
    path = tmp_path / "reference-rate-validation-registry.json"
    payload = {
        "approved_availability_schedules": [
            {
                "authoritative_policy_reference": "https://example.test/availability-policy",
                "benchmark_id": "ESTR",
                "boundaries": [
                    {
                        "availability_boundary_utc": "2026-09-01T07:00:00.000000Z",
                        "observation_date": "2026-08-31",
                    }
                ],
                "calendar_id": "SYNTHETIC_TEST_CALENDAR",
                "calendar_version": "1.0.0",
                "rule_id": "SYNTHETIC_TEST_RULE",
                "rule_version": "1.0.0",
                "source_contract_fingerprint": source.fingerprint,
            }
        ],
        "approved_provider_revision_contracts": [
            {
                "authoritative_reference": "https://example.test/revision-policy",
                "benchmark_id": "ESTR",
                "contract_id": "SYNTHETIC_PROVIDER_REVISION_RULE",
                "contract_version": "1.0.0",
                "revision_indicator_source_field": "OBS_STATUS",
                "revision_indicator_value": "R",
                "source_contract_fingerprint": source.fingerprint,
            }
        ],
        "registry_schema_version": 1,
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    first = load_reference_rate_validation_registry(path)
    second = load_reference_rate_validation_registry(path)
    assert first == second
    assert first.approved_schedules[0].benchmark_id == "ESTR"
    assert first.approved_revision_contracts[0].source_contract_fingerprint == (
        source.fingerprint
    )
    link = tmp_path / "registry-link.json"
    link.symlink_to(path)
    with pytest.raises(ReferenceRateProvenanceValidationError, match="symlink"):
        load_reference_rate_validation_registry(link)
