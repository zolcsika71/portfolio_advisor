"""Explicit, in-memory reader views; no storage, admission or active consumers.

The public projections and composition are replayed before any DTO is exposed.
Provenance captures the evaluated policies, complete ledger and reader permutation.
It describes supplied evidence, not freshly inspected or admitted workbook bytes.
"""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from typing import Final, Literal, cast

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.repository import HoldingObservation
from portfolio_advisor.workbook_source import model_currency_risk_projection as risk
from portfolio_advisor.workbook_source import model_metric_projection as metrics
from portfolio_advisor.workbook_source.model_projection_ledger import (
    ComposedModelOccurrence,
    ModelProjectionLedger,
    compose_model_projection_ledger,
)
from portfolio_advisor.workbook_source.normalization import NormalizedFieldCandidate

MODEL_CLASSIFICATION_ORIGINAL_LABELS_V1: Final = (
    "MODEL_CLASSIFICATION_ORIGINAL_LABELS_V1"
)
MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1: Final = "MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1"
ORIGINAL_MODEL_DESCRIPTORS_V1: Final = "ORIGINAL_MODEL_DESCRIPTORS_V1"
SOURCE_ORDER_V1: Final = "SOURCE_ORDER_V1"
MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1: Final = (
    "MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1"
)
ClassificationProfile = Literal[
    "MODEL_CLASSIFICATION_ORIGINAL_LABELS_V1", "MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1"
]
DescriptorProfile = Literal["ORIGINAL_MODEL_DESCRIPTORS_V1"]
HoldingOrder = Literal["SOURCE_ORDER_V1", "MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1"]
CONTRACT_NAME: Final = "CANDIDATE_NATIVE_MODEL_PORTFOLIO_READER"
CONTRACT_VERSION: Final = 1

# Independent model authority, frozen from the approved tables; never import the
# mutable legacy dictionaries or borrow a shortlist manifest/correction admission.
_ASSET: Final = (
    ("alternatív", "Alternative"),
    ("kötvény", "Bond"),
    ("kötvény - befektetési kategória", "Investment Grade Bond"),
    ("kötvény-befektetési kategória", "Investment Grade Bond"),
    ("kötvény - magas hozamú", "High Yield Bond"),
    ("kötvény-magas hozamú", "High Yield Bond"),
    ("kötvény-rugalmas", "Flexible Bond"),
    ("pénzpiac", "Money Market"),
    ("pénzpiaci", "Money Market"),
    ("részvény", "Equity"),
)
_SUB_ASSET: Final = (
    ("abszolút hozamú", "Absolute Return"),
    ("amerikai dollár", "USD"),
    ("eur", "EUR"),
    ("euro", "EUR"),
    ("európa", "Europe"),
    ("európa-vállalatok", "Europe-Corporates"),
    ("európai vállalatok", "Europe-Corporates"),
    ("fejl?d? piacok", "Emerging Markets"),
    ("globál", "Global"),
    ("globál állampapír", "Global-Government Bond"),
    ("globál-állampapír", "Global-Government Bond"),
    ("hu-állampapír", "Hungary-Government Bond"),
    ("huf", "HUF"),
    ("ingatlan", "Real Estate"),
    ("kötvény - magyar állampapírok", "Bond - Hungarian Government Bonds"),
    ("közép-kelet európai állampapír", "Central and Eastern European Government Bond"),
    ("magyar forint", "HUF"),
    ("magyar állampapírok", "Hungarian Government Bonds"),
    ("nyersanyag", "Commodities"),
    ("részvény - fejl?d? piacok", "Equity - Emerging Markets"),
    ("usd", "USD"),
    ("észak-amerika", "North America"),
    ("észak-amerika-állampapír", "North America-Government Bond"),
    ("észak-amerikai állampapír", "North America-Government Bond"),
)
_DTO_METRICS: Final = (
    ("return_1y", "1yr"),
    ("sharpe_ratio_1y", "1Y Sharpe"),
    ("volatility_1y", "1Y Vol."),
    ("downside_risk", "Down. risk"),
    ("maximum_drawdown", "Max. drawd."),
)


