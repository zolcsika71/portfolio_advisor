# Model-portfolio workflow consolidation plan

Status: **Proposed**. This document does not authorize a migration, default
change, production cutover, or database retirement. The governing proposal is
[ADR-007](../decisions/ADR-007-consolidate-model-portfolio-workflows.md), and
the proposed operational flow is shown in the
[cutover diagram](../diagrams/model-portfolio-consolidation-cutover.puml).

Phase 1 is now **implemented as non-operational scaffolding**. The additive
schema, immutable rehearsal contracts, synthetic-only command, and explicit
read adapters do not change the Proposed status of the consolidation or make
the analytical database an operational authority.

Phase 3A now adds **explicit, opt-in read injection and shadow comparison**.
It leaves every application, importer, watcher, and command default on the
legacy store. Its comparison report is finalized only after one shared
analytical read session completes exit validation. Phase 3A is evidence for
the proposal; it is not authority transfer or cutover approval.

The maintained evidence refresh completed the previously blocked forward-label
comparison. Phase 3A now passes all eight bounded workflows, while all 1,224
labels remain explicitly unavailable under the current financial evidence.
Reader equivalence therefore does not establish outcome availability, release
readiness, or authority transfer.

## Scope and verified baseline

The scope is only the model-portfolio and MNB OTC workflows currently backed
by `database/model_portfolio.sqlite` and their proposed consolidation into
`database/portfolio_advisor.sqlite`. LTIA, official historical NAV,
supplementary evidence, and prospective-validation stores remain separate.

The following evidence was rechecked read-only at Git revision
`979d4bc6e60744477210197dfc96a6b50714a966` on 2026-09-23:

| Evidence | Verified state |
| --- | --- |
| Legacy store | SHA-256 `5bc361b957af6365106f1685f23ec97577b3f230c69a60ac8dccc4e55d6249c7`; `PRAGMA user_version = 0`; tables `model_portfolios` and `mnb_otc_observations` |
| Analytical store | SHA-256 `9a0f0f8daf76de892f78ba7ba8637fdb5f21ef1b9810d8af7af8a1f003b5c4a6`; `PRAGMA user_version = 3`; integrity and foreign-key checks passed |
| Model history | 5,283 legacy rows and analytical source occurrences, 408 analytical portfolio snapshots, 33 dates from 2024-07-02 through 2026-08-26, 13 portfolios, and 66 ISINs |
| Ranking compatibility | `validate_parallel_database` reported `PARALLEL_VALIDATED`, exact results on all 33 dates, maximum numeric delta `0.0`, and six retained unresolved source occurrences |
| Workbook evidence | The installed manifest names and hashes 33 processed workbooks and the legacy database; every binding still validated. The analytical evidence catalog records both `modell portfóliók` and `shortlist` sheets for all 33 workbooks. |
| Shortlist corrections | Dataset fingerprint `32216038d2f69dbf4c6436e91782025ae117dc59fe466e8a31ebc3719f93e8b2`; 1,278 original classification items, 287 composed items, 10,833 English pair-mapping items, and 23,979 metric corrections |
| MNB OTC evidence | Three weekly observations for one exact ISIN; their three retained PDFs exist and match the hashes stored in the legacy rows; the analytical store has no MNB OTC table |
| Concurrency context | No SQLite WAL, SHM, or journal sidecars were present. PyCharm held idle handles to both databases, and the installed XLS WatchPaths LaunchAgent was loaded, watching, and not running at inspection time. |

Different hashes would not by themselves establish a data difference. The
model comparison above used row-level and behavioral checks. Both database
files remain local evidence and were not changed by this review.

The Phase 3B planning review rechecked the operational boundary at Git revision
`f3e6bcb914b25c1dbe21d0fa5f1104f1c769d849` on 2026-09-24. The legacy and
analytical database SHA-256 values remained those shown above; neither database
had a WAL, SHM, or journal sidecar or an open handle at inspection time. The
live analytical store still has no Phase 1 feature marker or contract tables.
The installed WatchPaths LaunchAgent configuration had SHA-256
`43b5e9165942e36eb2113c8c2f3761c11a48a32768b943beae9650b1fb17b4fa` and
was loaded but not running. The refreshed maintained artifacts contained 1,224
coverage identities, 408 feature rows, and 1,224 materialized labels. Their
presence does not install an operational writer.

## Current authority and dependency matrix

`model_portfolio.sqlite` remains the operational authority today.
`portfolio_advisor.sqlite` is a parallel analytical store; the existing
cutover constant is `NOT_AUTHORIZED`. The proposed replacement column below is
a design target, not implemented behavior.

The analytical compatibility repository reads
`portfolio_holding_source_occurrence`, not `portfolio_holding`: the latter has
zero model rows because the six unresolved duplicate occurrences prevent a
canonical projection. Any replacement reader must preserve that deliberate
source-occurrence grain.

