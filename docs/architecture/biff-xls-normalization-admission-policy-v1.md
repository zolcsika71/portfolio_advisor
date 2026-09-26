# BIFF-XLS normalization and admission policy v1 (Admission Proposed)

## Status and authority

This document specifies the boundary between the implemented
[BIFF-XLS source envelope v1](biff-xls-source-envelope-v1.md), the implemented
pure normalization-candidate adapter, and a future database-admission workflow.
The admission policy remains proposed and is not an admission authorization.
ADR-007 remains `Proposed`.

On 2026-09-26 the user explicitly approved two bounded evidence-gate
sub-decisions: the exact 27-hash recovery exception and current-retained-file
formula-origin sufficiency for the exact 33-hash registry. Their conditions
are documented in the recovery/formula evidence contract. No eligibility or
enforcement mechanism is implemented by that approval.

The source parser continues to return `NOT_EVALUATED_PARSER_ONLY`. The pure
`BIFF_XLS_NORMALIZATION_CANDIDATE_V1` adapter returns a separate
`NOT_EVALUATED_NORMALIZATION_CANDIDATE_ONLY` result with
`admission_approval = NOT_GRANTED`; it does not rewrite the parser envelope or
produce an eligibility approval. A separately authorized eligibility boundary
and writer would still have to decide whether a candidate may be admitted.

This policy does not change the operational importer, watcher, database schema,
existing correction records, historical stages, or application defaults. It
does not authorize a live or temporary retained-data re-import.

Phase 3B.1's bounded approval of atomic two-sheet behavior for its synthetic
temporary writer remains implemented and is not reopened here. Every pending
atomicity choice in this document applies only to a future real-workbook
admission policy.

## Verified baseline

The contract is based on the retained 33-workbook corpus and the two current
stores at commit `8753a62028ca59b9217d8641d4395ce63fb16f3d`:

- 5,283 `MODEL_PORTFOLIO` rows from exact sheet name
  ` modell portfóliók`;
- 10,833 `ANALYTICAL_SHORTLIST` occurrences from exact sheet name
  ` shortlist`;
- 27 workbooks that fail strict `xlrd` opening and require compound-document
  recovery, plus six that open strictly;
- 24 model currency-risk warnings: numeric `2` nine times, numeric `3` three
  times, numeric `4` six times, and text `VALUE!` six times;
- three shortlist sustainability warnings, all text
  `Hosszú kötvény alap`;
- no text `"0"`, text-formatted number, error, date, boolean, percentage-formatted,
  or comma-decimal metric cells; and
- every present metric cell is a BIFF number with `General` format.

The existing analytical correction chain is immutable and bound to shortlist
dataset fingerprint
`32216038d2f69dbf4c6436e91782025ae117dc59fe466e8a31ebc3719f93e8b2`:

| Effective stage | Bound rows | Authority |
| --- | ---: | --- |
| Original sub-asset correction | 1,278 | Existing correction admission |
| Composed question-mark correction | 287 | Existing composition admission |
| English asset/sub-asset pair mapping | 10,833 | `USER_APPROVED_ENGLISH_CLASSIFICATION_MAPPING_2026_09_23` |
| Metric numeric-zero to `NULL` | 23,979 | ADR-003 correction admission |

The approved English mapping manifest is
`data/knowledge/validated_rules/shortlist_classification_english_mapping_v1.json`,
SHA-256
`bada863f56c6f7f2ec084dcbd7e4c0f25319ddb321977cba2cd65783ccbad470`.
Its 77 input pairs map all 10,833 occurrences to 51 effective pairs. Its scope
is shortlist asset/sub-asset classification only.

## Layer model

The future workflow must keep five layers distinguishable:

1. **Retained source:** immutable `.xls` bytes and their SHA-256.
2. **Typed source envelope:** exact BIFF cell type, raw value, format,
   coordinate, row occurrence, exact sheet name, and parser diagnostics.
3. **Normalized original candidate:** now implemented as immutable typed field
   candidates under the field rules below, with no effective correction or
   database operation applied.
4. **Admitted original evidence:** immutable occurrence/raw-payload rows and
   metric observations, each bound to stable source provenance.
5. **Effective projection:** only an already authorized, versioned correction
   or mapping may replace an original value for consumers.