def _policy_binding(
    classification_profile: ClassificationProfile,
    descriptor_profile: DescriptorProfile,
    holding_order: HoldingOrder,
) -> str:
    policies: dict[str, object] = {
        "approved_on": "2026-10-08",
        "classification": {
            "name": "MODEL_CLASSIFICATION_AUTHORITY_POLICY_V1",
            "version": 1,
            "profile": classification_profile,
            "lookup": ["NFC", "trim", "casefold"]
            if classification_profile == MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1
            else [],
            "asset_keys": _ASSET,
            "sub_asset_keys": _SUB_ASSET,
            "canonical_identity_keys": "SAME_COLUMN_ONLY",
            "reviewed_legacy_source_sha256": "edacb65aa8947f35934b115ff726dde9a37f2f4d593192989889c5e1b1e3943a",
        },
        "descriptors": {
            "name": "MODEL_READER_DESCRIPTIVE_ALLOCATION_POLICY_V1",
            "version": 1,
            "profile": descriptor_profile,
        },
        "ordering": {
            "name": "MODEL_READER_HOLDING_ORDER_POLICY_V1",
            "version": 1,
            "profile": holding_order,
            "ties": "SOURCE_OCCURRENCE_INDEX",
            "sort_fields": ["portfolio_name", "isin", "product"]
            if holding_order == MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1
            else [],
            "collation": "UTF8_BINARY"
            if holding_order == MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1
            else None,
        },
    }
    for binding in policies.values():
        if type(binding) is dict:
            binding["content_fingerprint"] = canonical_fingerprint(binding)
    return canonical_json(policies)