| Workflow or consumer | Current path and fields | Role today | Available replacement and remaining gate |
| --- | --- | --- | --- |
| Default advisor (`src/portfolio_advisor/main.py`, `CapitalPreservationAdvisor`) | `database/model_portfolio.sqlite`; latest/date-specific holdings and five ranking fields | Read | Explicit reader injection and the analytical adapter are implemented and the all-date shadow comparison passes. Do not auto-detect schemas. The default remains legacy until writer, authority, release, and rollback gates pass. |
| Manual workbook import (`DB_creation.database_create`) | Writes the flat `model_portfolios` table; parses all 21 worksheet fields, converts numeric zero to SQL NULL, skips an existing date, then moves the workbook | Write | Replace with one schema-v3 model admission command. It must retain workbook bytes/hash, raw payload, every parsed field, stable source identity, and an immutable import receipt. Pointing the flat importer at the analytical store is forbidden. |
| Watcher (`operations.xls_import_watch`) | Invokes `portfolio_advisor.main --import`; reads the hard-coded legacy path before and after import; then runs current-universe validation, prospective recording, and prospective audit | Orchestration/read/write | Use the same single admission command and shared configured analytical path. Preserve the lock, stable-file check, pending marker, and post-import sequence. Update and explicitly revalidate the installed LaunchAgent before cutover. |
| Core repository and advisor validation (`database.repository`, `advisor.*`) | Compatibility construction still selects `ModelPortfolioRepository`; model identity, allocation, currency/risk, asset class, and five ranking metrics | Read | `ModelPortfolioReader` and the opt-in `AnalyticalModelPortfolioRepository` are implemented. Retain the legacy implementation for operational defaults, comparison, and recovery validation until cutover. |
| Ranking and methodology commands (`validate_active_*`, `validate_capital_preservation_methodology.py`, `validate_forward_rank_signal.py`) | Default legacy database, same model snapshot contract plus governed policy/audit inputs | Read/audit | Change defaults only after all-date old/new equivalence and isolated tests pass. Audit manifests must name the repository contract and current authority epoch, not merely a filename. |
| Backtest coverage and strict validation (`audit_backtest_*`, `validate_strict_backtest_pipeline.py`, `HistoricalPortfolioRepository`) | Defaults use legacy holdings; optional flat `portfolio_nav_history` probe | Read/audit | Explicit model-reader and optional NAV-reader separation is implemented, and bounded strict-coverage shadow comparison passes. Defaults remain legacy; both current stores lack the optional NAV table, and no NAV may be synthesized during the change. |
| Features, labels, and research (`features.dataset`, `features.official_forward_labels`, `history.nav_acquisition`, `history.official_portfolio_performance_research`, portfolio-NAV blocker/methodology modules) | Defaults use legacy snapshot dates, holdings, ranking fields, and source identities | Read/build local artifacts | Opt-in feature and label injection is implemented; the refreshed 408-row feature and 1,224-row label shadows pass. Operational generation still needs an authority/receipt fingerprint, an atomic generation contract, and explicit coverage for acquisition and methodology commands. |
| Prospective decision command (`record_prospective_portfolio_decision.py`) | Default legacy reader; writes only to the separate prospective ledger | Read model/write separate ledger | Dry-run decision-input injection and shadow comparison pass without ledger writes. Switch only with the advisor, writer, and release gates; never replay or rewrite prior decisions. |
| LTIA comparison (`compare_tbsz_portfolio.py`) | Separate LTIA store plus legacy model repository | Read | Explicit model-side injection and shadow comparison pass. The LTIA store, negative cash, missing cash, and unknown-date semantics are out of scope and unchanged. |
| MNB OTC import and audits (`history.mnb_otc`, `import_mnb_otc_reports.py`, `inventory_mnb_otc_reports.py`, `generate_mnb_otc_coverage.py`, MNB/KELER audits) | Dedicated legacy table with all decimal text, period, type/frequency, source path, and source hash fields | Read/write evidence | The temporary-only analytical MNB contract and semantic shadow read are implemented, including exact decimal text. Operational import routing and portable evidence packaging remain unimplemented; MNB stays distinct from NAV and generic model metrics. |
| Parallel migration and schema-v3 reference workflow (`model_portfolio_parallel`, `SchemaV3ReferenceRepository`) | Analytical reads, but the singleton manifest and reference validator require the live legacy path/hash and all workbook hashes | Historical validation/read | Preserve the original manifest as an immutable migration receipt. Add a versioned operational authority epoch and import-batch validator; keep a separate historical validator that accepts the retained legacy recovery package. |
| Schema export, audits, and tests | `export_schema.zsh`, Milestone 4 audit, migration scripts, and several tests name or open the legacy store | Tooling/test | Make schema export explicit, convert retained-data tests to deterministic fixtures, and keep migration tests as historical compatibility tests. No test may require an ignored live database after retirement. |
| Documentation and manual tools | Root README, importer README, schema-management guide, script catalog, two PyCharm data sources, and user-supplied `--database` overrides | Operational/manual | Update maintained operational documentation at cutover. Preserve milestone records as historical. Manual or external consumers are unresolved until the operator confirms them; IDE presence alone is not application authority. |

The installed LaunchAgent is an active dependency even though its plist does
not contain a database path: it invokes the watcher whose code selects the
legacy store. Runtime `--database` overrides and ad-hoc SQL are possible
external consumers and cannot be disproved by repository search.

The direct repository search produced this concrete migration checklist:

- ranking and prospective commands:
  `validate_active_policy_temporal_stability.py`,
  `validate_active_ranking_policy_current_universe.py`,
  `validate_capital_preservation_methodology.py`,
  `validate_forward_rank_signal.py`,
  `validate_strict_backtest_pipeline.py`, and
  `record_prospective_portfolio_decision.py`;
