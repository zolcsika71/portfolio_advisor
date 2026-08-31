"""Governed capital-conservation shortlist construction."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from pathlib import Path

from portfolio_advisor.database.repository import HoldingObservation
from portfolio_advisor.DB_creation.excel_processing import VALUE_TRANSLATIONS
from portfolio_advisor.DB_creation.text_normalization import normalized_key
from portfolio_advisor.metrics.portfolio import calculate_portfolio_metrics
from portfolio_advisor.objectives import (
    CAPITAL_POLICY_ID,
    CAPITAL_POLICY_VERSION,
    InvestmentPolicy,
    PolicyCapabilityStatus,
    PolicyRegistry,
    PortfolioObjective,
    build_default_policy_registry,
)
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.models import CandidateEvaluation
from portfolio_advisor.ranking.ranking import rank_portfolios

from .models import (
    CapitalConservationShortlist,
    ConstructionProvenance,
    RankedInstrument,
    SourceLineage,
)
from .repository import (
    MembershipEvidence,
    SchemaV3ShortlistRepository,
    ShortlistEvidenceError,
)

CAPITAL_DEFENSIVE = "CAPITAL_DEFENSIVE"


class ShortlistConstructionError(RuntimeError):
    """Raised when a governed construction precondition is not proven."""


def construct_capital_conservation_shortlist(
    *,
    database_path: Path,
    repository_root: Path,
    expected_workbook_fingerprints: Mapping[str, str],
    expected_manifest_fingerprint: str,
    objective: PortfolioObjective | str = PortfolioObjective.CAPITAL_CONSERVATION,
    as_of: date | None = None,
    limit: int | None = None,
    registry: PolicyRegistry | None = None,
) -> CapitalConservationShortlist:
    """Return a deterministic read-only ranked instrument universe."""
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ShortlistConstructionError("limit must be a positive integer")
    governed_registry = registry or build_default_policy_registry(repository_root)
    try:
        parsed_objective = (
            objective if isinstance(objective, PortfolioObjective) else PortfolioObjective.parse(objective)
        )
        policy = governed_registry.resolve_active_policy(parsed_objective)
        _validate_policy(policy, repository_root)
    except Exception as error:
        if isinstance(error, ShortlistConstructionError):
            raise
        raise ShortlistConstructionError(f"objective or policy resolution failed: {error}") from error
    if parsed_objective is not PortfolioObjective.CAPITAL_CONSERVATION:
        raise ShortlistConstructionError("only capital_conservation construction is governed")

    evidence_repository = SchemaV3ShortlistRepository(database_path)
    try:
        snapshot = evidence_repository.select_snapshot(
            as_of=as_of,
            expected_workbook_fingerprints=expected_workbook_fingerprints,
            expected_manifest_fingerprint=expected_manifest_fingerprint,
        )
        evidence = evidence_repository.load_memberships(snapshot)
    except ShortlistEvidenceError as error:
        raise ShortlistConstructionError(str(error)) from error

    rules = load_ranking_rules(repository_root / policy.artifact_reference)
    metrics = [calculate_portfolio_metrics([_holding(item)]) for item in evidence]
    evaluations, warnings = rank_portfolios(metrics, rules)
    evidence_by_isin = {item.isin: item for item in evidence}
    if len(evidence_by_isin) != len(evidence):
        raise ShortlistConstructionError("duplicate output instrument identity")
    candidates = tuple(_ranked(item, evidence_by_isin[item.metrics.portfolio_name]) for item in evaluations)
    eligible = tuple(item for item in candidates if item.eligible)
    if not eligible:
        raise ShortlistConstructionError("eligible instrument universe is empty")
    if limit is not None and limit > len(eligible):
        raise ShortlistConstructionError("limit exceeds eligible instrument count")
    constructed = eligible if limit is None else eligible[:limit]
    if [item.rank for item in eligible] != list(range(1, len(eligible) + 1)):
        raise ShortlistConstructionError("ranking is internally inconsistent")
    provenance = ConstructionProvenance(
        objective=parsed_objective.value,
        strategy=CAPITAL_DEFENSIVE,
        construction_capability=policy.capabilities.construction.value,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_fingerprint=policy.fingerprint,
        registry_fingerprint=governed_registry.registry_fingerprint(),
        capability_states=tuple(sorted(policy.capabilities.to_dict().items())),
        snapshot_id=snapshot.snapshot_id,
        snapshot_date=snapshot.snapshot_date,
        source_file=snapshot.source_file,
        source_file_sha256=snapshot.source_file_sha256,
        source_sheet_id=snapshot.source_sheet_id,
        source_sheet_name=snapshot.source_sheet_name,
        shortlist_manifest_fingerprint=snapshot.manifest_fingerprint,
        shortlist_integration_version=snapshot.integration_version,
    )
    result = CapitalConservationShortlist(
        provenance=provenance,
        candidates=candidates,
        constructed=constructed,
        ranking_warnings=warnings,
    )
    return result


def _validate_policy(policy: InvestmentPolicy, repository_root: Path) -> None:
    if policy.policy_id != CAPITAL_POLICY_ID or policy.version != CAPITAL_POLICY_VERSION:
        raise ShortlistConstructionError("capital policy identity or version mismatch")
    if policy.capabilities.construction is not PolicyCapabilityStatus.AVAILABLE_REVIEWED:
        raise ShortlistConstructionError("capital policy construction capability is unavailable")
    artifact = repository_root / policy.artifact_reference
    try:
        fingerprint = sha256(artifact.read_bytes()).hexdigest()
    except OSError as error:
        raise ShortlistConstructionError("capital policy artifact is unavailable") from error
    if fingerprint != policy.fingerprint:
        raise ShortlistConstructionError("capital policy fingerprint mismatch")
    rules = load_ranking_rules(artifact)
    if rules.policy_name != policy.policy_id or rules.version != policy.version:
        raise ShortlistConstructionError("policy artifact identity mismatch")


def _holding(item: MembershipEvidence) -> HoldingObservation:
    metrics = dict(item.metrics)
    return HoldingObservation(
        portfolio_name=item.isin,
        product=item.canonical_name,
        isin=item.isin,
        allocation=100.0,
        asset_class=None,
        currency=item.currency,
        currency_risk=_currency_risk(item.currency_risk),
        return_1y=metrics.get("RETURN_1Y"),
        sharpe_ratio_1y=metrics.get("SHARPE_RATIO_1Y"),
        volatility_1y=metrics.get("VOLATILITY_1Y"),
        downside_risk=metrics.get("DOWNSIDE_RISK"),
        maximum_drawdown=metrics.get("MAXIMUM_DRAWDOWN"),
    )


def _currency_risk(value: str | None) -> str | None:
    if value is None:
        return None
    translations = VALUE_TRANSLATIONS["Currency Risk"]
    key = normalized_key(value)
    english = {normalized_key(translated): translated for translated in translations.values()}
    if key in translations:
        return translations[key]
    if key in english:
        return english[key]
    raise ShortlistConstructionError(f"unsupported currency-risk evidence: {value!r}")


def _ranked(evaluation: CandidateEvaluation, evidence: MembershipEvidence) -> RankedInstrument:
    values = (
        ("annualized_volatility", _value(evaluation, "annualized_volatility")),
        ("maximum_drawdown", _value(evaluation, "maximum_drawdown")),
        ("return_1y", _value(evaluation, "return_1y")),
        ("sharpe_ratio", _value(evaluation, "sharpe_ratio")),
        ("unhedged_allocation", _value(evaluation, "unhedged_allocation")),
    )
    contributions = tuple(
        (item.metric, item.raw_value, item.normalized_value, item.contribution)
        for item in evaluation.contributions
    )
    return RankedInstrument(
        instrument_id=evidence.instrument_id,
        isin=evidence.isin,
        canonical_name=evidence.canonical_name,
        eligible=evaluation.eligible,
        rejection_reasons=evaluation.rejection_reasons,
        rank=evaluation.rank,
        total_score=evaluation.total_score,
        feature_values=values,
        weighted_contributions=contributions,
        lineage=SourceLineage(
            shortlist_entry_id=evidence.shortlist_entry_id,
            source_occurrence_ids=evidence.occurrence_ids,
            source_row_numbers=evidence.source_rows,
        ),
    )


def _value(evaluation: CandidateEvaluation, field: str) -> float | None:
    value = getattr(evaluation.metrics, field)
    return value.value if value.available else None
