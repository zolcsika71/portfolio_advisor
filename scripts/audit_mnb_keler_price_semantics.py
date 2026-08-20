"""Audit retained MNB/KELER price-semantics evidence and actual quotations offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.mnb_keler_price_semantics import (
    PriceSemanticsError,
    diagnostic_price_analysis,
    return_suitability,
)
from portfolio_advisor.history.mnb_otc import TARGET_HU_ISIN, MnbOtcRepository


def load_ledger(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PriceSemanticsError(f"Unable to load price-semantics ledger: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("research_status") not in {
        "MNB_OTC_PRICE_SEMANTICS_VALIDATED", "MNB_OTC_PRICE_SEMANTICS_PARTIAL", "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND", "MNB_OTC_PRICE_SEMANTICS_CONFLICT"
    } or payload.get("stopping_rule_completed") is not True:
        raise PriceSemanticsError("Price-semantics ledger is malformed or incomplete")
    if any(payload.get(field) is not False for field in ("nav_equivalent", "backtest_return_series_approved", "usable_for_backtest")):
        raise PriceSemanticsError("Price-semantics ledger has unsafe approval flags")
    return payload


def question(answer: str, rationale: str) -> dict[str, str]:
    return {"answer": answer, "rationale": rationale}


def build_audit(ledger: dict[str, object], database: Path) -> dict[str, object]:
    observations = MnbOtcRepository(database).observations(TARGET_HU_ISIN)
    diagnostic = diagnostic_price_analysis(observations)
    fields = ledger.get("questions")
    if not isinstance(fields, dict):
        raise PriceSemanticsError("Price-semantics ledger has no field questions")
    def unknown(field: str) -> dict[str, str]:
        item = fields.get(field)
        if not isinstance(item, dict) or item.get("answer") not in {"YES", "UNKNOWN"}:
            raise PriceSemanticsError("Price-semantics field answer is malformed")
        return question(str(item["answer"]), "No locally retained authoritative direct report-field definition was accepted.")
    return {
        "schema_version": 1,
        "source": "mnb_otc",
        "isin": TARGET_HU_ISIN,
        "series": "K2025/23",
        "instrument_name": "K250604 Egyéves Magyar Állampapír",
        "currency": "HUF",
        "price_semantics_status": ledger["research_status"],
        "evidence_ledger": "data/audit/mnb_keler_price_semantics_evidence.json",
        "stopping_rule_completed": True,
        "accepted_source_documents": ledger.get("accepted_documents"),
        "questions": {
            "q1_percentage_of_par": unknown("percentage_of_par"),
            "q2_clean_price": unknown("clean_price"),
            "q3_accrued_interest_excluded": unknown("accrued_interest_excluded"),
            "q4_dirty_price": unknown("dirty_price"),
            "q5_accrued_interest_included": unknown("accrued_interest_included"),
            "q6_transaction_weighted_average": unknown("average_transaction_weighted"),
            "q7_nominal_volume_weighted_average": unknown("average_nominal_volume_weighted"),
            "q8_arithmetic_mean": unknown("average_arithmetic_mean"),
            "q9_minimum_maximum_meaning": question(
                "YES" if fields.get("minimum_semantics", {}).get("answer") == "YES" and fields.get("maximum_semantics", {}).get("answer") == "YES" else "UNKNOWN",
                "Both minimum and maximum require direct field definitions.",
            ),
            "q10_tetelszam_trade_count": unknown("tetelszam_trade_count"),
            "q11_tetelszam_settlement_count": unknown("tetelszam_settlement_count"),
            "q12_date_semantics": unknown("date_semantics"),
            "q13_fees_included": unknown("fees_included"),
            "q14_descriptive_quoted_price_comparison": question("YES", "Actual source quotations may be compared descriptively; this is not an investment return."),
            "q15_direct_holding_period_return": question("NO", "Return methodology, accrued-interest treatment, cash-flow integration, and boundaries are not approved."),
            "q16_coupon_redemption_integration": question("NO", "No approved transformation or return methodology is introduced by this audit."),
            "q17_exact_backtest_boundary_price": question("NO", "Weekly period aggregates are not approved exact boundaries; no nearest-date substitution is allowed."),
            "q18_daily_conversion": question("NO", "Weekly OTC aggregates are not converted into a daily series."),
        },
        "diagnostic_analysis": diagnostic,
        "return_suitability": return_suitability(),
        "validated_price_properties": [name for name, item in sorted(fields.items()) if isinstance(item, dict) and item.get("answer") == "YES"],
        "unknown_price_properties": ledger.get("remaining_unknowns"),
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
        "synthetic_observations_created": 0,
        "zero_prices_created": 0,
        "daily_resampling_performed": False,
        "return_metrics_implemented": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=Path("data/audit/mnb_keler_price_semantics_evidence.json"))
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/mnb_keler_price_semantics.json"))
    args = parser.parse_args()
    try:
        audit = build_audit(load_ledger(args.ledger), args.database)
    except PriceSemanticsError as exc:
        print(f"MNB/KELER price-semantics audit failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"MNB/KELER price semantics: {audit['price_semantics_status']}")
    print("Backtest return series approved: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
