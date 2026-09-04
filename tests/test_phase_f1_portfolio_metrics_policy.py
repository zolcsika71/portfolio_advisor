"""Milestone 11C Phase F1 methodology-policy contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from portfolio_advisor.metrics import (
    PHASE_F1_DECISION_TOKENS,
    PHASE_F1_POLICY_ARTIFACT,
    PHASE_F1_POLICY_FINGERPRINT,
    PHASE_F1_POLICY_ID,
    PHASE_F1_POLICY_VERSION,
    PhaseF1PolicyValidationError,
    load_phase_f1_portfolio_metrics_policy,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / PHASE_F1_POLICY_ARTIFACT


def _mutated_policy(tmp_path: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    value = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    mutate(value)
    path = tmp_path / "phase-f1.yaml"
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_approved_policy_loads_with_exact_identity_ballot_and_fingerprint() -> None:
    policy = load_phase_f1_portfolio_metrics_policy(ARTIFACT)
    assert (policy.policy_id, policy.version, policy.schema_version) == (
        PHASE_F1_POLICY_ID,
        PHASE_F1_POLICY_VERSION,
        1,
    )
    assert dict(policy.decision_tokens) == PHASE_F1_DECISION_TOKENS
    assert policy.fingerprint == PHASE_F1_POLICY_FINGERPRINT
    assert json.loads(policy.canonical_json()) == policy.artifact_payload()


def test_policy_is_deterministic_and_yaml_key_order_independent(tmp_path: Path) -> None:
    value = yaml.safe_load(ARTIFACT.read_text(encoding="utf-8"))
    reordered = {key: value[key] for key in reversed(tuple(value))}
    path = tmp_path / "reordered.yaml"
    path.write_text(
        yaml.safe_dump(reordered, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    first = load_phase_f1_portfolio_metrics_policy(ARTIFACT)
    second = load_phase_f1_portfolio_metrics_policy(path)
    assert first.fingerprint == second.fingerprint
    assert first.render_audit() == second.render_audit()


def test_unknown_missing_or_changed_policy_content_is_rejected(tmp_path: Path) -> None:
    unknown = _mutated_policy(tmp_path, lambda value: value.update({"fallback": "PROHIBITED"}))
    with pytest.raises(PhaseF1PolicyValidationError, match="differs"):
        load_phase_f1_portfolio_metrics_policy(unknown)

    missing = _mutated_policy(tmp_path, lambda value: value.pop("precision"))
    with pytest.raises(PhaseF1PolicyValidationError, match="differs"):
        load_phase_f1_portfolio_metrics_policy(missing)

    changed = _mutated_policy(
        tmp_path,
        lambda value: value["cash"].update({"return_treatment": "BENCHMARK_ACCRUAL"}),
    )
    with pytest.raises(PhaseF1PolicyValidationError, match="differs"):
        load_phase_f1_portfolio_metrics_policy(changed)


def test_duplicate_keys_and_binary_float_values_are_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    with pytest.raises(PhaseF1PolicyValidationError, match="duplicate YAML key"):
        load_phase_f1_portfolio_metrics_policy(duplicate)

    floating = _mutated_policy(
        tmp_path,
        lambda value: value["portfolio_dynamics"].update({"initial_cash_weight": 0.2}),
    )
    with pytest.raises(PhaseF1PolicyValidationError, match="floating-point"):
        load_phase_f1_portfolio_metrics_policy(floating)


def test_supplementary_prefix_counts_and_ranges_are_exact_but_not_admitted() -> None:
    payload = load_phase_f1_portfolio_metrics_policy(ARTIFACT).artifact_payload()
    supplement = payload["supplementary_nav"]
    assert isinstance(supplement, dict)
    assert supplement["status"] == "APPROVED_NOT_ADMITTED"
    assert supplement["admission_semantics"] == "APPEND_ONLY_NEW_MANIFESTS"
    assert supplement["replace_existing_evidence"] is False
    currencies = supplement["currencies"]
    assert isinstance(currencies, dict)
    expected = {
        "EUR": ("2025-05-23", 527, 2486),
        "HUF": ("2025-08-14", 83, 2108),
    }
    for currency, (start, count, resulting) in expected.items():
        details = currencies[currency]
        assert isinstance(details, dict)
        instruments = details["instruments"]
        assert isinstance(instruments, list)
        assert len(instruments) == 8
        assert sum(item["observation_count"] for item in instruments) == count
        assert {item["first_date"] for item in instruments} == {start}
        assert details["supplementary_observation_count"] == count
        assert details["resulting_phase_e_observation_count"] == resulting


def test_decision_timestamp_is_bound_to_phase_e_release_artifact() -> None:
    payload = load_phase_f1_portfolio_metrics_policy(ARTIFACT).artifact_payload()
    context = payload["decision_context"]
    assert isinstance(context, dict)
    assert context == {
        "decision_as_of_semantics": "PHASE_E_RELEASE_RESEARCH_TIMESTAMP",
        "decision_as_of_utc": "2026-09-04T12:24:23.000000Z",
        "immutable_repository_artifact": "79ab552afdceed7d5feacee42e0a7d1ade2003f8",
        "immutable_repository_epoch": 1788524663,
        "immutable_repository_timezone_offset": "+0200",
        "investment_horizon_is_metric_lookback": False,
        "nav_evidence_cutoff": "2026-08-31",
    }


def test_volatility_is_an_explicit_model_contract_not_an_implementation() -> None:
    payload = load_phase_f1_portfolio_metrics_policy(ARTIFACT).artifact_payload()
    volatility = payload["volatility"]
    assert isinstance(volatility, dict)
    assert volatility["model_class"] == "CONSTANT_DRIFT_CONSTANT_DIFFUSION"
    assert volatility["interpretation"] == (
        "MODEL_BASED_ESTIMATOR_FOR_IRREGULAR_OBSERVED_INTERVALS"
    )
    assert volatility["model_free_claim"] == "PROHIBITED"
    assert volatility["universal_validity_claim"] == "PROHIBITED"
    assert volatility["day_count"] == "ACT_365_FIXED"
    assert volatility["degrees_of_freedom"] == "N_MINUS_1"
    assert volatility["governed_minimum_return_intervals"] == 252
    assert volatility["phase_f2_required_tests"] == [
        "EXACT_FORMULA",
        "IRREGULAR_GAPS",
        "EQUAL_GAP_EQUIVALENCE",
        "ZERO_VARIANCE",
        "INVALID_WEALTH",
        "DETERMINISTIC_DECIMAL",
        "ENDPOINT_RECONCILIATION",
    ]


def test_distribution_unknowns_block_the_real_eur_candidate() -> None:
    payload = load_phase_f1_portfolio_metrics_policy(ARTIFACT).artifact_payload()
    distributions = payload["distributions"]
    assert isinstance(distributions, dict)
    statuses = distributions["eur_instruments"]
    assert isinstance(statuses, dict)
    assert len(statuses) == 8
    assert set(statuses.values()) == {"UNKNOWN"}
    assert distributions["infer_from_fund_name"] == "PROHIBITED"
    assert distributions["current_eur_candidate_status"] == "BLOCKED"


def test_decimal_precision_output_scale_and_reconciliation_are_separate() -> None:
    payload = load_phase_f1_portfolio_metrics_policy(ARTIFACT).artifact_payload()
    precision = payload["precision"]
    assert isinstance(precision, dict)
    assert precision["decimal_context_precision_significant_digits"] == 50
    assert precision["decimal_rounding_mode"] == "ROUND_HALF_EVEN"
    assert precision["canonical_output_scale_decimal_places"] == 18
    assert precision["canonical_scale_is_economic_accuracy_claim"] is False
    assert precision["endpoint_reconciliation_relative_tolerance"] == "0." + "0" * 39 + "1"
    assert precision["derived_weight_sum_absolute_tolerance"] == "0." + "0" * 39 + "1"
    assert precision["ranking_comparison"] == "EXACT_UNQUANTIZED_DECIMAL_NO_EPSILON_TIES"


def test_policy_audit_is_byte_stable_and_all_runtime_work_remains_blocked() -> None:
    policy = load_phase_f1_portfolio_metrics_policy(ARTIFACT)
    assert policy.render_audit() == policy.render_audit()
    audit = json.loads(policy.render_audit())
    assert audit["eur_real_candidate_status"] == "BLOCKED"
    assert audit["huf_status"] == (
        "BLOCKED_PENDING_AUTHORITATIVE_HUFONIA_DAY_COUNT_AND_APPLICABILITY"
    )
    boundary = audit["implementation_boundary"]
    assert boundary["implemented_here"] == "POLICY_AND_CONTRACT_ONLY"
    assert boundary["supplementary_nav_admission"] == "NOT_PERFORMED"
    assert boundary["phase_f2_metric_engine"] == "NOT_IMPLEMENTED"
    assert boundary["real_portfolio_construction"] == "NOT_AUTHORIZED"
    assert boundary["database_migration"] == "NOT_PERFORMED"
    assert boundary["production_cutover"] == "NOT_AUTHORIZED"
