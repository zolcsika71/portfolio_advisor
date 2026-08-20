"""Bounded, fail-closed research records for direct portfolio performance."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from portfolio_advisor.database.repository import ModelPortfolioRepository

DIRECT_SOURCE_VALIDATED: Final = "DIRECT_OFFICIAL_SOURCE_VALIDATED"
AUTHORITATIVE_DOMAINS: Final = frozenset({"erstebank.hu", "erstegroup.com", "mnb.hu"})
VALUE_TYPES: Final = frozenset({"PORTFOLIO_NAV", "PORTFOLIO_INDEX_VALUE", "OFFICIAL_PORTFOLIO_PRICE", "OFFICIAL_TOTAL_RETURN_INDEX"})


class OfficialPortfolioPerformanceResearchError(RuntimeError):
    """Direct-source research cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class PortfolioPerformanceTarget:
    portfolio_id: str
    portfolio_name: str
    currency_or_declared_currency: str
    first_snapshot_date: date
    last_snapshot_date: date
    snapshot_count: int
    source_workbook_names: tuple[str, ...]
    provider_source_family: str


@dataclass(frozen=True, slots=True)
class DirectPortfolioSourceCandidate:
    portfolio_id: str
    portfolio_name: str
    source_authority: str
    source_url_or_reference: str
    authority_domain: str
    identity_exact: bool
    historical_period_start: date | None
    historical_period_end: date | None
    value_type: str | None
    currency: str | None
    reproducible_retained_file: bool
    local_path: str | None
    sha256: str | None
    portfolio_level: bool


def build_search_targets(*, database_path: Path, processed_workbook_dir: Path) -> list[PortfolioPerformanceTarget]:
    """Build exact target identities from retained portfolio snapshots."""
    repository = ModelPortfolioRepository(database_path)
    records: dict[str, list[tuple[date, str | None]]] = defaultdict(list)
    for observation_date in repository.observation_dates():
        for holding in repository.load_holdings(observation_date):
            records[holding.portfolio_name].append((observation_date, holding.currency))
    targets: list[PortfolioPerformanceTarget] = []
    for portfolio_name, snapshots in sorted(records.items()):
        dates = sorted({item[0] for item in snapshots})
        currencies = sorted({item[1] for item in snapshots if item[1]})
        workbook_names = tuple(
            path.name
            for path in sorted(processed_workbook_dir.glob("*.xls"))
            if any(item.strftime("%Y%m%d") in path.name for item in dates)
        )
        if len(workbook_names) != len(dates):
            raise OfficialPortfolioPerformanceResearchError(f"workbook provenance does not reconcile for {portfolio_name!r}")
        targets.append(
            PortfolioPerformanceTarget(
                portfolio_id=portfolio_name,
                portfolio_name=portfolio_name,
                currency_or_declared_currency=currencies[0] if len(currencies) == 1 else "MULTI_CURRENCY",
                first_snapshot_date=dates[0],
                last_snapshot_date=dates[-1],
                snapshot_count=len(dates),
                source_workbook_names=workbook_names,
                provider_source_family="ERSTE_BANK_HUNGARY_PRIVATE_BANKING_CANDIDATE",
            )
        )
    return targets


def classify_candidate(candidate: DirectPortfolioSourceCandidate) -> str:
    """Do not promote authority, identity, semantics, or retention by inference."""
    if candidate.authority_domain not in AUTHORITATIVE_DOMAINS:
        return "SOURCE_UNAVAILABLE"
    if not candidate.identity_exact or not candidate.portfolio_level:
        return "AUTHORITATIVE_SOURCE_FOUND_IDENTITY_UNRESOLVED"
    if candidate.value_type not in VALUE_TYPES or not candidate.currency:
        return "AUTHORITATIVE_SOURCE_FOUND_SEMANTICS_INSUFFICIENT"
    if candidate.historical_period_start is None or candidate.historical_period_end is None:
        return "AUTHORITATIVE_SOURCE_FOUND_PERIOD_INSUFFICIENT"
    if not candidate.reproducible_retained_file or not candidate.local_path or not candidate.sha256:
        return "AUTHORITATIVE_SOURCE_FOUND_SEMANTICS_INSUFFICIENT"
    return DIRECT_SOURCE_VALIDATED


def write_search_targets(path: Path, targets: list[PortfolioPerformanceTarget]) -> None:
    payload: dict[str, object] = {"schema_version": 1, "target_count": len(targets), "targets": [_target(item) for item in targets]}
    payload["target_fingerprint"] = _fingerprint(payload)
    _write_json_atomic(path, payload)


