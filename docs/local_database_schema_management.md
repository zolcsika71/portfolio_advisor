# Local SQLite schema management

The project retains local SQLite evidence under `database/`; the directory is
ignored by Git. The code that owns a schema remains its only authority. A
standalone `schema.sql` is deliberately not maintained, because a second,
manually maintained schema source would drift from the application contract.

Run the read-only health check from the repository root:

```bash
poetry run python scripts/audit_local_databases.py
poetry run python scripts/audit_local_databases.py --database tbsz_portfolio.sqlite
```

The auditor opens databases read-only, is restricted to `database/`, skips
symlinks, and reports integrity, foreign-key, version, schema-object, and row
count diagnostics without printing retained financial values.

## Schema ownership

| Database | Authoritative schema owner | Schema SQL file | Version model |
| --- | --- | --- | --- |
| `portfolio_advisor.sqlite` | `portfolio_advisor.database.schema.v3`, additive Milestone 11B/11C migrations, and their validators | No | `PRAGMA user_version = 3` plus deterministic `schema_feature_contract` markers |
| `tbsz_portfolio.sqlite` | `portfolio_advisor.tbsz.repository` | No | `PRAGMA user_version`; current version 2 |
| `model_portfolio.sqlite` | `DB_creation.database_create` and `history.mnb_otc` | No | Source-column contract; incompatible workbook schemas fail closed |
| `official_historical_nav.sqlite` | `history.official_nav_store` | No | Embedded single-table evidence-store contract |
| `prospective_portfolio_validation.sqlite` | `prospective.validation` | No | Embedded append-only ledger contract |

TBSZ v2 is created for a fresh database. A recognized v1 (or historical
pre-versioned v0) database may receive the transactional, data-preserving
upgrade chain. The v1-to-v2 step adds only nullable source-supported
`position_snapshots.observed_roi`; it neither rewrites retained rows nor
creates target allocations or recommendations. Before an existing ledger is
migrated, the repository creates and verifies an ignored SQLite backup under
`database/backups/`. Unknown versions, missing tables, altered constraints,
integrity failures, or foreign-key violations fail closed. Run the explicit
schema-only command when required:

```bash
poetry run python scripts/migrate_tbsz_portfolio.py
```

The Phase B ECB importer does not change schema. It requires the installed
`MILESTONE_11C_REFERENCE_RATE_EVIDENCE` feature, builds a disposable database
with SQLite backup semantics, validates all pre-existing logical values,
schema fingerprints, integrity and foreign keys, and installs only after a
verified external backup exists. `validate_ecb_estr_reference_rate.py` is the
read-only populated-evidence gate. Phase A's empty-schema validator remains a
historical foundation check and intentionally rejects populated tables.

The other stores have no current detected schema drift. If any of their
schemas changes, add an explicit component migration contract in its existing
code owner rather than introducing an independent `schema.sql` copy.
