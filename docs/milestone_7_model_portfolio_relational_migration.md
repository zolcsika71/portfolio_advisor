# Milestone 7 — Model portfolio relational migration

Milestone 7 builds `database/portfolio_advisor.sqlite` only as a parallel
analytical store. `database/model_portfolio.sqlite` remains authoritative and
the default application repository. Cutover, deletion, and decommissioning are
**NOT_AUTHORIZED**.

## Builder and safety gates

The builder defaults to a disposable dry run:

```bash
poetry run python scripts/build_schema_v3_model_portfolio_parallel.py
```

Only an explicit apply may create an absent target:

```bash
poetry run python scripts/build_schema_v3_model_portfolio_parallel.py --apply
poetry run python scripts/validate_schema_v3_model_portfolio_parallel.py
```

It compares retained database and workbook SHA-256 values with the ignored,
validated Milestone 7 predecessor dry-run artifact. Changed evidence requires a
new audit. An existing target is never overwritten: it is instead read-only
validated. The builder creates a private sibling temporary database, imports
and validates it, then uses an atomic rename only after all gates pass.

Failure rolls back the database transaction, removes only its private temporary
file, leaves any existing target unchanged, and never changes a legacy source
or workbook.

## Mapping and compatibility

Every legacy holding reconciles one-to-one with a visible model-workbook row.
The parallel store records source file SHA, sheet, row, original payload,
instrument ISIN, aliases, portfolio/snapshot identity, raw source occurrences,
and provider-reported metric observations. It imports neither shortlist, NAV,
cash without source evidence, nor private LTIA evidence.

The compatibility repository reads raw
`portfolio_holding_source_occurrence` rows and their metric references through
the existing `HoldingObservation` contract. It therefore preserves source-row
multiplicity and uses the existing metrics and ranking implementation instead
of duplicating financial logic.

The six `IE00B7KFL990` 2024-09-17 rows are immutable source occurrences marked
`UNRESOLVED_DUPLICATE_SEMANTICS`. No `portfolio_holding` analytical projection
is created for them (or for any migration row).

## Manifest and validation

`migration_build_manifest` stores schema/build versions, source and policy
fingerprints, counts, unresolved-semantic count, exact-equivalence status and a
stable dataset fingerprint. The read-only validator checks the manifest,
source/policy freshness, SQLite integrity and foreign keys, then reruns exact
all-date equivalence. It reports any field mismatch directly and has no
tolerance-based acceptance.

The ignored local artifact is
`data/audit/milestone_7_model_portfolio_relational_migration.json`. It records
counts, fingerprints, duplicate preservation, per-date equivalence and the
fixed cutover status.
