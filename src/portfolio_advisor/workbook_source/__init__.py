"""Lossless source-extraction contracts for retained workbooks."""

from .biff_evidence_approval import (
    BiffEvidenceGateEvaluation,
    EvidenceDecisionReason,
    EvidenceGateVerdict,
    EvidenceReportBinding,
    evaluate_biff_xls_evidence,
)
from .biff_xls import (
    ANALYTICAL_SHORTLIST_ROLE,
    CONTRACT_NAME,
    CONTRACT_VERSION,
    MODEL_PORTFOLIO_ROLE,
    BiffXlsEnvelope,
    BiffXlsParseError,
    parse_biff_xls,
)
from .normalization import (
    ADMISSION_APPROVAL,
    CANDIDATE_STATUS,
    BiffXlsNormalizationCandidate,
    BiffXlsNormalizationError,
    normalize_biff_xls_envelope,
)

__all__ = [
    "ADMISSION_APPROVAL",
    "ANALYTICAL_SHORTLIST_ROLE",
    "CANDIDATE_STATUS",
    "CONTRACT_NAME",
    "CONTRACT_VERSION",
    "MODEL_PORTFOLIO_ROLE",
    "BiffEvidenceGateEvaluation",
    "BiffXlsEnvelope",
    "BiffXlsNormalizationCandidate",
    "BiffXlsNormalizationError",
    "BiffXlsParseError",
    "EvidenceDecisionReason",
    "EvidenceGateVerdict",
    "EvidenceReportBinding",
    "evaluate_biff_xls_evidence",
    "normalize_biff_xls_envelope",
    "parse_biff_xls",
]