`NULL` at layer 3 or 4 does not rewrite a source cell. A shortlist source zero,
normalized `0.0`, and corrected effective `NULL` are three different states.

## Provenance and deterministic identities

A normalization candidate must bind all of the following without using a local
temporary path or runtime timestamp:

- envelope contract and parser versions;
- envelope fingerprint, workbook SHA-256, byte length, and source filename;
- strictly parsed snapshot date;
- exact sheet name, index, visibility, role, and sheet fingerprint;
- exact ordered header cells and header coordinates;
- row occurrence index, 1-based source row, row fingerprint, and parser row
  reference;
- for every projected field, exact header text, 1-based column, cell
  coordinate, BIFF type, raw value, XF index, format key, and number format;
- normalization-policy version and every mapping/correction identifier used;
  and
- a canonical candidate fingerprint over all preceding values and all proposed
  normalized outputs.

For existing shortlist metric compatibility, the target source reference must
remain exactly:

```text
SHORTLIST:{workbook_sha256}:{exact_sheet_name}:{source_row}:{exact_header}
```

The leading space in `exact_sheet_name` is significant. The adapter must also
retain the parser's `BIFF_XLS_V1:{workbook_sha256}:{sheet_index}:{source_row}`
row reference. Reconstructing a correction reference from a trimmed sheet name,
database row ID, instrument membership, or row position after sorting is
forbidden.

## Source-to-effective field matrix

All raw cells remain in the source envelope. `source_payload_json` remains a
compatibility representation of trimmed display values; it is not a substitute
for BIFF type or format evidence.

| Source header / identity | Role | Normalized original candidate | Current or proposed database representation | Effective rule and rejection boundary |
| --- | --- | --- | --- | --- |
| Workbook bytes | Both | Immutable SHA-256 and length | `source_file` plus content-addressed retained evidence in a future writer | Hash must match the exact bytes parsed; changed bytes are a different source |
| Filename date | Both | ISO date parsed only from terminal valid `YYYYMMDD.xls` | Snapshot date | Reject missing, invalid, ambiguous, or non-terminal date |
| Exact sheet name | Both | `MODEL_PORTFOLIO` or `ANALYTICAL_SHORTLIST`, while retaining exact leading-space name | `source_sheet` and role-specific snapshot | Reject hidden, missing, ambiguous, renamed, or unsupported sheet |
| `Portfólió neve` | Model | Trimmed non-empty text | Portfolio identity and snapshot; raw text retained | Reject blank/non-text; do not infer or merge names |
| `Termék` | Both | Trimmed non-empty source name | Instrument display/alias plus observed source name | Reject blank/non-text; no identity inference from name |
| `ISIN` | Both | Trimmed explicit ISIN | Instrument identity plus occurrence binding | Reject blank/malformed ISIN; do not synthesize an identifier |
| `Hányad (%)` | Model | Finite BIFF number, non-negative, in source percentage points | `reported_weight` as SQLite `REAL` | Do not divide by 100; numeric zero remains zero; reject text, boolean, date, error, negative, or non-finite values |
| `Eszközosztály` | Both | Original trimmed Hungarian text plus optional separate projection | Raw payload always; current model observed field is legacy-compatible English, current shortlist observed field is original Hungarian | Never overwrite original. Only the exact approved shortlist pair manifest may supply shortlist effective English |
| `Aleszközosztály` | Both | Original trimmed Hungarian text plus optional separate projection | Raw payload always; current model observed field is a legacy-compatible projection, current shortlist observed field is original Hungarian | Apply mappings as an asset/sub-asset pair, never as independent guesses |
| `Termék típus` | Shortlist | Nullable trimmed original text | Currently raw JSON only | No approved canonical mapping; preserve or `NULL` when source-missing |
| `Deviza` | Both | Nullable trimmed original text | Observed currency field and raw payload | Preserve exact source evidence; a future code validator must be separately specified rather than silently upper-casing |
| `Devizakockázat` | Both | Nullable original text; non-text anomaly remains a diagnostic, not a coerced label | Model currently has legacy-compatible English/`NULL`; shortlist is currently raw JSON only | No approved cross-workflow English mapping. Unknown/anomalous present values make admission ineligible without an occurrence-level disposition |
| `Fenntarthatóság` | Both | Nullable original text | Model is raw JSON today and has a temporary Phase 1 extension contract; shortlist is raw JSON only | Existing model translation is compatibility-only. No shortlist correction is authorized; the three misplaced labels require explicit disposition |
| Any duplicate row | Both | Separate occurrence in original source order | Separate source occurrence and lineage | Never deduplicate. A membership projection may group an ISIN only while retaining every occurrence and conflict status |