- backtest, feature, and label commands:
  `audit_backtest_missing_data_policies.py`,
  `audit_backtest_window_coverage.py`,
  `build_point_in_time_feature_dataset.py`, and
  `build_official_forward_label_store.py`;
- historical-source and methodology commands:
  `plan_historical_nav_acquisition.py`,
  `research_official_portfolio_performance_source.py`,
  `resolve_portfolio_nav_methodology_blockers.py`,
  `audit_portfolio_nav_aggregation_rebalancing_methodology.py`,
  `audit_hu0000554795_alternative_price_sources.py`,
  `audit_hu0000554795_sparse_trading_semantics.py`, and
  `validate_erste_mapping.py`;
- MNB-specific readers/writer:
  `import_mnb_otc_reports.py`, `inventory_mnb_otc_reports.py`,
  `generate_mnb_otc_coverage.py`, and the two `audit_mnb_keler_*` /
  `audit_hu0000554795_*` families;
- migration/tooling commands: `build_schema_v3_model_portfolio_parallel.py`,
  `validate_schema_v3_model_portfolio_parallel.py`,
  `audit_schema_v3_model_portfolio_migration_dry_run.py`, and
  `export_schema.zsh`; and
- retained-data test dependencies:
  `test_active_policy_temporal_stability.py`,
  `test_active_ranking_policy_current_universe.py`,
  `test_backtest_eligibility.py`, `test_forward_rank_signal_validation.py`,
  `test_milestone_4_audit.py`, `test_objective_policy_registry.py`,
  `test_point_in_time_feature_dataset.py`,
  `test_strict_backtest_end_to_end.py`, and the live-baseline cases in
  `test_schema_v3_model_portfolio_migration.py`.

Each list is repository-relative under `scripts/` or `tests/`. Indirect Python
consumers are identified in the matrix by their owning package because they
receive a database path from these entry points.

## Data-gap inventory and reconciliation

### Model workbook rows

The analytical store retains one immutable raw payload for every legacy row.
Applying the current header translation, categorical translation, and numeric
zero-to-NULL rules to those payloads produced an exact 5,283-row multiset match
against the flat legacy table. That establishes recoverability for the current
dataset, but not yet an operational typed interface for every field.

| Field group | Current analytical representation | Verified coverage | Required action |
| --- | --- | --- | --- |
| Date, portfolio, product, ISIN, allocation, asset/sub-asset class, currency, currency risk | Typed snapshot/source-occurrence columns plus raw payload | All 5,283 rows; no missing portfolio, product, or ISIN | Preserve both typed and original values. Define stable identity from source-file SHA-256, sheet, source row, snapshot date, portfolio, and ISIN rather than SQLite row IDs. |
| Ranking metrics: 1-year return, 1-year Sharpe, 1-year volatility, downside risk, maximum drawdown | `instrument_metric_observation` with occurrence-bound references | 5,021; 4,720; 5,139; 18; and 4,299 non-NULL observations respectively | Preserve codes, values, source occurrence multiplicity, and missingness exactly. |
| Sustainability | Raw payload only | 5,217 non-NULL parsed legacy values | Add a typed nullable occurrence attribute while retaining the original payload text and translation version. |
| YTD, 3-year return, 5-year return | Raw payload only | 5,195; 4,731; and 4,127 non-NULL parsed legacy values | Normalize as provider-reported metric observations with stable occurrence references. |
| 3-year Sharpe, 5-year Sharpe, 3-year volatility, information ratio | Raw payload only | 4,394; 3,962; 4,717; and 4,453 non-NULL parsed legacy values | Normalize as provider-reported metric observations; do not infer absent values. |
| Original worksheet cells | `source_payload_json` | 21 original headers per occurrence | Keep immutable. Raw text such as `"0"` is evidence; the established model importer separately maps numeric zero to NULL in its parsed representation. Do not apply shortlist correction semantics to model data. |

### Identifiers, aliases, duplicates, and conflicts

- The legacy data contains 66 ISINs and 89 distinct raw ISIN/product pairs.
  The analytical occurrences preserve all 89. The alias table contains 86
  normalized aliases, so consumers requiring the exact historical label must
  read the occurrence, not treat `instrument_alias` as a lossless row store.
- Twenty-two ISINs have more than one raw legacy product label; normalization
  reduces that to 20 multi-alias ISINs. This is a label reconciliation fact,
  not evidence that instruments should be merged beyond exact ISIN identity.
- Six occurrences of `IE00B7KFL990` on 2024-09-17 across three USD portfolios
  remain `UNRESOLVED_DUPLICATE_SEMANTICS`. The compatibility reader preserves
  them and exact ranking passes. Consolidation must not deduplicate them. They
  continue to block a canonical `portfolio_holding` projection, but they do
  not block a source-occurrence-compatible reader if every old/new result is
  exact.
- The legacy table has no primary key, foreign key, or persisted source-row
  identity. The analytical source file/sheet/row lineage is therefore the
  durable identity. Reconciliation must reject zero or multiple matches.

### MNB OTC observations

Three weekly observations for `HU0000554795` exist only in the legacy table,
covering 2024-08-26–2024-09-01, 2024-10-28–2024-11-03, and
2024-11-25–2024-12-01. The corresponding three retained PDFs match their
stored SHA-256 values. A migration must preserve every decimal as text, the
transaction count, period, currency, price type/frequency, original source
document value, and content hash. These records remain non-NAV audit evidence
and must never become an approved return series merely because their storage
location changes.

