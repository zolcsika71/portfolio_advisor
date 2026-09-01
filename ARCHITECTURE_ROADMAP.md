# Portfolio Advisor — Architecture Roadmap

## 1. Purpose

Portfolio Advisor is an evidence-driven, deterministic portfolio advisory platform.

Its purpose is to:

1. maintain a normalized investment-data foundation;
2. select the best predefined model portfolio for a chosen investment objective;
3. construct one or more portfolios from the current `shortlist` investment universe;
4. select the best shortlist-derived portfolio for the same objective;
5. compare the best model portfolio with the best shortlist-derived portfolio;
6. present recommended portfolios to the user;
7. let the user make the final portfolio choice;
8. compare the selected target portfolio with the user's actual LTIA investments;
9. produce advisory, account-aware rebalancing guidance;
10. track future outcomes and determine whether the recommendation methodology actually works.

The project must remain:

* auditable;
* reproducible;
* deterministic;
* source-backed;
* fail-closed when evidence is incomplete.

## 1.1 Roadmap governance

This roadmap is a living architectural plan. Its implementation sequence may evolve when verified
implementation results or source evidence reveal a safer or more effective order. A sequencing
change must not remove an architectural, financial-governance, provenance or fail-closed
requirement merely because that requirement is difficult to satisfy.

Every material roadmap change must record:

```text
reason
prerequisite
replacement or revised sequence
current verified status
```

Completed history is forward-only: do not rewrite a completed checkpoint or historical commit to
make later terminology or sequencing appear contemporaneous.

Roadmap milestone states mean:

| State | Meaning |
| --- | --- |
| `COMPLETED` | The bounded checkpoint's implemented contract and verification gates are complete. |
| `IN_PROGRESS` | Authorized implementation has started but its complete checkpoint gate has not passed. |
| `PLANNED` | The checkpoint is defined but implementation has not started. |
| `BLOCKED_BY_DATA` | Code or policy progress cannot produce the governed result until required evidence is admitted. |
| `BLOCKED_BY_POLICY` | Required reviewed policy or methodology approval is absent. |
| `SUPERSEDED` | A later recorded sequence replaces this planned checkpoint; retained history is not deleted. |
| `NOT_AUTHORIZED` | The action, especially production cutover, has no authorization to proceed. |

`GO` and `NO-GO` are checkpoint decisions, not milestone states.

A passing validator establishes only the contract that validator actually checks. It does not
implicitly prove upstream source authority, unimplemented calculations, portfolio comparability,
roadmap completion or production readiness.

## 1.2 Verified implementation status

This status is grounded in repository history, versioned contracts and the installed schema-v3
validators through the Milestone 11C Phase B release.

| Checkpoint | State | Verified boundary |
| --- | --- | --- |
| Milestones 4–10 | `COMPLETED` | Data audit, schema-v3/LTIA foundation, model migration, NAV integration, shortlist import and objective registry checkpoints are retained with their validators. |
| Milestone 11A — construction-policy approval | `COMPLETED` | Commit `ea3406c2df38a3b7a274c7507cb174950149b7d3`; reviewed policy only. |
| Milestone 11B — constructed-portfolio domain and schema | `COMPLETED` | Commit `0aa7cf12a7ef5bead07b0753b2229909fb8e2373`; allocation, schema, lineage and persistence foundation. |
| Milestone 11C Phase A — reference-rate schema foundation | `COMPLETED` | Commit `37d9e0c2a9f57de1837a8d7a43aab54f9ef38772`; empty evidence schema and immutable contracts. |
| Milestone 11C Phase B — ECB €STR evidence | `COMPLETED` | Exact official EUR history, immutable raw provenance, offline import and read-only validation; no metric activation. |
| Overall roadmap-compliant Milestone 11 | `BLOCKED_BY_DATA` | EUR €STR evidence is admitted, but current NAV, remaining currency benchmarks, alignment and portfolio analytics cannot produce a governed real candidate. |
| Roadmap-compliant Milestone 12 | `BLOCKED_BY_DATA` | No real persisted `SHORTLIST_FINALIST` exists for portfolio-to-portfolio comparison. |
| Milestone 13 | `PLANNED` / `NO-GO` | Dividend evidence work remains deferred. |
| Production cutover | `NOT_AUTHORIZED` | No checkpoint authorizes production cutover. |

## 1.3 Historical exploratory Milestone 11–12 infrastructure

Commits `b07bf94` and `73efe92` contain useful deterministic intermediate infrastructure. The first
ranks a singleton shortlist instrument through the reviewed instrument-screening policy; the
second compares that instrument with a model portfolio. The singleton's synthetic 100% adapter is
not a portfolio allocation and neither commit creates or selects a persisted
`SHORTLIST_CONSTRUCTED` portfolio.

These workflows are classified as intermediate instrument screening and exploratory
model-versus-instrument comparison. They are not roadmap-compliant completion of Milestone 11 or
Milestone 12. Preserve their commits, import compatibility and audit value; do not revert, remove,
repurpose or rewrite them.

---

# 2. Domain terminology

## LTIA — Long-Term Investment Account

`LTIA` means:

```text
Long-Term Investment Account
```

This is the domain term used throughout Portfolio Advisor for the user's actual long-term investment accounts.

The current implementation still contains legacy identifiers such as:

```text
tbsz_portfolio.sqlite
tbsz_current_portfolio.sqlite

src/portfolio_advisor/tbsz/

tbsz_accounts
```

These are compatibility names inherited from the existing implementation.

They should eventually migrate toward:

```text
ltia_portfolio.sqlite

src/portfolio_advisor/ltia/

ltia_account
ltia_snapshot
ltia_position
ltia_cash_balance
ltia_transaction
```

The terminology migration must not alter financial semantics or historical evidence.

---

# 3. Core decision model

Portfolio Advisor must never answer:

```text
What is the best portfolio?
```

without first knowing the investment objective.

The correct question is:

```text
What is the best portfolio
for the selected investment objective?
```

The initial supported objectives are:

```text
CAPITAL_CONSERVATION
DIVIDEND_PORTFOLIO
```

The architecture must support future objectives without requiring another database redesign.

Possible future objectives include:

```text
TOTAL_RETURN
INCOME_AND_GROWTH
LOW_VOLATILITY
BALANCED
QUALITY
VALUE
MULTI_ASSET_DEFENSIVE
INFLATION_PROTECTION
```

---

# 4. Final user workflow

```text
                    SELECT INVESTMENT OBJECTIVE
                              │
                              ▼
                      objective policy
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
     MODEL PORTFOLIOS                     SHORTLIST
              │                               │
              ▼                               ▼
      objective-specific               objective-specific
         eligibility                      eligibility
              │                               │
              ▼                               ▼
          ranking                    portfolio construction
              │                               │
              ▼                               ▼
      BEST MODEL PORTFOLIO            shortlist candidates
                                              │
                                              ▼
                                      shortlist ranking
                                              │
                                              ▼
                                  BEST SHORTLIST PORTFOLIO
              │                               │
              └───────────────┬───────────────┘
                              ▼
                     FINAL COMPARISON
                              │
                              ▼
                     RECOMMENDED PORTFOLIOS
                              │
                              ▼
                         USER CHOICE
                              │
                              ▼
                    SELECTED TARGET PORTFOLIO
                              │
                              ▼
                    CURRENT LTIA PORTFOLIO
                              │
                              ▼
                      ALLOCATION GAP
                              │
                              ▼
                   ADVISORY REBALANCING
                              │
                              ▼
                     PROSPECTIVE TRACKING
                              │
                              ▼
                      OUTCOME VALIDATION
```

The final investment decision remains with the user.

Portfolio Advisor must never place brokerage orders.

