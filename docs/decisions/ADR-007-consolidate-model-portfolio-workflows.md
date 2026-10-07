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

The explicitly authorized Phase 3B.1 slice now implements synthetic,
temporary-only workbook envelopes, atomic model-and-shortlist admission,
authority/predecessor chains, immutable receipts, and pending outbox records.
It retains generated envelope bytes content-addressably below a caller-supplied
temporary root, rejects path escapes and links, uses one outer SQLite
transaction, and validates exact source and metric bindings before commit.
Raw cell payloads remain distinct from normalized parser output; normalization
cannot rewrite the retained source representation.
Exact replay is mutation-free; changed same-date content is rejected; and a
new append fails closed when a Phase 1 authority, historical dataset manifest,
or dataset-bound shortlist correction is installed.
These choices are approved only for this bounded implementation and do not
select an operational watcher policy or authorize a live correction mapping.

Phase 3B.1 does not wire the watcher, change defaults, admit retained Excel
workbooks, install a live schema, execute outbox work, publish artifacts, or
transfer authority. Any future same-date supersession, generation-level
artifact publication, compatibility lifetime, operational dual-sheet release,
and portable MNB package layout still require explicit approval. This
implementation evidence does not accept this ADR or authorize migration,
authority transfer, cutover, or retirement.

The explicitly authorized Phase 3B.2 parser-only slice now implements a
versioned BIFF-XLS dual-sheet source envelope without database admission. It
binds the exact parsed bytes and filename date, requires the two actual visible
leading-space sheet names, assigns real-source roles that are deliberately
distinct from Phase 3B.1's synthetic `terméklista` role, and preserves header
order, row/column coordinates, BIFF cell types, raw values, formats, duplicate
occurrences, and source-order fingerprints. Numeric zero, formatted blank,
empty, text `"0"`, error, and other BIFF types remain distinct. Hungarian
labels and anomalous currency-risk or sustainability values are not translated
or repaired. Canonical serialization contains no local path or timestamp.

The parser records that `xlrd` provides only cached formula results when
available and does not expose formula text or a reliable formula-presence flag;
it never evaluates formulas. Its success status is
`NOT_EVALUATED_PARSER_ONLY`, not an admission decision. Read-only reconciliation
of the 33 retained workbooks preserved 5,283 model rows and 10,833 shortlist
occurrences, while synthetic fixtures cover typed-cell and fail-closed
structure behavior. No database, workbook, watcher, operational route,
correction layer, or artifact is mutated. Database admission, normalized and
legacy projections, correction disposition, evidence packaging, receipt-chain
integration, and operational coordination remain future authorization gates.
This implementation evidence leaves this ADR Proposed.

The proposed
[BIFF-XLS normalization and admission policy v1](../architecture/biff-xls-normalization-admission-policy-v1.md)
now governs an implemented pure `BIFF_XLS_NORMALIZATION_CANDIDATE_V1` adapter.
The adapter separates
typed raw cells, normalized originals, admitted originals, and effective
values; retains exact shortlist source references and dataset-bound correction
bindings; keeps model and shortlist zero rules scoped; and emits diagnostics for
recovered parsing, uncertain formula origin, currency-risk translations,
anomaly dispositions, and real-source model zero semantics. The adapter
predates the subsequent model-zero policy approval and still emits
`UNRESOLVED_MODEL_ZERO_SEMANTICS`. The separate pure projection described below
preserves that input diagnostic and records its scoped policy resolution.
It accepts only an in-memory parser envelope, caller-supplied expected workbook
hash, and optional exact approved mapping bytes. It has no database or
filesystem surface, emits `admission_approval = NOT_GRANTED`, and cannot
authorize a write, correction rebinding, watcher route, authority transfer, or
cutover. This bounded implementation does not accept this ADR.

The subsequent read-only
[recovery/formula verifier](../architecture/biff-xls-recovery-formula-evidence-v1.md)
independently inspects the CFBF allocation graph and BIFF record streams for a
committed inventory of all 33 retained hashes. It confirms six strict-open
files, the exact root
mini-stream/Workbook-chain overlap in 27 recovery-dependent files, complete
two-sheet record coverage, and zero worksheet formula-related records in the
retained bytes. Calamine and the published parser are explicit cross-checks,
not the source of the independent allocation or formula findings. The verifier
creates only a deterministic local evidence report, grants no recovery or
formula-origin approval, performs no admission, and leaves this ADR Proposed.
Its `NOT_GRANTED` fields remain the unchanged audit-time record. On 2026-09-26
the user subsequently and explicitly approved two separately scoped evidence
sub-decisions: the documented allocation-defect recovery exception for only
the 27 enumerated full hashes, subject to every acceptance and rejection
condition, and current-retained-file formula-origin sufficiency for all 33
enumerated full hashes. The latter makes no claim about calculations, formulas,
or pasted values before export. The pure
`BIFF_XLS_EVIDENCE_GATE_EVALUATION` v1 evaluator now enforces only these two
hash-bound decisions against the exact historical report and returns separate
verdicts while always emitting `admission_approval = NOT_GRANTED`. It performs
no current-workbook inspection or admission and does not approve the remaining
consolidation, operational-authority, migration, or cutover decisions. No separate ADR is
created because these bounded sub-decisions remain within this ADR's broader
scope, whose overall status remains Proposed.

