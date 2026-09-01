# Repository guide

## Implementation status

This repository has no LLM or autonomous application agents. It provides a
deterministic advisor workflow whose financial calculations, eligibility, and
ranking are typed Python functions controlled by reviewed configuration.

`src/portfolio_advisor/main.py` prints the current capital-preservation ranking
by default. `--import` retains the workbook-import workflow. The active policy
is `CAPITAL_PRESERVATION_RANKING_POLICY` v1.0.1.

Milestone 11B adds the constructed-portfolio domain, deterministic allocation
engine, additive schema feature, lineage, and transactional persistence. The
production runtime is `IMPLEMENTED_BLOCKED_BY_DATA`; roadmap-complete portfolio
metrics/ranking and finalist comparison remain unavailable. Production cutover
remains `NOT_AUTHORIZED`.

## Core components

- `DB_creation/`: reads the visible `modell portfóliók` workbook sheet,
  translates supported headers/categories, validates rows, and imports dated
  snapshots into `model_portfolios`.
- `database/repository.py`: read-only date and holding access for analysis,
  feature construction, and historical audits.
- `metrics/`: allocation-weighted reported snapshot indicators and standalone
  return-series formulas for an already approved series.
- `ranking/`: policy validation, strict eligibility, normalization, scoring,
  and deterministic tie-breaking.
- `construction/`: deprecated instrument-screening compatibility plus the
  Milestone 11B normalized 80/20 candidate engine, evidence checks, lineage,
  persistence, and read-only foundation audit.
- `history/`: provenance-aware constituent history, lifecycle/reconciliation
  evidence, strict resolvability, local source stores, and reconstruction
  governance.
- `backtesting/`: fixed forward-window construction and strict admission of
  canonical official results.
- `features/`: point-in-time feature data and an explicit forward-label store
  that retains unavailable candidates rather than fabricating labels.
- `prospective/`: append-only live decision records, pending horizon slots,
  offline due monitoring, and provenance-gated direct-outcome admission.
- `operations/`: the bounded, current-user WatchPaths XLS import wrapper and
  its fail-closed LaunchAgent installer.
- `tbsz/`: a separate local database of observed TBSZ evidence, manually
  recorded completed transactions, reconciliation, and advisory-only model
  portfolio comparison.

## Critical boundaries

- Graphify provides methodology navigation and source-backed constraints only;
  it never supplies financial inputs or realized outcomes.
- A required unresolved constituent rejects a strict backtest. There is no
  dropping, renormalization, proxy, cash/zero-return, interpolation, fill,
  source stitching, or nearest-date substitution.
- Synthetic constituent-to-portfolio NAV reconstruction is frozen as
  `PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED` until portfolio-specific
  allocation, timing, currency, distribution, and duplicate-row semantics are
  proven.
- Direct official portfolio performance remains a separate possible path, but
  outcomes are admitted only after their horizon is due and provenance,
  identity, semantics, and interval checks pass.
- Prospective historical schema replays use `RESEARCH_BACKFILL`; only
  `PROSPECTIVE_LIVE_RECORD` entries count as live evidence.

## Local data conventions

`database/`, generated `data/audit/`, retained `data/raw/`, `data/tbsz/`,
`logs/`, and `tmp/` are local-only. They may contain provider-controlled
evidence, generated audits, private TBSZ records, or machine-specific state.
Their code, tests, deterministic schemas, and validated policy are versioned.
Do not add credentials, tokens, cookies, private account data, or
machine-specific paths.

## Operational commands

Run from the repository root:

```bash
poetry run python -m portfolio_advisor.main
poetry run python -m portfolio_advisor.main --import
poetry run python scripts/record_prospective_portfolio_decision.py --record-type live
poetry run python scripts/check_due_prospective_outcomes.py
poetry run python scripts/audit_prospective_portfolio_validation.py
poetry run python scripts/process_watched_xls_import.py --dry-run
poetry run python scripts/initialize_tbsz_portfolio_from_pdfs.py
```

The due-monitor LaunchAgent, when explicitly installed, invokes only the
offline due monitor and prospective audit. It must never acquire a provider
source or admit an outcome automatically. The separate XLS WatchPaths
LaunchAgent invokes only the watcher wrapper, which uses the existing importer
and never acquires providers or admits outcomes.