---

# 5. Architectural rules

## 5.1 Python is authoritative

Python remains authoritative for:

* database imports;
* identity validation;
* financial calculations;
* eligibility;
* normalization;
* portfolio construction;
* allocation constraints;
* scoring;
* ranking;
* diagnostics;
* stress/scenario calculations;
* outcome admission;
* validation metrics;
* rebalancing calculations.

---

## 5.2 LLM boundary

LLMs may:

* explain structured results;
* summarize methodology;
* produce human-readable reports;
* explain strengths and weaknesses;
* generate source-backed narrative.

LLMs must not change:

* ISIN identity;
* eligibility;
* portfolio weights;
* scores;
* ranking;
* calculated financial metrics;
* admitted observations;
* realized outcomes;
* policy values.

---

## 5.3 Graphify boundary

Graphify is restricted to:

* methodology retrieval;
* source-backed financial theory;
* constraints;
* citations;
* explainability;
* research navigation.

Graphify must never generate:

* financial inputs;
* market observations;
* portfolio scores;
* rankings;
* realized outcomes;
* security identities.

---

## 5.4 Fail-closed evidence policy

Missing, unresolved or ambiguous evidence must remain explicit.

Do not silently:

* interpolate;
* proxy;
* substitute securities;
* stitch incompatible sources;
* use nearest-date replacements;
* drop unresolved constituents;
* renormalize incomplete portfolios;
* manufacture forward labels;
* manufacture portfolio NAV history.

If evidence is insufficient:

```text
UNAVAILABLE
UNRESOLVED
REJECTED
```

is preferable to a fabricated result.

---

# 6. Separation of concepts

The architecture must keep four concepts separate.

## 6.1 What can be invested in?

Investment universes:

```text
model portfolios
shortlist securities
```

---

## 6.2 What does the user own now?

Current private investment state:

```text
LTIA accounts
security positions
cash balances
```

---

## 6.3 What does “best” mean?

Investment objective:

```text
CAPITAL_CONSERVATION
DIVIDEND_PORTFOLIO
future objectives
```

---

## 6.4 What should the user choose?

Portfolio Advisor produces:

```text
SYSTEM_RECOMMENDED
```

The user produces:

```text
USER_SELECTED
```

These concepts must remain independent.

---

# 7. Target database architecture

## 7.1 Preferred physical databases

Long-term target:

```text
database/
    portfolio_advisor.sqlite
    ltia_portfolio.sqlite
```

During migration the existing databases remain:

```text
model_portfolio.sqlite
official_historical_nav.sqlite
prospective_portfolio_validation.sqlite

tbsz_portfolio.sqlite
tbsz_current_portfolio.sqlite
```

Legacy names must not be renamed until migration compatibility is proven.

---

# 8. `portfolio_advisor.sqlite`

This is the objective-neutral analytical database. The list below describes the architectural
destination; the verified implementation state is recorded separately in Section 1.2.

It should contain:

* canonical instrument master;
* instrument aliases;
* source/import metadata;
* model portfolios;
* portfolio snapshots;
* portfolio holdings;
* portfolio cash;
* shortlist snapshots;
* shortlist entries;
* constructed portfolios;
* historical NAV/price observations;
* instrument metrics;
* portfolio metrics;
* dividend observations;
* fundamental observations;
* investment objectives;
* policy references;
* decision runs;
* recommendations;
* finalists;
* prospective outcomes;
* diagnostics;
* stress-test results.

## 8.1 Installed reference-rate evidence foundation

Schema v3 currently contains the additive feature:

```text
MILESTONE_11C_REFERENCE_RATE_EVIDENCE
```

with:

```text
feature revision:             1
feature fingerprint:          aace6e9cf4b33fbf9cad503a987b945ebb44e1db5673381fd47cbd57716d921b
schema-contract fingerprint:  1d9cb07e1bee4bed81ebe6a58a293ea544249498f736899069452ae167b59d61
```

The installed tables are:

```text
reference_rate_definition
reference_rate_source
reference_rate_import_manifest
reference_rate_observation
```

The evidence contract requires exact reference-rate values through validated `Decimal`
representation; immutable hashes of retained raw source bytes; exact request and retrieval
metadata; and deterministic source-dataset fingerprints. Definition and source evidence preserves
benchmark identity, administrator, currency, units, official day-count convention and compounding
convention. Observation date and publication date remain separate.

Provider corrections are append-only revisions. A later observation identifies the superseded
revision rather than overwriting it, and admission must apply a governed as-of cutoff. Definitions,
sources, manifests and observations remain linked by genuine foreign keys so evidence cannot cross
benchmark or source identity.

Milestone 11C Phase B admits the official ECB series below:

```text
dataflow:             ECB:EST(1.0)
data structure:       ECB:ECB_EST1(1.0)
series key:           B.EU000A2X2A25.WT
full series identity: EST.B.EU000A2X2A25.WT
benchmark ISIN:       EU000A2X2A25
frequency:            business-daily
unit:                 percent per annum, multiplier 0, three decimals
official day count:   ACT/360
daily accrual:        simple overnight; the daily series is not a compounded index
```

The reviewed machine endpoint is
`https://data-api.ecb.europa.eu/service/data/ECB,EST,1.0/B.EU000A2X2A25.WT`
with `detail=full`, `format=csvdata` and `includeHistory=true`. The retained HTTP 200 CSV is
618,988 bytes with SHA-256
`e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8`.
It contains 1,771 admitted dates/provider versions from `2019-10-01` through `2026-08-31`.
`TIME_PERIOD` remains the observation date; timezone-qualified `VALID_FROM` supplies provider
revision identity and publication availability, while `VALID_TO` supplies explicit supersession.

Installed production row counts are one definition, one source, one import manifest and 1,771
observations. Dataset fingerprint
`99a1a2ff837688bb78fd0b81cbef1ef64f27f1cab36cc2acdb0ded5026cc534e` binds the parsed
history. The raw bytes and request/retrieval receipt remain immutable local evidence. Exact repeat
import is a no-op; malformed, missing, wrong-series, duplicate or conflicting evidence fails
closed. Reference-rate runtime admission, benchmark alignment, cash-return treatment, Sharpe and
Sortino remain unavailable.

---

# 9. `ltia_portfolio.sqlite`

This becomes the target name for the private investor-specific database.

It contains:

* LTIA accounts;
* source snapshots;
* source-specific instrument identities;
* aliases;
* position observations;
* cash observations;
* transactions;
* observed ROI;
* reconciliation evidence.

Cross-database security matching must use:

```text
ISIN
```

not database-local integer identifiers.

---

# 10. Existing database roles

## 10.1 `model_portfolio.sqlite`

Current authoritative source for predefined model portfolios.

Its current flat structure must be migrated toward a normalized relational representation.

During migration it remains a read-only compatibility source.

---

## 10.2 `official_historical_nav.sqlite`

Current historical NAV evidence store.

Its instrument identity should eventually map to the canonical instrument master through ISIN.

Synthetic constituent-to-portfolio NAV reconstruction remains prohibited unless its methodology is later proven.

---

## 10.3 `prospective_portfolio_validation.sqlite`

Current prospective-decision evidence ledger.

Its append-only semantics and fingerprints must remain intact.

Future relational versions may reference:

```text
decision_run
portfolio_snapshot_id
investment_objective
policy_version
```

Historical evidence must never be rewritten to invent information that was not available at decision time.

---

## 10.4 `tbsz_portfolio.sqlite`

Current legacy private LTIA evidence database.

Target:

```text
ltia_portfolio.sqlite
```

Do not rename immediately.

Migrate only after:

* schema compatibility is proven;
* repository references are updated;
* tests pass;
* operational scripts are updated.

