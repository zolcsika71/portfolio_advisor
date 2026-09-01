"""Reviewed Capital Defensive construction-policy contract and strict loader."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import yaml

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json

from .models import PortfolioObjective

CONSTRUCTION_POLICY_SCHEMA_VERSION = 1
CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID = "CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY"
CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_VERSION = "1.0.0"
CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT = (
    "data/knowledge/validated_rules/capital_defensive_construction.yaml"
)
_SEMVER = re.compile(r"0|[1-9][0-9]*\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_CURRENCIES = ("EUR", "USD", "HUF")
_REFERENCE_RATES = {
    "EUR": (
        "€STR",
        "European Central Bank",
        (
            "https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/"
            "euro_short-term_rate/html/index.en.html"
        ),
    ),
    "USD": (
        "SOFR",
        "Federal Reserve Bank of New York",
        "https://www.newyorkfed.org/markets/reference-rates/sofr",
    ),
    "HUF": (
        "HUFONIA",
        "Magyar Nemzeti Bank",
        "https://statisztika.mnb.hu/statistical-topics/monetary-policy-statistics",
    ),
}


class ConstructionPolicyValidationError(ValueError):
    """Raised when a construction policy or run input is not exactly governed."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConstructionPolicyValidationError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


@dataclass(frozen=True, slots=True)
class ReferenceRatePolicy:
    """One approved official currency benchmark identity."""

    currency: str
    benchmark: str
    administrator: str
    official_source_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "administrator": self.administrator,
            "benchmark": self.benchmark,
            "official_source_url": self.official_source_url,
        }


@dataclass(frozen=True, slots=True)
class CapitalDefensiveConstructionPolicy:
    """Immutable, fully explicit reviewed policy; it performs no construction."""

    schema_version: int
    policy_id: str
    version: str
    objective: str
    strategy: str
    status: str
    runtime_construction_readiness: str
    cash_input: tuple[tuple[str, object], ...]
    currency_behavior: tuple[tuple[str, object], ...]
    allocation: tuple[tuple[str, object], ...]
    diversification: tuple[tuple[str, object], ...]
    candidate_generation: tuple[tuple[str, object], ...]
    historical_nav: tuple[tuple[str, object], ...]
    portfolio_risk: tuple[tuple[str, object], ...]
    reference_rates: tuple[ReferenceRatePolicy, ...]
    reference_rate_methodology: tuple[tuple[str, object], ...]
    runtime_dependencies: tuple[tuple[str, str], ...]
    artifact_reference: str

    @property
    def identity(self) -> tuple[PortfolioObjective, str, str]:
        return (
            PortfolioObjective.CAPITAL_CONSERVATION,
            self.policy_id,
            self.version,
        )

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.artifact_payload())

    @property
    def supported_currencies(self) -> tuple[str, ...]:
        value = dict(self.cash_input)["supported_initial_currencies"]
        if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
            raise ConstructionPolicyValidationError("frozen supported currencies are invalid")
        return value

    def artifact_payload(self) -> dict[str, object]:
        return {
            "allocation": _thaw(self.allocation),
            "candidate_generation": _thaw(self.candidate_generation),
            "cash_input": _thaw(self.cash_input),
            "currency_behavior": _thaw(self.currency_behavior),
            "diversification": _thaw(self.diversification),
            "historical_nav": _thaw(self.historical_nav),
            "objective": self.objective,
            "policy_id": self.policy_id,
            "portfolio_risk": _thaw(self.portfolio_risk),
            "reference_rate_methodology": _thaw(self.reference_rate_methodology),
            "reference_rates": {
                rate.currency: rate.to_dict() for rate in self.reference_rates
            },
            "runtime_construction_readiness": self.runtime_construction_readiness,
            "runtime_dependencies": dict(self.runtime_dependencies),
            "schema_version": self.schema_version,
            "status": self.status,
            "strategy": self.strategy,
            "version": self.version,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.artifact_payload())

    def registry_dict(self) -> dict[str, object]:
        return {
            "artifact_reference": self.artifact_reference,
            "fingerprint": self.fingerprint,
            "objective": PortfolioObjective.CAPITAL_CONSERVATION.value,
            "policy_id": self.policy_id,
            "runtime_construction_readiness": self.runtime_construction_readiness,
            "schema_version": self.schema_version,
            "status": self.status,
            "strategy": self.strategy,
            "version": self.version,
        }


