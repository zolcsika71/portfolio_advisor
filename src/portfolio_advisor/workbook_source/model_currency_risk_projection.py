"""Explicit model currency-risk projection, not a reader or admission decision.

The fixed registry contains only approved anomaly provenance, never financial
rows. No policy, registry, or approval argument can be supplied by a caller.
Input fingerprints bind in-memory evidence; they do not inspect current files.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Final, Literal

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.workbook_source import biff_xls as source
from portfolio_advisor.workbook_source import model_metric_projection as metrics
from portfolio_advisor.workbook_source import normalization

MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1: Final = (
    "MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1"
)
MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1: Final = (
    "MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1"
)
MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1: Final = (
    "MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1"
)
CONTRACT_NAME: Final = "MODEL_CURRENCY_RISK_PROJECTION"
CONTRACT_VERSION: Final = 1
POLICY_APPROVAL_DATE: Final = "2026-10-07"
TYPED_MANIFEST_SHA256: Final = (
    "c4d678e0c90abac49337bf1f7547816b45134b6e6e7886ab4aacf1ee594f123a"
)
ANOMALY_REASON: Final = "LEGACY_ANOMALY_AS_NONE_WITHOUT_INTERPRETATION"
_HEADER: Final = "Devizakockázat"
_MAPPINGS: Final = (
    ("fedezve", "Hedged"),
    ("nincs fedezve", "Unhedged"),
    ("részben fedezve", "Partially Hedged"),
)

type CurrencyRiskProjectionChoice = Literal[
    "MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1"
]
type CurrencyRiskDisposition = Literal[
    "APPROVED_TEXT_TRANSLATION", "SOURCE_MISSING", "KNOWN_ANOMALY_UNINTERPRETED"
]


class ModelCurrencyRiskProjectionError(ValueError):
    """Invalid candidate, unsupported choice, or unapproved currency-risk state."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True, slots=True)
class _AnomalyEntry:
    source_row: int
    raw_value: str | float
    row_fingerprint: str
    evidence_entry_fingerprint: str


@dataclass(frozen=True, slots=True)
class _ApprovedWorkbook:
    workbook_sha256: str
    source_filename: str
    snapshot_date: str
    byte_length: int
    envelope_fingerprint: str
    candidate_fingerprint: str
    model_sheet_fingerprint: str
    shortlist_sheet_fingerprint: str
    model_rows: int
    shortlist_rows: int
    entries: tuple[_AnomalyEntry, ...]


