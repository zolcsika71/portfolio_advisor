# BIFF-XLS dual-sheet source envelope v1

## Status and scope

Implemented by `portfolio_advisor.workbook_source.biff_xls`. The contract is a
pure extraction boundary for retained BIFF `.xls` bytes. It does not admit a
workbook, write SQLite, move a file, select an operational reader, apply a
correction, or establish eligibility for later admission.

The envelope status is always `NOT_EVALUATED_PARSER_ONLY`. In particular, a
successful parse is not an admission receipt and is not evidence that the
current dataset-bound shortlist corrections can be inherited or extended.

## Source binding and roles

The parser reads an ordinary file into bytes once, computes SHA-256 over those
bytes, and supplies the same byte sequence to `xlrd`. The source filename must
end with one valid calendar date in `YYYYMMDD.xls` form. Absolute paths and
execution timestamps are excluded from serialization.

Version 1 requires exactly one visible sheet for each real source role:

| Exact BIFF sheet name | Envelope role |
|---|---|
| ` modell portfóliók` | `MODEL_PORTFOLIO` |
| ` shortlist` | `ANALYTICAL_SHORTLIST` |

Names include their leading spaces. A trimmed/case-folded match is used only to
diagnose ambiguous or unsupported variants; it does not make a variant valid.
The analytical shortlist role is deliberately distinct from Phase 3B.1's
synthetic `terméklista` sheet and JSON `SHORTLIST` role.

The workbook inventory retains every sheet's exact name, zero-based index, and
BIFF visibility code. The two parsed target sheets additionally retain the
exact ordered source header cells, one-based header position, merged ranges,
and every applicable nonempty row in source order. Duplicate rows are separate
occurrences with separate row coordinates and stable source references.

## Typed cells

Every header and data cell records:

- one-based row and column plus A1 coordinate;
- BIFF type code and semantic type (`empty`, `blank`, `text`, `number`,
  `date_serial`, `boolean`, or `error`);
- raw value without percentage or unit scaling;
- XF index, format key, and exact number-format string; and
- the error code and text when the BIFF cell is an error.

`empty` and formatted `blank` remain different. Numeric `0.0`, text `"0"`, and
blank remain different. Hungarian labels and anomalous values remain unchanged.
No model zero-to-NULL rule, shortlist zero correction, classification mapping,
currency-risk translation, annualization inference, or duplicate resolution is
part of this contract.

Unexpected nonblank currency-risk and sustainability values are preserved and
reported as occurrence-level warnings. A text-zero metric is also preserved
and warned rather than converted to a numeric observation.

## Determinism and diagnostics

Canonical JSON uses sorted keys, compact separators, ASCII escaping, and no
environment-dependent path or timestamp. Sheet, row, and envelope fingerprints
are SHA-256 hashes over the corresponding canonical payloads. Source references
bind contract version, workbook hash, zero-based sheet index, and one-based row.

Parsing fails with a stable diagnostic code for a non-file or link, invalid
filename/date, malformed or unsupported BIFF bytes, missing/ambiguous/hidden or
variant target sheets, missing/ambiguous headers, unexpected header/data cells,
unresolvable number formats, unsupported cell types, or an empty target sheet.

The parser uses `xlrd` compound-document recovery mode for every retained file;
27 of the 33 files fail a separate strict open and require that mode, while six
open strictly. Recovery use is declared in parser metadata; it does not modify
or repair the source bytes and does not establish admission eligibility.

## Formula limitation

`xlrd` exposes a formula cell's cached BIFF result when available, but does not
expose the formula text or a reliable formula-present flag. The parser does not
evaluate formulas. Consequently it can preserve only the cached cell type,
value, and format exposed by `xlrd`; it cannot prove the formula expression or
whether a non-formula cell produced the same cached representation. This limit
is recorded in every envelope and must be resolved before any workflow that
requires formula provenance.

## Deferred work

Database admission, portable evidence packaging, legacy/effective projections,
correction disposition/rebinding, receipt-chain construction, watcher/manual
coordination, outbox execution, artifact publication, and cutover remain
separately gated. The implemented pure normalization-candidate boundary is
documented in
[BIFF-XLS normalization and admission policy v1](biff-xls-normalization-admission-policy-v1.md);
it is not part of this parser and cannot approve admission. ADR-007 remains
Proposed.