On 2026-09-26 the user explicitly approved the separate
`MODEL_METRIC_ZERO_HANDLING_POLICY_V1` sub-decision. It preserves finite numeric
zero as original evidence for exactly `YTD`, `1yr`, `3yr`, `5yr`, `1Y Sharpe`,
`3Y Sharpe`, `5Y Sharpe`, `1Y Vol.`, `3Y Vol.`, `Down. risk`, `Info. ratio`,
and `Max. drawd.`, and defines legacy omission only through the separately
named, explicitly selected, provenance-bound
`MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1` projection. Omission applies
only to that metric observation and never drops its source occurrence or
holding. Allocation, shortlist fields, text `"0"`, missing cells, errors, dates,
booleans, and non-finite values are excluded.

The approval covers the 33 inventoried workbooks and future workbooks that
independently satisfy the same v1 typed-source contract. The earlier corpus
audit attributed 12,072 numeric-zero cells to the retained scope; this approval
record does not freshly measure them or claim they mean missing data. Future
bytes inherit no recovery, formula, correction, eligibility, or admission
authority. The pure `project_model_metrics` API now implements original and
compatibility selection with a required candidate fingerprint, immutable typed
provenance, and field omission reasons. Both modes preserve all input rows,
shortlist fields, original values, and diagnostics; only eligible model numeric
zero observations are omitted in compatibility mode. Results remain
`NOT_EVALUATED_PROJECTION_ONLY` with admission `NOT_GRANTED`. Consumer activation,
full candidate-native reader equivalence, and any operational default change
remain pending. This approved sub-decision does not accept ADR-007 or authorize admission,
migration, authority transfer, or cutover.

