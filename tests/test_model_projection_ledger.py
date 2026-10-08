"""Synthetic composition only; no reader, retained inputs or approval override."""

from __future__ import annotations

import inspect
import math
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.workbook_source import (
    MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1 as PROFILE,
)
from portfolio_advisor.workbook_source import (
    MODEL_METRIC_ORIGINAL_V1 as ORIGINAL,
)
from portfolio_advisor.workbook_source import (
    MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1 as COMPATIBILITY,
)
from portfolio_advisor.workbook_source import (
    BiffXlsNormalizationCandidate,
    ModelProjectionLedgerError,
    compose_model_projection_ledger,
    normalization,
    normalize_biff_xls_envelope,
    parse_biff_xls,
    project_model_currency_risk,
    project_model_metrics,
)
from portfolio_advisor.workbook_source import model_currency_risk_projection as risk
from portfolio_advisor.workbook_source import model_metric_projection as metrics
from portfolio_advisor.workbook_source import model_projection_ledger as ledger
from portfolio_advisor.workbook_source.biff_xls import MODEL_PORTFOLIO_ROLE
from tests.fixtures.biff_xls_fixture import write_biff_fixture
from tests.test_model_currency_risk_projection import _cell, _synthetic_anomaly_case
from tests.test_model_metric_projection import _cell as metric_cell


@pytest.fixture
def candidate(tmp_path: Path) -> BiffXlsNormalizationCandidate:
    envelope = parse_biff_xls(
        write_biff_fixture(
            tmp_path, model_currency_risk="Fedezve", duplicate_shortlist_first_row=True
        )
    )
    return normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )


def _results(candidate: BiffXlsNormalizationCandidate, mode: Any = COMPATIBILITY):
    return (
        project_model_metrics(
            candidate,
            projection=mode,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        ),
        project_model_currency_risk(
            candidate,
            projection=PROFILE,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        ),
    )


def _compose(
    candidate: BiffXlsNormalizationCandidate, mode: Any = COMPATIBILITY, **changes: Any
) -> ledger.ModelProjectionLedger:
    m, r = _results(candidate, mode)
    arguments = {
        "metric_result": m,
        "currency_risk_result": r,
        "metric_projection": mode,
        "currency_risk_projection": PROFILE,
        "expected_candidate_fingerprint": candidate.candidate_fingerprint,
        **changes,
    }
    return compose_model_projection_ledger(candidate, **arguments)


@pytest.mark.parametrize("mode", [ORIGINAL, COMPATIBILITY])
def test_public_composition_preserves_all_fields_and_resolutions(candidate, mode):
    before = candidate.to_json()
    m, r = _results(candidate, mode)
    m_before, r_before = m.to_json(), r.to_json()
    result = _compose(candidate, mode, metric_result=m, currency_risk_result=r)
    assert result.original_candidate is candidate
    assert len(result.occurrences) == 2
    for original, composed in zip(
        candidate.sheets[0].rows, result.occurrences, strict=True
    ):
        assert composed.original is original
        assert composed.metrics.original == composed.currency_risk.original == original
        assert len(composed.original.fields) == 21
        assert len(composed.metrics.metrics) == 12
        assert (
            tuple(
                (metric.original.header, metric.original.target_field)
                for metric in composed.metrics.metrics
            )
            == metrics._METRIC_SCOPE
        )
        assert composed.currency_risk.currency_risk.value == "Hedged"
        assert (
            composed.original.classification.mapping_status
            == "NOT_APPLICABLE_TO_MODEL_ROLE"
        )
        assert composed.original.fields[3].header == "Hányad (%)"
        assert composed.original.fields[3].normalized_value == 50.0
    zero = result.occurrences[0].metrics.metrics[0]
    assert zero.original.normalized_value == 0.0
    assert zero.value == (0.0 if mode == ORIGINAL else None)
    assert zero.zero_policy_applied
    assert result.occurrences[1].metrics.metrics[0].disposition == "SOURCE_MISSING"
    payload = result.to_dict()
    assert payload["original_candidate"] == candidate.to_dict()
    assert (
        payload["evaluated_metric_binding"]["projection_fingerprint"]
        == m.projection_fingerprint
    )
    assert (
        payload["evaluated_currency_risk_binding"]["projection_fingerprint"]
        == r.projection_fingerprint
    )
    assert payload["evidence_order"] == "SOURCE_OCCURRENCE_ORDER_NOT_READER_ORDER"
    assert len(payload["pending_reader_decisions"]) == 3
    assert payload["status"] == "NOT_EVALUATED_COMPOSITION_LEDGER_ONLY"
    assert result.admission_approval == payload["admission_approval"] == "NOT_GRANTED"
    assert candidate.to_json() == before
    assert (m.to_json(), r.to_json()) == (m_before, r_before)
    assert result.to_json() == _compose(candidate, mode).to_json()


