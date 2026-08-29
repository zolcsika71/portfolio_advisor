# Milestone 5 — Schema-v3 Relational Foundation Scaffolding

**Status:** implemented and tested only against temporary SQLite databases.

This milestone provides the schema and guarded migration infrastructure; it
does not authorize a database migration, production cutover, workbook import,
or a change to the capital-preservation policy.

## Implemented foundation

`portfolio_advisor.database.schema.v3` initializes schema version 3
idempotently and enables `PRAGMA foreign_keys = ON` for every connection it
opens. It creates the following central analytical-database tables:

- Schema/provenance: `schema_version`, `source_file`, `source_sheet`.
- Canonical identity: `instrument`, `instrument_alias`.
- Portfolio evidence and projections: `portfolio`, `portfolio_snapshot`,
  `portfolio_holding_source_occurrence`, `portfolio_holding`,
  `portfolio_holding_lineage`, `portfolio_cash`.
- Metrics: `metric_definition`, `instrument_metric_observation`,
  `portfolio_metric_observation`.
- Shortlist foundation: `shortlist_snapshot`, `shortlist_entry`.

The implementation does not choose a path for a retained database and never
opens a repository database or workbook. Tests create only `tmp_path` SQLite
files.

## Identity, provenance, and cash constraints

`instrument.isin` is unique, non-empty, structurally checked, and validated
with the ISIN check digit by the insertion API. The API rejects cash; aliases
retain their source, normalized source name, explicit mapping status, optional
canonical instrument, validity period, and resolution evidence. A candidate,
ambiguous, or unresolved alias may have no canonical instrument. Similarity is
not an identity-resolution mechanism and cannot create an instrument.

Each source file records filename, a unique SHA-256, type, and source date.
Each source sheet belongs to one source file. Each raw holding occurrence
records its sheet, unique source-row number within that sheet, source payload
SHA-256, reported weight, and source-semantics status. This preserves source
rows even when they name the same instrument in the same snapshot. SQLite
triggers reject updates and deletes to raw source occurrences after insertion.

Cash is stored only in `portfolio_cash` as an amount and/or weight with a
three-letter currency and role. It has no instrument foreign key and does not
receive an ISIN.

## Source occurrences and analytical projections

`portfolio_holding_source_occurrence` intentionally has no unique
`(portfolio_snapshot_id, instrument_id)` constraint. Thus all six
`IE00B7KFL990` source rows remain independently representable, including the
three unresolved pairs from 2024-09-17.

`portfolio_holding` is an optional analytical projection and does have that
unique pair. The `create_analytical_holding_projection` API accepts only an
approved direct occurrence or approved aggregation, requires both calculation
version and approval reference, verifies that its lineage is the complete
source-occurrence set for the snapshot/instrument, and rejects changed total
weight. It also rejects occurrences marked `UNRESOLVED_DUPLICATE_SEMANTICS` or
`CONFLICTING_DUPLICATE_ROWS`. Consequently it cannot silently drop, merge, or
renormalize evidence. The current duplicate semantics are unresolved, so no
automated projection for those pairs is authorized.

Metric observations retain one of `PROVIDER_REPORTED`, `CALCULATED`,
`DERIVED`, or `OBSERVED`, as well as a source/calc reference.

## Migration safety

The migration package contains:

- explicit `BEGIN IMMEDIATE` transaction handling with rollback (and safe
  nested savepoints);
- schema-version detection and validation by `integrity_check` plus
  `foreign_key_check`;
- a non-replacing byte-SHA-256-verified backup helper;
- a read-only v2 dry-run that creates no destination; and
- an unconditional `CutoverNotAuthorized` guard for any v2-to-v3 execution.

There is no code path in this milestone that migrates retained data. A future
authorized migration must take a verified backup, run inside an explicit
transaction, preserve every source row and source hash, reconcile counts and
allocations, validate integrity/foreign keys, and retain a tested rollback
path before cutover.

## Unresolved cutover blockers

- The business semantics of every `IE00B7KFL990` duplicate pair remain
  `UNRESOLVED_DUPLICATE_SEMANTICS`; human approval is required before any
  derived analytical aggregation.
- The Milestone 4 source/identity audit blockers remain the authority for
  historical imports and mapping resolution.
- Legacy-to-relational ranking equivalence has not been executed against a
  migrated retained dataset; no migration or cutover is authorized.

**Milestone 5 scaffolding is complete. Legacy model-portfolio migration
development remains NO-GO until those evidence and cutover approvals are
explicitly granted.**
