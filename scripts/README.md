# Operational scripts

Run scripts from the repository root with `poetry run python scripts/<name>.py
--help`. Most scripts are offline audits; acquisition scripts are explicit and
must never be invoked by ranking, backtesting, label construction, tests, the
prospective monitor, or the scheduler.

## Core validation

| Script | Purpose |
|---|---|
| `formalize_capital_preservation_ranking_policy.py` | Build the policy contract from reviewed local evidence. |
| `validate_capital_preservation_methodology.py` | Validate metric directions, formulas, and point-in-time safeguards. |
| `validate_active_ranking_policy_current_universe.py` | Validate the active policy on the current candidate universe. |
| `validate_active_policy_temporal_stability.py` | Audit deterministic temporal ranking behavior. |
| `build_point_in_time_feature_dataset.py` | Build point-in-time features without forward labels. |
| `validate_strict_backtest_pipeline.py` | Reconcile strict backtest eligibility and source evidence. |
| `audit_backtest_window_coverage.py` | Audit exact source/window coverage. |
| `build_official_forward_label_store.py` | Materialize official results or explicit unavailable labels. |
| `validate_forward_rank_signal.py` | Assess an existing label store; never optimize policy weights. |

## Event-driven XLS import

| Script | Purpose |
|---|---|
| `process_watched_xls_import.py` | Run one bounded WatchPaths event through stable-file, lock, existing-importer, validation, prospective-recording, and audit stages. |
| `install_xls_import_watch.py` | Validate or explicitly install the matching current-user WatchPaths LaunchAgent. |

The watcher is event-driven rather than a polling loop. It never fetches a
provider or admits an outcome; see `ops/launchd/README.md` for installation
and operational constraints.

## Official reference-rate evidence

| Script | Purpose |
|---|---|
| `acquire_ecb_estr.py` | The sole ECB network boundary: perform one bounded request to the reviewed official ECB €STR endpoint and retain validated content-addressed bytes plus a receipt. |
| `acquire_sofr.py` | The sole New York Fed network boundary: perform one bounded fixed SOFR request with redirects disabled and retain exact content-addressed JSON plus a receipt. |
| `acquire_hufonia.py` | The sole MNB HUFONIA network boundary: perform one bounded workbook request with redirects disabled and retain exact content-addressed BIFF8 bytes plus a receipt. |
| `build_ecb_estr_candidate.py` | Historical Phase B path: copy an empty reference-rate target, import retained €STR evidence offline, and reconcile pre-existing logical data. |
| `build_sofr_candidate.py` | Copy the populated provenance-v2 installation to a disposable target, import retained SOFR offline, and prove exact €STR/non-reference preservation. |
| `build_hufonia_candidate.py` | Copy the exact Phase C installation to a disposable target, import retained HUFONIA offline, and prove exact €STR, SOFR, schema, and non-reference preservation. |
| `import_ecb_estr_reference_rate.py` | Deterministically import an explicit retained raw/receipt pair into an explicit database target; an identical repeat is a byte-preserving no-op. |
| `import_sofr_reference_rate.py` | Deterministically import the fixed retained SOFR raw/receipt pair; exact replay inserts zero rows and leaves database bytes unchanged. |
| `import_hufonia_reference_rate.py` | Deterministically import the retained MNB HUFONIA workbook/receipt pair; exact replay inserts zero rows and leaves database bytes unchanged. |
| `migrate_reference_rate_provenance_contract.py` | Copy an exact populated v1 installation to an explicit disposable candidate, or replay an explicit v2 candidate with `--target`; verify the retained ECB pair offline and transactionally reconstruct provider-neutral v2 provenance. It never installs the candidate. |
| `validate_ecb_estr_reference_rate.py` | Validate the admitted ECB evidence, raw provenance, fingerprints, row contracts, integrity, foreign keys, and zero constructed-portfolio rows read-only. |
| `validate_sofr_reference_rate.py` | Validate benchmark-scoped SOFR contracts, retained raw/receipt evidence, conservative availability, fingerprints, integrity, foreign keys, and zero constructed rows read-only. |
| `validate_hufonia_reference_rate.py` | Validate benchmark-scoped HUFONIA contracts, retained workbook/receipt evidence, date-basis transition, Decimal precision, conservative availability, fingerprints, integrity, foreign keys, and zero constructed rows read-only. |
| `validate_reference_rate_schema.py` | Validate the exact current v2 reference-rate DDL and feature marker read-only, whether the feature is empty or populated. |
| `validate_reference_rate_provenance.py` | Validate every admitted benchmark bundle, internal identity, provider fields, retained artifact/receipt reconciliation, availability, revision chains, fingerprints, and benchmark-wide source isolation read-only. `--require-sofr` requires at least the completed Phase C scope; `--require-hufonia` requires the exact ESTR+SOFR+HUFONIA Phase D scope. |
| `acquire_phase_e_nav.py` | Explicit operator-only Erste Market acquisition boundary, classified `APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE`; complete responses are retained before validation, rejected transport is quarantined, and `--offline-audit` deterministically replays existing evidence without network or writes. |
| `build_phase_e_nav_candidate.py` | Copy an explicit installed database to a disposable path, add only Phase E NAV provenance schema, import the retained EUR/HUF bundles offline, and reconcile every pre-existing logical table. |
| `import_phase_e_nav.py` | Import the retained 16 exact-share-class manifests and 3,984 exact Decimal NAV observations into an explicit Phase E candidate; identical replay is a byte-preserving no-op. |
| `audit_milestone_11c_phase_e.py` | Validate installed Phase E schema, exact cohorts, raw/receipt/manifest/observation lineage, fingerprints, legacy NAV, integrity, foreign keys, and zero constructed rows read-only. |
| `audit_phase_f2_metric_foundation.py` | Revalidate Phase E read-only and emit the deterministic Phase F2 policy/metric-foundation audit with regular, irregular, and flat synthetic references; it does not admit evidence, construct wealth, rank, select, or activate production use. |
| `audit_phase_f3a_wealth_foundation.py` | Emit the timestamp-free Phase F3A synthetic EUR wealth and recomputed-lineage audit to stdout. It reads only the immutable F1 metrics and construction policies; it does not access a database, admit evidence, use a provider, activate reconstruction, rank, select, persist, or write an audit file. |