The existing model compatibility translations in
`DB_creation/excel_processing.py` remain separate from the approved shortlist
pair mapping. Reusing the shortlist mapping must not silently change model
terminology or historical constructed-artifact stages.

## Metric contract

### Common typing and precision

The twelve metrics use the exact source headers below. A present value must be
a finite BIFF `number`. The adapter carries the unscaled Python/xlrd binary64
value into SQLite `REAL` without rounding, percentage conversion, sign change,
annualization, or other derivation. The typed source envelope remains the
precision authority.

Return, volatility, downside-risk, and drawdown fields are provider-reported
decimal ratios. Sharpe and information ratio are dimensionless; the existing
metric catalog stores all of them with unit `RATIO`. The `3yr` and `5yr`
headers do not establish annualized versus cumulative semantics, so the
adapter must not claim either.

| Exact header | Metric identity | Semantic unit | Model source: number / missing / numeric zero | Shortlist source: number / missing / numeric zero | Existing effective rule |
| --- | --- | --- | ---: | ---: | --- |
| `YTD` | `YTD` | Decimal return ratio | 5,239 / 44 / 44 | 10,794 / 39 / 326 | Shortlist: 326 exact bound zeros become effective `NULL` |
| `1yr` | `RETURN_1Y` | Decimal return ratio | 5,239 / 44 / 218 | 10,794 / 39 / 352 | Shortlist: 352 exact bound zeros become effective `NULL` |
| `3yr` | `RETURN_3Y` | Decimal return ratio; horizon convention unresolved | 5,239 / 44 / 508 | 10,794 / 39 / 1,039 | Shortlist: 1,039 exact bound zeros become effective `NULL` |
| `5yr` | `RETURN_5Y` | Decimal return ratio; horizon convention unresolved | 5,239 / 44 / 1,112 | 10,794 / 39 / 1,966 | Shortlist: 1,966 exact bound zeros become effective `NULL` |
| `1Y Sharpe` | `SHARPE_RATIO_1Y` | Dimensionless ratio | 5,235 / 48 / 515 | 10,833 / 0 / 1,039 | Shortlist: 1,039 exact bound zeros become effective `NULL` |
| `3Y Sharpe` | `SHARPE_RATIO_3Y` | Dimensionless ratio | 5,235 / 48 / 841 | 10,833 / 0 / 1,580 | Shortlist: 1,580 exact bound zeros become effective `NULL` |
| `5Y Sharpe` | `SHARPE_RATIO_5Y` | Dimensionless ratio | 5,235 / 48 / 1,273 | 10,833 / 0 / 2,331 | Shortlist: 2,331 exact bound zeros become effective `NULL` |
| `1Y Vol.` | `VOLATILITY_1Y` | Decimal volatility ratio | 5,239 / 44 / 100 | 10,794 / 39 / 310 | Shortlist: 310 exact bound zeros become effective `NULL` |
| `3Y Vol.` | `VOLATILITY_3Y` | Decimal volatility ratio | 5,239 / 44 / 522 | 10,794 / 39 / 1,119 | Shortlist: 1,119 exact bound zeros become effective `NULL` |
| `Down. risk` | `DOWNSIDE_RISK` | Decimal downside-risk ratio | 5,235 / 48 / 5,217 | 10,833 / 0 / 10,833 | Shortlist: all 10,833 exact bound zeros become effective `NULL` |
| `Info. ratio` | `INFORMATION_RATIO` | Dimensionless ratio | 5,235 / 48 / 782 | 10,833 / 0 / 1,540 | Shortlist: 1,540 exact bound zeros become effective `NULL` |
| `Max. drawd.` | `MAXIMUM_DRAWDOWN` | Decimal drawdown ratio | 5,239 / 44 / 940 | 10,794 / 39 / 1,544 | Shortlist: 1,544 exact bound zeros become effective `NULL` |

`Missing` in the corpus table combines BIFF `blank` and `empty`, because both
produce no normalized observation. Their distinct source types remain in the
envelope. No current metric cell is empty text or text `"0"`.