class CandidateModelReaderError(ValueError):
    """A complete snapshot cannot be safely exposed under the selected profiles."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise CandidateModelReaderError(code, message)


def _key(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def _classification(
    field: NormalizedFieldCandidate, profile: ClassificationProfile
) -> tuple[str, str | None]:
    value = _text(field)
    if profile == MODEL_CLASSIFICATION_ORIGINAL_LABELS_V1:
        return value, None
    assert type(field.source_cell.raw_value) is str
    key = _key(field.source_cell.raw_value)
    table = _ASSET if field.header == "Eszközosztály" else _SUB_ASSET
    matches = {output for source, output in table if key in (source, _key(output))}
    _require(
        len(matches) == 1,
        "UNKNOWN_CLASSIFICATION",
        f"{field.field_occurrence_id}: no unique approved same-column lexical key {key!r}",
    )
    return next(iter(matches)), key


def _text(field: NormalizedFieldCandidate) -> str:
    _require(
        field.status == "NORMALIZED"
        and field.source_cell.cell_type == "text"
        and type(field.normalized_value) is str
        and bool(field.normalized_value),
        "INVALID_REQUIRED_FIELD",
        f"{field.field_occurrence_id}: require valid nonempty source text",
    )
    return cast(str, field.normalized_value)


@dataclass(frozen=True, slots=True)
class ReaderFieldProvenance:
    """A DTO/sidecar value with its original typed field and applied disposition."""

    field_name: str
    original: NormalizedFieldCandidate
    value: str | float | None
    reason: str
    lookup_key: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "field_name": self.field_name,
            "original": self.original.to_dict(),
            "value": self.value,
            "reason": self.reason,
            "lookup_key": self.lookup_key,
        }


@dataclass(frozen=True, slots=True)
class ReaderHoldingProvenance:
    dto_position: int
    source_occurrence: ComposedModelOccurrence
    observation: HoldingObservation
    fields: tuple[ReaderFieldProvenance, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "dto_position": self.dto_position,
            "source_occurrence": self.source_occurrence.to_dict(),
            "observation": asdict(self.observation),
            "fields": [field.to_dict() for field in self.fields],
        }


@dataclass(frozen=True, slots=True)
class ReaderSnapshotProvenance:
    observation_date: date
    ledger: ModelProjectionLedger
    holdings: tuple[HoldingObservation, ...]
    provenance: tuple[ReaderHoldingProvenance, ...]
    # Zero-based DTO position -> zero-based model source position, a bijection.
    dto_to_source: tuple[int, ...]
    _evaluated_reader_policies_json: str

    def to_dict(self) -> dict[str, object]:
        return {
            "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
            "status": "NOT_EVALUATED_READER_ONLY",
            "admission_approval": "NOT_GRANTED",
            "evaluated_reader_policies": json.loads(
                self._evaluated_reader_policies_json
            ),
            "observation_date": self.observation_date.isoformat(),
            "ledger": self.ledger.to_dict(),
            "dto_to_source": list(self.dto_to_source),
            "provenance": [item.to_dict() for item in self.provenance],
        }


def _replay(
    ledger: ModelProjectionLedger, candidate_fingerprint: str, ledger_fingerprint: str
) -> ModelProjectionLedger:
    _require(
        type(ledger) is ModelProjectionLedger, "MALFORMED_LEDGER", "require a v1 ledger"
    )
    _require(
        metrics._typed(ledger, ModelProjectionLedger),
        "MALFORMED_LEDGER",
        "require deeply immutable typed ledger contents, not caller equality/serialization substitutes",
    )
    _require(
        type(ledger_fingerprint) is str
        and ledger_fingerprint == ledger.ledger_fingerprint,
        "LEDGER_FINGERPRINT_MISMATCH",
        "expected ledger identity differs",
    )
    payload = ledger.to_dict()
    metric_choice = cast(
        metrics.ProjectionChoice, _choice(payload, "evaluated_metric_binding")
    )
    risk_choice = cast(
        risk.CurrencyRiskProjectionChoice,
        _choice(payload, "evaluated_currency_risk_binding"),
    )
    candidate = ledger.original_candidate
    replay = compose_model_projection_ledger(
        candidate,
        metric_result=metrics.project_model_metrics(
            candidate,
            projection=metric_choice,
            expected_candidate_fingerprint=candidate_fingerprint,
        ),
        currency_risk_result=risk.project_model_currency_risk(
            candidate,
            projection=risk_choice,
            expected_candidate_fingerprint=candidate_fingerprint,
        ),
        metric_projection=metric_choice,
        currency_risk_projection=risk_choice,
        expected_candidate_fingerprint=candidate_fingerprint,
    )
    _require(
        ledger == replay
        and ledger.to_json() == replay.to_json()
        and canonical_json([item.to_dict() for item in ledger.occurrences])
        == canonical_json([item.to_dict() for item in replay.occurrences]),
        "LEDGER_CONTENT_MISMATCH",
        "complete live and captured ledger contents differ from public same-candidate replay",
    )
    return replay


def _choice(payload: dict[str, object], name: str) -> str:
    binding = payload[name]
    _require(type(binding) is dict, "MALFORMED_LEDGER", f"missing {name}")
    projection = cast(dict[str, object], binding)["projection"]
    _require(
        type(projection) is dict, "MALFORMED_LEDGER", "missing projection selection"
    )
    value = cast(dict[str, object], projection)["name"]
    _require(type(value) is str, "MALFORMED_LEDGER", "projection name must be text")
    return cast(str, value)


def _observation(
    occurrence: ComposedModelOccurrence, classification_profile: ClassificationProfile
) -> tuple[HoldingObservation, tuple[ReaderFieldProvenance, ...]]:
    originals = {field.header: field for field in occurrence.original.fields}
    provenance: list[ReaderFieldProvenance] = []
    values: dict[str, str | float | None] = {}
    for name, header in (
        ("portfolio_name", "Portfólió neve"),
        ("product", "Termék"),
        ("isin", "ISIN"),
    ):
        field = originals[header]
        values[name] = _text(field)
        provenance.append(
            ReaderFieldProvenance(name, field, values[name], "ORIGINAL_TRIMMED_TEXT")
        )
    currency = originals["Deviza"]
    if currency.status == "SOURCE_MISSING":
        currency_value = None
        reason = "ORIGINAL_SOURCE_MISSING"
    else:
        currency_value = _text(currency)
        reason = "ORIGINAL_TRIMMED_TEXT"
    values["currency"] = currency_value
    provenance.append(
        ReaderFieldProvenance("currency", currency, currency_value, reason)
    )
    allocation = originals["Hányad (%)"]
    weight = allocation.normalized_value
    _require(
        allocation.status == "NORMALIZED"
        and type(weight) is float
        and math.isfinite(weight)
        and weight >= 0,
        "INVALID_ALLOCATION",
        f"{allocation.field_occurrence_id}: require unchanged finite nonnegative percentage points",
    )
    values["allocation"] = cast(float, weight)
    provenance.append(
        ReaderFieldProvenance(
            "allocation", allocation, cast(float, weight), "UNCHANGED_PERCENTAGE_POINTS"
        )
    )
    for name, header in (
        ("asset_class", "Eszközosztály"),
        ("sub_asset_class", "Aleszközosztály"),
    ):
        field = originals[header]
        value, lookup = _classification(field, classification_profile)
        provenance.append(
            ReaderFieldProvenance(name, field, value, classification_profile, lookup)
        )
        if name == "asset_class":
            values[name] = value
    projected_risk = occurrence.currency_risk.currency_risk
    values["currency_risk"] = projected_risk.value
    provenance.append(
        ReaderFieldProvenance(
            "currency_risk",
            projected_risk.original,
            projected_risk.value,
            projected_risk.reason,
            projected_risk.lookup_key,
        )
    )
    projected_metrics = {
        item.original.header: item for item in occurrence.metrics.metrics
    }
    for name, header in _DTO_METRICS:
        metric = projected_metrics[header]
        _require(
            metric.disposition != "REJECTED",
            "REJECTED_READER_METRIC",
            f"{metric.original.field_occurrence_id}: rejected DTO metric",
        )
        values[name] = metric.value
        provenance.append(
            ReaderFieldProvenance(
                name, metric.original, metric.value, metric.disposition
            )
        )
    return HoldingObservation(
        portfolio_name=cast(str, values["portfolio_name"]),
        product=cast(str, values["product"]),
        isin=cast(str, values["isin"]),
        allocation=cast(float, values["allocation"]),
        currency=cast(str | None, values["currency"]),
        currency_risk=cast(str | None, values["currency_risk"]),
        return_1y=cast(float | None, values["return_1y"]),
        sharpe_ratio_1y=cast(float | None, values["sharpe_ratio_1y"]),
        volatility_1y=cast(float | None, values["volatility_1y"]),
        downside_risk=cast(float | None, values["downside_risk"]),
        maximum_drawdown=cast(float | None, values["maximum_drawdown"]),
        asset_class=cast(str, values["asset_class"]),
    ), tuple(provenance)


def _snapshot(
    ledger: ModelProjectionLedger,
    classification_profile: ClassificationProfile,
    holding_order: HoldingOrder,
    evaluated_policies_json: str,
) -> ReaderSnapshotProvenance:
    raw_labels: dict[str, str] = {}
    built = []
    for occurrence in ledger.occurrences:
        observation, fields = _observation(occurrence, classification_profile)
        raw = next(
            field.source_cell.raw_value
            for field in occurrence.original.fields
            if field.header == "Portfólió neve"
        )
        assert type(raw) is str
        key = observation.portfolio_name
        _require(
            key not in raw_labels or raw_labels[key] == raw,
            "PORTFOLIO_TRIM_COLLISION",
            f"{occurrence.original.occurrence_id}: distinct raw portfolio labels collapse to {key!r}",
        )
        raw_labels[key] = raw
        built.append((observation, fields))
    permutation = tuple(range(len(built)))
    if holding_order == MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1:
        permutation = tuple(
            sorted(
                permutation,
                key=lambda index: (
                    built[index][0].portfolio_name.encode("utf-8"),
                    cast(str, built[index][0].isin).encode("utf-8"),
                    cast(str, built[index][0].product).encode("utf-8"),
                    ledger.occurrences[index].original.occurrence_index,
                ),
            )
        )
    return ReaderSnapshotProvenance(
        date.fromisoformat(ledger.original_candidate.source_binding.snapshot_date),
        ledger,
        tuple(built[index][0] for index in permutation),
        tuple(
            ReaderHoldingProvenance(position, ledger.occurrences[index], *built[index])
            for position, index in enumerate(permutation)
        ),
        permutation,
        evaluated_policies_json,
    )


@dataclass(frozen=True, slots=True, init=False)
class CandidateNativeModelPortfolioReader:
    """Opt-in, storage-neutral reader; construct all snapshots atomically in memory.

    Every selection and expected identity is mandatory. Metric and currency-risk
    choices are explicitly captured by the supplied ledgers, not defaulted here.
    Only synthetic validation is established; retained reader equivalence is pending.
    """

    snapshots: tuple[ReaderSnapshotProvenance, ...]
    _evaluated_payload_json: str

    def __init__(
        self,
        *,
        ledgers: tuple[ModelProjectionLedger, ...],
        expected_candidate_fingerprints: tuple[str, ...],
        expected_ledger_fingerprints: tuple[str, ...],
        classification_profile: ClassificationProfile,
        descriptor_profile: DescriptorProfile,
        holding_order: HoldingOrder,
    ) -> None:
        try:
            for value, supported in (
                (
                    classification_profile,
                    (
                        MODEL_CLASSIFICATION_ORIGINAL_LABELS_V1,
                        MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1,
                    ),
                ),
                (descriptor_profile, (ORIGINAL_MODEL_DESCRIPTORS_V1,)),
                (
                    holding_order,
                    (SOURCE_ORDER_V1, MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1),
                ),
            ):
                _require(
                    type(value) is str and value in supported,
                    "UNSUPPORTED_PROFILE",
                    "select an exact approved v1 profile",
                )
            _require(
                type(ledgers) is tuple
                and type(expected_candidate_fingerprints) is tuple
                and type(expected_ledger_fingerprints) is tuple
                and bool(ledgers)
                and len(ledgers)
                == len(expected_candidate_fingerprints)
                == len(expected_ledger_fingerprints),
                "INCOMPLETE_SNAPSHOT_BINDINGS",
                "require nonempty immutable ledgers and one expected candidate/ledger identity per snapshot",
            )
            evaluated_policies_json = _policy_binding(
                classification_profile, descriptor_profile, holding_order
            )
            snapshots = tuple(
                _snapshot(
                    _replay(ledger, candidate_id, ledger_id),
                    classification_profile,
                    holding_order,
                    evaluated_policies_json,
                )
                for ledger, candidate_id, ledger_id in zip(
                    ledgers,
                    expected_candidate_fingerprints,
                    expected_ledger_fingerprints,
                    strict=True,
                )
            )
            dates = tuple(item.observation_date for item in snapshots)
            _require(
                len(set(dates)) == len(dates),
                "DUPLICATE_SNAPSHOT_DATE",
                "one complete candidate per date; do not merge or select precedence",
            )
            snapshots = tuple(sorted(snapshots, key=lambda item: item.observation_date))
            payload = {
                "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
                "status": "NOT_EVALUATED_READER_ONLY",
                "admission_approval": "NOT_GRANTED",
                "policies": json.loads(evaluated_policies_json),
                "snapshots": [item.to_dict() for item in snapshots],
            }
            object.__setattr__(self, "snapshots", snapshots)
            object.__setattr__(self, "_evaluated_payload_json", canonical_json(payload))
        except CandidateModelReaderError:
            raise
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
            RecursionError,
        ) as error:
            raise CandidateModelReaderError(
                getattr(error, "code", "MALFORMED_READER_INPUT"), str(error)
            ) from error

    @property
    def admission_approval(self) -> str:
        return "NOT_GRANTED"

    @property
    def reader_fingerprint(self) -> str:
        return canonical_fingerprint(json.loads(self._evaluated_payload_json))

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = json.loads(self._evaluated_payload_json)
        return {**payload, "reader_fingerprint": self.reader_fingerprint}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    def observation_dates(self) -> tuple[date, ...]:
        return tuple(item.observation_date for item in self.snapshots)

    def latest_observation_date(self) -> date:
        return self.snapshots[-1].observation_date

    def snapshot_provenance(self, observation_date: date) -> ReaderSnapshotProvenance:
        _require(
            type(observation_date) is date,
            "INVALID_OBSERVATION_DATE",
            "request an exact date, not a coercion",
        )
        for snapshot in self.snapshots:
            if snapshot.observation_date == observation_date:
                return snapshot
        raise CandidateModelReaderError(
            "UNAVAILABLE_OBSERVATION_DATE", f"no exact snapshot for {observation_date}"
        )

    def load_holdings(self, observation_date: date) -> list[HoldingObservation]:
        return list(self.snapshot_provenance(observation_date).holdings)