def load_capital_defensive_construction_policy(
    path: Path,
    *,
    artifact_reference: str = CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
) -> CapitalDefensiveConstructionPolicy:
    """Load and strictly validate the sole reviewed v1 construction contract."""
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except OSError as error:
        raise ConstructionPolicyValidationError(f"could not read construction policy: {path}") from error
    except (yaml.YAMLError, ConstructionPolicyValidationError) as error:
        raise ConstructionPolicyValidationError(f"malformed construction policy: {error}") from error
    root = _exact_mapping(
        raw,
        {
            "schema_version", "policy_id", "version", "objective", "strategy", "status",
            "runtime_construction_readiness", "cash_input", "currency_behavior", "allocation",
            "diversification", "candidate_generation", "historical_nav", "portfolio_risk",
            "reference_rates", "reference_rate_methodology", "runtime_dependencies",
        },
        "construction policy",
    )
    if root["schema_version"] != CONSTRUCTION_POLICY_SCHEMA_VERSION or isinstance(
        root["schema_version"], bool
    ):
        raise ConstructionPolicyValidationError("unsupported construction-policy schema_version")
    if root["policy_id"] != CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID:
        raise ConstructionPolicyValidationError("unsupported construction policy identity")
    version = _string(root["version"], "version")
    if _SEMVER.fullmatch(version) is None or version != CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_VERSION:
        raise ConstructionPolicyValidationError("unsupported construction policy version")
    _expect(root, "objective", "CAPITAL_CONSERVATION")
    _expect(root, "strategy", "CAPITAL_DEFENSIVE")
    _expect(root, "status", "APPROVED")
    _expect(root, "runtime_construction_readiness", "NOT_IMPLEMENTED")
    _validate_artifact_reference(artifact_reference)

    cash = _section(root, "cash_input", {
        "input", "exactly_one_positive_currency_amount", "supported_initial_currencies",
        "amount_semantics", "binary_floating_point_inputs", "implicit_conversion",
    })
    _expect(cash, "input", "PER_RUN_CASH_AMOUNTS_BY_CURRENCY")
    _expect(cash, "exactly_one_positive_currency_amount", True)
    if _require_list(cash["supported_initial_currencies"], "supported_initial_currencies") != list(_CURRENCIES):
        raise ConstructionPolicyValidationError("supported currencies differ from reviewed policy")
    _expect(cash, "amount_semantics", "EXACT_DECIMAL")
    _expect(cash, "binary_floating_point_inputs", "REJECTED")
    _expect(cash, "implicit_conversion", "PROHIBITED")

    currency = _section(root, "currency_behavior", {
        "currencies_per_run", "instrument_currency_match", "fx_conversion",
        "fewer_than_required_same_currency_instruments", "currency_substitution",
    })
    _expect(currency, "currencies_per_run", 1)
    _expect(currency, "instrument_currency_match", "REQUIRED")
    _expect(currency, "fx_conversion", "PROHIBITED")
    _expect(currency, "fewer_than_required_same_currency_instruments", "UNAVAILABLE")
    _expect(currency, "currency_substitution", "PROHIBITED")

    allocation = _section(root, "allocation", {
        "cash_reserve_weight", "security_count", "weight_per_security", "total_security_weight",
        "total_cash_weight", "weights_are_governed_allocation_contract",
        "ranking_feature_weights_are_portfolio_weights", "transaction_units", "order_quantities",
        "brokerage_rounding",
    })
    _decimal_equals(allocation, "cash_reserve_weight", "0.20")
    security_count = _integer(allocation["security_count"], "security_count")
    _expect(allocation, "security_count", 8)
    _decimal_equals(allocation, "weight_per_security", "0.10")
    _decimal_equals(allocation, "total_security_weight", "0.80")
    _decimal_equals(allocation, "total_cash_weight", "0.20")
    if (
        _decimal(allocation["weight_per_security"], "weight_per_security")
        * security_count
        != _decimal(allocation["total_security_weight"], "total_security_weight")
        or _decimal(allocation["total_security_weight"], "total_security_weight")
        + _decimal(allocation["total_cash_weight"], "total_cash_weight")
        != Decimal("1.00")
    ):
        raise ConstructionPolicyValidationError("security and cash weights do not reconcile to 100%")
    _expect(allocation, "weights_are_governed_allocation_contract", True)
    _expect(allocation, "ranking_feature_weights_are_portfolio_weights", False)
    for field in ("transaction_units", "order_quantities", "brokerage_rounding"):
        _expect(allocation, field, "OUTSIDE_SCOPE")

    diversification = _section(root, "diversification", {
        "minimum_distinct_conflict_free_groups", "maximum_group_weight",
        "maximum_holdings_per_group", "grouping_basis",
        "missing_conflicting_or_unmappable_category_evidence", "issuer_concentration",
    })
    minimum_groups = _integer(
        diversification["minimum_distinct_conflict_free_groups"],
        "minimum_distinct_conflict_free_groups",
    )
    _expect(diversification, "minimum_distinct_conflict_free_groups", 3)
    _decimal_equals(diversification, "maximum_group_weight", "0.40")
    _expect(diversification, "maximum_holdings_per_group", 4)
    _expect(diversification, "grouping_basis", "ASSET_AND_SUB_ASSET_GROUP")
    _expect(diversification, "missing_conflicting_or_unmappable_category_evidence", "REJECTED")
    _expect(diversification, "issuer_concentration", "NOT_ENFORCED_EVIDENCE_UNAVAILABLE")
    if minimum_groups > security_count:
        raise ConstructionPolicyValidationError("invalid minimum diversification group count")
    if (
        _decimal(diversification["maximum_group_weight"], "maximum_group_weight")
        / _decimal(allocation["weight_per_security"], "weight_per_security")
        != diversification["maximum_holdings_per_group"]
    ):
        raise ConstructionPolicyValidationError("group weight and holding limit conflict")

    candidate = _section(root, "candidate_generation", {
        "candidates_per_run_currency", "selection_objective",
        "preserve_reviewed_instrument_eligibility_and_rank_order", "feasible_set_objective",
        "exact_tie_break", "randomized_search", "opaque_exhaustive_output",
        "implementation_status",
    })
    _expect(candidate, "candidates_per_run_currency", 1)
    _expect(candidate, "selection_objective", "HIGHEST_RANKED_FEASIBLE_EIGHT_INSTRUMENT_SET")
    _expect(candidate, "preserve_reviewed_instrument_eligibility_and_rank_order", True)
    _expect(candidate, "feasible_set_objective", "MINIMIZE_ORDERED_RANK_VECTOR")
    _expect(candidate, "exact_tie_break", "LEXICOGRAPHICALLY_ORDERED_ISIN_TUPLE")
    _expect(candidate, "randomized_search", "PROHIBITED")
    _expect(candidate, "opaque_exhaustive_output", "PROHIBITED")
    _expect(candidate, "implementation_status", "NOT_IMPLEMENTED")

    nav = _section(root, "historical_nav", {
        "minimum_history_span_calendar_days", "minimum_aligned_return_intervals",
        "maximum_observation_staleness_calendar_days", "quality",
        "common_aligned_return_window_all_instruments", "interpolation",
        "nearest_date_substitution", "proxy_instrument", "failure_status",
    })
    history_days = _integer(nav["minimum_history_span_calendar_days"], "minimum_history_span_calendar_days")
    aligned_intervals = _integer(nav["minimum_aligned_return_intervals"], "minimum_aligned_return_intervals")
    staleness_days = _integer(
        nav["maximum_observation_staleness_calendar_days"],
        "maximum_observation_staleness_calendar_days",
    )
    if not 0 <= staleness_days < history_days:
        raise ConstructionPolicyValidationError("invalid staleness/history relationship")
    if not 0 < aligned_intervals <= history_days:
        raise ConstructionPolicyValidationError("invalid aligned interval/history relationship")
    _expect(nav, "minimum_history_span_calendar_days", 365)
    _expect(nav, "minimum_aligned_return_intervals", 252)
    _expect(nav, "maximum_observation_staleness_calendar_days", 30)
    _expect(nav, "quality", "ADMITTED_AND_VALIDATED")
    _expect(nav, "common_aligned_return_window_all_instruments", "REQUIRED")
    for field in ("interpolation", "nearest_date_substitution", "proxy_instrument"):
        _expect(nav, field, "PROHIBITED")
    _expect(nav, "failure_status", "UNAVAILABLE")

    risk = _section(root, "portfolio_risk", {"method", "weighted_sum_of_individual_volatilities"})
    if _require_list(risk["method"], "portfolio_risk.method") != [
        "ALIGNED_CONSTITUENT_NAV_SERIES", "CONSTITUENT_RETURNS",
        "WEIGHTED_PORTFOLIO_RETURN_SERIES", "PORTFOLIO_VOLATILITY",
        "MAXIMUM_DRAWDOWN", "SUPPORTED_RISK_ADJUSTED_METRICS",
    ]:
        raise ConstructionPolicyValidationError("portfolio-risk method differs from reviewed policy")
    _expect(risk, "weighted_sum_of_individual_volatilities", "PROHIBITED")

    rates = _parse_reference_rates(root["reference_rates"])
    methodology = _section(root, "reference_rate_methodology", {
        "preserved_fields", "official_day_count_and_compounding_conventions",
        "deterministic_alignment_to_portfolio_return_dates", "fill_unknown_observations_with_zero",
        "substitute_policy_or_base_rates", "sharpe_reference_return",
        "sortino_minimum_acceptable_return", "sharpe_runtime", "sortino_runtime",
    })
    if _require_list(methodology["preserved_fields"], "preserved_fields") != [
        "administrator", "series_identity", "observation_date", "publication_date",
        "source_url", "value", "units", "quality",
    ]:
        raise ConstructionPolicyValidationError("reference-rate provenance fields are incomplete")
    for field in (
        "official_day_count_and_compounding_conventions",
        "deterministic_alignment_to_portfolio_return_dates",
    ):
        _expect(methodology, field, "REQUIRED")
    for field in ("fill_unknown_observations_with_zero", "substitute_policy_or_base_rates"):
        _expect(methodology, field, "PROHIBITED")
    _expect(methodology, "sharpe_reference_return", "ALIGNED_CURRENCY_BENCHMARK")
    _expect(
        methodology,
        "sortino_minimum_acceptable_return",
        "GOVERNED_ALIGNED_CURRENCY_BENCHMARK",
    )
    for field in ("sharpe_runtime", "sortino_runtime"):
        _expect(methodology, field, "UNAVAILABLE_PENDING_VALIDATED_INGESTION")

    dependencies = _section(root, "runtime_dependencies", {
        "schema", "current_nav", "official_reference_rates", "portfolio_construction",
        "portfolio_persistence", "portfolio_metrics",
    })
    for field in ("schema", "current_nav", "official_reference_rates"):
        _expect(dependencies, field, "MISSING")
    for field in ("portfolio_construction", "portfolio_persistence", "portfolio_metrics"):
        _expect(dependencies, field, "NOT_IMPLEMENTED")

    return CapitalDefensiveConstructionPolicy(
        schema_version=CONSTRUCTION_POLICY_SCHEMA_VERSION,
        policy_id=CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ID,
        version=version,
        objective="CAPITAL_CONSERVATION",
        strategy="CAPITAL_DEFENSIVE",
        status="APPROVED",
        runtime_construction_readiness="NOT_IMPLEMENTED",
        cash_input=_freeze(cash),
        currency_behavior=_freeze(currency),
        allocation=_freeze(allocation),
        diversification=_freeze(diversification),
        candidate_generation=_freeze(candidate),
        historical_nav=_freeze(nav),
        portfolio_risk=_freeze(risk),
        reference_rates=rates,
        reference_rate_methodology=_freeze(methodology),
        runtime_dependencies=tuple(sorted((str(key), str(value)) for key, value in dependencies.items())),
        artifact_reference=artifact_reference,
    )


