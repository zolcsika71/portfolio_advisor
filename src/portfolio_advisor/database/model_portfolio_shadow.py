"""Opt-in, read-only shadow comparison for model-portfolio consumers.

The runner deliberately knows nothing about production defaults.  Callers
provide one capture function per bounded workflow.  Each capture is evaluated
against the legacy reader and against one shared validated analytical session.
The report is returned only after the analytical session has successfully
performed its exit checks.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from portfolio_advisor.database.model_portfolio_phase1 import (
    AnalyticalModelPortfolioRepository,
    AnalyticalModelPortfolioSession,
    MnbOtcEvidenceRecord,
)
from portfolio_advisor.database.repository import (
    FileBackedModelPortfolioReader,
)

REQUIRED_WORKFLOWS = (
    "ADVISOR_CURRENT",
    "ADVISOR_TEMPORAL",
    "STRICT_BACKTEST_COVERAGE",
    "FEATURE_DATASET",
    "FORWARD_LABELS",
    "PROSPECTIVE_DECISION",
    "LTIA_MODEL_COMPARISON",
    "MNB_SEMANTIC_AUDIT",
)


class ShadowComparisonError(RuntimeError):
    """A comparison is incomplete, stale, or not explicitly accounted for."""


@dataclass(frozen=True, slots=True)
class ShadowMnbObservation:
    """Comparable MNB semantics including provider-preserved decimal text."""

    source: str
    isin: str
    instrument_name: str
    currency: str
    period_start: str
    period_end: str
    nominal_value_huf_thousand: str
    purchase_value_huf_thousand: str
    average_price: str
    minimum_price: str
    maximum_price: str
    transaction_count: int
    price_type: str
    frequency: str
    source_document: str
    source_document_hash: str


@dataclass(frozen=True, slots=True)
class ShadowReadContext:
    """Storage-neutral model input plus separately scoped MNB audit evidence."""

    model: FileBackedModelPortfolioReader
    mnb_observations: tuple[ShadowMnbObservation, ...]


class ShadowCapture(Protocol):
    def __call__(self, context: ShadowReadContext) -> object: ...


@dataclass(frozen=True, slots=True)
class ExpectedDifference:
    """One exact, reviewed provenance/presentation difference."""

    path: str
    legacy_value: object
    analytical_value: object
    reason: str


@dataclass(frozen=True, slots=True)
class ShadowWorkflow:
    """One required workflow capture or one concrete external blocker."""

    name: str
    capture: ShadowCapture | None = None
    blocker: str | None = None
    expected_differences: tuple[ExpectedDifference, ...] = ()

    def __post_init__(self) -> None:
        if (self.capture is None) == (self.blocker is None):
            raise ShadowComparisonError(
                "a workflow must define exactly one capture or blocker"
            )
        if self.blocker is not None and not self.blocker.strip():
            raise ShadowComparisonError("workflow blocker must be specific")
        if self.blocker is not None and self.expected_differences:
            raise ShadowComparisonError(
                "a blocked workflow cannot claim expected comparison differences"
            )


@dataclass(frozen=True, slots=True)
class ShadowWorkflowResult:
    name: str
    status: str
    elapsed_seconds: float
    payload_fingerprint: str | None
    expected_differences: tuple[ExpectedDifference, ...]
    blocker: str | None


@dataclass(frozen=True, slots=True)
class ShadowSourceProvenance:
    role: str
    path: str
    sha256: str
    authority_epoch_id: str | None
    authority_dataset_fingerprint: str | None
    model_projection_fingerprint: str


@dataclass(frozen=True, slots=True)
class ShadowComparisonReport:
    status: str
    workflows: tuple[ShadowWorkflowResult, ...]
    legacy_provenance: ShadowSourceProvenance
    analytical_provenance: ShadowSourceProvenance
    analytical_full_validation_count: int
    elapsed_seconds: float


def run_shadow_comparison(
    *,
    legacy_repository: FileBackedModelPortfolioReader,
    analytical_repository: AnalyticalModelPortfolioRepository,
    workflows: tuple[ShadowWorkflow, ...],
) -> ShadowComparisonReport:
    """Compare every required workflow and return only after exit validation.

    A blocked workflow is recorded but makes the overall result ``PARTIAL``.
    Any unexplained or incorrectly allowlisted difference fails immediately.
    """
    _validate_workflow_set(workflows)
    started = time.monotonic()
    legacy_before = _sha256(legacy_repository.database_path)
    analytical_before = _sha256(analytical_repository.database_path)
    legacy_mnb = _legacy_mnb_observations(legacy_repository.database_path)
    legacy_context = ShadowReadContext(legacy_repository, legacy_mnb)
    legacy_projection_fingerprint = _model_projection_fingerprint(legacy_repository)
    legacy_payloads: dict[str, object] = {}
    legacy_timings: dict[str, float] = {}
    for workflow in workflows:
        if workflow.capture is None:
            continue
        stage_started = time.monotonic()
        legacy_payloads[workflow.name] = _canonical_value(
            workflow.capture(legacy_context)
        )
        legacy_timings[workflow.name] = time.monotonic() - stage_started

    pending_results: list[ShadowWorkflowResult] = []
    validation_count = 0
    analytical_provenance: ShadowSourceProvenance | None = None
    with analytical_repository.validated_session() as session:
        analytical_context = ShadowReadContext(
            session,
            tuple(
                _shadow_mnb_record(item) for item in session.load_mnb_evidence_records()
            ),
        )
        analytical_provenance = _analytical_provenance(
            session, analytical_repository.authority_epoch_id
        )
        for workflow in workflows:
            if workflow.capture is None:
                pending_results.append(
                    ShadowWorkflowResult(
                        workflow.name,
                        "BLOCKED",
                        0.0,
                        None,
                        (),
                        workflow.blocker,
                    )
                )
                continue
            stage_started = time.monotonic()
            analytical_payload = _canonical_value(workflow.capture(analytical_context))
            elapsed = legacy_timings[workflow.name] + (time.monotonic() - stage_started)
            legacy_payload = legacy_payloads[workflow.name]
            differences = tuple(_differences(legacy_payload, analytical_payload))
            _validate_expected_differences(
                workflow.name, differences, workflow.expected_differences
            )
            pending_results.append(
                ShadowWorkflowResult(
                    workflow.name,
                    "PASS",
                    elapsed,
                    _fingerprint(analytical_payload),
                    workflow.expected_differences,
                    None,
                )
            )
        validation_count = session.full_validation_count

    if validation_count != 1:
        raise ShadowComparisonError(
            "one bounded shadow run must perform exactly one full analytical validation"
        )
    if legacy_before != _sha256(legacy_repository.database_path):
        raise ShadowComparisonError("legacy source changed during shadow comparison")
    if analytical_before != _sha256(analytical_repository.database_path):
        raise ShadowComparisonError(
            "analytical source changed during shadow comparison"
        )
    if analytical_provenance is None:  # pragma: no cover - context guarantees this
        raise ShadowComparisonError("analytical provenance was not finalized")
    if (
        analytical_provenance.model_projection_fingerprint
        != legacy_projection_fingerprint
    ):
        raise ShadowComparisonError(
            "legacy and analytical model projection fingerprints differ"
        )
    legacy_provenance = ShadowSourceProvenance(
        role="LEGACY_OPERATIONAL_SOURCE",
        path=str(legacy_repository.database_path.resolve()),
        sha256=legacy_before,
        authority_epoch_id=None,
        authority_dataset_fingerprint=None,
        model_projection_fingerprint=legacy_projection_fingerprint,
    )
    status = (
        "PASS" if all(item.status == "PASS" for item in pending_results) else "PARTIAL"
    )
    return ShadowComparisonReport(
        status=status,
        workflows=tuple(pending_results),
        legacy_provenance=legacy_provenance,
        analytical_provenance=analytical_provenance,
        analytical_full_validation_count=validation_count,
        elapsed_seconds=time.monotonic() - started,
    )


def _validate_workflow_set(workflows: tuple[ShadowWorkflow, ...]) -> None:
    names = tuple(item.name for item in workflows)
    if len(set(names)) != len(names):
        raise ShadowComparisonError("shadow workflow names must be unique")
    missing = sorted(set(REQUIRED_WORKFLOWS) - set(names))
    unexpected = sorted(set(names) - set(REQUIRED_WORKFLOWS))
    if missing or unexpected:
        raise ShadowComparisonError(
            f"shadow workflow set mismatch; missing={missing}, unexpected={unexpected}"
        )


def _legacy_mnb_observations(database_path: Path) -> tuple[ShadowMnbObservation, ...]:
    uri = f"file:{database_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mnb_otc_observations'"
        ).fetchone()
        if table is None:
            return ()
        rows = connection.execute(
            "SELECT * FROM mnb_otc_observations ORDER BY period_start, period_end"
        ).fetchall()
    return tuple(
        ShadowMnbObservation(
            source=str(row["source"]),
            isin=str(row["isin"]),
            instrument_name=str(row["instrument_name"]),
            currency=str(row["currency"]),
            period_start=str(row["period_start"]),
            period_end=str(row["period_end"]),
            nominal_value_huf_thousand=str(row["nominal_value_huf_thousand"]),
            purchase_value_huf_thousand=str(row["purchase_value_huf_thousand"]),
            average_price=str(row["average_price"]),
            minimum_price=str(row["minimum_price"]),
            maximum_price=str(row["maximum_price"]),
            transaction_count=int(row["transaction_count"]),
            price_type=str(row["price_type"]),
            frequency=str(row["frequency"]),
            source_document=str(row["source_document"]),
            source_document_hash=str(row["source_document_hash"]),
        )
        for row in rows
    )


def _shadow_mnb_record(record: MnbOtcEvidenceRecord) -> ShadowMnbObservation:
    observation = record.observation
    exact = record.exact_text
    return ShadowMnbObservation(
        source=observation.source,
        isin=observation.isin,
        instrument_name=observation.instrument_name,
        currency=observation.currency,
        period_start=observation.period_start.isoformat(),
        period_end=observation.period_end.isoformat(),
        nominal_value_huf_thousand=exact.nominal_value_huf_thousand,
        purchase_value_huf_thousand=exact.purchase_value_huf_thousand,
        average_price=exact.average_price,
        minimum_price=exact.minimum_price,
        maximum_price=exact.maximum_price,
        transaction_count=observation.transaction_count,
        price_type=observation.price_type,
        frequency=observation.frequency,
        source_document=observation.source_document,
        source_document_hash=observation.source_document_hash,
    )


def _analytical_provenance(
    session: AnalyticalModelPortfolioSession, authority_epoch_id: str
) -> ShadowSourceProvenance:
    epoch_id, dataset_fingerprint = session.authority_provenance()
    if epoch_id != authority_epoch_id:
        raise ShadowComparisonError("validated analytical authority epoch changed")
    return ShadowSourceProvenance(
        role="PHASE1_NON_OPERATIONAL_ANALYTICAL_SOURCE",
        path=str(session.database_path.resolve()),
        sha256=_sha256(session.database_path),
        authority_epoch_id=epoch_id,
        authority_dataset_fingerprint=dataset_fingerprint,
        model_projection_fingerprint=_model_projection_fingerprint(session),
    )


def _model_projection_fingerprint(
    reader: FileBackedModelPortfolioReader,
) -> str:
    return _fingerprint(
        [
            {
                "observation_date": observation_date,
                "holdings": reader.load_holdings(observation_date),
            }
            for observation_date in reader.observation_dates()
        ]
    )


def _canonical_value(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _canonical_value(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ShadowComparisonError("workflow capture returned a non-finite float")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ShadowComparisonError(
        f"workflow capture returned unsupported type: {type(value).__name__}"
    )


def _differences(
    legacy: object, analytical: object, path: str = "$"
) -> list[ExpectedDifference]:
    if isinstance(legacy, dict) and isinstance(analytical, dict):
        result: list[ExpectedDifference] = []
        for key in sorted(set(legacy) | set(analytical)):
            child = f"{path}.{key}"
            if key not in legacy or key not in analytical:
                result.append(
                    ExpectedDifference(
                        child,
                        legacy.get(key, "<MISSING>"),
                        analytical.get(key, "<MISSING>"),
                        "unexplained",
                    )
                )
            else:
                result.extend(_differences(legacy[key], analytical[key], child))
        return result
    if isinstance(legacy, list) and isinstance(analytical, list):
        result = []
        for index in range(max(len(legacy), len(analytical))):
            child = f"{path}[{index}]"
            if index >= len(legacy) or index >= len(analytical):
                result.append(
                    ExpectedDifference(
                        child,
                        legacy[index] if index < len(legacy) else "<MISSING>",
                        analytical[index] if index < len(analytical) else "<MISSING>",
                        "unexplained",
                    )
                )
            else:
                result.extend(_differences(legacy[index], analytical[index], child))
        return result
    if legacy != analytical:
        return [ExpectedDifference(path, legacy, analytical, "unexplained")]
    return []


def _validate_expected_differences(
    name: str,
    actual: tuple[ExpectedDifference, ...],
    expected: tuple[ExpectedDifference, ...],
) -> None:
    actual_values = {
        (
            item.path,
            _fingerprint(item.legacy_value),
            _fingerprint(item.analytical_value),
        )
        for item in actual
    }
    expected_values = {
        (
            item.path,
            _fingerprint(_canonical_value(item.legacy_value)),
            _fingerprint(_canonical_value(item.analytical_value)),
        )
        for item in expected
    }
    if actual_values != expected_values:
        raise ShadowComparisonError(
            f"{name} has unexplained or stale expected differences: "
            f"actual={sorted(item[0] for item in actual_values)}, "
            f"expected={sorted(item[0] for item in expected_values)}"
        )
    if any(not item.reason.strip() for item in expected):
        raise ShadowComparisonError(
            f"{name} has an expected difference without a reason"
        )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _canonical_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise ShadowComparisonError(f"shadow source is missing: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
