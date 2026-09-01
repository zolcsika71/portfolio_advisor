# Milestone 11B — Constructed-portfolio domain and schema foundation

Milestone 11B implements the reviewed construction machinery for
`CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY` v1.0.0 without claiming a production shortlist
portfolio. The policy fingerprint remains
`a5dc75f07eac4e0ab615f1669a95f7eecdbb3f0e31e1c6bb174dd000097ccbbf`.
The production runtime state is `IMPLEMENTED_BLOCKED_BY_DATA`; production cutover is
`NOT_AUTHORIZED`.

## Domain and normalized allocation

`construct_capital_defensive_portfolio(...)` accepts the reviewed ranked instrument-screening
result, an exact one-currency `Decimal` cash request, the approved policy, exact shortlist and
NAV lineage, and explicit benchmark/metric readiness. A successful synthetic run produces one
immutable candidate with eight unique same-currency holdings at exact 10% weights and a separate
20% cash reserve. The result contains no transaction quantity, brokerage rounding, order size,
private account identity, or user cash amount.

The private amount is validated in memory and discarded. Persistence uses
`portfolio_cash.weight = 0.20` and `portfolio_cash.amount = NULL`; candidate and portfolio
fingerprints cannot reveal the amount.

## Deterministic selection and hard constraints

The generator sorts candidates by reviewed rank and ISIN and performs include-first depth-first
feasibility search with remaining-capacity, category, and common-date pruning. The first complete
set therefore minimizes the ordered rank vector; equal rank vectors use the lexicographically
ordered ISIN tuple. It does not randomize, retain exhaustive combinations, use ranking feature
weights as allocations, substitute currencies, or perform FX.

Every selected instrument must have a valid canonical ISIN, the exact source shortlist membership,
conflict-free asset/sub-asset evidence, admitted validated NAV, at least 365 calendar days of
history, at least 252 aligned return intervals, and no more than 30 days of staleness. The eight
instruments must share a qualifying aligned date window. Interpolation, nearest-date substitution,
and proxies are rejected. At least three groups are required and no group may contain more than
four holdings. Issuer concentration remains `NOT_ENFORCED_EVIDENCE_UNAVAILABLE`.

## Additive schema and lineage

Schema v3 gains the deterministic feature marker
`MILESTONE_11B_CONSTRUCTED_PORTFOLIO` revision 1 and two tables:

- `constructed_portfolio_metadata` binds a normal `SHORTLIST_CONSTRUCTED` portfolio snapshot to
  the exact shortlist snapshot, objective, strategy, policy identity/fingerprint, cash currency,
  portfolio/eligible/selected/candidate fingerprints, status, and canonical provenance;
- `constructed_portfolio_holding_lineage` binds each normal `portfolio_holding` to the exact
  `shortlist_entry`, selected rank, fixed-weight allocation basis, exact `0.10` decimal contract,
  and constraint-evidence fingerprint.

Existing shortlist membership lineage keeps every source occurrence reachable; source occurrences
are not duplicated. Cash remains in `portfolio_cash` and never receives an ISIN. The feature uses
genuine foreign keys, restricts source deletion, and cascades holding-lineage deletion only when its
own derived holding is deleted.

The migration revision is `MILESTONE_11B_CONSTRUCTED_PORTFOLIO_SCHEMA_V1`. It copies the installed
derived database to an explicitly named disposable target, installs the feature transactionally,
proves every pre-existing table value unchanged, compares the migrated feature contract with a
full from-scratch schema-v3 build, and validates integrity and foreign keys before publication.

## Identity, persistence, and idempotency

Portfolio identity covers objective, strategy, cash currency, and construction-policy ID/version.
Candidate identity additionally covers stable shortlist source identity, policy fingerprint,
eligible- and selected-universe fingerprints, normalized holdings and exact weights, category and
constraint fingerprints, and cash weight/currency. It excludes timestamps, private cash, and
database-local row IDs.

Persistence inserts portfolio identity, snapshot, holdings, cash, metadata, and holding lineage in
one transaction. An identical rerun reuses the candidate. A different payload under the same
portfolio/snapshot identity fails closed; injected failures roll back all rows. Validation occurs
before commit and rechecks the complete persisted candidate, 80/20 reconciliation, provenance,
candidate fingerprints, SQLite integrity, and foreign keys.

## Current production blocked state

The retained target has 384 reviewed eligible screened instruments but only 16 with admitted NAV.
The latest admitted observations end on 2026-06-30, which is stale for the 2026-08-26 construction
date. Milestone 11C Phase B subsequently admits official EUR €STR observations, but does not
change the runtime readiness flags: benchmark alignment and portfolio metrics remain
unimplemented, while SOFR and HUFONIA remain absent. The read-only attempt therefore still
returns:

```text
IMPLEMENTED_BLOCKED_BY_DATA

INSUFFICIENT_ADMITTED_NAV_COVERAGE
STALE_NAV
MISSING_OFFICIAL_REFERENCE_RATE_EVIDENCE
UNAVAILABLE_PORTFOLIO_RISK_METRICS
```

It creates zero constructed portfolios, snapshots, holdings, cash rows, metadata rows, or lineage
rows. The Milestone 11B checkpoint changed no policy, NAV, reference-rate, source, LTIA, Graphify,
prospective, or workbook evidence. Phase B later changed only the derived reference-rate evidence
tables and still leaves all production constructed-portfolio counts at zero.

## Validation and remaining boundary

`scripts/validate_milestone_11b_constructed_portfolio.py` supports an explicit database target,
validates the schema contract and all persisted synthetic candidates read-only, detects missing or
tampered rows, verifies 80/20 and lineage, and emits deterministic privacy-safe JSON. The historical
Milestone 11 instrument-screening and Milestone 12 model-versus-instrument APIs remain deprecated
and import-compatible; neither is advertised as roadmap-complete construction or comparison.

Milestone 11C still requires current admitted constituent NAV, SOFR and HUFONIA ingestion,
governed benchmark alignment, aligned portfolio return series, portfolio volatility, maximum
drawdown, supported risk-adjusted metrics, and shortlist-portfolio ranking. Roadmap Milestones 11
and 12, Milestone 13, and production cutover remain NO-GO.