@pytest.mark.parametrize(
    "value,expected",
    [
        (" Fedezve ", "Hedged"),
        ("Nincs fedezve", "Unhedged"),
        ("Részben fedezve", "Partially Hedged"),
        (None, None),
        ("", None),
    ],
)
def test_currency_outcomes_and_missing_originals(tmp_path, value, expected):
    envelope = parse_biff_xls(write_biff_fixture(tmp_path, model_currency_risk=value))
    candidate = normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )
    result = _compose(candidate)
    assert all(
        row.currency_risk.currency_risk.value == expected for row in result.occurrences
    )
    assert result.original_candidate.to_dict() == candidate.to_dict()
    assert result.original_candidate.diagnostics == candidate.diagnostics


def test_duplicate_occurrences_survive_without_reader_order_selection(tmp_path):
    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, model_currency_risk="Fedezve")
    )
    sheet = envelope.sheet(MODEL_PORTFOLIO_ROLE)
    second = sheet.rows[1]
    cells = tuple(
        replace(first, source_row=second.source_row, coordinate=other.coordinate)
        for first, other in zip(sheet.rows[0].cells, second.cells, strict=True)
    )
    second = replace(second, cells=cells)
    payload = second.to_dict()
    payload.pop("row_fingerprint")
    second = replace(second, row_fingerprint=canonical_fingerprint(payload))
    sheet = replace(sheet, rows=(sheet.rows[0], second))
    payload = sheet.to_dict()
    payload.pop("sheet_fingerprint")
    sheet = replace(sheet, sheet_fingerprint=canonical_fingerprint(payload))
    envelope = replace(envelope, sheets=(sheet, envelope.sheets[1]))
    candidate = normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )
    result = _compose(candidate)
    assert len(result.occurrences) == 2
    assert len({row.original.occurrence_id for row in result.occurrences}) == 2
    assert [row.original.source_row for row in result.occurrences] == [2, 3]
    assert (
        result.occurrences[0].original.fields[0].normalized_value
        == result.occurrences[1].original.fields[0].normalized_value
    )


def test_public_anomaly_composition_with_restored_test_local_registry(
    tmp_path, monkeypatch
):
    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, model_currency_risk="Fedezve")
    )
    candidate, synthetic_workbook = _synthetic_anomaly_case(envelope)
    before = candidate.to_json()
    with pytest.raises(
        risk.ModelCurrencyRiskProjectionError, match="UNAPPROVED_CURRENCY_RISK_STATE"
    ):
        _compose(candidate)
    # Test-only seam: public APIs and proof matchers stay active. This does not
    # impersonate historical hashes or prove retained-candidate acceptance.
    with monkeypatch.context() as patch:
        patch.setattr(risk, "_APPROVED_WORKBOOKS", (synthetic_workbook,))
        projected_metrics, projected_risk = _results(candidate)
        result = _compose(candidate)
        captured = result.to_json()
        for row in result.occurrences:
            resolution = row.currency_risk.currency_risk
            assert resolution.disposition == "KNOWN_ANOMALY_UNINTERPRETED"
            assert resolution.value is None
            assert resolution.reason == risk.ANOMALY_REASON
            assert resolution.evidence_entry_fingerprint is not None
            assert (
                row.metrics.metrics[0].disposition
                == "OMITTED_NUMERIC_ZERO_FOR_LEGACY_COMPATIBILITY"
            )
        assert len(result.occurrences) == 24
    assert result.to_json() == captured  # captures evaluated registry, not globals
    assert candidate.to_json() == before
    with pytest.raises(ModelProjectionLedgerError):
        compose_model_projection_ledger(
            candidate,
            metric_result=projected_metrics,
            currency_risk_result=projected_risk,
            metric_projection=COMPATIBILITY,
            currency_risk_projection=PROFILE,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )


