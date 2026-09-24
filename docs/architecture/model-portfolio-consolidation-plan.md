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
| Workbook evidence | The installed manifest names and hashes 33 processed workbooks and the legacy database; every binding still validated |
| Shortlist corrections | Dataset fingerprint `32216038d2f69dbf4c6436e91782025ae117dc59fe466e8a31ebc3719f93e8b2`; 1,278 original classification items, 287 composed items, 10,833 English pair-mapping items, and 23,979 metric corrections |
| MNB OTC evidence | Three weekly observations for one exact ISIN; their three retained PDFs exist and match the hashes stored in the legacy rows; the analytical store has no MNB OTC table |
| Concurrency context | No SQLite WAL, SHM, or journal sidecars were present. PyCharm held idle handles to both databases, and the installed XLS WatchPaths LaunchAgent was loaded, watching, and not running at inspection time. |

Different hashes would not by themselves establish a data difference. The
model comparison above used row-level and behavioral checks. Both database
files remain local evidence and were not changed by this review.

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

| Workflow or consumer | Current path and fields | Role today | Proposed replacement and gate |
| --- | --- | --- | --- |
| Default advisor (`src/portfolio_advisor/main.py`, `CapitalPreservationAdvisor`) | `database/model_portfolio.sqlite`; latest/date-specific holdings and five ranking fields | Read | Inject an explicit analytical model repository implementing the same snapshot interface. Do not auto-detect schemas. Prove exact `HoldingObservation`, warnings, eligibility, metrics, ranks, and winner on every date before changing the default. |
| Manual workbook import (`DB_creation.database_create`) | Writes the flat `model_portfolios` table; parses all 21 worksheet fields, converts numeric zero to SQL NULL, skips an existing date, then moves the workbook | Write | Replace with one schema-v3 model admission command. It must retain workbook bytes/hash, raw payload, every parsed field, stable source identity, and an immutable import receipt. Pointing the flat importer at the analytical store is forbidden. |
| Watcher (`operations.xls_import_watch`) | Invokes `portfolio_advisor.main --import`; reads the hard-coded legacy path before and after import; then runs current-universe validation, prospective recording, and prospective audit | Orchestration/read/write | Use the same single admission command and shared configured analytical path. Preserve the lock, stable-file check, pending marker, and post-import sequence. Update and explicitly revalidate the installed LaunchAgent before cutover. |
| Core repository and advisor validation (`database.repository`, `advisor.*`) | Concrete `ModelPortfolioRepository`; model identity, allocation, currency/risk, asset class, and five ranking metrics | Read | Introduce a small read protocol and an `AnalyticalModelPortfolioRepository`; retain the legacy implementation only for comparison and recovery validation. |
| Ranking and methodology commands (`validate_active_*`, `validate_capital_preservation_methodology.py`, `validate_forward_rank_signal.py`) | Default legacy database, same model snapshot contract plus governed policy/audit inputs | Read/audit | Change defaults only after all-date old/new equivalence and isolated tests pass. Audit manifests must name the repository contract and current authority epoch, not merely a filename. |
| Backtest coverage and strict validation (`audit_backtest_*`, `validate_strict_backtest_pipeline.py`, `HistoricalPortfolioRepository`) | Legacy holdings; optional flat `portfolio_nav_history` probe | Read/audit | Use the analytical model adapter. Split the optional legacy portfolio-NAV probe from the model repository; both current stores lack that optional table, and no NAV may be synthesized during the change. |
| Features, labels, and research (`features.dataset`, `features.official_forward_labels`, `history.nav_acquisition`, `history.official_portfolio_performance_research`, portfolio-NAV blocker/methodology modules) | Legacy snapshot dates, holdings, ranking fields, and source identities | Read/build local artifacts | Inject the analytical adapter and include the new authority/dataset fingerprint in generated manifests. Compare generated artifacts byte-for-byte where deterministic and semantically otherwise. |
| Prospective decision command (`record_prospective_portfolio_decision.py`) | Default legacy reader; writes only to the separate prospective ledger | Read model/write separate ledger | Switch only with the advisor reader gate. Preserve the prospective store and decision identity; do not replay or rewrite prior decisions. |
| LTIA comparison (`compare_tbsz_portfolio.py`) | Separate LTIA store plus legacy model repository | Read | Change only the model-side adapter. The LTIA store, negative cash, missing cash, and unknown-date semantics are out of scope and unchanged. |
| MNB OTC import and audits (`history.mnb_otc`, `import_mnb_otc_reports.py`, `inventory_mnb_otc_reports.py`, `generate_mnb_otc_coverage.py`, MNB/KELER audits) | Dedicated legacy table with all decimal text, period, type/frequency, source path, and source hash fields | Read/write evidence | Add a dedicated analytical MNB OTC evidence extension, not NAV or generic model metrics. Migrate and revalidate all three rows and PDFs exactly; keep exact replay/no-op and conflict rejection. |
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

