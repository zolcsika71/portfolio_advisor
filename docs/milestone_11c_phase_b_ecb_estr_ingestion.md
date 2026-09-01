# Milestone 11C Phase B — official ECB €STR evidence

Milestone 11C Phase B admits one official, immutable ECB €STR history response
into the Phase A reference-rate evidence contract. Acquisition, offline import,
and validation are separate. Only the acquisition command may use the network;
tests, repeat imports, candidate construction, and validation are offline.

This checkpoint admits EUR benchmark evidence only. It does not implement
portfolio-date alignment, compounding calculations, cash-sleeve return policy,
Sharpe, Sortino, portfolio construction, SOFR, HUFONIA, NAV repair, trading, or
production cutover.

## Reviewed official identity

```text
administrator:       European Central Bank
benchmark:           €STR
benchmark ID:        ESTR
dataflow:            ECB:EST(1.0)
data structure:      ECB:ECB_EST1(1.0)
series key:          B.EU000A2X2A25.WT
full series identity: EST.B.EU000A2X2A25.WT
benchmark ISIN:      EU000A2X2A25
frequency:           business-daily (B)
data type:           volume-weighted trimmed mean (WT)
unit:                percent per annum (PC)
unit multiplier:     0
published decimals:  3
day count:           ACT/360
daily accrual:       simple overnight (`SIMPLE_ACT_360_OVERNIGHT`)
```

Official references:

