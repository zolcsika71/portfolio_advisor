# Milestone 11A — Capital Defensive construction-policy contract

Milestone 11A approves the immutable `CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY` v1.0.0
for the `CAPITAL_CONSERVATION` objective and `CAPITAL_DEFENSIVE` strategy. The
artifact schema is version 1, its status is `APPROVED`, and runtime construction
readiness is `NOT_IMPLEMENTED`. The reviewed artifact is
`data/knowledge/validated_rules/capital_defensive_construction.yaml`; its loader
rejects unknown fields, duplicate keys, invalid types or ranges, binary floating-point
cash amounts, altered official-source identities, and conflicting registrations. Its
canonical JSON has a deterministic SHA-256 fingerprint.

## Approved allocation and cash contract

Each future run receives cash amounts by currency and must contain exactly one finite,
positive `Decimal` amount in EUR, USD, or HUF. Only instruments denominated in that same
currency may be selected. FX conversion, currency substitution, and implicit conversion
are prohibited. Fewer than eight qualifying same-currency instruments yields `UNAVAILABLE`.

The governed allocation is exactly:

| Component | Count | Weight each | Total weight |
| --- | ---: | ---: | ---: |
| Securities | 8 | 10% | 80% |
| Cash reserve | 1 | 20% | 20% |

These are portfolio weights. The existing ranking-policy feature weights are not portfolio
weights. Transaction units, order quantities, and brokerage rounding remain outside scope.

At least three conflict-free asset/sub-asset groups are required, and one group may carry
at most 40%, or four holdings. Missing, conflicting, or non-deterministically mapped category
evidence is `REJECTED`. Issuer concentration is
`NOT_ENFORCED_EVIDENCE_UNAVAILABLE`; issuer identity must not be manufactured.

## Future deterministic selection

One candidate is permitted per run and currency. A future generator must preserve reviewed
instrument eligibility and rank order, select a feasible eight-instrument set, minimize the
ordered rank vector, and break an exact feasible-set tie with the lexicographically ordered
ISIN tuple. Randomized search and opaque exhaustive output are prohibited. This milestone
defines that behavior and does not implement it.

## NAV and portfolio-risk requirements

Every selected instrument must eventually have at least 365 calendar days of admitted and
validated history, at least 252 aligned return intervals, and an observation no more than
30 calendar days stale at construction. All eight instruments need one valid common aligned
return window. Interpolation, nearest-date substitution, and proxy instruments are prohibited;
failure is `UNAVAILABLE`.

The only approved future calculation path is aligned constituent NAV series, constituent
returns, weighted portfolio return series, portfolio volatility, maximum drawdown, and
supported risk-adjusted metrics. Calculating portfolio volatility as the weighted sum of
individual volatilities is explicitly prohibited.

## Official reference-rate policy

| Currency | Benchmark | Administrator | Official source |
| --- | --- | --- | --- |
| EUR | €STR | European Central Bank | <https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/euro_short-term_rate/html/index.en.html> |
| USD | SOFR | Federal Reserve Bank of New York | <https://www.newyorkfed.org/markets/reference-rates/sofr> |
| HUF | HUFONIA | Magyar Nemzeti Bank | <https://statisztika.mnb.hu/statistical-topics/monetary-policy-statistics> |

Future evidence must preserve administrator, series identity, observation dates, exact
provider publication metadata when supplied, a conservative auditable availability boundary,
source URL, value, units, and quality. Provider revision IDs, dataset versions, and publication
values must never be synthesized when absent. Evidence must respect official day-count and compounding
conventions and align reference returns to portfolio-return dates deterministically. Unknown
observations cannot be zero-filled, and policy or base rates cannot substitute for the approved
benchmark. Sharpe uses the aligned currency benchmark as its reference return. Sortino uses the
same governed aligned benchmark as its minimum acceptable return unless a later reviewed policy
changes it. Runtime Sharpe and Sortino remain `UNAVAILABLE` until official series and methodology
metadata are ingested and validated.

## Corrected capability boundary

Capital eligibility, instrument screening/ranking, and the construction policy are
`AVAILABLE_REVIEWED`. Constructed-portfolio runtime, finalist comparison, and outcome success
criteria are `NOT_IMPLEMENTED`. Dividend has `NO_VALIDATED_ACTIVE_POLICY` for every capability.

The Milestone 11 singleton ranking remains import-compatible, deprecated intermediate instrument
screening. The Milestone 12 model-versus-instrument workflow remains import-compatible,
deprecated exploratory comparison. Neither is roadmap-complete portfolio construction or
portfolio-versus-portfolio comparison. Their historical commits remain valid forward history.

This document records the 11A boundary at approval time. Milestone 11B subsequently implements
the schema, deterministic allocation engine, lineage, and persistence foundation without changing
this artifact or its fingerprint. Production remains blocked by incomplete and stale admitted NAV,
missing SOFR/HUFONIA and governed benchmark alignment, and unavailable portfolio metrics.
Milestone 11C Phase B admits official EUR €STR evidence without changing the construction policy
or activating Sharpe/Sortino. Phase C0 later replaces the provider-specific revision-1
provenance assumptions with the provider-neutral v2 availability contract while preserving the
policy artifact and its fingerprint. SOFR remains unadmitted and HUFONIA remains pending.
Milestone 11A itself
performed no allocation, candidate generation, schema migration, NAV/reference-rate ingestion,
persistence, or portfolio metric calculation.
Production remains `NOT_AUTHORIZED`.

## Audit

```bash
poetry run python scripts/audit_capital_defensive_construction_policy.py
```

The audit is deterministic, read-only, and explicitly states that no constructed portfolio can
yet be produced.
