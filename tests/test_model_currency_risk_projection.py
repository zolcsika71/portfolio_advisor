"""Synthetic-only currency-risk projection tests; no private evidence inputs.

The pinned anomaly proof surface is tested with synthetic metadata. Complete
public-API anomaly paths use a test-local synthetic registry, restored afterwards;
no production override or historical-candidate impersonation is authorized.
"""

from __future__ import annotations

import inspect
import unicodedata
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.workbook_source import (
    MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1 as PROFILE,
)
from portfolio_advisor.workbook_source import (
    MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1,
    ModelCurrencyRiskProjectionError,
    project_model_currency_risk,
    project_model_metrics,
)
from portfolio_advisor.workbook_source import model_currency_risk_projection as risk
from portfolio_advisor.workbook_source.biff_xls import (
    ANALYTICAL_SHORTLIST_ROLE,
    MODEL_PORTFOLIO_ROLE,
    BiffXlsEnvelope,
    SourceRow,
    parse_biff_xls,
)
from portfolio_advisor.workbook_source.normalization import (
    BiffXlsNormalizationCandidate,
    NormalizedFieldCandidate,
    normalize_biff_xls_envelope,
)
from tests.fixtures.biff_xls_fixture import write_biff_fixture


@pytest.fixture
def envelope(tmp_path: Path) -> BiffXlsEnvelope:
    return parse_biff_xls(
        write_biff_fixture(
            tmp_path,
            model_currency_risk="Fedezve",
            duplicate_shortlist_first_row=True,
        )
    )


def _normalize(envelope: BiffXlsEnvelope) -> BiffXlsNormalizationCandidate:
    return normalize_biff_xls_envelope(
        envelope,
        expected_workbook_sha256=envelope.workbook_sha256,
    )


def _cell(
    envelope: BiffXlsEnvelope,
    *,
    role: str = MODEL_PORTFOLIO_ROLE,
    occurrence: int = 1,
    **changes: Any,
) -> BiffXlsEnvelope:
    sheet = envelope.sheet(role)
    index = next(
        i for i, c in enumerate(sheet.headers) if c.raw_value == "Devizakockázat"
    )
    row = sheet.rows[occurrence - 1]
    cells = list(row.cells)
    cells[index] = replace(cells[index], **changes)
    row = replace(row, cells=tuple(cells))
    payload = row.to_dict()
    payload.pop("row_fingerprint")
    row = replace(row, row_fingerprint=canonical_fingerprint(payload))
    rows = list(sheet.rows)
    rows[occurrence - 1] = row
    sheet = replace(sheet, rows=tuple(rows))
    payload = sheet.to_dict()
    payload.pop("sheet_fingerprint")
    sheet = replace(sheet, sheet_fingerprint=canonical_fingerprint(payload))
    return replace(
        envelope, sheets=tuple(sheet if s.role == role else s for s in envelope.sheets)
    )


def _project(
    candidate: BiffXlsNormalizationCandidate,
) -> risk.ModelCurrencyRiskProjection:
    return project_model_currency_risk(
        candidate,
        projection=PROFILE,
        expected_candidate_fingerprint=candidate.candidate_fingerprint,
    )


