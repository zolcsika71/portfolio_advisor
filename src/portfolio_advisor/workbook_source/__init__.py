"""Lossless source-extraction contracts for retained workbooks."""

from .biff_xls import (
    ANALYTICAL_SHORTLIST_ROLE,
    CONTRACT_NAME,
    CONTRACT_VERSION,
    MODEL_PORTFOLIO_ROLE,
    BiffXlsEnvelope,
    BiffXlsParseError,
    parse_biff_xls,
)

__all__ = [
    "ANALYTICAL_SHORTLIST_ROLE",
    "CONTRACT_NAME",
    "CONTRACT_VERSION",
    "MODEL_PORTFOLIO_ROLE",
    "BiffXlsEnvelope",
    "BiffXlsParseError",
    "parse_biff_xls",
]
