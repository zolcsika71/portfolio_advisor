# BIFF-XLS recovery and formula evidence verifier v1

## Status and authority

Implemented by `portfolio_advisor.workbook_source.biff_evidence` and
`scripts/verify_biff_xls_evidence.py`. This is a read-only, hash-bound evidence
verifier for the 33 retained workbooks in `data/xls/processed`. It does not
normalize or admit data, grant a recovery exception, approve formula origin,
connect to SQLite, move a workbook, or alter the watcher.

Every successful report has status
`READ_ONLY_EVIDENCE_NOT_ADMISSION_APPROVAL` and records all three approval
fields as `NOT_GRANTED`. ADR-007 remains `Proposed`.

The expected inventory is
`data/knowledge/biff_xls_processed_expected_inventory_v1.json`. It binds the
exact filename and full SHA-256 of all 33 workbooks, the strict-open outcome,
the expected allocation-overlap count, per-sheet row counts, data-field count,
and zero expected formula records. Missing, additional, renamed, changed, or
symlinked inputs fail before a successful report is written.

## Independent evidence and cross-checks

The verifier deliberately keeps three methods separate:

1. Its independent CFBF inspector reads the header, DIFAT, FAT, directory,
   MiniFAT-sector chain, root mini-stream chain, and Workbook chain directly
   from bytes. It validates FAT/DIFAT markers and reserved-chain ownership,
   extracts the Workbook stream through its own allocation walk, and records
   the exact overlap signature.
2. Its independent BIFF inspector parses workbook globals and both worksheet
   substreams through explicit EOF records. It reads BOUNDSHEET identity and
   visibility, XF-to-format references, ROW/COLINFO inherited formats, cell
   record coordinates and kinds, explicit BLANK/MULBLANK records, absent-cell
   locations, and formula-related record opcodes.
3. Rust-backed Calamine supplies an independent value view from the same
   in-memory bytes that were hashed and inspected. The published `biff_xls`
   parser is then used only as an explicit comparison for source values, cell
   types, coordinates, XF/format references, row coverage, and workbook-hash
   binding.

Calamine does not prove blank-versus-absent, formula presence, or cell-level
format provenance. Those properties come from the raw BIFF inspection. The
raw inspector does not treat Calamine's missing representation as a cell type.
For numeric NUMBER/RK/MULRK and BOOLERR records, it also decodes the record
value directly before comparison. Text is value-compared through Calamine and
record-type-compared through BIFF.

A successful report requires exact leading-space worksheet names in source
order:

| Index | Exact name | Role |
| ---: | --- | --- |
| 0 | ` modell portfóliók` | `MODEL_PORTFOLIO` |
| 1 | ` shortlist` | `ANALYTICAL_SHORTLIST` |

The verifier rejects truncated records, missing or wrong-type BIFF8 BOF/EOF,
nonzero skipped or trailing BIFF regions, invalid allocation chains or sector
markers, invalid XF references, duplicate or unaccounted cell records (including
explicit blanks), unexpected sheet metadata, row/coordinate differences,
value/type/format differences, or any formula inventory different from the
hash-bound expectation. Errors include bounded coordinates and cause exit
status 2; no successful report is created.

## Recovery finding

Six inventory hashes open strictly. The other 27 fail strict `xlrd` opening
with `CompDocError: Workbook corruption: seen[2] == 4`. For each of those 27,
the root directory entry declares a 512-byte mini-stream beginning at sector 1,
but its FAT chain continues at sector 2 through the complete Workbook chain;
the Workbook directory entry itself begins at sector 2. The verifier requires
the exact per-hash overlap-sector count in the inventory.

Independent extraction can read the complete Workbook substreams and compare
their cells, but that does not make the compound allocation graph valid. Every
recovery-dependent result therefore remains
`RECOVERY_EXCEPTION_APPROVAL_REQUIRED`. A future exception would need explicit
approval for the listed hashes and must fail when a hash or defect signature
changes.

## Formula statement and limitation

The formula inventory covers `FORMULA`, `FORMULA3`, `FORMULA4`, `ARRAY`,
`ARRAY2`, `SHRFMLA`, `TABLEOP`, `TABLEOP2`, `TABLEOP_B2`, and formula-result
`STRING` records in workbook globals and both complete worksheet substreams.

“Zero formula records in these bytes” proves that those formula-related BIFF
records are absent from the exact retained Workbook stream and that the
examined explicit cells use non-formula cell-record opcodes. It does not prove
pre-export authoring history, whether a producer previously evaluated a
formula and pasted its value, or any state not encoded in the retained bytes.
Approval is still required to decide whether current-file BIFF origin is
sufficient for admission provenance.

## Deterministic report

Run from the repository root using the configured Poetry environment:

```bash
REPORT_DIR="$(mktemp -d /tmp/portfolio-advisor-biff-evidence.XXXXXX)"
poetry run python scripts/verify_biff_xls_evidence.py \
  --output "$REPORT_DIR/biff-xls-recovery-formula-evidence-v1.json"
shasum -a 256 "$REPORT_DIR/biff-xls-recovery-formula-evidence-v1.json"
```

The output parent must already exist, the output path must be fresh and outside
the repository, and the command uses exclusive creation so a path created
during verification is not overwritten. The report excludes local paths,
timestamps, and raw financial row values. It includes:

- report contract, verifier version, verifier-source SHA-256, dependency
  versions, and committed-inventory SHA-256;
- every workbook filename, full SHA-256, byte length, strict/recovery result,
  allocation signature, worksheet/EOF coverage, cell-property counts, formula
  count, and evidence verdict;
- aggregate row, field, type, formula, and mismatch counts;
- bounded formula or discrepancy coordinates when present; and
- a canonical `report_fingerprint` computed without that fingerprint field.

The pretty-printed file bytes and canonical report fingerprint are deterministic
for the same verifier source, locked dependencies, inventory, and workbook
bytes. Detailed generated evidence remains local and must not be committed.
