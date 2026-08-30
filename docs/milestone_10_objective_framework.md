# Milestone 10 — Objective framework

Milestone 10 separates a stable portfolio objective from the reviewed,
versioned policy that may implement it. The relational database remains
objective-neutral: no objective-specific fact table, holding, score, rank,
recommendation, or outcome is created.

## Supported objectives and policy availability

| Objective | Active-policy availability | Registered active policy |
| --- | --- | --- |
| `capital_conservation` | `VALIDATED_ACTIVE_POLICY` | `CAPITAL_PRESERVATION_RANKING_POLICY` v1.0.1 |
| `dividend_portfolio` | `NO_VALIDATED_ACTIVE_POLICY` | None |

The capital policy registration is loaded from and validated against
`data/knowledge/validated_rules/capital_preservation_ranking.yaml` plus the
existing policy-contract evidence. Its artifact path is repository-relative,
and its SHA-256 is computed from the unchanged bytes. The mandate is a
3–12-month horizon, capital conservation first, and risk-adjusted return
second.

The dividend objective is supported as an identity only. Active-policy
resolution fails with `NoValidatedActivePolicyError`; there is no placeholder,
cross-objective fallback, invented metric, eligibility rule, weight,
construction rule, or success criterion.

## Capability boundary

The active capital policy has reviewed eligibility and ranking capabilities.
Construction, finalist comparison, and outcome success criteria remain
`NOT_IMPLEMENTED`. An objective without an active validated policy reports
`NO_VALIDATED_ACTIVE_POLICY` for every capability. Policy activation does not
authorize schema-v3 application cutover.

The registry rejects duplicate or conflicting registrations and keeps policy
versions distinct. It resolves only one explicitly approved and active policy
for an exact objective. Unknown objectives, absent versions, unavailable
policies, and multiple active registrations fail closed with typed errors.

## Deterministic audit

Run:

```bash
poetry run python scripts/audit_objective_policy_registry.py
```

The command validates the existing policy loader and contract, then emits
stable JSON containing objective availability, policy identity, artifact
fingerprint, capability states, the objective-neutral database boundary, and a
canonical registry fingerprint. It contains no timestamp, absolute path,
financial observation, holding, credential, private LTIA value, network input,
or database write.

The registry fingerprint reuses the same canonical JSON/SHA-256 utility as the
existing prospective policy records. It covers the registry schema version,
sorted objective identities, and sorted immutable policy metadata. It is
independent of registration order, machine path, timestamps, random state, and
environment.

## Deferred work and cutover

Milestone 11 may use the registered capital policy to implement the separately
reviewed `CAPITAL_DEFENSIVE` shortlist constructor. Dividend data and policy
remain deferred to later milestones until reviewed evidence and explicit rules
exist. This milestone does not construct or rank shortlist portfolios and does
not generate recommendations. Production cutover remains **NO-GO /
NOT_AUTHORIZED**.