def _synthetic_anomaly_case(
    envelope: BiffXlsEnvelope,
) -> tuple[BiffXlsNormalizationCandidate, risk._ApprovedWorkbook]:
    """Build typed synthetic states, not a reinspection of any XLS bytes.

    Only the tests replace the private registry. All substantive public
    validators and the fixed proof-shape matcher remain active, without a
    policy argument or a production bypass. The actual approved hashes are
    never reused for this synthetic workbook.
    """
    sheet = envelope.sheet(MODEL_PORTFOLIO_ROLE)
    values: tuple[str | float, ...] = (
        ("VALUE!",) * 6 + (2.0,) * 9 + (3.0,) * 3 + (4.0,) * 6
    )
    rows = []
    for index, value in enumerate(values, 1):
        physical_row = index + 1
        reference = f"BIFF_XLS_V1:{envelope.workbook_sha256}:0:{physical_row}"
        cells = tuple(
            replace(
                cell,
                source_row=physical_row,
                coordinate=f"{cell.coordinate.rstrip('0123456789')}{physical_row}",
            )
            for cell in sheet.rows[0].cells
        )
        currency = replace(
            cells[7],
            cell_type="text" if type(value) is str else "number",
            biff_type_code=1 if type(value) is str else 2,
            raw_value=value,
            xf_index=15,
            format_key=0,
            number_format="General",
        )
        row = SourceRow(
            index, physical_row, reference, (*cells[:7], currency, *cells[8:]), ""
        )
        payload = row.to_dict()
        payload.pop("row_fingerprint")
        rows.append(replace(row, row_fingerprint=canonical_fingerprint(payload)))
    headers = tuple(
        replace(cell, xf_index=16, format_key=0, number_format="General")
        if cell.raw_value == "Devizakockázat"
        else cell
        for cell in sheet.headers
    )
    sheet = replace(sheet, headers=headers, rows=tuple(rows))
    payload = sheet.to_dict()
    payload.pop("sheet_fingerprint")
    sheet = replace(sheet, sheet_fingerprint=canonical_fingerprint(payload))
    candidate = _normalize(replace(envelope, sheets=(sheet, envelope.sheets[1])))
    binding = candidate.source_binding
    assert binding.workbook_sha256 not in {
        w.workbook_sha256 for w in risk._APPROVED_WORKBOOKS
    }
    workbook = risk._ApprovedWorkbook(
        binding.workbook_sha256,
        binding.source_filename,
        binding.snapshot_date,
        binding.byte_length,
        binding.envelope_fingerprint,
        candidate.candidate_fingerprint,
        candidate.sheets[0].sheet_fingerprint,
        candidate.sheets[1].sheet_fingerprint,
        len(candidate.sheets[0].rows),
        len(candidate.sheets[1].rows),
        tuple(
            risk._AnomalyEntry(
                row.source_row,
                value,
                row.row_fingerprint,
                canonical_fingerprint({"synthetic_only": row.occurrence_id}),
            )
            for row, value in zip(candidate.sheets[0].rows, values, strict=True)
        ),
    )
    return candidate, workbook


