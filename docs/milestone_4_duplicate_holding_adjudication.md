# Milestone 4 duplicate-holding adjudication v1

Decision scope: the six duplicate model-holding source rows in
`PB_Modell_Portfoliok_es_Shortlist_20240917.xls`. This is a read-only evidence
adjudication. It neither changes the workbook/database nor changes
`CAPITAL_PRESERVATION_RANKING_POLICY v1.0.1`.

## Evidence

Source: workbook `PB_Modell_Portfoliok_es_Shortlist_20240917.xls`, visible sheet
` modell portfóliók`, snapshot date `2024-09-17`, ISIN `IE00B7KFL990`, displayed
name `PIMCO GIS INCOME FUND E USD CAP`.

Every row has currency `USD`, asset class `Kötvény-befektetési kategória`,
sub-asset class `Globál`, currency risk `Nincs fedezve`, sustainability
`1: ESG-Minimum standard`, and the following reported metrics:

| YTD | 1yr | 3yr | 5yr | 1Y Sharpe | 3Y Sharpe | 5Y Sharpe | 1Y Vol. | 3Y Vol. | Down. risk | Info. ratio | Max. drawd. |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.05856 | 0.10373 | 0.01264 | 0.0278 | 0.09 | -0.115 | 0.00065 | 0.05831 | 0.05616 | 0 | 0.41064 | -0.15057 |

| Portfolio | Source rows | Allocations | Field-for-field identical? | Non-empty differences |
| --- | --- | --- | --- | --- |
| PB Konzervatív USD | 33, 35 | 11.5%, 6.0% | No | `Hányad (%)` only |
| PB Kiegyensúlyozott USD | 87, 91 | 7.0%, 6.0% | No | `Hányad (%)` only |
| PB Dinamikus USD | 145, 152 | 7.5%, 5.0% | No | `Hányad (%)` only |

The generated JSON retains all fields for every source row, including source
row number and the complete displayed source payload:
`data/audit/milestone_4_current_data_audit.json` →
`duplicate_holding_adjudication.groups`.

## Classification

All three groups are classified as:

```text
DISTINCT_SOURCE_ROWS_FOR_SAME_INSTRUMENT
semantic status: UNRESOLVED_DUPLICATE_SEMANTICS
human approval required: true
```

They are not `EXACT_DUPLICATE_SOURCE_ROWS`: the two allocations in each group
are non-empty and unequal. They are not `CONFLICTING_DUPLICATE_ROWS`: no other
non-empty field differs. The source does not explain whether the two rows are
intentional sleeves, a workbook duplication, or another provider convention.
Therefore it cannot justify dropping, merging, or aggregating them.

## Financial-impact diagnostics

The audit evaluates the retained 2024-09-17 legacy rows in memory using the
unchanged active policy, and records candidate features, eligibility,
normalization, weighted contributions, total scores, rank order, and winner in
the JSON artifact. It does not call the importer or write either database.

| Scenario | Allocation totals / eligibility | Features | Normalized values, contributions, scores | Rank order / winner |
| --- | --- | --- | --- | --- |
| Legacy behavior | Baseline | Baseline | Baseline | Baseline |
| Retain each source occurrence | Identical | Exact serialization identical | Exact serialization identical | Identical |
| Deduplicate exact rows only | Identical; no exact groups exist, so no rows removed | Exact serialization identical | Exact serialization identical | Identical |
| Aggregate by portfolio snapshot + ISIN | Identical: all three affected portfolios remain 100% allocated and eligibility is unchanged | Not byte-identical due to IEEE-754 operation order; largest delta `1.734723475976807e-18`, within `1e-12` | Same maximum delta, within `1e-12` | Identical; winner remains `PB Konzervatív EUR` |

Thus aggregation happens to preserve the observed outcome here because the
duplicated rows share every non-allocation value. That result is a diagnostic,
not evidence that aggregation is economically valid. A strict relational
equivalence test must define a reviewed numeric representation before accepting
even this microscopic float difference.

## Required schema treatment

`UNIQUE(portfolio_snapshot_id, instrument_id)` is **not valid for raw source
occurrences**: each of these three portfolio snapshots has two independent,
provenance-bearing source rows with the same instrument ID.

Schema-v3 scaffolding must retain an occurrence table, for example:

```text
portfolio_holding_source_occurrence
  portfolio_holding_source_occurrence_id PK
  portfolio_snapshot_id FK NOT NULL
  instrument_id FK NOT NULL
  source_sheet_id FK NOT NULL
  source_row_number INTEGER NOT NULL
  reported_weight NUMERIC NULL
  source_payload_hash TEXT NOT NULL
  source_value_provenance ...

  UNIQUE(source_sheet_id, source_row_number)
```

The existing `portfolio_holding` unique pair may be retained only as an
explicitly derived analytical projection, with a lineage relation to one or
more source occurrences. It must not become the raw import table. For an
unresolved group, no automatic occurrence-to-single-holding projection is
permitted. This preserves the constraint where it describes a genuine derived
business fact while preserving all source evidence.

## Required human approval and decision

A reviewer must determine, from provider evidence if available, whether the
two rows per portfolio represent intentional sleeves or erroneous duplication.
The reviewer must approve any later projection rule and its effective scope;
the original occurrence rows remain immutable either way.

**GO for Milestone 5 schema-v3 scaffolding**, conditional on introducing the
source-occurrence representation and not importing these groups into a raw
table constrained by `(portfolio_snapshot_id, instrument_id)`. **NO-GO for a
schema-v3 data migration/cutover that assumes one raw holding per ISIN** until
the human semantic decision is recorded.