@pytest.mark.parametrize("side", ["metric_result", "currency_risk_result"])
@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "reorder"])
def test_incomplete_or_misordered_coverage_rejects(candidate, side, change):
    m, r = _results(candidate)
    value = m if side == "metric_result" else r
    rows = value.rows
    altered = {
        "missing": rows[:1],
        "extra": (*rows, rows[0]),
        "duplicate": (rows[0], rows[0]),
        "reorder": rows[::-1],
    }[change]
    changed = replace(value, rows=altered)
    assert changed.projection_fingerprint != value.projection_fingerprint
    with pytest.raises(
        ModelProjectionLedgerError, match="OCCURRENCE_COVERAGE_MISMATCH"
    ):
        _compose(candidate, **{side: changed})


@pytest.mark.parametrize("side", ["metric_result", "currency_risk_result"])
def test_mixed_candidates_reject(candidate, side):
    other = replace(
        candidate,
        source_binding=replace(candidate.source_binding, envelope_fingerprint="a" * 64),
    )
    other_m, other_r = _results(other)
    with pytest.raises(ModelProjectionLedgerError, match="CANDIDATE_MISMATCH"):
        _compose(candidate, **{side: other_m if side == "metric_result" else other_r})


@pytest.mark.parametrize(
    "kind", ["value", "reason", "original", "coordinate", "reference", "format"]
)
@pytest.mark.parametrize("side", ["metric_result", "currency_risk_result"])
def test_recomputed_fingerprints_do_not_hide_tampering(candidate, side, kind):
    m, r = _results(candidate)
    result = m if side == "metric_result" else r
    row = result.rows[0]
    projected = row.metrics[0] if side == "metric_result" else row.currency_risk
    if kind == "value":
        projected = replace(
            projected, value=1.25 if side == "metric_result" else "Unhedged"
        )
    elif kind == "reason":
        projected = (
            replace(projected, zero_policy_applied=False)
            if side == "metric_result"
            else replace(projected, reason="FORGED")
        )
    else:
        original = projected.original
        if kind == "original":
            original = replace(
                original,
                normalized_value=99.0 if side == "metric_result" else "Changed",
            )
        elif kind == "reference":
            original = replace(original, field_occurrence_id="forged")
        else:
            cell = replace(
                original.source_cell,
                **(
                    {"coordinate": "Z999"}
                    if kind == "coordinate"
                    else {"number_format": "0.00"}
                ),
            )
            original = replace(original, source_cell=cell)
        projected = replace(projected, original=original)
    row = (
        replace(row, metrics=(projected, *row.metrics[1:]))
        if side == "metric_result"
        else replace(row, currency_risk=projected)
    )
    changed = replace(result, rows=(row, *result.rows[1:]))
    assert changed.projection_fingerprint != result.projection_fingerprint
    with pytest.raises(ModelProjectionLedgerError, match="PROJECTION_CONTENT_MISMATCH"):
        _compose(candidate, **{side: changed})


@pytest.mark.parametrize(
    "change", ["bool_value", "mutable_rows", "mutable_metrics", "registry"]
)
def test_malformed_types_and_registry_identity_reject(candidate, change):
    m, r = _results(candidate)
    if change == "bool_value":
        first = replace(m.rows[0].metrics[0], value=True)
        m = replace(
            m,
            rows=(
                replace(m.rows[0], metrics=(first, *m.rows[0].metrics[1:])),
                *m.rows[1:],
            ),
        )
    elif change == "mutable_rows":
        r = replace(r, rows=list(r.rows))
    elif change == "mutable_metrics":
        m = replace(
            m, rows=(replace(m.rows[0], metrics=list(m.rows[0].metrics)), *m.rows[1:])
        )
    else:
        r = replace(r)
        object.__setattr__(r, "_registry_fingerprint", "a" * 64)
    with pytest.raises(ModelProjectionLedgerError):
        _compose(candidate, metric_result=m, currency_risk_result=r)