def test_public_anomaly_success_uses_test_local_registry_only(
    envelope: BiffXlsEnvelope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, synthetic = _synthetic_anomaly_case(envelope)
    before = candidate.to_json()
    production_registry = risk._APPROVED_WORKBOOKS
    production_identity = canonical_fingerprint(risk._registry_identity())
    # The unmodified production scope rejects the same source states.
    with pytest.raises(
        ModelCurrencyRiskProjectionError, match="UNAPPROVED_CURRENCY_RISK_STATE"
    ):
        _project(candidate)
    with monkeypatch.context() as local:
        local.setattr(risk, "_APPROVED_WORKBOOKS", (synthetic,))
        first, second = _project(candidate), _project(candidate)
        assert first.to_json() == second.to_json()
        assert len(first.rows) == 24
        assert first.admission_approval == "NOT_GRANTED"
        assert first.original_candidate is candidate
        assert first.original_candidate.diagnostics is candidate.diagnostics
        assert first.original_candidate.sheets[1] is candidate.sheets[1]
        for row, source_row, entry in zip(
            first.rows, candidate.sheets[0].rows, synthetic.entries, strict=True
        ):
            field = row.currency_risk
            assert row.original is source_row
            assert field.original is source_row.fields[7]
            assert field.value is None
            assert field.reason == "LEGACY_ANOMALY_AS_NONE_WITHOUT_INTERPRETATION"
            assert field.disposition == "KNOWN_ANOMALY_UNINTERPRETED"
            assert field.evidence_entry_fingerprint == entry.evidence_entry_fingerprint
            assert (
                field.policy_resolution
                == risk.MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1
            )
        assert len({row.original.occurrence_id for row in first.rows}) == 24
        assert candidate.to_json() == before
    assert risk._APPROVED_WORKBOOKS is production_registry
    assert canonical_fingerprint(risk._registry_identity()) == production_identity
    with pytest.raises(
        ModelCurrencyRiskProjectionError, match="UNAPPROVED_CURRENCY_RISK_STATE"
    ):
        _project(candidate)


def test_projection_retains_the_registry_identity_evaluated_at_creation(
    envelope: BiffXlsEnvelope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, synthetic = _synthetic_anomaly_case(envelope)
    with monkeypatch.context() as local:
        local.setattr(risk, "_APPROVED_WORKBOOKS", (synthetic,))
        result = _project(candidate)
        serialized = result.to_json()
        fingerprint = result.projection_fingerprint
    # Restoring test-local policy state must not retroactively change an existing
    # immutable result's claimed registry or canonical fingerprint.
    assert result.to_json() == serialized
    assert result.projection_fingerprint == fingerprint
    with pytest.raises(FrozenInstanceError):
        result._registry_fingerprint = "0" * 64  # type: ignore[misc]
    assert (
        "_registry_fingerprint"
        not in inspect.signature(risk.ModelCurrencyRiskProjection).parameters
    )
    assert (
        "_registry_entries"
        not in inspect.signature(risk.ModelCurrencyRiskProjection).parameters
    )


@pytest.mark.parametrize(
    "damage",
    [
        "candidate_fingerprint",
        "envelope_fingerprint",
        "raw_value",
        "format",
        "occurrence",
        "missing_entry",
        "extra_entry",
        "duplicate_entry",
        "reordered_entry",
    ],
)
def test_public_anomaly_rejection_keeps_all_substantive_checks_active(
    envelope: BiffXlsEnvelope,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    candidate, synthetic = _synthetic_anomaly_case(envelope)
    if damage == "candidate_fingerprint":
        synthetic = replace(synthetic, candidate_fingerprint="0" * 64)
    elif damage == "envelope_fingerprint":
        synthetic = replace(synthetic, envelope_fingerprint="0" * 64)
    elif damage in ("raw_value", "format", "occurrence"):
        row = candidate.sheets[0].rows[0]
        field = row.fields[7]
        if damage == "raw_value":
            field = replace(
                field, source_cell=replace(field.source_cell, raw_value=False)
            )
        elif damage == "format":
            field = replace(
                field, source_cell=replace(field.source_cell, number_format="0.00%")
            )
        else:
            row = replace(row, occurrence_id="fabricated")
        row = replace(row, fields=(*row.fields[:7], field, *row.fields[8:]))
        row = replace(
            row,
            row_fingerprint=canonical_fingerprint(
                {
                    "occurrence_index": row.occurrence_index,
                    "source_row": row.source_row,
                    "source_reference": row.source_reference,
                    "cells": [f.source_cell.to_dict() for f in row.fields],
                }
            ),
        )
        candidate = replace(
            candidate,
            sheets=(
                replace(candidate.sheets[0], rows=(row, *candidate.sheets[0].rows[1:])),
                candidate.sheets[1],
            ),
        )
        # A self-consistent candidate hash does not bypass typed validation or
        # the independently pinned source format / occurrence proof.
        synthetic = replace(
            synthetic, candidate_fingerprint=candidate.candidate_fingerprint
        )
    elif damage == "missing_entry":
        synthetic = replace(synthetic, entries=synthetic.entries[1:])
    elif damage == "reordered_entry":
        synthetic = replace(synthetic, entries=tuple(reversed(synthetic.entries)))
    elif damage in ("extra_entry", "duplicate_entry"):
        extra = (
            synthetic.entries[0]
            if damage == "duplicate_entry"
            else replace(synthetic.entries[0], source_row=99)
        )
        synthetic = replace(synthetic, entries=(*synthetic.entries, extra))
    with monkeypatch.context() as local:
        local.setattr(risk, "_APPROVED_WORKBOOKS", (synthetic,))
        with pytest.raises(ModelCurrencyRiskProjectionError):
            _project(candidate)


@pytest.mark.parametrize(
    ("raw", "english"),
    [
        ("Fedezve", "Hedged"),
        ("Nincs fedezve", "Unhedged"),
        ("Részben fedezve", "Partially Hedged"),
        (" \tFEDEZVE\n", "Hedged"),
        (" nincs FEDEZVE ", "Unhedged"),
        (unicodedata.normalize("NFD", "RÉSZBEN FEDEZVE"), "Partially Hedged"),
    ],
)
def test_approved_text_lookup_preserves_originals_and_diagnostics(
    envelope: BiffXlsEnvelope,
    raw: str,
    english: str,
) -> None:
    candidate = _normalize(_cell(envelope, raw_value=raw))
    before = candidate.to_json()
    result = _project(candidate)
    field = result.rows[0].currency_risk
    assert (field.value, field.disposition, field.policy_resolution) == (
        english,
        "APPROVED_TEXT_TRANSLATION",
        risk.MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1,
    )
    assert field.lookup_key == unicodedata.normalize("NFC", raw).strip().casefold()
    assert field.original.source_cell.raw_value == raw
    assert field.original.normalized_value == raw.strip()
    assert field.original.field_occurrence_id.endswith(":CELL:H2:HEADER:Devizakockázat")
    assert result.original_candidate is candidate
    assert result.original_candidate.diagnostics is candidate.diagnostics
    assert candidate.to_json() == before
    assert field.evidence_entry_fingerprint is None
    assert "UNAPPROVED_CURRENCY_RISK_TRANSLATION" in result.to_json()
    if raw != unicodedata.normalize("NFC", raw):
        assert "ANOMALOUS_CURRENCY_RISK_VALUE" in result.to_json()
        # This resolves only lexical translation, not an extra anomaly exception.
        assert (
            field.policy_resolution != risk.MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1
        )
    assert result.admission_approval == "NOT_GRANTED"


@pytest.mark.parametrize(
    ("kind", "code", "raw"),
    [("blank", 6, None), ("empty", 0, None), ("text", 1, ""), ("text", 1, "  \t")],
)
def test_missing_states_remain_distinct(
    envelope: BiffXlsEnvelope,
    kind: str,
    code: int,
    raw: Any,
) -> None:
    candidate = _normalize(
        _cell(envelope, cell_type=kind, biff_type_code=code, raw_value=raw)
    )
    field = _project(candidate).rows[0].currency_risk
    assert (field.value, field.disposition, field.reason) == (
        None,
        "SOURCE_MISSING",
        "ORIGINAL_SOURCE_MISSING",
    )
    assert (
        field.original.source_cell.cell_type,
        field.original.source_cell.raw_value,
    ) == (kind, raw)
    assert field.policy_resolution is None
    assert field.evidence_entry_fingerprint is None
    if kind == "text":
        assert "EMPTY_TEXT_AS_MISSING" in candidate.to_json()


@pytest.mark.parametrize(
    ("kind", "code", "raw", "error"),
    [
        *(
            ("text", 1, label, None)
            for label in (
                "Hedged",
                "Unhedged",
                "Partially Hedged",
                "VALUE!",
                "value!",
                "2",
                "3",
                "4",
                "0",
                "részben fedezett",
                "Reszben fedezve",
                "fedezett",
                "unknown",
            )
        ),
        ("number", 2, 2.0, None),
        ("number", 2, 3.0, None),
        ("number", 2, 4.0, None),
        ("number", 2, 0.0, None),
        ("number", 2, float("inf"), None),
        ("number", 2, float("nan"), None),
        ("boolean", 4, False, None),
        ("date_serial", 3, 2.0, None),
        ("error", 5, 15, "#VALUE!"),
    ],
)
def test_unknown_or_excluded_states_fail_closed_without_a_none_fallback(
    envelope: BiffXlsEnvelope,
    kind: str,
    code: int,
    raw: Any,
    error: str | None,
) -> None:
    candidate = _normalize(
        _cell(
            envelope,
            cell_type=kind,
            biff_type_code=code,
            raw_value=raw,
            error_text=error,
        )
    )
    before = candidate.to_json()
    with pytest.raises(
        ModelCurrencyRiskProjectionError, match="UNAPPROVED_CURRENCY_RISK_STATE.*H2"
    ):
        _project(candidate)
    assert candidate.to_json() == before


def test_model_only_all_duplicates_holdings_and_other_fields_survive(
    envelope: BiffXlsEnvelope,
) -> None:
    # Identical source values in two physical model occurrences; retain both.
    candidate = _normalize(
        _cell(
            envelope,
            role=ANALYTICAL_SHORTLIST_ROLE,
            raw_value="Unapproved shortlist value",
        )
    )
    original = candidate.to_json()
    metric_before = project_model_metrics(
        candidate,
        projection=MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1,
        expected_candidate_fingerprint=candidate.candidate_fingerprint,
    )
    result = _project(candidate)
    assert len(result.rows) == len(candidate.sheets[0].rows) == 2
    assert len(result.original_candidate.sheets[1].rows) == 3
    assert (
        result.rows[0].original.occurrence_id != result.rows[1].original.occurrence_id
    )
    for row, input_row in zip(result.rows, candidate.sheets[0].rows, strict=True):
        assert row.original is input_row
        assert row.currency_risk.original in input_row.fields
        assert row.currency_risk.value == "Hedged"
    assert result.original_candidate.sheets[1] is candidate.sheets[1]
    assert candidate.to_json() == original
    assert (
        metric_before.to_json()
        == project_model_metrics(
            result.original_candidate,
            projection=MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        ).to_json()
    )


def test_explicit_profile_fingerprint_and_no_public_policy_override(
    envelope: BiffXlsEnvelope,
) -> None:
    candidate = _normalize(envelope)
    with pytest.raises(TypeError):
        project_model_currency_risk(
            candidate, expected_candidate_fingerprint=candidate.candidate_fingerprint
        )  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        project_model_currency_risk(candidate, projection=PROFILE)  # type: ignore[call-arg]
    assert tuple(inspect.signature(project_model_currency_risk).parameters) == (
        "candidate",
        "projection",
        "expected_candidate_fingerprint",
    )
    with pytest.raises(
        ModelCurrencyRiskProjectionError, match="CANDIDATE_FINGERPRINT_MISMATCH"
    ):
        project_model_currency_risk(
            candidate, projection=PROFILE, expected_candidate_fingerprint="0" * 64
        )


@pytest.mark.parametrize(
    "choice",
    [
        None,
        "",
        "original",
        "compatibility",
        "MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V2",
        [],
        True,
    ],
)
def test_unsupported_selection(envelope: BiffXlsEnvelope, choice: Any) -> None:
    candidate = _normalize(envelope)
    with pytest.raises(
        ModelCurrencyRiskProjectionError, match="UNSUPPORTED_PROJECTION"
    ):
        project_model_currency_risk(
            candidate,
            projection=choice,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )


def test_deterministic_deeply_immutable_pure_result(
    envelope: BiffXlsEnvelope, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _normalize(envelope)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("projection attempted I/O")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    first, second = _project(candidate), _project(candidate)
    assert first.to_json() == second.to_json()
    assert first.projection_fingerprint == canonical_fingerprint(first._payload())
    assert first.to_dict()["admission_approval"] == "NOT_GRANTED"
    assert "2026-10-07" in first.to_json()
    assert "FORMULA_ORIGIN_NOT_ESTABLISHED" in first.to_json()
    assert "UNRESOLVED_MODEL_ZERO_SEMANTICS" in first.to_json()
    assert first.to_dict()["model_sheet"]["exact_name"] == " modell portfóliók"  # type: ignore[index]
    for obj, name in (
        (first, "rows"),
        (first.rows[0], "currency_risk"),
        (first.rows[0].currency_risk, "value"),
        (first.original_candidate, "diagnostics"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, None)
    payload = first.to_dict()
    payload.clear()
    assert first.to_json() == second.to_json()


@pytest.mark.parametrize(
    "damage",
    [
        "typed_value",
        "coordinates",
        "occurrence",
        "field_id",
        "normalization",
        "dropped_diagnostic",
        "mutable",
        "version",
        "sheet_name",
        "header",
        "format",
        "hash",
    ],
)
def test_recomputed_fingerprints_do_not_hide_malformed_structure(
    envelope: BiffXlsEnvelope, damage: str
) -> None:
    candidate = _normalize(envelope)
    sheet, row = candidate.sheets[0], candidate.sheets[0].rows[0]
    field = row.fields[7]
    if damage == "mutable":
        candidate = replace(candidate, diagnostics=list(candidate.diagnostics))  # type: ignore[arg-type]
    elif damage == "version":
        candidate = replace(
            candidate,
            source_binding=replace(candidate.source_binding, source_contract_version=2),
        )
    elif damage == "hash":
        candidate = replace(
            candidate,
            source_binding=replace(candidate.source_binding, workbook_sha256="a" * 64),
        )
    elif damage == "dropped_diagnostic":
        candidate = replace(
            candidate,
            diagnostics=tuple(
                d
                for d in candidate.diagnostics
                if d.code != "UNAPPROVED_CURRENCY_RISK_TRANSLATION"
            ),
        )
    else:
        if damage == "sheet_name":
            sheet = replace(sheet, exact_name="modell portfóliók")
        elif damage == "occurrence":
            row = replace(row, occurrence_id="fabricated")
        else:
            if damage == "typed_value":
                field = replace(
                    field, source_cell=replace(field.source_cell, raw_value=False)
                )
            elif damage == "coordinates":
                field = replace(
                    field, source_cell=replace(field.source_cell, coordinate="H999")
                )
            elif damage == "field_id":
                field = replace(field, field_occurrence_id="fabricated")
            elif damage == "normalization":
                field = replace(field, normalized_value="Unhedged")
            elif damage == "header":
                field = replace(field, header="Deviza")
            elif damage == "format":
                field = replace(
                    field, source_cell=replace(field.source_cell, xf_index=-1)
                )
            row = replace(
                row,
                fields=tuple(
                    field if f.header == "Devizakockázat" else f for f in row.fields
                ),
            )
        # Even an updated source-row hash plus candidate hash is not sufficient.
        row = replace(
            row,
            row_fingerprint=canonical_fingerprint(
                {
                    "occurrence_index": row.occurrence_index,
                    "source_row": row.source_row,
                    "source_reference": row.source_reference,
                    "cells": [f.source_cell.to_dict() for f in row.fields],
                }
            ),
        )
        sheet = replace(sheet, rows=(row, *sheet.rows[1:]))
        candidate = replace(candidate, sheets=(sheet, candidate.sheets[1]))
    with pytest.raises(ModelCurrencyRiskProjectionError):
        _project(candidate)


_PROOFS = tuple(
    risk._expected_proof(w, e) for w in risk._APPROVED_WORKBOOKS for e in w.entries
)


@pytest.mark.parametrize(
    "proof", _PROOFS, ids=lambda p: f"{p.snapshot_date}:{p.source_cell.coordinate}"
)
def test_all_exact_anomaly_proofs_and_reason_bearing_none(
    proof: risk._AnomalyProof,
) -> None:
    # Synthetic proof metadata at the private matching boundary, not a fabricated
    # full historical candidate. The public API still requires full validation.
    entry = risk._match_anomaly(proof)
    assert entry is not None
    field = NormalizedFieldCandidate(
        "Devizakockázat",
        "original_currency_risk",
        None,
        "TEXT" if proof.source_cell.cell_type == "text" else "NULL",
        "UNRESOLVED_POLICY",
        "VALUE!" if proof.source_cell.cell_type == "text" else None,
        proof.field_occurrence_id,
        None,
        proof.source_cell,
    )
    result = risk._project_anomaly(field, proof)
    assert result.value is None
    assert result.reason == "LEGACY_ANOMALY_AS_NONE_WITHOUT_INTERPRETATION"
    assert result.original is field
    assert result.policy_resolution == risk.MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1
    assert result.evidence_entry_fingerprint == entry.evidence_entry_fingerprint


@pytest.mark.parametrize(
    "damage",
    [
        "workbook_sha256",
        "source_filename",
        "snapshot_date",
        "envelope_fingerprint",
        "candidate_fingerprint",
        "role",
        "sheet_name",
        "sheet_index",
        "sheet_fingerprint",
        "header",
        "source_row",
        "occurrence_index",
        "source_reference",
        "occurrence_id",
        "row_fingerprint",
        "field_occurrence_id",
        "raw",
        "type",
        "format",
        "xf",
        "error",
        "header_cell",
        "bool_index",
        "int_raw",
    ],
)
def test_changed_anomaly_binding_is_never_authorized(damage: str) -> None:
    proof = _PROOFS[3]  # numeric 2.0, never text "2" or integer 2
    cell = proof.source_cell
    if damage in ("raw", "type", "format", "xf", "error", "int_raw"):
        variants: dict[str, dict[str, Any]] = {
            "raw": {"raw_value": 3.0},
            "type": {"cell_type": "text", "biff_type_code": 1, "raw_value": "2"},
            "format": {"number_format": "0.00%"},
            "xf": {"xf_index": 16},
            "error": {"error_text": "#VALUE!"},
            "int_raw": {"raw_value": 2},
        }
        proof = replace(proof, source_cell=replace(cell, **variants[damage]))
    elif damage == "header_cell":
        proof = replace(proof, header_cell=replace(proof.header_cell, format_key=1))
    elif damage == "bool_index":
        proof = replace(proof, sheet_index=False)
    else:
        original = getattr(proof, damage)
        proof_changes: dict[str, Any] = {
            damage: original + 1 if type(original) is int else "changed",
        }
        proof = replace(proof, **proof_changes)
    assert risk._match_anomaly(proof) is None


def test_registry_identity_is_exact_immutable_and_not_a_test_override() -> None:
    assert (
        len(_PROOFS)
        == len({(p.workbook_sha256, p.source_cell.coordinate) for p in _PROOFS})
        == 24
    )
    assert sum(p.source_cell.cell_type == "text" for p in _PROOFS) == 6
    assert [
        sum(p.source_cell.raw_value == v for p in _PROOFS) for v in (2.0, 3.0, 4.0)
    ] == [9, 3, 6]
    assert (
        risk.TYPED_MANIFEST_SHA256
        == "c4d678e0c90abac49337bf1f7547816b45134b6e6e7886ab4aacf1ee594f123a"
    )
    registry = risk._registry_identity()
    assert (
        canonical_fingerprint(registry)
        == "45bdd829e31a5f902f576e65ed1e83d167f38b80899d4ef9a2fbc6b694d5e325"
    )
    assert canonical_fingerprint(registry) == canonical_fingerprint(
        risk._registry_identity()
    )
    registry.clear()
    assert len(risk._registry_identity()["workbooks"]) == 2  # type: ignore[arg-type]
    with pytest.raises(FrozenInstanceError):
        risk._APPROVED_WORKBOOKS[0].candidate_fingerprint = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("candidate", [None, {}, [], object()])
def test_untyped_inputs_fail_clearly(candidate: Any) -> None:
    with pytest.raises(ModelCurrencyRiskProjectionError, match="MALFORMED_CANDIDATE"):
        project_model_currency_risk(
            candidate, projection=PROFILE, expected_candidate_fingerprint="0" * 64
        )


@pytest.mark.parametrize("binding", [None, True, "", "ABC", "A" * 64])
def test_malformed_expected_fingerprints(
    envelope: BiffXlsEnvelope, binding: Any
) -> None:
    with pytest.raises(ModelCurrencyRiskProjectionError, match="MALFORMED_CANDIDATE"):
        project_model_currency_risk(
            _normalize(envelope),
            projection=PROFILE,
            expected_candidate_fingerprint=binding,
        )


@pytest.mark.parametrize(
    "name,value",
    [
        ("CONTRACT_NAME", "OTHER_SOURCE"),
        ("CONTRACT_VERSION", 2),
        ("PARSER_NAME", "other.parser"),
        ("PARSER_VERSION", 2),
    ],
)
def test_future_source_contract_is_not_silently_accepted(
    envelope: BiffXlsEnvelope, monkeypatch: pytest.MonkeyPatch, name: str, value: Any
) -> None:
    candidate = _normalize(envelope)
    monkeypatch.setattr(risk.source, name, value)
    with pytest.raises(
        ModelCurrencyRiskProjectionError, match="UNSUPPORTED_SOURCE_CONTRACT"
    ):
        _project(candidate)


def test_serialized_policy_bindings_are_explicit(envelope: BiffXlsEnvelope) -> None:
    payload = _project(_normalize(envelope)).to_dict()
    assert payload["projection"] == {
        "name": PROFILE,
        "version": 1,
        "approved_on": "2026-10-07",
    }
    identity = payload["mapping_identity"]
    assert isinstance(identity, dict)
    fingerprint = identity.pop("fingerprint")
    assert canonical_fingerprint(identity) == fingerprint
    assert identity["policy"] == risk.MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1
    assert identity["lookup_operations"] == ["NFC", "trim", "casefold"]
    assert identity["mappings"] == [
        {"key": "fedezve", "value": "Hedged"},
        {"key": "nincs fedezve", "value": "Unhedged"},
        {"key": "részben fedezve", "value": "Partially Hedged"},
    ]
    registry = payload["anomaly_registry"]
    assert isinstance(registry, dict)
    assert registry["entries"] == 24
    assert registry["typed_manifest_sha256"] == risk.TYPED_MANIFEST_SHA256
    assert registry["policy"] == risk.MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1


@pytest.mark.parametrize("approved", risk._APPROVED_WORKBOOKS)
def test_public_api_rejects_synthetic_impersonation_of_historical_candidates(
    tmp_path: Path,
    approved: risk._ApprovedWorkbook,
) -> None:
    envelope = parse_biff_xls(
        write_biff_fixture(
            tmp_path, filename=approved.source_filename, model_currency_risk="Fedezve"
        )
    )
    envelope = replace(envelope, workbook_sha256=approved.workbook_sha256)
    # Re-key complete synthetic source references and row hashes, then normalize.
    sheets = []
    for sheet in envelope.sheets:
        rows = []
        for row in sheet.rows:
            row = replace(
                row,
                source_reference=f"BIFF_XLS_V1:{approved.workbook_sha256}:{sheet.sheet_index}:{row.source_row}",
            )
            payload = row.to_dict()
            payload.pop("row_fingerprint")
            rows.append(replace(row, row_fingerprint=canonical_fingerprint(payload)))
        sheet = replace(sheet, rows=tuple(rows))
        payload = sheet.to_dict()
        payload.pop("sheet_fingerprint")
        sheets.append(replace(sheet, sheet_fingerprint=canonical_fingerprint(payload)))
    candidate = _normalize(replace(envelope, sheets=tuple(sheets)))
    with pytest.raises(
        ModelCurrencyRiskProjectionError,
        match="APPROVED_ANOMALY_WORKBOOK_BINDING_MISMATCH",
    ):
        _project(candidate)