## Proposed source-authority and import contracts

### Authority epochs and historical manifests

1. Keep the existing `migration_build_manifest` row byte-for-byte as the
   historical Milestone 7 receipt. It continues to prove which legacy database,
   workbooks, parser/build version, policy, counts, and dataset fingerprint
   established the analytical baseline.
2. Add a versioned `model_source_authority_epoch` for the proposed cutover.
   It binds the baseline legacy SHA-256, portable workbook roles and hashes,
   full parsed-row fingerprint, normalized projection fingerprint, schema and
   parser versions, policy hash, and explicit cutover authorization.
3. Add append-only `model_workbook_admission` and `model_import_batch` records.
   Each binds the original workbook hash, filename/date, sheet/header
   signature, row count, per-row stable identities, before/after model dataset
   fingerprints, parser version, outcome, and operator authorization.
4. Operational readers validate the current authority epoch and ordered import
   receipts. Historical migration validation remains a separate command that
   takes the retained legacy recovery package. It must not silently substitute
   the current analytical hash for an unavailable legacy hash.
5. New contracts use content hashes and portable evidence roles. The existing
   absolute paths stay in the historical receipt; they are not rewritten.

The proposed operational authority is the admitted workbook evidence plus its
ordered receipts, with `portfolio_advisor.sqlite` as the sole operational
projection. The legacy database remains authoritative until a separately
approved cutover completes.

### Single writer and atomic failure

The current importer is not this writer. `process_directory` can create the
legacy database, skips any already-present date without checking workbook
bytes, reads only the visible model sheet, commits one file, and then performs
a separate filesystem move. The watcher adds stability checks, a PID lock, and
a pending latest-snapshot marker, but manual `--import` does not share that
lock. Its post-import sequence runs current-universe validation, prospective
decision construction, and prospective audit; it does not refresh the full
coverage-to-label artifact chain.

The proposed operational contract is:

1. **One entry point and authority.** Manual and watcher requests call the same
   command. It requires one active operational authority epoch, one configured
   analytical target, and one writer identity. The writer lock is acquired
   before input inspection and is also used by manual operation; a SQLite
   `BEGIN IMMEDIATE` remains the final database serialization boundary. Legacy
   and analytical writers must never be enabled concurrently.
2. **Retain bytes before parsing.** After a bounded stability check, copy the
   workbook to a content-addressed, non-overwriting evidence path, verify the
   retained SHA-256, and parse only those retained bytes. The incoming path,
   mtime, and receipt time are operational metadata, never financial dates.
   Failure before verified retention admits nothing and leaves the input
   pending.
3. **Inventory every visible sheet.** Bind the workbook hash, sheet names,
   visibility, recognized roles, header signatures, source rows, parser
   version, and per-sheet disposition into one immutable envelope. Both model
   and shortlist sheets receive an explicit disposition; an unrecognized or
   unhandled sheet can never disappear merely because one supported sheet was
   admitted.
4. **Validate before and inside one transaction.** Parse structural content
   before opening the transaction. Inside one outer transaction, recheck the
   authority predecessor, expected before-fingerprint, source bindings, and
   retained evidence hash; append source rows, typed facts, metrics, lineage,
   sheet dispositions, the ordered admission receipt, and durable downstream
   work items. Run schema, integrity, foreign-key, occurrence, all-date model
   projection, historical-manifest, MNB, and every installed correction
   validator before the sole commit. Any body, interruption, or exit-validation
   failure rolls back the entire admission.
5. **Exact replay and conflict.** The same workbook hash, sheet inventory,
   parser/authority versions, stable rows, expected before-fingerprint, and
   resulting after-fingerprint is a no-op that can resume incomplete
   post-commit work. The same date with changed bytes or bindings is rejected.
   An append-only supersession remains a separate unresolved design; old rows
   are never updated in place and arrival time never implies source chronology.
   Duplicate source occurrences retain their row-level multiplicity and status;
   the six unresolved occurrences are not canonicalized.
6. **Filesystem and downstream work follow commit.** SQLite cannot make a
   database commit atomic with a workbook move or generated files. After commit,
   the receipt is authoritative even if moving the incoming file or refreshing
   artifacts fails. A durable outbox/pending record drives idempotent retries.
   Artifact candidates are generated in dependency order and published as one
   validated generation so consumers never observe a mixed old/new set.

The before/after dataset fingerprints and predecessor receipt form an ordered
chain. Historical receipts and constructed artifacts stay pinned to their
original authority, dataset, classification stages, and correction records;
incremental admission appends a new state instead of invalidating that history.
The exact MNB text contract remains separate and unchanged.

### Failure and recovery states

| Failure | Required durable outcome | Retry/recovery behavior |
| --- | --- | --- |
| Concurrent arrivals or manual/watcher overlap | One writer owns the lock; the other request remains unadmitted | Retry after the owner exits; never bypass the common lock |
| Crash before retained-byte verification | No database receipt | Original input remains pending; reacquire and re-hash |
| Crash after retention but before commit | Retained evidence may exist; no admission | Reconcile by content hash, then start a fresh transaction |
| Parse, binding, correction, or exit-validation failure | No committed admission or outbox item | Correct the input or contract; a retry must revalidate everything |
| Commit succeeds but incoming move/marker fails | Committed admission plus pending operational step | Exact replay finds the receipt and resumes the move/marker without inserting rows |
| Commit succeeds but downstream refresh fails | Committed admission plus `REFRESH_PENDING` work; prior published generation remains active | Regenerate candidates and atomically publish only after the full dependency set validates |
| Software rollback after later admissions | Consolidated receipts and authority remain authoritative | Route compatible readers or build a temporary validated flat export; do not resume stale legacy writes |
| Data restoration | Latest verified analytical backup plus later retained receipts | Restore elsewhere, replay subsequent admissions in order, validate, then separately authorize replacement |

