"""Read-only adapter from admitted Phase E NAV rows to the Phase F2 boundary."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.history.nav_provenance import (
    NavProvenanceError,
    validate_phase_e_nav,
)
from portfolio_advisor.metrics.governed import (
    GovernedMetricSeries,
    GovernedObservation,
    MetricSuitabilityState,
    ObservationFingerprintScheme,
    ObservationSemantics,
    PhaseF2ExecutionMode,
    SourceApprovalState,
    bind_series_provenance,
)

ERSTE_MARKET_GOVERNANCE = "APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE"


class PhaseEReadError(ValueError):
    """Installed Phase E evidence cannot satisfy the read-only adapter contract."""


def load_admitted_phase_e_nav_series(
    database_path: Path,
    *,
    exact_isin: str,
    repository_root: Path,
    phase_e_index_path: Path,
) -> GovernedMetricSeries:
    """Load one current exact-ISIN NAV series without promoting it to portfolio wealth.

    Phase E NAV is admitted evidence, but its price-only semantics and unknown
    distribution treatment are deliberately retained.  Consequently the F2
    engine rejects it for portfolio total-return metrics rather than treating
    it as a synthetic portfolio NAV.
    """
    if len(exact_isin) != 12 or not exact_isin.isalnum() or exact_isin != exact_isin.upper():
        raise PhaseEReadError("exact ISIN must be canonical 12-character uppercase text")
    try:
        phase_e_validation = validate_phase_e_nav(
            repository_root=repository_root,
            target=database_path,
            index_path=phase_e_index_path,
            legacy_source=database_path,
        )
    except (NavProvenanceError, OSError, ValueError) as error:
        raise PhaseEReadError("Phase E validation failed before read-only adaptation") from error
    validation_fingerprint = canonical_fingerprint(phase_e_validation)
    database_uri = f"file:{database_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                """
                SELECT
                    n.nav_observation_version_id,
                    n.observation_date,
                    n.nav_decimal,
                    n.currency_code,
                    n.quality_status,
                    n.observation_fingerprint,
                    n.raw_artifact_sha256,
                    m.import_status,
                    m.manifest_fingerprint,
                    m.dataset_fingerprint,
                    m.series_retrieval_timestamp,
                    m.admitted_observation_count,
                    m.exact_isin,
                    s.source_code,
                    s.source_governance,
                    s.source_fingerprint
                FROM nav_observation_version AS n
                JOIN nav_import_manifest AS m
                  ON m.nav_import_manifest_id = n.nav_import_manifest_id
                 AND m.instrument_id = n.instrument_id
                 AND m.exact_isin = n.exact_isin
                JOIN nav_evidence_source AS s
                  ON s.nav_evidence_source_id = m.nav_evidence_source_id
                WHERE n.exact_isin = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM nav_observation_version AS successor
                      WHERE successor.supersedes_observation_id = n.nav_observation_version_id
                  )
                ORDER BY n.observation_date, n.nav_observation_version_id
                """,
                (exact_isin,),
            ).fetchall()
    except sqlite3.Error as error:
        raise PhaseEReadError("could not read installed Phase E evidence") from error

    if not rows:
        raise PhaseEReadError("exact ISIN has no admitted Phase E observations")
    if any(
        row["quality_status"] != "ADMITTED_VALIDATED"
        or row["import_status"] != "VALIDATED_ADMITTED"
        for row in rows
    ):
        raise PhaseEReadError("Phase E rows are not admitted and validated")
    if {str(row["exact_isin"]) for row in rows} != {exact_isin}:
        raise PhaseEReadError("Phase E exact-ISIN lineage is inconsistent")
    currencies = {str(row["currency_code"]) for row in rows}
    manifests = {str(row["manifest_fingerprint"]) for row in rows}
    sources = {str(row["source_code"]) for row in rows}
    source_governance = {str(row["source_governance"]) for row in rows}
    source_fingerprints = {str(row["source_fingerprint"]) for row in rows}
    datasets = {str(row["dataset_fingerprint"]) for row in rows}
    admitted_counts = {int(row["admitted_observation_count"]) for row in rows}
    retrieval_timestamps = {str(row["series_retrieval_timestamp"]) for row in rows}
    lineage_sets = (
        currencies,
        manifests,
        sources,
        source_governance,
        source_fingerprints,
        datasets,
        admitted_counts,
        retrieval_timestamps,
    )
    if any(len(values) != 1 for values in lineage_sets):
        raise PhaseEReadError("Phase E manifest/source lineage is ambiguous")
    if source_governance != {ERSTE_MARKET_GOVERNANCE}:
        raise PhaseEReadError("Phase E source governance differs from the approved contract")
    if admitted_counts != {len(rows)}:
        raise PhaseEReadError("Phase E manifest observation count mismatch")

    observations: list[GovernedObservation] = []
    try:
        for row in rows:
            observations.append(
                GovernedObservation(
                    observation_date=str(row["observation_date"]),
                    value=Decimal(str(row["nav_decimal"])),
                    evidence_reference=(
                        "nav_observation_version:"
                        f"{int(row['nav_observation_version_id'])}:"
                        f"{row['raw_artifact_sha256']!s}"
                    ),
                    observation_fingerprint=str(row["observation_fingerprint"]),
                )
            )
    except (InvalidOperation, ValueError) as error:
        raise PhaseEReadError("Phase E NAV Decimal text is malformed") from error

    currency = next(iter(currencies))
    manifest = next(iter(manifests))
    dataset = next(iter(datasets))
    source = next(iter(sources))
    source_fingerprint = next(iter(source_fingerprints))
    retrieval_timestamp = next(iter(retrieval_timestamps))
    observation_references = tuple(item.evidence_reference for item in observations)
    series = GovernedMetricSeries(
        series_identity=f"PHASE_E_EXACT_ISIN_NAV:{exact_isin}:{dataset}",
        subject_identity=exact_isin,
        source_identity=source,
        source_governance=ERSTE_MARKET_GOVERNANCE,
        currency_code=currency,
        observation_semantics=ObservationSemantics.INSTRUMENT_NAV_PRICE_ONLY,
        source_approval_state=SourceApprovalState.ADMITTED_VALIDATED,
        metric_suitability_state=MetricSuitabilityState.UNKNOWN_DISTRIBUTION_STATUS,
        observation_fingerprint_scheme=(
            ObservationFingerprintScheme.PHASE_E_NAV_OBSERVATION_VERSION_V1
        ),
        execution_mode=PhaseF2ExecutionMode.ADMITTED_EVIDENCE,
        observations=tuple(observations),
        evidence_references=(
            f"nav_evidence_source:{source_fingerprint}",
            f"nav_import_manifest:{manifest}",
            f"nav_dataset:{dataset}",
            f"phase_e_validation:{validation_fingerprint}",
            *observation_references,
        ),
        decision_as_of_utc="2026-09-04T12:24:23.000000Z",
        evidence_available_at_utc=retrieval_timestamp,
        nav_evidence_cutoff="2026-08-31",
        alignment_method="SINGLE_INSTRUMENT_OBSERVED_DATES",
        window_selection_method="NOT_APPLICABLE_NOT_COMMON_PORTFOLIO_WINDOW",
        endpoint_method="OBSERVED_ENDPOINTS_EXACT_ELAPSED_DAYS",
        portfolio_dynamics="NOT_APPLICABLE_NOT_PORTFOLIO_WEALTH",
        cash_return_treatment="NOT_APPLICABLE_NOT_PORTFOLIO_WEALTH",
    )
    return bind_series_provenance(series)
