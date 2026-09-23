# ADR-007: Consolidate model-portfolio workflows into the analytical store

## Status

Proposed

## Context

`database/model_portfolio.sqlite` is still the operational model-portfolio
reader and writer. It is selected by the default advisor, the workbook
importer, the installed XLS watcher, ranking/backtest/feature scripts, LTIA
comparison, and the MNB OTC evidence tools. Its flat model table contains
5,283 rows across 33 dates, and its separate MNB table contains three records
that are absent from `database/portfolio_advisor.sqlite`.

The analytical store already contains an exact ranking-compatible projection
of the 5,283 model rows plus shortlist, historical NAV, reference-rate,
constructed-portfolio, and correction evidence. Its historical migration
manifest binds the legacy database path and SHA-256 and all 33 processed
workbooks. Current validators deliberately fail if those bindings change.
Eight fields parsed by the legacy importer remain available only through raw
model source payloads rather than typed analytical fields.

The shortlist correction layers are separately dataset-bound and immutable:
1,278 original classification corrections, 287 composed corrections, 10,833
English pair mappings, and 23,979 zero-to-NULL metric corrections. A model
workflow consolidation cannot silently rebuild, discard, or extend them.
Historical constructed artifacts must retain their recorded classification
stages.

Alternatives considered are:

- keep both databases as permanent operational authorities;
- point the existing flat-table importer at the analytical database;
- rebuild the analytical database from the legacy database after every import;
- switch readers immediately because current ranking results are exact; or
- introduce a versioned single-writer and source-authority contract, validate
  it in parallel, then seek separate cutover approval.

The first option preserves duplicated authority and drift risk. The second is
schema-incompatible. The third can overwrite unrelated analytical evidence and
dataset-bound corrections. The fourth lacks import, MNB, rollback, and
historical-provenance coverage.

## Decision

Propose the final alternative, subject to later acceptance and explicit
cutover authorization:

- `portfolio_advisor.sqlite` becomes the sole operational model projection
  only after the phased gates in the
  [consolidation plan](../architecture/model-portfolio-consolidation-plan.md)
  pass.
- One versioned admission command serves manual and watcher imports. It stores
  immutable workbook evidence, all parsed fields, stable source identities,
  ordered import receipts, and before/after dataset fingerprints in one SQLite
  transaction.
- A model repository protocol separates consumers from storage. An explicit
  analytical adapter replaces each legacy reader only after all-date and
  workflow equivalence is proven; schema auto-detection is not used.
- The three MNB OTC observations move under a dedicated evidence contract.
  They remain weekly OTC aggregates and never become NAV or approved returns.
- The existing Milestone 7 manifest remains an immutable historical receipt.
  A new source-authority epoch and import-batch contract replaces live legacy
  freshness checks for operational reads without fabricating or deleting the
  original path/hash binding.
- Model imports do not alter shortlist tables or corrections. A changed
  shortlist dataset fails closed unless separately authorized; no correction
  is inherited by row ID or automatically extended.
- The legacy database is frozen at cutover and retained until every retirement
  criterion is met. Post-cutover rollback replays retained admissions or uses
  a validated temporary compatibility export; it never resumes from a stale
  legacy file.

This ADR is Proposed. It records a reviewable design, not approval to migrate,
switch defaults, freeze the writer, or retire a database.

## Consequences

- Model source authority becomes explicit and portable instead of being
  inferred from a mutable local path, while the historical migration remains
  reproducible.
- All parsed workbook data and unique MNB evidence must be normalized or
  durably retained before cutover; ranking equality alone is insufficient.
- The watcher, manual importer, readers, scripts, tests, documentation, and
  installed operational configuration require a coordinated release.
- Import failures are atomic at the database transaction boundary. Workbook
  movement and post-import workflows remain resumable operations rather than
  being misrepresented as part of that transaction.
- Dataset-bound shortlist corrections and historical constructed artifacts
  retain their existing provenance and semantics.
- Temporary parallel readers, recovery packages, and rollback rehearsals add
  implementation cost but make the authority change testable and recoverable.
- The unresolved dual-sheet watcher policy, changed historical workbook
  contract, external consumers, compatibility lifetime, and MNB portable
  package layout must be decided before acceptance or cutover.