- [ECB €STR page](https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/euro_short-term_rate/html/index.en.html)
- [ECB Data Portal series](https://data.ecb.europa.eu/data/datasets/EST/EST.B.EU000A2X2A25.WT)
- [ECB €STR methodology and policies](https://www.ecb.europa.eu/stats/euro-short-term-rates/interest_rate_benchmarks/WG_euro_risk-free_rates/shared/pdf/ecb.ESTER_methodology_and_policies.en.pdf)
- [ECB compounded €STR calculation rules](https://www.ecb.europa.eu/stats/euro-short-term-rates/interest_rate_benchmarks/WG_euro_risk-free_rates/shared/pdf/ecb.Compounded_euro_short-term_rate_calculation_rules.en.pdf)
- [ECB website copyright and reuse terms](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html)

The fixed machine endpoint is:

```text
https://data-api.ecb.europa.eu/service/data/ECB,EST,1.0/B.EU000A2X2A25.WT
```

with canonical parameters:

```text
detail=full
format=csvdata
includeHistory=true
```

`TIME_PERIOD` is the benchmark observation date. The provider's timezone-aware
`VALID_FROM` timestamp is retained as provider revision identity and its date
is the distinct publication-availability date. `VALID_TO` identifies a
superseded provider version. Missing or malformed values are rejected; dates
are never inferred, filled, interpolated, or replaced.

The daily €STR series is not itself a compounded index. Its official accrual
factor is simple ACT/360 for the calendar days to which that business-day rate
applies. Any later multiplication of daily factors is a separate governed
portfolio-alignment/compounding implementation and is not part of Phase B.

## Retained evidence and deterministic identities

The single bounded HTTP 200 response was retained under
`data/raw/reference_rates/ecb/estr/` as local ignored evidence:

```text
retrieved UTC:      2026-09-01T20:48:46+00:00
content type:       text/csv
Last-Modified:      Tue, 01 Sep 2026 06:05:24 GMT
byte count:         618,988
raw SHA-256:        e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8
receipt file SHA:   461e6f518c079b70ebbeaff917d2621904b2efe40aafc2b035b28d8237333abf
receipt fingerprint:a180e89e9806d1e307d5dd6590d6c9844d27f4593961a2f1f6d405aeb5561f9c
```

The admitted semantic dataset contains 1,771 dates and 1,771 provider
versions, from `2019-10-01` through `2026-08-31`. Every retained row has
observation status `A`. This response exposes no superseded historical version;
the equality of date and version counts is evidence about this artifact, not a
claim that ECB values can never be corrected.

```text
dataset fingerprint:    99a1a2ff837688bb78fd0b81cbef1ef64f27f1cab36cc2acdb0ded5026cc534e
definition fingerprint: 7eae4e19d31ef73f4fc16683c369c4f0b82708390776ddcccd4eb23c033d5926
source fingerprint:     f4103515ef8b7fe3b789ae49922be0a85b4317efe572914672186eddf76d479d
manifest fingerprint:   dcf37a54eff7001f83cdddb0fbc4689383d744de8e634e008d396c59d92a3772
```

The parser requires the exact 32-column ECB history response and validates the
governed identity, unit, observation-status, and revision metadata on every
row. It also requires strict UTF-8, exact dates, exact `Decimal` rates, one
unique current version per observation date, and a contiguous provider
revision chain. It validates the complete artifact before a write transaction.

## Persistence, idempotency, and installation

Phase B populates only:

```text
reference_rate_definition:       1
reference_rate_source:           1
reference_rate_import_manifest:  1
reference_rate_observation:      1,771
```

The installed Phase A schema feature remains unchanged:

```text
feature ID:                    MILESTONE_11C_REFERENCE_RATE_EVIDENCE
feature revision:              1
feature fingerprint:           aace6e9cf4b33fbf9cad503a987b945ebb44e1db5673381fd47cbd57716d921b
schema-contract fingerprint:   1d9cb07e1bee4bed81ebe6a58a293ea544249498f736899069452ae167b59d61
Phase B migration revision:    MILESTONE_11C_PHASE_B_ECB_ESTR_V1
pre-Phase-B database SHA-256:  1a597b5cf799294e0341df786ff6219e24caca4128172c45b341c243ff6b3be9
Phase-B database SHA-256:      19e6efdadfd44235408cb046d6af38b593cf88a76dfc7ef1f815aef8769ae5d2
```

Candidate construction uses SQLite backup semantics, preserves the exact
pre-reference-rate logical fingerprint, validates the schema contract,
integrity and foreign keys, and requires zero production constructed-portfolio
rows. An identical offline re-import inserts zero rows and leaves database
bytes unchanged. A different snapshot under an already admitted bundle fails
closed; it never overwrites or silently repairs evidence. A later reviewed
refresh contract must preserve new manifests and revisions append-only.

The deterministic Phase B audit was byte-identical across repeated runs; its
canonical JSON SHA-256 is
`d527e80ef476a958c6f85e23ae519c0c572d7d67a71e336e5cd682904cc80161`.

## Commands and offline reproduction

The explicit acquisition command is the sole network boundary:

```bash
poetry run python scripts/acquire_ecb_estr.py
```

The retained response can then be replayed without a network connection:

```bash
poetry run python scripts/build_ecb_estr_candidate.py \
  --candidate /absolute/path/to/disposable.sqlite \
  --raw-artifact data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.csv \
  --receipt data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.receipt.json

poetry run python scripts/import_ecb_estr_reference_rate.py \
  --target /absolute/path/to/disposable.sqlite \
  --raw-artifact data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.csv \
  --receipt data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.receipt.json

poetry run python scripts/validate_ecb_estr_reference_rate.py \
  --target database/portfolio_advisor.sqlite \
  --raw-artifact data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.csv \
  --receipt data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.receipt.json
```

The historical Phase A validator intentionally requires all four tables to be
empty. Preserve it for empty-foundation validation; use the Phase B validator
for the populated installed database.

## Remaining boundary

Reference-rate runtime admission remains `NO-GO`. EUR evidence exists, but its
governed alignment and portfolio calculation methodology are not implemented.
SOFR and HUFONIA remain absent, retained NAV remains insufficient/stale, and
production contains zero `SHORTLIST_CONSTRUCTED` candidates. Overall Milestone
11 is `IMPLEMENTED_BLOCKED_BY_DATA`; Milestones 12 and 13 are `NO-GO`, and
production cutover is `NOT_AUTHORIZED`.
