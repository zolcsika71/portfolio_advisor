# Milestone 6 — LTIA identity and current-state reconciliation

LTIA is the new domain term; legacy `tbsz` packages, databases, tables, and
scripts remain compatibility names. Private evidence remains local and is
audited read-only.

Resolution is exact-only: explicit valid ISIN, manual alias, unique exact name,
then approved exact model/shortlist mapping. Fuzzy matching is review-only.
Ambiguous, conflicting, and missing evidence remains explicit. Manual
confirmation validation is dry-run by default. The user-approved completion
gate writes only the ignored `data/tbsz/ltia_identity_confirmations.json`
overlay atomically; it never rewrites retained TBSZ evidence. It records the
confirmation actor, timestamp, exact-name rule and registry provenance.

Every source snapshot is retained. Equal undated evidence may contribute to a
derived current view only when deterministic evidence fingerprints prove
equivalence; otherwise current-state precedence is unresolved. Account rows
retain source-snapshot lineage. Consolidation groups confirmed ISINs only,
never names; unresolved positions remain separate. Cash remains account and
currency data, with no ISIN and no FX conversion.

Run the local aggregate audit:

```bash
poetry run python -c "from pathlib import Path; from portfolio_advisor.tbsz.ltia_reconciliation import audit_ltia_read_only; print(audit_ltia_read_only(Path('database/tbsz_portfolio.sqlite')))"
```

This milestone does not migrate private data, create trade instructions, or
authorize Milestone 7 cutover.

For the approved local confirmation gate, first review then explicitly apply:

```bash
poetry run python scripts/confirm_ltia_identity_mappings.py
poetry run python scripts/confirm_ltia_identity_mappings.py --apply
poetry run python scripts/audit_milestone_6_ltia.py
```

The two undated semantically equivalent groups use only their lowest stable
snapshot IDs as derived-view representatives; both source IDs stay in lineage
and no observation date is created.
