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

## TBSZ observed-portfolio workflow

The separate local TBSZ database records facts the user already owns or has
already traded; it never connects to a broker or submits an order.

| Script | Purpose |
|---|---|
| `initialize_tbsz_portfolio_from_pdfs.py` | Import only explicitly confirmed George PDF facts from `data/tbsz/source/`; `--write-template` creates a filename-only confirmation template when a screen cannot be reliably parsed. |
| `show_tbsz_current_portfolio.py` | Show the latest observed source snapshot and the separate, non-netted manual transaction ledger for one account. |
| `update_tbsz_transaction.py` | Append one user-completed BUY or SELL to the manual transaction ledger. It is not a brokerage command. |
| `confirm_tbsz_instrument_mapping.py` | Add a reviewed manual ISIN/alias mapping; fuzzy similarity is never promoted. |
| `reconcile_tbsz_pdf_snapshots.py` | Compare two retained, dated position snapshots without rewriting history. |
| `compare_tbsz_portfolio.py` | Read-only TBSZ-vs-model allocation comparison with an explicit tolerance and no FX or provider fetch. |

Source PDFs, manual confirmations, and `database/tbsz_portfolio.sqlite` are
local-only and ignored by Git. Initial observed positions are not fabricated as
historical transactions; later PDF evidence is appended and reconciled.

## Graphify and utility wrappers

`gquery.zsh`, `gupdate.zsh`, and `check_graphify.zsh` change into
`data/knowledge` before using Graphify. `update_graphify.zsh` updates the
external CLI; it is not part of financial validation. `export_schema.zsh`
exports the local SQLite schema.

## Safety

No script may turn constituent history, Graphify knowledge, snapshot indicators,
or a missing value into realized portfolio performance. See the repository
README and the documentation in `docs/` for the governing architecture.
