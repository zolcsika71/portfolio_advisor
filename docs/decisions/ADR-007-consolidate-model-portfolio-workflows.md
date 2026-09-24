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

Phase 1 now implements the proposal's additive contracts and explicit adapters
for synthetic temporary databases. Every Phase 1 authority record is marked
non-operational, the command refuses retained project paths, and the current
legacy defaults and watcher remain unchanged. This implementation evidence
does not accept this ADR or authorize any later phase.

The 2026-09-24 corrective rehearsal extended that non-operational contract to
retain provider decimal strings independently from parsed Decimal values and
to expose connection-scoped validated admission/read sessions. The rehearsal
on isolated SQLite-API copies reconciled all 33 dates and three MNB records,
including exact `102.9096` text, while reducing the all-date public adapter from
an unfinished two-hour run to one 89.098-second validated session. The
transaction-safe admission completed in 105.266 seconds and exact replay in
201.283 seconds. This is validation evidence for the proposal, not acceptance
or cutover authorization.

Phase 3A now implements explicit opt-in reader injection and a fail-closed
shadow comparison boundary without changing any operational default. Model
snapshots and optional direct portfolio NAV are separate inputs. A bounded
shadow run uses one validated analytical session, records both source and
authority provenance, requires a result or concrete blocker for each reviewed
workflow, and accepts only exact path/value-specific expected differences.
The initial retained-corpus rehearsal passed seven workflows and correctly
blocked on an incomplete coverage grid. After the separately authorized
evidence refresh, coverage contained 1,224 identities, the feature dataset
contained 408 rows, and the label store contained 1,224 records. The follow-up
bounded shadow comparison passed all eight workflows and session-exit
validation. All labels remain explicitly unavailable under current evidence;
the result proves reader equivalence, not financial-data availability.

Phase 3B proposes—not accepts—the operational writer contract. One shared
manual/watcher entry point would retain and hash workbook bytes before parsing,
inventory every visible sheet, bind ordered before/after dataset fingerprints,
and admit one immutable receipt plus durable post-commit work in a single
validated SQLite transaction. Exact replay would resume pending filesystem or
artifact work without adding rows. Same-date changed bytes would fail closed,
and downstream artifacts would remain on the prior validated generation until
a complete replacement generation was ready. Database commit, workbook
movement, and artifact publication are explicitly separate failure domains.

The smallest recommended implementation slice is synthetic and temporary-only:
workbook envelopes, per-sheet dispositions, authority chaining, receipts,
outbox state, common coordination, and failure injection. It does not wire the
watcher, change defaults, admit retained workbooks, install a live schema, or
transfer authority. The dual-sheet disposition, any future same-date
supersession, generation-level artifact publication, compatibility lifetime,
and portable MNB package layout still require explicit approval. This planning
evidence does not accept this ADR or authorize migration, authority transfer,
cutover, or retirement.

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
- Exact source formatting is part of MNB provenance even when its parsed
  Decimal value is numerically equal. Reusable validation is scoped to one
  connection and database snapshot plus verified external dependencies; a
  changed dependency or database state must open and validate a new session.
- A Phase 1 admission session owns its outer transaction. It commits only
  after exhaustive exit validation; interruption, stale state, or failed exit
  validation rolls back the entire session so unvalidated admissions cannot
  become visible or durable.
- The implemented Phase 1 surface deliberately stops before workbook
  ingestion: it binds typed normalization to already staged synthetic source
  occurrences. A real single writer, baseline migration, and operational
  authority epoch remain later gated work.
- Phase 3A dependency injection is explicit and read-only. Compatibility
  constructors and all operational defaults remain legacy-backed; an
  analytical reader is never selected by schema discovery or fallback.
- Model projection equality is not evidence of direct-NAV or strict-coverage
  equality. The complete grid now demonstrates equivalent available and
  unavailable results, but Phase 3A does not synthesize NAV, eligibility, or
  labels and does not make unavailable outcomes available.
- The proposed writer retains evidence before parsing and commits one ordered
  receipt only after exhaustive validation. Later filesystem finalization and
  artifact refresh are resumable from durable pending work; their failure must
  not be described as rolling back an already committed admission.
- Software rollback does not transfer data authority back to a stale legacy
  file. Data restoration requires the latest verified analytical backup plus
  ordered replay of later retained admissions.
- The unresolved dual-sheet watcher policy, changed historical workbook
  contract, atomic artifact-generation publication, external consumers,
  compatibility lifetime, and MNB portable package layout must be decided
  before acceptance or cutover.