def validate_construction_cash_input(
    policy: CapitalDefensiveConstructionPolicy,
    cash_by_currency: Mapping[str, Decimal],
) -> tuple[str, Decimal]:
    """Validate one exact positive cash amount without conversion or allocation."""
    if not isinstance(cash_by_currency, Mapping) or len(cash_by_currency) != 1:
        raise ConstructionPolicyValidationError("exactly one positive currency amount is required")
    currency, amount = next(iter(cash_by_currency.items()))
    if currency not in policy.supported_currencies:
        raise ConstructionPolicyValidationError(f"unsupported construction currency: {currency!r}")
    if not isinstance(amount, Decimal):
        raise ConstructionPolicyValidationError("cash amount must use exact Decimal semantics")
    if not amount.is_finite() or amount <= Decimal(0):
        raise ConstructionPolicyValidationError("cash amount must be finite and positive")
    return currency, amount


def _parse_reference_rates(value: object) -> tuple[ReferenceRatePolicy, ...]:
    rates = _exact_mapping(value, set(_CURRENCIES), "reference_rates")
    result: list[ReferenceRatePolicy] = []
    for currency in _CURRENCIES:
        item = _exact_mapping(
            rates[currency], {"benchmark", "administrator", "official_source_url"},
            f"reference_rates.{currency}",
        )
        expected = _REFERENCE_RATES[currency]
        actual = tuple(_string(item[name], f"{currency}.{name}") for name in (
            "benchmark", "administrator", "official_source_url"
        ))
        if actual != expected:
            raise ConstructionPolicyValidationError(f"unapproved reference-rate source for {currency}")
        result.append(ReferenceRatePolicy(currency, *actual))
    return tuple(result)


