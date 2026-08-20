"""Audit alternative HU0000554795 sources from retained research only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.alternative_price_sources import (
    TARGET_HU_ISIN,
    AlternativePriceSourceError,
)
from portfolio_advisor.history.mnb_otc import MnbOtcRepository


def load(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AlternativePriceSourceError(f"Unable to load alternative-source research: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("isin") != TARGET_HU_ISIN
        or payload.get("research_outcome")
        not in {
            "ALTERNATIVE_PRICE_SOURCE_FOUND",
            "ALTERNATIVE_PRICE_SOURCE_PARTIAL",
            "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND",
            "ALTERNATIVE_PRICE_SOURCE_CONFLICT",
        }
        or payload.get("stopping_rule_completed") is not True
        or any(payload.get(field) is not False for field in ("nav_equivalent", "backtest_return_series_approved", "usable_for_backtest"))
    ):
        raise AlternativePriceSourceError("Alternative-source research artifact is malformed or unsafe")
    return payload


def build_audit(research: dict[str, object], database: Path) -> dict[str, object]:
    candidates = research.get("candidates")
    if not isinstance(candidates, list):
        raise AlternativePriceSourceError("Alternative-source research has no candidates")
    keler = MnbOtcRepository(database).observations(TARGET_HU_ISIN)
    validated = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and candidate.get("admission_status") == "AUDIT_CANDIDATE_VALIDATED"
    ]
    partial = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and candidate.get("admission_status") == "AUDIT_CANDIDATE_PARTIAL"
    ]
    return {
        "schema_version": 1,
        "isin": TARGET_HU_ISIN,
        "research_interval": research["research_interval"],
        "research_outcome": research["research_outcome"],
        "research_artifact": "data/audit/hu0000554795_alternative_price_sources.json",
        "source_tiers": research["source_tiers"],
        "query_families": research["query_families"],
        "candidate_count": len(candidates),
        "validated_candidate_count": len(validated),
        "partial_candidate_count": len(partial),
        "candidates": candidates,
        "overlap_comparisons": [],
        "overlap_comparison_reason": "No validated alternative exact-ISIN series exists; KELER observations were not modified or reused.",
        "hypothetical_window_coverage": [],
        "preferred_audit_candidate": None,
        "exact_boundary_capable_candidate_exists": False,
        "validated_price_semantics_candidate_exists": False,
        "keler_observation_count_unchanged": len(keler),
        "unresolved_semantic_gaps": [
            "No authoritative alternative exact-ISIN historical price series was retained.",
            "No alternative source has validated price/date semantics or exact boundaries.",
        ],
        "source_stitching_performed": False,
        "nearest_date_substitution_performed": False,
        "interpolation_performed": False,
        "fill_performed": False,
        "daily_resampling_performed": False,
        "synthetic_observations_created": 0,
        "return_calculation_performed": False,
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
        "recommended_next_action": "ACCEPT_HU0000554795_AS_BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE"
        if research["research_outcome"] == "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND"
        else "DEEPEN_AUTHORITATIVE_HU0000554795_PRICE_SOURCE_RESEARCH",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", type=Path, default=Path("data/audit/hu0000554795_alternative_price_sources.json"))
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/hu0000554795_alternative_price_sources_audit.json"))
    args = parser.parse_args()
    try:
        artifact = build_audit(load(args.research), args.database)
    except AlternativePriceSourceError as exc:
        print(f"Alternative price-source audit failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Alternative price-source outcome: {artifact['research_outcome']}")
    print(f"Validated candidates: {artifact['validated_candidate_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
