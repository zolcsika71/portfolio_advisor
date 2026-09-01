# Historical Milestone 11 infrastructure — capital-conservation instrument screening

Forward correction: this commit remains valid history, but the implementation below ranks
singleton instruments. It is deprecated intermediate instrument screening, not the
roadmap-complete Milestone 11 portfolio constructor. The reviewed construction policy is defined
by Milestone 11A. Milestone 11B supplies the additive schema, allocation engine, lineage,
and persistence foundation. Milestone 11C Phase B admits official EUR €STR evidence, but
production construction remains blocked by incomplete/stale NAV, absent USD/HUF benchmark
evidence, unimplemented alignment, and unavailable portfolio metrics.

## Contract

The historical Milestone 11 implementation added a deterministic, read-only adapter for the
`capital_conservation` objective and its `CAPITAL_DEFENSIVE` strategy. The
constructor ranks the complete eligible ISIN universe from one governed
schema-v3 shortlist snapshot. It is an instrument shortlist, not a portfolio,
allocation, recommendation, or trade instruction.

The constructor resolves the sole approved active policy through the objective
registry. It then verifies `CAPITAL_PRESERVATION_RANKING_POLICY` version 1.0.1
and the retained artifact SHA-256 before loading the existing ranking rules.
Eligibility, normalization, scoring, and tie-breaking are delegated unchanged
to the authoritative metrics and ranking packages.

Each security is evaluated as one complete unit candidate solely because the
reviewed eligibility contract requires a 100-percent candidate total. This is
an internal adapter into the existing calculation API, not a proposed holding
weight: no weight appears in the constructed result and no portfolio row is
created.

## Source and temporal behavior

The source is the ignored parallel schema-v3 database. All connections use
SQLite read-only and query-only modes. Construction requires a complete
Milestone 9 manifest, exact retained-workbook fingerprints, the expected
shortlist dataset fingerprint, schema version 3, `integrity_check = ok`, and no
foreign-key violations.

Without `as_of`, the unique latest complete snapshot is selected. With
`as_of`, the latest snapshot on or before that date is selected; future
evidence is never admitted. Each membership must have one unambiguous source
occurrence and complete lineage. An unresolved metadata-conflict occurrence
fails closed rather than being interpreted.

There is no implicit top-N. The default result contains the complete eligible
ranking. A caller may supply a strictly positive explicit limit that does not
exceed the eligible count.

## Governed result

The immutable result contains objective, strategy, policy, registry, manifest,
snapshot, source-file, membership, occurrence, and source-row identities. Each
candidate retains eligibility, rejection reasons, reviewed feature values,
weighted contributions, score, rank, and lineage. Canonical JSON and SHA-256
use the shared repository serializer and contain no timestamp or machine path.

Allocation, cash deployment, and FX conversion are explicitly
`NOT_PERFORMED`. No schema row is written and no recommendation is persisted.

## Capability boundary

Capital conservation has reviewed eligibility, instrument screening/ranking, and a separate
Milestone 11A construction policy. The production constructed-portfolio runtime is
`IMPLEMENTED_BLOCKED_BY_DATA`; finalist comparison and outcome-success criteria remain
`NOT_IMPLEMENTED`.
The dividend objective remains supported but has
`NO_VALIDATED_ACTIVE_POLICY`; there is no fallback to the capital policy.

Deferred work includes portfolio weights, cash constraints and deployment,
FX or hedging decisions, portfolio optimization, finalist comparison,
recommendations, outcome success criteria, API/UI integration, and all dividend
behavior. Production cutover remains `NOT_AUTHORIZED`. No historical commit is rewritten.

## Audit command

```bash
poetry run python scripts/audit_capital_conservation_shortlist.py
poetry run python scripts/audit_capital_conservation_shortlist.py --as-of 2026-08-26
```

The command audits retained workbook evidence, validates the read-only target,
and emits deterministic privacy-safe JSON. It exits nonzero on every governed
failure.