---

## 10.5 `tbsz_current_portfolio.sqlite`

Current legacy current-state projection.

Treat it as a derived read model rather than a second independent source of truth.

Preferred architecture:

```text
LTIA source evidence
      ↓
LTIA evidence database
      ↓
deterministic projection
      ↓
CURRENT LTIA INVESTMENTS
```

---

# 11. Canonical security identity

## 11.1 ISIN is the business identifier

For investable securities:

```text
ISIN
```

is the canonical cross-system identity.

It must identify securities across:

* model portfolios;
* shortlist;
* historical NAV;
* constructed portfolios;
* LTIA holdings;
* dividend observations;
* fundamental observations;
* prospective analytics.

---

## 11.2 Surrogate relational keys

Inside the central relational database use:

```text
instrument_id INTEGER PRIMARY KEY
isin TEXT UNIQUE NOT NULL
```

for efficient joins.

However:

```text
instrument_id
```

is database-local.

Across physical databases use:

```text
ISIN
```

---

# 12. Cash is not a security

Cash/free money must never receive a fake ISIN.

Do not create:

```text
CASH
EUR_CASH
FREE_MONEY
```

as instrument identities.

Represent cash as:

```text
currency + amount
```

Examples:

```text
EUR 12500
USD 3000
HUF 750000
```

Portfolio value is:

```text
security holdings
+
cash balances
```

---

# 13. Canonical instrument master

## 13.1 Instrument universe source

Build the canonical instrument master from the union of valid ISINs found in:

```text
modell portfóliók
+
shortlist
```

across the historical XLS files.

Do not build it from `model_portfolio.sqlite` alone.

---

## 13.2 `instrument`

Suggested schema:

```text
instrument

instrument_id INTEGER PRIMARY KEY
isin TEXT NOT NULL UNIQUE

canonical_name TEXT NOT NULL
instrument_type TEXT NULL
base_currency_code TEXT NULL
asset_class TEXT NULL
sub_asset_class TEXT NULL
issuer TEXT NULL

active_from DATE NULL
active_to DATE NULL

created_at
updated_at
```

Only stable or slowly changing attributes belong here.

---

# 14. Instrument aliases

Different providers may use different names for the same ISIN.

Create:

```text
instrument_alias
```

Suggested fields:

```text
alias_id PK
instrument_id FK

source_type
source_name
normalized_source_name

source_file_id FK NULL

mapping_status
valid_from NULL
valid_to NULL
```

Example source types:

```text
MODEL_XLS
SHORTLIST_XLS
GEORGE_LTIA
NAV_PROVIDER
DIVIDEND_PROVIDER
```

---

# 15. Identity resolution policy

Use this deterministic hierarchy:

```text
1. valid explicit ISIN supplied by source

2. already manually confirmed alias

3. exact normalized source name
   → exactly one canonical instrument

4. exact shortlist mapping
   → exactly one ISIN

5. exact model-portfolio mapping
   → exactly one ISIN

6. authoritative manual resolution
```

Never automatically promote:

```text
fuzzy-name similarity
```

to canonical identity.

Similarity may generate:

```text
IDENTITY_CANDIDATE
```

for manual review only.

---

# 16. Cross-source identity consistency

Validate:

```text
same normalized name
+
multiple ISINs
```

as an identity conflict.

Validate:

```text
same ISIN
+
conflicting metadata
```

as a data-quality warning or error.

Examples:

* currency mismatch;
* asset-class mismatch;
* share-class mismatch;
* inconsistent product naming.

Do not silently overwrite conflicting metadata.

---

# 17. Source provenance

## `source_file`

```text
source_file_id PK
filename
sha256 UNIQUE
source_type
source_date
imported_at
```

## `source_sheet`

```text
source_sheet_id PK
source_file_id FK
sheet_name
```

Every imported observation must remain traceable to its original source.

---

# 18. Relational model portfolios

## `portfolio`

```text
portfolio_id PK
portfolio_name
portfolio_type
base_currency_code NULL
active_from NULL
active_to NULL
```

Initial portfolio types:

```text
MODEL
SHORTLIST_CONSTRUCTED
```

Future:

```text
CUSTOM
BENCHMARK
SIMULATED
```

---

# 19. `portfolio_snapshot`

```text
portfolio_snapshot_id PK
portfolio_id FK
snapshot_date
source_sheet_id FK NULL
construction_policy_id FK NULL
created_at
```

Portfolio identity and dated portfolio state must remain separate.

---

# 20. `portfolio_holding`

```text
portfolio_holding_id PK

portfolio_snapshot_id FK
instrument_id FK

weight
market_value NULL
source_row NULL

UNIQUE(
    portfolio_snapshot_id,
    instrument_id
)
```

---

# 21. `portfolio_cash`

```text
portfolio_cash_id PK
portfolio_snapshot_id FK

currency_code
amount NULL
weight NULL

cash_role
source
```

Possible roles:

```text
AVAILABLE
RESERVE
PORTFOLIO_ALLOCATION
PENDING_INVESTMENT
```

---

# 22. Migration of `model_portfolio.sqlite`

Treat the current flat database as a migration source.

Target decomposition:

```text
portfolio
      ↓
portfolio_snapshot
      ↓
portfolio_holding
      ↓
instrument
```

Metrics should move into relational observation tables where appropriate.

---

# 23. Migration equivalence requirement

Storage migration must not silently change investment logic.

For historical snapshots:

```text
legacy DB
+
same policy
```

must produce the same:

```text
eligibility
feature values
scores
rank order
winner
```

as:

```text
relational DB
+
same policy
```

Any difference requires investigation.

---

# 24. Database migration framework

Introduce schema v3 for the relational redesign.

Suggested:

```text
src/portfolio_advisor/database/
    schema/
    migrations/
        backup.py
        validation.py
        v2_to_v3.py
```

Migration process:

```text
1. inspect schema version
2. create verified backup
3. begin transaction
4. create relational structures
5. migrate instrument identities
6. migrate portfolios
7. migrate snapshots
8. migrate holdings
9. migrate metrics
10. migrate cash
11. validate counts
12. validate allocation totals
13. validate foreign keys
14. validate ranking equivalence
15. commit
```

Failure:

```text
ROLLBACK
```

---

# 25. Relational constraints

Every SQLite connection must enable:

```sql
PRAGMA foreign_keys = ON;
```

Migration validation includes:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

Use:

```text
PRIMARY KEY
FOREIGN KEY
UNIQUE
NOT NULL
CHECK
```

where they represent genuine business constraints.

---

# 26. Indexing

Likely useful indexes:

```text
instrument(isin)

portfolio_snapshot(
    portfolio_id,
    snapshot_date
)

portfolio_holding(
    portfolio_snapshot_id
)

portfolio_holding(
    instrument_id
)

shortlist_snapshot(
    snapshot_date
)

shortlist_entry(
    shortlist_snapshot_id,
    instrument_id
)

instrument_nav_observation(
    instrument_id,
    observation_date
)

instrument_metric_observation(
    instrument_id,
    metric_id,
    observation_date
)

portfolio_metric_observation(
    portfolio_snapshot_id,
    metric_id
)
```

Validate actual benefit using:

```sql
EXPLAIN QUERY PLAN
```

Do not add indexes speculatively.

---

# 27. Historical XLS audit

Before freezing schema v3, inspect all available XLS files.

For both:

```text
modell portfóliók
shortlist
```

collect:

```text
snapshot_date
sheet
ISIN
product_name
currency
asset_class
sub_asset_class
available metrics
source_file
source_row
```

Detect schema changes over time.

Do not assume all historical workbooks share an identical structure.

---

