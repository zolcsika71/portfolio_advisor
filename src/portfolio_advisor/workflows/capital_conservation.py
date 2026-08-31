"""Governed capital-conservation model-versus-shortlist reference workflow."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path

from portfolio_advisor.construction import (
    CapitalConservationShortlist,
    construct_capital_conservation_shortlist,
)
from portfolio_advisor.objectives import (
    CAPITAL_POLICY_ARTIFACT,
    PolicyCapabilityStatus,
    PolicyRegistry,
    PortfolioObjective,
    build_default_policy_registry,
)
from portfolio_advisor.ranking.models import CandidateEvaluation

from .comparison import build_finalist_comparison_policy, compare_finalists
from .models import (
    CapitalConservationReferenceWorkflow,
    CapitalConservationUserChoice,
    FinalistKind,
    FinalistProvenance,
    UserChoiceOption,
    UserChoiceState,
    WorkflowFinalist,
)
from .repository import (
    ModelRankingEvidence,
    ReferenceEvidenceError,
    SchemaV3ReferenceRepository,
)

WORKFLOW_ID = "CAPITAL_CONSERVATION_REFERENCE_WORKFLOW"
WORKFLOW_VERSION = "1.0.0"
VALID_USER_CHOICES = (
    UserChoiceOption.SELECT_MODEL_PORTFOLIO,
    UserChoiceOption.SELECT_SHORTLIST_CANDIDATE,
    UserChoiceOption.DEFER,
    UserChoiceOption.DECLINE,
)


class CapitalConservationWorkflowError(RuntimeError):
    """Raised when a governed workflow or choice precondition is not proven."""


def build_capital_conservation_reference_workflow(
    *,
    database_path: Path,
    repository_root: Path,
    expected_workbook_fingerprints: Mapping[str, str],
    expected_shortlist_manifest_fingerprint: str,
    objective: PortfolioObjective | str = PortfolioObjective.CAPITAL_CONSERVATION,
    as_of: date | None = None,
    registry: PolicyRegistry | None = None,
) -> CapitalConservationReferenceWorkflow:
    """Build the deterministic read-only finalist comparison and stop before choice."""
    governed_registry = registry or build_default_policy_registry(repository_root)
    try:
        parsed = objective if isinstance(objective, PortfolioObjective) else PortfolioObjective.parse(objective)
        policy = governed_registry.resolve_active_policy(parsed)
    except Exception as error:
        raise CapitalConservationWorkflowError(f"objective or policy resolution failed: {error}") from error
    if parsed is not PortfolioObjective.CAPITAL_CONSERVATION:
        raise CapitalConservationWorkflowError("only capital_conservation workflow is governed")
    if policy.capabilities.construction is not PolicyCapabilityStatus.AVAILABLE_REVIEWED:
        raise CapitalConservationWorkflowError("capital construction capability is unavailable")
    if policy.capabilities.finalist_comparison is not PolicyCapabilityStatus.AVAILABLE_REVIEWED:
        raise CapitalConservationWorkflowError("capital finalist-comparison capability is unavailable")
    expected_registry = build_default_policy_registry(repository_root)
    if governed_registry.registry_fingerprint() != expected_registry.registry_fingerprint():
        raise CapitalConservationWorkflowError("stale or mismatched objective-policy registry")

    repository = SchemaV3ReferenceRepository(
        database_path,
        repository_root / CAPITAL_POLICY_ARTIFACT,
    )
    try:
        common_date = _common_date(repository, as_of)
        model_ranking = repository.rank_models(common_date)
    except (ReferenceEvidenceError, OSError) as error:
        raise CapitalConservationWorkflowError(str(error)) from error
    model_winners = tuple(item for item in model_ranking if item.evaluation.rank == 1)
    if len(model_winners) != 1:
        raise CapitalConservationWorkflowError("model rank-one finalist is missing or ambiguous")
    if not model_winners[0].evaluation.eligible:
        raise CapitalConservationWorkflowError("model rank-one finalist is ineligible")

    try:
        shortlist = construct_capital_conservation_shortlist(
            database_path=database_path,
            repository_root=repository_root,
            expected_workbook_fingerprints=expected_workbook_fingerprints,
            expected_manifest_fingerprint=expected_shortlist_manifest_fingerprint,
            objective=parsed,
            as_of=common_date,
            registry=governed_registry,
        )
    except Exception as error:
        raise CapitalConservationWorkflowError(f"shortlist construction failed: {error}") from error
    if shortlist.provenance.snapshot_date != common_date:
        raise CapitalConservationWorkflowError("model and shortlist snapshot dates do not match")
    shortlist_winners = tuple(item for item in shortlist.constructed if item.rank == 1)
    if len(shortlist_winners) != 1:
        raise CapitalConservationWorkflowError("shortlist rank-one finalist is missing or ambiguous")
    if not shortlist_winners[0].eligible:
        raise CapitalConservationWorkflowError("shortlist rank-one finalist is ineligible")

    model_finalist = _model_finalist(model_winners[0])
    shortlist_finalist = _shortlist_finalist(shortlist)
    comparison_policy = build_finalist_comparison_policy()
    recommendation = compare_finalists(model_finalist, shortlist_finalist, comparison_policy)
    result = CapitalConservationReferenceWorkflow(
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        objective=parsed.value,
        common_as_of_date=common_date,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_fingerprint=policy.fingerprint,
        registry_fingerprint=governed_registry.registry_fingerprint(),
        capability_states=tuple(sorted(policy.capabilities.to_dict().items())),
        model_finalist=model_finalist,
        shortlist_finalist=shortlist_finalist,
        recommendation=recommendation,
        user_choice_state=UserChoiceState.AWAITING_USER_CHOICE,
        valid_choice_options=VALID_USER_CHOICES,
    )
    if result.user_choice_state is not UserChoiceState.AWAITING_USER_CHOICE:
        raise CapitalConservationWorkflowError("workflow implicitly created a user choice")
    return result


def record_capital_conservation_user_choice(
    workflow: CapitalConservationReferenceWorkflow,
    choice: UserChoiceOption | str,
    *,
    expected_workflow_fingerprint: str,
    expected_recommendation_fingerprint: str,
) -> CapitalConservationUserChoice:
    """Validate an explicit choice and return a non-persistent immutable record."""
    if workflow.workflow_fingerprint != expected_workflow_fingerprint:
        raise CapitalConservationWorkflowError("stale or mismatched workflow fingerprint")
    if workflow.recommendation.fingerprint != expected_recommendation_fingerprint:
        raise CapitalConservationWorkflowError("stale or mismatched recommendation fingerprint")
    if workflow.user_choice_state is not UserChoiceState.AWAITING_USER_CHOICE:
        raise CapitalConservationWorkflowError("workflow is not awaiting a user choice")
    try:
        parsed = choice if isinstance(choice, UserChoiceOption) else UserChoiceOption(choice)
    except ValueError as error:
        raise CapitalConservationWorkflowError(f"invalid user choice: {choice!r}") from error
    if parsed not in workflow.valid_choice_options:
        raise CapitalConservationWorkflowError("choice is not valid for this workflow")
    selected = {
        UserChoiceOption.SELECT_MODEL_PORTFOLIO: workflow.model_finalist.stable_id,
        UserChoiceOption.SELECT_SHORTLIST_CANDIDATE: workflow.shortlist_finalist.stable_id,
        UserChoiceOption.DEFER: None,
        UserChoiceOption.DECLINE: None,
    }[parsed]
    recommendation = workflow.recommendation.recommended_finalist_id
    disagrees = selected is not None and recommendation is not None and selected != recommendation
    return CapitalConservationUserChoice(
        workflow_fingerprint=workflow.workflow_fingerprint,
        recommendation_fingerprint=workflow.recommendation.fingerprint,
        choice=parsed,
        selected_finalist_id=selected,
        disagrees_with_recommendation=disagrees,
    )


def _common_date(repository: SchemaV3ReferenceRepository, as_of: date | None) -> date:
    model_dates = set(repository.model_dates())
    shortlist_dates = set(repository.shortlist_dates())
    common = sorted(model_dates & shortlist_dates)
    if as_of is not None:
        common = [value for value in common if value <= as_of]
    if not common:
        raise CapitalConservationWorkflowError("no common complete model and shortlist date exists")
    return common[-1]


def _model_finalist(item: ModelRankingEvidence) -> WorkflowFinalist:
    evaluation = item.evaluation
    if evaluation.rank is None or evaluation.total_score is None:
        raise CapitalConservationWorkflowError("model finalist has no governed rank or score")
    return WorkflowFinalist(
        kind=FinalistKind.MODEL_PORTFOLIO,
        stable_id=f"PORTFOLIO:{item.portfolio_id}",
        database_local_id=item.portfolio_id,
        display_name=evaluation.metrics.portfolio_name,
        rank=evaluation.rank,
        eligible=evaluation.eligible,
        total_score=evaluation.total_score,
        feature_values=_feature_values(evaluation),
        provenance=FinalistProvenance(
            snapshot_id=item.snapshot_id,
            snapshot_date=item.snapshot_date,
            source_file=item.source_file,
            source_file_sha256=item.source_file_sha256,
            source_sheet_id=item.source_sheet_id,
            source_sheet_name=item.source_sheet_name,
            evidence_ids=item.occurrence_ids,
            source_row_numbers=item.source_rows,
            source_dataset_fingerprint=item.dataset_fingerprint,
        ),
    )


def _shortlist_finalist(shortlist: CapitalConservationShortlist) -> WorkflowFinalist:
    item = next(candidate for candidate in shortlist.constructed if candidate.rank == 1)
    if item.rank is None or item.total_score is None:
        raise CapitalConservationWorkflowError("shortlist finalist has no governed rank or score")
    provenance = shortlist.provenance
    return WorkflowFinalist(
        kind=FinalistKind.SHORTLIST_INSTRUMENT,
        stable_id=item.isin,
        database_local_id=item.instrument_id,
        display_name=item.canonical_name,
        rank=item.rank,
        eligible=item.eligible,
        total_score=item.total_score,
        feature_values=item.feature_values,
        provenance=FinalistProvenance(
            snapshot_id=provenance.snapshot_id,
            snapshot_date=provenance.snapshot_date,
            source_file=provenance.source_file,
            source_file_sha256=provenance.source_file_sha256,
            source_sheet_id=provenance.source_sheet_id,
            source_sheet_name=provenance.source_sheet_name,
            evidence_ids=item.lineage.source_occurrence_ids,
            source_row_numbers=item.lineage.source_row_numbers,
            source_dataset_fingerprint=provenance.shortlist_manifest_fingerprint,
        ),
    )


def _feature_values(evaluation: CandidateEvaluation) -> tuple[tuple[str, float | None], ...]:
    return tuple(
        (name, metric.value if metric.available else None)
        for name, metric in (
            ("annualized_volatility", evaluation.metrics.annualized_volatility),
            ("maximum_drawdown", evaluation.metrics.maximum_drawdown),
            ("return_1y", evaluation.metrics.return_1y),
            ("sharpe_ratio", evaluation.metrics.sharpe_ratio),
            ("unhedged_allocation", evaluation.metrics.unhedged_allocation),
        )
    )
