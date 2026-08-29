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

This becomes the objective-neutral analytical database.

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

```text
instrument_nav_observation

instrument_id FK
observation_date
nav
currency_code
source
quality_status

PRIMARY KEY(
    instrument_id,
    observation_date,
    source
)
```

---

# 35. Portfolio-risk calculation requirement

Never calculate portfolio volatility as:

```text
Σ(weight × individual volatility)
```

because correlations matter.

Proper constructed-portfolio calculations require:

```text
ISIN historical NAV/price series
        ↓
aligned return series
        ↓
portfolio return series
        ↓
covariance
        ↓
volatility
maximum drawdown
Sharpe
Sortino
```

If required data is unavailable:

```text
UNAVAILABLE
```

must be returned.

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

---

# 59. Construction principles

Initially use deterministic and explainable methods.

Do not generate thousands of opaque combinations.

For Capital Conservation start with:

```text
CAPITAL_DEFENSIVE
```

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
available cash by currency
+
objective policy
+
construction constraints
```

Do not assume 100% investment.

Capital Conservation may deliberately retain cash.

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

---

# 63. Construction lineage

Create:

```text
constructed_portfolio_metadata

portfolio_snapshot_id PK/FK
shortlist_snapshot_id FK

investment_objective_id
construction_policy_id

construction_strategy
eligible_universe_hash

created_at
```

Every constructed candidate must be reproducible.

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

---

# 68. Final comparison

Both finalists compete under the same selected investment objective.

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

## Milestone 11 — Capital Conservation Shortlist Constructor

Implement first deterministic strategy:

```text
CAPITAL_DEFENSIVE
```

Use:

```text
eligible ISIN universe
+
cash constraints
+
capital-conservation policy
```

Persist output as a normal portfolio snapshot.

---

## Milestone 12 — Capital Conservation End-to-End

Complete:

```text
best model portfolio
vs
best shortlist portfolio
        ↓
recommendations
        ↓
user choice
```

This becomes the first complete reference workflow.

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
MILESTONE 4
CURRENT DATA AUDIT & ISIN FOUNDATION
```

Immediate deliverables:

```text
A. full database inventory

B. historical XLS / shortlist schema audit

C. canonical ISIN instrument registry specification

D. instrument alias specification

E. LTIA ISIN reconciliation report

F. LTIA duplicate/equivalent-source report

G. canonical current-investments projection design

H. schema-v3 ERD

I. schema-v3 migration specification

J. legacy-vs-relational equivalence test plan
```

Do not begin complex shortlist portfolio construction before these foundations are complete.

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
