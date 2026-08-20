"""Write a deterministic audit of the append-only prospective validation ledger."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.prospective.validation import (
    ProspectiveValidationError,
    ProspectiveValidationStore,
    build_prospective_validation_audit,
    write_prospective_validation_audit,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path("database/prospective_portfolio_validation.sqlite"))
    parser.add_argument("--freeze", type=Path, default=Path("data/audit/portfolio_nav_reconstruction_freeze.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/prospective_portfolio_validation_pipeline.json"))
    args = parser.parse_args(argv)
    try:
        payload = build_prospective_validation_audit(
            store=ProspectiveValidationStore(args.store), repository_root=Path.cwd().resolve(), freeze_path=args.freeze
        )
        repeated = build_prospective_validation_audit(
            store=ProspectiveValidationStore(args.store), repository_root=Path.cwd().resolve(), freeze_path=args.freeze
        )
        if payload != repeated:
            raise ProspectiveValidationError("prospective audit is nondeterministic")
        write_prospective_validation_audit(args.output, payload)
    except (ProspectiveValidationError, RuntimeError, ValueError) as error:
        print(f"Prospective validation audit failed: {error}", file=sys.stderr)
        return 2
    print(f"Pipeline status: {payload['pipeline_status']}")
    print(f"Readiness: {payload['prospective_validation_readiness']}")
    print(f"Audit fingerprint: {payload['audit_fingerprint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
