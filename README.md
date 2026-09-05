# Portfolio Advisor

Portfolio Advisor imports dated model-portfolio workbooks, calculates
point-in-time portfolio indicators, and applies a deterministic
capital-preservation ranking policy. It also keeps separate, local-only LTIA
(Long-Term Investment Account) holdings and cash evidence for later advisory
comparison. Financial calculations, eligibility, and ranking are typed Python
code; Graphify is limited to source-backed methodology, constraints, and
explainability.

See the [current workflow and availability contract](docs/portfolio_workflow_status.md)
for the governed target workflow, the distinction between user horizon and
historical evidence, and the parts not implemented today.

## Current operating boundaries

- `CAPITAL_PRESERVATION_RANKING_POLICY` v1.0.1 is the active policy.
- Ranking uses only decision-date, allocation-weighted reported indicators.
- Historical constituent NAV evidence is retained separately from ranking.
- Synthetic constituent-to-portfolio NAV reconstruction is frozen as
  `PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED`.
- No historical portfolio forward label is manufactured when direct official
  portfolio evidence is absent.
- The prospective ledger records finalized live decisions and pending 90/180/
  365-day outcome slots without fabricating outcomes.
- Actual LTIA evidence is separate from model portfolios, remains local, and
  never submits brokerage orders. Legacy `tbsz` names are compatibility
  identifiers only.
- Official ECB €STR, New York Fed daily SOFR, and MNB HUFONIA history artifacts
  are admitted as exact EUR, USD, and HUF reference-rate evidence under
  provider-neutral provenance contract v2. Evidence admission does not activate
  benchmark alignment, Sharpe/Sortino, portfolio construction, trading, or
  production cutover.
- Phase E admits exact-share-class EUR/HUF NAV evidence under immutable
  provenance from Erste Market as an `APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE`;
  Erste Market is not an authoritative NAV administrator. Portfolio analytics
  and construction remain a later checkpoint.
- Phase F1 approves and fingerprints the portfolio-metrics methodology contract,
  including strict date intersection, buy-and-hold drift, unremunerated cash,
  benchmark alignment, exact Decimal boundaries, and an explicitly model-based
  irregular-interval volatility estimator. Phase F2 implements the pure,
  deterministic Decimal metric foundation but does not construct portfolio
  wealth or activate real evidence use, ranking, or selection. EUR remains
  blocked by unknown share-class distribution treatment; HUF remains blocked
  by HUFONIA convention evidence.
- No runtime retrieves Graphify citations for a recommendation and no OpenAI
  explanation layer exists. Neither system supplies financial inputs, metrics,
  rankings, or a final selection.
- No current workflow calculates a current-LTIA-versus-target transition,
  proposes buy/sell/cash changes, rebalances, submits an order, or authorizes
  production cutover.

## Model-portfolio workflow

Requirements: Python 3.12 and Poetry.

```bash
poetry install
poetry run python -m portfolio_advisor.main
```

The analysis command prints a structured JSON ranking from
`database/model_portfolio.sqlite` and the reviewed v1.0.1 policy in
`data/knowledge/validated_rules/capital_preservation_ranking.yaml`. The import
command retains the direct manual import path:

```bash
poetry run python -m portfolio_advisor.main --import
poetry run python -m portfolio_advisor.DB_creation.database_create \
  --input-directory /path/to/import \
  --processed-directory /path/to/processed \
  --database /path/to/model_portfolio.sqlite
```

Workbooks must expose a supported `modell portfóliók` worksheet and have an
eight-digit date immediately before `.xls`, for example
`portfolio_20250726.xls`.

When the explicitly installed current-user WatchPaths LaunchAgent is enabled,
normal monthly operation is:

```text
copy .xls to data/xls/import
        ↓
WatchPaths LaunchAgent
        ↓
stable-file and lock guard
        ↓
existing XLS importer
        ↓
database/model_portfolio.sqlite
        ↓
latest ranking validation
        ↓
prospective live-decision recording and audit
```

The wrapper waits for a positive-size file with unchanged size and mtime over
a bounded window, prevents concurrent imports, and delegates workbook parsing
to the existing importer. It does not fetch providers or admit outcomes. See
[launchd operations](ops/launchd/README.md) for explicit dry-run and install
commands.

## LTIA current-investment workflow (legacy TBSZ compatibility)

LTIA records are separate from model-portfolio recommendations and are never
used to change ranking policy or execute a trade. The retained local source
and implementation paths still use `tbsz` as a compatibility identifier:

```text
local George PDFs in data/tbsz/source/
        ↓
confirmed LTIA source import
        ↓
database/tbsz_portfolio.sqlite
        ↓
append-only manual BUY/SELL ledger
        ↓
later PDF reconciliation
        ↓
read-only comparison with a recommended model portfolio
```

The source directory, manual confirmations, and legacy-named LTIA database are
ignored by Git. The importer preserves filename/hash provenance and unknown source fields
as `NULL`; it requires manual confirmation rather than OCR or fuzzy identity
promotion. Manual BUY/SELL commands record an action the user has already
executed—they are not brokerage orders. Cash remains separate by currency and
comparison never fetches FX. See the [script catalog](scripts/README.md) for
the operational commands.

## Architecture and evidence

