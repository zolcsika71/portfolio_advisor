# Shortlist zero-to-NULL corrections

The shortlist source workbook and normalized
`instrument_metric_observation` rows remain immutable evidence. The governed
correction feature records an authorized, dataset-specific interpretation of
selected original numeric zeros as missing. It does not establish that every
reported zero is erroneous and does not affect future datasets automatically.

The contract is governed by
[ADR-003](decisions/ADR-003-preserve-shortlist-evidence-with-null-corrections.md),
and the implemented transaction and replay flow is shown in the
[shortlist correction diagram](diagrams/shortlist-zero-null-correction.puml).

## Admission

Admission requires an explicit correction identity, the installed shortlist
dataset fingerprint, the original pre-write database SHA-256, an authorization
reference, a reason, a non-existing backup path, and `--apply`:

```bash
poetry run python scripts/admit_shortlist_zero_null_corrections.py \
  --database database/portfolio_advisor.sqlite \
  --backup /path/outside/repository/portfolio_advisor.pre-shortlist-correction.sqlite \
  --correction-id SHORTLIST_ZERO_NULL_2026_09_22 \
  --dataset-fingerprint <64-lowercase-hex> \
  --initial-target-sha256 <64-lowercase-hex> \
  --authorization-reference USER_REQUEST_2026_09_22 \
  --reason "Expose the verified current shortlist numeric zeros as missing values." \
  --apply
```

The command creates its backup with SQLite's backup API before admission. A
failed admission rolls back schema and data changes. Repeating the command is
an exact no-op only when every stored binding matches; use a new, non-existing
backup path for any retry. Restore by disconnecting all consumers, retaining
the current file separately, and copying the verified backup into place while
SQLite is closed.

## Read-only verification

The following query shows original and effective values without modifying the
database:

```sql
SELECT metric_code,
       count(*) AS observation_count,
       sum(CASE WHEN original_value = 0.0 THEN 1 ELSE 0 END) AS original_zero_count,
       sum(CASE WHEN effective_value = 0.0 THEN 1 ELSE 0 END) AS effective_zero_count,
       sum(CASE WHEN effective_value IS NULL THEN 1 ELSE 0 END) AS effective_null_count
FROM v_effective_shortlist_metric_observation
WHERE metric_code IN (
  'YTD', 'RETURN_1Y', 'RETURN_3Y', 'RETURN_5Y',
  'SHARPE_RATIO_1Y', 'SHARPE_RATIO_3Y', 'SHARPE_RATIO_5Y',
  'VOLATILITY_1Y', 'VOLATILITY_3Y', 'DOWNSIDE_RISK',
  'INFORMATION_RATIO', 'MAXIMUM_DRAWDOWN'
)
GROUP BY metric_code
ORDER BY metric_code;
```

`original_zero_count` is evidence retained from the workbook.
`effective_zero_count` must be zero after a valid admission. The raw JSON
payloads retain their original string values, including `"0"`.
