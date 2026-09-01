"""Guarded migration infrastructure for the central analytical database."""

from .backup import create_verified_backup
from .constructed_portfolio import (
    MIGRATION_REVISION,
    ConstructedPortfolioMigrationError,
    ConstructedPortfolioMigrationResult,
    build_constructed_portfolio_schema_candidate,
)
from .ecb_estr import (
    ECB_ESTR_EVIDENCE_MIGRATION_REVISION,
    EcbEstrCandidateResult,
    build_ecb_estr_evidence_candidate,
)
from .reference_rate import (
    REFERENCE_RATE_MIGRATION_REVISION,
    ReferenceRateMigrationError,
    ReferenceRateMigrationResult,
    build_reference_rate_schema_candidate,
    validate_reference_rate_schema_foundation,
)
from .v2_to_v3 import CutoverNotAuthorized, MigrationPlan, dry_run_v2_to_v3

__all__ = [
    "ECB_ESTR_EVIDENCE_MIGRATION_REVISION",
    "MIGRATION_REVISION",
    "REFERENCE_RATE_MIGRATION_REVISION",
    "ConstructedPortfolioMigrationError",
    "ConstructedPortfolioMigrationResult",
    "CutoverNotAuthorized",
    "EcbEstrCandidateResult",
    "MigrationPlan",
    "ReferenceRateMigrationError",
    "ReferenceRateMigrationResult",
    "build_constructed_portfolio_schema_candidate",
    "build_ecb_estr_evidence_candidate",
    "build_reference_rate_schema_candidate",
    "create_verified_backup",
    "dry_run_v2_to_v3",
    "validate_reference_rate_schema_foundation",
]
