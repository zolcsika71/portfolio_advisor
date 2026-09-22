# Shortlist classification corrections

The shortlist workbook values, normalized
`shortlist_entry_source_occurrence` columns, and `source_payload_json` remain
immutable evidence. The governed correction contract records the authorized
effective interpretation of the exact sub-asset label `Fejl?d? piacok` as
`Fejlődő piacok` for one verified dataset. It does not perform fuzzy matching,
repair other encoding, or establish a rule for future imports.

The contract is governed by
[ADR-004](decisions/ADR-004-preserve-shortlist-classification-evidence-with-effective-mapping.md),
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

The command uses SQLite's backup API before admission. Admission installs the
mapping and effective view in one transaction. Exact replay adds no records;
changed authorization, dataset, source, mapping, or hash bindings fail without
partial admission. A same-dataset copy-on-write re-import revalidates the
stable evidence bindings before publication.

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
WHERE classification.original_sub_asset_class = 'Fejl?d? piacok'
   OR classification.effective_sub_asset_class = 'Fejlődő piacok'
ORDER BY snapshot.snapshot_date,
         classification.effective_asset_class,
         classification.effective_sub_asset_class,
         classification.isin;
```

`original_sub_asset_class` remains the source spelling.
`effective_sub_asset_class` is the application classification. Conflict status
is independent and remains source-reported.
