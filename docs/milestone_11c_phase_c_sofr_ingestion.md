# Milestone 11C Phase C — official New York Fed SOFR evidence

Phase C admits one fixed, immutable official Federal Reserve Bank of New York
response containing only daily overnight Secured Overnight Financing Rate
(SOFR) observations. It uses the unchanged provider-neutral provenance-v2
schema completed in Phase C0. No SOFR average, SOFR Index, EFFR, OBFR, BGCR, or
TGCR value is admitted.

Phase C does not align a benchmark to portfolio returns, calculate a portfolio
metric, repair NAV, construct a portfolio, or authorize production cutover.

## Official identity and sources

- [SOFR overview](https://www.newyorkfed.org/markets/reference-rates/sofr)
- [Markets Data API documentation](https://markets.newyorkfed.org/static/docs/markets-api.html)
- [Reference-rate methodology, contingencies, publication and revisions](https://www.newyorkfed.org/markets/reference-rates/additional-information-about-reference-rates)
- [New York Fed terms of use](https://www.newyorkfed.org/privacy/termsofuse)

SOFR is a broad measure of the cost of borrowing cash overnight collateralized
by U.S. Treasury securities. It is calculated from Treasury repo-market
transactions as a volume-weighted median and published in percentage points per
annum, rounded to the nearest basis point. Stored `3.68` therefore means 3.68%,
not 0.0368. Parsing uses `Decimal` directly from JSON and never binary float.
The definition retains `ACT_360` and `SIMPLE_ACT_360_OVERNIGHT` conventions.

SOFR is secured USD repo evidence. ECB €STR is unsecured EUR overnight
borrowing evidence. The two benchmarks are not interchangeable.

The fixed machine request is:

```text
https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate=2018-04-02&endDate=2026-08-31&type=rate
```

The HTTP 200 response has media type `application/json;charset=utf-8`. Its root
is exactly `refRates`; every admitted row has `type=SOFR`, `effectiveDate`,
`percentRate`, four percentile fields, and `revisionIndicator`. Two rows also
have `footnoteId=2` and exact `NA` percentile values. The published daily SOFR
rate remains present on those rows; missing or nonnumeric `percentRate` is
rejected rather than filled.

The New York Fed permits bounded automated access and retention subject to its
terms. Retained and redistributed evidence must preserve source/copyright
identification, must not distort the reference rate, and must not imply Federal
Reserve Bank of New York endorsement or affiliation. Operators must monitor the
official terms for later changes.

## Immutable local evidence

The only authorized Phase C acquisition made one request. Redirects were
disabled; connect/read timeouts, explicit content negotiation, exact
effective-URL validation, HTTP/media-type/encoding checks, and an 8 MiB maximum
were enforced. There were no credentials and no retry. All later work was
offline.

```text
raw path:       data/raw/reference_rates/new_york_fed/sofr/sofr-16031a7ffb8865e9fd7eb066ab119198edca696eba4278f455c30c892d952ecf.json
raw bytes:      445,820
raw SHA-256:    16031a7ffb8865e9fd7eb066ab119198edca696eba4278f455c30c892d952ecf
receipt path:   data/raw/reference_rates/new_york_fed/sofr/sofr-16031a7ffb8865e9fd7eb066ab119198edca696eba4278f455c30c892d952ecf.receipt.json
receipt SHA-256: ea59f1419b64508751fa0928c7b95024dcbae4c15d27cae8be666fe13908fb47
receipt fingerprint: 6759735f78a17ecc520680baace21cef0d476530d923c63f06710d576bdecba1
retrieval time: 2026-09-02T17:00:39+00:00
```

These ignored local files are required for every offline replay and validator.
They must be backed up with the installed database. Content-addressed paths are
immutable: partial pairs, symlinks, altered bytes, altered receipts, or a
different response at an existing identity fail closed. Raw provider evidence
and receipts are never committed.

## Availability and revisions

`effectiveDate` is the SOFR value date. The provider did not supply an exact
per-observation publication timestamp, provider revision ID, or dataset
version. These provider fields remain `NULL`; none is synthesized from the
value date, retrieval time, artifact hash, request, or system identity.

The official schedule says SOFR is normally published at approximately 8:00
a.m. ET on applicable business days, one business day after its value date,
with holiday and exceptional nonpublication rules. That does not prove an exact
historical boundary for every date. Phase C therefore uses
`RETRIEVAL_BOUND=2026-09-02T17:00:39.000000Z` for all SOFR observations. A
temporal query sees no SOFR value before that instant. Provider publication and
schedule-derivation fields remain `NULL`.

All 2,102 wire rows supply `revisionIndicator` as the exact empty string. It is
stored with source field `revisionIndicator` and status
`PROVIDER_EMPTY_REVISION_INDICATOR`; it is not mislabeled as an explicit
provider statement of no revision. The New York Fed may publish a qualifying
same-day revision at approximately 2:30 p.m. ET and footnote it. A future
changed value remains blocked unless separately captured evidence and a
validated provider-revision transition contract authorize an append. Values
are never silently overwritten.

## Counts and deterministic identities

```text
SOFR observations:       2,102
SOFR date range:         2018-04-02 through 2026-08-31
SOFR dataset:            48053c9ced286708de4e6dcfbe571db055dd6c4c2b677c28980bb24872ee180f
SOFR definition:         8c5636fb6b73ab620149ccf20210f21a0bfb0d7ef16f49ea09d3b7ce5cd71df0
SOFR source:             6919a79a48826df9d315d8275ba75188898717da6a9b24436b0aee34ade25feb
SOFR manifest:           06d0003e8a0f71ac04ed365e732c335c778c720d78d208508f8236151c5f92f6
system evidence identity: f6b861f5afaf3db1c39c3dea5214c01f60454fda25f66a0381c58831e99b9ca0
migration identity:      MILESTONE_11C_PHASE_C_NYFED_SOFR_V1
provenance contract:     2
feature revision:        2
feature fingerprint:     3add5fa914e71807fff2add5b369a3ef80f50c2d22844743cfb74b00680cfe71
schema fingerprint:      4862a51f6724b22e7e7aab0c1e914449750077dbd0fa54ca66e2aa478e579404
```

Phase C adds one definition, one source, one manifest, and 2,102 observations.
Installed totals are 2 definitions, 2 sources, 2 manifests, and 3,873
observation versions. Exact offline replay inserts zero rows and preserves
database bytes.

Existing €STR evidence remains exact: 1,771 observations from 2019-10-01
through 2026-08-31, dataset fingerprint
`99a1a2ff837688bb78fd0b81cbef1ef64f27f1cab36cc2acdb0ded5026cc534e`,
definition fingerprint
`20f194bc719fa1a0f0971187d6620716e44b8d15c0add2fe44d649bdc6638df3`,
source fingerprint
`f4103515ef8b7fe3b789ae49922be0a85b4317efe572914672186eddf76d479d`,
and manifest fingerprint
`5cf0a1625d7f50cdf3241e26a4e8807c04c33974f4f99950ac7b8599e1bc775b`.
Its raw and receipt hashes are unchanged.

## Offline reproduction

Acquisition is explicit and must not be repeated for this fixed artifact:

```bash
poetry run python scripts/acquire_sofr.py
```

Candidate construction, import, and all validators use retained files offline:

```bash
poetry run python scripts/build_sofr_candidate.py \
  --source database/portfolio_advisor.sqlite \
  --candidate /absolute/disposable/portfolio_advisor.sqlite \
  --raw-artifact data/raw/reference_rates/new_york_fed/sofr/sofr-16031a7ffb8865e9fd7eb066ab119198edca696eba4278f455c30c892d952ecf.json \
  --receipt data/raw/reference_rates/new_york_fed/sofr/sofr-16031a7ffb8865e9fd7eb066ab119198edca696eba4278f455c30c892d952ecf.receipt.json
poetry run python scripts/import_sofr_reference_rate.py --help
poetry run python scripts/validate_reference_rate_schema.py \
  --target database/portfolio_advisor.sqlite
poetry run python scripts/validate_ecb_estr_reference_rate.py --help
poetry run python scripts/validate_sofr_reference_rate.py --help
poetry run python scripts/validate_reference_rate_provenance.py \
  --target database/portfolio_advisor.sqlite --require-sofr
```

Validators are read-only, reconstruct each bundle from its retained raw/receipt
pair, reject cross-benchmark contamination, and produce byte-identical JSON on
repeat runs.

## Decision boundary

Phase C supports official daily EUR €STR and official daily USD SOFR evidence.
HUF HUFONIA is the next reference-rate checkpoint and remains `NOT_STARTED`.
NAV remediation, benchmark alignment, cash-return policy, portfolio metrics,
and real construction remain blocked. Overall roadmap-compliant Milestone 11
remains **NO-GO**. Milestones 12 and 13 remain **NO-GO**. Production cutover
remains **NO-GO / NOT_AUTHORIZED**.