# 28. Shortlist ingestion

The XLS files contain:

```text
shortlist
```

This is a first-class investment universe.

It is not itself a portfolio.

---

# 29. `shortlist_snapshot`

```text
shortlist_snapshot_id PK
snapshot_date
source_sheet_id FK
created_at
```

---

# 30. `shortlist_entry`

```text
shortlist_entry_id PK

shortlist_snapshot_id FK
instrument_id FK

source_row
status

UNIQUE(
    shortlist_snapshot_id,
    instrument_id
)
```

Do not duplicate stable instrument metadata here.

---

# 31. Metric definitions

Create:

```text
metric_definition
```

Suggested fields:

```text
metric_id PK
metric_code UNIQUE
name
unit
description
direction NULL
```

Examples:

```text
annualized_volatility
maximum_drawdown
return_1y
sharpe_ratio
sortino_ratio
downside_risk
unhedged_allocation

dividend_yield
dividend_growth_3y
payout_ratio
dividend_coverage
```

---

# 32. Instrument metric observations

```text
instrument_metric_observation

instrument_metric_observation_id PK

instrument_id FK
metric_id FK

observation_date
value
provenance_type

source_file_id FK NULL
```

---

# 33. Portfolio metric observations

```text
portfolio_metric_observation

portfolio_metric_observation_id PK

portfolio_snapshot_id FK
metric_id FK

value
provenance_type

observation_date NULL
calculation_version NULL
source_file_id FK NULL
```

Possible provenance:

```text
PROVIDER_REPORTED
CALCULATED
DERIVED
OBSERVED
```

Never hide provenance differences.

---

# 34. Historical NAV data

Normalize historical NAV around the canonical instrument master.

The existing schema-v3 `instrument_nav_observation` table is a simplified analytical destination
for the retained Milestone 8 integration. It is not, by itself, a complete raw-source provenance
model for future refreshes or additional providers.

```text
instrument_nav_observation

instrument_nav_observation_id PK
instrument_id FK
observation_date
nav_value REAL
currency_code
value_type
source_provider
source_identifier
provenance_reference
quality_status
source_fingerprint
imported_at

UNIQUE(
    instrument_id,
    observation_date,
    source_provider,
    value_type,
    source_identifier
)
```

Future policy-compliant NAV ingestion must additionally preserve:

* exact ISIN and exact share-class identity;
* a versioned source definition, including provider identity and explicit usage/licence metadata;
* an immutable raw-artifact manifest with content hash;
* exact request URL, request parameters and retrieval timestamp;
* an exact `Decimal` NAV value and source currency;
* provider-native observation identity and provider revision identity;
* an observation-to-import-manifest foreign key;
* append-only revision and supersession lineage;
* a deterministic source-dataset fingerprint;
* explicit duplicate and conflicting-observation detection.

Never destructively overwrite an admitted observation. Never fabricate a NAV through
interpolation, a proxy instrument, reconstructed performance percentages, nearest-date
replacement or another share class. Missing or conflicting exact-share-class evidence remains
`UNAVAILABLE`, `UNRESOLVED` or `REJECTED`.

---

# 35. Portfolio-risk calculation requirement

Never calculate portfolio volatility as:

```text
Σ(weight × individual volatility)
```

because correlations matter.

Proper constructed-portfolio calculations require:

```text
constituent NAV observations
        ↓
governed common-date alignment
        ↓
constituent return series
        ↓
weighted portfolio return series
        ↓
covariance-aware volatility
maximum drawdown
supported risk-adjusted metrics
```

If required data is unavailable:

```text
UNAVAILABLE
```

must be returned.

## 35.1 Official reference rates and temporal controls

Sharpe or Sortino ranking requires admitted official evidence for the portfolio currency:

```text
EUR → ECB €STR
USD → Federal Reserve Bank of New York SOFR
HUF → Magyar Nemzeti Bank HUFONIA
```

The calculation must preserve publication-time and decision as-of controls, official day-count
and applicability conventions, and explicit official correction/revision handling. Portfolio
return dates and benchmark dates must be aligned by a reviewed deterministic methodology.

Do not use generic forward-fill, silent holiday/calendar assumptions, zero for an unknown rate,
a central-bank policy rate, or an unofficial substitute. If a required observation, publication
state, applicability rule, revision state or methodology remains unresolved, the affected metric
is `UNAVAILABLE`.

The 20% cash sleeve does not imply a benchmark return or any other cash return. Cash-return
treatment is a separate governed policy decision and must not be inferred from the Sharpe/Sortino
reference series.

Official EUR €STR evidence is now admitted through Phase B, but admission does not prove the
future alignment or portfolio calculation contracts. SOFR and HUFONIA remain absent.

---

# 36. Current LTIA investments

Current investments become a canonical domain object.

They contain:

```text
snapshot date
LTIA account
security holdings
cash balances
```

---

# 37. Current LTIA security positions

Minimum fields:

```text
account
snapshot_date

ISIN
security_name

market_value
market_currency
```

Recommended where available:

```text
quantity
unit_price
price_currency

reporting_value
reporting_currency

observed_roi
average_acquisition_price
acquisition_cost
unrealized_profit_loss
```

Optional missing fields must not prevent allocation-level analysis.

---

# 38. Current LTIA cash

Canonical fields:

```text
account
snapshot_date
currency
balance
```

Cash has no ISIN.

---

# 39. LTIA evidence architecture

Preferred authoritative flow:

```text
George / provider source evidence
        ↓
LTIA evidence database
        ↓
identity reconciliation
        ↓
deterministic current-state projection
        ↓
CURRENT LTIA INVESTMENTS
```

---

# 40. LTIA duplicate/equivalent evidence

Original source evidence must be retained.

Do not delete source records solely because multiple snapshots represent the same visible account state.

Distinguish:

```text
SOURCE EVIDENCE
```

from:

```text
CURRENT STATE
```

Current-state projection must not double-count equivalent observations.

Use deterministic equivalence based on:

```text
account
view type
source date
evidence fingerprint
position/cash fingerprint
```

---

# 41. LTIA identity completion

Legacy LTIA evidence may temporarily contain:

```text
ISIN = NULL
```

only when identity status explicitly records:

```text
IDENTITY_UNRESOLVED
```

Unresolved securities may remain in evidence but cannot participate in automatic cross-database reconciliation.

---

# 42. CSV policy

Do not require manually created LTIA CSV files.

The existing PDF/evidence workflow remains valid.

CSV support becomes optional only if a broker export improves:

* ISIN coverage;
* quantity;
* price;
* cost basis;
* transaction detail;
* reconciliation.

Never manually transcribe PDFs into CSV solely to satisfy the application.

---

# 43. LTIA repository refactoring

Current legacy module:

```text
src/portfolio_advisor/tbsz/
```

Target domain module:

```text
src/portfolio_advisor/ltia/
```

Recommended eventual structure:

```text
src/portfolio_advisor/ltia/
    models.py
    service.py

    repository/
        schema.py
        migrations.py
        accounts.py
        instruments.py
        snapshots.py
        positions.py
        cash.py
        transactions.py
        observations.py
        provenance.py

    importers/
        george_pdf.py
        csv.py

    reconciliation/
        identity.py
        current_state.py
```

Perform the rename as a controlled compatibility migration.

Do not combine terminology renaming with unrelated financial-logic changes.

---

# 44. Investment objectives

Make investment objective a first-class domain concept.

```python
class PortfolioObjective(StrEnum):
    CAPITAL_CONSERVATION = "capital_conservation"
    DIVIDEND_PORTFOLIO = "dividend_portfolio"
```

---

# 45. Objective-neutral database

Never create:

```text
capital_conservation.sqlite
dividend.sqlite
```

