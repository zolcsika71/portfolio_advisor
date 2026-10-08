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
are documented in the recovery/formula evidence contract. The implemented pure
evidence-gate evaluator enforces those conditions only; it is not a general
eligibility or admission mechanism. Its workbook hash and filename are declared
lookup identities matched to the bound historical report, not proof of a fresh
inspection of current workbook bytes.

On 2026-09-26 the user also explicitly approved
`MODEL_METRIC_ZERO_HANDLING_POLICY_V1`: preserve finite numeric zeros as
original observations for the exact twelve model metrics and expose legacy
omission only through the separately identified, explicitly selected,
provenance-bound `MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1` projection.
The approval covers the 33 inventoried workbooks and future workbooks that
independently satisfy the same v1 typed-source contract. The pure, explicitly
selected projection is now implemented. A bounded retained-corpus comparison
established metric/ranking equivalence with legacy descriptive inputs fixed;
full candidate-native reader equivalence and active consumer integration remain
pending. No admission or operational authority is granted.

On 2026-10-07 the user explicitly approved the three independently reviewed
model currency-risk policy sub-decisions below: lexical translation, the exact
24-occurrence anomaly disposition, and the historical comparison-profile
definition. The subsequent authorized synthetic-only slice implements their
pure, explicitly selected projection. A separately authorized read-only
currency-risk comparison now supplies the bounded evidence below; consumer
activation and admission remain unauthorized. Neither the policies nor that
completed comparison authorize further comparison execution.