Automated tests, offline import, candidate construction, and validation never
contact a provider. Missing provider revision IDs or dataset versions are
represented as `NULL`, never synthesized. Raw empty revision indicators remain
distinct from absent fields, and `RETRIEVAL_BOUND` evidence is unavailable
before exact capture. Malformed, empty, wrong-series, duplicate, unauthorized
revision, conflicting, or provenance-inconsistent evidence fails closed and is
never silently overwritten. EUR €STR, USD daily SOFR, and HUF HUFONIA are
admitted, and no benchmark is aligned to portfolio returns.

Phase E's retained Erste Market chart responses preserve the strict global
`application/json` transport contract. Only the exact HTTPS numeric chart
endpoint has a separate semantic assessment for whole-body JSON mislabelled
`text/html; charset=utf-8`; the original transport classification remains
`QUARANTINED_REJECTED_RESPONSE`. See the
[Phase E evidence record](../docs/milestone_11c_phase_e_nav_provenance.md).

## Historical evidence and source audits

| Script group | Purpose |
|---|---|
| `plan_historical_nav_acquisition.py`, `acquire_missing_historical_nav_series.py` | Plan and explicitly acquire bounded constituent history. |
| `validate_erste_mapping.py`, `validate_oekb.py` | Validate provider-specific retained history. |
| `reconcile_at0000605324.py`, `check_at0000605324_*.py`, `analyze_at0000605324_conflict_patterns.py` | Audit, never auto-resolve, the AT0000605324 conflict. |
| `audit_hu0000554795_*.py`, `analyze_hu0000554795_coverage_gap.py`, `research_hu0000554795_alternative_price_sources.py` | Preserve evidence and fail-closed terminal status for HU0000554795. |
| `inventory_mnb_otc_reports.py`, `import_mnb_otc_reports.py`, `generate_mnb_otc_coverage.py`, `audit_mnb_keler_*.py` | Handle MNB/KELER OTC evidence as non-NAV audit material. |
| `audit_backtest_missing_data_policies.py`, `freeze_mnb_keler_absence_semantics.py` | Audit missing-data semantics without changing strict eligibility. |