The database stores facts.

Policies determine how those facts are evaluated.

---

# 46. Policy architecture

Use reviewed, versioned policy files.

Suggested:

```text
data/knowledge/validated_rules/

    capital_conservation/
        ranking_v1.0.1.yaml
        construction_v1.yaml

    dividend/
        ranking_v1.yaml
        construction_v1.yaml
```

---

# 47. Policy registry

Conceptually:

```python
objective = policy_registry.get(
    PortfolioObjective.CAPITAL_CONSERVATION
)
```

The selected objective controls:

* eligibility;
* active metrics;
* directions;
* weights;
* hard thresholds;
* construction constraints;
* finalist comparison;
* outcome success criteria.

---

# 48. Capital Conservation objective

Canonical code:

```text
CAPITAL_CONSERVATION
```

Mandate:

```text
3–12 month horizon
capital conservation first
risk-adjusted return second
```

The current:

```text
CAPITAL_PRESERVATION_RANKING_POLICY v1.0.1
```

remains the initial champion.

Never rewrite historical decisions using later policies.

---

# 49. Capital Conservation features

Initial core features remain:

```text
annualized_volatility
maximum_drawdown
return_1y
sharpe_ratio
unhedged_allocation
```

Future reviewed additions may include:

```text
downside_deviation
Sortino ratio
liquidity
cash allocation
stress loss
```

Do not add metrics merely because data exists.

---

# 50. Horizon-aware Capital Conservation

Future challenger policies:

```text
90d
180d
365d
```

Example governance:

```text
Champion:
CAPITAL_PRESERVATION_RANKING_POLICY v1.0.1

Challenger:
CAPITAL_CONSERVATION_90D

Challenger:
CAPITAL_CONSERVATION_180D

Challenger:
CAPITAL_CONSERVATION_365D
```

---

# 51. Dividend objective

Canonical code:

```text
DIVIDEND_PORTFOLIO
```

Mandate:

```text
sustainable dividend income
+
dividend quality
+
diversification
+
acceptable risk
```

Do not define this as:

```text
MAX(dividend_yield)
```

---

# 52. Dividend quality principle

Use:

```text
sustainability first
yield second
```

High headline yield may indicate:

* collapsing market price;
* unsustainable payout;
* weak earnings;
* excessive leverage;
* dividend-cut risk.

---

# 53. Dividend observations

Create ISIN-based dividend evidence:

```text
dividend_observation

dividend_observation_id PK
instrument_id FK

ex_dividend_date NULL
record_date NULL
payment_date NULL

dividend_per_share
currency_code
dividend_type

source
```

Possible dividend types:

```text
REGULAR
SPECIAL
INTERIM
FINAL
UNKNOWN
```

Special dividends must not automatically count as recurring income.

---

# 54. Dividend-related metrics

Possible future metrics:

```text
trailing_dividend_yield
forward_dividend_yield

dividend_growth_1y
dividend_growth_3y
dividend_growth_5y

dividend_consistency
dividend_cut_history

payout_ratio
free_cash_flow_payout_ratio
dividend_coverage

earnings_growth
free_cash_flow_growth

volatility
maximum_drawdown
```

Use only reliable, sufficiently complete data.

---

# 55. Dividend income concentration

Measure not only holding concentration but:

```text
dividend_income_concentration
```

Example:

```text
security weight = 12%
expected dividend-income contribution = 27%
```

This represents income concentration risk.

---

# 56. Future objective extensibility

Adding another strategy should require:

```text
new objective
+
new policy
+
possibly new metric observations
```

not:

```text
new database
+
new portfolio schema
+
new shortlist engine
+
new LTIA subsystem
```

---

# 57. Shortlist eligibility

Each objective defines its own shortlist eligibility.

Shared minimum rules may include:

```text
valid ISIN
supported instrument type
required data coverage
supported currency
```

Capital Conservation may additionally require:

```text
acceptable volatility
acceptable drawdown
acceptable downside risk
```

Dividend Portfolio may require:

```text
dividend history
sufficient dividend data
acceptable payout sustainability
acceptable dividend coverage
```

Every rejection must preserve its reason.

---

# 58. Portfolio construction engine

Suggested shared package:

```text
src/portfolio_advisor/construction/
    models.py
    constraints.py
    allocation.py
    diversification.py
    candidate_generator.py
    validation.py
    service.py
```

Generic construction infrastructure is shared.

Objective-specific policies provide the rules.

## 58.1 Approved initial `CAPITAL_DEFENSIVE` contract

`CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY` v1.0.0 governs the initial Capital Conservation
constructor. One run receives an immutable exact `Decimal` investable-cash amount and one currency
from `EUR`, `USD` or `HUF`. The private amount is validated in memory; the central analytical
database persists only the normalized candidate and must not persist or reversibly fingerprint the
user's amount.

The approved initial allocation and evidence constraints are exactly:

```text
one currency per construction run
no FX conversion in the approved initial policy
future FX remains prohibited without separately validated FX evidence and policy

cash reserve:              20% of total portfolio value
securities:                exactly 8
weight per security:       10% of total portfolio value
total security weight:     80%

minimum conflict-free asset/sub-asset groups:  3
maximum total-portfolio weight in one group:   40%

minimum admitted NAV history:                  365 calendar days
minimum common aligned return intervals:       252
maximum NAV staleness at construction:          30 calendar days
```

Only same-currency instruments with exact shortlist lineage, valid canonical ISIN, conflict-free
category evidence and admitted validated NAV may be selected. One deterministic candidate is
allowed per currency/run: select the highest-ranked feasible eight-instrument set, minimize its
ordered rank vector, and use the lexicographically ordered ISIN tuple only for an exact feasible-set
tie.

Issuer concentration remains `NOT_ENFORCED_EVIDENCE_UNAVAILABLE`; issuer identity must not be
manufactured.

Ranking-policy feature weights are not portfolio-allocation weights. Incomplete construction,
category, NAV, currency, benchmark or methodology evidence fails closed. Official currency-specific
reference-rate evidence is required before Sharpe or Sortino can participate in portfolio ranking.

The machinery is implemented, but the current production runtime is
`IMPLEMENTED_BLOCKED_BY_DATA`. EUR €STR evidence is admitted, but governed alignment and
portfolio metrics are not implemented, SOFR/HUFONIA are absent, and retained NAV is insufficient
and stale. Production contains zero constructed portfolio candidates.

---

# 59. Construction principles

Initially use deterministic and explainable methods.

Do not generate thousands of opaque combinations.

For Capital Conservation start with:

```text
CAPITAL_DEFENSIVE
```

The approved initial behavior is the fixed contract in Section 58.1. Additional strategies require
separate reviewed policy versions and must not silently change that contract.

Then optionally:

```text
CAPITAL_MIN_RISK
CAPITAL_RISK_ADJUSTED
```

For Dividend Portfolio start with:

```text
DIVIDEND_QUALITY
```

Then optionally:

```text
DIVIDEND_GROWTH
DIVIDEND_INCOME
```

---

# 60. Cash-aware construction

The constructor receives:

```text
eligible ISIN universe
+
per-run cash request by currency
+
objective policy
+
construction constraints
```

For the approved initial contract, that request contains exactly one positive supported-currency
amount. Zero, negative, multiple-currency, unsupported-currency and binary floating-point amounts
are rejected.

Do not assume 100% investment.

The approved initial Capital Conservation strategy retains exactly 20% as a cash reserve and
allocates exactly 80% to eight 10% security holdings. Its persisted normalized representation uses:

```text
portfolio_cash.weight = 0.20
portfolio_cash.amount = NULL
```

Transaction units, order quantities and brokerage rounding remain outside construction-policy
scope.