### Shortlist correction boundary

The four installed correction stages are bound to shortlist dataset fingerprint
`32216038d2f69dbf4c6436e91782025ae117dc59fe466e8a31ebc3719f93e8b2`.
A model-only import must not alter shortlist source rows, its stage manifest,
or any correction admission. It records any shortlist sheet in the same
workbook as outside the model admission scope.

A changed shortlist dataset must continue to fail the existing correction
validators unless a separate authorized admission defines how all corrections
bind to the changed evidence. Corrections are never silently dropped, copied
by row ID, or extended to new evidence. Phase 3B.1 implements the explicitly
approved conservative behavior for its synthetic temporary targets: a new
dual-sheet append is rejected before durable database mutation whenever a
dataset-bound shortlist correction layer is installed. Exact replay of an
already admitted envelope remains permitted after full validation.

Phase 3B.1 records both supported sheets and admits them in the same outer
transaction. An invalid or unsupported sheet rejects the entire envelope. A
future partial-disposition design may admit the model sheet while retaining a
durable `SHORTLIST_PENDING` state, but that changes operational semantics and
requires separate approval. Merely recording `SHORTLIST_NOT_ADMITTED` after
moving the workbook remains insufficient. The bounded synthetic approval does
not select this behavior for the operational watcher or authorize a live
correction disposition.

Historical constructed artifacts keep their recorded original/effective
classification stages and fingerprints. New artifacts use the then-current
validated stages. Consolidation does not reinterpret prior artifacts.

## Phased implementation and cutover

### Phase 1 — contracts and adapters, no retained-data writes

**Implementation status: Implemented, non-operational.**

- `ModelPortfolioReader` defines the storage-neutral snapshot boundary.
  `AnalyticalModelPortfolioRepository` is explicit opt-in and requires a
  validated Phase 1 authority-epoch binding; no default selects it.
- `model_source_occurrence_typed_extension` retains the translated nullable
  Sustainability attribute. YTD, 3/5-year return, 3/5-year Sharpe, 3-year
  volatility, and information ratio are nullable provider-reported
  `instrument_metric_observation` records with stable source references.
  Numeric zero follows the legacy parser's zero-to-NULL rule. Raw text such as
  `"0"` remains in `source_payload_json`; the typed API rejects raw strings
  rather than guessing their parsed meaning.
- `model_source_authority_epoch`, `model_workbook_admission`, its stable item
  bindings, and ordered `model_import_batch` receipts are append-only. Every
  Phase 1 epoch is explicitly `PHASE1_NON_OPERATIONAL`; it cannot represent a
  production cutover authorization.
- The dedicated `model_mnb_otc_evidence_source` and
  `model_mnb_otc_evidence_observation` tables preserve the exact provider
  decimal strings separately from their validated numerical meaning. Both
  representations are fingerprinted, so a numerically equal replay with
  changed formatting is rejected. Reporting periods, source hashes, portable
  roles, and non-NAV semantics remain bound as well.
- `admit_model_portfolio_phase1.py` accepts only an existing database beneath
  the system temporary directory. It normalizes source occurrences already
  staged by a synthetic fixture; it does not parse or insert a workbook,
  migrate retained data, or move files.
- Contract validation rechecks stable source bindings, ordered receipt
  fingerprints, metric provenance, exact MNB evidence, SQLite integrity and
  foreign keys, and every installed shortlist correction layer. The existing
  `migration_build_manifest` and its legacy path/hash validator are unchanged.
- Ordered temporary admissions use a connection-bound validation session. It
  validates all prerequisite contracts and declared external dependencies at
  entry, owns one outer `BEGIN IMMEDIATE`, and validates each request before a
  nested savepoint write. The session exhaustively validates the completed
  contract before its sole commit; body exceptions, interruption, stale state,
  and exit-validation failures roll back every session admission. Unvalidated
  rows are never visible to another connection. The public analytical reader
  uses one pinned read transaction and one full validation for an all-date
  session; database, sidecar, or declared-dependency changes invalidate that
  session.
- Phase 1 contract revision 2 adds these exact-text and session guarantees and
  fails closed on revision-1 temporary schemas. The retained analytical store
  has no Phase 1 feature marker, so this is not a live-schema migration.
- Deterministic synthetic fixtures preserve duplicate occurrences and compare
  the legacy and analytical adapters through the real metric and ranking
  functions. No test requires retained workbooks, ignored audits, financial
  databases, provider access, or machine-specific paths.

**Gate:** schemas and contracts are versioned; exact replay and conflicting
date/hash cases are tested; no production default changes. This gate covers
only the implemented scaffolding and does not itself authorize real-data
rehearsal. The corrective rehearsal below was separately authorized.

