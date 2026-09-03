# Milestone 11C Phase D — official MNB HUFONIA evidence

Phase D admits one fixed, immutable official Magyar Nemzeti Bank workbook as
HUF reference-rate evidence under the unchanged provider-neutral provenance-v2
contract. It does not change the schema, reinterpret the admitted ECB €STR or
New York Fed SOFR bundles, align a benchmark to portfolio returns, repair NAV,
calculate a portfolio metric, construct a portfolio, or authorize production
cutover.

## Official identity and sources

- [MNB monetary-policy statistics](https://statisztika.mnb.hu/statistical-topics/monetary-policy-statistics)
- [Official HUFONIA workbook](https://www.mnb.hu/letoltes/hufonia.xls)
- [MNB announcement introducing the HUFONIA name](https://www.mnb.hu/en/pressroom/press-releases/press-releases-2010/press-release-on-the-introduction-of-the-hufonia-name)
- [MNB market information](https://www.mnb.hu/en/monetary-policy/market-information)
- [MNB Bulletin methodology discussion](https://www.mnb.hu/letoltes/erhart-kollarik-eng.pdf)
- [MNB website disclaimer and reuse conditions](https://www.mnb.hu/en/the-central-bank/practical-issues/disclaimer)

HUFONIA is the Hungarian Forint Overnight Index Average, administered and
calculated by the MNB. The workbook definition describes the effective
overnight HUF interest rate as the transaction-amount-weighted average of
unsecured overnight interbank HUF lending transactions between the reporting
banks, specialized credit institutions, and applicable EEA branches. Its
transaction evidence comes from the mandatory MNB K12 daily report. HUFONIA is
therefore unsecured HUF interbank evidence; it is not the secured USD repo
SOFR benchmark, the unsecured EUR €STR benchmark, BUBOR, the MNB base rate, or
the separately terminated HUFONIA Swap Index.

The workbook reports rates in percentage points per annum. Stored `17.880`
means 17.880%, not 0.17880. Every admitted rate is created as an exact `Decimal`
from the BIFF numeric bits or the one reviewed exact text cell; binary
floating-point conversion is never used for financial normalization. The
official HUFONIA period uses three displayed decimals. The official workbook's
pre-name history uses two displayed decimals, and those values are preserved at
the precision actually published. The MNB sources reviewed for this checkpoint
do not supply a day-count convention, so that definition field is truthfully
`NOT_SUPPLIED_BY_MNB`; the daily overnight rate is not a compounded average.

## Official workbook contract and history

The authoritative machine-readable artifact is an HTTP 200
`application/vnd.ms-excel` BIFF8 workbook at:

```text
https://www.mnb.hu/letoltes/hufonia.xls
```

It contains one visible `info` worksheet and visible annual worksheets from
2002 through 2026. The parser requires the reviewed sheet identities, order,
headers, cell record types, styles, display precision, and definition text. It
rejects formulas, hidden/replacement sheets, embedded streams other than the
reviewed workbook/summary streams, malformed OLE/BIFF records, unknown
annotations, duplicate dates, conflicting values, and truncated workbooks.

The fixed Phase D boundary admits 6,231 unique observations from 2002-01-02
through 2026-08-31. The HUFONIA name was introduced for publication from
2010-09-01; earlier rows are the predecessor effective overnight average that
the MNB includes in the official HUFONIA history workbook. Phase D preserves
that distinction through the launch date and the workbook's two-decimal versus
three-decimal display contract.

The workbook states that the date basis changed from value date to trade date
on 2016-10-04. The canonical dataset therefore identifies 3,735 `VALUE_DATE`
observations and 2,496 `TRADE_DATE` observations. The exact provider annotation
is retained in the canonical dataset and raw artifact. These semantics must be
used in any later alignment work; Phase D performs no alignment.

The capture also contained 2026-09-01 and 2026-09-02 rows beyond the authorized
boundary, one exact annual-sheet overlap for 2011-01-03, and one legacy trailing
2002 turnover-only row. The parser validates those reviewed structures
explicitly and does not admit them as extra observations. There is no admitted
blank, formula, provisional, or missing-rate row. Absent weekend, holiday, or
other non-publication dates create no value and are never filled, interpolated,
or replaced by zero or another rate.

## Availability and revisions

The workbook says MNB calculates HUFONIA to three decimals and publishes at
10:30 on the following MNB working day. It supplies neither exact historical
per-observation publication timestamps nor a versioned complete MNB business-
day calendar. Historical material also records an earlier 11:00 publication
time. Consequently, the official sources do not prove a reproducible exact
publication boundary for every historical date. Phase D does not infer one.

All HUFONIA observations use the conservative provenance-v2 basis:

```text
availability basis:    RETRIEVAL_BOUND
availability boundary: 2026-09-03T12:27:03.000000Z
```

No HUFONIA observation is available to a temporal query before that exact
capture instant. Provider publication fields and all schedule-derived rule and
calendar fields remain `NULL`. The value/trade date, HTTP `Date`, workbook
modification time, artifact hash, and internal evidence identity are never
presented as provider publication metadata.

The workbook has no provider dataset version or observation revision-ID field;
both remain `NULL`. An exact 2015-11-19 note, `módosítva 14:53-kor`, explicitly
marks the published row as corrected. It is preserved as the raw provider
revision indicator with status `PROVIDER_EXPLICIT_REVISION`, but there is no
provider revision ID and no transition contract. The other 6,230 observations
have no revision field and use `PROVIDER_REVISION_FIELD_NOT_SUPPLIED`. A later
different artifact or value is not an authorized revision and fails closed;
only byte-identical offline replay is a no-op.

## Immutable local evidence

The sole Phase D acquisition made one request with finite connect/read
timeouts, redirects disabled, explicit content negotiation and user agent,
environment proxy trust disabled, exact effective-URL/status/media-type/
encoding checks, an 8 MiB limit, and no credentials. All parsing, imports,
tests, and validation after capture are offline.

```text
raw path:       data/raw/reference_rates/mnb/hufonia/hufonia-e44e41c78d9f7d96dfc60b7baa8a47b11b02cb0dd32218466337a4c8166ee649.xls
raw bytes:      574,464
raw SHA-256:    e44e41c78d9f7d96dfc60b7baa8a47b11b02cb0dd32218466337a4c8166ee649
receipt path:   data/raw/reference_rates/mnb/hufonia/hufonia-e44e41c78d9f7d96dfc60b7baa8a47b11b02cb0dd32218466337a4c8166ee649.receipt.json
receipt SHA-256: fe931bd99387d87f3c57d4c7b38037b888a3fae0aa62e278a07160e0f470d472
receipt fingerprint: 4ed4c46af9c25a36ae0b31833869d04ed4a84d61bbedb3ec8b14e740b9713223
retrieval time: 2026-09-03T12:27:03+00:00
```

The MNB disclaimer permits unchanged distribution with MNB source attribution
and disclaims informational accuracy/completeness guarantees. The source is
therefore recorded as official-administrator evidence, not as investment
advice or an MNB endorsement. Operators must preserve attribution and review
later terms before any new acquisition or redistribution.

The ignored raw/receipt pair is required for every replay and validator. It
must be backed up together with the installed database. Content-addressed paths
are immutable: a partial pair, symlink, path escape, changed byte, changed
receipt, or conflicting evidence at an existing identity fails closed. Raw MNB
evidence and receipts are never committed.

## Counts and deterministic identities

```text
HUFONIA observations:       6,231
HUFONIA date range:         2002-01-02 through 2026-08-31
HUFONIA dataset:            a7e65378518ea7749fdafa1e971cd6b685600268a22d1b7a176fa70bb971e273
HUFONIA definition:         4ac27463ae9a89c635393300bcaf55f36ab217b60aa53771a2e1b4071f8c7edd
HUFONIA source:             568a5217a6f13a33cd55ddf687cb1dcace33ce59bdf3771fa2cd4ee2ceea0cc9
HUFONIA manifest:           7423a3743bb587f18961d1e0314972dd3ebff96eb7b49e191cb2cda57848dac0
system evidence identity:   5bc0a734ab82a055d633a1fb49aff15a8531f7f1550acad5c14929928ffd35be
migration identity:         MILESTONE_11C_PHASE_D_MNB_HUFONIA_V1
provenance contract:        2
feature revision:           2
feature fingerprint:        3add5fa914e71807fff2add5b369a3ef80f50c2d22844743cfb74b00680cfe71
schema fingerprint:         4862a51f6724b22e7e7aab0c1e914449750077dbd0fa54ca66e2aa478e579404
```

`MNB_HUFONIA_XLS_HISTORY` is the adapter's system-local stable series
identifier for this exact official workbook contract; it is not presented as
an MNB-issued dataset code or version.

Phase D adds one definition, one source, one manifest, and 6,231 observations.
Installed totals are 3 definitions, 3 sources, 3 manifests, and 10,104
observation versions. Exact replay inserts zero rows and preserves database
bytes.

Existing official evidence is unchanged:

- ECB €STR: 1,771 observations, 2019-10-01 through 2026-08-31, dataset
  `99a1a2ff837688bb78fd0b81cbef1ef64f27f1cab36cc2acdb0ded5026cc534e`.
- New York Fed SOFR: 2,102 observations, 2018-04-02 through 2026-08-31,
  dataset `48053c9ced286708de4e6dcfbe571db055dd6c4c2b677c28980bb24872ee180f`.

Their definition, source, manifest, observation, raw-artifact, and receipt
content is byte/logically preserved. The reference-rate schema, contract,
feature, construction, ranking, and registry policy fingerprints are unchanged.

## Offline reproduction

Acquisition is an explicit operator action and must not be repeated for this
fixed artifact:

```bash
poetry run python scripts/acquire_hufonia.py
```

Candidate construction, replay, and validation use retained files offline:

```bash
poetry run python scripts/build_hufonia_candidate.py \
  --source database/portfolio_advisor.sqlite \
  --candidate /absolute/disposable/portfolio_advisor.sqlite \
  --raw-artifact data/raw/reference_rates/mnb/hufonia/hufonia-e44e41c78d9f7d96dfc60b7baa8a47b11b02cb0dd32218466337a4c8166ee649.xls \
  --receipt data/raw/reference_rates/mnb/hufonia/hufonia-e44e41c78d9f7d96dfc60b7baa8a47b11b02cb0dd32218466337a4c8166ee649.receipt.json
poetry run python scripts/import_hufonia_reference_rate.py --help
poetry run python scripts/validate_reference_rate_schema.py \
  --target database/portfolio_advisor.sqlite
poetry run python scripts/validate_ecb_estr_reference_rate.py --help
poetry run python scripts/validate_sofr_reference_rate.py --help
poetry run python scripts/validate_hufonia_reference_rate.py --help
poetry run python scripts/validate_reference_rate_provenance.py \
  --target database/portfolio_advisor.sqlite --require-hufonia
```

The HUFONIA validator is benchmark-scoped. The complete validator reconstructs
all three bundles independently from retained raw/receipt evidence, rejects
cross-benchmark or cross-source contamination, and emits byte-identical JSON
on repeated runs.

## Decision boundary

Phase D supports official daily EUR €STR, official daily USD SOFR, and the
official MNB HUFONIA history for HUF as separate evidence bundles. They are not
interchangeable, and none is yet a portfolio cash-return or risk-free-rate
methodology.

The next checkpoint is Milestone 11C Phase E: NAV provenance remediation and
the smallest feasible EUR/HUF source refresh. Benchmark alignment, cash-return
treatment, portfolio metrics, and real construction remain blocked. Overall
roadmap-compliant Milestone 11 remains **NO-GO**. Milestones 12 and 13 remain
**NO-GO**. Production cutover remains **NO-GO / NOT_AUTHORIZED**.