| Layer | Responsibility |
|---|---|
| `src/portfolio_advisor/DB_creation/` | Validates and imports the visible `modell portfóliók` model-portfolio worksheet. |
| `database/model_portfolio.sqlite` | Local snapshot source; never mutated by ranking/backtesting. |
| `src/portfolio_advisor/ranking/` | Approved policy loading, fail-closed eligibility, normalization, scoring, and stable ranking. |
| `src/portfolio_advisor/construction/` | Reviewed normalized 80/20 construction domain and persistence foundation; production remains data-blocked. |
| `src/portfolio_advisor/reference_rates/` | Provider-neutral v2 provenance, conservative availability boundaries, official ECB €STR, New York Fed SOFR, and MNB HUFONIA adapters, exact offline import, and read-only multi-benchmark validation. |
| `src/portfolio_advisor/history/` | Provenance-aware source resolution, Phase E exact-share-class NAV evidence, lifecycle evidence, local official constituent history, and reconstruction freeze. |
| `src/portfolio_advisor/backtesting/` | Strict forward-window eligibility and canonical metric handling. |
| `src/portfolio_advisor/features/` | Point-in-time features and explicit official-forward-label availability records. |
| `src/portfolio_advisor/prospective/` | Append-only decision ledger, due monitoring, and provenance-gated future outcome admission. |
| `src/portfolio_advisor/operations/` | Event-driven XLS watcher orchestration and current-user LaunchAgent installation safety. |
| `src/portfolio_advisor/tbsz/` | Local observed LTIA evidence, manual transactions, reconciliation, and read-only comparison; `tbsz` is a compatibility package name. |

Generated databases, audit JSON, raw evidence, and operational logs stay
local under `database/` and `data/`; they are deliberately ignored because
they may be large, provider-controlled, or machine-specific. The code and
tests that reproduce their deterministic handling are versioned.

See the [current workflow and availability contract](docs/portfolio_workflow_status.md), [methodology](docs/methodology.md), [Phase F1 methodology policy](docs/milestone_11c_phase_f1_portfolio_metrics_policy.md), [Phase F2 metric foundation](docs/milestone_11c_phase_f2_metric_foundation.md), [Phase E NAV evidence](docs/milestone_11c_phase_e_nav_provenance.md), [HUFONIA Phase D evidence](docs/milestone_11c_phase_d_hufonia_ingestion.md), [SOFR Phase C evidence](docs/milestone_11c_phase_c_sofr_ingestion.md), [provider-neutral Phase C0 provenance](docs/milestone_11c_phase_c0_reference_rate_provenance_contract.md), [ECB €STR Phase B evidence](docs/milestone_11c_phase_b_ecb_estr_ingestion.md), [historical source rules](docs/historical_nav_sources.md), [backtesting and prospective validation](docs/backtesting.md), [launchd operations](ops/launchd/README.md), and the [script catalog](scripts/README.md).

## Official reference-rate evidence operations

Each provider-specific acquisition command is an explicit network boundary. It
performs one bounded request to its reviewed official endpoint and retains
immutable content-addressed raw bytes plus a provenance receipt. For ECB €STR:

```bash
poetry run python scripts/acquire_ecb_estr.py
```

Candidate construction, repeat import, and validation consume those retained
files offline and require explicit paths:

```bash
poetry run python scripts/build_ecb_estr_candidate.py --help
poetry run python scripts/import_ecb_estr_reference_rate.py --help
poetry run python scripts/validate_ecb_estr_reference_rate.py --help
poetry run python scripts/migrate_reference_rate_provenance_contract.py --help
poetry run python scripts/validate_reference_rate_schema.py --help
poetry run python scripts/validate_reference_rate_provenance.py --help
poetry run python scripts/acquire_sofr.py
poetry run python scripts/build_sofr_candidate.py --help
poetry run python scripts/import_sofr_reference_rate.py --help
poetry run python scripts/validate_sofr_reference_rate.py --help
poetry run python scripts/validate_reference_rate_provenance.py --require-sofr
poetry run python scripts/acquire_hufonia.py
poetry run python scripts/build_hufonia_candidate.py --help
poetry run python scripts/import_hufonia_reference_rate.py --help
poetry run python scripts/validate_hufonia_reference_rate.py --help
poetry run python scripts/validate_reference_rate_provenance.py --require-hufonia
poetry run python scripts/acquire_phase_e_nav.py --offline-audit
poetry run python scripts/build_phase_e_nav_candidate.py --help
poetry run python scripts/import_phase_e_nav.py --help
poetry run python scripts/audit_milestone_11c_phase_e.py
```

The acquisition commands are explicit operator-only network boundaries; every
import and validation command is offline. EUR €STR, USD daily overnight SOFR,
and HUF HUFONIA are admitted, but they are distinct benchmarks and are not
interchangeable. Phase E provides exact-share-class EUR/HUF NAV evidence, but
benchmark-to-portfolio-date alignment, cash-return treatment, Sharpe/Sortino,
and real shortlist construction remain fail-closed. Milestone 11 remains
NO-GO; Milestones 12 and 13 remain NO-GO; production cutover remains
`NOT_AUTHORIZED`.

## Prospective validation operations

Record the current, latest canonical decision before any outcome is known:

```bash
poetry run python scripts/record_prospective_portfolio_decision.py --record-type live --dry-run
poetry run python scripts/record_prospective_portfolio_decision.py --record-type live
poetry run python scripts/audit_prospective_portfolio_validation.py
```

The due monitor is offline and never acquires or admits an outcome:

```bash
poetry run python scripts/check_due_prospective_outcomes.py
poetry run python scripts/schedule_prospective_outcome_due_checks.py
```

On macOS, an explicitly requested `--install` installs the generated
monitor-only LaunchAgent for the current user. It is not a provider-acquisition
or outcome-admission workflow.

## Development checks

```bash
poetry run pytest
poetry run ruff check src tests scripts
poetry run mypy src tests
git diff --check
```