The corrective real-data rehearsal on 2026-09-24 passed on independent
SQLite-API copies. It admitted 5,283 occurrences from 33 workbook snapshots,
31,579 numeric observations, and all three MNB records; retained six unresolved
duplicates and zero canonical holdings; and preserved the four installed
shortlist correction sets. The final MNB maximum-price string remained exactly
`102.9096`. After the transaction-safety correction, admission completed in
105.266 seconds with two full validations and kept pending rows invisible to a
second connection; the public all-date comparison completed in 89.098 seconds
with one full validation, instead of exceeding two hours through per-call
revalidation. Exact replay completed in 201.283 seconds and was byte-stable,
representative mismatches failed without mutation, and integrity and foreign
keys passed. This rehearsal evidence does not change application defaults,
accept ADR-007, or authorize migration, cutover, or retirement.

### Phase 2 — baseline migration rehearsal

- Use SQLite's backup API to clone the analytical store to a temporary path.
- Add the proposed schema, reconcile all 5,283 model rows from the 33 bound
  workbooks, normalize the eight missing fields, and migrate the three MNB OTC
  records with their three verified source PDFs.
- Verify all original raw payloads, 5,283 occurrences, six unresolved
  duplicate occurrences, 89 raw aliases, 33 dates, and every parsed-field
  multiset. Verify the four shortlist correction stages and historical
  constructed artifacts unchanged.

**Gate:** integrity and foreign keys pass; full row/provenance comparisons are
exact; no retained database was written.

### Phase 3A — explicit consumer injection and shadow comparison

**Implementation status: Implemented, non-operational; retained-corpus result
PASS for all eight bounded workflows.**

- `CapitalPreservationAdvisor`, active/temporal validation, feature and label
  builders, prospective decision construction, and LTIA model-side comparison
  accept the storage-neutral reader boundary. Existing callers still construct
  `ModelPortfolioRepository`, so production behavior and defaults are unchanged.
- `HistoricalPortfolioRepository` now separates model snapshots from the
  optional direct portfolio-NAV source. Its compatibility default still probes
  the model database, while shadow callers can explicitly supply a distinct
  NAV reader or `None`. Model-row equality therefore cannot be mistaken for
  official backtest coverage equality.
- `run_shadow_comparison` requires an explicit result or concrete blocker for
  advisor/current, temporal ranking, strict coverage, feature data, forward
  labels, prospective decision input, LTIA model-side comparison, and MNB
  semantic audit. It rejects missing workflow names, unexplained differences,
  and stale allowlists. Expected differences bind an exact result path and both
  expected values; there is no blanket fingerprint exclusion.
- One bounded run opens one validated analytical read session. Dataset and
  authority provenance are recorded separately for the legacy and analytical
  sources, a storage-neutral all-date model-projection fingerprint must match,
  exact MNB decimal text is compared, and results are returned only after
  database, sidecar, and external-dependency exit validation succeeds.

Synthetic tests cover all eight workflow categories, explicit NAV separation,
the one-validation boundary, exact source-path difference accounting, and stale
session rejection. On 2026-09-24 retained-current-corpus rehearsals used
SQLite-backup-API copies and the supported temporary-only admission contract:

- admission reconciled 5,283 occurrences, 31,579 numeric observations, three
  exact-text MNB records, six unresolved duplicates, and zero canonical model
  holdings in 106.708 seconds with two full validations;
- one validated shadow session completed in 204.410 seconds; current and all
  33 temporal advisor results, strict coverage results, point-in-time feature
  rows, prospective decision input, LTIA model-side allocation, and exact MNB
  semantics matched without unexplained differences;
- the prospective record's `source_evidence_state.database_reference` differed
  only as explicitly expected (`legacy.sqlite` versus `analytical.sqlite`), and
  both isolated database bytes were fingerprinted independently;
- the initial public forward-label comparison failed closed because the old
  retained strict-coverage artifact omitted 72 identities for the two newest
  dates;
- after the separately authorized evidence refresh, maintained coverage held
  the exact 1,224 identities, the feature dataset held 408 rows, and the label
  store held 1,224 materialized records. The follow-up bounded shadow comparison
  passed forward-label equivalence and successful session-exit validation, so
  all eight workflows now pass;
- all 1,224 labels remain explicitly unavailable under current evidence. For
  example, `PB Dinamikus EUR` at 2026-08-18/90 days has required endpoint
  2026-11-16 and remains `MISSING_END` / `SOURCE_INTERVAL_INCOMPLETE`. This is
  correct evidence, not a successful financial label; and
- integrity and foreign keys passed, one analytical full validation served the
  bounded comparison, retained database hashes were unchanged, and all
  temporary private data was removed.

**Gate:** all eight bounded workflows pass on the current corpus with no
unexplained model-reader difference. Phase 3A is PASS. This closes only the
read-equivalence gate: no model or NAV evidence was synthesized, and explicit
unavailability remains unchanged.

### Phase 3B — operational workbook-writer contract

Phase 3B remains Proposed and non-operational. Its first bounded slice,
**Phase 3B.1: synthetic writer core**, is implemented for generated JSON
workbook envelopes and temporary schema-v3 databases only:

- `src/portfolio_advisor/database/model_portfolio_phase3b.py` defines an exact
  two-sheet synthetic inspection format. It requires the reviewed model and
  shortlist sheet roles,
  names, ordered headers, nonempty row sets, ISO snapshot date, explicit ISINs,
  and established model parser/zero-to-NULL rules. This is not the retained
  Excel parser and cannot be selected by production defaults. Raw cell payloads
  and normalized parser output are bound separately, so source text such as
  `"0"` remains exact evidence while typed numeric zero follows the applicable
  model or shortlist storage contract.
