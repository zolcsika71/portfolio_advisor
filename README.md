# Portfolio Advisor

Portfolio Advisor imports dated model-portfolio workbooks, calculates
point-in-time portfolio indicators, and applies a deterministic
capital-preservation ranking policy. It also keeps a separate, local-only TBSZ
observed-portfolio record for advisory comparison. Financial calculations,
eligibility, and ranking are typed Python code; Graphify is limited to
source-backed methodology, constraints, and explainability.

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
- Actual TBSZ evidence is separate from the model portfolio, remains local,
  and never submits brokerage orders.
- One official ECB €STR history artifact is admitted as exact EUR
  reference-rate evidence. It does not activate benchmark alignment,
  Sharpe/Sortino, portfolio construction, trading, or production cutover.

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

## TBSZ observed-portfolio workflow

TBSZ records are separate from model-portfolio recommendations and are never
used to change ranking policy or execute a trade:

```text
local George PDFs in data/tbsz/source/
        ↓
confirmed TBSZ source import
        ↓
database/tbsz_portfolio.sqlite
        ↓
append-only manual BUY/SELL ledger
        ↓
later PDF reconciliation
        ↓
read-only comparison with a recommended model portfolio
```

The source directory, manual confirmations, and TBSZ database are ignored by
Git. The importer preserves filename/hash provenance and unknown source fields
as `NULL`; it requires manual confirmation rather than OCR or fuzzy identity
promotion. Manual BUY/SELL commands record an action the user has already
executed—they are not brokerage orders. Cash remains separate by currency and
comparison never fetches FX. See the [script catalog](scripts/README.md) for
the operational commands.

## Architecture and evidence

| Layer | Responsibility |
|---|---|
| `src/portfolio_advisor/DB_creation/` | Validates and imports visible model-portfolio worksheets. |
| `database/model_portfolio.sqlite` | Local snapshot source; never mutated by ranking/backtesting. |
| `src/portfolio_advisor/ranking/` | Approved policy loading, fail-closed eligibility, normalization, scoring, and stable ranking. |
| `src/portfolio_advisor/construction/` | Reviewed normalized 80/20 construction domain and persistence foundation; production remains data-blocked. |
| `src/portfolio_advisor/reference_rates/` | Official ECB €STR acquisition boundary, exact offline parsing/import, immutable provenance, and read-only validation. |
| `src/portfolio_advisor/history/` | Provenance-aware source resolution, lifecycle evidence, local official constituent history, and reconstruction freeze. |
| `src/portfolio_advisor/backtesting/` | Strict forward-window eligibility and canonical metric handling. |
| `src/portfolio_advisor/features/` | Point-in-time features and explicit official-forward-label availability records. |
| `src/portfolio_advisor/prospective/` | Append-only decision ledger, due monitoring, and provenance-gated future outcome admission. |
| `src/portfolio_advisor/operations/` | Event-driven XLS watcher orchestration and current-user LaunchAgent installation safety. |
| `src/portfolio_advisor/tbsz/` | Local observed TBSZ evidence, manual transactions, reconciliation, and read-only comparison. |

Generated databases, audit JSON, raw evidence, and operational logs stay
local under `database/` and `data/`; they are deliberately ignored because
they may be large, provider-controlled, or machine-specific. The code and
tests that reproduce their deterministic handling are versioned.

See [methodology](docs/methodology.md), [ECB €STR Phase B evidence](docs/milestone_11c_phase_b_ecb_estr_ingestion.md), [historical source rules](docs/historical_nav_sources.md), [backtesting and prospective validation](docs/backtesting.md), [launchd operations](ops/launchd/README.md), and the [script catalog](scripts/README.md).

## Official ECB €STR evidence operations

The acquisition command below is the only network path. It performs one
bounded request to the reviewed official ECB endpoint and retains immutable
content-addressed raw bytes plus a provenance receipt:

```bash
poetry run python scripts/acquire_ecb_estr.py
```

Candidate construction, repeat import, and validation consume those retained
files offline and require explicit paths:

```bash
poetry run python scripts/build_ecb_estr_candidate.py --help
poetry run python scripts/import_ecb_estr_reference_rate.py --help
poetry run python scripts/validate_ecb_estr_reference_rate.py --help
```

Only EUR €STR is admitted. USD SOFR, HUF HUFONIA, benchmark-to-portfolio-date
alignment, cash-return treatment, Sharpe/Sortino, and real shortlist
construction remain fail-closed.

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