The subsequent [bounded retained-corpus comparison](../architecture/biff-xls-normalization-admission-policy-v1.md#bounded-retained-corpus-comparison-evidence)
at revision `c0e3d51cc3d46cfc3c0ee29746e51c87e3ca5b0c` covers 33
workbooks/dates and 408 date/portfolio identities, preserving 5,283 model rows
and 10,833 shortlist occurrences. All twelve model fields (63,396 metric cells)
match legacy after 12,072 scoped zero omissions. Actual production ranking
uses only five of those metrics; with legacy descriptive inputs held fixed,
compatibility matches availability, coverage, eligibility, scores, ordering,
warnings, winners and four observed ties. Original mode intentionally adds
49 eligible identities, changes 405 scores, all 33 dates' ordering and 11 winners.
The original report SHA-256 is
`4155b6d70e0bfae04202d680ab497ec7fd64372c0624f85342f370204b2a5493`;
the actual successful harness and evidence are preserved outside Git in
recovery package `model-metric-comparison-v1-preserved.YHDm4b`. Preservation
verifies existing evidence, not a new corpus run. Reproduction requires external
retained inputs; the original complete dependency freeze was not recorded, and
the unchanged analysis script assumes a fixed output layout. Preservation-time
versions are not a complete historical environment record. This bounded result does not
establish full candidate-native reader equivalence or resolve translations,
anomaly dispositions, metric interpretation, correction preservation or remaining
eligibility/admission gates; it grants no additional authority. ADR-007 remains
`Proposed` and admission remains `NOT_GRANTED`.

On 2026-10-07 the user explicitly approved all three
[model currency-risk policy sub-decisions](../architecture/biff-xls-normalization-admission-policy-v1.md#model-currency-risk-translation-and-anomaly-policy):
“Approve all three within their reviewed scopes.” These are independent policy
approvals only. The subsequent synthetic-only task authorizes the separate pure
projection described below; comparison execution, consumer activation, and
admission remain unauthorized. `MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1`
defines only declared Hungarian BIFF text keys mapped to `Hedged`, `Unhedged` and
`Partially Hedged`, preserving originals and the explicit lookup/projection
identity through text-only NFC/trim/casefold lookup. Shortlist, numeric-code
translation, synonyms and already-English input extensions are excluded.
Approved `MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1` concerns only
the enumerated 24 occurrences in two exact workbook hashes: six text `VALUE!`
values, nine numeric `2`, three numeric `3`, six numeric `4`. Their meanings
remain unknown. The separate pure implementation pins a fail-closed registry
for an explicitly selected `MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1`
to expose `None` with reason `LEGACY_ANOMALY_AS_NONE_WITHOUT_INTERPRETATION`;
no original, diagnostic, duplicate occurrence or holding is dropped. The
artifact must bind exact hashes, coordinates, types, raw values, formats,
occurrence/candidate fingerprints and policy identities. Changed bytes,
missing/extra entries, unsupported types or mismatched provenance fail closed.
Blank/empty states remain distinct, unknown labels and unlisted typed anomalies
have no fallback, and already-English inputs require a separate scope choice.

The 2026-10-07 bounded inspection now supplies typed occurrence evidence for
all 24 coordinates in those two hashes, reconciling three/21 cells by workbook
with the older textual index without discrepancies. Parser-exposed types are
six text `VALUE!` cells and eighteen numeric floats `2.0`/`3.0`/`4.0` (9/3/6);
the older strings do not establish those types. Recovery package
`model-currency-risk-typed-v1.FyqXIB` preserves the actual harness and manifest
`inspection/typed-occurrence-manifest-v1.json` (SHA-256
`c4d678e0c90abac49337bf1f7547816b45134b6e6e7886ab4aacf1ee594f123a`).
Exact provenance and parser recovery/formula limitations are documented in the
linked policy. This is not an independent raw-BIFF reinspection or newly
evaluated candidate binding. The subsequent pure projection pins the manifest
and its historical candidate references without rerunning retained inputs.
The manifest's `NOT_GRANTED` fields remain its unchanged audit-time record;
the later approvals are recorded separately in documentation. Policy approval
does not install an executable artifact, prove consumer equivalence or grant
admission authority.

The translation approval covers inventoried and independently v1-conforming
future model sheets; the anomaly dispositions never extend beyond their exact
hash/coordinate/type/value bindings. Future files inherit no evidence exception,
correction, eligibility or admission authority. The third approved sub-decision
is the definition of a faithful historical comparison profile, bound to the
approved mapping/disposition versions: current ranking counts
only English `Unhedged` and reports full indicator coverage even for missing
risk labels when total allocation is positive. Keeping that behavior for a
comparison is not endorsement of complete FX-risk measurement or new ranking
semantics. Full candidate-native reader equivalence is still unperformed and
depends on other descriptive contracts too. Missing and partially hedged labels
do not prove hedging or complete currency-risk information. None of these
approvals alone authorizes
implementation, comparison execution, consumer activation or admission, changes the
approved classification/model-zero decisions, or decides shortlist translation,
sustainability, metric interpretation, eligibility composition or cutover.
This ADR remains `Proposed`; admission remains `NOT_GRANTED`.

The separately authorized pure
[`model_currency_risk_projection.py`](../../src/portfolio_advisor/workbook_source/model_currency_risk_projection.py)
implementation requires explicit profile selection and a candidate fingerprint.
It reuses substantive immutable v1 validation, preserves both sheets and all
diagnostics/occurrences, and binds the approved mapping and exact anomaly
registry without a public policy substitution path. Scoped policy resolutions
are separate from historical diagnostics. Results remain
`NOT_EVALUATED_PROJECTION_ONLY` / `NOT_GRANTED`. Complete, source-ordered registry
coverage is checked; duplicate/unused entries fail closed. Synthetic tests cover
public mapping/missing requests, public anomaly success/rejection with a
test-local restored registry, and fixed anomaly proof matching. No production
override is exposed; these tests do not prove complete retained-candidate
acceptance or reader equivalence. No consumer, database, ranking,
eligibility or operational default is changed. Future lexical inputs inherit
no evidence/admission authority; future anomalies inherit no exception.

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
- The implemented Phase 3B.1 contract proves only the synthetic transaction
  and provenance boundary. Its JSON envelope is not the retained Excel parser,
  its pending outbox has no worker, and its SQLite serialization is not the
  operational manual/watcher lock.
- The implemented Phase 3B.2 parser proves typed extraction and deterministic
  provenance only. It deliberately has no admission adapter and establishes no
  compatibility between the real shortlist sheet and Phase 3B.1's synthetic
  `terméklista` role.
- The implemented normalization candidate is a separate, non-writing boundary.
  It preserves exact source provenance and unresolved gates, produces no
  eligibility approval, and is not consumed by any current writer.
- The unresolved operational dual-sheet watcher policy, changed historical
  workbook contract, atomic artifact-generation publication, external
  consumers, compatibility lifetime, and MNB portable package layout must be
  decided before acceptance or cutover.