### Missing, zero, and invalid-cell truth table

| Typed source state | Normalized original | Effective value | Disposition |
| --- | --- | --- | --- |
| BIFF `number`, finite and non-zero | Same unscaled binary64 value | Same unless an exact authorized correction says otherwise | Eligible |
| BIFF `number` equal to zero, shortlist | `0.0` observation | `NULL` only for the 23,979 exact ADR-003 correction bindings; otherwise `0.0` | Eligible only under exact dataset/correction binding |
| BIFF `number` equal to zero, model | No metric observation in the existing model-only legacy-compatible projection; typed zero remains in the source envelope | `NULL`/absent for compatibility consumers | **Proposed reuse requires approval.** It is not a statement that the source failed cleaning and must never apply to shortlist or allocation |
| BIFF `blank` | No observation / SQL `NULL` | `NULL` | Eligible; retain `blank` source type |
| BIFF `empty` | No observation / SQL `NULL` | `NULL` | Eligible; retain `empty` source type |
| Text empty string | No observation / SQL `NULL` | `NULL` | Eligible only with an explicit `EMPTY_TEXT_AS_MISSING` diagnostic; retain text source type |
| Text `"0"` or other numeric-looking text | None | None | Reject numeric normalization; no implicit decimal-comma or string coercion |
| BIFF error or error-like text | None | None | Reject metric candidate; preserve raw/error text |
| BIFF boolean or date serial | None | None | Reject metric candidate even if the payload is numerically representable |
| Non-finite number | None | None | Reject metric candidate |

The current analytical model stage has typed observations for only five metric
identities (`RETURN_1Y`, `SHARPE_RATIO_1Y`, `VOLATILITY_1Y`, `DOWNSIDE_RISK`,
and `MAXIMUM_DRAWDOWN`); its other seven metric fields and sustainability are
retained only in raw JSON. The temporary Phase 1 contract demonstrates an
additive typed extension for those fields, but does not authorize installing
it on the retained database. This is a compatibility gap, not source absence.

## Classification and descriptive-field policy

### Approved shortlist asset/sub-asset projection

For the exact existing shortlist dataset only, apply the correction stages in
their recorded order:

1. retain the original Hungarian pair from the occurrence/raw payload;
2. apply the admitted 1,278-row original correction stage;
3. apply the admitted 287-row composition stage;
4. match the resulting pair against the approved 77-entry English manifest;
5. expose English values only through the effective view; and
6. retain every correction ID, application order, item fingerprint, mapping
   manifest hash, and original/effective pair in provenance.

An unknown pair, changed count, changed snapshot effect, new occurrence, or
changed dataset fingerprint rejects reuse of the mapping. It must not fall back
to a single-column mapping or the model parser's translation dictionary.

### Currency-risk inventory and proposed translations

| Original source value | Model rows | Shortlist rows | Proposed English candidate | Authority / disposition |
| --- | ---: | ---: | --- | --- |
| `Nincs fedezve` | 3,345 | 7,917 | `Unhedged` | Proposal only; legacy model parser already uses it, but no cross-workflow effective mapping is approved |
| `nincs fedezve` | 6 | 11 | `Unhedged` after case-normalized match | Proposal only; preserve original case |
| `Fedezve` | 1,515 | 1,556 | `Hedged` | Proposal only; does not establish target currency, hedge ratio, or coverage period |
| `Részben fedezve` | 102 | 289 | `Partially Hedged` | Proposal only; degree and period remain unknown |
| Source-missing | 291 | 1,060 | `NULL` / unknown | Preserve BIFF blank versus empty in the envelope |
| Numeric `2`, `3`, or `4` | 18 | 0 | None | Known model anomaly; raw value plus diagnostic required; no coercion |
| Text `VALUE!` | 6 | 0 | None | Known model anomaly; raw value plus diagnostic required; it is not a BIFF error cell |

The three shortlist `Fenntarthatóság = Hosszú kötvény alap` values likewise
have no authorized repair. They remain original text plus
`ANOMALOUS_SUSTAINABILITY_VALUE`; they must not be moved into classification or
discarded.

A future baseline exception could admit the exact known 24 and three warning
occurrences only through a reviewed, hash/row/coordinate-bound disposition
manifest. That manifest is proposed, does not yet exist, and must not authorize
new warning values or future rows.