MNB/KELER acquisition and external-source research commands are deliberate,
bounded operations. They are not ordinary validation commands and must retain
their provenance locally.

## Portfolio-NAV governance and direct-source research

| Script | Purpose |
|---|---|
| `audit_portfolio_nav_aggregation_rebalancing_methodology.py` | Assess whether snapshot semantics support a portfolio-NAV methodology. |
| `resolve_portfolio_nav_methodology_blockers.py` | Resolve only provenance-backed methodology blockers. |
| `freeze_portfolio_nav_reconstruction.py` | Freeze unproven synthetic reconstruction fail closed. |
| `reassess_forward_validation_strategy.py` | Classify the validation evidence currently available. |
| `research_official_portfolio_performance_source.py` | Perform bounded direct official portfolio-performance research. |

## Prospective validation

| Script | Purpose |
|---|---|
| `record_prospective_portfolio_decision.py` | Finalize a live decision record, or an explicitly marked research backfill. |
| `audit_prospective_portfolio_validation.py` | Produce the deterministic append-only ledger audit. |
| `check_due_prospective_outcomes.py` | Offline classification of live outcome slots; no fetch or admission. |
| `assess_due_prospective_outcome_unavailable.py` | Append-only closure of one due slot with provenance and no metrics. |
| `admit_prospective_portfolio_outcome.py` | Admit one due, direct, validated official outcome supplied locally. |
| `schedule_prospective_outcome_due_checks.py` | Generate or explicitly install a monitor-only rolling launchd schedule. |

## LTIA observed-current-state workflow (legacy TBSZ compatibility)

The separate local LTIA database records facts the user already owns or has
already traded; it never connects to a broker or submits an order. Script,
directory, package, and database names containing `tbsz` are legacy
compatibility identifiers.

| Script | Purpose |
|---|---|
| `initialize_tbsz_portfolio_from_pdfs.py` | Import only explicitly confirmed LTIA facts from legacy path `data/tbsz/source/`; `--write-template` creates a filename-only confirmation template when a screen cannot be reliably parsed. |
| `migrate_tbsz_portfolio.py` | Apply only the recognized local schema migration after creating and verifying an ignored backup; it does not import sources or compare portfolios. |
| `show_tbsz_current_portfolio.py` | Show one read-only, unified LTIA current view for one account: explicit `ASSET` and `CASH` rows retain native source currencies and separate position/cash snapshot provenance. |
| `update_tbsz_transaction.py` | Append one user-completed BUY or SELL to the manual transaction ledger. It is not a brokerage command. |
| `confirm_tbsz_instrument_mapping.py` | Add a reviewed manual ISIN/alias mapping; fuzzy similarity is never promoted. |
| `reconcile_tbsz_pdf_snapshots.py` | Compare two retained, dated position snapshots without rewriting history. |
| `compare_tbsz_portfolio.py` | Read-only LTIA-vs-model target-allocation comparison with an explicit tolerance, identity/FX blockers, separate cash, and no provider or FX fetch. |
| `create_tbsz_current_portfolio_once.py` | One-time isolated LTIA current-standings read model from retained, manually confirmed PDFs in legacy TBSZ paths. It contains current ASSET and CASH rows only; normal reruns refuse overwrite and `--force` first creates a verified ignored backup. |

Source PDFs, manual confirmations, and `database/tbsz_portfolio.sqlite` are
local-only and ignored by Git. Initial observed positions are not fabricated as
historical transactions; later PDF evidence is appended and reconciled.

## Graphify and utility wrappers

`gquery.zsh`, `gupdate.zsh`, and `check_graphify.zsh` change into
`data/knowledge` before using Graphify. `update_graphify.zsh` updates the
external CLI; it is not part of financial validation. `export_schema.zsh`
exports the local SQLite schema.

Graphify is not a financial-data input, metric engine, ranker, or decision
maker. No script implements retrieved Graphify citations in a portfolio
explanation, an OpenAI explanation, a constructed-shortlist finalist, or a
buy/sell/cash proposal. Those target responsibilities and current availability
are defined in [Portfolio workflow and current availability](../docs/portfolio_workflow_status.md).

## Safety

No script may turn constituent history, Graphify knowledge, snapshot indicators,
or a missing value into realized portfolio performance. See the repository
README and the documentation in `docs/` for the governing architecture.