Dividend strategy may retain cash for:

```text
reserve
allocation rounding
insufficient eligible instruments
future deployment
```

---

# 61. Currency conversion

Do not silently convert:

```text
EUR cash
→
USD instrument
```

without validated FX data.

The initial `CAPITAL_DEFENSIVE` policy is stricter: one run uses one currency and selects only
instruments denominated in that same currency. No fallback currency or implicit conversion is
permitted.

Future table:

```text
fx_rate_observation

base_currency
quote_currency
observation_date
rate
source
```

Every FX conversion preserves:

```text
rate
date
source
```

---

# 62. Constructed portfolios

Constructed shortlist candidates use the same relational structure as model portfolios:

```text
portfolio
portfolio_snapshot
portfolio_holding
portfolio_cash
```

with:

```text
portfolio_type = SHORTLIST_CONSTRUCTED
```

Milestone 11B implements this normalized domain and persistence foundation, but the production
tables contain no such portfolios while the governed data prerequisites remain unsatisfied.

---

# 63. Construction lineage

The installed Milestone 11B feature provides:

```text
constructed_portfolio_metadata

portfolio_snapshot_id PK/FK
shortlist_snapshot_id FK

objective_code
construction_policy_id
construction_policy_version
construction_policy_fingerprint

construction_strategy
cash_currency
portfolio_identity_fingerprint
eligible_universe_fingerprint
selected_universe_fingerprint
candidate_fingerprint
deterministic_provenance_json
created_at

constructed_portfolio_holding_lineage
    portfolio_holding_id PK/FK
    shortlist_entry_id FK
    selected_instrument_rank
    allocation_basis
    allocation_weight_decimal
    constraint_evidence_fingerprint
```

Every constructed candidate must be reproducible through the exact shortlist membership and its
existing source-occurrence lineage. No source occurrence is duplicated merely to support a
constructed holding.

---

# 64. Candidate abstraction

Downstream systems should not care whether a candidate originated as:

```text
MODEL
```

or:

```text
SHORTLIST_CONSTRUCTED
```

Both become:

```text
PortfolioCandidate
```

identified primarily by:

```text
portfolio_snapshot_id
```

---

# 65. Model finalist

For the selected objective:

```text
model portfolio universe
        ↓
objective eligibility
        ↓
objective ranking
        ↓
BEST MODEL PORTFOLIO
```

This becomes:

```text
MODEL_FINALIST
```

---

# 66. Shortlist finalist

For the same objective:

```text
shortlist
        ↓
objective eligibility
        ↓
portfolio construction
        ↓
constructed candidates
        ↓
objective ranking
        ↓
BEST SHORTLIST PORTFOLIO
```

This becomes:

```text
SHORTLIST_FINALIST
```

It is valid only when the selected candidate is a real persisted
`SHORTLIST_CONSTRUCTED` portfolio snapshot. A singleton ranked instrument cannot satisfy this
role.

---

# 67. Two-finalist architecture

```text
all model portfolios
        ↓
BEST MODEL


shortlist
        ↓
constructed candidates
        ↓
BEST SHORTLIST


BEST MODEL
     vs
BEST SHORTLIST
        ↓
FINAL COMPARISON
```

This must remain explicit and auditable.

Roadmap-compliant final comparison cannot begin until both branches produce portfolio finalists.

---

# 68. Final comparison

Both finalists compete under the same selected investment objective.

Both finalists must represent portfolios, not a portfolio on one side and an instrument on the
other.

Neither receives preferential treatment because of its source.

Only semantically comparable evidence may enter direct comparison.

For example:

```text
PROVIDER_REPORTED volatility
```

versus:

```text
CALCULATED volatility
```

is acceptable only if methodology, horizon and definition are compatible.

Metric provenance must remain visible.

Horizons, observation cutoffs, metric definitions, calculation methodologies and provenance must
be semantically compatible. When comparability is not proven, the workflow must abstain rather
than recommend.

---

# 69. Final eligibility gate

A finalist must still pass all hard objective constraints.

Example:

```text
Shortlist score = 90
Maximum drawdown threshold = FAIL

→ INELIGIBLE
```

The correct flow is:

```text
eligible finalists
        ↓
comparison
        ↓
recommendations
```

not simply:

```text
maximum score
```

---

# 70. Recommendation versus user choice

Store separately:

```text
SYSTEM_RECOMMENDED
```

and:

```text
USER_SELECTED
```

The user may select a lower-ranked recommendation.

That distinction must remain explicit.

---

# 71. Decision run

Suggested:

```text
decision_run

decision_run_id PK

decision_date
investment_objective_id
policy_bundle_version

model_snapshot_date
shortlist_snapshot_id NULL

created_at
```

---

# 72. Portfolio recommendations

```text
portfolio_recommendation

recommendation_id PK
decision_run_id FK

portfolio_snapshot_id FK
candidate_type

recommended_rank
score
eligibility_status

created_at
```

A decision run may contain multiple recommendations.

---

# 73. User selection

Optional persistence:

```text
portfolio_selection

selection_id PK
decision_run_id FK
portfolio_snapshot_id FK

selection_source
selected_at
```

Possible:

```text
USER_SELECTED
SYSTEM_TOP_RANKED
```

Default investment workflow:

```text
USER_SELECTED
```

---

# 74. Current-versus-target comparison

After user selection:

```text
selected target
        ↓
compare
        ↓
current LTIA investments
```

Security matching:

```text
ISIN
```

Cash matching:

```text
currency
```

---

# 75. Consolidated and account-level LTIA views

Support both:

```text
CONSOLIDATED CURRENT LTIA PORTFOLIO
```

and:

```text
ACCOUNT-LEVEL LTIA PORTFOLIO
```

Consolidated analysis may aggregate identical ISINs.

Account-level provenance must never be lost.

---

# 76. LTIA account constraints

Do not assume securities or cash can move freely between LTIA accounts.

Rebalancing must distinguish:

```text
desired consolidated allocation
```

from:

```text
account-aware implementation plan
```

---

# 77. LTIA rebalancing advisor

Allowed advisory actions:

```text
HOLD
INCREASE
REDUCE
REBALANCE
```

No brokerage execution.

Initial focus:

```text
allocation gaps
```

rather than exact order execution.

---

# 78. Use available cash first

Rebalancing should distinguish:

```text
allocate free cash
```

from:

```text
sell existing holdings
```

Preferred logic:

```text
1. preserve required cash reserve
2. allocate available free cash
3. calculate remaining target gaps
4. recommend sales only when justified
```

subject to:

* account boundaries;
* selected objective;
* currency constraints.

---

# 79. Rebalancing optimization

A future deterministic rebalancer may minimize:

```text
target deviation
+
unnecessary turnover
+
currency conversion
```

while respecting:

```text
LTIA account boundaries
cash constraints
currency constraints
holding constraints
objective constraints
```

Portfolio selection and rebalancing remain separate subsystems.

---

# 80. Outcome & Performance Engine

After relational identity, shortlist and recommendation foundations are stable, build:

```text
src/portfolio_advisor/performance/
    models.py
    observed_returns.py
    realized_returns.py
    benchmark.py
    attribution.py
    service.py
```

---

# 81. Capital Conservation outcomes

Track:

```text
90 days
180 days
365 days
```

Possible metrics:

```text
forward return
realized volatility
maximum drawdown
downside deviation
benchmark return
benchmark drawdown
excess return
drawdown saved
```

Keep:

```text
capital_preservation_success
```

separate from:

```text
return_success
```

---

# 82. Dividend outcomes

Track:

```text
realized dividend income
realized dividend yield
dividend increases
dividend cuts
income stability
income shortfall
total return
maximum drawdown
```