## Admission eligibility

A future eligibility evaluator may emit `ELIGIBLE_CANDIDATE` only when every
gate below passes. The implemented normalization adapter deliberately emits no
eligibility status. Even a later eligibility result would not perform or
authorize admission.

1. **Exact source binding:** workbook bytes, parser envelope fingerprint,
   filename date, sheet fingerprints, row fingerprints, and header/coordinate
   identities all agree.
2. **Parser status:** the input is the implemented v1 envelope and still says
   `NOT_EVALUATED_PARSER_ONLY`; the adapter must not rewrite it to an admitted
   status.
3. **Structure:** both exact visible leading-space sheets are present once,
   ordered headers are exact, and every applicable row and duplicate occurrence
   remains in source order.
4. **Field typing:** every candidate satisfies the matrix above. A rejected
   cell rejects its sheet candidate; silent coercion is forbidden.
5. **Recovery:** separately probe the same bytes without compound-document
   recovery. A recovery-dependent source is ineligible unless its exact hash is
   covered by an approved exception and an independent reader agrees on every
   admitted cell and coordinate. The user-approved 2026-09-26 exception covers
   only the 27 hashes in the evidence contract's normative registry and only
   under every documented acceptance and rejection condition. Current software
   does not enforce that approval or turn the registry into a general allowlist.
6. **Formula origin:** cached-value equality does not prove a literal cell or
   formula absence. The user-approved 2026-09-26 sub-decision accepts the
   verifier's complete zero-formula-record finding as sufficient for this gate
   only for the current bytes of the exact 33-hash registry. It makes no claim
   about pre-export calculations, formulas, pasted values, or original literal
   entry, and current software does not enforce the decision.
7. **Diagnostics:** every warning has an authorized occurrence-level
   disposition. Unknown warnings reject the candidate.
8. **Dataset and correction state:** the before-state dataset fingerprint and
   every installed correction validator match. A new shortlist occurrence does
   not inherit an old correction.
9. **Real-workbook atomicity policy:** both role candidates and their
   eligibility results are produced together. Phase 3B.1 already implements
   approved atomic Option A for synthetic temporary targets. Only the future
   real-workbook writer's choice between Option A and a durable partial-state
   Option B remains an ADR-007 approval decision; silent shortlist omission is
   never allowed.
10. **Final validation:** integrity, foreign keys, immutable evidence bindings,
    all correction contracts, receipt ordering, and affected consumer
    projections pass inside the writer transaction before commit.

## Replay, changed sources, and correction preservation

- Exact workbook hash, envelope fingerprint, normalization-policy version,
  mapping/correction identities, candidate fingerprint, predecessor receipt,
  and before-state fingerprint may replay as a mutation-free no-op.
- The same snapshot date with different workbook bytes, envelope content,
  row/cell fingerprints, mapping version, or candidate output rejects before
  mutation. Supersession remains unapproved.
- Source occurrences are identified by workbook/sheet/row/cell provenance, not
  by an instrument/date natural key. Duplicate multiplicity is immutable.
- Existing shortlist correction rows must not be copied or rebound by database
  row ID. A rehearsal must first reproduce the existing shortlist source
  references and dataset fingerprint exactly, then run all four existing
  correction validators unchanged.
- If re-import changes even one bound source reference, original value,
  occurrence count, pair inventory, or dataset fingerprint, the historical
  correction layer remains attached to the old evidence and the candidate
  fails. A new correction disposition requires separate evidence and approval.
- Historical classification stages and constructed artifacts remain pinned to
  the stage recorded when they were produced. A newer effective mapping must
  not rewrite those records.
- A baseline rebuild should attach the new BIFF envelope/cell provenance as an
  additive evidence layer. It must not replace the existing text-faithful raw
  JSON, source occurrences, correction admissions, or historical source
  references merely to adopt a newer parser.

## Consumer boundary

Current consumers intentionally see different layers:

- the operational advisor and importer remain backed by
  `model_portfolio.sqlite` until a separately approved cutover;
- analytical model migration rows contain immutable occurrence/raw JSON
  evidence and a partial typed metric projection;
- shortlist analytical consumers use effective metric and classification views
  when the corresponding correction features are installed, otherwise the
  original observations; and
- historical constructed artifacts resolve the correction stage recorded in
  their own provenance.

