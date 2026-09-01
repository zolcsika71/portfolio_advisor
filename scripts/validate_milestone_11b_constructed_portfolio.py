"""Validate Milestone 11B schema and the current read-only production blocked state."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

from portfolio_advisor.construction import attempt_current_production_construction
from portfolio_advisor.construction.foundation_audit import (
    foundation_audit_payload,
    validate_constructed_foundation,
)
from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    build_default_policy_registry,
    load_capital_defensive_construction_policy,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/portfolio_advisor.sqlite"))
    parser.add_argument("--workbooks", type=Path, default=Path("data/xls/processed"))
    parser.add_argument("--currency", choices=("EUR", "USD", "HUF"), default="EUR")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    before = _sha256(arguments.database)
    try:
        policy = load_capital_defensive_construction_policy(
            root / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
        )
        registry = build_default_policy_registry(root)
        production = attempt_current_production_construction(
            database_path=arguments.database,
            repository_root=root,
            workbook_directory=arguments.workbooks,
            cash_by_currency={arguments.currency: Decimal(1)},
        )
        validation = validate_constructed_foundation(
            arguments.database,
            expected_policy_fingerprint=policy.fingerprint,
            expect_zero_constructed_rows=True,
        )
        payload = foundation_audit_payload(
            validation=validation,
            production_attempt=production,
            policy=policy,
            registry=registry,
        )
        if _sha256(arguments.database) != before:
            raise RuntimeError("read-only validator changed the target database")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Milestone 11B validation failed: {error}", file=sys.stderr)
        return 2
    output = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        sys.stdout.write(output)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
    return 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