- One command owns model workbook admission. The manual CLI and watcher call
  it; no second importer writes the same scope.
- Parse and validate the workbook before opening a write transaction. Reject
  unknown headers/categories, ambiguous dates, invalid identifiers, and
  inconsistent source lineage.
- Create and verify a SQLite-backup-API recovery snapshot before a live write.
  Acquire the watcher lock and one SQLite write transaction. Insert source,
  parsed facts, metrics, lineage, and the import receipt in that transaction.
- Before commit, run schema, integrity, foreign-key, source-row, all-date model
  projection, and every installed shortlist-correction validator. A failure
  rolls back the entire database transaction. No atomic claim extends to the
  later filesystem move of the workbook.
- Move a workbook to `data/xls/processed` only after commit. If that move or a
  post-import step fails, the immutable receipt makes an exact retry a no-op
  and allows the operational step to resume without adding rows.
- Exact source hash plus identical bindings is a no-op. A changed workbook for
  an existing date fails closed. Historical replacement requires a separate,
  explicitly authorized supersession contract; it never updates old rows in
  place.

### Shortlist correction boundary

The four installed correction stages are bound to shortlist dataset fingerprint
`32216038d2f69dbf4c6436e91782025ae117dc59fe466e8a31ebc3719f93e8b2`.
A model-only import must not alter shortlist source rows, its stage manifest,
or any correction admission. It records any shortlist sheet in the same
workbook as outside the model admission scope.

A changed shortlist dataset must continue to fail the existing correction
validators unless a separate authorized admission defines how all corrections
bind to the changed evidence. Corrections are never silently dropped, copied
by row ID, or extended to new evidence. Whether the watcher should block the
whole workbook until that separate shortlist disposition is available is an
unresolved operational decision below.

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

### Phase 3 — shadow readers and behavioral comparison

- Run legacy and analytical adapters for every consumer in the matrix while
  retaining the legacy writer and defaults.
- Compare every date's holdings, missingness, metrics, warnings, eligibility,
  ranking order, winner, backtest coverage, features, labels, acquisition
  targets, methodology outputs, prospective input, and LTIA model-side result.
- Compare MNB audit manifests semantically and verify exact persisted values.
  Approved shortlist English-label differences are outside model equivalence
  and must not be reported as numerical or selection regressions.

**Gate:** zero unexplained numerical, selection, multiplicity, or provenance
differences. Expected presentation differences are explicitly allowlisted.

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

1. **Dual-sheet watcher policy:** decide whether a workbook containing a new
   shortlist sheet blocks model admission until shortlist correction bindings
   are separately authorized, or is admitted model-only with an explicit
   `SHORTLIST_NOT_ADMITTED` receipt and retained pending evidence. Silent
   omission is not acceptable.
2. **Historical workbook correction contract:** same-date changed bytes must
   fail today. A future append-only supersession model needs explicit approval
   before such evidence can be admitted.
3. **External/manual consumers:** repository search cannot prove that no user
   command, SQL console, or external script uses the legacy path. Operator
   confirmation is required before cutover and again before retirement.
4. **Compatibility rollback lifetime:** define how long the temporary flat
   compatibility exporter remains supported after cutover. It must never
   become a second authority.
5. **MNB source-path portability:** retain the original source-document text,
   but new authority records should also bind portable evidence roles and
   hashes. The package layout and validator input need approval.

Until these are resolved and Phase 4 is explicitly authorized, the current
legacy defaults and parallel analytical status remain correct.