def _section(root: Mapping[str, object], name: str, fields: set[str]) -> dict[str, object]:
    return _exact_mapping(root[name], fields, name)


def _exact_mapping(value: object, fields: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ConstructionPolicyValidationError(f"{label} must be a mapping")
    keys = set(value)
    if keys != fields or any(not isinstance(key, str) for key in keys):
        raise ConstructionPolicyValidationError(
            f"{label} fields differ: unknown={sorted(str(key) for key in keys - fields)}, "
            f"missing={sorted(fields - keys)}"
        )
    return {str(key): item for key, item in value.items()}


def _expect(data: Mapping[str, object], field: str, expected: object) -> None:
    value = data[field]
    if value != expected or type(value) is not type(expected):
        raise ConstructionPolicyValidationError(f"{field} differs from reviewed value {expected!r}")


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConstructionPolicyValidationError(f"{field} must be an exact non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConstructionPolicyValidationError(f"{field} must be an integer")
    return value


def _require_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ConstructionPolicyValidationError(f"{field} must be a list of non-empty strings")
    return value


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ConstructionPolicyValidationError(f"{field} must be a quoted exact decimal")
    try:
        result = Decimal(value)
    except ArithmeticError as error:
        raise ConstructionPolicyValidationError(f"{field} is not an exact decimal") from error
    if not result.is_finite():
        raise ConstructionPolicyValidationError(f"{field} must be finite")
    return result


def _decimal_equals(data: Mapping[str, object], field: str, expected: str) -> None:
    if _decimal(data[field], field) != Decimal(expected) or data[field] != expected:
        raise ConstructionPolicyValidationError(f"{field} differs from reviewed value {expected}")


def _validate_artifact_reference(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ConstructionPolicyValidationError("artifact_reference must be repository-relative POSIX")


def _freeze(value: Mapping[str, object]) -> tuple[tuple[str, object], ...]:
    return tuple(sorted((key, _freeze_value(item)) for key, item in value.items()))


def _freeze_value(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    return value


def _thaw(value: tuple[tuple[str, object], ...]) -> dict[str, object]:
    return {key: _thaw_value(item) for key, item in value}


def _thaw_value(value: object) -> object:
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    return value
