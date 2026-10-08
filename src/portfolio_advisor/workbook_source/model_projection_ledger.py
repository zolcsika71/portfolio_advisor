"""Same-candidate projection evidence, never a reader or admission decision.

Supplied projections are replayed through both existing public APIs. Captured
serialization binds the evaluated identities, not subsequent runtime state or
freshly inspected workbook bytes. There is no policy/registry override.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.workbook_source import biff_xls as source
from portfolio_advisor.workbook_source import model_currency_risk_projection as risk
from portfolio_advisor.workbook_source import model_metric_projection as metrics
from portfolio_advisor.workbook_source import normalization

CONTRACT_NAME: Final = "MODEL_PROJECTION_COMPOSITION_LEDGER"
CONTRACT_VERSION: Final = 1
_PENDING: Final = (
    "MODEL_ENGLISH_CLASSIFICATION_AUTHORITY",
    "DESCRIPTIVE_ALLOCATION_COMPATIBILITY",
    "FINAL_READER_HOLDING_ORDER",
)


class ModelProjectionLedgerError(ValueError):
    """Unsupported, inconsistent or incomplete composition evidence."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True, slots=True)
class ComposedModelOccurrence:
    """One source occurrence, including unresolved fields and both resolutions."""

    original: normalization.NormalizedRowCandidate
    metrics: metrics.ProjectedModelRow
    currency_risk: risk.ProjectedModelCurrencyRiskRow

    def to_dict(self) -> dict[str, object]:
        return {
            "original": self.original.to_dict(),
            "metrics": self.metrics.to_dict(),
            "currency_risk": self.currency_risk.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ModelProjectionLedger:
    """Deeply immutable evidence in source order, not final reader order.

    The canonical payload is captured after validation; dictionaries returned by
    serialization are fresh copies, including evaluated mapping/registry identities.
    Neither scoped resolution erases the candidate's historical diagnostics.
    """

    original_candidate: normalization.BiffXlsNormalizationCandidate
    occurrences: tuple[ComposedModelOccurrence, ...]
    _evaluated_payload_json: str

    @property
    def admission_approval(self) -> str:
        return "NOT_GRANTED"

    @property
    def ledger_fingerprint(self) -> str:
        return canonical_fingerprint(json.loads(self._evaluated_payload_json))

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = json.loads(self._evaluated_payload_json)
        return {
            **payload,
            "admission_approval": self.admission_approval,
            "ledger_fingerprint": self.ledger_fingerprint,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ModelProjectionLedgerError(code, message)


def _versions() -> None:
    _require(
        (
            normalization.CONTRACT_NAME,
            normalization.CONTRACT_VERSION,
            normalization.POLICY_NAME,
            normalization.POLICY_VERSION,
        )
        == (
            "BIFF_XLS_NORMALIZATION_CANDIDATE_V1",
            1,
            "BIFF_XLS_NORMALIZATION_ADMISSION_POLICY",
            1,
        )
        and (
            metrics.CONTRACT_NAME,
            metrics.CONTRACT_VERSION,
            metrics.POLICY_NAME,
            metrics.POLICY_APPROVAL_DATE,
        )
        == (
            "MODEL_METRIC_PROJECTION",
            1,
            "MODEL_METRIC_ZERO_HANDLING_POLICY_V1",
            "2026-09-26",
        )
        and (
            risk.CONTRACT_NAME,
            risk.CONTRACT_VERSION,
            risk.POLICY_APPROVAL_DATE,
            risk.MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1,
            risk.MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1,
            risk.TYPED_MANIFEST_SHA256,
        )
        == (
            "MODEL_CURRENCY_RISK_PROJECTION",
            1,
            "2026-10-07",
            "MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1",
            "MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1",
            "c4d678e0c90abac49337bf1f7547816b45134b6e6e7886ab4aacf1ee594f123a",
        ),
        "UNSUPPORTED_VERSION",
        "composition requires the published v1 contracts and approved identities",
    )


def _binding(
    projection: metrics.ModelMetricProjection | risk.ModelCurrencyRiskProjection,
) -> dict[str, object]:
    """Capture every evaluated identity without duplicating rows or candidates."""
    return {
        key: value
        for key, value in projection.to_dict().items()
        if key not in ("original_candidate", "rows")
    }


def _model_classifications(
    candidate: normalization.BiffXlsNormalizationCandidate,
) -> None:
    # Public projection replay validates each original field, but does not
    # replay classification metadata. Model roles have no English authority.
    for row in candidate.sheet(source.MODEL_PORTFOLIO_ROLE).rows:
        originals = {field.header: field.normalized_value for field in row.fields}
        asset, sub_asset = originals["Eszközosztály"], originals["Aleszközosztály"]
        expected = normalization.ClassificationCandidate(
            asset if type(asset) is str else None,
            sub_asset if type(sub_asset) is str else None,
            None,
            None,
            "NOT_APPLICABLE_TO_MODEL_ROLE",
            None,
        )
        _require(
            row.classification == expected,
            "MODEL_CLASSIFICATION_MISMATCH",
            f"{row.occurrence_id}: require unchanged original-only model classification metadata",
        )


def compose_model_projection_ledger(
    candidate: normalization.BiffXlsNormalizationCandidate,
    *,
    metric_result: metrics.ModelMetricProjection,
    currency_risk_result: risk.ModelCurrencyRiskProjection,
    metric_projection: metrics.ProjectionChoice,
    currency_risk_projection: risk.CurrencyRiskProjectionChoice,
    expected_candidate_fingerprint: str,
) -> ModelProjectionLedger:
    """Validate and join both explicit projections on the complete same candidate.

    All arguments are mandatory. Replaying the public APIs substantively validates
    source types, coordinates and provenance, fixed anomaly bindings and projected
    values. Supplied fingerprints/PASS flags alone cannot authorize composition.
    No field becomes a HoldingObservation and no pending reader choice is selected.
    """
    try:
        _versions()
        expected_metrics = metrics.project_model_metrics(
            candidate,
            projection=metric_projection,
            expected_candidate_fingerprint=expected_candidate_fingerprint,
        )
        expected_risk = risk.project_model_currency_risk(
            candidate,
            projection=currency_risk_projection,
            expected_candidate_fingerprint=expected_candidate_fingerprint,
        )
        _model_classifications(candidate)
        _require(
            metrics._typed(metric_result, metrics.ModelMetricProjection)
            and metrics._typed(currency_risk_result, risk.ModelCurrencyRiskProjection),
            "MALFORMED_PROJECTION",
            "expected deeply immutable typed projection results",
        )
        _require(
            metric_result.original_candidate == candidate
            and currency_risk_result.original_candidate == candidate,
            "CANDIDATE_MISMATCH",
            "both results must preserve the same complete candidate",
        )
        _require(
            metric_result.projection == metric_projection
            and currency_risk_result.projection == currency_risk_projection,
            "PROJECTION_CHOICE_MISMATCH",
            "results disagree with the explicit selections",
        )
        model_rows = candidate.sheet(source.MODEL_PORTFOLIO_ROLE).rows
        identities = tuple(row.occurrence_id for row in model_rows)
        _require(
            len(set(identities)) == len(identities)
            and tuple(row.original.occurrence_id for row in metric_result.rows)
            == identities
            and tuple(row.original.occurrence_id for row in currency_risk_result.rows)
            == identities,
            "OCCURRENCE_COVERAGE_MISMATCH",
            "both projections require complete unique source-ordered occurrence coverage",
        )
        _require(
            metric_result == expected_metrics
            and currency_risk_result == expected_risk
            and metric_result.to_json() == expected_metrics.to_json()
            and currency_risk_result.to_json() == expected_risk.to_json(),
            "PROJECTION_CONTENT_MISMATCH",
            "projected values, originals, provenance or evaluated bindings differ from public replay",
        )
        occurrences = tuple(
            ComposedModelOccurrence(row, metric_row, risk_row)
            for row, metric_row, risk_row in zip(
                model_rows, metric_result.rows, currency_risk_result.rows, strict=True
            )
        )
        payload = {
            "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
            "status": "NOT_EVALUATED_COMPOSITION_LEDGER_ONLY",
            "admission_approval": "NOT_GRANTED",
            "evidence_order": "SOURCE_OCCURRENCE_ORDER_NOT_READER_ORDER",
            "pending_reader_decisions": list(_PENDING),
            "original_candidate": candidate.to_dict(),
            "evaluated_metric_binding": _binding(metric_result),
            "evaluated_currency_risk_binding": _binding(currency_risk_result),
            "occurrences": [row.to_dict() for row in occurrences],
        }
        return ModelProjectionLedger(candidate, occurrences, canonical_json(payload))
    except ModelProjectionLedgerError:
        raise
    except (
        metrics.ModelMetricProjectionError,
        risk.ModelCurrencyRiskProjectionError,
    ) as error:
        raise ModelProjectionLedgerError(error.code, str(error)) from error
    except (
        AttributeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise ModelProjectionLedgerError("MALFORMED_INPUT", str(error)) from error