# Exact typed manifest bindings. The candidate hashes were recorded by the
# preserved metric comparison, referenced (not re-evaluated) by typed inspection.
# No new admission, repair, or independent raw-BIFF inspection is implied.
_APPROVED_WORKBOOKS: Final = (
    _ApprovedWorkbook(
        "fadecf0acb478cb4ab4596d1cd509390f0a1d221ee811e05528e5ad6cc173abe",
        "PB_Modell_Portfoliok_es_Shortlist_20241017.xls",
        "2024-10-17",
        211456,
        "ce467f89497c76ea694e0332062de1461372858903dcca4bb20f195209256b66",
        "b7ac6e48eb83dd0d953eefb556c4d0beb9c286ddcbde962f6a7805e28556303d",
        "97fea8f0e095fd2faa4e9b763ec963c340490f71a698bba9572996f63fb23bd3",
        "3c87927903aa4ee41831be953bae076b3e7716207450f970d9666662cef76445",
        169,
        328,
        (
            _AnomalyEntry(
                42,
                "VALUE!",
                "bbe647528001b8012c151fea1b77f2c4b162ea92d9bed887d6f7e63b68500e1a",
                "16a0e6a80c2233ab0d55b7b19167cc900e37d4baa686502c7b167d171d10b1db",
            ),
            _AnomalyEntry(
                90,
                "VALUE!",
                "ee539c0def73e26b68f53a6ab250ff778bfad4ba8e97fd14d2678def5daebf3a",
                "ae202da32596d3bc73bdd00c5e17f5ab240d48aed82ac427cc2b8cca77ad0d72",
            ),
            _AnomalyEntry(
                143,
                "VALUE!",
                "4d28f690142e09c4b3abb0d1cf428e90616c77e9bb00ca87d1be647eac62b78e",
                "4db82b489481d4b3ffa33923f701150f8e21d44061dba92794a46135e1e07e1c",
            ),
        ),
    ),
    _ApprovedWorkbook(
        "a74151e567b5a4b095f302e19411ffac1d191d794a0a1de5898a85f2268ebbed",
        "PB_Modell_Portfoliok_es_Shortlist_20250509.xls",
        "2025-05-09",
        199680,
        "9ba6eee0106768e7d155c8199fe445e40b7f8407405921924c47468bd97f01a9",
        "20f2656bd3da0843f155777a9f98b1561fcb886561228301f90b759f0d7245f2",
        "fffef88ffe758b13bda47a488117a65761378faf29e941aeb4f04b1471fa6dca",
        "e8c83f50ca1c5529b086116545ab97871a5fed928c95dbfb658369ed444f0b77",
        155,
        311,
        (
            _AnomalyEntry(
                4,
                2.0,
                "d2ce3d384c9fa77203cb07e440ed5c4c954a80a98c77296c29950a54e9264b7a",
                "b2e637e057a83c35907169410609b90d2e090fff193c20551417b807083d5b85",
            ),
            _AnomalyEntry(
                17,
                4.0,
                "858f49fa5527cb70b91c385a555b4b44bdefe9409bbb7a5a0ad75a923d4746e5",
                "f4da785379cf79d1fc6223bad7fd2583f6c45be26e543cb4e6f455eff02e9bc8",
            ),
            _AnomalyEntry(
                27,
                2.0,
                "e390014e07c207386f771cb11f42775f7852f8481a84bba4efdde06800f2112f",
                "a179154920203dfce27e193c0a1dcb5bb087e16c63bb6d74bed746f4fcda7b75",
            ),
            _AnomalyEntry(
                28,
                4.0,
                "82a99f134425c81bac16dbde94af3e5010cf1e929ddcb2d8f814f0857e7bc6eb",
                "4decc43664bb00f53b122cfbd244f2953cb02bd649bbda8b637720dd532c13ae",
            ),
            _AnomalyEntry(
                30,
                3.0,
                "444d6b23d6966bda009e56940a29b8132007ef7df1ffc658f37e60bc2bf4ec90",
                "34021725dcf55d144f539d4d91d0a29824b444daf51a9879e2ae09742579a1aa",
            ),
            _AnomalyEntry(
                33,
                "VALUE!",
                "de1ade7eb58f5a011900572211c6228b19a2f61141b64c8a006543c41010e4c9",
                "ec0486bbf17cc5716563f263d2979d799a3cf6a3a198c07bf6cfe76eb0fe1846",
            ),
            _AnomalyEntry(
                37,
                2.0,
                "1576e84c9d462006973cec67cbc33216363c61533d604165666738fd4e78e011",
                "dab3ed3afb3e95db05e8cf15ec749313d5e1917e1f374fa814116ede9d928553",
            ),
            _AnomalyEntry(
                47,
                2.0,
                "133724c68835a9a6ca838ff4a1082d51d63e5e30bf27ae0e2e64a0beb8dc73d0",
                "fb946dc4abbcd5a40c176dd49fc200597218536b58d805e860e979189f30a546",
            ),
            _AnomalyEntry(
                68,
                4.0,
                "18c5ce926de89f7e38e23a9dde8a6afeba5d199d26518a5b04e2fb39e2c11bc7",
                "3a18e18f0c02f6e7cc04c76dd648c99d74c617d837d3fd87424196e4b37f3461",
            ),
            _AnomalyEntry(
                75,
                2.0,
                "27d8055a7ee448c82b0bbd928f223447435cc61d36cd53fbfd1c251ca729555f",
                "2135c617e73685366021c7dadc381e333f6975b88cab912b8b1120dbe550967e",
            ),
            _AnomalyEntry(
                77,
                4.0,
                "465b580041dc7c265faa7bea48727ea18eeb9abfd9ca5522c5f26337d8477d51",
                "a4924c69a8e03cd8fa02105b2be72a3c4c23f4f3aef90859919ea05b7a5cd7c8",
            ),
            _AnomalyEntry(
                80,
                "VALUE!",
                "abf1e7ebafa13a38a7ca1721ababa550eb8e80f45acf18160ea089c3a5c143e1",
                "cb628ab4852864955fd819fc4856c8e065a5b9e617f3160fee9ab44fe1e92a42",
            ),
            _AnomalyEntry(
                82,
                3.0,
                "8f7a3334b84c2248d7cec55e8d83272445f7d254d1244a14a861700fab953e82",
                "b301dcf80a981a832709d8e78c4e0f2d23684a37655a51684d53bdcdbfb73261",
            ),
            _AnomalyEntry(
                89,
                2.0,
                "a095d57e084fc1f1a61dadf5a7b4a70cdab9cfca1f2f847f9b91030427219878",
                "dd3ba494b8d3afffdce2232f55524300de7141c33dce7a5848e482de662884bf",
            ),
            _AnomalyEntry(
                102,
                2.0,
                "22a1b5324c1aa1b50f0e59630501e196e51b507bbc93cf958234de4fca235a3c",
                "c9ef3e2e31de30d015efacc227511da69d1d9443f1517ecb01bb4acca0186202",
            ),
            _AnomalyEntry(
                120,
                4.0,
                "6f9dc03ade309415f788ac4012610cfe7d88af0391952c18cb2012841715aaca",
                "63475dd4b52ee0fd6f87c152aff0cbe5ed0dfd104dd76f579ab09cebaf319308",
            ),
            _AnomalyEntry(
                130,
                2.0,
                "f870630607856831134e8e4121d184aa06db684288132c0d08fefb959e2d95d4",
                "c5c77b81d983b6b17f2d9a9a9c6c27084d4f5cf5f593ea3d58b1b667cfe6e5bb",
            ),
            _AnomalyEntry(
                131,
                4.0,
                "10508b56da46c033277003d7de036bde6721688ec990b76b9af004ed4998d968",
                "3d58cd9eb7836f3e13d8cd1ccf4547045c4168c1ee94f08a216aa102e81c65e3",
            ),
            _AnomalyEntry(
                132,
                "VALUE!",
                "b2309a3c97104e228c1834bd4297ecb77aa8d33ee3b2d3a9853d410333752c55",
                "c51ef27c46b497f77cff80c1e7bf53496a82e8e41c957cca2db36af37f3f5fac",
            ),
            _AnomalyEntry(
                137,
                3.0,
                "a396b6be585e17b00b9752838a4732fbcc727ceac50f511bf0fbc9207c44f6e7",
                "ccb88c165a197c45d9598144f0cfd85793994a4bf24332c51808f264a6ff9d05",
            ),
            _AnomalyEntry(
                143,
                2.0,
                "453d1487fdc0a64e09c2d960dc34d23a2ba48a39e5223481be4dbaea6cc901fb",
                "0512fca5c5f36ef59bda81db79931befd5e78b68468719d1610a2d8fd32cb339",
            ),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class _AnomalyProof:
    """Exact proof surface; only constructed after complete candidate validation."""

    workbook_sha256: str
    source_filename: str
    snapshot_date: str
    envelope_fingerprint: str
    candidate_fingerprint: str
    role: str
    sheet_name: str
    sheet_index: int
    sheet_fingerprint: str
    header_cell: source.SourceCell
    header: str
    source_row: int
    occurrence_index: int
    source_reference: str
    occurrence_id: str
    row_fingerprint: str
    field_occurrence_id: str
    source_cell: source.SourceCell


def _expected_proof(workbook: _ApprovedWorkbook, entry: _AnomalyEntry) -> _AnomalyProof:
    row, index = entry.source_row, entry.source_row - 1
    reference = f"BIFF_XLS_V1:{workbook.workbook_sha256}:0:{row}"
    coordinate = f"H{row}"
    text = type(entry.raw_value) is str
    return _AnomalyProof(
        workbook.workbook_sha256,
        workbook.source_filename,
        workbook.snapshot_date,
        workbook.envelope_fingerprint,
        workbook.candidate_fingerprint,
        "MODEL_PORTFOLIO",
        " modell portfóliók",
        0,
        workbook.model_sheet_fingerprint,
        source.SourceCell(1, 8, "H1", "text", 1, _HEADER, 16, 0, "General", None),
        _HEADER,
        row,
        index,
        reference,
        f"{reference}:OCCURRENCE:{index}",
        entry.row_fingerprint,
        f"{reference}:CELL:{coordinate}:HEADER:{_HEADER}",
        source.SourceCell(
            row,
            8,
            coordinate,
            "text" if text else "number",
            1 if text else 2,
            entry.raw_value,
            15,
            0,
            "General",
            None,
        ),
    )


def _match_anomaly(proof: _AnomalyProof) -> _AnomalyEntry | None:
    # Dataclass equality alone would treat numeric int/bool/float as equal.
    # Exact recursive types and fixed values are both required here.
    if not metrics._typed(proof, _AnomalyProof):
        return None
    for workbook in _APPROVED_WORKBOOKS:
        for entry in workbook.entries:
            expected = _expected_proof(workbook, entry)
            if (
                type(proof.source_cell.raw_value) is type(entry.raw_value)
                and proof == expected
            ):
                return entry
    return None


def _mapping_identity() -> dict[str, object]:
    return {
        "policy": MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1,
        "version": 1,
        "approved_on": POLICY_APPROVAL_DATE,
        "role": "MODEL_PORTFOLIO",
        "header": _HEADER,
        "lookup_operations": ["NFC", "trim", "casefold"],
        "mappings": [{"key": key, "value": value} for key, value in _MAPPINGS],
    }


def _registry_identity() -> dict[str, object]:
    return {
        "policy": MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1,
        "version": 1,
        "approved_on": POLICY_APPROVAL_DATE,
        "typed_manifest_sha256": TYPED_MANIFEST_SHA256,
        "reason": ANOMALY_REASON,
        "workbooks": [
            {
                "sha256": workbook.workbook_sha256,
                "candidate_fingerprint": workbook.candidate_fingerprint,
                "byte_length": workbook.byte_length,
                "model_rows": workbook.model_rows,
                "shortlist_rows": workbook.shortlist_rows,
                "shortlist_sheet_fingerprint": workbook.shortlist_sheet_fingerprint,
                "entries": [
                    {
                        "proof": {
                            name: getattr(proof, name)
                            for name in proof.__dataclass_fields__
                            if name not in ("source_cell", "header_cell")
                        },
                        "source_cell": proof.source_cell.to_dict(),
                        "header_cell": proof.header_cell.to_dict(),
                        "evidence_entry_fingerprint": entry.evidence_entry_fingerprint,
                    }
                    for entry in workbook.entries
                    for proof in (_expected_proof(workbook, entry),)
                ],
            }
            for workbook in _APPROVED_WORKBOOKS
        ],
    }


@dataclass(frozen=True, slots=True)
class ProjectedModelCurrencyRisk:
    original: normalization.NormalizedFieldCandidate
    value: str | None
    disposition: CurrencyRiskDisposition
    reason: str
    lookup_key: str | None
    evidence_entry_fingerprint: str | None
    policy_resolution: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "original": self.original.to_dict(),
            "value": self.value,
            "disposition": self.disposition,
            "reason": self.reason,
            "lookup_key": self.lookup_key,
            "evidence_entry_fingerprint": self.evidence_entry_fingerprint,
            "policy_resolution": self.policy_resolution,
        }


@dataclass(frozen=True, slots=True)
class ProjectedModelCurrencyRiskRow:
    original: normalization.NormalizedRowCandidate
    currency_risk: ProjectedModelCurrencyRisk

    def to_dict(self) -> dict[str, object]:
        return {
            "occurrence_id": self.original.occurrence_id,
            "occurrence_index": self.original.occurrence_index,
            "source_row": self.original.source_row,
            "source_reference": self.original.source_reference,
            "row_fingerprint": self.original.row_fingerprint,
            "currency_risk": self.currency_risk.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ModelCurrencyRiskProjection:
    """Complete immutable evidence plus ordered, explicitly requested attributes."""

    original_candidate: normalization.BiffXlsNormalizationCandidate
    projection: CurrencyRiskProjectionChoice
    rows: tuple[ProjectedModelCurrencyRiskRow, ...]
    _registry_fingerprint: str = dataclass_field(
        init=False,
        default_factory=lambda: canonical_fingerprint(_registry_identity()),
        repr=False,
    )
    _registry_entries: int = dataclass_field(
        init=False,
        default_factory=lambda: sum(len(w.entries) for w in _APPROVED_WORKBOOKS),
        repr=False,
    )

    @property
    def admission_approval(self) -> str:
        return "NOT_GRANTED"

    def _payload(self) -> dict[str, object]:
        sheet = self.original_candidate.sheet(source.MODEL_PORTFOLIO_ROLE)
        mapping = _mapping_identity()
        return {
            "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
            "projection": {
                "name": self.projection,
                "version": 1,
                "approved_on": POLICY_APPROVAL_DATE,
            },
            "mapping_identity": {
                **mapping,
                "fingerprint": canonical_fingerprint(mapping),
            },
            "anomaly_registry": {
                "policy": MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1,
                "version": 1,
                "approved_on": POLICY_APPROVAL_DATE,
                "typed_manifest_sha256": TYPED_MANIFEST_SHA256,
                "fingerprint": self._registry_fingerprint,
                "entries": self._registry_entries,
            },
            "status": "NOT_EVALUATED_PROJECTION_ONLY",
            "admission_approval": self.admission_approval,
            "original_candidate": self.original_candidate.to_dict(),
            "model_sheet": {
                "exact_name": sheet.exact_name,
                "role": sheet.role,
                "sheet_index": sheet.sheet_index,
                "sheet_fingerprint": sheet.sheet_fingerprint,
            },
            "rows": [row.to_dict() for row in self.rows],
        }

    @property
    def projection_fingerprint(self) -> str:
        return canonical_fingerprint(self._payload())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._payload(),
            "projection_fingerprint": self.projection_fingerprint,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


def _validate_historical_binding(
    candidate: normalization.BiffXlsNormalizationCandidate,
) -> None:
    binding = candidate.source_binding
    for workbook in _APPROVED_WORKBOOKS:
        if binding.workbook_sha256 != workbook.workbook_sha256:
            continue
        model = candidate.sheet(source.MODEL_PORTFOLIO_ROLE)
        shortlist = candidate.sheet(source.ANALYTICAL_SHORTLIST_ROLE)
        if (
            candidate.candidate_fingerprint != workbook.candidate_fingerprint
            or binding.envelope_fingerprint != workbook.envelope_fingerprint
            or binding.source_filename != workbook.source_filename
            or binding.snapshot_date != workbook.snapshot_date
            or binding.byte_length != workbook.byte_length
            or binding.parser_library_version != "2.0.2"
            or model.sheet_fingerprint != workbook.model_sheet_fingerprint
            or shortlist.sheet_fingerprint != workbook.shortlist_sheet_fingerprint
            or len(model.rows) != workbook.model_rows
            or len(shortlist.rows) != workbook.shortlist_rows
        ):
            raise ModelCurrencyRiskProjectionError(
                "APPROVED_ANOMALY_WORKBOOK_BINDING_MISMATCH",
                "historical anomaly hash requires its complete exact recorded candidate; "
                "changed/omitted occurrences or optional mapping variants are not authorized",
            )


def _project_field(
    candidate: normalization.BiffXlsNormalizationCandidate,
    sheet: normalization.NormalizedSheetCandidate,
    row: normalization.NormalizedRowCandidate,
    field: normalization.NormalizedFieldCandidate,
) -> ProjectedModelCurrencyRisk:
    cell = field.source_cell
    if field.status == "SOURCE_MISSING":
        return ProjectedModelCurrencyRisk(
            field,
            None,
            "SOURCE_MISSING",
            "ORIGINAL_SOURCE_MISSING",
            None,
            None,
            None,
        )
    if cell.cell_type == "text":
        assert type(cell.raw_value) is str  # substantive validation already ran
        key = unicodedata.normalize("NFC", cell.raw_value).strip().casefold()
        for approved_key, value in _MAPPINGS:
            if key == approved_key:
                return ProjectedModelCurrencyRisk(
                    field,
                    value,
                    "APPROVED_TEXT_TRANSLATION",
                    "APPROVED_HUNGARIAN_TEXT_MAPPING",
                    key,
                    None,
                    MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1,
                )
    binding = candidate.source_binding
    proof = _AnomalyProof(
        binding.workbook_sha256,
        binding.source_filename,
        binding.snapshot_date,
        binding.envelope_fingerprint,
        candidate.candidate_fingerprint,
        sheet.role,
        sheet.exact_name,
        sheet.sheet_index,
        sheet.sheet_fingerprint,
        next(header for header in sheet.headers if header.raw_value == _HEADER),
        field.header,
        row.source_row,
        row.occurrence_index,
        row.source_reference,
        row.occurrence_id,
        row.row_fingerprint,
        field.field_occurrence_id,
        cell,
    )
    return _project_anomaly(field, proof)


def _project_anomaly(
    field: normalization.NormalizedFieldCandidate,
    proof: _AnomalyProof,
) -> ProjectedModelCurrencyRisk:
    entry = _match_anomaly(proof)
    if entry is None:
        raise ModelCurrencyRiskProjectionError(
            "UNAPPROVED_CURRENCY_RISK_STATE",
            f"{proof.source_reference} {proof.source_cell.coordinate}: no approved typed text mapping "
            "or exact anomaly binding; no None fallback, coercion, or holding exclusion",
        )
    return ProjectedModelCurrencyRisk(
        field,
        None,
        "KNOWN_ANOMALY_UNINTERPRETED",
        ANOMALY_REASON,
        None,
        entry.evidence_entry_fingerprint,
        MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1,
    )


def _validate_registry_coverage(
    candidate: normalization.BiffXlsNormalizationCandidate,
    rows: tuple[ProjectedModelCurrencyRiskRow, ...],
) -> None:
    """Every bound entry must resolve once, in source order, with no extras.

    The production registry is fixed; this also fails closed if an implementation
    change accidentally introduces a duplicate or unused registry entry.
    """
    for workbook in _APPROVED_WORKBOOKS:
        if workbook.workbook_sha256 != candidate.source_binding.workbook_sha256:
            continue
        expected = tuple(entry.evidence_entry_fingerprint for entry in workbook.entries)
        actual = tuple(
            row.currency_risk.evidence_entry_fingerprint
            for row in rows
            if row.currency_risk.disposition == "KNOWN_ANOMALY_UNINTERPRETED"
        )
        if actual != expected or len(set(expected)) != len(expected):
            raise ModelCurrencyRiskProjectionError(
                "ANOMALY_REGISTRY_COVERAGE_MISMATCH",
                "every bound anomaly entry must resolve exactly once in source order; "
                "missing, extra, duplicate or reordered registry entries are rejected",
            )


def project_model_currency_risk(
    candidate: normalization.BiffXlsNormalizationCandidate,
    *,
    projection: CurrencyRiskProjectionChoice,
    expected_candidate_fingerprint: str,
) -> ModelCurrencyRiskProjection:
    """Select the approved historical profile's attributes, never a consumer.

    Missing values stay missing; unknown states fail the entire request. The
    unchanged candidate retains every diagnostic. A scoped policy resolution
    neither removes a diagnostic nor satisfies unrelated gates, including an
    anomaly diagnostic on an NFC-only translation variant. No I/O is performed.
    """
    if (
        type(projection) is not str
        or projection != MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1
    ):
        raise ModelCurrencyRiskProjectionError(
            "UNSUPPORTED_PROJECTION",
            "explicitly select the exact approved v1 profile",
        )
    try:
        if (
            source.CONTRACT_NAME != "PORTFOLIO_ADVISOR_BIFF_XLS_DUAL_SHEET_ENVELOPE"
            or source.CONTRACT_VERSION != 1
            or source.PARSER_NAME != "portfolio_advisor.workbook_source.biff_xls"
            or source.PARSER_VERSION != 1
        ):
            raise ModelCurrencyRiskProjectionError(
                "UNSUPPORTED_SOURCE_CONTRACT",
                "only the pinned v1 source parser contract is supported",
            )
        metrics._validate_candidate(candidate, expected_candidate_fingerprint)
        _validate_historical_binding(candidate)
    except metrics.ModelMetricProjectionError as error:
        raise ModelCurrencyRiskProjectionError(error.code, str(error)) from error
    except ModelCurrencyRiskProjectionError:
        raise
    except (
        AttributeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise ModelCurrencyRiskProjectionError(
            "MALFORMED_CANDIDATE", str(error)
        ) from error
    sheet = candidate.sheet(source.MODEL_PORTFOLIO_ROLE)
    rows = tuple(
        ProjectedModelCurrencyRiskRow(
            row,
            _project_field(
                candidate,
                sheet,
                row,
                next(field for field in row.fields if field.header == _HEADER),
            ),
        )
        for row in sheet.rows
    )
    _validate_registry_coverage(candidate, rows)
    return ModelCurrencyRiskProjection(candidate, projection, rows)
