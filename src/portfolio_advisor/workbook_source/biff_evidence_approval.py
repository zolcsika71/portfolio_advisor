"""Pure enforcement of the approved retained-workbook evidence gates.

The evaluator consumes the already-produced verifier report as in-memory bytes.
It neither reads workbooks nor grants admission.  The two approvals are checked
independently, but both are bound to the exact historical report and normative
workbook registry approved on 2026-09-26.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json

CONTRACT_NAME: Final = "BIFF_XLS_EVIDENCE_GATE_EVALUATION"
CONTRACT_VERSION: Final = 1
EVALUATION_STATUS: Final = "EVIDENCE_GATES_EVALUATED_NOT_ADMISSION"
ADMISSION_APPROVAL: Final = "NOT_GRANTED"
EVIDENCE_SCOPE: Final = "BOUND_HISTORICAL_REPORT_NOT_FRESH_WORKBOOK_INSPECTION"
FRESH_WORKBOOK_INSPECTION: Final = "NOT_PERFORMED"
APPROVAL_DATE: Final = "2026-09-26"
RECOVERY_APPROVAL_ID: Final = "HASH_BOUND_RECOVERY_EXCEPTION_2026_09_26"
FORMULA_APPROVAL_ID: Final = "RETAINED_BYTES_FORMULA_SUFFICIENCY_2026_09_26"

_REPORT_SHA256: Final = (
    "892e9748de25237be9151c7cae320eff9459eb6455a490aff403e81570539582"
)
_REPORT_FINGERPRINT: Final = (
    "c3e1756bedb2306e0f49cb90601a0591b0a6c1918bfaa9e9507ab4d5cf599b3e"
)
_VERIFIER_SOURCE_SHA256: Final = (
    "cb002a39988ba22e14f0810421efda221a6e951dfb0dfbe86c45732ada47fcf0"
)
_INVENTORY_SHA256: Final = (
    "f37d4c2267d89c0073c60eb1ab1b43115bb3a1d1de3692c08974ea1dba4573b4"
)
_STRICT_ERROR: Final = "CompDocError: Workbook corruption: seen[2] == 4"
_DEFECT: Final = "ROOT_MINISTREAM_WORKBOOK_CHAIN_OVERLAP"
_NO_DEFECT: Final = "NONE"
_FORMULA_STATUS: Final = "NO_FORMULA_RECORDS_IN_EXACT_BYTES"
_RECOVERY_REQUIRED_VERDICT: Final = "RECOVERY_EXCEPTION_APPROVAL_REQUIRED"
_VERIFIED_VERDICT: Final = "INDEPENDENTLY_VERIFIED_EXAMINED_PROPERTIES"
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
_FORMULA_SCOPE: Final = (
    (
        "does_not_prove",
        (
            "It does not prove pre-export authoring history, whether values were "
            "previously calculated and pasted, or any state not encoded in the "
            "exact retained bytes."
        ),
    ),
    (
        "proves",
        (
            "The exact retained Workbook streams contain the reported BIFF "
            "formula-related record inventory in the two complete worksheet "
            "substreams. Zero means those bytes contain no worksheet formula "
            "records or formula-result STRING records."
        ),
    ),
)

RecoveryOutcome = Literal[
    "BOUNDED_EXCEPTION_GRANTED", "NOT_REQUIRED_STRICT_OPEN", "REJECTED"
]
FormulaOutcome = Literal["SUFFICIENT_FOR_RETAINED_BYTES", "REJECTED"]


@dataclass(frozen=True)
class EvidenceDecisionReason:
    """Stable, actionable explanation for one gate verdict."""

    code: str
    message: str
    evidence_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "evidence_path": self.evidence_path,
        }


@dataclass(frozen=True)
class EvidenceGateVerdict:
    """One independently evaluated evidence gate."""

    gate: Literal["RECOVERY_EXCEPTION", "FORMULA_ORIGIN_SUFFICIENCY"]
    outcome: RecoveryOutcome | FormulaOutcome
    approval_id: str
    approval_date: str
    reasons: tuple[EvidenceDecisionReason, ...]

    @property
    def satisfied(self) -> bool:
        return self.outcome in {
            "BOUNDED_EXCEPTION_GRANTED",
            "NOT_REQUIRED_STRICT_OPEN",
            "SUFFICIENT_FOR_RETAINED_BYTES",
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "outcome": self.outcome,
            "satisfied": self.satisfied,
            "approval_id": self.approval_id,
            "approval_date": self.approval_date,
            "reasons": [reason.to_dict() for reason in self.reasons],
        }


@dataclass(frozen=True)
class EvidenceReportBinding:
    """Observed and approved identities for the historical evidence report."""

    report_sha256: str
    expected_report_sha256: str
    report_fingerprint: str | None
    expected_report_fingerprint: str
    verifier_source_sha256: str | None
    expected_verifier_source_sha256: str
    inventory_sha256: str | None
    expected_inventory_sha256: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "report_sha256": self.report_sha256,
            "expected_report_sha256": self.expected_report_sha256,
            "report_fingerprint": self.report_fingerprint,
            "expected_report_fingerprint": self.expected_report_fingerprint,
            "verifier_source_sha256": self.verifier_source_sha256,
            "expected_verifier_source_sha256": self.expected_verifier_source_sha256,
            "inventory_sha256": self.inventory_sha256,
            "expected_inventory_sha256": self.expected_inventory_sha256,
        }


@dataclass(frozen=True)
class BiffEvidenceGateEvaluation:
    """Immutable non-admitting result for the two evidence gates."""

    source_filename: str
    workbook_sha256: str
    evidence_binding: EvidenceReportBinding
    recovery: EvidenceGateVerdict
    formula_origin: EvidenceGateVerdict

    def _payload(self) -> dict[str, object]:
        return {
            "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
            "evaluation_status": EVALUATION_STATUS,
            "admission_approval": ADMISSION_APPROVAL,
            "evidence_scope": EVIDENCE_SCOPE,
            "fresh_workbook_inspection": FRESH_WORKBOOK_INSPECTION,
            "source_identity": {
                "filename": self.source_filename,
                "workbook_sha256": self.workbook_sha256,
                "identity_basis": (
                    "CALLER_SUPPLIED_IDENTITY_MATCHED_TO_BOUND_HISTORICAL_REPORT"
                ),
            },
            "evidence_binding": self.evidence_binding.to_dict(),
            "recovery": self.recovery.to_dict(),
            "formula_origin": self.formula_origin.to_dict(),
        }

    @property
    def evaluation_fingerprint(self) -> str:
        return canonical_fingerprint(self._payload())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._payload(),
            "evaluation_fingerprint": self.evaluation_fingerprint,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class _ApprovedWorkbook:
    filename: str
    sha256: str
    strict_open: bool
    model_rows: int
    shortlist_rows: int
    data_fields: int
    overlap_sector_count: int
    overlap_first_sector: int | None
    overlap_last_sector: int | None
    overlap_sector_fingerprint: str


@dataclass(frozen=True)
class _ApprovalPolicy:
    report_sha256: str
    report_fingerprint: str
    verifier_source_sha256: str
    inventory_sha256: str
    workbooks: tuple[_ApprovedWorkbook, ...]
    aggregate_json: str


_APPROVED_WORKBOOK_ROWS: Final = (
    (
        "PB_Modell_Portfoliok_es_Shortlist_20240702.xls",
        "4643359b2693764ce24d3ed8dc985b2ee40d104e25a27ebed852d94ff82e0004",
        False,
        165,
        322,
        9905,
        396,
        2,
        397,
        "11f66306607ecc99b62928297a91eb86dd23c5534f406ac88bbcb79196df5e99",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20240718.xls",
        "1162ad9340eae704cbaa059c54de41cf2dace581236cc5b78fd110362c0634a5",
        False,
        165,
        322,
        9905,
        396,
        2,
        397,
        "11f66306607ecc99b62928297a91eb86dd23c5534f406ac88bbcb79196df5e99",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20240809.xls",
        "c051421872b63fe73fd87f29087d9dcf2c938a55df6b369fc5a65a92581e84db",
        False,
        165,
        322,
        9905,
        396,
        2,
        397,
        "11f66306607ecc99b62928297a91eb86dd23c5534f406ac88bbcb79196df5e99",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20240823.xls",
        "b299cc7b3aed7ed4c8d7cb21fc2ef1dff5dc36deba455c671f0e3970ce920021",
        False,
        165,
        323,
        9925,
        397,
        2,
        398,
        "2dc73e0d5a90558143f18202f60896098376667c4876ed958cef5f12a21767c5",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20240902.xls",
        "dfa41fb6dc1c9a9c659161226ae53dd741c6a678a155b807db74151ec973cca5",
        False,
        165,
        323,
        9925,
        397,
        2,
        398,
        "2dc73e0d5a90558143f18202f60896098376667c4876ed958cef5f12a21767c5",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20240917.xls",
        "4ece94b61d396a861745032251eb85990ca84dff42f24622124834635c83235a",
        False,
        165,
        323,
        9925,
        397,
        2,
        398,
        "2dc73e0d5a90558143f18202f60896098376667c4876ed958cef5f12a21767c5",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20240930.xls",
        "f8318124002feb7804364eb68fcba6a74a9788d52bb223ec134f29bb814d6faf",
        False,
        162,
        325,
        9902,
        396,
        2,
        397,
        "11f66306607ecc99b62928297a91eb86dd23c5534f406ac88bbcb79196df5e99",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20241007.xls",
        "646b0b323e1f8523b5e6fa466c7b89a608bc96576e70014b9e9d9d01f3d030f7",
        True,
        169,
        328,
        10109,
        0,
        None,
        None,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20241017.xls",
        "fadecf0acb478cb4ab4596d1cd509390f0a1d221ee811e05528e5ad6cc173abe",
        False,
        169,
        328,
        10109,
        405,
        2,
        406,
        "704a670114e1e9ce3d2aed353ff80ed256fbd70a1bbf552c64ea4deb648ab85f",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20241105.xls",
        "b5ff8fef0b4ea778ed4e6ffc661100fdea82b656f03d7b5c58a5f58cff9ec560",
        False,
        169,
        328,
        10109,
        404,
        2,
        405,
        "c6daa6dc7177ac104255b5712e1b9aa40c0ae0ef1b5a8a37b1dc3807c14f1914",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20241122.xls",
        "1fe00c6e2c52778cb16b3fd3a698224c672a835ae918c556a2add1c8b51a9ecf",
        False,
        169,
        331,
        10169,
        406,
        2,
        407,
        "62ec1b1c7110eeaf3b55c4fef5b032dac255eb39a373283e6125096a29cb6c9c",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20241203.xls",
        "a294fac8c5fd0f4021203484fc1f5b2bf5c67f03ff7346689d716c8e60eb891e",
        False,
        166,
        331,
        10106,
        404,
        2,
        405,
        "c6daa6dc7177ac104255b5712e1b9aa40c0ae0ef1b5a8a37b1dc3807c14f1914",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20241220.xls",
        "5b0bbd39c85fcb5982a901ff929f4d0698086aff64dd531b4309e80d2a0bfd13",
        True,
        166,
        331,
        10106,
        0,
        None,
        None,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250115.xls",
        "8a8023a9aa962506c5f5af7e7e89e0f35bfe7ea2c81b7bd3cc8f4b5ec6ab2008",
        False,
        166,
        336,
        10206,
        409,
        2,
        410,
        "55bf9ace3cb7a5e0c8da728b42e61ca5addb5c36fb2415d03e570d965583e419",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250203.xls",
        "996916e943b50076a3e31ce7d4d58f9c395f79c4f44bc52eb14cd7a8a5c83e1f",
        True,
        166,
        336,
        10206,
        0,
        None,
        None,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250303.xls",
        "2d8bba121219fbcf63d128dcc474915d94e63dfbeb4432f487eb9eeaaa4ffcea",
        True,
        166,
        311,
        9706,
        0,
        None,
        None,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250307.xls",
        "3d372d7bba9f7c51ebabdbafe186bf48da876c334dfec9101e3e39417320fb5d",
        True,
        155,
        308,
        9415,
        0,
        None,
        None,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250401.xls",
        "dc62ee8f966be6f16dc6063a0d49f1d8db15ef280ac28d4c6d9dcb1d61cd998f",
        False,
        155,
        305,
        9355,
        374,
        2,
        375,
        "98d9499f9b340ec3a507ee312133243ee27b3c4b380815fef699f27fa0310276",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250414.xls",
        "b6c58dd7b731e92c3b452a9b761f86653349b3ab6b209b7000fd3cf663a3625f",
        False,
        155,
        310,
        9455,
        379,
        2,
        380,
        "1809e093c5a7a638928a15fd088e1b73f6bb97932b0d4935c5b5a2c559fe17ec",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250509.xls",
        "a74151e567b5a4b095f302e19411ffac1d191d794a0a1de5898a85f2268ebbed",
        False,
        155,
        311,
        9475,
        382,
        2,
        383,
        "3254472ab31869fade9ec288ddf637bfdb514f37991d4b3a2ce493d3c2a751d0",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250519.xls",
        "867096ed3ee82283c267cda3ec49f02caa4f0f96144c3faaeb11ed620344374f",
        False,
        155,
        312,
        9495,
        380,
        2,
        381,
        "0aec8b61b5dfb905f6a61da08d096d86dd2c03ee98232aef5ed82ffee8a53fae",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250603.xls",
        "7959bac98498473fd7c29e7647945e50b2a78e20d18ed93cf0c79ebc8fd320e8",
        False,
        155,
        312,
        9495,
        380,
        2,
        381,
        "0aec8b61b5dfb905f6a61da08d096d86dd2c03ee98232aef5ed82ffee8a53fae",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250604.xls",
        "4e691f2afeac69b2ba63e8803840d5bcd7890a476c41b87b0afb624b30867c67",
        True,
        155,
        317,
        9595,
        0,
        None,
        None,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250613.xls",
        "b7f2d9f8346dabbda3ff0facbb136fdee92e90845f50002ea447b2de5020db8c",
        False,
        155,
        317,
        9595,
        385,
        2,
        386,
        "2338b538bbdef548c972d7b9563d5cd224b22f8c764ece8b56cbe5e366a508d5",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250703.xls",
        "c636586a06f2e7862ce2af33b8f810b6759a1dc50def440ac7094afa38a3bc9e",
        False,
        155,
        319,
        9635,
        386,
        2,
        387,
        "cbd4de7b7d08402b92a2a994e1422c72965e4e0d8e2995b098e10c1517dc69d9",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250812.xls",
        "ff49b7244b1c5c8eecbc935fd00922ce8bbbf9df10d87676aefe6182d096b825",
        False,
        155,
        319,
        9635,
        386,
        2,
        387,
        "cbd4de7b7d08402b92a2a994e1422c72965e4e0d8e2995b098e10c1517dc69d9",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250825.xls",
        "c5f5b1bd0945942cfd42b8f4c4b99b8e16554c754bbd8f681f76269be027b69e",
        False,
        155,
        319,
        9635,
        387,
        2,
        388,
        "7811d02617cd2e8cd479e50abad3228b936bdc69a9db0537e2f4277c24afd206",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20250910.xls",
        "08d7d242b0965ad8b166f5a545853d9598ad6cf8b7e43500c004d10d3b32e583",
        False,
        152,
        324,
        9672,
        388,
        2,
        389,
        "2e309c2ddef75fe894cd592652265204d313561990eeae16137072cbaccd745f",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20260119.xls",
        "239a564d4afd17f9ad7a78d13923b4f508214db54957e532466be912899b3d98",
        False,
        152,
        326,
        9712,
        390,
        2,
        391,
        "fd52e6b89eaaf9ce1c88531e015928d955bbbb7ca4cba0142aa0d08adc00443c",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20260401.xls",
        "6b244e04ccb4efd1830cddf188008e7e7ec7e200f169fd1583dd0993908b36cb",
        False,
        154,
        358,
        10394,
        419,
        2,
        420,
        "0a69f8a393012b80dba18d7485b4b3afb3581b5c2b34c891b4c91de187582656",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20260706.xls",
        "fea2ca91d597c520d45cee2de8767be96e32b38f6e5720843f1b0c8b4701cc76",
        False,
        154,
        383,
        10894,
        441,
        2,
        442,
        "595b9a2966a681e43e454778c848438ae0d46434cd9bfc1dedf5f10909cc002c",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20260818.xls",
        "646940a4447fc9287097b8ee1705886b0040fc7af2e488c0054960bcf9d99be5",
        False,
        154,
        386,
        10954,
        443,
        2,
        444,
        "80e37a82e3b9a722fb70333f0cf187d364fecf73b41eb6e0890028f17ed98fb6",
    ),
    (
        "PB_Modell_Portfoliok_es_Shortlist_20260826.xls",
        "852f697d73112a53fcd75946c16dbc71c5505eea3f35c43d4681f7fbabd34c73",
        False,
        154,
        387,
        10974,
        444,
        2,
        445,
        "20395c0654865f8011d03f390058bf2e8d175970db50d0d4dc45cb0f6016ba25",
    ),
)

_APPROVED_WORKBOOKS: Final = tuple(
    _ApprovedWorkbook(*row) for row in _APPROVED_WORKBOOK_ROWS
)
_EXPECTED_AGGREGATE_JSON: Final = canonical_json(
    {
        "cell_property_mismatches": 0,
        "data_cell_types": {
            "blank": 1945,
            "empty": 390,
            "number": 197872,
            "text": 127396,
        },
        "data_fields": 327603,
        "formula_records": 0,
        "header_fields": 1353,
        "model_rows": 5283,
        "recovery_required": 27,
        "shortlist_rows": 10833,
        "strict_open": 6,
        "value_mismatches": 0,
        "workbooks": 33,
    }
)
_APPROVED_POLICY: Final = _ApprovalPolicy(
    report_sha256=_REPORT_SHA256,
    report_fingerprint=_REPORT_FINGERPRINT,
    verifier_source_sha256=_VERIFIER_SOURCE_SHA256,
    inventory_sha256=_INVENTORY_SHA256,
    workbooks=_APPROVED_WORKBOOKS,
    aggregate_json=_EXPECTED_AGGREGATE_JSON,
)

_REPORT_KEYS: Final = frozenset(
    {
        "admission_approval",
        "aggregate",
        "contract",
        "discrepancies",
        "formula_origin_approval",
        "formula_scope",
        "inventory",
        "recovery_exception_approval",
        "report_fingerprint",
        "report_status",
        "tool",
        "workbooks",
    }
)
_WORKBOOK_KEYS: Final = frozenset(
    {
        "allocation",
        "byte_length",
        "comparison",
        "data_cell_types",
        "data_fields",
        "filename",
        "format_key_counts_including_headers",
        "formula_record_count",
        "formula_record_examples",
        "formula_status",
        "header_fields",
        "model_rows",
        "recovery_open",
        "sha256",
        "sheet_evidence",
        "shortlist_rows",
        "strict_error",
        "strict_open",
        "trailing_workbook_padding_bytes",
        "unreadable_regions",
        "verdict",
    }
)


def evaluate_biff_xls_evidence(
    evidence_report: bytes,
    *,
    workbook_sha256: str,
    source_filename: str,
) -> BiffEvidenceGateEvaluation:
    """Evaluate the approved historical evidence without reading current files."""

    policy = _APPROVED_POLICY
    if not isinstance(evidence_report, bytes):
        raise TypeError("evidence_report must be immutable bytes")

    actual_report_sha256 = hashlib.sha256(evidence_report).hexdigest()
    report: dict[str, object] | None
    parse_reason: EvidenceDecisionReason | None = None
    try:
        loaded = json.loads(
            evidence_report,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
        if not isinstance(loaded, dict):
            raise TypeError("top-level JSON value is not an object")
        report = cast(dict[str, object], loaded)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        report = None
        parse_reason = _reason(
            "MALFORMED_EVIDENCE_REPORT",
            f"evidence report is not strict JSON: {error}",
            "$",
        )

    binding = _binding(report, actual_report_sha256, policy)
    common_reasons = [parse_reason] if parse_reason is not None else []
    approved: _ApprovedWorkbook | None = None
    workbook: Mapping[str, object] | None = None
    if report is not None:
        common_reasons.extend(
            _validate_common_report(report, actual_report_sha256, policy)
        )
        approved = next(
            (item for item in policy.workbooks if item.sha256 == workbook_sha256),
            None,
        )
        if not _SHA256_RE.fullmatch(workbook_sha256):
            common_reasons.append(
                _reason(
                    "INVALID_WORKBOOK_SHA256",
                    "workbook SHA-256 must be 64 lowercase hexadecimal characters",
                    "$.source_identity.workbook_sha256",
                )
            )
        elif approved is None:
            common_reasons.append(
                _reason(
                    "WORKBOOK_HASH_OUTSIDE_APPROVED_SCOPE",
                    "workbook hash is not in the approved 33-hash registry",
                    "$.source_identity.workbook_sha256",
                )
            )
        elif source_filename != approved.filename:
            common_reasons.append(
                _reason(
                    "SOURCE_FILENAME_MISMATCH",
                    "source filename does not match the approved inventory entry",
                    "$.source_identity.filename",
                )
            )
        if approved is not None:
            workbook = _find_workbook(report, workbook_sha256)
            if workbook is None:
                common_reasons.append(
                    _reason(
                        "WORKBOOK_EVIDENCE_MISSING",
                        "bound report does not contain exactly one entry for the requested hash",
                        "$.workbooks",
                    )
                )
            elif workbook.get("filename") != source_filename:
                common_reasons.append(
                    _reason(
                        "REPORT_FILENAME_MISMATCH",
                        "report filename does not match the requested source identity",
                        "$.workbooks[].filename",
                    )
                )

    common = tuple(reason for reason in common_reasons if reason is not None)
    if common or approved is None or workbook is None:
        return _result(source_filename, workbook_sha256, binding, common, common)

    structural = tuple(_validate_workbook_structure(workbook, approved))
    recovery_reasons = structural + tuple(_validate_recovery(workbook, approved))
    formula_reasons = structural + tuple(_validate_formula(workbook))

    if recovery_reasons:
        recovery = _rejected_recovery(recovery_reasons)
    elif approved.strict_open:
        recovery = EvidenceGateVerdict(
            gate="RECOVERY_EXCEPTION",
            outcome="NOT_REQUIRED_STRICT_OPEN",
            approval_id=RECOVERY_APPROVAL_ID,
            approval_date=APPROVAL_DATE,
            reasons=(
                _reason(
                    "STRICT_OPEN_RECOVERY_EXCEPTION_NOT_REQUIRED",
                    "the bound historical report records strict opening for the approved hash; no recovery exception is granted and no current workbook was inspected",
                    "$.workbooks[].strict_open",
                ),
            ),
        )
    else:
        recovery = EvidenceGateVerdict(
            gate="RECOVERY_EXCEPTION",
            outcome="BOUNDED_EXCEPTION_GRANTED",
            approval_id=RECOVERY_APPROVAL_ID,
            approval_date=APPROVAL_DATE,
            reasons=(
                _reason(
                    "HASH_BOUND_RECOVERY_EXCEPTION_GRANTED",
                    "the bound historical report entry for the exact approved hash records the sole permitted defect and every required comparison; no current workbook was inspected",
                    "$.workbooks[].allocation",
                ),
            ),
        )

    formula = (
        _rejected_formula(formula_reasons)
        if formula_reasons
        else EvidenceGateVerdict(
            gate="FORMULA_ORIGIN_SUFFICIENCY",
            outcome="SUFFICIENT_FOR_RETAINED_BYTES",
            approval_id=FORMULA_APPROVAL_ID,
            approval_date=APPROVAL_DATE,
            reasons=(
                _reason(
                    "ZERO_COVERED_FORMULA_RECORDS_IN_RETAINED_BYTES",
                    "the bound historical report records complete inspection and zero covered formula-related records for the exact retained bytes; no current workbook was inspected and upstream authoring history is not established",
                    "$.workbooks[].formula_record_count",
                ),
            ),
        )
    )
    return BiffEvidenceGateEvaluation(
        source_filename=source_filename,
        workbook_sha256=workbook_sha256,
        evidence_binding=binding,
        recovery=recovery,
        formula_origin=formula,
    )


def _binding(
    report: Mapping[str, object] | None,
    actual_report_sha256: str,
    policy: _ApprovalPolicy,
) -> EvidenceReportBinding:
    fingerprint = report.get("report_fingerprint") if report is not None else None
    tool = report.get("tool") if report is not None else None
    inventory = report.get("inventory") if report is not None else None
    return EvidenceReportBinding(
        report_sha256=actual_report_sha256,
        expected_report_sha256=policy.report_sha256,
        report_fingerprint=fingerprint if isinstance(fingerprint, str) else None,
        expected_report_fingerprint=policy.report_fingerprint,
        verifier_source_sha256=_nested_string(tool, "source_sha256"),
        expected_verifier_source_sha256=policy.verifier_source_sha256,
        inventory_sha256=_nested_string(inventory, "inventory_sha256"),
        expected_inventory_sha256=policy.inventory_sha256,
    )


def _validate_common_report(
    report: Mapping[str, object], actual_sha256: str, policy: _ApprovalPolicy
) -> list[EvidenceDecisionReason]:
    reasons: list[EvidenceDecisionReason] = []
    _require(
        reasons,
        actual_sha256 == policy.report_sha256,
        "REPORT_BYTES_MISMATCH",
        "report bytes do not match the approved historical report SHA-256",
        "$",
    )
    _require(
        reasons,
        frozenset(report) == _REPORT_KEYS,
        "UNSUPPORTED_REPORT_STRUCTURE",
        "report keys differ from the supported v1 contract",
        "$",
    )
    _require(
        reasons,
        report.get("contract")
        == {"name": "BIFF_XLS_RECOVERY_FORMULA_EVIDENCE_REPORT", "version": 1},
        "UNSUPPORTED_REPORT_CONTRACT",
        "report contract name or version is not approved",
        "$.contract",
    )
    _require(
        reasons,
        report.get("report_status") == "READ_ONLY_EVIDENCE_NOT_ADMISSION_APPROVAL",
        "UNSUPPORTED_REPORT_STATUS",
        "historical report status is missing or contradictory",
        "$.report_status",
    )
    for field in (
        "admission_approval",
        "recovery_exception_approval",
        "formula_origin_approval",
    ):
        _require(
            reasons,
            report.get(field) == "NOT_GRANTED",
            "HISTORICAL_APPROVAL_FIELD_CHANGED",
            f"historical {field} must remain NOT_GRANTED",
            f"$.{field}",
        )
    observed_fingerprint = report.get("report_fingerprint")
    payload = dict(report)
    payload.pop("report_fingerprint", None)
    _require(
        reasons,
        isinstance(observed_fingerprint, str)
        and canonical_fingerprint(payload) == observed_fingerprint,
        "REPORT_FINGERPRINT_INVALID",
        "canonical report fingerprint does not match report content",
        "$.report_fingerprint",
    )
    _require(
        reasons,
        observed_fingerprint == policy.report_fingerprint,
        "REPORT_FINGERPRINT_UNAPPROVED",
        "report fingerprint is outside the approved evidence binding",
        "$.report_fingerprint",
    )
    _require(
        reasons,
        report.get("formula_scope") == dict(_FORMULA_SCOPE),
        "FORMULA_SCOPE_MISMATCH",
        "formula coverage or limitation statement differs from the approved report",
        "$.formula_scope",
    )

    tool = report.get("tool")
    expected_tool = {
        "name": "portfolio_advisor.workbook_source.biff_evidence",
        "version": 1,
        "source_sha256": policy.verifier_source_sha256,
        "published_parser_name": "portfolio_advisor.workbook_source.biff_xls",
        "published_parser_version": 1,
        "xlrd_version": "2.0.2",
        "python_calamine_version": "0.8.2",
    }
    _require(
        reasons,
        tool == expected_tool,
        "TOOL_BINDING_MISMATCH",
        "verifier, parser, source, or dependency identity differs from the approved evidence",
        "$.tool",
    )
    inventory = report.get("inventory")
    expected_inventory_aggregate = {
        "workbooks": 33,
        "strict_open": 6,
        "recovery_required": 27,
        "model_rows": 5283,
        "shortlist_rows": 10833,
        "data_fields": 327603,
        "formula_records": 0,
    }
    if len(policy.workbooks) != 33:
        expected_inventory_aggregate = {
            "workbooks": len(policy.workbooks),
            "strict_open": sum(item.strict_open for item in policy.workbooks),
            "recovery_required": sum(not item.strict_open for item in policy.workbooks),
            "model_rows": sum(item.model_rows for item in policy.workbooks),
            "shortlist_rows": sum(item.shortlist_rows for item in policy.workbooks),
            "data_fields": sum(item.data_fields for item in policy.workbooks),
            "formula_records": 0,
        }
    expected_inventory = {
        "contract": {"name": "BIFF_XLS_PROCESSED_EXPECTED_INVENTORY", "version": 1},
        "expected_aggregate": expected_inventory_aggregate,
        "inventory_sha256": policy.inventory_sha256,
    }
    _require(
        reasons,
        inventory == expected_inventory,
        "INVENTORY_BINDING_MISMATCH",
        "inventory identity or expected totals differ from the approved evidence",
        "$.inventory",
    )
    _require(
        reasons,
        canonical_json(report.get("aggregate")) == policy.aggregate_json,
        "AGGREGATE_MISMATCH",
        "report aggregate differs from the approved evidence totals",
        "$.aggregate",
    )
    _require(
        reasons,
        report.get("discrepancies") == [],
        "REPORT_CONTAINS_DISCREPANCIES",
        "approved evidence requires an empty discrepancy list",
        "$.discrepancies",
    )

    workbooks = report.get("workbooks")
    if not isinstance(workbooks, list):
        reasons.append(
            _reason(
                "MALFORMED_WORKBOOK_EVIDENCE",
                "workbooks must be a JSON array",
                "$.workbooks",
            )
        )
    else:
        identities = [
            (entry.get("filename"), entry.get("sha256"))
            if isinstance(entry, dict)
            else (None, None)
            for entry in workbooks
        ]
        expected_identities = [
            (item.filename, item.sha256) for item in policy.workbooks
        ]
        _require(
            reasons,
            identities == expected_identities,
            "WORKBOOK_REGISTRY_MISMATCH",
            "report workbook identities/order differ from the approved registry",
            "$.workbooks",
        )
    return reasons


def _validate_workbook_structure(
    workbook: Mapping[str, object], approved: _ApprovedWorkbook
) -> list[EvidenceDecisionReason]:
    reasons: list[EvidenceDecisionReason] = []
    _require(
        reasons,
        frozenset(workbook) == _WORKBOOK_KEYS,
        "UNSUPPORTED_WORKBOOK_EVIDENCE",
        "workbook evidence keys differ from the supported v1 structure",
        "$.workbooks[]",
    )
    for field, expected_count in (
        ("model_rows", approved.model_rows),
        ("shortlist_rows", approved.shortlist_rows),
        ("data_fields", approved.data_fields),
    ):
        _require(
            reasons,
            _is_int(workbook.get(field), expected_count),
            "WORKBOOK_COVERAGE_MISMATCH",
            f"{field} differs from the approved inventory",
            f"$.workbooks[].{field}",
        )
    _require(
        reasons,
        _is_positive_int(workbook.get("byte_length")),
        "INVALID_BYTE_LENGTH",
        "workbook byte length must be a positive integer",
        "$.workbooks[].byte_length",
    )
    _require(
        reasons,
        workbook.get("recovery_open") is True,
        "RECOVERY_EXTRACTION_INCOMPLETE",
        "recovery-mode extraction did not complete",
        "$.workbooks[].recovery_open",
    )
    _require(
        reasons,
        workbook.get("unreadable_regions") == [],
        "UNREADABLE_REGIONS",
        "unreadable regions prevent evidence-gate satisfaction",
        "$.workbooks[].unreadable_regions",
    )
    _require(
        reasons,
        _is_int(workbook.get("trailing_workbook_padding_bytes"), 0),
        "TRAILING_WORKBOOK_BYTES",
        "nonzero trailing Workbook bytes are unsupported",
        "$.workbooks[].trailing_workbook_padding_bytes",
    )

    sheets = workbook.get("sheet_evidence")
    if not isinstance(sheets, list) or len(sheets) != 2:
        reasons.append(
            _reason(
                "SHEET_COVERAGE_INCOMPLETE",
                "exactly two worksheet evidence entries are required",
                "$.workbooks[].sheet_evidence",
            )
        )
    else:
        expected_sheets = (
            (0, " modell portfóliók", "MODEL_PORTFOLIO", 21, approved.model_rows, "U"),
            (1, " shortlist", "ANALYTICAL_SHORTLIST", 20, approved.shortlist_rows, "T"),
        )
        prior_end: int | None = None
        for sheet, expected_sheet in zip(sheets, expected_sheets, strict=True):
            if not isinstance(sheet, dict):
                reasons.append(
                    _reason(
                        "MALFORMED_SHEET_EVIDENCE",
                        "sheet evidence must be an object",
                        "$.workbooks[].sheet_evidence[]",
                    )
                )
                continue
            index, name, role, headers, rows, last_column = expected_sheet
            prefix = f"$.workbooks[].sheet_evidence[{index}]"
            exact = (
                sheet.get("sheet_index") == index
                and sheet.get("exact_name") == name
                and sheet.get("role") == role
                and sheet.get("visibility") == "visible"
                and _is_int(sheet.get("header_fields"), headers)
                and _is_int(sheet.get("rows"), rows)
                and _is_int(sheet.get("first_data_row"), 2)
                and _is_int(sheet.get("last_data_row"), rows + 1)
                and sheet.get("coordinate_coverage") == f"A1:{last_column}{rows + 1}"
                and _is_int(sheet.get("data_fields"), rows * headers)
                and sheet.get("reached_eof") is True
            )
            _require(
                reasons,
                exact,
                "SHEET_IDENTITY_OR_COVERAGE_MISMATCH",
                "worksheet identity, order, visibility, coordinates, or EOF coverage differs",
                prefix,
            )
            explicit = sheet.get("explicit_cell_records")
            absent = sheet.get("absent_cells")
            accounted = (
                _plain_int(explicit) is not None
                and _plain_int(absent) is not None
                and cast(int, explicit) + cast(int, absent) == headers * (rows + 1)
            )
            _require(
                reasons,
                accounted,
                "UNACCOUNTED_CELLS",
                "explicit plus absent cells do not cover the rectangular source range",
                prefix,
            )
            _require(
                reasons,
                _is_positive_int(sheet.get("raw_record_count")),
                "INCOMPLETE_BIFF_RECORD_COVERAGE",
                "raw BIFF record count is missing",
                f"{prefix}.raw_record_count",
            )
            start = _plain_int(sheet.get("substream_start"))
            end = _plain_int(sheet.get("substream_end"))
            _require(
                reasons,
                start is not None and end is not None and 0 <= start < end,
                "INVALID_SUBSTREAM_BOUNDS",
                "worksheet substream bounds are invalid",
                prefix,
            )
            if prior_end is not None:
                _require(
                    reasons,
                    start == prior_end,
                    "SKIPPED_BIFF_REGION",
                    "worksheet substreams are not contiguous",
                    f"{prefix}.substream_start",
                )
            prior_end = end

    comparison = workbook.get("comparison")
    total = approved.data_fields + 41
    comparison_ok = isinstance(comparison, dict) and (
        _is_int(comparison.get("value_mismatches"), 0)
        and _is_int(comparison.get("cell_property_mismatches"), 0)
        and _is_int(comparison.get("compared_cells_including_headers"), total)
        and _is_int(comparison.get("raw_cell_properties_exact"), total)
        and _plain_int(comparison.get("calamine_exact_values")) is not None
        and _plain_int(comparison.get("calamine_collapsed_missing")) is not None
        and cast(int, comparison.get("calamine_exact_values"))
        + cast(int, comparison.get("calamine_collapsed_missing"))
        == total
    )
    _require(
        reasons,
        comparison_ok,
        "INDEPENDENT_COMPARISON_INCOMPLETE",
        "value/property comparisons are incomplete or contain mismatches",
        "$.workbooks[].comparison",
    )
    _require(
        reasons,
        _is_int(workbook.get("header_fields"), 41),
        "HEADER_COVERAGE_MISMATCH",
        "combined header count must be 41",
        "$.workbooks[].header_fields",
    )
    cell_types = workbook.get("data_cell_types")
    cell_type_ok = (
        isinstance(cell_types, dict)
        and "formula" not in cell_types
        and all(
            isinstance(key, str)
            and _plain_int(value) is not None
            and cast(int, value) >= 0
            for key, value in cell_types.items()
        )
        and sum(cast(int, value) for value in cell_types.values())
        == approved.data_fields
    )
    _require(
        reasons,
        cell_type_ok,
        "CELL_TYPE_COVERAGE_MISMATCH",
        "typed data-cell inventory is incomplete or contains formula cells",
        "$.workbooks[].data_cell_types",
    )
    formats = workbook.get("format_key_counts_including_headers")
    format_ok = (
        isinstance(formats, dict)
        and all(
            isinstance(key, str)
            and _plain_int(value) is not None
            and cast(int, value) >= 0
            for key, value in formats.items()
        )
        and sum(cast(int, value) for value in formats.values()) == total
    )
    _require(
        reasons,
        format_ok,
        "FORMAT_COVERAGE_MISMATCH",
        "XF/format inventory does not cover every compared cell",
        "$.workbooks[].format_key_counts_including_headers",
    )
    return reasons


def _validate_recovery(
    workbook: Mapping[str, object], approved: _ApprovedWorkbook
) -> list[EvidenceDecisionReason]:
    reasons: list[EvidenceDecisionReason] = []
    allocation = workbook.get("allocation")
    if not isinstance(allocation, dict):
        return [
            _reason(
                "MALFORMED_ALLOCATION_EVIDENCE",
                "allocation evidence must be an object",
                "$.workbooks[].allocation",
            )
        ]
    if approved.strict_open:
        _require(
            reasons,
            workbook.get("strict_open") is True
            and workbook.get("strict_error") is None,
            "STRICT_OPEN_OUTCOME_MISMATCH",
            "approved strict-open workbook did not reproduce its strict outcome",
            "$.workbooks[].strict_open",
        )
        _require(
            reasons,
            workbook.get("verdict") == _VERIFIED_VERDICT,
            "WORKBOOK_VERDICT_MISMATCH",
            "strict workbook evidence verdict is contradictory",
            "$.workbooks[].verdict",
        )
        expected = (_NO_DEFECT, 0, None, None, approved.overlap_sector_fingerprint)
        actual = tuple(
            allocation.get(key)
            for key in (
                "defect",
                "overlap_sector_count",
                "overlap_first_sector",
                "overlap_last_sector",
                "overlap_sector_fingerprint",
            )
        )
        _require(
            reasons,
            actual == expected,
            "UNEXPECTED_ALLOCATION_DEFECT",
            "strict workbook has unexpected allocation evidence",
            "$.workbooks[].allocation",
        )
        return reasons

    _require(
        reasons,
        workbook.get("strict_open") is False
        and workbook.get("strict_error") == _STRICT_ERROR,
        "STRICT_FAILURE_SIGNATURE_MISMATCH",
        "strict failure does not match the inventory-bound CompDocError",
        "$.workbooks[].strict_error",
    )
    _require(
        reasons,
        workbook.get("verdict") == _RECOVERY_REQUIRED_VERDICT,
        "WORKBOOK_VERDICT_MISMATCH",
        "recovery workbook must retain the audit-time approval-required verdict",
        "$.workbooks[].verdict",
    )
    expected_signature = {
        "defect": _DEFECT,
        "root_start_sector": 1,
        "root_declared_size": 512,
        "workbook_start_sector": 2,
        "overlap_sector_count": approved.overlap_sector_count,
        "overlap_first_sector": approved.overlap_first_sector,
        "overlap_last_sector": approved.overlap_last_sector,
        "overlap_sector_fingerprint": approved.overlap_sector_fingerprint,
    }
    for key, expected_value in expected_signature.items():
        _require(
            reasons,
            allocation.get(key) == expected_value,
            "RECOVERY_DEFECT_SIGNATURE_MISMATCH",
            f"allocation field {key} differs from the sole approved defect signature",
            f"$.workbooks[].allocation.{key}",
        )
    workbook_size = _plain_int(allocation.get("workbook_declared_size"))
    sheets = workbook.get("sheet_evidence")
    final_end = (
        sheets[-1].get("substream_end")
        if isinstance(sheets, list) and sheets and isinstance(sheets[-1], dict)
        else None
    )
    _require(
        reasons,
        workbook_size is not None and workbook_size == final_end,
        "WORKBOOK_CHAIN_COVERAGE_MISMATCH",
        "complete Workbook chain length does not end at the final worksheet EOF",
        "$.workbooks[].allocation.workbook_declared_size",
    )
    return reasons


def _validate_formula(workbook: Mapping[str, object]) -> list[EvidenceDecisionReason]:
    reasons: list[EvidenceDecisionReason] = []
    _require(
        reasons,
        _is_int(workbook.get("formula_record_count"), 0),
        "FORMULA_RECORDS_PRESENT",
        "covered formula-related record count must be zero",
        "$.workbooks[].formula_record_count",
    )
    _require(
        reasons,
        workbook.get("formula_record_examples") == [],
        "FORMULA_RECORD_EXAMPLES_PRESENT",
        "formula record examples contradict zero-formula evidence",
        "$.workbooks[].formula_record_examples",
    )
    _require(
        reasons,
        workbook.get("formula_status") == _FORMULA_STATUS,
        "FORMULA_STATUS_MISMATCH",
        "formula status does not state zero records in exact bytes",
        "$.workbooks[].formula_status",
    )
    sheets = workbook.get("sheet_evidence")
    if isinstance(sheets, list):
        for index, sheet in enumerate(sheets):
            ok = isinstance(sheet, dict) and _is_int(sheet.get("formula_records"), 0)
            _require(
                reasons,
                ok,
                "SHEET_FORMULA_RECORDS_PRESENT",
                "worksheet formula-related record count must be zero",
                f"$.workbooks[].sheet_evidence[{index}].formula_records",
            )
    return reasons


def _find_workbook(
    report: Mapping[str, object], workbook_sha256: str
) -> Mapping[str, object] | None:
    workbooks = report.get("workbooks")
    if not isinstance(workbooks, list):
        return None
    matches = [
        entry
        for entry in workbooks
        if isinstance(entry, dict) and entry.get("sha256") == workbook_sha256
    ]
    return cast(Mapping[str, object], matches[0]) if len(matches) == 1 else None


def _result(
    source_filename: str,
    workbook_sha256: str,
    binding: EvidenceReportBinding,
    recovery_reasons: Sequence[EvidenceDecisionReason],
    formula_reasons: Sequence[EvidenceDecisionReason],
) -> BiffEvidenceGateEvaluation:
    fallback = (
        _reason(
            "EVIDENCE_REJECTED", "evidence did not satisfy the approved contract", "$"
        ),
    )
    return BiffEvidenceGateEvaluation(
        source_filename=source_filename,
        workbook_sha256=workbook_sha256,
        evidence_binding=binding,
        recovery=_rejected_recovery(tuple(recovery_reasons) or fallback),
        formula_origin=_rejected_formula(tuple(formula_reasons) or fallback),
    )


def _rejected_recovery(
    reasons: Sequence[EvidenceDecisionReason],
) -> EvidenceGateVerdict:
    return EvidenceGateVerdict(
        "RECOVERY_EXCEPTION",
        "REJECTED",
        RECOVERY_APPROVAL_ID,
        APPROVAL_DATE,
        tuple(reasons),
    )


def _rejected_formula(reasons: Sequence[EvidenceDecisionReason]) -> EvidenceGateVerdict:
    return EvidenceGateVerdict(
        "FORMULA_ORIGIN_SUFFICIENCY",
        "REJECTED",
        FORMULA_APPROVAL_ID,
        APPROVAL_DATE,
        tuple(reasons),
    )


def _reason(code: str, message: str, evidence_path: str) -> EvidenceDecisionReason:
    return EvidenceDecisionReason(
        code=code, message=message, evidence_path=evidence_path
    )


def _require(
    reasons: list[EvidenceDecisionReason],
    condition: bool,
    code: str,
    message: str,
    evidence_path: str,
) -> None:
    if not condition:
        reasons.append(_reason(code, message, evidence_path))


def _plain_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_int(value: object, expected: int) -> bool:
    return _plain_int(value) == expected


def _is_positive_int(value: object) -> bool:
    parsed = _plain_int(value)
    return parsed is not None and parsed > 0


def _nested_string(value: object, key: str) -> str | None:
    if isinstance(value, Mapping):
        nested = value.get(key)
        return nested if isinstance(nested, str) else None
    return None


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> object:
    raise ValueError(f"non-finite JSON value is unsupported: {value}")
