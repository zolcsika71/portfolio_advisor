# Schema-v3 model-portfolio migration dry run

This is a pre-Milestone 7 validation harness, not a migration or cutover.
It opens `database/model_portfolio.sqlite` with SQLite `mode=ro`, reads the
retained workbooks without mutation, and writes only a caller-selected new
temporary database plus an ignored audit JSON artifact.

Run:

```bash
poetry run python scripts/audit_schema_v3_model_portfolio_migration_dry_run.py \
  --destination tmp/schema_v3_model_portfolio_dry_run.sqlite
```

The command rejects an existing destination, `database/portfolio_advisor.sqlite`,
and destinations under the retained `database/` directory. It has no cutover
entry point; `execute_model_portfolio_cutover` always raises
`CutoverNotAuthorized`.

## Mapping and compatibility

Each legacy holding is reconciled to exactly one visible `modell portfóliók`
workbook row by date, portfolio name, product, valid ISIN, and allocation.
Unmatched, duplicated, or ambiguous evidence fails closed. The target records
source file hash, source sheet, source-row number, raw displayed fields and
payload hash, source occurrence, ISIN instrument, source alias where its
source-type/name mapping is unambiguous, and five provider-reported metrics.

The schema-v3 adapter reads only
`portfolio_holding_source_occurrence` plus its provenance-tagged metric
observations and returns the existing `HoldingObservation` contract. Existing
metrics, advisor, and ranking functions are then reused unchanged. No
`portfolio_holding` analytical projection is written by the migration.

The three `IE00B7KFL990` pairs dated 2024-09-17 are stored as six distinct,
immutable source occurrences with their workbook row lineage and
`UNRESOLVED_DUPLICATE_SEMANTICS`. They are never deduplicated, aggregated, or
projected.

## Equivalence and blockers

For every legacy observation date, the harness compares candidate universe,
source-occurrence count, allocation totals, feature values, eligibility and
rejection reasons, normalization, contributions, score, rank order, and
winner through a deterministic serialization of the existing ranking results.
Values must compare exactly. Any float difference is emitted with path, both
values, absolute delta, and cause; no acceptance tolerance is applied.

The ignored artifact is
`data/audit/schema_v3_model_portfolio_migration_dry_run.json`. It contains no
LTIA evidence. A passing dry run does not resolve canonical metadata conflicts,
the `IE00B7KFL990` business semantics, production backup/cutover approval, or
the later Milestone 6 LTIA work. Therefore it is **not authorization for a
retained-data migration or production cutover**.
