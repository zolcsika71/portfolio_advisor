# Repository guide

## Implementation status

This repository has no LLM or autonomous application agents. It provides a
deterministic advisor workflow whose financial calculations, eligibility, and
ranking are typed Python functions controlled by reviewed configuration.

`src/portfolio_advisor/main.py` prints the current capital-preservation ranking
by default. `--import` retains the workbook-import workflow. The active policy
is `CAPITAL_PRESERVATION_RANKING_POLICY` v1.0.1.

Milestone 11C admits immutable official ECB €STR, New York Fed daily SOFR, and
MNB HUFONIA history artifacts through provider-neutral reference-rate
provenance contract v2. EUR, USD, and HUF benchmark evidence are admitted.
Phase E exact-share-class EUR/HUF NAV evidence is installed. Phase F1 adds the
approved portfolio-metrics methodology contract. Phase F2 adds the pure,
Decimal-only metric foundation, including the explicitly model-based
constant-drift/constant-diffusion irregular-interval volatility estimator, but
no portfolio-wealth construction or supplementary NAV admission. Real
metrics/ranking and finalist comparison remain unavailable. EUR is blocked by
unknown distribution treatment and HUF by benchmark-convention evidence. The
construction runtime is `IMPLEMENTED_BLOCKED_BY_DATA` and production cutover
is `NOT_AUTHORIZED`.

## Core components

- `DB_creation/`: reads the visible `modell portfóliók` workbook sheet,
  translates supported headers/categories, validates rows, and imports dated
  snapshots into `model_portfolios`.
- `database/repository.py`: read-only date and holding access for analysis,
  feature construction, and historical audits.
- `metrics/`: allocation-weighted reported snapshot indicators, historical
  equal-period compatibility formulas, the Phase F1 policy contract, and the
  separate Phase F2 Decimal/provenance governed metric foundation.
- `ranking/`: policy validation, strict eligibility, normalization, scoring,
  and deterministic tie-breaking.
- `construction/`: deprecated instrument-screening compatibility plus the
  Milestone 11B normalized 80/20 candidate engine, evidence checks, lineage,
  persistence, and read-only foundation audit.
- `reference_rates/`: provider-neutral v2 benchmark/source/manifest/observation
  contracts, explicit provider versus internal identity, conservative
  availability boundaries, official ECB €STR, New York Fed SOFR, and MNB
  HUFONIA adapters, deterministic offline migration/import, and multi-benchmark
  validation. Tests never contact a provider.
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
- `tbsz/`: a separate local database of observed LTIA evidence, manually
  recorded completed transactions, reconciliation, and advisory-only model
  portfolio comparison. `tbsz` is a legacy compatibility identifier.

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

## Architecture decision records

Codex must consult `docs/decisions/README.md` and the existing ADRs before work
that makes an important project decision. Important decisions include changes
to architecture, database or storage choices, data authority or provenance,
public interfaces, dependencies with substantial impact, and security or
deployment approaches. Routine fixes, formatting, and simple refactors do not
require an ADR.

When important decision-making is in scope, Codex must check for an existing
relevant ADR before creating another and create or update the ADR as part of
the same work. New ADRs use the next unused sequential, zero-padded number and
must never overwrite or renumber an existing ADR. An unresolved choice remains
`Proposed`; `Accepted` requires explicit approval or an established decision
within the authorized task scope. A change to an accepted decision requires a
new ADR linked to the old record, whose status becomes `Superseded by ADR-NNN`.
Do not invent historical approval or backfill unrelated decisions. Keep ADRs
in `docs/decisions/`, architecture documentation in `docs/architecture/`, and
diagram artifacts in `docs/diagrams/`.

## Technical diagrams

During relevant development or documentation work, Codex must proactively
assess whether a technical diagram would materially clarify the change; do not
wait for an explicit diagram request. When justified, create or update a
PlantUML diagram for detailed sequences, complex state machines, component
interfaces, conditional processing, error or retry paths, and security or
trust boundaries. Keep prose or Mermaid when either is sufficient, and do not
create diagrams merely to populate the directory or bulk-convert useful
Mermaid diagrams.

Before creating a diagram, consult `docs/diagrams/README.md` and the existing
diagram sources. Update an applicable diagram instead of creating a duplicate.
Save every new source as `docs/diagrams/<descriptive-name>.puml`, link it from
the relevant maintained Markdown, and maintain the diagram index. Diagrams
must reflect verified code or contracts and cite what was checked. Clearly
label proposed behavior as `Proposed` and implemented behavior as `Implemented`;
never present an unverified design as current system behavior. When local
tooling is available, validate PlantUML syntax and inspect the rendered output
for readability.

This is a task-driven obligation, not background generation. It remains
subject to the current task's scope, read-only requirements, and explicit edit
limits; when those constraints prohibit a warranted diagram change, report the
need without making the change.

## Local data conventions

`database/`, generated `data/audit/`, retained `data/raw/`, legacy compatibility
path `data/tbsz/`, `logs/`, and `tmp/` are local-only. They may contain
provider-controlled evidence, generated audits, private LTIA records, or
machine-specific state.
Their code, tests, deterministic schemas, and validated policy are versioned.
Do not add credentials, tokens, cookies, private account data, or
machine-specific paths.

Provider acquisition is always an explicit operator action. Raw response bytes and
their receipt are immutable local evidence; repeat import and validation are
offline. No scheduler or ordinary advisor workflow may invoke acquisition.

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
poetry run python scripts/acquire_ecb_estr.py
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
```

The due-monitor LaunchAgent, when explicitly installed, invokes only the
offline due monitor and prospective audit. It must never acquire a provider
source or admit an outcome automatically. The separate XLS WatchPaths
LaunchAgent invokes only the watcher wrapper, which uses the existing importer
and never acquires providers or admits outcomes.
