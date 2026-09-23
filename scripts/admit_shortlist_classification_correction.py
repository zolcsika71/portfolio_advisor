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
from portfolio_advisor.database.migrations.shortlist_classification_composition import (
    ClassificationCompositionRequest,
    admit_composed_classification_correction,
)
from portfolio_advisor.database.migrations.shortlist_classification_pair_mapping import (
    ClassificationPairMappingRequest,
    admit_pair_mapping_correction,
    load_pair_mapping_manifest,
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
    parser.add_argument(
        "--question-mark-to-o-double-acute",
        action="store_true",
        help=(
            "append the authorized effective-sub-asset replacement '?' -> 'ő'; "
            "requires --expected-prior-label-count"
        ),
    )
    parser.add_argument(
        "--expected-prior-label-count",
        action="append",
        default=[],
        metavar="LABEL=COUNT",
        help="exact prior effective label and authorized row count (repeatable)",
    )
    parser.add_argument(
        "--english-pair-mapping-manifest",
        type=Path,
        help=(
            "append the exact authorized effective asset/sub-asset pair mapping "
            "from a reviewed manifest"
        ),
    )
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.apply:
        parser.error("live admission requires explicit --apply")
    if (
        arguments.question_mark_to_o_double_acute
        and arguments.english_pair_mapping_manifest is not None
    ):
        parser.error(
            "--question-mark-to-o-double-acute and "
            "--english-pair-mapping-manifest are mutually exclusive"
        )
    if (
        arguments.english_pair_mapping_manifest is not None
        and arguments.expected_prior_label_count
    ):
        parser.error(
            "--expected-prior-label-count cannot be used with "
            "--english-pair-mapping-manifest"
        )
    try:
        pair_manifest = (
            load_pair_mapping_manifest(arguments.english_pair_mapping_manifest)
            if arguments.english_pair_mapping_manifest is not None
            else None
        )
        backup_sha256 = backup_database(arguments.database, arguments.backup)
        if pair_manifest is not None:
            result = admit_pair_mapping_correction(
                arguments.database,
                ClassificationPairMappingRequest(
                    correction_id=arguments.correction_id,
                    dataset_fingerprint=arguments.dataset_fingerprint,
                    initial_target_sha256=arguments.initial_target_sha256,
                    authorization_reference=arguments.authorization_reference,
                    reason=arguments.reason,
                    manifest=pair_manifest,
                ),
            )
        elif arguments.question_mark_to_o_double_acute:
            expected_counts = _parse_expected_counts(
                arguments.expected_prior_label_count
            )
            result = admit_composed_classification_correction(
                arguments.database,
                ClassificationCompositionRequest(
                    correction_id=arguments.correction_id,
                    dataset_fingerprint=arguments.dataset_fingerprint,
                    initial_target_sha256=arguments.initial_target_sha256,
                    authorization_reference=arguments.authorization_reference,
                    reason=arguments.reason,
                    expected_prior_label_counts=expected_counts,
                ),
            )
        else:
            if arguments.expected_prior_label_count:
                parser.error(
                    "--expected-prior-label-count requires "
                    "--question-mark-to-o-double-acute"
                )
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


def _parse_expected_counts(values: list[str]) -> tuple[tuple[str, int], ...]:
    if not values:
        raise ValueError("at least one expected prior label count is required")
    result: list[tuple[str, int]] = []
    for value in values:
        label, separator, count_text = value.rpartition("=")
        if not separator or not label:
            raise ValueError(
                "expected prior label counts must use the form LABEL=COUNT"
            )
        count = int(count_text)
        result.append((label, count))
    return tuple(result)


if __name__ == "__main__":
    raise SystemExit(main())
