# Historical Milestone 12 infrastructure — exploratory model-versus-instrument comparison

Forward correction: this historical workflow compares a model portfolio with one ranked
instrument. It remains import-compatible exploratory infrastructure; it is not roadmap-complete
portfolio-versus-portfolio finalist comparison and does not complete Milestone 12.

The historical implementation provided this exploratory workflow:

```text
best reviewed model portfolio
versus
rank-one eligible shortlist instrument
        ↓
governed comparison and system recommendation
        ↓
explicit user choice
```

The shortlist finalist is one instrument from the Milestone 11 ranked universe. It is not
described as an allocated or diversified portfolio.

## Public contract

`build_capital_conservation_reference_workflow(...)` resolves the active
`capital_conservation` policy, selects the latest common complete model/shortlist date (or the
latest common date not after an explicit `as_of`), runs the existing model ranking and Milestone
11 constructor, and compares their sole rank-one eligible finalists. The result is immutable and
canonical. It includes policy, registry, snapshot, source, occurrence, membership, lineage,
recommendation, and workflow fingerprints.

The initial state is always `AWAITING_USER_CHOICE`; no system recommendation is converted into a
choice. `record_capital_conservation_user_choice(...)` requires explicit workflow and
recommendation fingerprints and accepts only the model finalist, shortlist finalist, `DEFER`, or
`DECLINE`. A user may disagree with the recommendation. The returned choice record is immutable
and non-persistent.

## Comparison policy

`CAPITAL_CONSERVATION_FINALIST_COMPARISON_POLICY` v1.0.0 is an approved, deterministic,
unweighted strict-Pareto policy. It compares the five raw one-year/snapshot features already
governed by the capital policy:

- annualized volatility (lower is better);
- maximum drawdown (higher/less negative is better);
- one-year return (higher is better);
- one-year Sharpe ratio (higher is better);
- unhedged allocation (lower is better).

The dimensions are `PARTIALLY_COMPARABLE`: definitions and units are shared, while a model
portfolio is an allocation-weighted aggregate and a shortlist finalist is a single instrument.
The separately normalized model and shortlist total scores are explicitly unavailable for
cross-universe comparison. There are no comparison weights. Strict dominance recommends the
dominating finalist; mixed signals or exact ties return `NO_CLEAR_RECOMMENDATION`; missing any
required dimension returns `INSUFFICIENT_COMPARABLE_EVIDENCE`.

## Capability and safety boundaries

Capital eligibility, instrument screening/ranking, and the Milestone 11A construction policy are
`AVAILABLE_REVIEWED`. Constructed-portfolio runtime, roadmap finalist comparison, and
outcome-success criteria are `NOT_IMPLEMENTED`. Dividend remains
`NO_VALIDATED_ACTIVE_POLICY` with no fallback.

The workflow is read-only. It performs no allocation, optimization, cash deployment, FX
conversion, persistence, brokerage action, suitability determination, outcome tracking, or
Graphify/LTIA operation. Schema v3 remains objective-neutral. Production cutover remains
`NOT_AUTHORIZED`. Real construction remains blocked pending schema, current NAV, and official
reference-rate ingestion; historical commits remain valid forward history.

Fail-closed conditions include unknown/dividend objectives, missing or ambiguous active policy,
capability or fingerprint mismatch, stale source manifests, incompatible/corrupted SQLite,
integrity or foreign-key failure, no common date, mismatched snapshots, missing lineage, empty or
ambiguous rank one, insufficient comparable evidence, and stale/invalid user-choice references.

## Audit command

```bash
poetry run python scripts/audit_capital_conservation_reference_workflow.py
poetry run python scripts/audit_capital_conservation_reference_workflow.py --as-of 2026-08-26
```

The command emits stable, privacy-safe JSON, performs no writes, and deliberately ends with
`AWAITING_USER_CHOICE`.