@pytest.mark.parametrize("change", ["coordinate", "type", "reference", "value"])
def test_substantive_candidate_validation_with_recomputed_binding(candidate, change):
    row = candidate.sheets[0].rows[0]
    field = row.fields[9]
    if change == "coordinate":
        field = replace(
            field, source_cell=replace(field.source_cell, coordinate="Z999")
        )
    elif change == "type":
        field = replace(field, source_cell=replace(field.source_cell, raw_value=True))
    elif change == "reference":
        field = replace(field, field_occurrence_id="forged")
    else:
        field = replace(field, normalized_value=17.0)
    row = replace(row, fields=(*row.fields[:9], field, *row.fields[10:]))
    sheet = replace(candidate.sheets[0], rows=(row, *candidate.sheets[0].rows[1:]))
    changed = replace(candidate, sheets=(sheet, candidate.sheets[1]))
    m, r = _results(candidate)
    with pytest.raises(ModelProjectionLedgerError):
        compose_model_projection_ledger(
            changed,
            metric_result=m,
            currency_risk_result=r,
            metric_projection=COMPATIBILITY,
            currency_risk_projection=PROFILE,
            expected_candidate_fingerprint=changed.candidate_fingerprint,
        )


@pytest.mark.parametrize("module", [metrics, risk, normalization])
def test_unsupported_versions_reject(candidate, monkeypatch, module):
    m, r = _results(candidate)
    monkeypatch.setattr(module, "CONTRACT_VERSION", 2)
    with pytest.raises(ModelProjectionLedgerError, match="UNSUPPORTED_VERSION"):
        compose_model_projection_ledger(
            candidate,
            metric_result=m,
            currency_risk_result=r,
            metric_projection=COMPATIBILITY,
            currency_risk_projection=PROFILE,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )


@pytest.mark.parametrize(
    "argument,value",
    [
        ("metric_projection", "DEFAULT"),
        ("currency_risk_projection", "DEFAULT"),
        ("expected_candidate_fingerprint", "a" * 64),
        ("metric_result", None),
        ("currency_risk_result", None),
    ],
)
def test_invalid_bindings_and_selections_reject(candidate, argument, value):
    with pytest.raises(ModelProjectionLedgerError):
        _compose(candidate, **{argument: value})


def test_valid_but_conflicting_selection_rejects(candidate):
    with pytest.raises(ModelProjectionLedgerError, match="PROJECTION_CHOICE_MISMATCH"):
        _compose(candidate, metric_projection=ORIGINAL)


def test_malformed_candidate_rejects_clearly(candidate):
    m, r = _results(candidate)
    with pytest.raises(ModelProjectionLedgerError, match="MALFORMED_CANDIDATE"):
        compose_model_projection_ledger(
            None,
            metric_result=m,
            currency_risk_result=r,
            metric_projection=COMPATIBILITY,
            currency_risk_projection=PROFILE,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )


def test_explicit_selection_no_override_and_deep_immutability(candidate):
    signature = inspect.signature(compose_model_projection_ledger)
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in signature.parameters.values()
    )
    assert not {"policy", "registry", "approval"} & set(signature.parameters)
    result = _compose(candidate)
    before = result.to_json()
    with pytest.raises(FrozenInstanceError):
        result.occurrences = ()
    with pytest.raises(FrozenInstanceError):
        result.occurrences[0].currency_risk.currency_risk.value = "Changed"
    with pytest.raises(FrozenInstanceError):
        result.original_candidate.sheets[0].rows[0].fields[
            0
        ].source_cell.raw_value = "Changed"
    payload = result.to_dict()
    payload["occurrences"].clear()
    payload["original_candidate"].clear()
    payload["evaluated_currency_risk_binding"]["anomaly_registry"].clear()
    assert result.to_json() == before
    payload = result.to_dict()
    fingerprint = payload.pop("ledger_fingerprint")
    assert fingerprint == canonical_fingerprint(payload)


def test_unknown_currency_states_fail_without_fallback(tmp_path):
    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, model_currency_risk="Unknown")
    )
    candidate = normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )
    m = project_model_metrics(
        candidate,
        projection=COMPATIBILITY,
        expected_candidate_fingerprint=candidate.candidate_fingerprint,
    )
    with pytest.raises(
        ModelProjectionLedgerError, match="UNAPPROVED_CURRENCY_RISK_STATE"
    ):
        compose_model_projection_ledger(
            candidate,
            metric_result=m,
            currency_risk_result=None,
            metric_projection=COMPATIBILITY,
            currency_risk_projection=PROFILE,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )


