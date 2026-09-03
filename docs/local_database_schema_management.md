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

The Phase B ECB importer does not change schema. Phase C0 is the explicit
copy-on-write exception: it reconstructs the four reference-rate tables from
exact revision 1 to provider-neutral revision 2 in a disposable candidate,
using the verified retained ECB artifact to recover raw `OBS_STATUS`. The
migrator preserves all non-reference logical data and the exact ECB business
projection, updates the feature marker last, and is byte-identical on v2
replay. Exact `ABSENT`, `V1`, and `V2` states are recognized; partial, mixed,
constraint-damaged, index-damaged, or future states fail closed.
Phase C adds no schema revision: the existing provenance-v2 tables admit a
second benchmark-scoped definition/source/manifest/observation bundle. The
SOFR candidate builder copies the populated installation, preserves every
stored €STR field, imports only retained offline SOFR evidence, and requires
the same schema-contract fingerprint.
Phase D likewise adds no schema revision. Its candidate builder requires the
exact populated €STR+SOFR Phase C scope, preserves every stored field in both
existing bundles, imports one retained HUFONIA bundle, and requires the
unchanged provenance-v2 feature and schema fingerprints.

```bash
poetry run python scripts/migrate_reference_rate_provenance_contract.py --help
poetry run python scripts/validate_reference_rate_schema.py --help
poetry run python scripts/validate_ecb_estr_reference_rate.py --help
poetry run python scripts/validate_reference_rate_provenance.py --help
poetry run python scripts/build_sofr_candidate.py --help
poetry run python scripts/validate_sofr_reference_rate.py --help
poetry run python scripts/validate_reference_rate_provenance.py --require-sofr
poetry run python scripts/build_hufonia_candidate.py --help
poetry run python scripts/validate_hufonia_reference_rate.py --help
poetry run python scripts/validate_reference_rate_provenance.py --require-hufonia
```

The schema validator checks the exact current v2 structure whether empty or
populated. The ECB, SOFR, and HUFONIA validators check their retained official
bundles, and the complete provenance validator checks all admitted bundles
read-only. Installation still requires a verified external backup, a fully
gated candidate, atomic replacement, and immediate rollback on any
post-installation failure.

The other stores have no current detected schema drift. If any of their
schemas changes, add an explicit component migration contract in its existing
code owner rather than introducing an independent `schema.sql` copy.
