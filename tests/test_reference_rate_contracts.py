"""Exact, immutable reference-rate evidence contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    load_capital_defensive_construction_policy,
)
from portfolio_advisor.reference_rates import (
    REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
    ReferenceRateContractError,
    ReferenceRateDefinition,
    ReferenceRateImportManifest,
    ReferenceRateObservation,
    ReferenceRateSource,
    validate_policy_binding,
)
from portfolio_advisor.reference_rates.contracts import canonical_request_parameters

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
    return ReferenceRateImportManifest(
        source_contract_fingerprint=source.fingerprint,
        retrieval_timestamp="2026-09-01T12:00:00+00:00",
        request_url=source.machine_readable_url,
        request_parameters=canonical_request_parameters(
            {"endPeriod": "2026-08-31", "format": "csvdata"}
        ),
        response_content_type="text/csv",
        http_status=200,
        raw_artifact_reference="data/raw/reference_rates/ecb/estr-2026-09-01.csv",
        raw_artifact_sha256="a" * 64,
        provider_dataset_version="retrieved-2026-09-01T12:00:00Z",
        import_status="VALIDATED_ADMITTED",
        dataset_fingerprint="b" * 64,
    )


def test_definition_is_strict_versioned_and_canonically_fingerprinted() -> None:
    definition = _definition()
    assert definition.fingerprint == _definition().fingerprint
    assert len(definition.fingerprint) == 64
    assert definition.canonical_payload()["series_identifier"] == "EST.B.EU000A2X2A25.WT"
    with pytest.raises(ReferenceRateContractError, match="schema version"):
        replace(definition, contract_schema_version=2)
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
        benchmark_id="ESTR",
        source_contract_fingerprint=source.fingerprint,
        import_manifest_fingerprint=manifest.fingerprint,
        observation_date=date(2026, 8, 31),
        publication_date=date(2026, 9, 1),
        rate=Decimal("2.1880"),
        provider_revision_id="STANDARD-2026-09-01T08:00:00CET",
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
        replace(observation, publication_date=date(2026, 8, 30))
    with pytest.raises(ReferenceRateContractError, match="predecessor"):
        replace(observation, revision_sequence=2)
    with pytest.raises(ReferenceRateContractError, match="quality"):
        replace(observation, quality_status="PENDING")