def test_empty_text_missing_type_evidence(tmp_path):
    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, model_currency_risk="Fedezve")
    )
    envelope = _cell(envelope, cell_type="text", biff_type_code=1, raw_value="")
    candidate = normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )
    result = _compose(candidate)
    field = result.occurrences[0].currency_risk.currency_risk
    assert field.value is None
    assert field.original.source_cell.cell_type == "text"
    assert field.original.source_cell.raw_value == ""


@pytest.mark.parametrize("header", metrics._METRIC_HEADERS)
@pytest.mark.parametrize("mode", [ORIGINAL, COMPATIBILITY])
def test_all_twelve_joint_zero_resolutions(tmp_path, header, mode):
    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, model_currency_risk="Nincs fedezve")
    )
    envelope = metric_cell(
        envelope, header, cell_type="number", biff_type_code=2, raw_value=-0.0
    )
    candidate = normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )
    result = _compose(candidate, mode)
    selected = next(
        metric
        for metric in result.occurrences[0].metrics.metrics
        if metric.original.header == header
    )
    assert selected.original.source_cell.raw_value == -0.0
    assert selected.value == (-0.0 if mode == ORIGINAL else None)
    assert selected.zero_policy_applied
    assert result.occurrences[0].currency_risk.currency_risk.value == "Unhedged"
    assert result.original_candidate.diagnostics == candidate.diagnostics


@pytest.mark.parametrize(
    "kind,code,value,error_text,disposition",
    [
        ("text", 1, "0", None, "REJECTED"),
        ("text", 1, "", None, "SOURCE_MISSING"),
        ("boolean", 4, False, None, "REJECTED"),
        ("date_serial", 3, 45000.0, None, "REJECTED"),
        ("error", 5, 7, "#DIV/0!", "REJECTED"),
        ("blank", 6, None, None, "SOURCE_MISSING"),
        ("empty", 0, None, None, "SOURCE_MISSING"),
        ("number", 2, float("inf"), None, "REJECTED"),
        ("number", 2, float("-inf"), None, "REJECTED"),
        ("number", 2, float("nan"), None, "REJECTED"),
    ],
)
def test_excluded_metric_states_remain_rejected_or_missing_evidence(
    tmp_path, kind, code, value, error_text, disposition
):
    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, model_currency_risk="Fedezve")
    )
    envelope = metric_cell(
        envelope,
        "YTD",
        cell_type=kind,
        biff_type_code=code,
        raw_value=value,
        error_text=error_text,
    )
    candidate = normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )
    result = _compose(candidate)
    metric = result.occurrences[0].metrics.metrics[0]
    assert metric.disposition == disposition
    if type(value) is float and math.isnan(value):
        assert math.isnan(metric.original.source_cell.raw_value)
    else:
        assert metric.original.source_cell.raw_value == value
    assert metric.original.source_cell.cell_type == kind
    assert not metric.zero_policy_applied
    assert result.original_candidate.diagnostics == candidate.diagnostics


def test_allocation_zero_and_unknown_original_classification_stay_evidence(tmp_path):
    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, model_currency_risk="Fedezve")
    )
    envelope = metric_cell(envelope, "Hányad (%)", raw_value=0.0)
    envelope = metric_cell(
        envelope, "Eszközosztály", raw_value="Unknown Hungarian label"
    )
    candidate = normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )
    result = _compose(candidate)
    original = result.occurrences[0].original
    assert original.fields[3].normalized_value == 0.0
    assert original.fields[4].normalized_value == "Unknown Hungarian label"
    assert original.classification.mapping_status == "NOT_APPLICABLE_TO_MODEL_ROLE"
    assert result.original_candidate == candidate


@pytest.mark.parametrize(
    "changes",
    [
        {"english_asset_class_candidate": "Bond"},
        {"english_sub_asset_class_candidate": "Global"},
        {"mapping_status": "APPROVED"},
        {"original_asset_class": "Changed"},
        {"original_sub_asset_class": "Changed"},
    ],
)
def test_recomputed_candidate_cannot_import_model_classification_authority(
    candidate, changes
):
    row = candidate.sheets[0].rows[0]
    row = replace(row, classification=replace(row.classification, **changes))
    sheet = replace(candidate.sheets[0], rows=(row, *candidate.sheets[0].rows[1:]))
    altered = replace(candidate, sheets=(sheet, candidate.sheets[1]))
    with pytest.raises(
        ModelProjectionLedgerError, match="MODEL_CLASSIFICATION_MISMATCH"
    ):
        _compose(altered)
