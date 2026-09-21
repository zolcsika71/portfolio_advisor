"""Admit one authorized screenshot-backed LTIA cash correction."""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from portfolio_advisor.tbsz.models import (
    CashCorrectionInput,
    ScreenshotArtifactRole,
    SourceCashInput,
    SourceConflictError,
    TbszError,
)
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository
from portfolio_advisor.tbsz.screenshot_evidence import retain_screenshot_artifact


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("balance must be a decimal") from error
    if not result.is_finite() or result < 0:
        raise argparse.ArgumentTypeError("balance must be finite and non-negative")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    parser.add_argument("--evidence-root", type=Path, default=Path("data/tbsz"))
    parser.add_argument("--primary-screenshot", type=Path, required=True)
    parser.add_argument("--primary-sha256", required=True)
    parser.add_argument("--supporting-crop", type=Path, required=True)
    parser.add_argument("--supporting-sha256", required=True)
    parser.add_argument("--correction-id", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--predecessor-snapshot", type=int, required=True)
    parser.add_argument("--currency", required=True)
    parser.add_argument("--balance", type=_decimal, required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args(argv)

    try:
        primary = retain_screenshot_artifact(
            args.primary_screenshot,
            evidence_root=args.evidence_root,
            role=ScreenshotArtifactRole.PRIMARY_ACCOUNT_CONTEXT,
            expected_sha256=args.primary_sha256,
        )
        supporting = retain_screenshot_artifact(
            args.supporting_crop,
            evidence_root=args.evidence_root,
            role=ScreenshotArtifactRole.SUPPORTING_CROP,
            expected_sha256=args.supporting_sha256,
        )
        correction = CashCorrectionInput(
            correction_id=args.correction_id,
            account_label=args.account,
            predecessor_snapshot_id=args.predecessor_snapshot,
            reason=args.reason,
            source_date=None,
            evidence_status="MANUALLY_CONFIRMED_FROM_ERSTE_SCREENSHOT",
            cash=(
                SourceCashInput(
                    currency=args.currency.upper(),
                    balance=args.balance,
                    data_quality_status="MANUALLY_CONFIRMED_FROM_ERSTE_SCREENSHOT",
                ),
            ),
            artifacts=(primary, supporting),
        )
        repository = TbszPortfolioRepository(args.database)
        backup = repository.initialize()
        snapshot, inserted = repository.admit_cash_correction(
            correction,
            evidence_root=args.evidence_root,
        )
    except (OSError, SourceConflictError, TbszError, ValueError) as error:
        print(f"TBSZ_CASH_CORRECTION_FAILED detail={error}", file=sys.stderr)
        return 2

    status = "ADMITTED" if inserted else "ALREADY_ADMITTED_IDENTICAL"
    print(
        f"{status} correction_id={correction.correction_id} "
        f"snapshot_id={snapshot.snapshot_id} backup={backup or 'NONE'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
