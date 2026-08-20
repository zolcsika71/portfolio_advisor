"""Resolve only portfolio-NAV methodology facts supported by local evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.DB_creation.excel_processing import read_target_worksheet
from portfolio_advisor.DB_creation.text_normalization import normalized_key
from portfolio_advisor.history.official_nav_store import OfficialNavStore

ALLOCATION_SEMANTICS_UNKNOWN: Final = "UNKNOWN"
DUPLICATE_SEMANTICS_UNRESOLVED: Final = "REPEATED_SOURCE_ROW_UNRESOLVED"


class PortfolioNavBlockerResolutionError(RuntimeError):
    """Local evidence cannot be deterministically traced or classified."""


@dataclass(frozen=True, slots=True)
class DuplicateSourceRow:
    normalized_frame_row: int
    allocation: str
    product: str
    currency: str
    asset_class: str


def classify_allocation_semantics(source_header: str) -> str:
    """Return UNKNOWN unless the source explicitly specifies economic meaning."""
    if normalized_key(source_header) == "hányad (%)":
        return ALLOCATION_SEMANTICS_UNKNOWN
    return ALLOCATION_SEMANTICS_UNKNOWN


def classify_duplicate_source_rows(rows: tuple[DuplicateSourceRow, ...]) -> str:
    """Never aggregate repeated ISIN source rows without an explicit sleeve key."""
    if len(rows) < 2:
        raise PortfolioNavBlockerResolutionError("duplicate resolution requires at least two rows")
    return DUPLICATE_SEMANTICS_UNRESOLVED


def build_portfolio_nav_blocker_resolution(
    *,
    database_path: Path,
    nav_store_path: Path,
    processed_workbook_dir: Path,
    worksheet_importer_path: Path,
    database_importer_path: Path,
    model_repository_path: Path,
    methodology_document_path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    """Build deterministic blocker and duplicate-source audits without network I/O."""
    repository = ModelPortfolioRepository(database_path)
    duplicate_payload = _duplicate_resolution(
        repository, processed_workbook_dir
    )
    duplicate_workbook_sha = duplicate_payload["source_workbook_sha256"]
    if not isinstance(duplicate_workbook_sha, str):
        raise PortfolioNavBlockerResolutionError("duplicate-workbook fingerprint is malformed")
    nav_store = OfficialNavStore(nav_store_path)
    store_summary = nav_store.summary()
    workbook_reference = _relative_reference(processed_workbook_dir)
    worksheet_importer_reference = _relative_reference(worksheet_importer_path)
    database_importer_reference = _relative_reference(database_importer_path)
    repository_reference = _relative_reference(model_repository_path)
    methodology_reference = _relative_reference(methodology_document_path)
    blockers = [
        _blocker(
            blocker="SNAPSHOT_WEIGHT_SEMANTICS_UNRESOLVED",
            after=ALLOCATION_SEMANTICS_UNKNOWN,
            evidence_type="SOURCE_TO_FIELD_TRACE",
            source="Original workbook header plus importer mapping",
            local_path=worksheet_importer_reference,
            sha256=_sha256(worksheet_importer_path),
            section_or_location="HEADER_TRANSLATIONS['hányad (%)'] -> 'Allocation (%)'",
            reasoning="The source calls the field 'Hányad (%)' but does not establish target allocation, observed holdings, recommendation, or execution.",
            missing="An authoritative portfolio document defining the economic meaning of the allocation field.",
        ),
        _blocker(
            blocker="REBALANCE_EFFECTIVE_TIMESTAMP_UNRESOLVED",
            after="UNKNOWN",
            evidence_type="IMPORTER_TRACE",
            source="Workbook filename date importer",
            local_path=database_importer_reference,
            sha256=_sha256(database_importer_path),
            section_or_location="extract_date() and add_date_field()",
            reasoning="The importer retains one filename date only; it does not retain publication time, trade time, or an effective-date rule.",
            missing="An authoritative rebalance rule with effective date, effective time, and non-trading-day semantics.",
        ),
        _blocker(
            blocker="DUPLICATE_CONSTITUENT_ROWS_REQUIRE_RESOLUTION",
            after="PARTIALLY_RESOLVED_PROVENANCE_ONLY",
            evidence_type="ORIGINAL_WORKBOOK_ROWS",
            source="Retained 2024-09-17 Modell Portfóliók worksheet",
            local_path=f"{workbook_reference}/PB_Modell_Portfoliok_es_Shortlist_20240917.xls",
            sha256=duplicate_workbook_sha,
            section_or_location="Duplicate-resolution cases in companion audit",
            reasoning="All three duplicates are present in the original workbook, so they are not created by the importer. No sleeve, position, or split-allocation identifier is retained to justify summing them.",
            missing="Authoritative source semantics for repeated same-ISIN rows within one portfolio/date.",
        ),
        _blocker(
            blocker="PORTFOLIO_REPORTING_CURRENCY_UNRESOLVED",
            after="UNKNOWN",
            evidence_type="VALIDATED_REPOSITORY_DOCUMENTATION",
            source="Deterministic capital-preservation methodology",
            local_path=methodology_reference,
            sha256=_sha256(methodology_document_path),
            section_or_location="currency_mismatch: no investor base currency",
            reasoning="The retained Currency field is a constituent field. The local methodology explicitly records no investor base currency.",
            missing="Portfolio-specific reporting/base currency evidence.",
        ),
        _blocker(
            blocker="FX_METHODOLOGY_REQUIRED",
            after="UNKNOWN",
            evidence_type="SNAPSHOT_CURRENCY_AUDIT",
            source="model_portfolios Currency field",
            local_path="database/model_portfolio.sqlite:model_portfolios",
            sha256=_sha256(database_path),
            section_or_location="Mixed HUF/EUR/USD constituent rows",
            reasoning="Mixed nominal constituent currencies occur in retained snapshots and no approved FX series or conversion rule is retained.",
            missing="Authoritative point-in-time FX source and conversion methodology.",
        ),
        _blocker(
            blocker="DISTRIBUTION_AND_CORPORATE_ACTION_TOTAL_RETURN_SEMANTICS_UNRESOLVED",
            after="UNKNOWN",
            evidence_type="CANONICAL_NAV_STORE_SCHEMA",
            source="Validated constituent NAV store",
            local_path="database/official_historical_nav.sqlite:asset_nav_observations",
            sha256=_sha256(nav_store_path),
            section_or_location="Value Type = NAV; no total-return/distribution classification field",
            reasoning=f"{store_summary.acquired_isin_count} retained constituent series are typed NAV, but retained evidence does not classify each as accumulation/distribution total return or provide cash-flow treatment.",
            missing="Per-instrument authoritative distribution, coupon, redemption, and corporate-action return semantics.",
        ),
        _blocker(
            blocker="PORTFOLIO_CASHFLOW_AND_FEE_TREATMENT_UNRESOLVED",
            after="UNKNOWN",
            evidence_type="VALIDATED_REPOSITORY_DOCUMENTATION",
            source="Deterministic capital-preservation methodology",
            local_path=methodology_reference,
            sha256=_sha256(methodology_document_path),
            section_or_location="cost_indicators: no cost/fee field",
            reasoning="No retained portfolio transaction-cost, advisory-fee, platform-fee, subscription/redemption, or cash-allocation field establishes reconstruction accounting.",
            missing="Portfolio-specific accounting treatment or an explicit statement that such effects are not part of the performance series.",
        ),
        _blocker(
            blocker="STRICT_PORTFOLIO_NAV_SOURCE_NOT_MATERIALIZED",
            after="NOT_ACTIVATED",
            evidence_type="HISTORY_SOURCE_CONTRACT",
            source="Read-only historical repository contract",
            local_path="src/portfolio_advisor/history/repository.py",
            sha256=_sha256(Path("src/portfolio_advisor/history/repository.py")),
            section_or_location="portfolio_nav_history read-only access; no approved constituent aggregation contract",
            reasoning="The retained source contract permits a pre-existing official portfolio NAV series only. It contains no approved rule for turning constituent NAV observations into a portfolio series.",
            missing="An approved, portfolio-specific aggregation and rebalancing contract satisfying every economic blocker.",
        ),
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "validation_status": "PORTFOLIO_NAV_METHODOLOGY_BLOCKERS_PARTIALLY_RESOLVED",
        "activation_state": "NOT_ACTIVATED",
        "local_research_only": False,
        "bounded_authoritative_research": {
            "source_family": "erstebank.hu",
            "queries": [
                'site:erstebank.hu "PB Modell Portfólió"',
                'site:erstebank.hu "Modell Portfóliók" "Hányad"',
            ],
            "purpose": "Find a portfolio-specific definition of allocation semantics or an effective rebalance rule.",
            "outcome": "NOT_FOUND",
            "admitted_evidence": [],
            "reason_not_retained": "The bounded official-source search returned no applicable document; no snippet or generic finance convention was admitted.",
        },
        "graphify_admitted_items": [],
        "source_to_field_trace": [
            {
                "source_field": "Hányad (%)",
                "source_semantics": "UNKNOWN",
                "worksheet_import": "HEADER_TRANSLATIONS['hányad (%)'] -> 'Allocation (%)'",
                "worksheet_import_path": worksheet_importer_reference,
                "worksheet_import_sha256": _sha256(worksheet_importer_path),
                "sqlite_field": "model_portfolios.Allocation (%)",
                "model_use": "Reported-indicator allocation coverage only; no portfolio return construction is approved.",
                "model_use_path": methodology_reference,
                "model_use_sha256": _sha256(methodology_document_path),
            },
            {
                "source_field": "Workbook filename date",
                "source_semantics": "Snapshot/publication date only; effective trading timestamp UNKNOWN",
                "database_import": "extract_date() plus add_date_field()",
                "database_import_path": database_importer_reference,
                "database_import_sha256": _sha256(database_importer_path),
                "sqlite_field": "model_portfolios.Date",
                "repository_read_path": repository_reference,
                "repository_read_sha256": _sha256(model_repository_path),
                "model_use": "Observation date for point-in-time ranking; it is not an approved rebalance-effective timestamp.",
            },
            {
                "source_field": "Deviza",
                "source_semantics": "Constituent currency, not an evidenced portfolio reporting currency",
                "worksheet_import": "HEADER_TRANSLATIONS['deviza'] -> 'Currency'",
                "worksheet_import_path": worksheet_importer_reference,
                "worksheet_import_sha256": _sha256(worksheet_importer_path),
                "sqlite_field": "model_portfolios.Currency",
                "model_use": "Descriptive constituent field; it does not license FX conversion or nominal cross-currency aggregation.",
                "model_use_path": methodology_reference,
                "model_use_sha256": _sha256(methodology_document_path),
            },
        ],
        "rebalance_semantics": {
            "rebalance_rule": "UNKNOWN",
            "effective_date_rule": "UNKNOWN",
            "effective_time_rule": "UNKNOWN",
            "non_trading_day_rule": "UNKNOWN",
            "evidence_note": "A filename-derived snapshot date cannot be promoted to an execution convention.",
        },
        "portfolio_currency_assessment": _portfolio_currency_assessment(repository),
        "constituent_nav_return_semantics": _nav_return_semantic_assessment(nav_store),
        "blockers": blockers,
        "duplicate_resolution_reference": "data/audit/portfolio_duplicate_constituent_resolution.json",
        "resolution_summary": {
            "provenance_resolved_blockers": [
                "SNAPSHOT_WEIGHT_SEMANTICS_UNRESOLVED",
                "DUPLICATE_CONSTITUENT_ROWS_REQUIRE_RESOLUTION",
            ],
            "mandatory_blockers_remaining": [item["blocker"] for item in blockers],
            "external_research_performed": True,
            "portfolio_nav_constructed": False,
            "official_labels_created": False,
        },
    }
    payload["resolution_fingerprint"] = _fingerprint(payload)
    duplicate_payload["resolution_fingerprint"] = _fingerprint(duplicate_payload)
    return payload, duplicate_payload


def _portfolio_currency_assessment(
    repository: ModelPortfolioRepository,
) -> list[dict[str, object]]:
    currencies: defaultdict[str, set[str]] = defaultdict(set)
    for observation_date in repository.observation_dates():
        for holding in repository.load_holdings(observation_date):
            if holding.currency:
                currencies[holding.portfolio_name].add(holding.currency)
    return [
        {
            "portfolio_id": portfolio,
            "observed_constituent_currencies": sorted(values),
            "portfolio_reporting_currency": "UNKNOWN",
            "name_based_currency_inference": "PROHIBITED",
        }
        for portfolio, values in sorted(currencies.items())
    ]


def _nav_return_semantic_assessment(store: OfficialNavStore) -> list[dict[str, str]]:
    return [
        {
            "isin": isin,
            "source_provider": provider,
            "nav_currency": currency,
            "retained_value_type": value_type,
            "semantic_classification": "NAV_RETURN_SEMANTICS_UNRESOLVED",
            "investor_total_return_semantics": "UNKNOWN",
        }
        for isin, provider, currency, value_type in store.identities()
    ]


def write_resolution_artifact(path: Path, payload: dict[str, object]) -> None:
    _write_json_atomic(path, payload)


def _duplicate_resolution(
    repository: ModelPortfolioRepository, processed_workbook_dir: Path
) -> dict[str, object]:
    grouped: defaultdict[tuple[str, str, str], list[object]] = defaultdict(list)
    for observation_date in repository.observation_dates():
        for holding in repository.load_holdings(observation_date):
            if holding.isin is not None:
                grouped[(observation_date.isoformat(), holding.portfolio_name, holding.isin)].append(holding)
    cases: list[dict[str, object]] = []
    workbook_hashes: dict[str, str] = {}
    for (date_value, portfolio, isin), holdings in sorted(grouped.items()):
        if len(holdings) < 2:
            continue
        workbook = _workbook_for_date(processed_workbook_dir, date_value)
        source_rows = _source_rows(workbook, isin, portfolio)
        if len(source_rows) != len(holdings):
            raise PortfolioNavBlockerResolutionError(
                f"source duplicate rows do not reconcile for {date_value}:{portfolio}:{isin}"
            )
        status = classify_duplicate_source_rows(tuple(source_rows))
        reference = _relative_reference(workbook)
        workbook_hashes[reference] = _sha256(workbook)
        cases.append(
            {
                "decision_date": date_value,
                "portfolio_id": portfolio,
                "isin": isin,
                "source_path": reference,
                "source_sha256": workbook_hashes[reference],
                "source_rows": [
                    {
                        "normalized_frame_row": item.normalized_frame_row,
                        "allocation": item.allocation,
                        "product": item.product,
                        "currency": item.currency,
                        "asset_class": item.asset_class,
                    }
                    for item in source_rows
                ],
                "classification": status,
                "treatment": "PORTFOLIO_NAV_UNAVAILABLE_FOR_AFFECTED_CASE_UNTIL_SOURCE_SEMANTICS_RESOLVED",
                "automatic_isin_aggregation": "PROHIBITED",
            }
        )
    if len(cases) != 3:
        raise PortfolioNavBlockerResolutionError(
            f"expected exactly three duplicate constituent cases, found {len(cases)}"
        )
    return {
        "schema_version": 1,
        "validation_status": "DUPLICATE_CONSTITUENT_ROWS_PARTIALLY_RESOLVED",
        "case_count": len(cases),
        "source_workbook_sha256": _sha256(_workbook_for_date(processed_workbook_dir, "2024-09-17")),
        "cases": cases,
        "invariant": "No duplicate ISIN is grouped, summed, dropped, or renormalized without source semantics.",
    }


def _source_rows(workbook: Path, isin: str, portfolio: str) -> list[DuplicateSourceRow]:
    frame = next(iter(read_target_worksheet(workbook).values()))
    headers = [str(item).strip() for item in frame.iloc[0]]
    header_index = {normalized_key(header): index for index, header in enumerate(headers)}
    required = {"portfólió neve", "isin", "hányad (%)", "termék", "deviza", "eszközosztály"}
    if not required <= set(header_index):
        raise PortfolioNavBlockerResolutionError("source worksheet lacks duplicate-resolution columns")
    rows: list[DuplicateSourceRow] = []
    for position in range(1, len(frame)):
        row = frame.iloc[position]
        if str(row.iloc[header_index["portfólió neve"]]).strip() != portfolio:
            continue
        if str(row.iloc[header_index["isin"]]).strip() != isin:
            continue
        rows.append(
            DuplicateSourceRow(
                normalized_frame_row=position,
                allocation=str(row.iloc[header_index["hányad (%)"]]).strip(),
                product=str(row.iloc[header_index["termék"]]).strip(),
                currency=str(row.iloc[header_index["deviza"]]).strip(),
                asset_class=str(row.iloc[header_index["eszközosztály"]]).strip(),
            )
        )
    return rows


def _workbook_for_date(directory: Path, date_value: str) -> Path:
    compact = date_value.replace("-", "")
    matches = sorted(directory.glob(f"*{compact}.xls"))
    if len(matches) != 1:
        raise PortfolioNavBlockerResolutionError(
            f"expected one retained workbook for {date_value}, found {len(matches)}"
        )
    return matches[0]


def _blocker(
    *,
    blocker: str,
    after: str,
    evidence_type: str,
    source: str,
    local_path: str,
    sha256: str,
    section_or_location: str,
    reasoning: str,
    missing: str,
) -> dict[str, object]:
    return {
        "blocker": blocker,
        "previous_status": "UNRESOLVED",
        "new_status": after,
        "evidence_type": evidence_type,
        "source": source,
        "local_path": local_path,
        "sha256": sha256,
        "section_or_location": section_or_location,
        "reasoning": reasoning,
        "evidence_still_missing": missing,
    }


def _relative_reference(path: Path) -> str:
    root = Path.cwd().resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise PortfolioNavBlockerResolutionError("evidence path is outside repository") from exc


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise PortfolioNavBlockerResolutionError(f"evidence file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