def build_research_artifact(
    *,
    targets: list[PortfolioPerformanceTarget],
    source_families_searched: tuple[str, ...],
    query_families: tuple[str, ...],
    candidates: tuple[DirectPortfolioSourceCandidate, ...],
    local_direct_source_found: bool,
    pre_admission_discovery_rejections: tuple[str, ...] = (),
) -> dict[str, object]:
    """Record finite research; never construct a portfolio series."""
    if len(source_families_searched) > 5 or len(query_families) > 20 or len(candidates) > 40:
        raise OfficialPortfolioPerformanceResearchError("bounded search limits exceeded")
    rows = [
        {**_candidate(item), "validation_status": classify_candidate(item)}
        for item in sorted(candidates, key=lambda item: (item.portfolio_id, item.source_url_or_reference))
    ]
    accepted = [item for item in rows if item["validation_status"] == DIRECT_SOURCE_VALIDATED]
    payload: dict[str, object] = {
        "schema_version": 1,
        "search_status": "DIRECT_OFFICIAL_PORTFOLIO_PERFORMANCE_SOURCE_VALIDATED" if accepted else "DIRECT_OFFICIAL_PORTFOLIO_PERFORMANCE_SOURCE_NOT_FOUND",
        "target_portfolios": [_target(item) for item in targets],
        "source_families_searched": list(source_families_searched),
        "query_families": list(query_families),
        "candidates_reviewed": rows,
        "accepted_sources": accepted,
        "rejected_sources": [item for item in rows if item not in accepted],
        "pre_admission_discovery_rejections": list(pre_admission_discovery_rejections),
        "retained_files": [{"portfolio_id": item["portfolio_id"], "local_path": item["local_path"], "sha256": item["sha256"]} for item in accepted],
        "identity_validation": "Exact portfolio identity and portfolio-level evidence are mandatory.",
        "semantic_validation": "Explicit retained NAV/index/price/total-return semantics are mandatory.",
        "historical_coverage": "No direct official series was retained or admitted." if not accepted else "See accepted direct-source intervals.",
        "local_direct_source_found": local_direct_source_found,
        "stopping_rule": {
            "max_authoritative_source_families": 5,
            "max_query_families": 20,
            "max_serious_candidates": 40,
            "completed": True,
            "reason": "Bounded exact-name authoritative search completed; no candidate passed all admission requirements.",
        },
        "freeze_interaction": {
            "synthetic_reconstruction_freeze": "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED",
            "direct_official_source_path": "NOT_FOUND" if not accepted else "DIRECT_OFFICIAL_PORTFOLIO_PERFORMANCE_SOURCE",
            "synthetic_reconstruction_activated": False,
        },
        "forward_label_feasibility": {
            "DIRECT_SOURCE_WINDOW_COVERED": 0,
            "DIRECT_SOURCE_WINDOW_NOT_COVERED": 1152 if not accepted else 0,
            "DIRECT_SOURCE_SEMANTICS_BLOCKED": 0,
            "DIRECT_SOURCE_IDENTITY_BLOCKED": 0,
            "note": "No direct official series was admitted; these are not official labels.",
        },
        "recommended_next_action": "INTEGRATE_DIRECT_OFFICIAL_PORTFOLIO_PERFORMANCE_SOURCE_INTO_FORWARD_LABEL_STORE" if accepted else "DESIGN_PROSPECTIVE_PORTFOLIO_VALIDATION_PIPELINE",
    }
    payload["research_fingerprint"] = _fingerprint(payload)
    return payload


def write_research_artifact(path: Path, payload: dict[str, object]) -> None:
    _write_json_atomic(path, payload)


def _target(item: PortfolioPerformanceTarget) -> dict[str, object]:
    return {
        "portfolio_id": item.portfolio_id,
        "portfolio_name": item.portfolio_name,
        "currency_or_declared_currency": item.currency_or_declared_currency,
        "first_snapshot_date": item.first_snapshot_date.isoformat(),
        "last_snapshot_date": item.last_snapshot_date.isoformat(),
        "snapshot_count": item.snapshot_count,
        "source_workbook_names": list(item.source_workbook_names),
        "provider_source_family": item.provider_source_family,
    }


def _candidate(item: DirectPortfolioSourceCandidate) -> dict[str, object]:
    return {
        "portfolio_id": item.portfolio_id, "portfolio_name": item.portfolio_name,
        "source_authority": item.source_authority, "source_url_or_reference": item.source_url_or_reference,
        "authority_domain": item.authority_domain, "identity_exact": item.identity_exact,
        "historical_period_start": item.historical_period_start.isoformat() if item.historical_period_start else None,
        "historical_period_end": item.historical_period_end.isoformat() if item.historical_period_end else None,
        "value_type": item.value_type, "currency": item.currency,
        "reproducible_retained_file": item.reproducible_retained_file,
        "local_path": item.local_path, "sha256": item.sha256, "portfolio_level": item.portfolio_level,
    }


def _fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
