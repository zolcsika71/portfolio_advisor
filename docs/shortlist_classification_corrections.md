# Shortlist classification corrections

The shortlist workbook values, normalized
`shortlist_entry_source_occurrence` columns, and `source_payload_json` remain
immutable evidence. The governed correction contracts record separately
authorized effective interpretations for one verified dataset. Contract v1
maps the exact source label `Fejl?d? piacok` to `Fejlődő piacok`. Contract v2
composes later admissions in order; the authorized 2026-09-23 admission
replaces each literal `?` with `ő` in its explicitly inventoried prior
effective sub-asset labels. Contract v3 composes the approved English
asset/sub-asset pair mapping from the tracked, reviewable 77-entry manifest.
No contract performs fuzzy matching, changes original evidence, or establishes
an automatic rule for future imports.

The contract is governed by
[ADR-006](decisions/ADR-006-standardize-effective-shortlist-classifications-in-english.md),
and its admission, selection, persistence, and re-import flow is shown in the
[classification correction diagram](diagrams/shortlist-classification-correction.puml).

## Admission

Admission requires the installed dataset fingerprint, original pre-write
database SHA-256, correction identity, authorization reference, reason, a new
backup path, and `--apply`:

```bash
poetry run python scripts/admit_shortlist_classification_correction.py \
  --database database/portfolio_advisor.sqlite \
  --backup /path/outside/repository/portfolio_advisor.pre-classification-correction.sqlite \
  --correction-id SHORTLIST_CLASSIFICATION_2026_09_22 \
  --dataset-fingerprint <64-lowercase-hex> \
  --initial-target-sha256 <64-lowercase-hex> \
  --authorization-reference USER_REQUEST_2026_09_22 \
  --reason "Correct the exact shortlist sub-asset label while retaining source evidence." \
  --apply
```

An additional effective-label admission also requires the exact prior labels
and row counts established by the read-only inventory:

```bash
poetry run python scripts/admit_shortlist_classification_correction.py \
  --database database/portfolio_advisor.sqlite \
  --backup /path/outside/repository/portfolio_advisor.pre-composition.sqlite \
  --correction-id <new-immutable-id> \
  --dataset-fingerprint <64-lowercase-hex> \
  --initial-target-sha256 <64-lowercase-hex> \
  --authorization-reference <authorization> \
  --reason <reason> \
  --question-mark-to-o-double-acute \
  --expected-prior-label-count '<exact-prior-label>=<count>' \
  --apply
```

The command uses SQLite's backup API before admission. Admission installs its
immutable items and effective projection in one transaction. Ordered
composition records both the expected prior effective label and the resulting
label for every stable source occurrence. Exact replay adds no records;
changed authorization, inventory, dataset, source, mapping, or hash bindings
fail without partial admission. A same-dataset copy-on-write re-import
revalidates every stable evidence binding before publication.

The approved English pair mapping is admitted with its exact tracked manifest:

```bash
poetry run python scripts/admit_shortlist_classification_correction.py \
  --database database/portfolio_advisor.sqlite \
  --backup /path/outside/repository/portfolio_advisor.pre-english-mapping.sqlite \
  --correction-id SHORTLIST_CLASSIFICATION_ENGLISH_2026_09_23 \
  --dataset-fingerprint <64-lowercase-hex> \
  --initial-target-sha256 <authorized-pre-write-sha256> \
  --authorization-reference USER_APPROVED_ENGLISH_CLASSIFICATION_MAPPING_2026_09_23 \
  --reason "Standardize the reviewed effective pairs in English." \
  --english-pair-mapping-manifest \
    data/knowledge/validated_rules/shortlist_classification_english_mapping_v1.json \
  --apply
```

The v3 admission binds all 10,833 current occurrences to their stable source
references, original pair, expected v2 effective pair, and approved English
pair. The manifest additionally binds the 77-to-51 pair inventory, resulting
7 asset and 38 sub-asset labels, and reviewed snapshot group-count effects.
NULL classifications remain NULL. Original classifications, raw JSON, conflict
flags, and the installed v1/v2 records remain unchanged.

## Read-only reporting

This query reports original and effective labels without modifying data:

```sql
SELECT snapshot.snapshot_date,
       classification.isin,
       classification.original_asset_class,
       classification.original_sub_asset_class,
       classification.effective_asset_class,
       classification.effective_sub_asset_class,
       classification.conflict_status,
       classification.correction_id,
       classification.correction_set_fingerprint
FROM v_effective_shortlist_classification AS classification
JOIN shortlist_snapshot AS snapshot
  ON snapshot.shortlist_snapshot_id = classification.shortlist_snapshot_id
WHERE classification.original_sub_asset_class <> classification.effective_sub_asset_class
ORDER BY snapshot.snapshot_date,
         classification.effective_asset_class,
         classification.effective_sub_asset_class,
         classification.isin;
```

`original_sub_asset_class` remains the source spelling.
`original_asset_class` and `original_sub_asset_class` remain the source values;
the corresponding `effective_*` columns are the application classifications.
Conflict status is independent and remains source-reported. `correction_id`
identifies the latest admission governing that row; constructed-artifact
provenance binds the ordered aggregate correction set.
