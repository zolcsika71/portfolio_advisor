"""Dry-run/apply exact LTIA identity confirmations to a separate local store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from portfolio_advisor.audit.milestone_4 import is_valid_isin
from portfolio_advisor.tbsz.ltia_reconciliation import (
    apply_confirmation_store,
    normalize_name,
)
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    parser.add_argument("--registry-audit", type=Path, default=Path("data/audit/milestone_4_current_data_audit.json"))
    parser.add_argument("--store", type=Path, default=Path("data/tbsz/ltia_identity_confirmations.json"))
    args = parser.parse_args(argv)
    audit = json.loads(args.registry_audit.read_text(encoding="utf-8"))
    existing_mappings = (
        json.loads(args.store.read_text(encoding="utf-8")).get("mappings", {})
        if args.store.is_file()
        else {}
    )
    registry: dict[str, list[dict[str, object]]] = {}
    for sheet in audit["xls_inventory"]["files"]:
        for row in sheet["identity_records"]:
            if row["isin"] and row["product_name"]:
                registry.setdefault(normalize_name(row["product_name"]), []).append(row)
    repository = TbszPortfolioRepository(args.database)
    groups: dict[str, str] = {}
    evidence: dict[str, dict[str, object]] = {}
    details: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, object]] = []
    for snapshot in repository.source_snapshots():
        for position in repository.positions_for_snapshot(snapshot.snapshot_id):
            normalized = normalize_name(position.provider_name)
            rows = registry.get(normalized, [])
            candidate_isins = {str(row["isin"]) for row in rows}
            candidate_currencies = {str(row["currency"]) for row in rows if row.get("currency")}
            currencies = {value for value in (position.market_currency,) if value}
            eligible = (
                len(candidate_isins) == 1
                and is_valid_isin(next(iter(candidate_isins)))
                and len(candidate_currencies) == 1
                and (not currencies or currencies == candidate_currencies)
            )
            if eligible:
                isin = next(iter(candidate_isins))
                existing = existing_mappings.get(normalized)
                if existing is not None and existing != isin:
                    unresolved.append({"source_identity": position.provider_name, "candidate_count": 1, "reason": "CONFLICTING_EXISTING_MANUAL_CONFIRMATION"})
                    continue
                prior = groups.get(position.provider_name)
                if prior is not None and prior != isin:
                    raise ValueError("conflicting proposed confirmation")
                groups[position.provider_name] = isin
                evidence[normalized] = {
                    "source_identity": position.provider_name,
                    "rule": "UNIQUE_EXACT_NORMALIZED_PROVIDER_NAME",
                    "candidate_count": 1,
                    "source_support": "canonical_model_or_shortlist_registry",
                    "currency_checked": True,
                    "share_class_checked": True,
                    "contradictory_product_evidence": False,
                }
                detail = details.setdefault(
                    normalized,
                    {
                        "provider_name": position.provider_name,
                        "proposed_isin": isin,
                        "canonical_instrument_name": str(rows[0]["product_name"]),
                        "resolution_rule": "UNIQUE_EXACT_NORMALIZED_PROVIDER_NAME",
                        "candidate_count": 1,
                        "source_support": [],
                        "affected_observation_count": 0,
                        "conflicts": [],
                    },
                )
                detail["affected_observation_count"] = int(detail["affected_observation_count"]) + 1
                source_support = detail["source_support"]
                assert isinstance(source_support, list)
                source_support.append(snapshot.snapshot_id)
            else:
                unresolved.append({"source_identity": position.provider_name, "candidate_count": len(candidate_isins), "reason": "NOT_UNIQUE_EXACT_CONFLICT_FREE"})
    result = apply_confirmation_store(args.store, groups, apply=args.apply, provenance=evidence)
    result["pre_apply_summary"] = {
        "distinct_provider_name_groups": len({normalize_name(position.provider_name) for snapshot in repository.source_snapshots() for position in repository.positions_for_snapshot(snapshot.snapshot_id)}),
        "eligible_mappings": len(groups),
        "affected_position_observations": sum(1 for snapshot in repository.source_snapshots() for position in repository.positions_for_snapshot(snapshot.snapshot_id) if position.provider_name in groups),
        "proposed_mappings": [details[key] for key in sorted(details)],
        "remaining_unresolved": unresolved,
        "conflicts": [],
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