Dividend portfolios must not be evaluated solely by capital-conservation criteria.

---

# 83. Outcome states

Use explicit states:

```text
PENDING
AVAILABLE
ADMITTED
REJECTED
UNRESOLVED
```

Future observations must never contaminate decision-time features.

---

# 84. Outcome provenance

Persist:

```text
decision date
portfolio snapshot
objective
policy version
observation date
horizon
source provenance
admission status
rejection reason
calculation version
```

---

# 85. Alternative finalist tracking

For every prospective decision retain:

```text
selected finalist
alternative finalist
```

This allows later evaluation of:

```text
Would the other finalist have performed better?
```

---

# 86. Shortlist construction value-add

Measure whether shortlist construction provides value over simply selecting the best model portfolio.

Track independently:

```text
return value-add
```

and:

```text
drawdown value-add
```

Do not collapse them prematurely into one metric.

---

# 87. Stress & Scenario Engine

Suggested:

```text
src/portfolio_advisor/scenarios/
    models.py
    exposures.py
    scenario.py
    stress.py
    service.py
```

Initial scenarios:

```text
global equity shock
European equity shock

EUR rates +100 bp
USD rates +100 bp

risk-off

EUR/HUF shock
USD/EUR shock
```

Stress testing validates ranking; it does not replace it.

---

# 88. Stress architecture

```text
ranking
    ↓
finalists
    ↓
stress analysis
    ↓
objective validation
```

Flag candidates that violate approved stress thresholds.

---

# 89. Portfolio diagnostics

Suggested:

```text
src/portfolio_advisor/diagnostics/
    portfolio.py
    confidence.py
    warnings.py
```

Possible diagnostics:

```text
concentration risk
asset-class concentration
currency risk
drawdown risk
volatility risk
allocation quality
metric coverage
source staleness
provenance completeness
historical evidence quality
```

Constructed portfolios additionally expose:

```text
construction policy
eligible universe size
selected positions
binding constraints
metric provenance
```

---

# 90. Evidence confidence

Evidence confidence remains separate from portfolio score.

Possible dimensions:

```text
identity certainty
metric completeness
allocation completeness
source provenance
historical evidence
constituent resolvability
data staleness
outcome availability
```

Example:

```text
Quantitative metrics     100%
Identity coverage        100%
Allocation coverage      100%
Historical evidence       87%
Forward evidence       pending

Evidence class: B
```

Do not modify ranking based on confidence unless an approved policy explicitly requires it.

---

# 91. Decision-quality analytics

Once enough admitted outcomes exist, measure:

```text
top-1 success rate
top-3 success rate
drawdown success rate
candidate-median comparison

Spearman rank correlation

return regret
drawdown regret

champion-vs-challenger performance
```

Suggested:

```text
src/portfolio_advisor/validation/
    decision_quality.py
    rank_quality.py
    calibration.py
    policy_comparison.py
```

---

# 92. Champion / Challenger governance

Each objective maintains independent governance.

Capital Conservation:

```text
Champion:
CAPITAL_PRESERVATION_RANKING_POLICY v1.0.1

Challengers:
90d
180d
365d
future alternatives
```

Dividend:

```text
Champion:
DIVIDEND_QUALITY_POLICY v1

Future challengers:
DIVIDEND_GROWTH
DIVIDEND_INCOME
```

Never compare different objectives as if they solve the same optimization problem.

---

# 93. Policy promotion

A challenger may become champion only after sufficient admitted evidence demonstrates superior decision quality.

Promotion must be explicit.

Never rewrite historical decisions using a newer policy.

Store policy/version with every decision.

---

# 94. Reporting

Before implementing a dashboard, create:

```text
src/portfolio_advisor/reporting/
    models.py
    json_report.py
    html_report.py
```

---

# 95. `AnalysisReport`

Suggested contents:

```text
decision metadata
investment objective
policy/version

model finalist
shortlist finalist
recommendations

eligibility
ranking
diagnostics
stress results
evidence confidence

selected target
current LTIA comparison
allocation gaps

prospective outcome state
methodology references
```

Output order:

```text
JSON first
HTML second
UI later
```

Business logic must remain outside presentation code.

---

# 96. UI target

Initial interface:

```text
Select investment objective:

[ Capital conservation — 3–12 months ]
[ Best dividend portfolio ]
```

Future objectives may be added through the same interface.

---

# 97. Features to postpone

Do not prioritize yet:

* XGBoost portfolio selection;
* neural-network ranking;
* LLM-generated return forecasts;
* LLM-controlled ranking;
* automated brokerage execution;
* unconstrained portfolio optimization;
* large collections of unused metrics;
* complex UI before domain/reporting stability.

Machine-learning models may later become challenger models once sufficient clean labeled outcomes exist.

---

# 98. Revised implementation roadmap

## Milestone 4 — Current Data Audit & ISIN Foundation

Deliver:

```text
DB inventory
XLS inventory
ISIN coverage report
duplicate report
data-quality report
canonical instrument design
```

Inspect:

```text
model_portfolio.sqlite
official_historical_nav.sqlite
prospective_portfolio_validation.sqlite
tbsz_portfolio.sqlite
tbsz_current_portfolio.sqlite

historical XLS:
modell portfóliók
shortlist
```

---

## Milestone 5 — Schema v3 Relational Foundation

Create:

```text
instrument
instrument_alias

source_file
source_sheet

portfolio
portfolio_snapshot
portfolio_holding
portfolio_cash

metric_definition
instrument_metric_observation
portfolio_metric_observation
```

Keep private LTIA data physically separate.

---

## Milestone 6 — LTIA Identity & Current-State Reconciliation

Deliver:

```text
LTIA ISIN reconciliation
duplicate/equivalent snapshot handling
canonical current-investments projection
cash by account/currency
consolidated + account views
```

No manual CSV requirement unless future brokerage exports provide materially better evidence.

---

## Milestone 7 — Model Portfolio Relational Migration

Migrate the flat model portfolio database.

Prove:

```text
old ranking == new ranking
```

under unchanged policies.

Do not decommission the legacy DB until equivalence is proven.

---

## Milestone 8 — Historical NAV Integration

Resolve NAV security identity through the canonical instrument master.

Maintain existing strict provenance rules.

No synthetic portfolio NAV reconstruction.

---

## Milestone 9 — Shortlist Relational Import

Create:

```text
shortlist_snapshot
shortlist_entry
instrument_metric_observation
```

Import historical `shortlist` worksheets.

Detect source-schema changes explicitly.

---

## Milestone 10 — Objective Framework

Implement:

```text
PortfolioObjective
InvestmentPolicy
PolicyRegistry
```

Initial objectives:

```text
CAPITAL_CONSERVATION
DIVIDEND_PORTFOLIO
```

Database remains objective-neutral.

---

## Milestone 11 — Capital Conservation constructed-portfolio pipeline

The original goal remains a real, governed `CAPITAL_DEFENSIVE` shortlist portfolio and finalist.
Implementation is divided into traceable checkpoints:

```text
reason:        implementation and source-readiness evidence exposed separate policy, schema,
               benchmark-ingestion, NAV-provenance and portfolio-metric gates
prerequisite:  retain completed 11A, 11B and prior 11C contracts and forward history
replacement:   replace the former single broad Milestone 11 step with checkpoints 11A, 11B
               and 11C Phases A–F below
current status: overall Milestone 11 is BLOCKED_BY_DATA
```