- Input bytes are SHA-256 verified and retained at a non-overwriting,
  content-addressed relative path below a supplied temporary root. Input,
  target, and retained files reject resolved escapes, symlinks, and hard links.
- Additive immutable authority, receipt, sheet-disposition, model-item,
  shortlist-item, and pending-outbox records bind workbook bytes, raw payloads,
  stable source identities, parser projections, ordered before/after dataset
  fingerprints, and predecessor identity. Duplicate model occurrences remain
  `UNRESOLVED_DUPLICATE_SEMANTICS`; canonical model holdings remain empty.
- One `BEGIN IMMEDIATE` owns both sheet inserts, their model/shortlist metrics,
  receipt, and the `FILE_FINALIZATION` and `ARTIFACT_REFRESH` pending records.
  Full schema, source-binding, metric, integrity, foreign-key, receipt-chain,
  and installed-correction validation precedes the sole commit. Invalid sheet
  data, interruption, stale bindings, or final validation failure rolls the
  complete candidate back; a second connection cannot see uncommitted rows.
- Exact replay is a validation-only no-op and does not duplicate source rows,
  receipts, or outbox work. Changed content or bindings for an existing date
  fail closed. Concurrent exact requests serialize at SQLite and converge on
  one admission. A retry after a committed admission whose response was lost
  observes the immutable receipt and returns exact replay.
- Successive synthetic dates append an ordered receipt chain. Existing source
  payloads and receipts remain unchanged. A target with an installed Phase 1
  authority, historical migration/shortlist manifest, or correction
  layer rejects a new append rather than silently invalidating historical
  bindings or automatically extending dataset-bound corrections.

Focused synthetic failure injection covers either-sheet rejection, mid-write
interruption, final-validation failure, external visibility, concurrent exact
submissions, and response loss after commit. The installed schema is validated
against exact DDL. The implementation deliberately records pending work only:
there is no lock shared with the operational importer, outbox worker,
filesystem finalizer, or artifact publisher.

Phase 3B.1 excludes retained-data installation, real workbook admission,
watcher wiring, default changes, MNB writer changes, artifact publication,
prospective ledger writes, authority transfer, supersession, cutover, and
retirement.

The next bounded slice, **Phase 3B.2: BIFF-XLS parser/adapter**, is implemented
as a pure source extractor with no admission surface. Its versioned contract is
documented in the
[BIFF-XLS dual-sheet source envelope v1](biff-xls-source-envelope-v1.md):

- one immutable byte sequence is read once, SHA-256 bound, and supplied to
  `xlrd`; filenames require a terminal valid `YYYYMMDD.xls` date;
- the exact visible sheet names ` modell portfóliók` and ` shortlist` map to
  the distinct real-source roles `MODEL_PORTFOLIO` and
  `ANALYTICAL_SHORTLIST`. The latter is not Phase 3B.1's synthetic
  `terméklista` contract;
- exact sheet indices, visibility, ordered header cells, physical row/column
  coordinates, BIFF cell types, raw values, XF/format metadata, merged ranges,
  duplicate occurrences, row references, and deterministic fingerprints are
  retained without translation, scaling, correction, or zero replacement;
- currency-risk, sustainability, and text-zero metric anomalies are reported
  without changing source values; and
- formula text and formula presence remain unavailable through `xlrd`; only a
  cached result, when present, can be extracted, and formulas are never
  evaluated.

Synthetic BIFF fixtures cover both leading-space sheets, duplicate rows,
numeric zero versus blank and text zero, number formats, error cells, formula
limits, invalid dates, malformed bytes, missing/ambiguous/hidden/variant sheets,
invalid or ambiguous headers, data outside the header, and deterministic
repeated parsing. The retained-corpus read-only reconciliation parses all 33
workbooks and preserves 5,283 model plus 10,833 shortlist occurrences. This is
parser validation only: it creates no database, receipt, retained package,
normalized projection, or correction binding.

The proposed next boundary is specified in
[BIFF-XLS normalization and admission policy v1](biff-xls-normalization-admission-policy-v1.md).
It defines a deterministic normalization candidate between the parser and any
writer, including field types, unscaled metric units, role-scoped zero rules,
approved shortlist classification mapping reuse, warning dispositions,
recovery/formula eligibility, exact replay, and preservation of existing
dataset-bound correction references. It does not implement that adapter or
authorize admission. In particular, model zero-to-absence remains a proposed
reuse of the legacy-compatible model rule; shortlist original zeros remain
`0.0` and become effective `NULL` only through the exact existing ADR-003
bindings.

A later separately authorized admission rehearsal must use SQLite-backup-API
copies and retained workbooks, compare the entire ordered receipt chain and all
eight read workflows, exercise crash recovery, and leave all live stores and
watcher configuration unchanged.

**Gate:** Phase 3B does not pass merely because Phase 3B.1 and the parser-only
Phase 3B.2 slice are implemented. A separately authorized retained-data
admission rehearsal must prove the portable evidence package, correction
disposition, and ordered receipts. The operational release must still
prove a manual/watcher shared lock, authority transfer, correction disposition
for changed datasets, recoverable ordered receipts, outbox execution, and
atomic publication of generated evidence.