The implemented adapter exposes each immutable `source_cell` separately from
its `normalized_value`; it never emits an effective value. A future eligibility
or writer boundary must keep `raw`, `normalized_original`, and `effective`
layers explicit and must never make a consumer infer the layer from a nullable
scalar alone.

## Bounded validation evidence

The design was checked without modifying retained evidence:

- all 33 envelopes were parsed and inventoried: 5,283 model rows, 10,833
  shortlist occurrences, 27 strict-open failures, 24 currency-risk warnings,
  and three sustainability warnings;
- metric type/format inventories reproduced every count in the metric table and
  found no text-zero metric cells;
- 13 disposable synthetic truth-table cases passed, covering model and
  shortlist numeric zero, exact correction binding, non-zero preservation,
  blank, empty, empty text, text `"0"`, error, boolean, date, and non-finite
  inputs;
- a SQLite-backup-API copy under the system temporary directory passed the
  zero, original-classification, composed-classification, and English-pair
  validators with 23,979, 1,278, 287, and 10,833 items respectively; and
- changing one shortlist metric source reference on that disposable copy made
  the zero-correction validator fail with missing/ambiguous metric provenance.
  The temporary copy was then removed.

This validation demonstrates the proposed separation and current correction
bindings. A later read-only
[BIFF recovery and formula evidence verifier](biff-xls-recovery-formula-evidence-v1.md)
independently confirmed the allocation-defect signature and zero worksheet
formula records for the exact 33 hashes. The evidence report itself did not
grant either evidence approval. The subsequent explicit user approvals
recorded below do not install eligibility enforcement or a schema and do not
rehearse or authorize admission.

## Approved evidence sub-decisions and remaining decisions

The user explicitly approved these two independent evidence sub-decisions on
2026-09-26:

- The hash-bound recovery exception for exactly the 27 recovery-dependent
  hashes and every acceptance and rejection condition in
  [Proposal A](biff-xls-recovery-formula-evidence-v1.md#proposal-a-hash-bound-recovery-exception).
- Current-retained-file formula-origin sufficiency for exactly all 33 hashes,
  with the limitations and fail-closed conditions in
  [Proposal B](biff-xls-recovery-formula-evidence-v1.md#proposal-b-retained-file-formula-origin-sufficiency).

The evidence report's `NOT_GRANTED` fields remain an unchanged record of its
audit-time state. These subsequent documentation approvals do not implement
an eligibility mechanism or authorize admission. The following decisions
remain pending:

1. Whether the real-source model adapter may reuse the existing model-only
   numeric-zero-to-absence compatibility rule, or whether typed original model
   zero observations require an additive schema plus a separate effective
   compatibility view.
2. The occurrence-bound disposition manifest for the 24 currency-risk and
   three sustainability warnings.
3. English currency-risk mappings. All candidates in this document are
   proposals, not approved transformations.
4. Whether `3yr` and `5yr` returns are cumulative or annualized. No rescaling
   is allowed until this is evidenced and approved.
5. For a future real-workbook writer only, atomic dual-sheet Option A versus
   durable partial-state Option B, plus same-date supersession and post-commit
   publication policy. Phase 3B.1's bounded synthetic Option A approval remains
   implemented and is not pending.

## Implemented candidate boundary and smallest next slice

`portfolio_advisor.workbook_source.normalization` implements the pure
`BIFF_XLS_NORMALIZATION_CANDIDATE_V1` adapter. It:

- accepts an in-memory v1 source envelope and expected workbook SHA-256;
- emits immutable field candidates, typed rejection/warning diagnostics, exact
  target source references, and a deterministic candidate fingerprint;
- has no database connection, schema installation, correction mutation,
  file movement, watcher route, or operational default;
- validates optional English projection bytes against the exact approved
  shortlist mapping manifest while marking its existing dataset admission as
  reference-only and not inherited by the candidate; and
- ships only synthetic fixture tests for type/missingness boundaries, mapping
  success/failure, duplicate preservation, changed-source rejection,
  recovery/formula diagnostics, and deterministic serialization.

No current writer consumes the candidate format. The smallest next slice is a
separately authorized, non-writing eligibility evaluator and temporary
rehearsal that reproduces exact correction bindings before any admission
adapter is designed. It must not transfer authority or silently choose any
unresolved admission semantics.
