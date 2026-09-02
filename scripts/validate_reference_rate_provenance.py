"""Read-only deterministic validation of all reference-rate provenance bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.reference_rates.provenance import (
    ReferenceRateProvenanceValidationError,
    load_reference_rate_validation_registry,
    validate_reference_rate_database,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=ROOT / "database/portfolio_advisor.sqlite",
    )
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument(
        "--validation-registry",
        type=Path,
        help=(
            "optional reviewed offline JSON registry for schedule-derived availability "
            "or provider revision transitions"
        ),
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        registry = (
            load_reference_rate_validation_registry(arguments.validation_registry)
            if arguments.validation_registry is not None
            else None
        )
        result = validate_reference_rate_database(
            target=arguments.target,
            repository_root=arguments.repository_root,
            approved_schedules=(
                registry.approved_schedules if registry is not None else ()
            ),
            approved_revision_contracts=(
                registry.approved_revision_contracts if registry is not None else ()
            ),
        )
        if registry is not None:
            result["validation_registry_sha256"] = registry.artifact_sha256
    except (ReferenceRateProvenanceValidationError, OSError, ValueError) as error:
        print(f"Reference-rate provenance validation failed: {error}", file=sys.stderr)
        return 2
    output = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        sys.stdout.write(output)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