| Checkpoint | State | Scope |
| --- | --- | --- |
| Milestone 11A — construction-policy approval | `COMPLETED` | Reviewed immutable construction contract; commit `ea3406c2df38a3b7a274c7507cb174950149b7d3`. |
| Milestone 11B — construction domain and schema | `COMPLETED` | Deterministic allocation engine, normalized domain, lineage and idempotent persistence foundation; commit `0aa7cf12a7ef5bead07b0753b2229909fb8e2373`. |
| Milestone 11C Phase A — reference-rate schema foundation | `COMPLETED` | Empty official-rate evidence schema, contracts, copy-on-write migration and read-only validator; commit `37d9e0c2a9f57de1837a8d7a43aab54f9ef38772`. |
| Milestone 11C Phase B — ECB €STR adapter | `COMPLETED` | Official EUR history, immutable raw/receipt provenance, exact offline import, idempotency and read-only validation. |
| Milestone 11C Phase C — NY Fed SOFR adapter | `PLANNED` — next | Admit official USD benchmark evidence. |
| Milestone 11C Phase D — MNB HUFONIA adapter | `PLANNED` | Admit official HUF benchmark evidence. |
| Milestone 11C Phase E — NAV provenance upgrade and EUR/HUF refresh | `PLANNED` | Add complete raw-source/manifests/revision provenance and refresh the smallest feasible exact-share-class universes. |
| Milestone 11C Phase F — aligned portfolio analytics and real finalist | `PLANNED` | Governed aligned returns, covariance-aware portfolio metrics, runtime construction, real-candidate persistence, ranking and `SHORTLIST_FINALIST` selection. |

HUFONIA runtime admission remains conditional on resolving and encoding its official publication,
revision, holiday-applicability and missing-value semantics. A schema row or downloaded series is
not sufficient by itself.

USD shortlist construction remains blocked until at least eight exact-share-class histories in one
USD construction universe have the required admitted history, staleness and common aligned
coverage.

Overall roadmap-compliant Milestone 11 remains `BLOCKED_BY_DATA`. The earlier singleton-ranking
workflow is useful intermediate screening, not completion of this milestone.

---

## Milestone 12 — Capital Conservation portfolio-to-portfolio end-to-end

Roadmap-compliant Milestone 12 may begin only after Milestone 11 produces and persists a real:

```text
portfolio_type = SHORTLIST_CONSTRUCTED
```

portfolio snapshot selected as:

```text
SHORTLIST_FINALIST
```

The final workflow is:

```text
MODEL_FINALIST
        vs
SHORTLIST_FINALIST
        ↓
recommendations
        ↓
user choice
```

Both finalists must be portfolios evaluated through semantically compatible metrics, horizons,
methodologies, as-of dates and provenance. If compatibility is unresolved, the system abstains; it
does not manufacture a recommendation.

Store the system recommendation separately from explicit user choice. A user may select a
non-top-ranked option, defer or decline. The historical commit `73efe92` remains exploratory
model-versus-instrument infrastructure and does not complete this milestone.

Milestone 12 remains `BLOCKED_BY_DATA` / `NO-GO` until the Milestone 11 prerequisite is satisfied.

---

## Milestone 13 — Dividend Data Foundation

Add reliable:

```text
dividend observations
fundamental observations
```

by ISIN.

Do not create dividend ranking before evidence quality is sufficient.

---

## Milestone 14 — Dividend Policy & Constructor

Implement:

```text
DIVIDEND_QUALITY_POLICY v1
```

and:

```text
DIVIDEND_QUALITY
```

shortlist construction.

Sustainability takes precedence over maximum headline yield.

---

## Milestone 15 — Dividend End-to-End

Complete:

```text
best dividend model portfolio
vs
best dividend shortlist portfolio
        ↓
recommendations
        ↓
user choice
```

---

## Milestone 16 — LTIA Current-vs-Target Rebalancing

Compare:

```text
selected target holdings
vs
current LTIA holdings
```

by ISIN.

Compare cash independently by currency.

Produce:

```text
HOLD
INCREASE
REDUCE
REBALANCE
```

with account-aware constraints.

---

## Milestone 17 — Stress & Diagnostics

Apply deterministic stress analysis and portfolio diagnostics to model and shortlist finalists.

---

## Milestone 18 — Outcome & Performance Engine

Implement objective-specific prospective outcome tracking.

Capital Conservation:

```text
90d
180d
365d
```

Dividend:

```text
income delivery
dividend changes
total return
risk
```

---

## Milestone 19 — Decision Quality & Champion/Challenger

Evaluate:

```text
ranking policy quality
construction policy quality
shortlist value-add
model-vs-shortlist decision quality
```

using admitted outcomes.

---

## Milestone 20 — Reporting & UI

Implement:

```text
AnalysisReport
JSON
HTML
```

before introducing a full dashboard.

---

# 99. Terminology migration milestone

The legacy implementation currently uses:

```text
tbsz
```

in:

* source directories;
* Python packages;
* database filenames;
* table names;
* classes;
* tests;
* configuration.

Introduce a controlled terminology migration:

```text
TBSZ
→
LTIA
```

Target:

```text
src/portfolio_advisor/ltia/

database/ltia_portfolio.sqlite
```

Perform this migration only after the relational design is stable.

Migration requirements:

```text
1. no financial semantic changes
2. no data loss
3. compatibility migration
4. updated tests
5. updated documentation
6. updated operational scripts
7. explicit schema/version handling
```

Do not combine terminology migration with unrelated ranking or construction changes.

---

# 100. Immediate next implementation milestone

The next engineering milestone is:

```text
MILESTONE 11C PHASE C
NEW YORK FED SOFR ADAPTER
```

Phase B is completed forward history. Phase C may begin only as a bounded official-USD
reference-rate ingestion path. Its scope is:

```text
A. official Federal Reserve Bank of New York endpoint only

B. exact SOFR benchmark and series identity

C. immutable raw-response retention with SHA-256

D. exact request URL, parameters and retrieval timestamp

E. deterministic parser and exact Decimal values

F. reference-rate definition, source, import-manifest and observation persistence

G. separate observation-date and publication-date provenance

H. official revision, supersession and governed as-of controls

I. deterministic contract and dataset fingerprints

J. copy-on-write database candidate

K. complete source, schema, integrity, foreign-key and logical-preservation validation
   before atomic installation
```

This checkpoint must not change NAV evidence, calculate portfolio metrics, activate Sharpe or
Sortino, construct a production portfolio, or authorize production cutover. It must preserve the
completed ECB evidence unchanged and apply the same candidate-first, fail-closed release boundary.

---

# 101. Final architectural target

```text
                 FINANCIAL DATA PLATFORM
                          │
        Securities → canonical ISIN
        Cash       → currency + amount
                          │
                          ▼
                 SELECT OBJECTIVE
                          │
             ┌────────────┴────────────┐
             ▼                         ▼
     model portfolios               shortlist
             │                         │
             ▼                         ▼
        evaluation                 construction
             │                         │
             ▼                         ▼
      MODEL FINALIST           SHORTLIST FINALIST
             │                         │
             └────────────┬────────────┘
                          ▼
                   final comparison
                          │
                          ▼
                    recommendations
                          │
                          ▼
                       USER CHOICE
                          │
                          ▼
                   current LTIA state
                          │
                          ▼
                  allocation/rebalancing
                          │
                          ▼
                   prospective outcomes
                          │
                          ▼
                   methodology validation
```

The central architectural rule is:

```text
DATABASE MODEL ≠ INVESTMENT STRATEGY
```

The database describes:

```text
facts
identity
history
provenance
```

Investment objectives and policies determine:

```text
eligibility
construction
ranking
recommendation
success criteria
```

This separation allows Portfolio Advisor to evolve from the initial:

```text
CAPITAL_CONSERVATION
DIVIDEND_PORTFOLIO
```

objectives into a broader portfolio advisory platform without repeated database redesign.
