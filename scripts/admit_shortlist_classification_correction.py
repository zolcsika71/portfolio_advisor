"""Admit the authorized shortlist sub-asset classification correction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.database.migrations.shortlist_classification import (
    ClassificationCorrectionRequest,
    ShortlistClassificationCorrectionError,
    admit_classification_correction,
)
from portfolio_advisor.database.migrations.shortlist_zero_null import (
    ShortlistCorrectionError,
    backup_database,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--correction-id", required=True)
    parser.add_argument("--dataset-fingerprint", required=True)
    parser.add_argument("--initial-target-sha256", required=True)
    parser.add_argument("--authorization-reference", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.apply:
        parser.error("live admission requires explicit --apply")
    try:
        backup_sha256 = backup_database(arguments.database, arguments.backup)
        result = admit_classification_correction(
            arguments.database,
            ClassificationCorrectionRequest(
                correction_id=arguments.correction_id,
                dataset_fingerprint=arguments.dataset_fingerprint,
                initial_target_sha256=arguments.initial_target_sha256,
                authorization_reference=arguments.authorization_reference,
                reason=arguments.reason,
            ),
        )
    except (
        OSError,
        ShortlistCorrectionError,
        ShortlistClassificationCorrectionError,
        ValueError,
    ) as error:
        print(
            f"Shortlist classification correction admission failed: {error}",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "backup": str(arguments.backup.resolve()),
                "backup_sha256": backup_sha256,
                "correction_id": result.correction_id,
                "correction_set_fingerprint": result.correction_set_fingerprint,
                "dataset_fingerprint": result.dataset_fingerprint,
                "item_count": result.item_count,
                "replayed": result.replayed,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
