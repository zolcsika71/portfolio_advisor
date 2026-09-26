"""Synthetic tests for the non-admitting BIFF evidence-gate evaluator."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.workbook_source import biff_evidence_approval as subject

_RECOVERY_HASH = "1" * 64
_STRICT_HASH = "2" * 64
_SOURCE_HASH = "3" * 64
_INVENTORY_HASH = "4" * 64
_OVERLAP_HASH = "5" * 64
_EMPTY_OVERLAP_HASH = canonical_fingerprint([])


def test_recovery_and_formula_approvals_are_enforced_separately() -> None:
    report, policy = _report_and_policy()

    result = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "BOUNDED_EXCEPTION_GRANTED"
    assert result.recovery.satisfied is True
    assert result.formula_origin.outcome == "SUFFICIENT_FOR_RETAINED_BYTES"
    assert result.formula_origin.satisfied is True
    assert result.to_dict()["admission_approval"] == "NOT_GRANTED"
    assert result.to_dict()["evaluation_status"] == (
        "EVIDENCE_GATES_EVALUATED_NOT_ADMISSION"
    )
    assert result.to_dict()["evidence_scope"] == (
        "BOUND_HISTORICAL_REPORT_NOT_FRESH_WORKBOOK_INSPECTION"
    )
    assert result.to_dict()["fresh_workbook_inspection"] == "NOT_PERFORMED"
    source_identity = result.to_dict()["source_identity"]
    assert isinstance(source_identity, dict)
    assert source_identity["identity_basis"] == (
        "CALLER_SUPPLIED_IDENTITY_MATCHED_TO_BOUND_HISTORICAL_REPORT"
    )
    assert "no current workbook was inspected" in result.recovery.reasons[0].message
    assert (
        "no current workbook was inspected" in result.formula_origin.reasons[0].message
    )


def test_strict_workbook_does_not_receive_recovery_exception() -> None:
    report, policy = _report_and_policy()

    result = _evaluate_for_test(
        report,
        workbook_sha256=_STRICT_HASH,
        source_filename="strict.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "NOT_REQUIRED_STRICT_OPEN"
    assert result.recovery.reasons[0].code == (
        "STRICT_OPEN_RECOVERY_EXCEPTION_NOT_REQUIRED"
    )
    assert result.formula_origin.satisfied is True
    assert result.to_dict()["admission_approval"] == "NOT_GRANTED"


def test_result_is_deterministic_deeply_immutable_and_does_not_mutate_input() -> None:
    report, policy = _report_and_policy()
    input_before = bytes(report)

    first = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )
    second = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert report == input_before
    assert first.to_json() == second.to_json()
    assert first.evaluation_fingerprint == second.evaluation_fingerprint
    assert (
        first.evaluation_fingerprint
        == hashlib.sha256(canonical_json(first._payload()).encode()).hexdigest()
    )
    with pytest.raises(FrozenInstanceError):
        first.evidence_binding.report_sha256 = "0" * 64  # type: ignore[misc]
    detached = first.to_dict()
    detached["admission_approval"] = "GRANTED"
    assert first.to_dict()["admission_approval"] == "NOT_GRANTED"


@pytest.mark.parametrize("workbook_hash", ["9" * 64, "not-a-sha256"])
def test_unknown_or_malformed_workbook_hash_fails_both_gates(
    workbook_hash: str,
) -> None:
    report, policy = _report_and_policy()

    result = _evaluate_for_test(
        report,
        workbook_sha256=workbook_hash,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "REJECTED"
    assert result.formula_origin.outcome == "REJECTED"
    assert {reason.code for reason in result.recovery.reasons} & {
        "INVALID_WORKBOOK_SHA256",
        "WORKBOOK_HASH_OUTSIDE_APPROVED_SCOPE",
    }


def test_filename_inventory_mismatch_fails_both_gates() -> None:
    report, policy = _report_and_policy()

    result = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="renamed.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "REJECTED"
    assert result.formula_origin.outcome == "REJECTED"
    assert "SOURCE_FILENAME_MISMATCH" in _codes(result.recovery)


def test_changed_overlap_signature_rejects_only_recovery_gate() -> None:
    payload, records = _payload_and_records()
    recovery = _workbook(payload, _RECOVERY_HASH)
    allocation = _mapping(recovery["allocation"])
    allocation["overlap_sector_fingerprint"] = "6" * 64
    report, policy = _bind(payload, records)

    result = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "REJECTED"
    assert "RECOVERY_DEFECT_SIGNATURE_MISMATCH" in _codes(result.recovery)
    assert result.formula_origin.outcome == "SUFFICIENT_FOR_RETAINED_BYTES"


def test_caller_supplied_pass_verdict_cannot_replace_defect_evidence() -> None:
    payload, records = _payload_and_records()
    recovery = _workbook(payload, _RECOVERY_HASH)
    recovery["verdict"] = "INDEPENDENTLY_VERIFIED_EXAMINED_PROPERTIES"
    allocation = _mapping(recovery["allocation"])
    allocation["defect"] = "NONE"
    report, policy = _bind(payload, records)

    result = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "REJECTED"
    assert "WORKBOOK_VERDICT_MISMATCH" in _codes(result.recovery)
    assert "RECOVERY_DEFECT_SIGNATURE_MISMATCH" in _codes(result.recovery)


def test_caller_cannot_rewrite_historical_not_granted_fields() -> None:
    payload, records = _payload_and_records()
    payload["recovery_exception_approval"] = "GRANTED"
    payload["formula_origin_approval"] = "GRANTED"
    report, policy = _bind(payload, records)

    result = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "REJECTED"
    assert result.formula_origin.outcome == "REJECTED"
    assert "HISTORICAL_APPROVAL_FIELD_CHANGED" in _codes(result.recovery)
    assert result.to_dict()["admission_approval"] == "NOT_GRANTED"


def test_incomplete_sheet_eof_rejects_both_gates() -> None:
    payload, records = _payload_and_records()
    recovery = _workbook(payload, _RECOVERY_HASH)
    sheets = _sequence(recovery["sheet_evidence"])
    _mapping(sheets[1])["reached_eof"] = False
    report, policy = _bind(payload, records)

    result = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "REJECTED"
    assert result.formula_origin.outcome == "REJECTED"
    assert "SHEET_IDENTITY_OR_COVERAGE_MISMATCH" in _codes(result.recovery)


def test_nonzero_formula_record_rejects_formula_but_not_recovery() -> None:
    payload, records = _payload_and_records()
    recovery = _workbook(payload, _RECOVERY_HASH)
    recovery["formula_record_count"] = 1
    recovery["formula_record_examples"] = [
        {"record": "FORMULA", "sheet": " shortlist", "coordinate": "T2"}
    ]
    recovery["formula_status"] = "FORMULA_RECORDS_PRESENT"
    sheets = _sequence(recovery["sheet_evidence"])
    _mapping(sheets[1])["formula_records"] = 1
    report, policy = _bind(payload, records)

    result = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "BOUNDED_EXCEPTION_GRANTED"
    assert result.formula_origin.outcome == "REJECTED"
    assert "FORMULA_RECORDS_PRESENT" in _codes(result.formula_origin)


def test_recomputed_fingerprint_cannot_overcome_wrong_report_binding() -> None:
    report, policy = _report_and_policy()
    payload = json.loads(report)
    payload["report_status"] = "CALLER_SAYS_PASS"
    payload["report_fingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "report_fingerprint"}
    )
    changed = _serialize(payload)

    result = _evaluate_for_test(
        changed,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "REJECTED"
    assert result.formula_origin.outcome == "REJECTED"
    assert "REPORT_BYTES_MISMATCH" in _codes(result.recovery)
    assert "UNSUPPORTED_REPORT_STATUS" in _codes(result.recovery)


@pytest.mark.parametrize(
    "malformed",
    [
        b"not-json",
        b'{"duplicate":1,"duplicate":2}',
        b'{"non_finite":NaN}',
        b"[]",
    ],
)
def test_malformed_evidence_fails_closed(malformed: bytes) -> None:
    _, policy = _report_and_policy()

    result = _evaluate_for_test(
        malformed,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert result.recovery.outcome == "REJECTED"
    assert result.formula_origin.outcome == "REJECTED"
    assert "MALFORMED_EVIDENCE_REPORT" in _codes(result.recovery)
    assert result.to_dict()["admission_approval"] == "NOT_GRANTED"


def test_wrong_contract_and_dependency_bindings_fail_closed() -> None:
    payload, records = _payload_and_records()
    _mapping(payload["contract"])["version"] = 2
    _mapping(payload["tool"])["python_calamine_version"] = "999"
    report, policy = _bind(payload, records)

    result = _evaluate_for_test(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
        policy=policy,
    )

    assert "UNSUPPORTED_REPORT_CONTRACT" in _codes(result.recovery)
    assert "TOOL_BINDING_MISMATCH" in _codes(result.formula_origin)


def test_public_evaluator_rejects_unapproved_synthetic_report() -> None:
    report, _ = _report_and_policy()

    result = subject.evaluate_biff_xls_evidence(
        report,
        workbook_sha256=_RECOVERY_HASH,
        source_filename="recovery.xls",
    )

    assert result.recovery.outcome == "REJECTED"
    assert result.formula_origin.outcome == "REJECTED"
    assert "REPORT_BYTES_MISMATCH" in _codes(result.recovery)


def test_public_api_exposes_no_policy_override_or_synthetic_evaluator() -> None:
    signature = inspect.signature(subject.evaluate_biff_xls_evidence)

    assert tuple(signature.parameters) == (
        "evidence_report",
        "workbook_sha256",
        "source_filename",
    )
    assert not hasattr(subject, "_evaluate_with_policy")


def test_mutable_report_buffer_is_rejected() -> None:
    report, policy = _report_and_policy()

    with pytest.raises(TypeError, match="immutable bytes"):
        _evaluate_for_test(
            bytearray(report),  # type: ignore[arg-type]
            workbook_sha256=_RECOVERY_HASH,
            source_filename="recovery.xls",
            policy=policy,
        )


def test_published_registry_contains_exact_approved_scopes() -> None:
    assert len(subject._APPROVED_POLICY.workbooks) == 33
    assert (
        sum(not item.strict_open for item in subject._APPROVED_POLICY.workbooks) == 27
    )
    assert sum(item.strict_open for item in subject._APPROVED_POLICY.workbooks) == 6
    assert len({item.sha256 for item in subject._APPROVED_POLICY.workbooks}) == 33
    assert isinstance(subject._APPROVED_POLICY.aggregate_json, str)
    assert isinstance(subject._FORMULA_SCOPE, tuple)
    with pytest.raises(FrozenInstanceError):
        subject._APPROVED_POLICY.report_sha256 = "0" * 64  # type: ignore[misc]


def _evaluate_for_test(
    evidence_report: bytes,
    *,
    workbook_sha256: str,
    source_filename: str,
    policy: subject._ApprovalPolicy,
) -> subject.BiffEvidenceGateEvaluation:
    """Patch a private constant only inside synthetic tests; production has no seam."""

    with patch.object(subject, "_APPROVED_POLICY", policy):
        return subject.evaluate_biff_xls_evidence(
            evidence_report,
            workbook_sha256=workbook_sha256,
            source_filename=source_filename,
        )


def _report_and_policy() -> tuple[bytes, subject._ApprovalPolicy]:
    payload, records = _payload_and_records()
    return _bind(payload, records)


def _payload_and_records() -> tuple[
    dict[str, object], tuple[subject._ApprovedWorkbook, ...]
]:
    records = (
        subject._ApprovedWorkbook(
            "recovery.xls",
            _RECOVERY_HASH,
            False,
            1,
            1,
            41,
            2,
            2,
            3,
            _OVERLAP_HASH,
        ),
        subject._ApprovedWorkbook(
            "strict.xls",
            _STRICT_HASH,
            True,
            1,
            1,
            41,
            0,
            None,
            None,
            _EMPTY_OVERLAP_HASH,
        ),
    )
    workbooks = [_workbook_payload(item) for item in records]
    aggregate = {
        "cell_property_mismatches": 0,
        "data_cell_types": {"text": 82},
        "data_fields": 82,
        "formula_records": 0,
        "header_fields": 82,
        "model_rows": 2,
        "recovery_required": 1,
        "shortlist_rows": 2,
        "strict_open": 1,
        "value_mismatches": 0,
        "workbooks": 2,
    }
    payload: dict[str, object] = {
        "admission_approval": "NOT_GRANTED",
        "aggregate": aggregate,
        "contract": {
            "name": "BIFF_XLS_RECOVERY_FORMULA_EVIDENCE_REPORT",
            "version": 1,
        },
        "discrepancies": [],
        "formula_origin_approval": "NOT_GRANTED",
        "formula_scope": dict(subject._FORMULA_SCOPE),
        "inventory": {
            "contract": {
                "name": "BIFF_XLS_PROCESSED_EXPECTED_INVENTORY",
                "version": 1,
            },
            "expected_aggregate": {
                "data_fields": 82,
                "formula_records": 0,
                "model_rows": 2,
                "recovery_required": 1,
                "shortlist_rows": 2,
                "strict_open": 1,
                "workbooks": 2,
            },
            "inventory_sha256": _INVENTORY_HASH,
        },
        "recovery_exception_approval": "NOT_GRANTED",
        "report_status": "READ_ONLY_EVIDENCE_NOT_ADMISSION_APPROVAL",
        "tool": {
            "name": "portfolio_advisor.workbook_source.biff_evidence",
            "published_parser_name": "portfolio_advisor.workbook_source.biff_xls",
            "published_parser_version": 1,
            "python_calamine_version": "0.8.2",
            "source_sha256": _SOURCE_HASH,
            "version": 1,
            "xlrd_version": "2.0.2",
        },
        "workbooks": workbooks,
    }
    return payload, records


def _bind(
    payload: dict[str, object], records: tuple[subject._ApprovedWorkbook, ...]
) -> tuple[bytes, subject._ApprovalPolicy]:
    candidate = copy.deepcopy(payload)
    candidate.pop("report_fingerprint", None)
    candidate["report_fingerprint"] = canonical_fingerprint(candidate)
    report = _serialize(candidate)
    policy = subject._ApprovalPolicy(
        report_sha256=hashlib.sha256(report).hexdigest(),
        report_fingerprint=str(candidate["report_fingerprint"]),
        verifier_source_sha256=_SOURCE_HASH,
        inventory_sha256=_INVENTORY_HASH,
        workbooks=records,
        aggregate_json=canonical_json(candidate["aggregate"]),
    )
    return report, policy


def _workbook_payload(item: subject._ApprovedWorkbook) -> dict[str, object]:
    strict = item.strict_open
    allocation = {
        "defect": "NONE" if strict else "ROOT_MINISTREAM_WORKBOOK_CHAIN_OVERLAP",
        "overlap_first_sector": item.overlap_first_sector,
        "overlap_last_sector": item.overlap_last_sector,
        "overlap_sector_count": item.overlap_sector_count,
        "overlap_sector_fingerprint": item.overlap_sector_fingerprint,
        "root_declared_size": 512,
        "root_start_sector": 1,
        "workbook_declared_size": 300,
        "workbook_start_sector": 2,
    }
    sheets = [
        _sheet(0, " modell portfóliók", "MODEL_PORTFOLIO", 21, 1, "U", 100, 200),
        _sheet(1, " shortlist", "ANALYTICAL_SHORTLIST", 20, 1, "T", 200, 300),
    ]
    total = item.data_fields + 41
    return {
        "allocation": allocation,
        "byte_length": 4096,
        "comparison": {
            "calamine_collapsed_missing": 0,
            "calamine_exact_values": total,
            "cell_property_mismatches": 0,
            "compared_cells_including_headers": total,
            "raw_cell_properties_exact": total,
            "value_mismatches": 0,
        },
        "data_cell_types": {"text": item.data_fields},
        "data_fields": item.data_fields,
        "filename": item.filename,
        "format_key_counts_including_headers": {"0": total},
        "formula_record_count": 0,
        "formula_record_examples": [],
        "formula_status": "NO_FORMULA_RECORDS_IN_EXACT_BYTES",
        "header_fields": 41,
        "model_rows": item.model_rows,
        "recovery_open": True,
        "sha256": item.sha256,
        "sheet_evidence": sheets,
        "shortlist_rows": item.shortlist_rows,
        "strict_error": None if strict else subject._STRICT_ERROR,
        "strict_open": strict,
        "trailing_workbook_padding_bytes": 0,
        "unreadable_regions": [],
        "verdict": (
            "INDEPENDENTLY_VERIFIED_EXAMINED_PROPERTIES"
            if strict
            else "RECOVERY_EXCEPTION_APPROVAL_REQUIRED"
        ),
    }


def _sheet(
    index: int,
    name: str,
    role: str,
    headers: int,
    rows: int,
    last_column: str,
    start: int,
    end: int,
) -> dict[str, object]:
    explicit = headers * (rows + 1)
    return {
        "absent_cells": 0,
        "coordinate_coverage": f"A1:{last_column}{rows + 1}",
        "data_fields": headers * rows,
        "exact_name": name,
        "explicit_cell_records": explicit,
        "first_data_row": 2,
        "formula_records": 0,
        "header_fields": headers,
        "last_data_row": rows + 1,
        "raw_record_count": explicit + 4,
        "reached_eof": True,
        "role": role,
        "rows": rows,
        "sheet_index": index,
        "substream_end": end,
        "substream_start": start,
        "visibility": "visible",
    }


def _workbook(payload: dict[str, object], workbook_hash: str) -> dict[str, object]:
    workbooks = _sequence(payload["workbooks"])
    return next(
        _mapping(item)
        for item in workbooks
        if _mapping(item).get("sha256") == workbook_hash
    )


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def _serialize(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode()


def _codes(verdict: subject.EvidenceGateVerdict) -> set[str]:
    return {reason.code for reason in verdict.reasons}