### Phase 4 — separately authorized cutover

- Obtain explicit cutover approval; this Proposed ADR is not approval.
- Quiesce the WatchPaths workflow, require no pending marker or input file,
  verify no active write transaction or sidecar drift, and create a complete
  recovery package.
- Install the schema/authority baseline through the supported migration on a
  verified database state. Change the shared default, CLI, watcher, scripts,
  maintained operational documentation, and configuration in one release.
- Revalidate the installed LaunchAgent, run one read-only current ranking and
  all operational smoke checks, then re-enable the watcher. Freeze—not
  delete—the legacy database.

**Gate:** exactly one operational writer targets the analytical store; no
active path writes the legacy store; live old/new results equal the approved
rehearsal.

### Phase 5 — stabilization, recovery, and rollback drill

- Retain every admitted workbook and receipt. Take a verified backup before
  each admission and periodically package the database plus required evidence.
- A software rollback may change readers without changing data authority. If
  old code requires the flat schema, generate a temporary compatibility store
  from the consolidated receipts and prove it exact; never point it at the
  stale frozen legacy file after newer imports.
- A data rollback restores the latest verified analytical backup and replays
  later immutable workbook admissions in order. It must preserve shortlist
  corrections and other analytical evidence. Restoring only the cutover-day
  database after post-cutover imports is forbidden.
- Perform both rollback drills before considering retirement.

## Legacy-retirement criteria

`model_portfolio.sqlite` remains retained until all criteria hold and a
separate deletion decision is authorized:

1. No application, importer, watcher, script default, test, maintained
   operational document, installed LaunchAgent behavior, IDE procedure, or
   confirmed external/manual workflow depends on it.
2. All 5,283 baseline rows and every subsequent admission are reproducible
   from retained source evidence; the eight formerly raw-only parsed fields,
   aliases, dates, identifiers, missingness, six duplicate occurrences, and
   three MNB OTC records are preserved and validated.
3. Historical Milestone 7 validation remains reproducible from a portable,
   checksummed recovery package without changing its recorded legacy hash or
   pretending the current analytical store is that source.
4. Ranking, backtest, feature, prospective-input, research, and comparison
   behavior pass old/new gates, and historical constructed artifacts retain
   their recorded classification stages.
5. Recovery and post-cutover-import rollback drills pass. At least one intact,
   verified legacy recovery copy and all required workbooks/PDFs are retained.
6. The legacy file is quiescent, has no sidecars or open handles, and its hash
   matches the retirement manifest immediately before any later cleanup.

Meeting these criteria makes retirement reviewable; it does not authorize
removal.

## Unresolved decisions and blockers

1. **Dual-sheet watcher policy — operational approval still required.** Option
   A is an atomic workbook envelope: both valid sheets are admitted by the same
   outer transaction, and any unadmittable changed shortlist sheet blocks all
   sheet admissions. Option B permits model admission while retaining the exact
   workbook and a durable `SHORTLIST_PENDING` disposition that prevents the
   workbook from being declared fully processed. Phase 3B.1 implements Option
   A on synthetic temporary targets under the bounded approval for that slice.
   Option B should be reconsidered only with explicit partial-state,
   artifact, and operator-display requirements. Silent omission and an
   after-the-fact `SHORTLIST_NOT_ADMITTED` note are rejected options.
2. **Same-date changed workbook — supersession remains unapproved.** Phase
   3B.1 implements strict hash-aware rejection on synthetic temporary targets.
   Using that policy operationally still belongs to a later release approval.
   Append-only supersession would need an explicit predecessor,
   effective-state rules, affected-artifact policy, and authorization; neither
   arrival time nor a filename date is sufficient.
3. **External/manual consumers:** repository search cannot prove that no user
   command, SQL console, or external script uses the legacy path. Operator
   confirmation is required before cutover and again before retirement.
4. **Post-commit artifact publication — approval required.** Phase 3B.1
   transactionally records immutable pending file-finalization and artifact-
   refresh work, but implements no worker or state transition. The recommended
   operational design remains a database outbox plus a generation-level
   manifest and atomic pointer/directory promotion after coverage,
   missing-data policy, strict validation, features, and labels all validate.
   Publishing files one by one
   is rejected because it exposes mixed generations.
5. **Compatibility rollback lifetime:** define how long the temporary flat
   compatibility exporter remains supported after cutover. It must never
   become a second authority. Software rollback should preserve analytical
   data authority; data restoration should use the newest verified backup plus
   ordered replay of later receipts.
6. **MNB source-path portability:** retain the original source-document text,
   but new authority records should also bind portable evidence roles and
   hashes. The package layout and validator input need approval.
7. **Recovered BIFF and formula-origin eligibility:** 27 retained workbooks
   require compound-document recovery, and `xlrd` cannot prove literal-cell
   origin or formula absence. Approve either a hash-bound baseline exception
   plus independent cell comparison, or stronger formula-aware evidence;
   parser success alone is insufficient.
8. **Real-source model zero semantics and anomaly dispositions:** approve
   whether the model-only compatibility projection may continue omitting
   numeric-zero metric observations, and approve occurrence-bound treatment of
   the 24 currency-risk and three sustainability warnings. No proposed English
   currency-risk mapping is currently authorized.

Until these are resolved and Phase 4 is explicitly authorized, the current
legacy defaults and parallel analytical status remain correct.