On 2026-10-08 the user explicitly approved the three bounded
[reader policy sub-decisions](#three-approved-reader-policy-sub-decisions):
model classification authority, descriptive/allocation rules and explicit holding
order. Each approval preserves its reviewed scope and fail-closed conditions.
The separately authorized pure facade and bounded retained-reader acceptance are
recorded below; these policy approvals did not authorize either execution.
Further legacy-reader/ranking comparison execution, consumer activation and
admission remain unauthorized. Descriptor/allocation equivalence, legacy
equal-key ordering and integrated reader equivalence remain unproven; observed
lexical coverage is limited to the examined reader profile and retained inputs.

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

## Proposed integrated candidate-native model reader contract

The original reader contract was traced at revision
`66b29b0bc6078169d72deddf20513adeb72ad871`. The pure facade is now implemented
under separate synthetic-only implementation authorization, as described below.
The ledger's bounded retained acceptance grants no reader comparison-execution,
consumer-activation or admission authority.
The separate [metric comparison](#bounded-retained-corpus-comparison-evidence) and
[currency-risk comparison](#bounded-currency-risk-compatibility-comparison-evidence)
held different legacy inputs fixed; neither separately nor together proves this
reader. The composition boundary is shown in the
[existing diagram](../diagrams/model-portfolio-consolidation-cutover.puml).

### Verified reader and consumer boundary

[`ModelPortfolioReader` and `HoldingObservation`](../../src/portfolio_advisor/database/repository.py)
provide three methods and twelve holding fields. The legacy repository returns
stored values, not fresh normalization, and orders holdings by SQL
`Portfolio Name`, `ISIN`, `Product`. Its optional absent `Asset Class` column
produces `None`; that schema compatibility branch is not permission to hide an
unmapped classification in a candidate with a required source header.
[`CapitalPreservationAdvisor`](../../src/portfolio_advisor/advisor/service.py)
already accepts an explicitly injected reader. It invokes the actual
[reported-indicator calculations](../../src/portfolio_advisor/metrics/portfolio.py)
and [ranking functions](../../src/portfolio_advisor/ranking/ranking.py).
The in-memory facade implements `ModelPortfolioReader`, **not**
`FileBackedModelPortfolioReader`; it must not invent a `database_path`.
`observation_dates()` returns the ascending unique `tuple[date, ...]`
of accepted input snapshots; `latest_observation_date()` returns its greatest
date; `load_holdings(date)` returns that complete snapshot's fresh
`list[HoldingObservation]` in the explicitly selected order. Empty/conflicting
inputs and an unavailable date fail visibly, without nearest-date substitution.

### Field-level reader contract

In this table, **C** is the same validated immutable normalization candidate,
**M** its explicitly selected public model-metric projection, and **R** its
explicitly selected public currency-risk projection. Every field has a sidecar
binding to C's workbook hash/date/envelope/candidate fingerprints, exact model
sheet name/index/fingerprint, row occurrence/reference/fingerprint and original
header/cell coordinate/type/raw value/XF/format. Projected fields additionally
bind the projection fingerprint, version, policy and disposition/reason.
The proposed DTO may collapse a missing value to `None`; the sidecar must retain
blank versus absent versus empty text, rejection versus omission, and originals.

| Reader field / method input | Candidate source and proposed transformation | Type / unit | Authority, missing/error behavior and remaining gap |
| --- | --- | --- | --- |
| Observation date | C.`source_binding.snapshot_date`; strict ISO date conversion, tied to the validated filename and workbook identity | `date` | Existing source v1 date contract; never choose a DB date, nearest date or latest file arrival |
| `portfolio_name` | `Portfólió neve` → `portfolio_name`; C's trimmed normalized text | Required `str` | Existing normalization requires nonempty BIFF text; rejected field blocks construction. No casefold/alias/group merging. Legacy ordinary text is not categorically translated; trim-induced identity differences remain to compare |
| `product` | `Termék` → `product_name`; C's trimmed normalized text | DTO `str \| None`, candidate requires text | Do not exploit DTO optionality to accept rejected/missing required candidate text; no product aliases or legacy lookup. Text preservation differences remain to compare |
| `isin` | `ISIN` → `isin`; exact validated uppercase source form after existing trim | DTO `str \| None`, candidate requires text | Existing 12-character regex, not inferred share-class identity or check-digit proof; no repair/fallback. Rejected/missing field blocks construction |
| `allocation` | `Hányad (%)` → `reported_weight`; C's finite nonnegative number unchanged | `float`, percentage points | Existing normalizer rejects missing/non-number/negative/non-finite weight. Preserve numeric zero; no `/100`, renormalization or model-metric omission rule. Legacy broad zero replacement also affected allocation: a zero-to-`None` reader extension is **not approved** |
| `currency` | `Deviza` → `original_currency`; C's trimmed normalized text unchanged | `str \| None`, reported label | Existing blank/absent/empty-text missing semantics; invalid non-text blocks construction. No ISO correction, conversion or investor base-currency inference. Legacy raw label/whitespace differences may change currency concentration |
| `currency_risk` | `Devizakockázat` → R's separately projected attribute | Canonical English `str \| None` | Approved three text mappings and exact 24-occurrence exception only; preserve R's missing/anomaly reasons. Unknown labels/types or changed bindings reject the whole R request; no hedge fraction, English-input extension or future anomaly exception |
| `return_1y` | `1yr` → `RETURN_1Y` in M | `float \| None`, unscaled ratio | Approved explicit original/compatibility selection; present number unchanged, source missing → `None`, eligible compatibility zero → `None` with omission reason. `REJECTED` is not missing and blocks the proposed reader |
| `sharpe_ratio_1y` | `1Y Sharpe` → `SHARPE_RATIO_1Y` in M | `float \| None`, unscaled ratio | Same scoped metric rule; do not recompute, annualize or coerce text `"0"` |
| `volatility_1y` | `1Y Vol.` → `VOLATILITY_1Y` in M | `float \| None`, unscaled ratio | Same scoped rule. Consumer's reported-annualized label is existing compatibility terminology, not independently established methodology |
| `downside_risk` | `Down. risk` → `DOWNSIDE_RISK` in M | `float \| None`, unscaled ratio | Same scoped rule; no inferred target return or risk methodology |
| `maximum_drawdown` | `Max. drawd.` → `MAXIMUM_DRAWDOWN` in M | `float \| None`, unscaled ratio | Same scoped rule; no absolute-value/sign repair or reconstructed drawdown |
| `asset_class` | `Eszközosztály` → C.`original_asset_class`; separately selected reader classification view | DTO `str \| None` | Original Hungarian text preserved. Original-label and exact model-only lexical profiles were approved on 2026-10-08 and are implemented in the synthetic-validated facade; corpus classification equivalence remains unproven. C retains `NOT_APPLICABLE_TO_MODEL_ROLE`, no English model mapping. Derived view/provenance belongs in the sidecar, never inherited shortlist mapping/correction authority or fallback `None` |

The remaining seven model metrics are still accounted for in M's complete
twelve-field ledger: `YTD` → `YTD`, `3yr` → `RETURN_3Y`, `5yr` → `RETURN_5Y`,
`3Y Sharpe` → `SHARPE_RATIO_3Y`, `5Y Sharpe` → `SHARPE_RATIO_5Y`,
`3Y Vol.` → `VOLATILITY_3Y`, and `Info. ratio` → `INFORMATION_RATIO`.
They have no `HoldingObservation` slot and must not be fabricated into the five
active source metrics. Preserve their dispositions and unresolved 3yr/5yr
interpretation. `Aleszközosztály` and `Fenntarthatóság` remain original fields in
the sidecar, not silently discarded or translated. No sustainability decision
is made here. Both entire sheets, including shortlist diagnostics, remain in C.

The advisor also needs an explicitly supplied reviewed rules path, observation
date/latest-date selection, `allow_proposed_rules` and `alternative_count`;
these are caller/ranking controls, not candidate financial fields. The reader
must not synthesize rules or enable proposed rules. Portfolio totals, weighted
indicator values/coverage/warnings, unhedged allocation and currency concentration
are calculated by the existing metric functions, not stored substitutes.
Eligibility/reasons, score contributions, scores, name-based tie-breaking,
ordering, winners and alternatives come from the existing advisor/ranking.
This contract authorizes no change to those functions or the active policy.

### Same-candidate composition and fail-closed behavior

The pure reader implements the following v1 construction boundary:

1. Require C and its expected candidate fingerprint, exact source/envelope/
   normalization v1 identities, and mandatory metric and currency-risk choices.
   Invoke **both published public projection APIs on C**, not on independently
   reconstructed, model-only or differently mapped candidates. Reuse their
   substantive immutable typed/provenance validation; a recomputed fingerprint
   alone is insufficient. No caller-approved registry or policy override.
2. Pin `MODEL_METRIC_PROJECTION` v1 and `MODEL_CURRENCY_RISK_PROJECTION` v1,
   their selected profile names, approved policy identities/dates, and R's
   captured mapping/anomaly-registry identity. Record both result fingerprints.
   Both `original_candidate` bindings must equal C's complete identity and
   unchanged canonical content. These are in-memory consistency bindings,
   not a fresh inspection of current workbook bytes.
3. Align one M row and one R row to **every** source-ordered model row using
   occurrence ID/index, source row/reference and row fingerprint. Check exact
   sheet/header/cell and field-occurrence identities too. Never join only by
   date, portfolio, ISIN or product, zip without checks, aggregate duplicates,
   drop a holding, or substitute a legacy field. Missing, extra, reordered,
   duplicated or mismatched projection rows fail the complete snapshot visibly.
4. Preserve the immutable source-ordered ledger, allocation and all non-target
   fields, full C, and its exact diagnostics. Carry scoped zero/translation/
   anomaly resolutions separately; do not delete historical unresolved messages
   or waive unrelated recovery, formula, classification, sustainability or
   metric-interpretation gates. Required reader fields with `REJECTED` status
   block the DTO boundary, rather than masquerade as `None`. Unused-field
   diagnostics stay explicit; reader construction is not a blanket eligibility
   verdict on them or on the shortlist.
5. Reject empty input, unsupported contracts/choices, stale expected identity,
   date conflicts, and requests for an unavailable date. A proposed multi-date
   reader accepts one complete candidate per date, sorts date keys, returns the
   greatest for `latest_observation_date`, and rejects duplicate date inputs
   (even equal fingerprints) rather than silently merge/replay/deduplicate.
   This is a reader-input proposal, not an admission replay/supersession policy.
   Return a fresh holdings list with immutable entries and stable positional
   sidecar bindings; mutating the list must not mutate the reader or C.

Historical construction **must remain**
`approved_shortlist_mapping_manifest=None`, as in the verified comparisons.
For the two anomaly-bearing hashes the production registry pins the complete
candidate/envelope, both sheet fingerprints/counts and parser-library version
`2.0.2`. Adding the optional approved shortlist manifest changes the candidate
fingerprint and must trigger `APPROVED_ANOMALY_WORKBOOK_BINDING_MISMATCH`;
model-only reconstruction or rebinding is not permitted. Preserving no mapping
for this historical reader does not approve shortlist admission or remove its
diagnostics. A different mapped historical variant would need new authorization,
not a composition-layer bypass. Future v1 candidates receive lexical/metric
policy scope only, never historical anomaly or evidence-gate authority.

### Implemented pure composition ledger

[`compose_model_projection_ledger`](../../src/portfolio_advisor/workbook_source/model_projection_ledger.py)
implements `MODEL_PROJECTION_COMPOSITION_LEDGER` v1, not `ModelPortfolioReader`.
Its mandatory inputs are complete C, `metric_result`, `currency_risk_result`,
explicit `metric_projection` and `currency_risk_projection` selections, and
`expected_candidate_fingerprint`. It invokes both public projections again on C
and compares the supplied deeply immutable typed results with that replay,
including canonical contents, evaluated bindings and exact source-ordered
occurrence coverage. Mixed candidates, missing/extra/duplicate/reordered
references, unsupported versions, altered values or provenance fail visibly,
even with recomputed fingerprints. It also checks model classification metadata
against validated original fields: English candidates/mapping identity must be
absent and status must be `NOT_APPLICABLE_TO_MODEL_ROLE`. This prevents forged
classification authority without choosing a reader translation profile.
No registry/policy/approval override exists.

The frozen ledger retains full C (both sheets and diagnostics) and one composed
entry per model occurrence, with all original fields, twelve metric dispositions
and the currency-risk disposition/reason. Its evaluated projection metadata,
policy dates, mapping/registry identities and fingerprints are captured in
immutable canonical serialization; `to_dict()` returns detached copies.
`SOURCE_OCCURRENCE_ORDER_NOT_READER_ORDER` declares evidence order only.
Original classifications and descriptive/allocation fields remain evidence;
the ledger selects neither a descriptor profile nor final holding order and
does not turn `REJECTED` into a valid reader value. Scoped policy resolutions
remain separate from unchanged historical diagnostics.

[`synthetic ledger tests`](../../tests/test_model_projection_ledger.py) exercise
both explicit metric modes, joint zero/risk outcomes, missing/excluded states,
duplicate preservation, tampering, and deep immutability. Anomaly-success
coverage uses only a restored test-local private registry through both public
APIs; those tests alone do not prove retained acceptance or offer a production
override. The separate preserved public-API run below supplies bounded retained
ledger acceptance without test-local substitutions.
No retained inputs or private packages are required by the API or tests.
Every ledger is `NOT_EVALUATED_COMPOSITION_LEDGER_ONLY`, with admission
`NOT_GRANTED`. The API itself validates in-memory composition, not fresh source
bytes, reader fields, eligibility, integrated equivalence or operational authority.

### Bounded retained-corpus ledger acceptance evidence

The subsequent read-only acceptance run used published revision
`7908995606c0a5c882cfea74197a6bbf68dad4f4`. Recovery package
`model-ledger-retained-acceptance-v1.82oxMA` preserves the actual
`accept_ledgers.py` harness, per-workbook/per-mode results, source/input
fingerprints, execution-time dependency versions and `REPRODUCE.md` outside
Git. Its `evaluated/acceptance.json`
(`MODEL_PROJECTION_LEDGER_RETAINED_ACCEPTANCE` v1) has SHA-256
`8930d1e3fd7f0cb66dc8e7bb9a832593dd0270b17af15dbf3e8b106f8c22997b`
and canonical report fingerprint
`37b6a55508702c60ac14ab4be941621794c29d91390682f05bc6838c093538eb`.
The package's `SHA256SUMS` has SHA-256
`bbeb4b1cd08321dd41df10fa166b9b7600bce253cb3c20b1cf05133e0fbac1ef`.
All 28 entries, 15 source bindings, 33 inventory/input bindings and saved
results were verified without executing the harness or rerunning the corpus.
Private occurrence-level evidence remains outside Git.

The harness verified all workbook hashes before parsing both exact leading-space
sheets. It normalized each envelope with the inventory hash and explicit
**`approved_shortlist_mapping_manifest=None`**, invoked both explicit public
metric modes and the public `MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1`,
then called public `compose_model_projection_ledger` for each metric mode with
the actual same complete candidate fingerprint. Production policies and the
anomaly registry were unchanged; no helper replaced a failed call, no registry
was substituted and no identity was fabricated.

- All 33 workbooks/dates passed both modes: **66 accepted public ledger cases**,
  with zero rejection, discrepancy or blocked case. Each mode retained all
  5,283 source-ordered model occurrences and checked 63,396 metric cells.
- Original mode retained 62,848 present metric observations, including all
  12,072 finite numeric zeros. Compatibility mode retained 50,776 nonzero
  observations and omitted exactly those 12,072 zero observations, not holdings.
  Both preserved the same 548 source-missing metric cells and their typed evidence.
- Currency risk accounted for all 5,283 occurrences: 4,968 approved translations,
  291 preserved missing states (255 formatted blanks and 36 empty cells), and
  all 24 exact manifest/registry-bound anomaly dispositions with reason
  `LEGACY_ANOMALY_AS_NONE_WITHOUT_INTERPRETATION`. No numeric-code meaning was inferred.
- Full candidates, allocation, originals, diagnostics, exact occurrence joins,
  source order and captured evaluated policy bindings remained intact, including
  all 10,833 shortlist occurrences and 327,603 typed source data fields.
  Model classification metadata remained original-only, with no English authority.
  Every occurrence survived independently; no economic-key deduplication occurred.
  Deep immutability and detached serialization checks passed without input mutation.
- Four representative candidates (2024-07-02, 2024-10-07, 2024-10-17 and
  2025-05-09), repeated in memory in both modes, produced eight byte-identical
  projection/ledger repeat cases. No second workbook parse was performed.
  `reconcile_prior.py` separately recorded 363 matching saved-evidence checks
  of workbook/envelope/candidate/projection identities, counts and policy bindings;
  neither earlier reader/ranking comparison was re-executed.

Reproduction requires the exact clean source revision and its own imports,
all 33 external XLS files at the recorded hashes, the unchanged typed-anomaly
package `model-currency-risk-typed-v1.FyqXIB`, and a fresh output directory.
The additional saved-evidence reconciliation requires the preserved metric and
currency-risk comparison packages. No database is an execution dependency;
no workbook/database bytes are copied into the package. Source copies identify
the executed code but do not replace the checked checkout. The recorded complete
34-distribution environment identifies this run, not earlier audits (Python
3.12.7 and xlrd 2.0.2); each workbook/mode/repeat stage was bounded to 90 seconds.

This establishes **joint retained ledger acceptance only**, not a reader or
integrated reader/ranking equivalence. Source order remains evidence order,
not final holding order. Typed observations are from the published parser,
not a new independent raw-BIFF/formula/recovery audit; the historical six/27
strict/recovery split was not freshly tested. Historical diagnostics and audit
`NOT_GRANTED` fields remain unchanged. Neither this run nor the earlier separate
metric/currency-risk comparisons establishes complete currency-risk information
or hedging of missing/partially hedged holdings. The three reader policies were
subsequently approved on 2026-10-08, separately from this evidence. Classification
field equivalence, descriptive/allocation equivalence and legacy equal-key ordering
remain unproven. The separate [retained reader acceptance](#bounded-retained-corpus-reader-acceptance-evidence)
below does not broaden this ledger result; integrated equivalence, activation and
eligibility composition remain pending.
ADR-007 stays `Proposed`; every ledger and the evidence report retain admission
`NOT_GRANTED`. Recording this result grants no further execution or admission
authority.

### Proposed opt-in surface and approved reader policy choices

The following illustrates a future interface, **not implemented code**:

```python
reader = CandidateNativeModelPortfolioReader(
    candidates=explicit_candidates,
    expected_candidate_fingerprints=explicit_fingerprints,
    metric_projection=MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1,
    currency_risk_projection=MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1,
    classification_profile="MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1",  # Policy approved; unimplemented
    descriptor_profile="ORIGINAL_MODEL_DESCRIPTORS_V1",  # Policy approved; unimplemented
    holding_order="MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1",  # Policy approved; unimplemented
)
```

No selection has a default. The original metric choice remains available only
when explicitly requested; it is intentionally not the historical compatibility
claim. The approved descriptor policy would expose existing normalized original
portfolio/product/ISIN/currency and allocation fields; classification is a
separate mandatory choice, not authority inherited from a descriptor profile.
The alternative `MODEL_CLASSIFICATION_ORIGINAL_LABELS_V1` exposes trimmed
originals (Hungarian when reported) and makes no English-equivalence claim.
All three reader policies below were approved on 2026-10-08; their implementation,
comparison execution, consumer activation and admission remain unauthorized.
A later separately authorized harness could inject the reader into
`CapitalPreservationAdvisor(reader, explicit_rules_path)` without changing any
default. No database is an input or fallback; legacy data belongs exclusively
on the comparator's independent expected-results side. A deterministic sidecar
must fingerprint all candidates, projections, selected profiles/order and every
DTO-to-occurrence field binding. Proposed reader results always retain
`admission_approval = NOT_GRANTED`; constructing them is neither consolidation
eligibility composition nor ranking eligibility.

The following static trace grounds the three separately approved policies below;
their pure facade implementation is described below; retained compatibility
validation remains pending:

- **Model classifications:** legacy
  [`excel_processing.VALUE_TRANSLATIONS`](../../src/portfolio_advisor/DB_creation/excel_processing.py)
  independently translates model asset/sub-asset text, including historical
  question-mark spellings, and accepts configured English values. That is code
  behavior, not approval to reuse the shortlist's pair corrections or authority.
  The approved original-label policy is truthful but cannot claim full English
  reader equivalence. The independently approved model lexical policy below
  supplies its own bounded scope, original/effective provenance and unknown-label
  rules, not shortlist authority. Asset class is not
  read by the current advisor metric/ranking functions, but it is a reader DTO
  field and other workflows use classifications; ranking equality cannot waive it.
  For example, the legacy model dictionary maps `részvény` to `Equity`,
  `kötvény` to `Bond`, and `pénzpiaci` to `Money Market`; its independent
  sub-asset dictionary maps `globál` to `Global` and the literal historical
  `fejl?d? piacok` to `Emerging Markets`. Legacy categorical NFC/trim/casefold
  lookup precedes flat storage; the repository reads that stored English text.
  C instead preserves normalized Hungarian originals plus exact source cells,
  with no model English classification candidate. The shortlist's original,
  composed and English pair-mapping correction stages are a different lineage,
  not authority to install either model dictionary as an effective mapping.
- **Descriptive identity/weight compatibility:** candidate text is trimmed and
  required fields are stricter; legacy ordinary portfolio/product/ISIN/currency
  text is not passed through those categorical mappings. Legacy numeric-zero
  replacement includes allocation, outside the approved twelve-metric policy.
  Compare these states explicitly; reject invalid candidate weights rather than
  invent missing allocation or broaden zero handling to force equivalence.
- **Holding order:** approved `SOURCE_ORDER_V1` policy preserves the source ledger and
  DTO order. Legacy SQL sorts by portfolio/ISIN/product and has no explicit
  source-occurrence tie-break for equal keys. Metric float sums follow holding
  order, so even a value-equivalent permutation can affect exact scores/ties.
  The approved key/source-tie policy below defines the explicit sort/collation/
  rejection contract and permutation-to-source provenance; it must never replace
  source order or be silently introduced to obtain a pass. No order equivalence
  or numeric-tolerance exception is approved here.

### Three approved reader policy sub-decisions

The user explicitly stated “Approve all three within their reviewed scopes.”
Each decision below is **Approved on 2026-10-08**, following independent static
review against source revision `7eb2a4e95f789c2a5da3a64ba001bab41685b501`.
These are three separate policy approvals, not execution grants. Subsequent
explicit authorization covered the pure facade and synthetic validation;
separate bounded retained-reader acceptance is recorded below. Neither grants
further legacy-reader/ranking comparison execution, consumer activation or
admission; construction requires explicit approved profile selections.
The existing twelve-field/three-method reader contract remains the boundary.
The [retained ledger acceptance](#bounded-retained-corpus-ledger-acceptance-evidence)
supplies same-candidate composition evidence, not authority for these choices.
The separate metric and currency-risk comparisons used legacy descriptors and/or
other inputs fixed; no joint reader, classification, descriptor or order
equivalence test is claimed here.

Common approved scope is `MODEL_PORTFOLIO` sheets of the 33 inventoried candidates
and future candidates independently satisfying the same supported v1 typed-source,
normalization and ledger contracts. No shortlist rule, correction admission,
evidence exception or operational authority is inherited. Historical construction
remains `approved_shortlist_mapping_manifest=None`; C and the source-ordered
ledger remain unchanged, including their original-only classification metadata.
Any later reader view belongs in a separate immutable provenance sidecar, never
inside C or by rebinding either exact anomaly candidate.

Each future result must bind policy/profile name and version, the separate
2026-10-08 user approval reference, evaluated content fingerprint, C/envelope/workbook/
ledger identities, projection identities, all original field/cell identities and
diagnostics, and the complete DTO-position-to-source-occurrence permutation.
Malformed typed inputs, stale/recomputed identities concealing changed content,
unsupported selections, missing/extra occurrences or a required field rejection
fail the complete snapshot with field/occurrence-specific reasons. Never drop or
merge a holding, invent `None`, fetch a legacy value, or infer reader/ranking/
admission eligibility. All proposed results retain admission `NOT_GRANTED`.

#### Decision 1: model classification authority — approved 2026-10-08

**Approval record:** The user's explicit 2026-10-08 approval of all three within
their reviewed scopes separately approves `MODEL_CLASSIFICATION_AUTHORITY_POLICY_V1`
under every scope, exclusion, preservation and rejection condition below. This
is model-only lexical/original-view authority, not implementation, comparison
execution, consumer activation, admission or semantic taxonomy validation.

**Problem and alternatives.** C has original Hungarian asset/sub-asset fields;
the legacy importer stores English labels from independent column dictionaries.
The current advisor does not consume `asset_class`, but
[LTIA comparison](../../src/portfolio_advisor/tbsz/comparison.py) retains it in
descriptive targets and [backtest eligibility](../../src/portfolio_advisor/backtesting/eligibility.py)
carries it into constituent diagnostics. Matching advisor scores cannot waive
classification fields. Alternatives are an explicit original-label view, a
separately authorized model lexical view, or a new semantic pair taxonomy.
The last requires additional provider/pair evidence; borrowing the shortlist's
[ADR-006](../decisions/ADR-006-standardize-effective-shortlist-classifications-in-english.md)
authority is not an alternative permitted by this contract.

**Approved policy: `MODEL_CLASSIFICATION_AUTHORITY_POLICY_V1`.** Permit explicit
`MODEL_CLASSIFICATION_ORIGINAL_LABELS_V1` or
`MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1` selection, recommending the latter for
later historical reader comparison. Original mode returns the existing trimmed
source labels with no English translation claim. Lexical policy independently
authorizes only the following model tables, copied from the reviewed
[`VALUE_TRANSLATIONS`](../../src/portfolio_advisor/DB_creation/excel_processing.py)
(file SHA-256 `edacb65aa8947f35934b115ff726dde9a37f2f4d593192989889c5e1b1e3943a`).
Lookup is **text-only NFC, trim, casefold**, using the original source text and
recording its lookup key. Each semicolon-separated item is a separate literal
key; hyphens, spaces and question marks are not repaired or generalized.

| Model source field | Exact lookup keys | Approved canonical English output |
| --- | --- | --- |
| `Eszközosztály` | `alternatív` | `Alternative` |
| `Eszközosztály` | `kötvény` | `Bond` |
| `Eszközosztály` | `kötvény - befektetési kategória`; `kötvény-befektetési kategória` | `Investment Grade Bond` |
| `Eszközosztály` | `kötvény - magas hozamú`; `kötvény-magas hozamú` | `High Yield Bond` |
| `Eszközosztály` | `kötvény-rugalmas` | `Flexible Bond` |
| `Eszközosztály` | `pénzpiac`; `pénzpiaci` | `Money Market` |
| `Eszközosztály` | `részvény` | `Equity` |
| `Aleszközosztály` | `abszolút hozamú` | `Absolute Return` |
| `Aleszközosztály` | `amerikai dollár`; `usd` | `USD` |
| `Aleszközosztály` | `eur`; `euro` | `EUR` |
| `Aleszközosztály` | `európa` | `Europe` |
| `Aleszközosztály` | `európa-vállalatok`; `európai vállalatok` | `Europe-Corporates` |
| `Aleszközosztály` | `fejl?d? piacok` | `Emerging Markets` |
| `Aleszközosztály` | `globál` | `Global` |
| `Aleszközosztály` | `globál állampapír`; `globál-állampapír` | `Global-Government Bond` |
| `Aleszközosztály` | `hu-állampapír` | `Hungary-Government Bond` |
| `Aleszközosztály` | `huf`; `magyar forint` | `HUF` |
| `Aleszközosztály` | `ingatlan` | `Real Estate` |
| `Aleszközosztály` | `kötvény - magyar állampapírok` | `Bond - Hungarian Government Bonds` |
| `Aleszközosztály` | `közép-kelet európai állampapír` | `Central and Eastern European Government Bond` |
| `Aleszközosztály` | `magyar állampapírok` | `Hungarian Government Bonds` |
| `Aleszközosztály` | `nyersanyag` | `Commodities` |
| `Aleszközosztály` | `részvény - fejl?d? piacok` | `Equity - Emerging Markets` |
| `Aleszközosztály` | `észak-amerika` | `North America` |
| `Aleszközosztály` | `észak-amerika-állampapír`; `észak-amerikai állampapír` | `North America-Government Bond` |

Lexical policy also permits **only the normalized identity keys of the listed
canonical outputs in that same column**, returning their exact canonical spelling.
This approved English-input permission is classification-specific; it does not
extend the approved currency-risk mappings. Freeze all 10 asset keys, 24 sub-asset
keys, column-specific canonical identity keys and lookup operations in the
versioned, content-fingerprinted implementation; runtime edits to the legacy
dictionary must not expand authority. If normalized keys collide with conflicting
outputs, fail rather than choose precedence. These are independent lexical
translations, **not** approved asset/sub-asset pair semantics or financial
eligibility. Literal `fejl?d?` recognition is compatibility with existing code,
not encoding repair or evidence that other spellings have the same meaning.

**Conditions and gaps.** Both classifications require existing valid nonempty
BIFF text. Missing/empty/rejected values, numeric codes, booleans, dates or errors
block construction. Unknown text blocks lexical mode visibly, rather than fall
back to originals or `None`; original mode may preserve well-formed unknown text
but cannot claim English equivalence. No new synonyms, accent repair beyond NFC,
aliases, wildcard matching or future-category inference. Preserve both original
labels and every cell binding; DTO `asset_class` receives the selected asset result, while the
sub-asset result and mapping provenance remain in the sidecar (no DTO slot).
The retained ledger checked original-only metadata, not corpus coverage of these
tables or provider semantic correctness. All-date label/field equivalence and
other classification-consuming workflows remain untested. Future matching text
gets lexical policy scope only, no historical correction or admission.

**Reviewed wording covered by the explicit approval:** “Approve `MODEL_CLASSIFICATION_AUTHORITY_POLICY_V1`
for model-only explicit original-label or legacy-lexical views, with exactly the
tables, same-column canonical-English identity keys, text lookup, preservation
and fail-closed conditions above. This creates independent model lexical authority,
not shortlist/pair-correction authority, semantic taxonomy validation,
implementation, comparison execution, activation or admission approval.”

#### Decision 2: descriptive/allocation compatibility — approved 2026-10-08

**Approval record:** The user's explicit 2026-10-08 approval of all three within
their reviewed scopes separately approves `MODEL_READER_DESCRIPTIVE_ALLOCATION_POLICY_V1`
and explicit `ORIGINAL_MODEL_DESCRIPTORS_V1` under every condition below.
This policy approval does not authorize implementation, comparison execution,
consumer activation or admission; descriptor/allocation equivalence is not
established by approval. Separate pure facade authorization is recorded below.

**Problem and alternatives.** The legacy reader returns stored ordinary text;
legacy `prepare_rows` does not categorically translate portfolio/product/ISIN/
currency, and its broad numeric-zero replacement includes allocation. C instead
trims text, requires portfolio/product/ISIN, and preserves a finite nonnegative
weight. Options are normalized-original fields, a separate raw-text legacy view,
or emulation of broad legacy cleaning. Raw text can change grouping/currency
keys; broad zero replacement exceeds the approved twelve-metric scope. Neither
is silently selected to force equivalence.

**Approved policy: `MODEL_READER_DESCRIPTIVE_ALLOCATION_POLICY_V1`, explicitly
selected as `ORIGINAL_MODEL_DESCRIPTORS_V1`.** Use only existing validated C fields:

| Reader input | Approved rule and missing/error boundary |
| --- | --- |
| Date | Existing strict snapshot ISO date → `date`; one complete candidate per date; duplicate/conflicting dates and unavailable requests reject, not merge or select a nearby date |
| `portfolio_name` / `product` | BIFF text, existing trim only, nonempty `str`; no casefold, Unicode rewrite, alias, numeric conversion or DB fallback. Required source missing/empty/rejected text blocks the snapshot despite optional `product` DTO type |
| `isin` | Existing trimmed exact uppercase 12-character source regex; no uppercase repair, inferred identity or check-digit claim; missing/rejected values block despite optional DTO type |
| `currency` | Existing trimmed optional text; blank/absent/empty or whitespace-only text → `None` with distinct original state/reason. No currency alias/ISO conversion; non-text/error/boolean/date values reject, not masquerade as missing |
| `allocation` | Existing `reported_weight`: finite nonnegative BIFF number → unchanged `float` in percentage points, **including numeric zero**. No `/100`, rounding, sum normalization, zero-to-absence or invented cash holding. Blank/empty/text `"0"`/other text, error, date, boolean, negative or non-finite source rejects |
| Classification, risk and five DTO metrics | Separate mandatory classification and already approved risk/metric projections; this descriptor policy neither overrides them nor resolves the remaining seven metrics/unused-field diagnostics |

Keep every occurrence, including zero-weight holdings and exact/economic duplicates.
For repeated holdings under one identical original portfolio label, grouping by
the validated trimmed name is explicit. If **different raw portfolio labels**
within a date collapse to one trimmed key, block with a normalization-collision
reason pending separate identity review; do not silently merge portfolios.
Do not deduplicate products/ISINs, normalize currency labels further, or enforce
a new 100%/per-holding cap at reader construction. Current ranking allocation
total/tolerance and metric-coverage eligibility remain the reviewed ranking
functions' responsibility. Reader success is not ranking eligibility.

**Provenance and compatibility limits.** Capture raw/normalized/DTO values,
typed missing states, applied trim, exact weight/format and field-occurrence
bindings; rejected fields receive actionable reasons without partial results.
`None` means authorized missing state or an already approved metric/risk
disposition, not a blanket error cleaner. Product/currency text `"0"` stays text
if valid under its own field contract; the metric/allocation rules do not coerce it.
The consumer groups by portfolio name and currency label, sums allocation and
weighted metrics in reader order, and tests metric availability using `is not None`.
Thus trim, allocation `0.0` versus legacy `None`, and label changes can matter
even when some numerical contributions are zero. The bounded acceptance below
found no raw-label collision census issues, but descriptive/allocation retained
equivalence and zero-allocation workflow equivalence remain unproven.
Differences must be recorded and reviewed later, not waived
because an advisor winner matches. Non-DTO sub-asset/sustainability evidence and
all diagnostics remain intact; sustainability dispositions remain unresolved.

**Reviewed wording covered by the explicit approval:** “Approve `MODEL_READER_DESCRIPTIVE_ALLOCATION_POLICY_V1`
and explicit `ORIGINAL_MODEL_DESCRIPTORS_V1` for model v1 candidates: existing
validated trimmed descriptors and unchanged finite nonnegative percentage-point
allocation, retaining zero-weight holdings, exact occurrences and originals.
Apply the stated required/optional missing, rejection and portfolio-key collision
rules, with no legacy fallback, broad zero cleaning or eligibility inference.
Historical descriptor equivalence, implementation, comparison execution,
activation and admission are not approved.”

#### Decision 3: reader holding order — approved 2026-10-08

**Approval record:** The user's explicit 2026-10-08 approval of all three within
their reviewed scopes separately approves `MODEL_READER_HOLDING_ORDER_POLICY_V1`
under every selection, permutation and rejection condition below. This defines
reader order only, not legacy equal-key equivalence, ranking-policy changes,
implementation, comparison execution, consumer activation or admission.

**Problem and alternatives.** The ledger is source-ordered evidence; the legacy
repository SQL orders by `Portfolio Name`, `ISIN`, `Product` without an occurrence
tie-break. Source order, an explicit legacy-key view, or a newly designed economic
order are separate choices. Economic sorting/grouping adds unsupported meaning.
Unspecified SQL equal-key ordering cannot be manufactured from source IDs or
recovered by silently borrowing legacy rows. The existing metric functions use
ordinary float sums and preserve input order inside each name group; portfolio
groups and final score ties use name order. Permutation invariance is unproven.

**Approved policy: `MODEL_READER_HOLDING_ORDER_POLICY_V1`.** Permit mandatory explicit
`SOURCE_ORDER_V1` (the unchanged model source sequence) or
`MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1`, recommending the latter for later
historical comparison. For the key view sort the complete date snapshot ascending
by the **actual selected DTO** `(portfolio_name, isin, product)` strings, each
compared by its UTF-8 bytes (binary, no locale/casefold/NFC/natural-numeric sort).
All three keys are nonempty text under Decision 2; no NULL ordering policy is
introduced for invalid inputs. Invalid UTF-8 encoding fails explicitly. Break
exact equal primary keys by the original model `occurrence_index`, preserving
source order within that key. Verify the occurrence index/reference sequence
before sorting; never use DB row IDs, metric/risk values, ISIN aggregation or
input arrival order as a tie-break.

Return a fresh holdings list and an immutable, complete bijective permutation
between DTO position and source occurrence, binding sort keys/profile/version
and fingerprint. Never reorder C, its ledger or diagnostics. Neither choice
drops/merges duplicates; every source occurrence gets exactly one DTO and sidecar
entry. Missing/extra/duplicate references, altered sort keys, unsupported collation,
or a permutation inconsistent with the selected policy fail the whole snapshot.
Dates remain ascending unique; `load_holdings(date)` applies only that snapshot's
explicit order, never nearest-date selection.

**Limitations and gaps.** UTF-8 binary keys target the ordinary default SQLite
text ordering expressed by the code-created schema (no explicit `COLLATE`),
not a fresh verification of the retained database's collation or storage values.
Normalized candidate keys may differ from stored legacy text. Source-index ties
are an approved deterministic extension, **not proof of legacy equal-key order**.
The saved currency-risk comparison preserved the actual legacy reader order and
reported no identical full `HoldingObservation` groups; that does not rule out
equal portfolio/ISIN/product keys with different metrics or allocations. Such
ties require full occurrence/multiplicity accounting in a later comparison; if
legacy rows cannot be uniquely bound, report order equivalence as unproven.
Different same-key rows can alter float sums, exact scores and winners; no
tolerance, reassociation, compensated-sum change or tie waiver is approved here.
Future files receive only the selected deterministic ordering rule, not inherited
legacy equivalence or evidence/admission authority.

**Reviewed wording covered by the explicit approval:** “Approve `MODEL_READER_HOLDING_ORDER_POLICY_V1`
with explicit source order or the defined UTF-8 binary portfolio/ISIN/product
key view and source-occurrence tie-break. Preserve the immutable source ledger,
every duplicate and a complete permutation sidecar. This specifies reader order,
not legacy equal-key order equivalence, ranking-policy change, implementation,
comparison execution, activation or admission approval.”

### Implemented pure candidate-native reader facade

[`CandidateNativeModelPortfolioReader`](../../src/portfolio_advisor/workbook_source/candidate_model_reader.py)
implements `CANDIDATE_NATIVE_MODEL_PORTFOLIO_READER` v1 under separate, explicit
synthetic-only implementation authorization. The three policy approvals above
did not themselves authorize implementation. All constructor arguments are
mandatory: nonempty immutable `ledgers`, parallel `expected_candidate_fingerprints`
and `expected_ledger_fingerprints`, `classification_profile`, `descriptor_profile`
and `holding_order`. Metric and currency-risk choices remain the explicit
selections captured by each ledger; no default or caller policy override exists.

Construction replays both public projections and public ledger composition,
checking complete captured **and live** contents, typed originals, supported v1
bindings and exact source-ordered occurrence coverage. A recursive typed guard
rejects mutable/subclass impostors before caller equality or serialization can
mask their structure. Full live-content comparison remains mandatory even for
valid typed values that compare equal (such as signed zero). Fingerprints alone are
insufficient. It validates every snapshot before exposing a reader, rejects
duplicate dates, invalid required descriptors/allocation, unknown lexical keys,
trim-induced portfolio identity collisions and rejected five DTO metrics.
Unused-field diagnostics remain unchanged, not waived. Allocation is unchanged
in percentage points; zero-weight holdings and duplicate occurrences survive.

The original-label or fixed model-only lexical view is separate from C's
original-only classification metadata. Exact 10/24 keys, same-column canonical
identity keys, lookup operations and policy/version/approval identities are
captured with evaluated content fingerprints. Sub-asset output stays in the
sidecar. No legacy database fallback or inherited shortlist authority exists.
Historical candidate construction remains `approved_shortlist_mapping_manifest=None`;
the unchanged public risk registry still enforces exact anomaly bindings.

The frozen reader returns ascending dates, the greatest date and fresh holdings
lists for exact dates only. `snapshot_provenance(date)` exposes frozen full-ledger,
field/raw/normalized/DTO/disposition provenance and a complete zero-based
`dto_to_source` permutation. Source mode leaves order intact; key mode uses selected
DTO UTF-8 binary keys and source-occurrence ties. Neither mutates C, ledger order,
diagnostics or holdings. Canonical `to_json()`, detached `to_dict()` and
`reader_fingerprint` bind the evaluated state; admission is always `NOT_GRANTED`
and status is `NOT_EVALUATED_READER_ONLY`, not eligibility or freshly inspected bytes.

[`Synthetic reader tests`](../../tests/test_candidate_model_reader.py) exercise
both classification/order/metric modes, all approved lexical keys and identity
boundaries, joint risk/missing/zero outcomes, descriptors, collisions, duplicates,
tampering with recomputed identities and deep immutability. Anomaly success uses
restored test-local synthetic proof-shape bindings through the public APIs only;
it does not demonstrate historical reader acceptance or expose a production seam.
That synthetic implementation slice performed no retained reader check or
advisor/ranking execution. The separately authorized acceptance below does not
wire consumers or grant operational authority.

### Bounded retained-corpus reader acceptance evidence

Local recovery package `model-reader-retained-acceptance-v1.6f_7pei7` preserves the
actual `audit_readers.py` harness, per-workbook records, `evaluated/acceptance.json`,
readable report, metadata-only saved-result verification, source/input fingerprints,
runtime versions and reproduction instructions. The report contract is
`CANDIDATE_NATIVE_MODEL_READER_RETAINED_ACCEPTANCE` v1, status `COMPLETE_PASS`, at
source revision `c4ef67487221c7ed0b649a942117787c334b335f`. Evidence identities:

- Report SHA-256: `3e85be6ae922e76abb5cfeef59a3bc77b2f2f5fa1fa01771cc0e04d11eed928d`.
- `SHA256SUMS` SHA-256: `9ca0d66b067c2748bdd758a6b95fa63e0750bde1fc14c5e8b58b34ad038d7dc3`;
  all 66 checksum entries and the index self-check were verified.
- Actual harness SHA-256: `72d56c5d8b9448b34a72ff8e6aeb20ce111944d9c5e6e2ed7adf144aaafa9113`.
- Canonical report fingerprint: `285afa8f8d0811ce1514d0d4d68cd8cd23331a28ff38acc64b17b7e08664a185`.

This record was checked against the saved harness/results without executing them,
parsing workbooks or connecting to a database. All 19 recorded source fingerprints
match the source revision, including dependency declarations and the published
inventory; all 33 input hashes/lengths match that inventory and retained bytes.
The exact examined selections were:

| Selection | Examined value |
|---|---|
| Metric projection | `MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1` |
| Currency-risk projection | `MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1` |
| Classification profile | `MODEL_CLASSIFICATION_LEGACY_LEXICAL_V1` |
| Descriptor profile | `ORIGINAL_MODEL_DESCRIPTORS_V1` |
| Holding order | `MODEL_READER_LEGACY_KEY_SOURCE_TIE_ORDER_V1` |
| Optional shortlist mapping | `approved_shortlist_mapping_manifest=None` |

The saved run used the actual public parser, normalizer, projections, composition
ledger and reader with unchanged production bindings. Each inventory hash was
supplied explicitly; candidate and ledger fingerprints came from actual complete
public results, not fabricated identities. The evaluated anomaly-registry
fingerprint was `45bdd829e31a5f902f576e65ed1e83d167f38b80899d4ef9a2fbc6b694d5e325`.
All 33 single-date readers accepted, with zero rejections/discrepancies, followed
by one atomic all-date reader accepting all 33 dates. All three public reader
methods, exact unavailable-date rejection, ascending enumeration/latest date and
per-date loads were checked; the atomic loads matched the singleton results.

The saved accounting covers 5,283 holdings across 408 date/portfolio identities,
all twelve DTO fields (63,396 checks) and thirteen sidecar fields including
sub-asset (68,679 bindings). Compatibility omitted exactly 12,072 numeric-zero
metric observations, preserving 50,776 nonzero observations and 548 missing
metric cells. Currency risk accounted for 4,968 translations, 291 preserved
missing states and 24 exact manifest-bound anomaly dispositions. Originals,
diagnostics, complete ordering permutations, source identities and both sheets
remained intact: 10,833 shortlist occurrences and 327,603 typed fields. Four
representative in-memory repeats were byte-identical. Three equal
portfolio/ISIN/product key groups retained three extra separate source-index-tied
occurrences; deterministic ties do **not** prove legacy equal-key ordering.
No zero-weight holdings were observed: that behavior remains synthetic-test
coverage only, not exercised retained-corpus evidence. No unknown/invalid
classification, descriptor/allocation or portfolio-trim-collision census issues
were recorded. Observed lexical coverage does not validate semantic taxonomy,
complete approved-table coverage or legacy classification-field equivalence.

Reproduction requires the exact clean checkout/import source, all 33 externally
retained hash-bound XLS files, the unchanged prior ledger and typed-anomaly
packages `model-ledger-retained-acceptance-v1.82oxMA` and
`model-currency-risk-typed-v1.FyqXIB`, a compatible configured environment and fresh
nonexistent output as specified in `REPRODUCE.md`. No database is needed. The
saved full distribution versions describe this execution (Python 3.12.7,
xlrd 2.0.2), not earlier audits. The actual
harness bounds each dated pipeline/repeat to 90 seconds and atomic construction
to 240 seconds; it refuses SQLite connections. Only these profiles and
compatibility metrics were examined. Findings are published-parser observations,
not a new independent raw-BIFF, formula or strict/recovery inspection; historical
diagnostics/audit fields remain unchanged.

This establishes **retained reader acceptance only**, not legacy-reader field/
order compatibility or integrated advisor/ranking equivalence. No legacy reader,
advisor or ranking comparison was executed. Neither this result nor the earlier
separate metric/currency-risk comparisons establishes integrated equivalence,
complete currency-risk information or hedging of missing/partial labels. Further
comparison execution requires separate authorization; consumer activation,
eligibility composition and admission remain pending. ADR-007 stays `Proposed`;
the report and every reader retain admission `NOT_GRANTED`.

### Later retained integration validation contract (not executed)

The pure immutable same-candidate ledger has synthetic validation and bounded
retained acceptance; it preserves fixed production bindings, independent scoped
resolutions and unconditional `NOT_GRANTED`. That ledger does not construct a reader. The
pure facade now has synthetic validation and the separate bounded retained-reader
acceptance above, with mandatory approved profile choices.
A retained reader-equivalence claim still requires separately authorized checks.
No DB adapter or active-consumer integration is present.

Later synthetic integration checks must include both metric selections, all
twelve metrics, joint metric-zero/currency-risk outcomes, nonzero and missing
states, excluded cell types, unknown model classifications, allocation zero,
text normalization boundaries, order-sensitive float sums, exact duplicates,
changed fingerprints/versions/coordinates/occurrences, cross-candidate joins,
projection failures and deterministic serialization/deep immutability. Public
anomaly-success tests may use a restored test-local registry only under existing
test conventions, never a public override or claimed historical acceptance.
Pinned real anomaly candidates with changed optional mapping inputs must reject.
Test date enumeration/latest/unavailable/conflicting dates, fresh returned lists,
and no legacy fallback or fabricated file-backed provenance.
The separately authorized synthetic facade tests now cover exact
classification-table/canonical-identity and unknown-key tests, raw-label trim
collisions, both explicit order profiles, UTF-8/non-ASCII and equal-key source
ties, complete permutation provenance, and failures on altered sidecar bindings.
Do not change either original-only candidate classifications or fixed anomaly
bindings to make those tests pass. Pure construction cases are covered by the
synthetic facade tests above; order-sensitive advisor calculations and retained
equivalence remain unexecuted.

A separately authorized all-date comparison must use the exact retained inputs,
recorded historical construction, both public projections on each same C, actual
legacy reader on the independent baseline side, and actual production advisor/
ranking functions. Verify rather than assume the historical 33 workbooks/dates,
5,283 model rows, 408 date/portfolio identities, 10,833 shortlist occurrences,
327,603 source fields and 12,072 metric-zero omissions. Account for every row
and all 21 model fields, all twelve projected metrics and every reader DTO field,
original types/missing states, duplicate multiplicity, source and consumer order,
both sheets/diagnostics, allocation, all mapping/disposition identities and
failures. Report exact field mismatches and order permutations; do not align
away a loss or silently accept different descriptors because ranking matches.

Compare complete per-date/per-portfolio indicator values and availability,
coverage/thresholds, ranking eligibility/reasons, score contributions/scores,
ordering, tie groups/tie-breaking, warnings/unavailable metrics, winners and
alternatives. Compatibility mode is the legacy comparator; original mode has
intentional metric-zero differences, reported independently. Include repeat
determinism checks. Any rejection blocks that date's comparison visibly; no
partial date, excluded holding, legacy descriptor borrowing, forced expectation,
or unapproved tolerance can yield equivalence. Any genuine intentional difference
needs its own reviewed disposition before a bounded equivalence statement.

The currency-risk coverage caveat remains: legacy full coverage coexisted with
missing labels for 160 identities and partial labels for 102 (possibly overlapping
groups). Neither missing nor partial labels establish hedging or complete risk
information. Even a future passing integrated advisor comparison would not prove
other reader workflows, admission eligibility, consumer activation or cutover.
The later legacy-reader/advisor/ranking integration comparisons in this subsection
remain unexecuted; neither synthetic checks nor retained reader acceptance
establish their results.

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
| `Devizakockázat` | Both | Nullable original text; non-text anomaly remains a diagnostic, not a coerced label | Model currently has legacy-compatible English/`NULL`; shortlist is currently raw JSON only | Model-only lexical and exact 24-occurrence disposition policies approved 2026-10-07, unimplemented. No approved cross-workflow or shortlist mapping. Unknown/anomalous present values remain ineligible without the required occurrence-level disposition |
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
| BIFF `number` equal to zero, model | `0.0` observation plus the adapter's still-current `UNRESOLVED_MODEL_ZERO_SEMANTICS` diagnostic | Explicit compatibility projection omits the metric observation; original remains `0.0` | **Policy approved 2026-09-26; pure projection implemented, consumers pending.** It is not a statement that the source failed cleaning and must never apply to shortlist or allocation |
| BIFF `blank` | No observation / SQL `NULL` | `NULL` | Eligible; retain `blank` source type |
| BIFF `empty` | No observation / SQL `NULL` | `NULL` | Eligible; retain `empty` source type |
| Text empty string | No observation / SQL `NULL` | `NULL` | Eligible only with an explicit `EMPTY_TEXT_AS_MISSING` diagnostic; retain text source type |
| Text `"0"` or other numeric-looking text | None | None | Reject numeric normalization; no implicit decimal-comma or string coercion |
| BIFF error or error-like text | None | None | Reject metric candidate; preserve raw/error text |
| BIFF boolean or date serial | None | None | Reject metric candidate even if the payload is numerically representable |
| Non-finite number | None | None | Reject metric candidate |

### Approved model-zero policy; pure projection implemented

**Decision status: explicitly approved by the user on 2026-09-26; pure projection
implemented, consumer activation pending.** The approved policy is
`MODEL_METRIC_ZERO_HANDLING_POLICY_V1`. It has two outputs that must never be
collapsed into one nullable scalar:

1. `MODEL_METRIC_ORIGINAL_V1` preserves every accepted finite BIFF number as
   the normalized original, including numeric zero; the typed raw value retains
   any signed-zero representation. The typed cell, raw value, format,
   coordinate, field occurrence, workbook hash, sheet/row reference, and
   normalization-candidate fingerprint remain the evidence authority.
2. `MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1` is a separately named
   projection. For an original numeric zero it emits disposition
   `OMITTED_NUMERIC_ZERO_FOR_LEGACY_COMPATIBILITY` and no metric observation;
   for a finite non-zero number it emits the identical unscaled number. It does
   not modify the original observation or claim that the provider meant
   “missing.”

The policy applies only to `MODEL_PORTFOLIO` fields with these exact header and
metric-identity pairs: `YTD`/`YTD`, `1yr`/`RETURN_1Y`,
`3yr`/`RETURN_3Y`, `5yr`/`RETURN_5Y`,
`1Y Sharpe`/`SHARPE_RATIO_1Y`, `3Y Sharpe`/`SHARPE_RATIO_3Y`,
`5Y Sharpe`/`SHARPE_RATIO_5Y`, `1Y Vol.`/`VOLATILITY_1Y`,
`3Y Vol.`/`VOLATILITY_3Y`, `Down. risk`/`DOWNSIDE_RISK`,
`Info. ratio`/`INFORMATION_RATIO`, and
`Max. drawd.`/`MAXIMUM_DRAWDOWN`. The legacy English column labels are target
aliases, not additional source headers. `Hányad (%)`, every descriptive field,
and the entire shortlist role are outside scope.

The earlier retained-corpus audit—not a measurement made by this approval
record—found 12,072 numeric-zero cells in this exact model metric scope across
5,283 rows and 33 workbooks. That count supports the approved scope but does not
establish that any provider zero means unavailable data.

#### Projection states and provenance

The compatibility projection must record its name and version, the input
normalization contract and candidate fingerprint, workbook SHA-256, exact sheet
name and role, row and field occurrence IDs, source coordinate and header,
metric identity, original type and value, output disposition/value, and a
deterministic projection fingerprint. Its states are:

| Normalized-original state | Original output | Compatibility output |
| --- | --- | --- |
| Finite BIFF number, non-zero | Same unscaled number | Same unscaled number, `PRESENT` |
| Finite BIFF number, numeric zero | `0.0`, `PRESENT` | No observation, `OMITTED_NUMERIC_ZERO_FOR_LEGACY_COMPATIBILITY` |
| BIFF `blank` or `empty` | No observation with exact source-missing type | No observation, `SOURCE_MISSING`; never relabel as omitted zero |
| Empty text | No observation plus `EMPTY_TEXT_AS_MISSING` | No observation with that diagnostic; never relabel as omitted zero |
| Text `"0"` or numeric-looking text | Rejected | Rejected; no compatibility coercion |
| Error, error-like text, boolean, date, or non-finite number | Rejected | Rejected; no compatibility output |

An implementation may serialize a compatibility omission as SQL `NULL` or as
an absent metric row, but it must retain the disposition and original binding.
`NULL`, no observation, source-missing, omitted zero, and rejected input are
therefore distinct contract states even where an existing reader represents
more than one of them as Python `None`. No state removes the source occurrence
or holding: a compatibility omission excludes only that metric observation
from a metric-specific consumer calculation.

#### Consumer selection and traced effects

Selection must be explicit and provenance-bearing:

- an evidence or research consumer selects `MODEL_METRIC_ORIGINAL_V1`;
- a separately approved legacy-compatible reader may select
  `MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1`; and
- absence of a supported selector, silent fallback, schema auto-detection, or
  mixing both projections in one result fails closed.

The operational importer currently calls
`DB_creation/excel_processing.py::replace_numeric_zeros` before its flat SQLite
insert, so every numeric zero in that prepared row becomes `None`. That helper
is broader than the approved policy because it also reaches non-metric numeric
fields; the approved compatibility rule intentionally does not copy that
breadth. Phase 1 `database/model_portfolio_phase1.py::_parsed_number` and the
synthetic Phase 3B.1 model writer's `_number_or_none(..., zero_is_null=True)`
also turn zero into no model metric observation, while the Phase 3B.1 shortlist
path explicitly passes `zero_is_null=False`. The flat
`database/repository.py::ModelPortfolioRepository` and schema-v3 reader adapters
then represent SQL `NULL` or a missing metric observation as
`HoldingObservation` `None`.

Current ranking reads only five of the twelve identities: `RETURN_1Y`,
`SHARPE_RATIO_1Y`, `VOLATILITY_1Y`, `DOWNSIDE_RISK`, and
`MAXIMUM_DRAWDOWN`. `metrics/portfolio.py::_weighted_metric` excludes `None`
holdings and reduces coverage; if every allocated holding is absent, the
portfolio metric is unavailable. The active ranking requires at least 70%
coverage for volatility and maximum drawdown, while an unavailable optional
scoring metric is excluded for all eligible portfolios with a warning. The
other seven scoped fields do not enter the current `HoldingObservation`
ranking contract.

For example, for 60% and 40% holdings with source volatility values `0.0` and
`0.10`, the original projection would produce `0.04` at 100% coverage. The
compatibility projection would omit the zero, produce `0.10` at 40% coverage,
and the current eligibility rule would reject the portfolio for insufficient
volatility coverage. A single 100%-weight zero would be an available `0.0` in
the original projection but unavailable in the compatibility projection. A
blank cell and text `"0"` would not follow either numeric-zero path: the former
is source-missing and the latter is rejected.

These effects follow directly from the current parser, Phase 1/3B.1, reader,
metric, eligibility, and scoring code. The original policy-design task did not
run retained data. A subsequent bounded comparison established the narrower
metric/ranking result below; full candidate-native reader equivalence remains
unproven.

#### Bounded retained-corpus comparison evidence

The completed read-only comparison evaluated published revision
`c0e3d51cc3d46cfc3c0ee29746e51c87e3ca5b0c`. Its unchanged private
`comparison.json` has SHA-256
`4155b6d70e0bfae04202d680ab497ec7fd64372c0624f85342f370204b2a5493`
and canonical payload fingerprint
`0f4e3d634f9c15c84694ce5870aa88af230ca43fbbd98496ffc9cd0bac28e749`.
The durable recovery package `model-metric-comparison-v1-preserved.YHDm4b`
preserves the actual successful harness, reports, input/source fingerprints,
dependency records, reproduction instructions, and relative checksums outside
Git. Preservation verified their bindings without rerunning the corpus.

Reproduction requires the external retained workbooks and two databases at
their recorded hashes. The original complete dependency freeze was not
recorded: the preserved lock and separately labelled preservation-time package
versions must not be presented as the full historical execution environment.
The unchanged analysis script assumes a fixed adjacent output layout and must
not be rerun into the preserved package; its reproduction instructions describe
the required fresh layout. These limitations do not extend the comparison's scope.

- All 33 workbooks/dates and 408 date/portfolio identities were evaluated:
  5,283 model rows and 10,833 shortlist occurrences, with no skipped sheet or
  occurrence. The exact leading-space sheet names and duplicate multiplicity
  remain preserved.
- All twelve model metrics matched actual legacy prepared values/availability:
  zero mismatches across 63,396 cells after exactly 12,072 scoped numeric-zero
  omissions. Nonzero values, allocation, shortlist fields, original evidence
  and diagnostics remained unchanged. The exclusions absent from this corpus
  are not newly validated by this retained-data comparison.
- The temporary in-memory bridge used the five fields consumed by current
  ranking (`1yr`, `1Y Sharpe`, `1Y Vol.`, `Down. risk`, `Max. drawd.`), with
  **legacy descriptive inputs held fixed** through actual `prepare_rows`.
  Production readers and `CapitalPreservationAdvisor` exercised actual metric,
  eligibility, scoring and ranking functions. Compatibility matched availability,
  coverage thresholds, eligibility/reasons, scores, ordering, warnings, winners
  and four observed tie groups exactly. The other seven fields were compared
  for value/availability, not exercised as ranking inputs. The existing
  analytical model-only reader also matched legacy results at all 33 dates.
- Original mode intentionally produced 49 additional ranking-eligible
  portfolio/date identities, 405 changed scores, changed ordering on all 33
  dates, and 11 changed winners. These are explained projection effects, not
  compatibility mismatches or proof that provider zeros mean missing data.

This establishes only the exercised twelve-metric comparison and five-metric
ranking bridge on the bound inputs/configuration. Full candidate-native
descriptive/reader equivalence, future-workbook equivalence, integrated use of
the separately implemented model currency-risk projection, shortlist translations,
sustainability dispositions, `3yr`/`5yr` interpretation, consumer activation, correction
preservation, overall eligibility/admission and authority/cutover remain pending.
The comparison neither applied shortlist corrections nor composed recovery/formula
approvals into eligibility. All results retain admission `NOT_GRANTED`;
ADR-007 remains `Proposed`.

#### Scope, acceptance, and rejection conditions

The approved scope is the twelve exact model fields both for the 33 inventoried
workbook hashes and for future workbooks that independently satisfy the same
`BIFF_XLS_V1` model-role, exact-header, typed-cell, and normalization contracts.
The corpus count is not projected onto future data. Changed or future bytes
inherit no recovery exception, formula decision, correction binding, anomaly
disposition, eligibility, or admission approval.

Any implementation conforms to the approved policy only when original zero
evidence is durable before any compatibility projection, the explicit
projection identity and provenance are available to every consumer, and
invalid or unsupported typed states fail closed. It is rejected if a zero is
rewritten in the original layer, a projection is selected implicitly, the
original and compatibility states cannot be distinguished, a field outside the
twelve-field model scope is changed, or a text/missing/error state is coerced
into the numeric-zero rule.

Alternatives considered are: preserve originals without a compatibility view,
which would not preserve the code-traced legacy consumer behavior; rewrite
originals to `NULL`, which destroys evidence; infer zero meaning per financial
metric, for which no provider evidence exists; reuse shortlist corrections,
whose exact dataset and authority do not cover model rows; or limit the rule to
the 33 hashes, which is more conservative but would force the same typed
decision for each future workbook. The approved two-output contract preserves
evidence while making legacy behavior explicit and selectable.

Recorded approval wording (explicit user authorization, 2026-09-26):

> I approve `MODEL_METRIC_ZERO_HANDLING_POLICY_V1` for the twelve enumerated
> `MODEL_PORTFOLIO` metrics on the 33 inventoried workbook hashes and future
> workbooks that independently satisfy the same v1 typed source contract. A
> finite BIFF numeric zero must remain an original `0.0` observation. The
> separately identified
> `MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1` projection may omit that
> observation only when explicitly selected and fully provenance-bound. This
> does not assert that provider zeros mean missing data, does not apply to
> allocation, shortlist, text zero, missing, error, or non-finite cells, and
> does not authorize implementation, admission, correction rebinding, ranking
> or default changes, migration, or cutover.

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

### Currency-risk inventory and translation authority

| Original source value | Model rows | Shortlist rows | English category / candidate | Authority / disposition |
| --- | ---: | ---: | --- | --- |
| `Nincs fedezve` | 3,345 | 7,917 | `Unhedged` | Model-only lexical policy approved 2026-10-07, implemented in the pure projection only; shortlist/cross-workflow mapping remains unapproved |
| `nincs fedezve` | 6 | 11 | `Unhedged` after case-normalized match | Same model-only approval; preserve original case; shortlist remains unapproved |
| `Fedezve` | 1,515 | 1,556 | `Hedged` | Same model-only approval; does not establish target currency, hedge ratio, or coverage period |
| `Részben fedezve` | 102 | 289 | `Partially Hedged` | Same model-only approval; degree and period remain unknown |
| Source-missing | 291 | 1,060 | `NULL` / unknown | Preserve BIFF blank versus empty in the envelope |
| Numeric `2`, `3`, or `4` | 18 | 0 | None | Known model anomaly; raw value plus diagnostic required; no coercion |
| Text `VALUE!` | 6 | 0 | None | Known model anomaly; raw value plus diagnostic required; it is not a BIFF error cell |

The three shortlist `Fenntarthatóság = Hosszú kötvény alap` values likewise
have no authorized repair. They remain original text plus
`ANOMALOUS_SUSTAINABILITY_VALUE`; they must not be moved into classification or
discarded.

The model-only disposition policy for the exact 24 currency-risk occurrences
is approved below and is pinned in the separate pure projection's immutable
registry; no admission path is installed. Typed occurrence evidence alone is
not an executable artifact. The three
sustainability warnings still require separate policy approval. Any future
admission would independently require a reviewed, hash/row/coordinate-bound
disposition artifact and all other gates; these approvals authorize neither
new warning values nor future anomaly occurrences.

### Model currency-risk translation and anomaly policy

**Policy approved by explicit user authorization on 2026-10-07; pure projection implemented.**
The independently approved identities are
`MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1`,
`MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1`, and the separately selected
`MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1`. The user stated:
“Approve all three within their reviewed scopes.” The approval defines only
these policy boundaries. The subsequent implementation task authorizes only
the pure projection and synthetic validation described below. The separately
authorized bounded comparison is recorded below; further comparison execution,
consumer activation, and admission remain unauthorized. No consolidation
eligibility decision is granted.
They concern only `MODEL_PORTFOLIO` / `Devizakockázat`, not
shortlist currency risk, sustainability, classification pairs or metric policy.

#### Evidence and implemented behavior

The inventory above is attributed to the earlier typed-source audit, not a new
workbook run. The preserved [bounded comparison](#bounded-retained-corpus-comparison-evidence)
records 24 model `ANOMALOUS_CURRENCY_RISK_VALUE` diagnostics across all 33
hashes, but deliberately held legacy currency-risk descriptors fixed. It does
not establish candidate-native translation or anomaly equivalence.

The existing local `data/audit/milestone_4_current_data_audit.json` (SHA-256
`0852aca0a2979346f1d4ed9cf804419d0e9684d1458cc0a08a8603a2bb5952db`)
provides a recorded row/header index for all 24 anomalies. It covers only 32
workbooks, omitting 2026-08-26; the 33-workbook comparison records no model
currency-risk anomaly in that omitted file. Its `source_values` are trimmed
strings and do **not** prove BIFF types. Their two workbook hashes and per-file
anomaly counts agree with the preserved comparison and published inventory.
The bounded typed inspection on 2026-10-07 parsed **only these two hash-bound
workbooks** with the published parser at revision
`f7d196373ac911b45952896693446a96f7bb766e`. All 24 coordinates below reconcile
with the older index: three on 2024-10-17 and 21 on 2025-05-09, with no
discrepancies. Six cells expose BIFF text (xlrd type code 1), raw `VALUE!`;
eighteen expose BIFF numbers (code 2), raw parser floats `2.0`/`3.0`/`4.0`
(9/3/6), rather than the older index's strings `"2"`/`"3"`/`"4"`.
No type is inferred from that index and no value is coerced or repaired.

The actual extraction harness, typed occurrence manifest, reconciliation and
reproduction instructions are preserved outside Git in recovery package
`model-currency-risk-typed-v1.FyqXIB`. Manifest
`inspection/typed-occurrence-manifest-v1.json` has SHA-256
`c4d678e0c90abac49337bf1f7547816b45134b6e6e7886ab4aacf1ee594f123a`.
Each entry binds the exact hash, leading-space sheet name/index, header,
coordinate, parser source reference, occurrence/field IDs, row/envelope
fingerprints, exposed type/raw value and format metadata. Duplicate occurrences
remain separate. These are parser/xlrd-exposed types, not a new independent
raw-BIFF scan; the parser's recovery and formula-origin limitations remain.
No strict-open test, evidence-gate evaluation, normalizer, projection, ranking
comparison or admission ran. Historical candidate fingerprints in the manifest
are reference-only, not newly evaluated candidate bindings.

Code tracing at revision `f7d196373ac911b45952896693446a96f7bb766e` establishes:

- `DB_creation/excel_processing.py::VALUE_TRANSLATIONS["Currency Risk"]`
  translates three Hungarian keys and accepts their three English outputs.
  `text_normalization.py::normalized_key` applies string conversion, trimming,
  NFC and casefold for this legacy lookup. `_translate_value` passes through
  `None`/pandas-missing values, returns `None` for lookup keys `2`, `3`, `4`,
  `value!`, and raises `ValueError` for other labels. `prepare_rows` ultimately
  turns pandas missing values into `None`, used for SQL `NULL` by the legacy
  importer. This is implemented compatibility behavior, not an approved meaning
  for provider codes. Its string-based lookup is not a typed-source rule:
  numeric `2.0` stringifies differently from integer `2`; blindly passing BIFF
  values into this helper is not evidence of equivalence.
- `workbook_source/normalization.py::_normalize_governed_text` preserves raw
  cells. Blank/empty cells yield `SOURCE_MISSING`; whitespace-only text adds
  `EMPTY_TEXT_AS_MISSING`. Non-text cells retain their source evidence but yield
  normalized `None` and an anomaly diagnostic. Nonempty text remains trimmed
  original text: unknown text such as `VALUE!` receives the anomaly diagnostic,
  and every nonempty currency-risk text also receives
  `UNAPPROVED_CURRENCY_RISK_TRANSLATION`. No English field is produced. The
  current comparison/projection does not resolve these diagnostics. That
  diagnostic identifier predates this approval and remains unchanged in the
  unmodified implementation; it does not revoke the subsequent policy approval.
- `database/repository.py::load_holdings` reads the stored English/NULL value
  into `HoldingObservation.currency_risk`. The schema-v3 model reader in
  `database/migrations/model_portfolio_dry_run.py` similarly reads
  `observed_currency_risk`, which the historical migration populated from the
  legacy holding; immutable raw JSON retains the separate source evidence.
  No current candidate-native descriptive reader exists.

#### Approved mapping and typed-state requirements

For the separately and explicitly selected pure projection, the approved lookup is only on
BIFF text,
using NFC, trim and casefold while preserving the exact original text and type.
The allowed Hungarian keys are exactly `fedezve`, `nincs fedezve`, and
`részben fedezve`; no synonyms, numeric codes, accent removal or fuzzy matching.
Case/Unicode/outer-whitespace normalization is a declared lookup operation, not
a rewrite of original evidence. The English labels name only the reported
category; none establishes investor base currency, hedge target, hedge ratio,
instrument-level exposure, effectiveness or observation period.

A mapping artifact must pin the policy name/version, model role/header,
the three key/output pairs, declared lookup operations, approval reference and
manifest fingerprint. Each projected field must retain its original candidate
binding, raw cell and occurrence identity, chosen projection/version, mapping
fingerprint and disposition reason. Policy approval does not install or validate
that executable artifact; the separate pure implementation below now pins it.
The current normalizer does not apply NFC to this field; a translation match
must not silently clear an existing anomaly diagnostic caused by a Unicode
variant. Any additional anomaly disposition remains outside the exact 24-cell
scope and requires its own review. English output is a separately bound
consumer attribute, not replacement of `original_currency_risk`.

| Source evidence / type | Earlier model count | Implemented legacy behavior | Approved policy output / unresolved state | Decision or limitation |
| --- | ---: | --- | --- | --- |
| Text `Nincs fedezve`; text `nincs fedezve` | 3,345; 6 | `Unhedged` after legacy lookup | `Unhedged`, with translation identity | Implemented only in the explicitly selected pure projection; no active consumer |
| Text `Fedezve` | 1,515 | `Hedged` | `Hedged`, with translation identity | Does not establish complete hedging against an investor's currency |
| Text `Részben fedezve` | 102 | `Partially Hedged` | `Partially Hedged`, with translation identity | Hedge fraction and period remain unknown; never assign a numerical fraction |
| Blank or absent BIFF cell | 291 source-missing cells in aggregate | Reader may collapse blank/empty into missing; prepared value is `None` | No English value; `SOURCE_MISSING`, retain exact missing type | Not an anomaly and not evidence of hedging |
| Empty or whitespace-only BIFF text | No separately reported count | If empty text survives reading, categorical lookup raises; pandas reader representation must not be inferred | No English value; existing `EMPTY_TEXT_AS_MISSING` semantics | Preserve raw text; no claim of retained-data equivalence for this state |
| BIFF number `2`, `3`, `4` | 9; 3; 6 | The exercised retained legacy path produces `None`; string-key matching is representation-sensitive | No English meaning; `KNOWN_ANOMALY_UNINTERPRETED` only for the exact occurrences below | Pure projection permits only the exact bound reason-bearing compatibility `None`; no numeric/string coercion or consumer activation |
| BIFF text `VALUE!` | 6 | `None` for legacy key `value!` | No English meaning; exact-occurrence anomaly state | Text, not the BIFF error `#VALUE!`; no repair or error-type relabelling |
| Other nonempty text, including numeric-looking text `"2"`/`"3"`/`"4"` | Not reported in retained model scope | Legacy lookup discards those numeric-looking keys; other unknown labels raise | `UNKNOWN_LABEL`; no authorized English or consumer fallback | Do not broaden the 18 numeric-cell dispositions to text codes |
| Already-English text `Hedged`, `Unhedged`, `Partially Hedged` | Not reported in retained model scope | Accepted and canonicalized | Not included in the approved Hungarian-input policy; unresolved unless separately approved as identity inputs | Future English-input support is an optional scope extension, not assumed |
| Other numbers, booleans, dates, BIFF errors or non-finite values | Not reported in this model-field inventory | No general typed exception; unsupported lookup usually raises | Preserve evidence and anomaly; no English value or compatibility output | The approved reader-profile definition rejects unsupported typed states; do not infer code meaning |

An unknown label or malformed binding must fail the pure reader-projection
request visibly; it must not become `Hedged`, `Unhedged`, `NULL`, or a silently
excluded holding. All originals and diagnostic records remain available even
when a projection cannot be produced. The policy does not generalize legacy
invalid-string suppression, repair errors, fill missing values, or borrow the
approved classification or model-zero authorities.

#### Approved exact anomaly disposition scope

Only these two full workbook hashes are approved for the known-anomaly
disposition, on exact sheet ` modell portfóliók`, header `Devizakockázat`:

- `PB_Modell_Portfoliok_es_Shortlist_20241017.xls`:
  `fadecf0acb478cb4ab4596d1cd509390f0a1d221ee811e05528e5ad6cc173abe`.
- `PB_Modell_Portfoliok_es_Shortlist_20250509.xls`:
  `a74151e567b5a4b095f302e19411ffac1d191d794a0a1de5898a85f2268ebbed`.

| Workbook date / bound hash above | Original type/value | Recorded coordinates | Approved policy disposition (pure projection only) |
| --- | --- | --- | --- |
| 2024-10-17 | Text `VALUE!` | `H42`, `H90`, `H143` | Retain original; uninterpreted anomaly; optional explicit legacy-reader `None` |
| 2025-05-09 | Text `VALUE!` | `H33`, `H80`, `H132` | Same; no formula/error inference |
| 2025-05-09 | Number `2` | `H4`, `H27`, `H37`, `H47`, `H75`, `H89`, `H102`, `H130`, `H143` | Same; no meaning assigned to code `2` |
| 2025-05-09 | Number `3` | `H30`, `H82`, `H137` | Same; no meaning assigned to code `3` |
| 2025-05-09 | Number `4` | `H17`, `H28`, `H68`, `H77`, `H120`, `H131` | Same; no meaning assigned to code `4` |

The typed occurrence evidence is complete for this approved 24-occurrence
policy scope, **not an admission exception**. The separate executable registry
references the typed evidence and binds every entry to the workbook
SHA-256, source filename/date,
role, exact sheet name/index, header and cell coordinate, physical source row,
actual occurrence index/ID, row fingerprint, field occurrence ID, original BIFF
type/raw value and format, envelope/candidate fingerprint, policy/projection
identity, and `LEGACY_ANOMALY_AS_NONE_WITHOUT_INTERPRETATION` reason. Matching
only an ISIN, date, value, row count or caller-supplied approval flag is
insufficient. Changed bytes, wrong cell type/value, missing/extra/duplicate
entries, unsupported records or mismatched provenance fail closed. Each source
occurrence and holding is retained; only its projected currency-risk attribute
may be `None`. No correction record or admitted original is overwritten.

The eighteen numeric meanings and the cause of the six text values remain
unknown. A later provider-supported repair would require a separately reviewed
decision, evidence and transformation, not replacement of this original layer.

#### Consumer compatibility boundary and temporal scope

`metrics/portfolio.py::calculate_portfolio_metrics` computes
`unhedged_allocation` using only
`(currency_risk or "").casefold() == "unhedged"`. For a positive allocation
total, `_allocation_indicator` uses **all** allocations in the denominator and
reports coverage `1.0`/available, including holdings with missing or anomalous
currency risk. `Partially Hedged`, `Hedged` and `None` do not contribute to the
unhedged numerator; this is not proof they have zero FX risk. Passing untranslated
Hungarian text directly would fail to count `Nincs fedezve`. The active
`CAPITAL_PRESERVATION_RANKING_POLICY` v1.0.1 gives this indicator weight 0.15,
lower-is-better, so changing labels or unknown handling can change normalized
scores, ties, ordering and winners, even without a metric-zero change.

By code tracing only, a 60% `Unhedged` holding plus a 40% missing-risk holding
produces legacy unhedged allocation `0.60` at reported full coverage; replacing
the first label with untranslated `Nincs fedezve` would produce `0.0`.
These are explanatory examples, not executed tests. Neither result measures
complete economic currency exposure. The recommendation for future truthful
reporting is to keep unknown exposure explicit rather than call it hedged;
choosing a coverage-aware financial measure or changing ranking behavior is a
separate policy decision, excluded here. For a faithful historical comparison,
the approved explicitly selected reader-profile definition would supply
canonical English
labels or reason-bearing `None` and use the existing calculation unchanged.
Policy approval does not authorize executing that comparison or activating the
profile operationally. Missing and partially hedged labels do not prove hedging
or complete currency-risk information.

Other consumers require separate validation: `features/dataset.py::portfolio_structure`
makes unhedged exposure unavailable if **any** currency-risk label is missing,
but otherwise counts the same exact English label without validating its meaning.
`prospective/validation.py::_holding_payload` retains currency-risk values in
source snapshot lineage. The separate construction path's `_currency_risk`
uses the legacy dictionary and rejects unsupported labels; it does not inherit
these model-only anomaly dispositions. No feature, prospective, construction,
shortlist, ranking or historical-reader behavior is changed by these policy
approvals.

Approved temporal scope: the three Hungarian text mappings may apply to the
33 inventoried model workbooks and future model sheets independently satisfying
the same v1 typed-source/provenance contract and the declared lookup rules.
Unknown/future labels remain unresolved. The 24 anomaly-to-`None` compatibility
dispositions apply **only** to the enumerated occurrences in the two exact
hashes, never to future numeric codes or text `VALUE!` cells. Future bytes gain
no recovery/formula exception, correction binding, eligibility or admission
authority. Full candidate-native reader equivalence remains unperformed; it
also depends on other descriptive-field contracts, not currency risk alone.

#### Recorded independent policy approvals (2026-10-07)

The explicit user authorization “Approve all three within their reviewed scopes”
adopts the following reviewed scope wording. At approval time it expressly left
implementation, comparison execution, consumer activation, and admission
unauthorized. The three decisions remain independent; the comparison-profile
definition must use the approved mapping/disposition versions, not bypass them.

1. **Translation scope:** “Approve `MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1`
   for model `Devizakockázat` only: BIFF text keys `fedezve`, `nincs fedezve`,
   `részben fedezve`, matched by NFC/trim/casefold, yield `Hedged`, `Unhedged`,
   `Partially Hedged` respectively in a separate provenance-bound projection.
   Preserve exact originals. Cover the inventoried and independently conforming
   future model sheets; approve no hedge ratio, synonym, numeric-code meaning,
   shortlist translation or English-input extension.”
2. **Known anomalies:** “Approve `MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1`
   only for the 24 typed occurrences enumerated above, conditional on the full
   reviewed occurrence-bound manifest and fail-closed checks. Retain them as
   uninterpreted anomalies, preserving originals and diagnostics. Permit a
   separately selected legacy-reader attribute `None` with the stated reason;
   do not remove any occurrence/holding, repair a value, waive other gates or
   extend this disposition to changed/future bytes.”
3. **Comparison profile:** “Approve the definition of the explicit
   `MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1` profile for a separately
   authorized future candidate-native comparison, acknowledging the current
   indicator's treatment of missing/partial labels. It must bind the approved
   mapping/disposition versions and preserve unresolved diagnostics. This is
   not approval of complete FX-risk measurement, a ranking-policy change,
   implementation, test execution, consumer activation or admission.”

Alternatives considered were to keep translation entirely
unresolved, block all anomalous projections until provider clarification, or
approve additional identity inputs such as already-English labels separately.
Such identity-input extensions still require separate approval. Unsupported
anomaly dispositions must not be bypassed by reusing the global legacy
dictionary. These three approvals define only their policy boundaries;
the now implemented pure projection does not authorize a bounded reader-equivalence
test. Sustainability, metric interpretation,
eligibility composition, admission, migration and cutover remain outside scope;
ADR-007 stays `Proposed`, and admission stays `NOT_GRANTED`.

#### Implemented pure currency-risk projection

The subsequent synthetic-only implementation task authorizes
[`model_currency_risk_projection.py`](../../src/portfolio_advisor/workbook_source/model_currency_risk_projection.py),
not comparison execution or consumer integration. Its in-memory API requires
both explicit keywords, with no default or caller-supplied policy/registry:

```python
project_model_currency_risk(
    candidate,
    projection=MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1,
    expected_candidate_fingerprint=candidate.candidate_fingerprint,
)
```

The immutable `MODEL_CURRENCY_RISK_PROJECTION` v1 result retains the entire
unchanged dual-sheet candidate and every ordered model occurrence, and exposes
one separately bound currency-risk attribute per model row. It records original
typed fields, the approved three-pair mapping/lookup fingerprint, policy versions
and approval date, the fixed 24-entry registry fingerprint and typed-manifest
SHA-256, field-specific disposition/reason and canonical projection fingerprint.
The evaluated registry fingerprint/count are captured immutably at construction,
not recomputed from runtime state when an existing result is serialized.
No holding is excluded. Every result remains `NOT_EVALUATED_PROJECTION_ONLY`
and `admission_approval = NOT_GRANTED`.

The existing model-metric projection's pure v1 validation is reused for deep
immutable types, dated source identity, exact headers/sheets, substantive cell
types/values/coordinates, row fingerprints, occurrence references and replayed
field normalization. Recomputed fingerprints alone cannot conceal malformed
structure. Anomaly matching additionally pins the two complete historical
candidate fingerprints recorded in the preserved metric comparison (reference-only
in the typed inspection), envelope/sheet/header/row/field bindings, exact exposed
floats/text and formats. It rejects changed, missing, extra or duplicate bindings;
every registry entry for the bound workbook must resolve exactly once in source
order, so unused, duplicated or reordered entries also fail closed;
even optional classification-mapping variants of these two recorded candidates
are not substituted. Other conforming model sources may use the lexical mapping,
but inherit no anomaly exception. Unknown labels and unsupported typed states
fail the whole projection request visibly rather than produce fallback `None`.

Blank/absent/empty-text states remain distinguishable through their retained
source cells and missing-value diagnostics. `policy_resolution` identifies only
the applied translation or exact anomaly disposition; it deletes no historical
diagnostic. In particular, an NFC translation match does not resolve an additional
Unicode-variant anomaly diagnostic. Other unresolved metrics, sustainability,
classification, recovery/formula and eligibility issues remain untouched.

Synthetic fixtures test complete public requests and malformed/recomputed
bindings. Public-API success and rejection paths additionally use a test-local
synthetic occurrence registry with all substantive validators still active;
the registry is restored afterwards and no production override is exposed.
Synthetic proof metadata separately tests all 24 fixed anomaly bindings.
Neither technique exercises a complete retained candidate end-to-end or proves
historical acceptance. Input hashes
are declared provenance, not newly inspected current bytes. No retained workbook,
database, filesystem evidence, reader or ranking is accessed by the API; this
slice establishes neither retained-corpus projection equivalence nor complete
currency-risk information. Historical evidence and its `NOT_GRANTED` fields
remain unchanged. Further comparison execution, active selection, eligibility composition
and admission still require separate authority.

#### Bounded currency-risk compatibility comparison evidence

The subsequent read-only comparison used published revision
`5a092ebc4fe68849c41fe6befbe38af6b002cc0b`. Recovery package
`model-currency-risk-comparison-v1.5VCBFa` preserves the actual
`compare_currency_risk.py` harness, imported `acceptance_support.py`, reports,
source/input fingerprints, execution-time dependency versions and
`REPRODUCE.md` outside Git. Its unchanged `evaluated/comparison.json`
(`MODEL_CURRENCY_RISK_LEGACY_COMPATIBILITY_COMPARISON_EVIDENCE` v1) has SHA-256
`4f4d53f5971dba80c58c0765f7208e214a90c15eb07d8186459a41d7d0ec71b6`
and canonical report fingerprint
`9809c0abcec2f2a97c0f70da3f70507a517b66ddbe4f30d7c7c207471c4a72af`.
The package's `SHA256SUMS` has SHA-256
`d5ca8de946dbbeb92b8a7fe2f56a7b9dea5b75e97bab4825ef9d6a61777f0432`;
all 63 entries, saved outcomes and source/input bindings were verified without
rerunning the comparison. Private row-level results are not committed here.

Historical candidate construction used the published parser and normalizer
with the verified workbook hash and **`approved_shortlist_mapping_manifest=None`**.
Every candidate went through the public `project_model_currency_risk` API with
explicit `MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1`, its actual candidate
fingerprint and the unchanged production anomaly registry. Actual legacy
`read_target_worksheet`/`prepare_rows` bound source occurrences to the actual
read-only `ModelPortfolioRepository`, preserving multiplicity and reader order.
The temporary injected reader changed **only `currency_risk`**; metrics,
allocations and every other descriptive input stayed fixed to legacy values.
Actual `CapitalPreservationAdvisor.evaluate` and production metric/eligibility/
scoring/ranking functions used approved ranking policy v1.0.1, not an approximation.

- All 33 workbooks/dates and 408 date/portfolio identities matched: 5,283
  model occurrences accounted for as 4,968 approved translations, 291 preserved
  source-missing states (255 formatted blanks and 36 empty cells), and all 24
  exact anomaly dispositions to reason-bearing `None`. There were zero public-API
  rejections, reader value/type mismatches, advisor discrepancies or blocked dates.
- Currency-risk indicators and coverage, eligibility/reasons, score
  contributions/totals, ordering, warnings, alternatives, winners and four
  observed tie groups matched exactly. Four representative in-memory projection
  and advisor repeats (2024-07-02, 2024-10-17, 2025-05-09, 2026-08-26) were
  deterministic; no second corpus run was performed.
- Both exact leading-space sheet identities, original candidates/cells,
  diagnostics, holdings and occurrence multiplicity stayed intact, including
  all 10,833 shortlist occurrences and 327,603 typed source data fields.
- The legacy indicator reported full coverage for **160 date/portfolio
  identities with missing labels** and **102 with partially hedged labels**.
  These groups may overlap and must not be summed as distinct identities.
  Neither matching historical calculations nor that coverage flag establishes
  complete currency-risk information, hedging of missing/partially hedged
  holdings, hedge fractions, or numeric-code meanings.

Reproduction requires a clean checkout at the exact source revision, its own
imports and recorded configuration, all external retained workbooks and the
legacy database at their recorded hashes, and the unchanged prior acceptance
package `model-currency-risk-retained-acceptance-v1.G7hAUB`. The package includes
hashes, not workbook/database bytes. The actual harness requires a fresh output
directory and its adjacent support module; production-source paths and Git
blobs are checked. Complete installed-dependency versions describe this
execution, not earlier audits. The harness matched full legacy holdings using
source-order queues; individually permuting indistinguishable duplicates cannot
be proven without legacy source coordinates, although multiplicity is preserved
and no identical `HoldingObservation` duplicate groups were observed here.
This is published-parser evidence, not a new independent BIFF/formula/recovery
inspection or waiver of historical diagnostics.

This result is separate from the
[earlier twelve-metric comparison](#bounded-retained-corpus-comparison-evidence),
which held legacy descriptive inputs fixed. Neither result alone nor the two
together proves an integrated candidate-native reader or joint projection
composition. Full candidate-native reader equivalence, consumer activation,
consolidation eligibility composition, remaining policy gates and admission
remain pending. Observed eligibility is existing production-ranking eligibility
only. All results retain admission `NOT_GRANTED`; ADR-007 remains `Proposed`.

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
   enforces that bounded approval without turning the registry into a general
   allowlist.
6. **Formula origin:** cached-value equality does not prove a literal cell or
   formula absence. The user-approved 2026-09-26 sub-decision accepts the
   verifier's complete zero-formula-record finding as sufficient for this gate
   only for the current bytes of the exact 33-hash registry. It makes no claim
   about pre-export calculations, formulas, pasted values, or original literal
   entry. The evidence-gate evaluator enforces this exact retained-byte scope
   but cannot establish upstream authoring history.
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

This validation demonstrates the separation that the user later approved and
the current correction bindings. A later read-only
[BIFF recovery and formula evidence verifier](biff-xls-recovery-formula-evidence-v1.md)
independently confirmed the allocation-defect signature and zero worksheet
formula records for the exact 33 hashes. The evidence report itself did not
grant either evidence approval. The subsequent explicit user approvals
recorded below are enforced only by the pure evidence-gate evaluator; they do
not install overall eligibility, a schema, or an admission path and do not
rehearse or authorize admission.

## Approved sub-decisions and remaining decisions

The user explicitly approved these two independent evidence sub-decisions on
2026-09-26:

- The hash-bound recovery exception for exactly the 27 recovery-dependent
  hashes and every acceptance and rejection condition in
  [Proposal A](biff-xls-recovery-formula-evidence-v1.md#proposal-a-hash-bound-recovery-exception).
- Current-retained-file formula-origin sufficiency for exactly all 33 hashes,
  with the limitations and fail-closed conditions in
  [Proposal B](biff-xls-recovery-formula-evidence-v1.md#proposal-b-retained-file-formula-origin-sufficiency).

On the same date, the user separately approved:

- `MODEL_METRIC_ZERO_HANDLING_POLICY_V1` for the exact twelve model metrics on
  the 33 inventoried workbooks and future workbooks independently satisfying
  the same v1 typed-source contract. Original numeric zero remains `0.0`; only
  the separately identified, explicitly selected, provenance-bound
  `MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1` projection may omit that
  metric observation, never its source occurrence or holding.

On 2026-10-07 the user explicitly approved all three model currency-risk
sub-decisions within the [reviewed scopes](#model-currency-risk-translation-and-anomaly-policy):
`MODEL_CURRENCY_RISK_TRANSLATION_POLICY_V1`,
`MODEL_CURRENCY_RISK_ANOMALY_DISPOSITION_V1`, and
`MODEL_CURRENCY_RISK_LEGACY_READER_PROJECTION_V1`. The subsequent pure
projection implementation and separately authorized
[bounded currency-risk comparison](#bounded-currency-risk-compatibility-comparison-evidence)
are completed; further comparison execution, consumer activation, and admission
remain unauthorized. The typed
inspection manifest's `NOT_GRANTED` fields remain its unchanged audit-time
record; the subsequent approval is recorded only here, not backfilled into
historical evidence.

The evidence report's `NOT_GRANTED` fields remain an unchanged record of its
audit-time state. The subsequent pure evidence-gate evaluator implements only
the two approved checks and still emits `admission_approval = NOT_GRANTED`; it
does not implement overall eligibility or authorize admission. The following
model-zero integration work remains pending: the current normalization adapter
still emits `UNRESOLVED_MODEL_ZERO_SEMANTICS`, whose input record is preserved
by the separate projection. No active projection consumer exists. The
[bounded comparison](#bounded-retained-corpus-comparison-evidence) establishes
metric/ranking equivalence only with legacy descriptive inputs fixed; full
candidate-native reader equivalence remains pending. The following
implementation and unrelated policy decisions remain pending:

1. Separately authorized candidate-native reader implementation and bounded
   comparison using the pure currency-risk projection, plus policy approval
   for the three sustainability warnings.
2. Shortlist English currency-risk mappings, already-English model inputs and
   any additional anomaly dispositions. The model-only lexical approval does
   not cover these extensions.
3. Whether `3yr` and `5yr` returns are cumulative or annualized. No rescaling
   is allowed until this is evidenced and approved.
4. For a future real-workbook writer only, atomic dual-sheet Option A versus
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

No current writer consumes the candidate format. The two evidence approvals
are now enforced by the separate pure
`BIFF_XLS_EVIDENCE_GATE_EVALUATION` v1 boundary, but it deliberately does not
combine them with normalization diagnostics or other eligibility gates. The
approved model-zero projection is now implemented separately in
`portfolio_advisor.workbook_source.model_metric_projection`. Its public API is:

```python
project_model_metrics(
    candidate,
    projection=MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1,
    expected_candidate_fingerprint=candidate.candidate_fingerprint,
)
```

Both keywords are required; `MODEL_METRIC_ORIGINAL_V1` is the other supported
selection. The immutable `MODEL_METRIC_PROJECTION` v1 result embeds the complete
unchanged two-sheet candidate and ordered model rows, with all twelve field
dispositions, original typed fields, policy/date, projection identity/version,
and a canonical `projection_fingerprint`. Every result remains
`NOT_EVALUATED_PROJECTION_ONLY` with `admission_approval = NOT_GRANTED`.
`zero_policy_applied` identifies only eligible numeric zeros in either mode;
it records resolution under this policy without deleting the input's historical
`UNRESOLVED_MODEL_ZERO_SEMANTICS` diagnostic. All other diagnostics remain intact.

Validation checks immutable candidate types, source-contract identity, dated
filename, exact sheets/headers, row and cell provenance, row fingerprints, and
field semantics by replaying the existing pure field normalizer. Missing or
altered required field diagnostics reject the input. This checks candidate
consistency, not workbook authenticity: envelope/sheet fingerprints remain
upstream bindings because full workbook metadata/merge evidence is not present
in the candidate. Classification candidates and mapping references are retained,
not newly approved or admitted. No filesystem or database is accessed; durable
retention remains the caller's separately governed responsibility.
Synthetic tests cover scope, exclusions, malformed candidates, duplicates,
determinism, provenance, input non-mutation, and compatibility of adjacent APIs.

The smallest next slice requires separate authorization for consumer selection
or eligibility composition using this result and the evidence-gate result.
Full candidate-native reader equivalence remains unproven despite the
[bounded metric/ranking comparison](#bounded-retained-corpus-comparison-evidence).
Any temporary rehearsal
must reproduce exact correction bindings before an admission adapter is
designed; none of these steps may transfer authority or silently choose
unresolved semantics.
